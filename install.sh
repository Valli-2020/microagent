#!/bin/sh
# micro installer. Idempotent. Env: MICRO_HOME, MICRO_DIR, BIN_DIR, MICRO_NAME. Flag: --uninstall
# One-liner: curl -fsSL https://raw.githubusercontent.com/Valli-2020/microagent/main/install.sh | sh
set -eu

# Piped from curl (no checkout next to this script): fetch the program into a standard place, then run its installer.
if [ ! -f "$(dirname "$0")/ma.py" ]; then
  APP=${MICRO_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/microagent}
  command -v git >/dev/null 2>&1 || { echo "git not found" >&2; exit 1; }
  if [ -d "$APP/.git" ]; then git -C "$APP" pull -q --ff-only; else git clone -q https://github.com/Valli-2020/microagent.git "$APP"; fi
  exec sh "$APP/install.sh" "$@"
fi
REPO=$(cd "$(dirname "$0")" && pwd)
MARK="# added by micro"
CONF=${MICRO_HOME:-$HOME/.config/microagent}
BIN_DIR=${BIN_DIR:-$HOME/.local/bin}
NAME=${MICRO_NAME:-micro}
LINK=$BIN_DIR/$NAME

if [ "${1:-}" = "--uninstall" ]; then
  if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$REPO/ma.py" ]; then
    rm "$LINK"; echo "Removed $LINK (config in $CONF kept)"
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
      if [ -f "$rc" ] && grep -q "$MARK" "$rc"; then { grep -v "$MARK" "$rc" || true; } > "$rc.micro.tmp"; cat "$rc.micro.tmp" > "$rc"; rm -f "$rc.micro.tmp"; fi
    done
    rm -f "$HOME/.config/fish/conf.d/micro.fish"
  else
    echo "Nothing to remove: $LINK is not a symlink into this repo"
  fi
  exit 0
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' \
  || { echo "python3 >= 3.9 required" >&2; exit 1; }

# Refuse early, before touching anything.
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  echo "Refusing to replace $LINK: it exists and is not a symlink. Try MICRO_NAME=<other> ./install.sh" >&2
  exit 1
fi

mkdir -p "$CONF" "$CONF/skills"
for f in personality.md facts.md; do
  [ -e "$CONF/$f" ] || cp "$REPO/$f" "$CONF/$f"
done

ask() { # ask <prompt> <default> -> value on stdout
  if [ -t 0 ]; then
    printf '%s [%s]: ' "$1" "$2" >&2
    read -r a || a=
    printf '%s' "${a:-$2}"
  else
    printf '%s' "$2"
  fi
}

if [ ! -e "$CONF/config.json" ]; then
  URL=$(ask "Model endpoint (OpenAI-compatible)" "http://localhost:11434/v1")
  MODEL=$(ask "Model name" "qwen3:8b")
  CRW=$(ask "crw URL (web search/scrape)" "http://localhost:3002")
  # json.dumps via python so odd input cannot break the file
  (umask 077; : > "$CONF/config.json")
  chmod 600 "$CONF/config.json"
  python3 -c '
import json, sys
u, m, c = sys.argv[1:4]
json.dump({"active": "local", "models": {"local": {"url": u, "model": m, "key": ""}},
           "crw": {"url": c, "key": ""},
           "skills": ["~/.config/microagent/skills"],
           "max_turns": 12, "ctx": 8192, "auto": False}, sys.stdout, indent=2)
print()' "$URL" "$MODEL" "$CRW" > "$CONF/config.json"
  echo "Wrote $CONF/config.json (mode 600). Put API keys there or in MICRO_KEY / CRW_KEY."
fi

mkdir -p "$BIN_DIR"
chmod +x "$REPO/ma.py" 2>/dev/null || echo "warning: ma.py not found or not executable" >&2
other=$(command -v "$NAME" 2>/dev/null || true)
if [ -n "$other" ] && [ "$other" != "$LINK" ]; then
  echo "warning: '$NAME' already resolves to $other on PATH (e.g. the micro text editor)." >&2
  echo "         Consider: MICRO_NAME=<other> ./install.sh" >&2
fi
ln -sfn "$REPO/ma.py" "$LINK"
echo "Linked $LINK -> $REPO/ma.py"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) # put BIN_DIR on PATH for future shells (once, marked so --uninstall can remove it)
    case "$(basename "${SHELL:-sh}")" in
      fish) mkdir -p "$HOME/.config/fish/conf.d"; echo "fish_add_path $BIN_DIR" > "$HOME/.config/fish/conf.d/micro.fish"; rc=fish ;;
      zsh) rc=${ZDOTDIR:-$HOME}/.zshrc ;;
      bash) rc=$HOME/.bashrc ;;
      *) rc=$HOME/.profile ;;
    esac
    if [ "$rc" = fish ] || ! grep -qs "$MARK" "$rc"; then
      [ "$rc" = fish ] || printf 'export PATH="%s:$PATH"  %s\n' "$BIN_DIR" "$MARK" >> "$rc"
      echo "Added $BIN_DIR to PATH ($rc). Open a new terminal, or run: exec \$SHELL"
    fi ;;
esac
echo "Done. Run: $NAME"
