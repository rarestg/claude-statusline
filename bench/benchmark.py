#!/usr/bin/env python3
"""Measure one-shot CPU including descendants, with real caller PID fixtures."""
import argparse
import collections
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import re
import resource
import shlex
import shutil
import statistics
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def worker(connection, index, root, cwd, script, baseline):
    root = Path(root)
    pid = os.getpid()
    sid = f'benchmark-session-{index}'
    start = ' '.join(subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart='], text=True,
                                            env={**os.environ, 'TZ': 'UTC'}).split())
    (root / 'registry' / f'{pid}.json').write_text(json.dumps(dict(
        pid=pid, sessionId=sid, procStart=start, name=f'fixture-agent-{index}',
        messagingSocketPath=f'/tmp/benchmark-agent-{index}.sock')))
    env = {**os.environ, 'CLAUDE_STATUSLINE_REGISTRY_DIR': str(root / 'registry'),
           'CLAUDE_STATUSLINE_CACHE_DIR': str(root / 'cache'), 'CCSTATUSLINE_WIDTH': '120',
           'COLUMNS': '120', 'NO_COLOR': '1'}
    data = dict(session_id=sid, cwd=cwd, workspace=dict(current_dir=cwd),
                model=dict(display_name='Opus 4.6 (1M context)'), effort=dict(level='high'),
                cost=dict(total_cost_usd=12.34), context_window=dict(used_percentage=42,
                    context_window_size=1000000, current_usage=dict(input_tokens=1000,
                        cache_creation_input_tokens=400000, cache_read_input_tokens=19000)))
    connection.send('ready')
    while True:
        task = connection.recv()
        if task is None:
            return
        kind, iteration = task
        data['cost']['total_cost_usd'] = 12.34 + iteration
        command = ['/bin/bash', script] if kind != 'baseline' else baseline
        if kind == 'trace':
            command = ['/bin/bash', '-x', script]
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        tick = time.perf_counter()
        # Bash -x traces can echo raw bytes, so don't fail on invalid UTF-8.
        p = subprocess.run(command, input=json.dumps(data), capture_output=True, text=True, errors='replace',
                           env=env, cwd=cwd, timeout=15)
        wall = (time.perf_counter() - tick) * 1000
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        plain = p.stdout.replace('\u00a0', ' ')
        expected_name = f'fixture-agent-{index}'
        valid = p.returncode == 0 and expected_name in plain and f'${12.34 + iteration:.2f}' in plain
        if kind != 'baseline':
            home = str(Path.home())
            display_cwd = '~' + cwd[len(home):] if cwd == home or cwd.startswith(home + '/') else cwd
            valid = valid and '420k/1.0M (42%)' in plain and plain.splitlines()[-1] == display_cwd
        result = dict(wall_ms=wall, cpu_ms=1000 * (after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime),
                      valid=valid)
        if not valid:
            result['error'] = (p.stderr + '\n' + p.stdout)[:500]
        if kind == 'trace':
            result['helpers'] = dict(collections.Counter(re.findall(
                r'^\++\s+(ps|jq|date|git|shasum|sha256sum|mkdir|mv|rm|mkfifo|stat|rmdir)\b', p.stderr, re.M)))
        connection.send(result)


