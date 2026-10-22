import json
import os
import shutil
import subprocess
from support import StatusTest


class IdentityTests(StatusTest):
    def test_wrong_session_falls_back(self):
        self.record['sessionId'] = 'another-session'
        self.write_registry()
        output = self.run_status()
        self.assertIn('session ' + self.sid, output)
        self.assertNotIn('yellowtail', output)

    def test_pid_reuse_or_wrong_pid_falls_back(self):
        for key, value in [('procStart', 'Wed Jan 01 00:00:00 2000'), ('pid', self.pid + 1)]:
            original = self.record[key]
            self.record[key] = value
            self.write_registry()
            self.assertNotIn('yellowtail', self.run_status())
            self.record[key] = original

    def test_rename_with_equal_timestamp(self):
        self.run_status()
        path = self.registry / f'{self.pid}.json'
        before = path.stat()
        self.record['name'] = 'renamed-agent'
        self.write_registry()
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertIn('renamed-agent [' + self.expected_ref() + ']', self.run_status())

    def test_socket_change_rehashes(self):
        self.run_status()
        self.record['messagingSocketPath'] = '/tmp/another-fictional.sock'
        self.write_registry()
        self.assertIn('[' + self.expected_ref() + ']', self.run_status())

    def test_hash_not_recomputed_on_warm_or_rename(self):
        self.run_status()
        log = self.base / 'hash-called'
        self.wrapper('shasum', f'printf called >> "{log}"; exit 1')
        self.record['name'] = 'renamed'
        self.write_registry()
        self.assertIn('renamed [' + self.expected_ref() + ']', self.run_status())
        self.assertFalse(log.exists())

    def test_corrupt_and_missing_registry(self):
        path = self.registry / f'{self.pid}.json'
        self.run_status()
        path.write_text('{bad')
        self.assertIn('session ' + self.sid, self.run_status())
        path.unlink()
        self.assertIn('session ' + self.sid, self.run_status())

    def test_shared_session_id_uses_ancestry(self):
        other = {**self.record, 'pid': 99999999, 'name': 'wrong-agent'}
        (self.registry / '99999999.json').write_text(json.dumps(other))
        self.assertIn('yellowtail-f5', self.run_status())
        self.assertNotIn('wrong-agent', self.run_status())

    def test_corrupt_cache_recovers(self):
        self.run_status()
        (self.cache / 'identity' / str(self.pid)).write_text('broken\n')
        self.assertIn('[' + self.expected_ref() + ']', self.run_status())

    def test_control_character_in_socket_falls_back(self):
        self.record['messagingSocketPath'] += '\n'
        self.write_registry()
        self.assertIn('session ' + self.sid, self.run_status())

    def test_broken_sha256sum_falls_back_to_shasum(self):
        if not shutil.which('shasum'):
            self.skipTest('shasum is not installed')
        called = self.base / 'sha256sum-called'
        self.wrapper('sha256sum', f'printf called > "{called}"\nexit 1')
        self.assertIn(f'yellowtail-f5 [{self.expected_ref()}]', self.run_status())
        self.assertTrue(called.exists(), 'sha256sum should be tried first')
