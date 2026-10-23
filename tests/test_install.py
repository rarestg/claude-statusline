import json
import os
from pathlib import Path
import subprocess
from support import ROOT, StatusTest


class InstallTests(StatusTest):
    def setUp(self):
        super().setUp()
        self.settings = self.base / 'settings.json'
        self.destination = self.base / 'installed statusline.sh'
        self.initial = {'hooks': {'unchanged': True}, 'theme': 'auto', 'statusLine': {'command': 'old', 'type': 'command'}}
        self.settings.write_text(json.dumps(self.initial))
        self.settings.chmod(0o640)

    def install(self, *extra):
        return subprocess.run(['/bin/bash', str(ROOT / 'scripts/install.sh'), '--settings', str(self.settings),
                               '--destination', str(self.destination), *extra], text=True, capture_output=True, env=self.env)

    def backup(self):
        return next((self.base / 'statusline-backups').iterdir())

    def restore(self):
        return subprocess.run(['/bin/bash', str(ROOT / 'scripts/uninstall.sh'), '--restore-statusline', str(self.backup())], text=True, capture_output=True)

    def test_dry_run_changes_nothing(self):
        before = self.settings.read_bytes()
        p = self.install('--dry-run')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.base / 'statusline-backups').exists())

    def test_install_idempotence_and_restore_keep_unrelated_edits(self):
        p = self.install(); self.assertEqual(p.returncode, 0, p.stderr)
        installed = json.loads(self.settings.read_text())
        self.assertEqual(installed['hooks'], self.initial['hooks'])
        self.assertEqual(installed['statusLine']['refreshInterval'], 30)
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o640)
        self.assertTrue(self.destination.exists())
        self.assertIn('Already installed', self.install().stdout)
        self.assertEqual(len(list((self.base / 'statusline-backups').iterdir())), 1)
        installed['theme'] = 'dark'; self.settings.write_text(json.dumps(installed))
        p = self.restore(); self.assertEqual(p.returncode, 0, p.stderr)
        restored = json.loads(self.settings.read_text())
        self.assertEqual(restored['statusLine'], self.initial['statusLine'])
        self.assertEqual(restored['theme'], 'dark')
        self.assertFalse(self.destination.exists())

    def test_settings_symlink_preserved(self):
        real = self.base / 'real.json'; self.settings.rename(real); self.settings.symlink_to(real.name)
        p = self.install(); self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.settings.is_symlink())
        p = self.restore(); self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(self.settings.is_symlink())

    def test_refuse_to_overwrite_later_statusline_change(self):
        self.assertEqual(self.install().returncode, 0)
        current = json.loads(self.settings.read_text()); current['statusLine'] = {'command': 'later'}
        self.settings.write_text(json.dumps(current))
        self.assertNotEqual(self.restore().returncode, 0)
        self.assertEqual(json.loads(self.settings.read_text())['statusLine'], {'command': 'later'})

    def test_missing_previous_statusline_is_removed_on_restore(self):
        self.settings.write_text('{"theme":"dark"}')
        self.assertEqual(self.install().returncode, 0)
        p = self.restore(); self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(self.settings.read_text()), {'theme': 'dark'})

    def test_quoted_destination_executes_correctly(self):
        self.destination = self.base / "installed 'quoted' statusline.sh"
        p = self.install(); self.assertEqual(p.returncode, 0, p.stderr)
        command = json.loads(self.settings.read_text())['statusLine']['command']
        p = subprocess.run(['/bin/sh', '-c', command], input=json.dumps(self.data), text=True, capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('yellowtail-f5', p.stdout)

    def test_concurrent_settings_edit_restores_previous_runtime(self):
        import shutil
        self.destination.write_text('original runtime')
        real_mv = shutil.which('mv')
        real_jq = shutil.which('jq')
        self.wrapper('mv', f'''"{real_mv}" "$@" || exit $?
if [[ ${{!#}} == "{self.destination}" ]]; then
  "{real_jq}" '.theme="concurrent"' "{self.settings}" > "{self.settings}.edit"
  "{real_mv}" "{self.settings}.edit" "{self.settings}"
fi''')
        p = self.install()
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.destination.read_text(), 'original runtime')
        self.assertEqual(json.loads(self.settings.read_text())['theme'], 'concurrent')

    def test_claude_config_dir_is_respected(self):
        config = self.base / 'config dir'; config.mkdir()
        (config / 'settings.json').write_text('{"theme": "dark"}')
        home = self.base / 'home'; home.mkdir()
        p = subprocess.run(['/bin/bash', str(ROOT / 'scripts/install.sh')], text=True, capture_output=True,
                           env={**self.env, 'CLAUDE_CONFIG_DIR': str(config), 'HOME': str(home)})
        self.assertEqual(p.returncode, 0, p.stderr)
        settings = json.loads((config / 'settings.json').read_text())
        self.assertIn(str(config / 'statusline.sh'), settings['statusLine']['command'])
        self.assertEqual(settings['theme'], 'dark')
        self.assertTrue((config / 'statusline.sh').is_file())
        self.assertFalse((home / '.claude').exists())

    def test_read_only_settings_are_refused(self):
        self.settings.chmod(0o440)
        p = self.install()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("isn't writable", p.stderr)
        self.assertEqual(json.loads(self.settings.read_text()), self.initial)
        self.assertFalse(self.destination.exists())

    def test_restore_replaces_runtime_without_writing_in_place(self):
        self.destination.write_text('original runtime')
        self.assertEqual(self.install().returncode, 0)
        installed_inode = self.destination.stat().st_ino
        self.assertEqual(self.restore().returncode, 0)
        self.assertEqual(self.destination.read_text(), 'original runtime')
        self.assertNotEqual(self.destination.stat().st_ino, installed_inode)

    def test_restore_keeps_an_edit_made_during_uninstall(self):
        import shutil
        self.assertEqual(self.install().returncode, 0)
        real_jq = shutil.which('jq'); real_mv = shutil.which('mv')
        # Edit the settings while uninstall checks the installed statusLine.
        self.wrapper('jq', f'''if [[ "$*" == *'.statusLine == $manifest.installed'* ]]; then
  "{real_jq}" '.theme="concurrent"' "{self.settings}" > "{self.settings}.edit" && "{real_mv}" "{self.settings}.edit" "{self.settings}"
fi
exec "{real_jq}" "$@"''')
        p = subprocess.run(['/bin/bash', str(ROOT / 'scripts/uninstall.sh'), '--restore-statusline', str(self.backup())],
                           text=True, capture_output=True, env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(json.loads(self.settings.read_text())['theme'], 'concurrent')
