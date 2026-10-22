#!/bin/bash
set -e
script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)
source "$script_dir/common.sh"
[[ ${1:-} == --restore-statusline && $# == 2 ]] || fail 'Usage: uninstall.sh --restore-statusline BACKUP_DIRECTORY'
backup=$2
[[ -f $backup/manifest.json ]] || fail 'Missing backup manifest.'
manifest=$(cat "$backup/manifest.json")
jq -e '.version == 1 and (.settings|type == "string") and (.destination|type == "string")' <<< "$manifest" >/dev/null || fail 'Invalid backup manifest.'
settings=$(jq -r .settings <<< "$manifest")
destination=$(jq -r .destination <<< "$manifest")
target=$(resolve_target "$settings")
[[ -f $target ]] || fail 'Current settings are missing; review the backup manually.'
require_writable "$target"
# Build the restored settings from one snapshot. atomic_settings replaces the
# file only if it still matches that snapshot, so an edit made while this runs
# is never overwritten.
snapshot=$target.$$.$RANDOM.snapshot
replacement=$target.$$.$RANDOM.tmp
trap 'rm -f "$snapshot" "$replacement"' EXIT
cp -p "$target" "$snapshot"
current=$(cat "$snapshot")
if ! jq -e --argjson manifest "$manifest" '.statusLine == $manifest.installed' <<< "$current" >/dev/null; then
    printf 'Refusing to overwrite a statusLine changed since installation.\nExpected:\n' >&2
    jq .installed <<< "$manifest" >&2
    printf 'Current:\n' >&2; jq .statusLine <<< "$current" >&2
    exit 1
fi
[[ ! -e $destination || ( ! -L $destination && -f $destination ) ]] || fail 'Runtime destination changed type; review it manually.'
if [[ -f $destination ]] && ! cmp -s "$destination" "$backup/runtime-installed.sh"; then
    fail 'Runtime changed since installation; review it manually.'
fi
jq --argjson manifest "$manifest" 'if $manifest.previous_present then .statusLine=$manifest.previous else del(.statusLine) end' <<< "$current" > "$replacement"
atomic_settings "$target" "$snapshot" "$replacement"
if [[ -f $backup/runtime-before.sh ]]; then replace_file "$backup/runtime-before.sh" "$destination"; else rm -f "$destination"; fi
printf 'Restored statusLine; unrelated settings and backup retained.\n'
