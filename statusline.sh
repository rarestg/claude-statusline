#!/bin/bash
# Claude Code status line. Bash 3.2+, jq; no daemon or transcript reads.
export LC_ALL=C
export TZ=UTC
umask 077

cache_root=${CLAUDE_STATUSLINE_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/claude-statusline}
registry_root=${CLAUDE_STATUSLINE_REGISTRY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions}
ttl=${CLAUDE_STATUSLINE_GIT_TTL:-10}
deadline=${CLAUDE_STATUSLINE_GIT_TIMEOUT:-2}
if [[ $ttl =~ ^[0-9]{1,2}$ ]]; then ttl=$((10#$ttl)); else ttl=10; fi
if [[ $deadline =~ ^[0-9]{1,2}$ ]]; then deadline=$((10#$deadline)); else deadline=2; fi
(( ttl >= 1 && ttl <= 60 )) || ttl=10
(( deadline >= 1 && deadline <= 10 )) || deadline=2

# A process's start time in the form Claude Code records as procStart: field 22
# of /proc/<pid>/stat (clock ticks since boot) on Linux, `ps -o lstart=` elsewhere.
proc_start() {
    local line words
    proc_start_result=
    if [[ -r /proc/$1/stat ]]; then
        { IFS= read -r line < "/proc/$1/stat"; } 2>/dev/null || :
        read -r -a words <<< "${line##*) }"
        [[ ${words[19]-} =~ ^[0-9]+$ ]] && proc_start_result=${words[19]}
    else
        read -r -a words <<< "$(ps -p "$1" -o lstart= 2>/dev/null)"
        proc_start_result=${words[*]-}
    fi
}

# Bound stdin. Bash's read avoids a separate cat process.
input=
IFS= read -r -d '' -n 1048577 input || :
(( ${#input} <= 1048576 )) || exit 0
[[ -n $input ]] || exit 0
# Without a writable cache, render anyway: skip the hash cache and Git counts.
cache_ok=0
{ [[ -d $cache_root/identity ]] || mkdir -p "$cache_root/identity" 2>/dev/null; } && [[ -w $cache_root/identity ]] && cache_ok=1

# The installer uses exec, so PPID is normally Claude itself. Wrappers are
# supported by a bounded cold-path walk. Every selected PID is checked live.
candidate=$PPID
actual_start=
registry=
for (( depth=0; depth<6 && candidate>1; depth++ )); do
    if [[ -f $registry_root/$candidate.json ]]; then
        registry=$registry_root/$candidate.json
        proc_start "$candidate"; actual_start=$proc_start_result
        kill -0 "$candidate" 2>/dev/null || actual_start=
        break
    fi
    parent=$(ps -p "$candidate" -o ppid= 2>/dev/null)
    read -r candidate <<< "$parent"
    [[ $candidate =~ ^[0-9]+$ ]] || break
done

reg_json='{}'
if [[ -n $registry && -n $actual_start ]]; then
    reg_json=
    IFS= read -r -d '' -n 65537 reg_json < "$registry" || :
    (( ${#reg_json} <= 65536 )) || reg_json='{}'
fi

id_file=$cache_root/identity/$candidate
cached_version= cached_start= cached_sid= cached_socket= cached_ref= cached_name=
if (( cache_ok )) && [[ -f $id_file ]]; then
    { IFS= read -r cached_version; IFS= read -r cached_start;
      IFS= read -r cached_sid; IFS= read -r cached_socket;
      IFS= read -r cached_ref; IFS= read -r cached_name; } < "$id_file"
fi

read -r -d '' filter <<'JQ' || :
def clean: tostring | gsub("[\u0000-\u001f\u007f-\u009f]"; "");
def num: if type == "number" then . elif type == "string" then (tonumber? // null) else null end;
def text: if type == "string" then clean else "" end;
def obj: if type == "object" then . else {} end;
def norm: text | gsub("[[:space:]]+"; " ") | sub("^ +"; "") | sub(" +$"; "");
def tokens: num | if . != null and . >= 0 and . < 1000000000000000 then floor else null end;
if type != "object" then error("expected object") else . end |
. as $d | ((try ($registry | fromjson) catch {}) | obj) as $r |
($d.workspace | obj) as $workspace | ($d.effort | obj) as $effort |
($d.cost | obj) as $cost | ($d.pr | obj) as $pr |
($d.session_id | text) as $sid |
(($workspace.current_dir // $d.cwd // "") | text) as $cwd |
((($r.pid | tostring) == $pid) and ($sid != "") and ($r.sessionId == $sid)
 and ($start != "") and (($r.procStart | norm) == $start)) as $valid |
($d.context_window | obj) as $ctx |
($ctx.context_window_size | tokens) as $limit |
($ctx.current_usage | obj) as $usage |
([$usage.input_tokens, $usage.cache_read_input_tokens, $usage.cache_creation_input_tokens] | map(tokens) | map(select(. != null))) as $parts |
(if ($parts | length) > 0 then ($parts | add | tokens) else null end) as $used |
(($ctx.used_percentage | num) // (if ($limit // 0) > 0 and $used != null then $used / $limit * 100 else null end)) as $pct |
[
 $sid, $cwd,
 (($d.model | if type == "object" then (.display_name // .id // "Claude") elif type == "string" then . else "Claude" end) | clean | sub(" \\(.*context\\)$"; "")),
 ($effort.level | text), (($cost.total_cost_usd | num) // ""),
 (if $pct == null then "" else ($pct | floor | [0, .] | max | [100, .] | min) end),
 ($pr.url | text),
 (if $valid then ($r.name | text) else "" end),
 (if $valid and (($r.messagingSocketPath | type) == "string") and ($r.messagingSocketPath == ($r.messagingSocketPath | clean)) then $r.messagingSocketPath else "" end),
 (($pr.number | num) | if . != null and . > 0 and . < 1000000000000 and . == floor then . else "" end),
 ($used // ""), ($limit // ""),
 (if ($cwd | startswith("/")) then "file://" + ($cwd | split("/") | map(@uri) | join("/")) else "" end)
] | .[] | tostring + "\u0000"
JQ

fields=()
while IFS= read -r -d '' field; do fields[${#fields[@]}]=$field; done < <(
    jq -j --arg registry "$reg_json" --arg pid "$candidate" --arg start "$actual_start" "$filter" <<< "$input" 2>/dev/null
)
(( ${#fields[@]} == 13 )) || exit 0
sid=${fields[0]} cwd=${fields[1]} model=${fields[2]} effort=${fields[3]}
cost=${fields[4]} percent=${fields[5]} pr_url=${fields[6]} name=${fields[7]} socket=${fields[8]}
pr_number=${fields[9]}
used_tokens=${fields[10]} context_limit=${fields[11]}
cwd_url=${fields[12]}

ref=
if [[ -n $name && -n $socket ]]; then
    if [[ $cached_version == 1 && $cached_start == "$actual_start" && $cached_sid == "$sid" && $cached_socket == "$socket" && $cached_ref =~ ^[0-9a-f]{6}$ ]]; then
        ref=$cached_ref
    else
        digest=
        command -v sha256sum >/dev/null 2>&1 && digest=$(printf 'session:%s' "$socket" | sha256sum 2>/dev/null)
        if [[ ! ${digest:0:6} =~ ^[0-9a-f]{6}$ ]] && command -v shasum >/dev/null 2>&1; then
            digest=$(printf 'session:%s' "$socket" | shasum -a 256 2>/dev/null)
        fi
        ref=${digest:0:6}
        [[ $ref =~ ^[0-9a-f]{6}$ ]] || ref=
    fi
    if (( cache_ok )) && [[ -n $ref && ( $cached_name != "$name" || $cached_ref != "$ref" || $cached_start != "$actual_start" || $cached_sid != "$sid" || $cached_socket != "$socket" ) ]]; then
        tmp=$id_file.$$.$RANDOM.tmp
        { printf '1\n%s\n%s\n%s\n%s\n%s\n' "$actual_start" "$sid" "$socket" "$ref" "$name" > "$tmp"; } 2>/dev/null && mv -f "$tmp" "$id_file" 2>/dev/null
    fi
fi

now=${EPOCHSECONDS:-}
[[ $now =~ ^[0-9]+$ ]] || now=$(date +%s)

# Git discovery and cache keys use builtins, not rev-parse or a hash process.
root=
gitdir=
if [[ -d $cwd ]]; then
    search=$(cd -P -- "$cwd" 2>/dev/null && printf '%s' "$PWD")
    for (( depth=0; depth<64; depth++ )); do
        if [[ -d $search/.git ]]; then root=$search; gitdir=$search/.git; break; fi
        if [[ -f $search/.git ]]; then
            IFS= read -r marker < "$search/.git"
            if [[ $marker == 'gitdir: '* ]]; then
                gitdir=${marker#gitdir: }
                [[ $gitdir == /* ]] || gitdir=$search/$gitdir
                gitdir=$(cd -P -- "$gitdir" 2>/dev/null && printf '%s' "$PWD")
                [[ -n $gitdir ]] && root=$search
            fi
            break
        fi
        [[ $search == / || -z $search ]] && break
        search=${search%/*}; [[ -n $search ]] || search=/
    done
fi

lock= lock_token= work= git_pid= timer_pid=
cleanup() {
    [[ -n $timer_pid ]] && kill "$timer_pid" 2>/dev/null
    [[ -n $git_pid ]] && kill -KILL -- "-$git_pid" 2>/dev/null
    [[ -n $git_pid ]] && wait "$git_pid" 2>/dev/null
    [[ -n $timer_pid ]] && wait "$timer_pid" 2>/dev/null
    git_pid= timer_pid=
    if [[ -n $lock_token && -f $lock/owner ]]; then
        current_token=
        { IFS= read -r current_token < "$lock/owner"; } 2>/dev/null
        if [[ $current_token == "$lock_token" ]]; then
            rm -f "$lock/owner"
            rmdir "$lock" 2>/dev/null
        fi
    fi
    lock_token=
    [[ -n $work ]] && rm -rf "$work"
    work=
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM

bounded_git() {
    local result
    rm -f "$work/timer"
    mkfifo "$work/timer" 2>/dev/null || return 1
    exec 9<> "$work/timer"
    # Git and its watchdog each get their own process group. Cancellation can
    # reap Git's descendants, and a kill aimed at this script's group can't take
    # the watchdog with it and leave Git running past the deadline. Signals wait
    # until both PIDs are recorded, so cleanup always knows what to reap.
    pending=0
    trap 'pending=1' HUP INT TERM
    set -m
    git --no-optional-locks -c core.fsmonitor=false -c core.preloadIndex=false -c index.threads=1 -C "$root" diff --no-ext-diff --no-textconv --shortstat "$@" > "$work/output" 2>/dev/null &
    git_pid=$!
    # The watchdog doesn't hold Claude's output pipe, so a killed status line
    # closes it right away.
    ( IFS= read -r -t "$deadline" signal <&9 || kill -KILL -- "-$git_pid" 2>/dev/null ) >/dev/null 2>&1 &
    timer_pid=$!
    set +m
    trap 'exit 0' HUP INT TERM
    (( pending )) && exit 0
    wait "$git_pid" 2>/dev/null; result=$?
    printf 'done\n' >&9
    wait "$timer_pid" 2>/dev/null
    exec 9>&-
    git_pid= timer_pid=
    return "$result"
}

branch= additions= deletions=
if [[ -n $root && -f $gitdir/HEAD ]]; then
    IFS= read -r head < "$gitdir/HEAD"
    if [[ $head == 'ref: refs/heads/'* ]]; then branch=${head#ref: refs/heads/}; else branch=@${head:0:8}; fi
    # Reject control characters. Git allows UTF-8 encoded C1 controls in ref
    # names, and [[:cntrl:]] misses them under LC_ALL=C.
    c1=$'\xc2'[$'\x80'-$'\x9f']
    [[ $branch =~ [[:cntrl:]] || $branch == *$c1* ]] && branch=
    common=$gitdir
    if [[ -f $gitdir/commondir ]]; then
        IFS= read -r common < "$gitdir/commondir"
        [[ $common == /* ]] || common=$gitdir/$common
    fi
    ref_path=
    [[ $head == 'ref: '* ]] && ref_path=$common/${head#ref: }
    git_cache=$cache_root/worktrees$gitdir
    counts=$git_cache/counts
    lock=$git_cache/refresh.lock

    read_counts() {
        count_version= count_head= count_time= count_add= count_del=
        if [[ -f $counts ]]; then
            { { IFS= read -r count_version; IFS= read -r count_head; IFS= read -r count_time;
                IFS= read -r count_add; IFS= read -r count_del; } < "$counts"; } 2>/dev/null
        fi
        counts_valid=0
        if [[ $count_version == 1 && $count_head == "$head" && $count_time =~ ^[0-9]{1,12}$ && $count_add =~ ^[0-9]{1,12}$ && $count_del =~ ^[0-9]{1,12}$ && ! $gitdir/HEAD -nt $counts && ! $gitdir/index -nt $counts && ! $common/packed-refs -nt $counts && ( -z $ref_path || ! $ref_path -nt $counts ) ]]; then
            count_time=$((10#$count_time)); count_add=$((10#$count_add)); count_del=$((10#$count_del))
            counts_valid=1
        fi
    }
    read_counts
    fresh=0
    (( counts_valid && now >= count_time && now - count_time < ttl )) && fresh=1
    if (( ! fresh )); then
        retry=0
        { IFS= read -r retry < "$git_cache/retry"; } 2>/dev/null
        [[ $retry =~ ^[0-9]{1,12}$ ]] || retry=0
        if (( cache_ok && ( retry <= now || retry - now > 5 ) )); then
            [[ -d $git_cache ]] || mkdir -p "$git_cache" 2>/dev/null
            # A dead owner cannot retain the lock forever. Missing owner files
            # receive a grace period so acquisition/publication is not disturbed.
            # Two processes can both reclaim a stale lock and end up refreshing
            # at once. That costs an extra diff and nothing else: each owner
            # keeps its Git output, timer, and start marker in its own work
            # directory, and only the owner whose token is in the lock publishes.
            if [[ -d $lock ]]; then
                owner= owner_pid= owner_start= owner_created=
                if [[ -f $lock/owner ]]; then
                    { { IFS= read -r owner; IFS= read -r owner_pid; IFS= read -r owner_start; IFS= read -r owner_created; } < "$lock/owner"; } 2>/dev/null
                    dead_owner=0
                    if [[ $owner_pid =~ ^[0-9]+$ ]]; then
                        if ! kill -0 "$owner_pid" 2>/dev/null; then
                            dead_owner=1
                        elif [[ ! $owner_created =~ ^[0-9]{1,12}$ ]] || (( now - owner_created > deadline * 2 + 5 || owner_created > now )); then
                            proc_start "$owner_pid"
                            [[ $proc_start_result == "$owner_start" ]] || dead_owner=1
                        fi
                    else
                        if [[ $OSTYPE == darwin* ]]; then lock_mtime=$(stat -f %m "$lock/owner" 2>/dev/null); else lock_mtime=$(stat -c %Y "$lock/owner" 2>/dev/null); fi
                        [[ $lock_mtime =~ ^[0-9]+$ ]] && (( now - lock_mtime > deadline * 2 + 5 )) && dead_owner=1
                    fi
                    if (( dead_owner )); then
                        stale=$lock.stale.$$.$RANDOM
                        current_owner=
                        { IFS= read -r current_owner < "$lock/owner"; } 2>/dev/null
                        if [[ $current_owner == "$owner" ]] && mv "$lock" "$stale" 2>/dev/null; then rm -rf "$stale"; fi
                    fi
                else
                    if [[ $OSTYPE == darwin* ]]; then lock_mtime=$(stat -f %m "$lock" 2>/dev/null); else lock_mtime=$(stat -c %Y "$lock" 2>/dev/null); fi
                    if [[ $lock_mtime =~ ^[0-9]+$ ]] && (( now - lock_mtime > deadline * 2 + 5 )); then
                        stale=$lock.stale.$$.$RANDOM
                        if mv "$lock" "$stale" 2>/dev/null; then rm -rf "$stale"; fi
                    fi
                fi
            fi
            if mkdir "$lock" 2>/dev/null; then
                lock_token=$$.$now.$RANDOM
                proc_start "$$"
                { printf '%s\n%s\n%s\n%s\n' "$lock_token" "$$" "$proc_start_result" "$now" > "$lock/owner"; } 2>/dev/null
                # Remove work directories left by owners that were killed.
                for stale_work in "$git_cache"/work.*; do
                    [[ -d $stale_work ]] || continue
                    stale_pid=${stale_work##*/work.}; stale_pid=${stale_pid%%.*}
                    [[ $stale_pid =~ ^[0-9]+$ ]] && ! kill -0 "$stale_pid" 2>/dev/null && rm -rf "$stale_work"
                done
                work=$git_cache/work.$$.$RANDOM
                mkdir "$work" 2>/dev/null || work=
                read_counts
                if [[ -n $work ]] && ! (( counts_valid && now >= count_time && now - count_time < ttl )); then
                    : > "$work/start"
                    added=0 removed=0 ok=1
                    for diff_kind in unstaged staged; do
                        if [[ $diff_kind == staged ]]; then bounded_git --cached; else bounded_git; fi
                        if (( $? != 0 )); then ok=0; break; fi
                        stat_line=
                        { IFS= read -r stat_line < "$work/output"; } 2>/dev/null || :
                        [[ $stat_line =~ ([0-9]+)\ insertion ]] && added=$(( added + BASH_REMATCH[1] ))
                        [[ $stat_line =~ ([0-9]+)\ deletion ]] && removed=$(( removed + BASH_REMATCH[1] ))
                    done
                    IFS= read -r head_after < "$gitdir/HEAD"
                    current_token=
                    { IFS= read -r current_token < "$lock/owner"; } 2>/dev/null
                    if (( ok )) && [[ $current_token == "$lock_token" && $head == "$head_after" && ! $gitdir/HEAD -nt $work/start && ! $gitdir/index -nt $work/start && ! $common/packed-refs -nt $work/start && ( -z $ref_path || ! $ref_path -nt $work/start ) ]]; then
                        tmp=$counts.$$.$RANDOM.tmp
                        { printf '1\n%s\n%s\n%s\n%s\n' "$head" "$now" "$added" "$removed" > "$tmp"; } 2>/dev/null && mv -f "$tmp" "$counts" 2>/dev/null
                        rm -f "$git_cache/retry"
                    else
                        tmp=$git_cache/retry.$$.$RANDOM.tmp
                        { printf '%s\n' "$((now + 5))" > "$tmp"; } 2>/dev/null && mv -f "$tmp" "$git_cache/retry" 2>/dev/null
                    fi
                fi
                cleanup
            fi
            read_counts
        fi
    fi
    if (( counts_valid )); then additions=$count_add; deletions=$count_del; fi
fi

# Rendering stays in Bash; jq has already sanitized session text and numbers.
if [[ ${NO_COLOR+x} || ${CLAUDE_STATUSLINE_COLOR:-1} == 0 ]]; then
    accent= dim= green= red= reset= model_color= effort_color= branch_color= context_color= id_color=
else
    accent=$'\033[38;5;166m' dim=$'\033[38;5;244m' green=$'\033[38;5;28m' red=$'\033[38;5;160m' reset=$'\033[0m'
    model_color=$'\033[38;5;99m' effort_color=$'\033[38;5;30m' branch_color=$'\033[38;5;169m' context_color=$'\033[38;5;32m'
    id_color=$'\033[38;5;67m'
fi
width=${COLUMNS:-120}
[[ $width =~ ^[0-9]{1,4}$ ]] || width=120
(( width < 20 )) && width=20
if [[ -n $name && -n $ref ]]; then identity="$name [$ref]"; else identity="session ${sid:-unknown}"; fi
first="$accent$identity$reset"
if (( ${#identity} + ${#model} + ${#effort} + 8 < width )); then
    first="$first$dim · $reset$model_color$model$reset${effort:+$dim · $reset$effort_color$effort$reset}"
fi

# PRs are separate from branches. Older status payloads may omit the number
# but still provide a GitHub/GitLab URL, which is enough for a local badge.
if [[ ! $pr_number =~ ^[0-9]{1,12}$ ]]; then
    pr_number=
    if [[ $pr_url =~ ^https?://[^[:space:]]+$ && $pr_url =~ /(pull|merge_requests)/([0-9]+)([/#?]|$) ]]; then pr_number=${BASH_REMATCH[2]}; fi
fi
pr_text=${pr_number:+PR #$pr_number}
cost_text=
[[ -n $cost ]] && printf -v cost_text '$%.2f' "$cost"

format_tokens() {
    if (( $1 >= 1000000 )); then printf -v formatted '%.1fM' "$(( $1 ))e-6"
    elif (( $1 >= 1000 )); then printf -v formatted '%.0fk' "$(( $1 ))e-3"
    else formatted=$1; fi
}
context_label=${percent:+$percent%}
if [[ -n $percent && -n $used_tokens && -n $context_limit ]] && (( context_limit > 0 )); then
    format_tokens "$used_tokens"; used_text=$formatted
    format_tokens "$context_limit"; context_label="$used_text/$formatted ($percent%)"
fi

# Track bar width in terminal cells: UTF-8 block characters are three bytes,
# so Bash's byte count under LC_ALL=C is not suitable for this segment.
make_context() {
    context_text= context_len=0
    [[ -n $percent ]] || return
    context_text="$context_color$context_label$reset"
    context_len=${#context_label}
    if (( $1 > 0 )); then
        local filled=$(( (percent * $1 + 50) / 100 )) full= empty= i
        for (( i=0; i<$1; i++ )); do
            if (( i < filled )); then full+=█; else empty+=░; fi
        done
        context_text="$context_color[$full$dim$empty$context_color] $context_label$reset"
        context_len=$(( $1 + ${#context_label} + 3 ))
    fi
}
measure_base() {
    base_len=$(( ${#cost_text} + ${#pr_text} + context_len ))
    base_count=0
    [[ -n $cost_text ]] && (( base_count += 1 ))
    [[ -n $pr_text ]] && (( base_count += 1 ))
    [[ -n $context_text ]] && (( base_count += 1 ))
    (( base_count > 1 )) && (( base_len += 3 * (base_count - 1) ))
}
make_context 10; measure_base
if (( base_len > width )); then make_context 4; measure_base; fi
if (( base_len > width )); then pr_text=; measure_base; fi
if (( base_len > width )); then make_context 0; measure_base; fi
if (( base_len > width )); then context_label=${percent:+$percent%}; make_context 0; measure_base; fi

branch_text= delta_text=
branch_budget=$(( width - base_len ))
(( base_count > 0 )) && (( branch_budget -= 3 ))
if [[ -n $branch ]] && (( branch_budget >= 5 )); then
    branch_limit=$(( branch_budget - 2 )); (( branch_limit > 60 )) && branch_limit=60
    branch_len=${#branch}
    if (( branch_len > branch_limit )); then
        # Never split a UTF-8 code point when shortening a branch.
        if [[ $branch =~ ^[[:print:]]*$ ]]; then branch=${branch:0:branch_limit-1}…; branch_len=$branch_limit; else branch=…; branch_len=1; fi
    fi
    branch_text="$branch_color⎇ $branch$reset"
    (( base_len += branch_len + 2 ))
    (( base_count > 0 )) && (( base_len += 3 ))
    (( base_count += 1 ))
fi
if [[ -n $additions ]] && (( additions || deletions )); then
    delta_len=$(( ${#additions} + ${#deletions} + 3 ))
    if (( base_len + delta_len + 3 <= width )); then delta_text="$green+$additions$reset $red−$deletions$reset"; fi
fi
if [[ -n $pr_text ]]; then
    if [[ ${CLAUDE_STATUSLINE_LINKS:-1} == 1 && $pr_url =~ ^https?://[^[:space:]]+$ && -n $accent ]]; then
        pr_text=$'\033]8;;'"$pr_url"$'\033\\'"$pr_text"$'\033]8;;\033\\'
    fi
    pr_text="$context_color$pr_text$reset"
fi
second=
for segment in "$branch_text" "$pr_text" "$delta_text" "${cost_text:+$green$cost_text$reset}" "$context_text"; do
    [[ -n $segment ]] || continue
    second="${second:+$second$dim · $reset}$segment"
done
printf '%s\n' "$first"
[[ -n $second ]] && printf '%s\n' "$second"
if [[ -n $cwd ]]; then
    directory_text=$cwd
    if [[ $cwd == "$HOME" ]]; then directory_text='~'
    elif [[ $cwd == "$HOME/"* ]]; then directory_text="~/${cwd#"$HOME/"}"; fi
    # The session ID follows the directory when it fits. Line 1 already shows
    # it when the address can't be verified. Byte counts overstate non-ASCII
    # paths, so those drop the ID a little early.
    session_text=
    if [[ -n $name && -n $ref && -n $sid ]] && (( ${#directory_text} + 3 + ${#sid} <= width )); then
        session_text="$dim · $reset$id_color$sid$reset"
    fi
    if [[ ${CLAUDE_STATUSLINE_LINKS:-1} == 1 && -n $cwd_url && -n $accent ]]; then
        directory_text=$'\033]8;;'"$cwd_url"$'\033\\'"$directory_text"$'\033]8;;\033\\'
    fi
    printf '%s%s%s%s\n' "$dim" "$directory_text" "$reset" "$session_text"
fi
exit 0
