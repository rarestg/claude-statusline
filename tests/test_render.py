import json
import os
import re
import subprocess
import time
from urllib.parse import quote
from support import SCRIPT, StatusTest


class RenderTests(StatusTest):
    def test_fields_and_current_values(self):
        output = self.run_status()
        self.assertIn('yellowtail-f5 [' + self.expected_ref() + ']', output)
        self.assertIn('Opus 4.6', output)
        self.assertIn('high', output)
        self.assertIn('$42.12', output)
        self.assertIn('42%', output)
        self.data['cost']['total_cost_usd'] = 13.37
        self.data['context_window']['used_percentage'] = 65
        self.data['effort']['level'] = 'low'
        output = self.run_status()
        self.assertIn('$13.37', output)
        self.assertIn('65%', output)
        self.assertIn('low', output)
        self.assertNotIn('$42.12', output)

    def test_optional_fields(self):
        output = self.run_status({'session_id': self.sid})
        self.assertIn('yellowtail-f5', output)
        self.assertNotIn('null', output)

    def test_malformed_optional_objects_are_omitted(self):
        output = self.run_status(dict(session_id=self.sid, workspace=12, effort='bad', cost=[], pr=True, context_window='bad'))
        self.assertIn('yellowtail-f5', output)
        self.assertNotIn('null', output)

    def test_malformed_and_large_input(self):
        for raw in ['{broken', '[]', 'null', 'x' * 1048577]:
            p = subprocess.run(['/bin/bash', str(SCRIPT)], input=raw, text=True, capture_output=True, env=self.env, timeout=5)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, '', ''))

    def test_no_transcript_read(self):
        import os
        fifo = self.base / 'transcript.jsonl'
        os.mkfifo(fifo)
        self.data['transcript_path'] = str(fifo)
        self.assertIn('yellowtail-f5', self.run_status(timeout=2))

    def test_control_characters_removed(self):
        self.record['name'] = 'safe\x1b]52;c;bad\x07\nname'
        self.write_registry()
        self.data['model']['display_name'] = 'Claude\x1b[31m'
        output = self.run_status()
        self.assertNotIn('\x1b', output)
        self.assertNotIn('\x07', output)
        self.assertEqual(len(output.splitlines()), 3)

    def test_context_percentage_fallback(self):
        self.data['context_window'] = dict(context_window_size=1000, current_usage=dict(input_tokens=100, cache_read_input_tokens=200))
        self.assertIn('30%', self.run_status())

    def test_context_tokens_use_current_input_and_caches(self):
        # Sanitized numbers from the live 1M session: historical totals and the
        # current output count must not be added to context-window occupancy.
        self.data['context_window'] = dict(
            context_window_size=1000000, used_percentage=58,
            total_input_tokens=9000000, total_output_tokens=100000,
            current_usage=dict(input_tokens=2, output_tokens=117,
                               cache_creation_input_tokens=573787, cache_read_input_tokens=10670))
        self.assertIn('[██████░░░░] 584k/1.0M (58%)', self.run_status())
        self.data['context_window'].update(used_percentage=46,
            current_usage=dict(input_tokens=6000, cache_read_input_tokens=450000))
        output = self.run_status()
        self.assertIn('456k/1.0M (46%)', output)
        self.assertNotIn('584k', output)

    def test_context_capacity_and_percentage_fallback(self):
        for limit, used, expected in [(200000, 46000, '46k/200k (23%)'),
                                      (1000000, 0, '0/1.0M (0%)'),
                                      (1000, 300, '300/1k (30%)')]:
            self.data['context_window'] = dict(context_window_size=limit,
                                               current_usage=dict(input_tokens=used))
            self.assertIn(expected, self.run_status())

    def test_missing_current_usage_does_not_show_cumulative_tokens(self):
        self.data['context_window'] = dict(context_window_size=1000000, used_percentage=58,
                                           total_input_tokens=9000000, current_usage=None)
        output = self.run_status()
        self.assertIn('58%', output)
        self.assertNotIn('/1.0M', output)
        self.assertNotIn('9.0M', output)

    def test_invalid_context_counts_degrade_to_percentage(self):
        for usage in [True, [], 'broken', dict(input_tokens=-1), dict(input_tokens=1e20)]:
            self.data['context_window'] = dict(context_window_size=1000000, used_percentage=58,
                                               current_usage=usage)
            self.assertIn('] 58%', self.run_status())

    def test_narrow_token_counts_fit_with_pr_and_branch(self):
        self.init_git()
        self.data['context_window'] = dict(context_window_size=1000000, used_percentage=46,
                                           current_usage=dict(input_tokens=456000))
        self.data['pr'] = dict(number=36566, url='https://github.com/example/project/pull/36566')
        for width in [20, 30, 40, 60, 80, 120]:
            output = self.run_status(env={'COLUMNS': str(width)})
            self.assertLessEqual(len(output.splitlines()[1]), width)
            self.assertIn('46%', output)
            self.assertIn('$42.12', output)
            if width >= 30:
                self.assertIn('456k/1.0M (46%)', output)

    def test_missing_pr_does_not_trigger_a_network_lookup(self):
        self.init_git()
        self.data['workspace']['repo'] = dict(host='github.com', owner='example', name='project')
        marker = self.base / 'network-called'
        for command in ['gh', 'curl']:
            self.wrapper(command, f'printf called >> "{marker}"; exit 1')
        self.assertNotIn('PR #', self.run_status())
        self.assertFalse(marker.exists())

    def test_bad_pr_url_does_not_create_link(self):
        self.init_git()
        self.data['pr'] = dict(url='javascript:alert(1)')
        env = {k: v for k, v in self.env.items() if k != 'NO_COLOR'}
        p = subprocess.run(['/bin/bash', str(SCRIPT)], input=json.dumps(self.data), text=True, capture_output=True, env=env)
        self.assertNotIn('javascript:', p.stdout)
        self.assertIn('\x1b]8;;file://', p.stdout)

    def test_width_does_not_truncate_identity(self):
        output = self.run_status(env={'COLUMNS': '20'})
        self.assertIn('yellowtail-f5 [' + self.expected_ref() + ']', output)
        self.assertNotIn('Opus', output)

    def test_narrow_metrics_do_not_wrap(self):
        self.init_git()
        output = self.run_status(env={'COLUMNS': '20'})
        self.assertLessEqual(len(output.splitlines()[1]), 20)
        self.assertIn('$42.12', output)

    def test_unicode_branch_truncation_remains_valid(self):
        self.init_git()
        self.git('checkout', '-qb', '日本語' * 15)
        output = self.run_status(env={'COLUMNS': '70'})
        self.assertNotIn('\ufffd', output)

    def test_identity_is_independent_of_caller_timezone(self):
        for timezone in ['America/Los_Angeles', 'Asia/Tokyo', 'UTC']:
            self.assertIn('yellowtail-f5 [' + self.expected_ref() + ']', self.run_status(env={'TZ': timezone}))

    def test_context_bar_tracks_percentage(self):
        for percentage, expected in [(0, '[░░░░░░░░░░] 0%'), (58, '[██████░░░░] 58%'), (100, '[██████████] 100%')]:
            self.data['context_window']['used_percentage'] = percentage
            self.assertIn(expected, self.run_status())

    def test_pr_badge_has_its_own_hyperlink(self):
        self.init_git()
        self.data['pr'] = dict(number=36566, url='https://github.com/example/project/pull/36566')
        env = {k: v for k, v in self.env.items() if k != 'NO_COLOR'}
        p = subprocess.run(['/bin/bash', str(SCRIPT)], input=json.dumps(self.data), text=True, capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        links = re.findall(r'\x1b\]8;;([^\x1b]+)\x1b\\(.*?)\x1b\]8;;\x1b\\', p.stdout)
        self.assertEqual(links, [('https://github.com/example/project/pull/36566', 'PR #36566'),
                                 (self.cwd.as_uri(), str(self.cwd))])
        self.assertIn('⎇ ', p.stdout)
        self.assertNotIn('Cost:', p.stdout)
        self.assertNotIn('Thinking:', p.stdout)

    def test_pr_badge_disappears_with_pr_field(self):
        self.data['pr'] = dict(number=36566, url='https://github.com/example/project/pull/36566')
        self.assertIn('PR #36566', self.run_status())
        del self.data['pr']
        self.assertNotIn('PR #', self.run_status())

    def test_pr_number_can_be_derived_from_url(self):
        for url in ['https://github.com/example/project/pull/36566', 'https://gitlab.com/example/project/-/merge_requests/36566?x=1']:
            self.data['pr'] = dict(url=url)
            self.assertIn('PR #36566', self.run_status())

    def test_pr_number_without_url_is_plain_badge(self):
        self.data['pr'] = dict(number=36566)
        self.assertIn('PR #36566', self.run_status())

    def test_narrow_pr_and_bar_fit_and_links_can_be_disabled(self):
        self.data['pr'] = dict(number=36566, url='https://github.com/example/project/pull/36566')
        for width in [20, 30, 40, 60, 80, 120]:
            out = self.run_status(env={'COLUMNS': str(width)})
            self.assertLessEqual(len(out.splitlines()[1]), width)
            self.assertIn('42%', out)
        env = {k: v for k, v in self.env.items() if k != 'NO_COLOR'}
        env['CLAUDE_STATUSLINE_LINKS'] = '0'
        p = subprocess.run(['/bin/bash', str(SCRIPT)], input=json.dumps(self.data), text=True, capture_output=True, env=env)
        self.assertIn('PR #36566', p.stdout)
        self.assertNotIn('\x1b]8;', p.stdout)

    def test_directory_link_uses_file_url_and_home_abbreviation(self):
        self.data['workspace']['current_dir'] = str(self.cwd / 'folder #?% ü')
        env = {k: v for k, v in self.env.items() if k != 'NO_COLOR'}
        env['HOME'] = str(self.base)
        p = subprocess.run(['/bin/bash', str(SCRIPT)], input=json.dumps(self.data), text=True,
                           capture_output=True, env=env)
        self.assertEqual((p.returncode, p.stderr), (0, ''))
        links = re.findall(r'\x1b\]8;;([^\x1b]+)\x1b\\(.*?)\x1b\]8;;\x1b\\', p.stdout)
        path = self.data['workspace']['current_dir']
        self.assertEqual(links, [('file://' + quote(path, safe='/'),
                                 '~/working dir with spaces/folder #?% ü')])
        self.assertEqual(len(p.stdout.splitlines()), 3)

    def test_home_abbreviation_respects_path_boundaries(self):
        for path, expected in [('/Users/fixture', '~'),
                               ('/Users/fixture/folder', '~/folder'),
                               ('/Users/fixture-other/folder', '/Users/fixture-other/folder')]:
            self.data['workspace']['current_dir'] = path
            self.assertEqual(self.run_status(env={'HOME': '/Users/fixture'}).splitlines()[-1], expected)

    def test_directory_falls_back_to_cwd_and_is_optional(self):
        self.data.pop('workspace')
        self.data['cwd'] = '/tmp/example'
        self.assertEqual(self.run_status().splitlines()[-1], '/tmp/example')
        self.data.pop('cwd')
        self.assertEqual(len(self.run_status().splitlines()), 2)

    def test_branch_with_c1_controls_is_hidden(self):
        self.init_git()
        head = self.cwd / '.git' / 'HEAD'
        # U+009D and U+009C wrap an OSC 52 clipboard write in their C1 form.
        head.write_bytes(b'ref: refs/heads/osc\xc2\x9d52;c;aGVsbG8=\xc2\x9c\n')
        output = self.run_status()
        self.assertNotIn('\u009d', output)
        self.assertNotIn('osc', output)
        head.write_text('ref: refs/heads/für 日本\n', encoding='utf-8')
        self.assertIn('für 日本', self.run_status())
