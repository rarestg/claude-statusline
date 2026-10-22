import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from support import SCRIPT, StatusTest


class GitTests(StatusTest):
    def setUp(self):
        super().setUp()
        self.init_git()

    def test_staged_unstaged_and_untracked(self):
        (self.cwd / 'file.txt').write_text('one\ntwo\nthree\n')
        self.git('add', 'file.txt')
        (self.cwd / 'file.txt').write_text('one\ntwo\nthree\nfour\n')
        (self.cwd / 'untracked').write_text('ignored\n' * 99)
        self.assertIn('+2', self.run_status())

    def test_git_configuration_is_preserved_and_external_diff_and_fsmonitor_are_skipped(self):
        marker = self.base / 'external-called'
        helper = self.base / 'external.sh'
        helper.write_text(f'#!/bin/bash\nprintf called >> "{marker}"\n'); helper.chmod(0o755)
        self.git('config', 'diff.external', str(helper))
        self.git('config', 'core.fsmonitor', str(helper))
        before = self.git('config', '--local', '--list')
        (self.cwd / 'file.txt').write_text('one\ntwo\nextra\n')
        self.assertIn('+1', self.run_status())
        self.assertFalse(marker.exists())
        self.assertEqual(self.git('config', '--local', '--list'), before)

    def test_warm_cache_skips_git(self):
        self.run_status()
        log = self.base / 'git-called'
        self.wrapper('git', f'printf called >> "{log}"; exit 1')
        self.run_status()
        self.assertFalse(log.exists())

    def test_linked_worktree_isolation_and_subdirectory_sharing(self):
        linked = self.base / 'linked worktree'
        self.git('worktree', 'add', '-qb', 'linked-branch', str(linked))
        self.assertNotIn('linked-branch', self.run_status())
        self.data['workspace']['current_dir'] = str(linked)
        self.assertIn('linked-branch', self.run_status())
        self.assertEqual(len(list((self.cache / 'worktrees').rglob('counts'))), 2)
        sub = linked / 'sub'; sub.mkdir()
        self.data['workspace']['current_dir'] = str(sub)
        self.assertIn('linked-branch', self.run_status())
        self.assertEqual(len(list((self.cache / 'worktrees').rglob('counts'))), 2)

    def test_22_expired_requests_only_refresh_once(self):
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        lines = counts.read_text().splitlines(); lines[2] = '0'; counts.write_text('\n'.join(lines) + '\n')
        log = self.base / 'git-log'
        real_git = shutil.which('git')
        self.wrapper('git', f'printf "%s\\n" "$*" >> "{log}"\nsleep 0.2\nexec "{real_git}" "$@"')
        def call(_):
            start = time.monotonic(); self.run_status(); return time.monotonic() - start
        with concurrent.futures.ThreadPoolExecutor(max_workers=22) as pool:
            timings = list(pool.map(call, range(22)))
        self.assertEqual(len(log.read_text().splitlines()), 2)
        self.assertLess(min(timings), 0.4)

    def test_branch_switch_never_uses_previous_counts(self):
        (self.cwd / 'file.txt').write_text('one\ntwo\nextra\n')
        self.assertIn('+1', self.run_status())
        counts = next((self.cache / 'worktrees').rglob('counts'))
        self.git('checkout', '-qb', 'new-branch')
        lock = counts.parent / 'refresh.lock'; lock.mkdir()
        (lock / 'owner').write_text(f'fixture\n{os.getpid()}\n{self.start}\n')
        output = self.run_status()
        self.assertIn('new-branch', output)
        self.assertNotIn('+1', output)

    def test_timeout_and_retry_backoff(self):
        log = self.base / 'git-log'
        self.wrapper('git', f'printf called >> "{log}"\nsleep 30')
        start = time.monotonic()
        output = self.run_status(env={'CLAUDE_STATUSLINE_GIT_TIMEOUT': '1'}, timeout=4)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIn('yellowtail-f5', output)
        self.run_status(env={'CLAUDE_STATUSLINE_GIT_TIMEOUT': '1'}, timeout=2)
        self.assertEqual(log.read_text(), 'called')
        self.assertFalse(list((self.cache / 'worktrees').rglob('refresh.lock')))

    def test_dead_owner_lock_recovers(self):
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        lines = counts.read_text().splitlines(); lines[2] = '0'; counts.write_text('\n'.join(lines) + '\n')
        lock = counts.parent / 'refresh.lock'; lock.mkdir()
        (lock / 'owner').write_text('dead-token\n99999999\n0\n')
        self.run_status()
        self.assertFalse(lock.exists())
        self.assertNotEqual(counts.read_text().splitlines()[2], '0')

    def test_reused_live_owner_pid_lock_recovers(self):
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        lines = counts.read_text().splitlines(); lines[2] = '0'; counts.write_text('\n'.join(lines) + '\n')
        lock = counts.parent / 'refresh.lock'; lock.mkdir()
        (lock / 'owner').write_text(f'reused\n{self.pid}\nWed Jan 01 00:00:00 2000\n')
        self.run_status()
        self.assertFalse(lock.exists())

    def test_empty_abandoned_lock_recovers(self):
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        lines = counts.read_text().splitlines(); lines[2] = '0'; counts.write_text('\n'.join(lines) + '\n')
        lock = counts.parent / 'refresh.lock'; lock.mkdir()
        os.utime(lock, (time.time() - 100, time.time() - 100))
        self.run_status()
        self.assertFalse(lock.exists())

    def test_corrupt_abandoned_owner_recovers(self):
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        lines = counts.read_text().splitlines(); lines[2] = '0'; counts.write_text('\n'.join(lines) + '\n')
        lock = counts.parent / 'refresh.lock'; lock.mkdir()
        owner = lock / 'owner'; owner.write_text('broken')
        os.utime(owner, (time.time() - 100, time.time() - 100))
        self.run_status()
        self.assertFalse(lock.exists())

    def test_sigkill_eventually_recovers(self):
        marker = self.base / 'first-git'
        real_git = shutil.which('git')
        self.wrapper('git', f'''if [[ ! -f "{marker}" ]]; then
  printf started > "{marker}"
  sleep 30
else
  exec "{real_git}" "$@"
fi''')
        env = {**self.env, 'CLAUDE_STATUSLINE_GIT_TIMEOUT': '1'}
        process = subprocess.Popen(['/bin/bash', str(SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=env)
        process.stdin.write(json.dumps(self.data)); process.stdin.close(); process.stdin = None
        for _ in range(100):
            if marker.exists(): break
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        process.kill(); process.communicate(timeout=4)
        time.sleep(2.1)
        self.run_status(env={'CLAUDE_STATUSLINE_GIT_TIMEOUT': '1'})
        self.assertFalse(list((self.cache / 'worktrees').rglob('refresh.lock')))

    def test_corrupt_cache_recovers_and_backward_clock_refreshes(self):
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        counts.write_text('broken')
        self.run_status()
        self.assertEqual(counts.read_text().splitlines()[0], '1')
        lines = counts.read_text().splitlines(); lines[2] = str(int(time.time()) + 10000)
        counts.write_text('\n'.join(lines) + '\n')
        self.run_status()
        self.assertLess(int(counts.read_text().splitlines()[2]), int(time.time()) + 10)

    def test_unborn_and_detached(self):
        self.git('checkout', '--detach', '-q')
        self.assertIn('⎇ @', self.run_status())

    def test_cancellation_cleans_lock_and_git_group(self):
        marker = self.base / 'git-pid'
        self.wrapper('git', f'printf "%s" "$$" > "{marker}"\nsleep 30')
        process = subprocess.Popen(['/bin/bash', str(SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=self.env)
        process.stdin.write(json.dumps(self.data)); process.stdin.close(); process.stdin = None
        for _ in range(100):
            if marker.exists(): break
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        process.terminate(); process.communicate(timeout=4)
        self.assertFalse(list((self.cache / 'worktrees').rglob('refresh.lock')))
        with self.assertRaises(ProcessLookupError): os.kill(int(marker.read_text()), 0)

    def test_group_kill_still_bounds_git(self):
        marker = self.base / 'git-pid'
        self.wrapper('git', f'printf "%s" "$$" > "{marker}"\nexec sleep 30')
        env = {**self.env, 'CLAUDE_STATUSLINE_GIT_TIMEOUT': '1'}
        process = subprocess.Popen(['/bin/bash', str(SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        process.stdin.write(json.dumps(self.data)); process.stdin.close(); process.stdin = None
        for _ in range(100):
            if marker.exists() and marker.read_text(): break
            time.sleep(0.02)
        git_pid = int(marker.read_text())
        # Kill the status line's whole process group, as a parent may on cancel.
        os.killpg(process.pid, signal.SIGKILL)
        start = time.monotonic()
        process.communicate(timeout=4)
        self.assertLess(time.monotonic() - start, 0.8, 'the watchdog kept the output pipe open')
        while time.monotonic() - start < 3:
            try: os.kill(git_pid, 0)
            except ProcessLookupError: break
            time.sleep(0.05)
        else:
            os.kill(git_pid, signal.SIGKILL)
            self.fail('Git outlived its deadline after a process-group kill')

    def test_concurrent_stale_lock_reclaim_is_quiet_and_correct(self):
        (self.cwd / 'file.txt').write_text('one\ntwo\nthree\n')
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        lock = counts.parent / 'refresh.lock'
        real_mv = shutil.which('mv'); real_git = shutil.which('git')
        # A slow mv widens the gap between checking a stale lock and moving it,
        # so several callers reclaim it and refresh at the same time.
        self.wrapper('mv', f'sleep 0.03\nexec "{real_mv}" "$@"')
        self.wrapper('git', f'sleep 0.2\nexec "{real_git}" "$@"')
        lines = counts.read_text().splitlines(); lines[2] = '0'; counts.write_text('\n'.join(lines) + '\n')
        lock.mkdir(); (lock / 'owner').write_text('dead-token\n99999999\n0\n')
        with concurrent.futures.ThreadPoolExecutor(max_workers=22) as pool:
            list(pool.map(lambda _: self.run_status(), range(22)))  # run_status asserts empty stderr
        self.assertEqual(counts.read_text().splitlines()[3:5], ['1', '0'])
        self.assertFalse(lock.exists())
        self.assertFalse(list(counts.parent.glob('work.*')))

    def test_dead_owner_work_directories_are_removed(self):
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        stale = counts.parent / 'work.99999999.1'; stale.mkdir(); (stale / 'output').write_text('junk')
        live = counts.parent / f'work.{os.getpid()}.1'; live.mkdir()
        lines = counts.read_text().splitlines(); lines[2] = '0'; counts.write_text('\n'.join(lines) + '\n')
        self.run_status()
        self.assertFalse(stale.exists())
        self.assertTrue(live.exists(), "a live owner's work directory was removed")

    def test_owner_whose_lock_is_stolen_does_not_publish(self):
        self.run_status()
        counts = next((self.cache / 'worktrees').rglob('counts'))
        lines = counts.read_text().splitlines(); lines[2] = '0'; counts.write_text('\n'.join(lines) + '\n')
        before = counts.read_text()
        lock = counts.parent / 'refresh.lock'
        stolen = self.base / 'stolen'
        real_git = shutil.which('git')
        # During the first diff, another process reclaims the lock.
        self.wrapper('git', f'''if [[ ! -e "{stolen}" ]]; then
  rm -rf "{lock}" && mkdir "{lock}" && printf 'thief\\n99999999\\n\\n0\\n' > "{lock}/owner" && : > "{stolen}"
fi
exec "{real_git}" "$@"''')
        (self.cwd / 'file.txt').write_text('one\ntwo\nthree\n')
        self.run_status()  # run_status asserts empty stderr
        self.assertTrue(stolen.exists())
        self.assertEqual(counts.read_text(), before)
        self.assertEqual((lock / 'owner').read_text().split()[0], 'thief')
        self.assertFalse(list(counts.parent.glob('work.*')))

    def test_unwritable_cache_still_renders(self):
        address = f'yellowtail-f5 [{self.expected_ref()}]'
        parent = self.base / 'read-only'; parent.mkdir(); parent.chmod(0o500)
        self.addCleanup(parent.chmod, 0o700)
        output = self.run_status(env={'CLAUDE_STATUSLINE_CACHE_DIR': str(parent / 'cache')})
        self.assertIn(address, output)
        self.assertIn('⎇ ', output)
        self.run_status()
        for path in [self.cache / 'identity', self.cache]:
            path.chmod(0o500); self.addCleanup(path.chmod, 0o700)
        self.assertIn(address, self.run_status())
