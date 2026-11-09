# Brain OS - Terminal CWD Tracker (Fish Shell)
# Auto-loaded by Fish from ~/.config/fish/conf.d/brain_cwd.fish

if test -n "$XDG_RUNTIME_DIR"
    set -g __BRAIN_CWD_DIR "$XDG_RUNTIME_DIR/brain/cwd"
else if test (uname) = "Darwin"
    set -g __BRAIN_CWD_DIR (test -n "$TMPDIR"; and echo "$TMPDIR"; or echo "/tmp")"/brain-"(id -u)"/cwd"
else
    set -g __BRAIN_CWD_DIR "/run/user/"(id -u)"/brain/cwd"
end
test -d "$__BRAIN_CWD_DIR"; or mkdir -p "$__BRAIN_CWD_DIR" 2>/dev/null

function __brain_track_cwd --on-variable PWD --description "Track CWD changes for Brain OS"
    if not status is-interactive; and test -z "$FORCE_TRACK"
        return 0
    end
    test -d "$__BRAIN_CWD_DIR"; or return 0
    echo "$PWD" > "$__BRAIN_CWD_DIR/$fish_pid" 2>/dev/null
    echo "$PWD" > "$__BRAIN_CWD_DIR/last" 2>/dev/null
    set -l ts (command date +%s%3N 2>/dev/null; or command date +%s)
    echo -e "$ts\t$fish_pid\tfish\t$PWD" >> "$__BRAIN_CWD_DIR/history.log" 2>/dev/null
end

function __brain_track_cwd_cleanup --on-event fish_exit --description "Clean up CWD session file on exit"
    rm -f "$__BRAIN_CWD_DIR/$fish_pid" 2>/dev/null
end

if status is-interactive; or test -n "$FORCE_TRACK"
    __brain_track_cwd
end
