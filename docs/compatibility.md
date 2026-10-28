# Agent-address compatibility

Agent display names and messaging refs are undocumented Claude Code internals. This script uses the same socket-based address formula as the [ccstatusline setup](https://gist.github.com/rarestg/3592a553adde3ee97e95df59ee3e35c2) it replaces:

```text
name + " [" + SHA256("session:" + messagingSocketPath)[0:6] + "]"
```

Registry source: `${CLAUDE_CONFIG_DIR:-~/.claude}/sessions/<pid>.json`. The selected PID must be in the invocation ancestry, its process must be alive, and its registry `pid`, `sessionId`, and `procStart` must match the request and live process. On Linux and WSL, process starts are compared as field 22 of `/proc/<pid>/stat`, the start time in clock ticks since boot, which is what Claude Code records there. Elsewhere they're compared with `LC_ALL=C TZ=UTC ps -o lstart=`. The UTC normalization matters: local-time ps output differed by seven hours from all 23 live registry records during development.

The installer uses `exec` to avoid an extra shell between Claude and the runtime. A bounded parent walk supports other wrappers, at additional cost. Ambiguous, stale, malformed, missing, or mismatched identities display a labeled session ID.

The script reads the selected registry file on every request rather than relying on `file -nt cache`: this makes same-second renames and atomic replacement visible without extra metadata commands. The one jq invocation validates input and registry data together. Only the hash and associated identity fields are cached; a changed socket or process incarnation invalidates it.

Known limitations preserved from the original setup:

- `tengu_session_stable_address` can change the hash input to a session-ID-based address. This version does not detect or implement that feature flag. It targets the socket-based behavior established for the existing installation; revalidate after Claude upgrades.
- `/list-agents` can lengthen refs on collisions. This script uses six characters. All 23 live socket-derived refs were unique during validation, but collision resolution is not implemented.
- `claude agents --json` exposes names/PIDs/session IDs, but not socket paths or full messaging refs in the installed version. It can validate names, not independently prove the ref formula. Compare `/list-agents` after upgrades before relying on cross-session addressing.
- If the parent Claude Code process has no registry file yet, for example right at startup, the parent walk continues upward. An ancestor Claude Code process with the same session ID would then pass every check, and line 1 would show its address. It would take nested Claude Code processes sharing one session ID, which hasn't been observed.
- Claude Code 2.1.312's Linux build records `procStart` from `/proc/<pid>/stat` field 22 and never calls `ps` for it; the macOS build uses `LC_ALL=C TZ=UTC ps -o lstart=`. The script reads the same source on each platform, so BusyBox `ps` without `lstart` doesn't affect identity on Linux. Windows records `procStartFt` instead and isn't supported.
- macOS Bash 3.2 was exercised. On Linux, the tests and a live session were verified on kernel 6.12 with Bash 5.2, procps-ng 4.0.4, and Claude Code 2.1.312.

No general session-name compatibility claim is made for future Claude releases. [Claude, What's Your Real Name?](https://rares.blog/articles/claude-code-statusline-session-name) describes how the formula was found.
