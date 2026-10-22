#!/bin/bash
set -e
script_dir=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)
source "$script_dir/common.sh"
config_dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
settings=$config_dir/settings.json
destination=$config_dir/statusline.sh
dry_run=0
while (( $# )); do
    case $1 in
        --dry-run) dry_run=1; shift ;;
        --settings) settings=$2; shift 2 ;;
        --destination) destination=$2; shift 2 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done
[[ $settings == /* && $destination == /* ]] || fail 'Settings and destination must be absolute paths.'
[[ $destination != *$'\n'* ]] || fail 'Destination cannot contain a newline.'
command -v jq >/dev/null || fail 'Install jq first.'
runtime=$script_dir/../statusline.sh
target=$(resolve_target "$settings")
[[ ! -e $target || -f $target ]] || fail 'Settings must be a regular file.'
require_writable "$target"
current='{}'
[[ ! -f $target ]] || current=$(cat "$target")
jq -e 'type == "object"' <<< "$current" >/dev/null || fail 'Settings must contain a JSON object.'
quoted_destination=${destination//\'/\'\\\'\'}
desired=$(jq -nc --arg command "exec /bin/bash '$quoted_destination'" '{type:"command",command:$command,padding:0,refreshInterval:10}')
if jq -e --argjson desired "$desired" '.statusLine == $desired' <<< "$current" >/dev/null && [[ -f $destination ]] && cmp -s "$runtime" "$destination"; then
    printf 'Already installed.\n'; exit 0
fi
if (( dry_run )); then
    printf 'Would install %s\nWould update only statusLine in %s:\n' "$destination" "$settings"
    jq . <<< "$desired"
    exit 0
fi
[[ ! -L $destination ]] || fail 'Runtime destination is a symlink; choose a regular destination.'
mkdir -p "${target%/*}" "${destination%/*}"
backup=${destination%/*}/statusline-backups/$(date +%Y%m%dT%H%M%S)-$$-$RANDOM
mkdir -p "$backup"
printf '%s\n' "$current" > "$backup/settings-before.json"
[[ ! -f $destination ]] || cp -p "$destination" "$backup/runtime-before.sh"
cp "$runtime" "$backup/runtime-installed.sh"
jq -n --arg settings "$settings" --arg destination "$destination" --argjson installed "$desired" --argjson previous "$current" \
    '{version:1,settings:$settings,destination:$destination,installed:$installed,previous_present:($previous|has("statusLine")),previous:$previous.statusLine}' > "$backup/manifest.json"
replacement=$target.$$.$RANDOM.tmp
trap 'rm -f "$replacement"' EXIT
jq --argjson desired "$desired" '.statusLine = $desired' <<< "$current" > "$replacement"
if [[ -f $target ]]; then
    # Compare bytes from disk, including the original trailing newline.
    cp -p "$target" "$backup/settings-snapshot.json"
    jq -e --argjson before "$current" '. == $before' "$backup/settings-snapshot.json" >/dev/null || fail 'Settings changed before installation.'
else
    printf '{}\n' > "$backup/settings-snapshot.json"
fi
runtime_tmp=$destination.$$.$RANDOM.tmp
cp "$runtime" "$runtime_tmp"; chmod 755 "$runtime_tmp"; mv -f "$runtime_tmp" "$destination"
if ! atomic_settings "$target" "$backup/settings-snapshot.json" "$replacement"; then
    if [[ -f $backup/runtime-before.sh ]]; then replace_file "$backup/runtime-before.sh" "$destination"; else rm -f "$destination"; fi
    fail 'Settings update failed; runtime restored.'
fi
printf 'Installed. Backup: %s\n' "$backup"