def summarize(rows):
    walls = sorted(r['wall_ms'] for r in rows)
    return dict(calls=len(rows), mean_cpu_ms=round(statistics.mean(r['cpu_ms'] for r in rows), 3),
                median_wall_ms=round(statistics.median(walls), 3),
                p95_wall_ms=round(walls[min(len(walls) - 1, int(len(walls) * .95))], 3),
                all_outputs_valid=all(r['valid'] for r in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', default=str(ROOT / 'statusline.sh'))
    parser.add_argument('--clients', type=int, default=22)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--duration', type=int, default=0, help='Repeat replacement at ten-second intervals for this duration')
    parser.add_argument('--baseline', default='ccstatusline')
    parser.add_argument('--live', action='store_true', help='Read-only Git checks in existing live session directories; inputs remain synthetic')
    parser.add_argument('--output', default=str(ROOT / 'bench/results.json'))
    args = parser.parse_args()
    if not 1 <= args.clients <= 64 or not 1 <= args.rounds <= 100:
        parser.error('clients must be 1..64 and rounds 1..100')
    context = multiprocessing.get_context('fork')
    runtime_hash = hashlib.sha256(Path(args.script).read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='statusline-bench-') as temp:
        root = Path(temp); (root / 'registry').mkdir()
        if args.live:
            cwds = []
            for path in (Path.home() / '.claude/sessions').glob('*.json'):
                try:
                    record = json.loads(path.read_text()); os.kill(int(record['pid']), 0)
                    if Path(record['cwd']).is_dir(): cwds.append(record['cwd'])
                except (OSError, ValueError, KeyError): pass
            if not cwds: raise RuntimeError('No live session directories found')
        else:
            repo = root / 'repo'; repo.mkdir()
            def git(*command):
                subprocess.run(['git', '-C', str(repo), *command], check=True, capture_output=True)
            git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'commit.gpgsign', 'false')
            (repo / 'file.txt').write_text('base\n'); git('add', 'file.txt'); git('commit', '-qm', 'fixture')
            cwds = [str(repo)]
            for i in range(3):
                path = root / f'worktree-{i}'; git('worktree', 'add', '-qb', f'fixture-{i}', str(path)); cwds.append(str(path))
        baseline = shlex.split(args.baseline)
        # Preserve the installed layout, redirecting only the legacy helper's
        # registry to benchmark fixtures. Never modify the installed files.
        config_path = Path.home() / '.config/ccstatusline/settings.json'
        if baseline == ['ccstatusline'] and config_path.exists():
            config = json.loads(config_path.read_text())
            agent_source = Path.home() / '.claude/statusline-agent-name.sh'
            if agent_source.exists():
                helper = root / 'legacy-agent.sh'
                helper.write_text(agent_source.read_text().replace('registry="$HOME/.claude/sessions"',
                                  'registry=' + shlex.quote(str(root / 'registry'))))
                helper.chmod(0o755)
                for line in config['lines']:
                    for item in line:
                        if item.get('commandPath', '').endswith('statusline-agent-name.sh'):
                            item['commandPath'] = str(helper)
            config_file = root / 'baseline-config.json'; config_file.write_text(json.dumps(config))
            baseline += ['--config', str(config_file)]
        children = []
        for i in range(args.clients):
            parent, child = context.Pipe()
            process = context.Process(target=worker, args=(child, i, temp, cwds[i % len(cwds)], str(Path(args.script).resolve()), baseline))
            process.start(); child.close(); children.append((process, parent))
        try:
            for _, connection in children: assert connection.recv() == 'ready'
            phases = collections.defaultdict(list); batches = collections.defaultdict(list)
            def batch(kind, iteration):
                tick = time.perf_counter()
                for _, connection in children: connection.send((kind, iteration))
                rows = [connection.recv() for _, connection in children]
                batches[kind].append((time.perf_counter() - tick) * 1000)
                phases[kind].extend(rows)
                for row in rows:
                    if not row['valid']: raise RuntimeError(row)
            batch('cold', 0)
            for i in range(args.rounds): batch('warm', i + 1)
            for i in range(min(args.rounds, 3)): batch('baseline', i + 1)
            # Force a shared expired-cache round without sleeping ten seconds.
            for path in (root / 'cache/worktrees').rglob('counts'):
                lines = path.read_text().splitlines(); lines[2] = '0'; path.write_text('\n'.join(lines) + '\n')
            batch('expired', 1)
            batch('trace', 1)
            if args.duration:
                end = time.monotonic() + args.duration
                iteration = 0
                while time.monotonic() < end:
                    batch('observed', iteration); iteration += 1
                    print(json.dumps(dict(observed_round=iteration)), flush=True)
                    time.sleep(min(10, max(0, end - time.monotonic())))
            report = dict(platform=platform.platform(), clients=args.clients, worktrees=len(set(cwds)),
                          inputs='Synthetic status JSON; no transcript. Real PID/process-start identity fixtures.',
                          phases={k: summarize(v) for k, v in phases.items() if k != 'trace'},
                          batch_wall_ms={k: [round(x, 2) for x in v] for k, v in batches.items() if k != 'trace'},
                          warm_helper_calls_per_request=phases['trace'][0]['helpers'])
            report['runtime_sha256'] = runtime_hash
            report['runtime_unchanged_during_measurement'] = runtime_hash == hashlib.sha256(Path(args.script).read_bytes()).hexdigest()
            baseline_cpu = report['phases']['baseline']['mean_cpu_ms']
            for phase in ['warm', 'expired', 'observed']:
                if phase in report['phases']:
                    report[phase + '_cpu_reduction_percent'] = round(100 * (1 - report['phases'][phase]['mean_cpu_ms'] / baseline_cpu), 2)
            report['cpu_reduction_phase'] = 'observed' if args.duration else 'warm'
            report['cpu_reduction_percent'] = report[report['cpu_reduction_phase'] + '_cpu_reduction_percent']
            Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2), flush=True)
        finally:
            for process, connection in children:
                try: connection.send(None)
                except (BrokenPipeError, EOFError): pass
            for process, connection in children:
                process.join(5)
                if process.is_alive(): process.terminate(); process.join(5)
                connection.close()


if __name__ == '__main__':
    main()
