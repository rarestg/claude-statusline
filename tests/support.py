import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'statusline.sh'


def proc_start(pid):
    """What Claude Code records as procStart: /proc/<pid>/stat field 22 on Linux, ps lstart elsewhere."""
    stat = Path(f'/proc/{pid}/stat')
    if stat.exists():
        return stat.read_text().rsplit(')', 1)[1].split()[19]
    return ' '.join(subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart='], text=True,
                                            env={**os.environ, 'TZ': 'UTC'}).split())


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='claude-statusline-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.registry = self.base / 'registry'
        self.registry.mkdir()
        self.cache = self.base / 'cache'
        self.cwd = self.base / 'working dir with spaces'
        self.cwd.mkdir()
        self.sid = 'session-under-test'
        self.pid = os.getpid()
        self.start = proc_start(self.pid)
        self.record = dict(pid=self.pid, procStart=self.start, sessionId=self.sid,
                           name='yellowtail-f5', messagingSocketPath='/tmp/fictional-statusline.sock')
        self.write_registry()
        self.env = {**os.environ, 'CLAUDE_STATUSLINE_REGISTRY_DIR': str(self.registry),
                    'CLAUDE_STATUSLINE_CACHE_DIR': str(self.cache), 'NO_COLOR': '1',
                    'COLUMNS': '120'}
        self.data = dict(session_id=self.sid, workspace=dict(current_dir=str(self.cwd)),
                         model=dict(display_name='Opus 4.6 (1M context)'), effort=dict(level='high'),
                         cost=dict(total_cost_usd=42.12), context_window=dict(used_percentage=42))

    def write_registry(self):
        (self.registry / f'{self.pid}.json').write_text(json.dumps(self.record))

    def run_status(self, data=None, env=None, timeout=12):
        result = subprocess.run(['/bin/bash', str(SCRIPT)], input=json.dumps(self.data if data is None else data),
                                text=True, capture_output=True, env={**self.env, **(env or {})}, timeout=timeout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return result.stdout

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.cwd), *args], text=True, stderr=subprocess.DEVNULL)

    def init_git(self):
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        (self.cwd / 'file.txt').write_text('one\ntwo\n')
        self.git('add', 'file.txt')
        self.git('commit', '-qm', 'fixture')

    def wrapper(self, name, body):
        bindir = self.base / 'bin'
        bindir.mkdir(exist_ok=True)
        path = bindir / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o755)
        self.env['PATH'] = str(bindir) + os.pathsep + os.environ['PATH']
        return path

    def expected_ref(self, socket=None):
        return hashlib.sha256(('session:' + (socket or self.record['messagingSocketPath'])).encode()).hexdigest()[:6]
