#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/brain/cwd"

echo "=== Instalacja Brain OS Terminal CWD Tracker ==="
mkdir -p "$RUNTIME_DIR"
echo "✓ Katalog pamięci RAM: $RUNTIME_DIR"

# 1. Fish Shell
if command -v fish >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/fish/conf.d"
    ln -sf "$SCRIPT_DIR/brain_cwd.fish" "$HOME/.config/fish/conf.d/brain_cwd.fish"
    echo "✓ Fish: dowiązano $HOME/.config/fish/conf.d/brain_cwd.fish"
fi

# 2. Bash
BASH_LINE="[ -f \"$SCRIPT_DIR/brain_cwd.bash\" ] && source \"$SCRIPT_DIR/brain_cwd.bash\""
if [ -f "$HOME/.bashrc" ]; then
    if ! grep -Fq "brain_cwd.bash" "$HOME/.bashrc"; then
        echo "" >> "$HOME/.bashrc"
        echo "# Brain OS CWD Tracker" >> "$HOME/.bashrc"
        echo "$BASH_LINE" >> "$HOME/.bashrc"
        echo "✓ Bash: dodano wpis do $HOME/.bashrc"
    else
        echo "✓ Bash: wpis już istnieje w $HOME/.bashrc"
    fi
fi

# 3. Zsh
ZSH_LINE="[ -f \"$SCRIPT_DIR/brain_cwd.zsh\" ] && source \"$SCRIPT_DIR/brain_cwd.zsh\""
ZSHRC="$HOME/.zshrc"
if [ ! -f "$ZSHRC" ] && [ -f "$HOME/.zshrc.bak-2026-10-03" ]; then
    cp "$HOME/.zshrc.bak-2026-10-03" "$ZSHRC"
    echo "✓ Zsh: odtworzono $ZSHRC z kopii zapasowej"
fi
if [ -f "$ZSHRC" ]; then
    if ! grep -Fq "brain_cwd.zsh" "$ZSHRC"; then
        echo "" >> "$ZSHRC"
        echo "# Brain OS CWD Tracker" >> "$ZSHRC"
        echo "$ZSH_LINE" >> "$ZSHRC"
        echo "✓ Zsh: dodano wpis do $ZSHRC"
    else
        echo "✓ Zsh: wpis już istnieje w $ZSHRC"
    fi
fi

echo "=== Instalacja zakończona pomyślnie ==="
