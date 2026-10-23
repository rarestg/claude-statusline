# claude-statusline

A Claude Code status line that stays cheap when you run many sessions at once. It's one Bash script. A warm refresh starts `ps` and `jq`, plus `date` on Bash older than 5.0, like the one macOS ships. It starts no Node process, reads no transcripts, makes no network requests, and runs no daemon.

```text
yellowtail-f5 [8d651c] · Opus 5.5 · high
⎇ fix-login · PR #42 · +12 −3 · $4.12 · [████░░░░░░] 84k/200k (42%)
~/code/my-app · 3f2a9c1e-7b4d-4e2a-9c1f-5d8e6b0a4c21
```

It replaces a [ccstatusline](https://github.com/sirmalloc/ccstatusline) setup that cost 0.3–0.45 s of CPU per refresh. Claude Code refreshes each session's status line every 10 seconds, so with 20+ sessions open, CPU spiked every 10 seconds. With 22 sessions refreshing at once, this script costs about 20 ms per refresh. See [Measurements](#measurements).

## What it shows

- **Line 1:** the session's `/list-agents` address, the name other sessions use to message it, then the model and effort level.
- **Line 2:** the Git branch, a clickable `PR #…` badge when Claude Code reports an open PR, staged plus unstaged line changes, session cost, and context usage.
- **Line 3:** the current directory, with your home directory shortened to `~`, then the session ID, which `claude --resume` takes. The directory is a `file://` link, so terminals that support file links can open it.

Context usage adds up the current input, cache creation, and cache read tokens. It leaves out output tokens and session totals. When Claude Code supplies a percentage, the script shows that one.

On narrow terminals, the context bar shrinks first, then optional details drop out. The address and directory always stay; long paths may wrap. The session ID drops out when it doesn't fit after the directory.

If the script can't verify which Claude Code process it belongs to, line 1 shows `session <session ID>` instead of an address, and line 3 doesn't repeat the ID. A wrong address would send other sessions' messages to the wrong place.

## Install

You need Bash 3.2 or newer at `/bin/bash`, `jq`, Git, `ps`, `date`, and `sha256sum` or `shasum`. Git refreshes also use `mkfifo` and other standard utilities. It's tested on macOS 26.5.1 with the system Bash 3.2 and Claude Code 2.1.306. Linux should work, but it hasn't been tested.

```sh
git clone https://github.com/rarestg/claude-statusline.git
cd claude-statusline
bash scripts/install.sh --dry-run   # shows what it will change
bash scripts/install.sh
```

The installer copies `statusline.sh` to `~/.claude/statusline.sh` and changes only `statusLine` in `~/.claude/settings.json`. If you set `CLAUDE_CONFIG_DIR`, it uses that directory instead of `~/.claude`. It keeps your other settings, the file's permissions, and a symlinked `settings.json`, and it stops if `settings.json` isn't writable. It prints a backup directory; keep that path, because uninstalling needs it. Running sessions pick up the new status line on their next refresh, without a restart.

For other locations, pass `--settings /absolute/path/settings.json --destination /absolute/path/statusline.sh`.

To set it up by hand, copy `statusline.sh` to `~/.claude/` and add this to your settings:

```json
{
  "statusLine": {
    "type": "command",
    "command": "exec /bin/bash ~/.claude/statusline.sh",
    "padding": 0,
    "refreshInterval": 10
  }
}
```

## Uninstall

```sh
bash scripts/uninstall.sh --restore-statusline /path/to/backup/printed/by/install
```

This restores the previous `statusLine` value and the previous `~/.claude/statusline.sh`, or removes the script if there wasn't one. If either has changed since you installed, or `settings.json` changes while it runs, it stops and tells you what changed instead of overwriting it. Each install backs up only the version right before it, so to undo several upgrades, restore their backups newest first.

## How it stays cheap

- **Session values come from Claude Code's input.** Model, effort, cost, context, and the PR URL arrive in the JSON that Claude Code sends on every refresh. The script never caches a rendered line, so these are always current.
- **The address is checked on every refresh, and the hash is computed once.** The script walks up its parent processes to its Claude Code process and reads that process's registry file in `~/.claude/sessions/`. One `ps` call confirms the PID, session ID, and process start time, which catches a reused PID. The hash is cached per process and socket path. Because the registry file is read every time, a `/rename` shows up on the next refresh.
- **Sessions in the same worktree share Git counts.** When the cached counts are more than 10 seconds old, the first session to notice takes a `mkdir` lock and refreshes them. The others keep showing the cached counts and don't wait. Linked worktrees have separate caches. The branch and `HEAD` are read with Bash builtins, without starting Git.
- **Git work is bounded.** Each `git diff` has a 2-second deadline, enforced by a watchdog that survives even if the status line itself is killed. A failed refresh backs off for 5 seconds, and a lock left by a killed process is recovered. If two sessions reclaim a stale lock at the same moment, both refresh, but each writes to its own work directory and only one publishes. These calls turn off optional locks, fsmonitor, external diff tools, and textconv, and run the index check on one thread (`core.preloadIndex=false`, `index.threads=1`). In large repositories, Git's parallel index preload used hundreds of milliseconds of CPU to save a few dozen milliseconds of wall time. The settings apply to these calls only; your Git config isn't changed.

Caches live in `${XDG_CACHE_HOME:-~/.cache}/claude-statusline`, readable only by you. They aren't pruned automatically. They're small, and deleting the directory is safe; the next refresh rebuilds them. If the directory can't be written, the status line still renders, without Git change counts.

## Configuration

Set these in the `statusLine` command, for example `"command": "CLAUDE_STATUSLINE_GIT_TTL=30 exec /bin/bash ~/.claude/statusline.sh"`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLAUDE_STATUSLINE_GIT_TTL` | `10` | Seconds before Git counts are refreshed, 1–60 |
| `CLAUDE_STATUSLINE_GIT_TIMEOUT` | `2` | Deadline per `git diff` in seconds, 1–10 |
| `CLAUDE_STATUSLINE_COLOR` | `1` | `0` turns off colors |
| `CLAUDE_STATUSLINE_LINKS` | `1` | `0` turns off the PR and directory links |
| `NO_COLOR` | unset | When set, turns off colors and links |
| `CLAUDE_STATUSLINE_CACHE_DIR` | `${XDG_CACHE_HOME:-~/.cache}/claude-statusline` | Cache location |
| `CLAUDE_STATUSLINE_REGISTRY_DIR` | `${CLAUDE_CONFIG_DIR:-~/.claude}/sessions` | Session registry location |
| `COLUMNS` | `120` | Terminal width; Claude Code normally sets it |

## Limits

- **The address relies on undocumented Claude Code internals:** the registry files in `~/.claude/sessions/` and the formula for the `[ref]`. The script doesn't handle the `tengu_session_stable_address` feature flag or the longer refs `/list-agents` uses when two collide. After upgrading Claude Code, check line 1 against `/list-agents` before you rely on it. [docs/compatibility.md](docs/compatibility.md) has the details.
- **Git clean filters still run.** A clean filter set in a repository's config and `.gitattributes`, such as Git LFS, runs during the diff, the same as when you run `git diff` there yourself.
- **Linux is untested.** The installed command calls `/bin/bash`, which NixOS doesn't have. BusyBox `ps`, as on Alpine, can't report process start times, so line 1 falls back to the session ID.
- **Caches assume one machine.** If `~/.cache` is shared between machines or containers, a lock held on one can look abandoned from another. Counts stay correct, but refreshes may repeat.
- Bare repositories and custom `GIT_DIR` or `GIT_WORK_TREE` layouts aren't supported.
- There's no widget or layout configuration. To change the layout, edit the script.

## Measurements

CPU per call, including child processes. These were measured on an 18-core Apple Silicon Mac on 2026-10-22, with 22 callers at once across four Git worktrees and synthetic status input:

| Case | This script | Previous ccstatusline setup |
| --- | ---: | ---: |
| Warm caches | 19.2 ms | 404 ms |
| Git counts expired | 32.7 ms | 404 ms |
| First run, empty caches | 41.9 ms | — |

A single caller with warm caches costs 12.7 ms. The ccstatusline column is version 2.2.32 running the layout this script replaces. It got no transcript, so the comparison favors it. With a real transcript, ccstatusline also parses the whole file on every refresh. Raw numbers are in [bench/results.json](bench/results.json) and [bench/single-client-results.json](bench/single-client-results.json). [bench/earlier/](bench/earlier/) holds results from earlier versions of the script.

## Tests and benchmarks

Python 3 is needed only for these:

```sh
/bin/bash -n statusline.sh scripts/common.sh scripts/install.sh scripts/uninstall.sh
python3 -m unittest discover -s tests -v
python3 -S bench/benchmark.py --clients 22 --rounds 5
```

The tests and benchmark use temporary registries, caches, and Git repositories. They don't touch your settings or sessions. The benchmark compares against `ccstatusline` by default; pass `--baseline '<command>'` to compare against a different status line command.

## License

[MIT](LICENSE)
