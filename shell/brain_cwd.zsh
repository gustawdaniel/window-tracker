# Brain OS - Terminal CWD Tracker (Zsh)
# Source this file from ~/.zshrc

zmodload zsh/datetime 2>/dev/null
if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    export __BRAIN_CWD_DIR="${XDG_RUNTIME_DIR}/brain/cwd"
elif [[ "$OSTYPE" == darwin* ]]; then
    export __BRAIN_CWD_DIR="${TMPDIR:-/tmp}/brain-${UID}/cwd"
else
    export __BRAIN_CWD_DIR="/run/user/${UID}/brain/cwd"
fi
[[ -d "$__BRAIN_CWD_DIR" ]] || mkdir -p "$__BRAIN_CWD_DIR" 2>/dev/null || true

__brain_track_cwd() {
    if [[ ! -o interactive ]] && [[ -z "${FORCE_TRACK:-}" ]]; then
        return 0
    fi
    [[ -d "$__BRAIN_CWD_DIR" ]] || return 0
    echo "$PWD" > "$__BRAIN_CWD_DIR/$$" 2>/dev/null || true
    echo "$PWD" > "$__BRAIN_CWD_DIR/last" 2>/dev/null || true
    local ts="${EPOCHREALTIME:-$(date +%s)}"
    printf "%s\t%s\tzsh\t%s\n" "$ts" "$$" "$PWD" >> "$__BRAIN_CWD_DIR/history.log" 2>/dev/null || true
}

__brain_track_cwd_cleanup() {
    rm -f "$__BRAIN_CWD_DIR/$$" 2>/dev/null
}

if [[ -o interactive ]] || [[ -n "${FORCE_TRACK:-}" ]]; then
    autoload -Uz add-zsh-hook 2>/dev/null
    add-zsh-hook chpwd __brain_track_cwd 2>/dev/null || chpwd_functions+=(__brain_track_cwd)
    trap __brain_track_cwd_cleanup EXIT
    __brain_track_cwd
fi
