# Brain OS - Terminal CWD Tracker (Bash)
# Source this file from ~/.bashrc

if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    export __BRAIN_CWD_DIR="${XDG_RUNTIME_DIR}/brain/cwd"
elif [[ "$OSTYPE" == darwin* ]]; then
    export __BRAIN_CWD_DIR="${TMPDIR:-/tmp}/brain-${UID}/cwd"
else
    export __BRAIN_CWD_DIR="/run/user/${UID}/brain/cwd"
fi
[[ -d "$__BRAIN_CWD_DIR" ]] || mkdir -p "$__BRAIN_CWD_DIR" 2>/dev/null || true

__brain_track_cwd() {
    # Rejestrujemy w sesjach interaktywnych (lub przy wymuszeniu testowym)
    if [[ $- != *i* ]] && [[ -z "${FORCE_TRACK:-}" ]]; then
        return 0
    fi
    [[ -d "$__BRAIN_CWD_DIR" ]] || return 0
    if [[ "$PWD" != "${__BRAIN_LAST_PWD:-}" ]]; then
        echo "$PWD" > "$__BRAIN_CWD_DIR/$$" 2>/dev/null || true
        echo "$PWD" > "$__BRAIN_CWD_DIR/last" 2>/dev/null || true
        local ts="${EPOCHREALTIME:-$(date +%s)}"
        printf "%s\t%s\tbash\t%s\n" "$ts" "$$" "$PWD" >> "$__BRAIN_CWD_DIR/history.log" 2>/dev/null || true
        export __BRAIN_LAST_PWD="$PWD"
    fi
}

__brain_track_cwd_cleanup() {
    rm -f "$__BRAIN_CWD_DIR/$$" 2>/dev/null
}

if [[ $- == *i* ]] || [[ -n "${FORCE_TRACK:-}" ]]; then
    trap __brain_track_cwd_cleanup EXIT
    PROMPT_COMMAND="__brain_track_cwd${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
    __brain_track_cwd
fi
