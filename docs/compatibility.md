# Agent-address compatibility

Agent display names and messaging refs are undocumented Claude Code internals. This script uses the same socket-based address formula as the [ccstatusline setup](https://gist.github.com/rarestg/3592a553adde3ee97e95df59ee3e35c2) it replaces:

```text
name + " [" + SHA256("session:" + messagingSocketPath)[0:6] + "]"
```

Registry source: `${CLAUDE_CONFIG_DIR:-~/.claude}/sessions/<pid>.json`. The selected PID must be in the invocation ancestry, its process must be alive, and its registry `pid`, `sessionId`, and `procStart` must match the request and live process. Process starts are compared with `LC_ALL=C TZ=UTC ps -o lstart=`. The UTC normalization matters: local-time ps output differed by seven hours from all 23 live registry records during development.

The installer uses `exec` to avoid an extra shell between Claude and the runtime. A bounded parent walk supports other wrappers, at additional cost. Ambiguous, stale, malformed, missing, or mismatched identities display a labeled session ID.

The script reads the selected registry file on every request rather than relying on `file -nt cache`: this makes same-second renames and atomic replacement visible without extra metadata commands. The one jq invocation validates input and registry data together. Only the hash and associated identity fields are cached; a changed socket or process incarnation invalidates it.

Known limitations preserved from the original setup:

- `tengu_session_stable_address` can change the hash input to a session-ID-based address. This version does not detect or implement that feature flag. It targets the socket-based behavior established for the existing installation; revalidate after Claude upgrades.
- `/list-agents` can lengthen refs on collisions. This script uses six characters. All 23 live socket-derived refs were unique during validation, but collision resolution is not implemented.
- `claude agents --json` exposes names/PIDs/session IDs, but not socket paths or full messaging refs in the installed version. It can validate names, not independently prove the ref formula. Compare `/list-agents` after upgrades before relying on cross-session addressing.
- If the parent Claude Code process has no registry file yet, for example right at startup, the parent walk continues upward. An ancestor Claude Code process with the same session ID would then pass every check, and line 1 would show its address. It would take nested Claude Code processes sharing one session ID, which hasn't been observed.
- Claude Code records `procStart` with `LC_ALL=C TZ=UTC ps -o lstart=` on every non-Windows platform, so the start-time check should work on Linux. BusyBox `ps` has no `lstart`, so identity falls back to the session ID there. Windows records `procStartFt` instead and isn't supported.
- macOS Bash 3.2 was exercised. Linux portability has not been verified in a Linux environment.

No general session-name compatibility claim is made for future Claude releases. [Claude, What's Your Real Name?](https://rares.blog/articles/claude-code-statusline-session-name) describes how the formula was found.
