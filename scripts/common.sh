#!/bin/bash
set -e
umask 077
fail() { printf '%s\n' "$*" >&2; exit 1; }
resolve_target() {
    local target=$1 link depth=0
    while [[ -L $target ]]; do
        (( depth += 1 )); (( depth <= 16 )) || fail 'Too many settings symlinks'
        link=$(readlink "$target")
        if [[ $link == /* ]]; then target=$link; else target=${target%/*}/$link; fi
    done
    printf '%s\n' "$target"
}
# Refuse a settings file this user can't write, rather than replacing it.
require_writable() { [[ ! -e $1 || -w $1 ]] || fail "$1 isn't writable. Make it writable, or update statusLine wherever that file is managed."; }
# Replace a file through a temporary copy, so a running status line never
# reads a half-written script.
replace_file() { local tmp=$2.$$.$RANDOM.tmp; cp -p "$1" "$tmp" && mv -f "$tmp" "$2"; }
mode_of() { if [[ $OSTYPE == darwin* ]]; then stat -f %Lp "$1"; else stat -c %a "$1"; fi; }
atomic_settings() {
    local target=$1 snapshot=$2 replacement=$3
    if [[ -f $target ]]; then
        cmp -s "$target" "$snapshot" || { printf 'Settings changed during update; retry after reviewing them.\n' >&2; return 1; }
        chmod "$(mode_of "$target")" "$replacement" || return 1
    else
        [[ ! -s $snapshot || $(cat "$snapshot") == '{}' ]] || { printf 'Settings disappeared during update.\n' >&2; return 1; }
    fi
    mv -f "$replacement" "$target"
}
