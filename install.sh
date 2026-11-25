#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/np4abdou1/cenima-cli.git"
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/cenima-cli"
VENV_DIR="$INSTALL_ROOT/venv"
SRC_DIR="$INSTALL_ROOT/src"
BIN_DIR="$HOME/.local/bin"

info()  { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
error() { printf "\033[1;31m[error]\033[0m %s\n" "$*" >&2; exit 1; }

# 1. Locate Python >= 3.9
PYTHON_BIN=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PYTHON_BIN="$cand"
      break
    fi
  fi
done
[ -n "$PYTHON_BIN" ] || error "Python 3.9+ is required. Please install Python 3.9 or newer."

info "Using Python: $($PYTHON_BIN --version)"

# 2. Create isolated virtual environment (avoids PEP 668 externally-managed errors)
mkdir -p "$INSTALL_ROOT" "$BIN_DIR"
if [ ! -d "$VENV_DIR" ]; then
  info "Creating virtual environment in $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null

# 3. Install from local checkout if run inside repo, otherwise clone/update repo
if [ -f "./pyproject.toml" ] && grep -q 'name = "cenima-cli"' "./pyproject.toml" 2>/dev/null; then
  info "Installing cenima-cli from local directory: $(pwd)"
  "$VENV_DIR/bin/pip" install --upgrade ".[download]"
else
  command -v git >/dev/null 2>&1 || error "git is required when installing remotely."
  if [ -d "$SRC_DIR/.git" ]; then
    info "Updating existing source in $SRC_DIR"
    git -C "$SRC_DIR" fetch --tags origin
    git -C "$SRC_DIR" checkout main
    git -C "$SRC_DIR" pull --ff-only origin main
  else
    info "Cloning cenima-cli into $SRC_DIR"
    rm -rf "$SRC_DIR"
    git clone --depth 1 "$REPO_URL" "$SRC_DIR"
  fi
  info "Installing cenima-cli + download extras"
  "$VENV_DIR/bin/pip" install --upgrade "$SRC_DIR[download]"
fi

# 4. Link executables into ~/.local/bin
for cmd in cinema-cli cenima-cli cenima; do
  if [ -x "$VENV_DIR/bin/$cmd" ]; then
    ln -sf "$VENV_DIR/bin/$cmd" "$BIN_DIR/$cmd"
  fi
done

# 5. Check native runtime tools (mpv, fzf, ffmpeg)
missing_tools=()
for tool in mpv fzf ffmpeg; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    missing_tools+=("$tool")
  fi
done

if [ ${#missing_tools[@]} -gt 0 ]; then
  warn "Missing recommended native tools: ${missing_tools[*]}"
  if command -v pacman >/dev/null 2>&1; then
    echo "    Install with: sudo pacman -S ${missing_tools[*]}"
  elif command -v apt-get >/dev/null 2>&1; then
    echo "    Install with: sudo apt update && sudo apt install -y ${missing_tools[*]}"
  elif command -v dnf >/dev/null 2>&1; then
    echo "    Install with: sudo dnf install -y ${missing_tools[*]}"
  elif command -v brew >/dev/null 2>&1; then
    echo "    Install with: brew install ${missing_tools[*]}"
  fi
fi

# 6. PATH check
case ":${PATH:-}:" in
  *":$BIN_DIR:"*) ;;
  *)
    warn "$BIN_DIR is not in your PATH."
    echo "    Add this to your shell profile (~/.bashrc or ~/.zshrc):"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    ;;
esac

info "Done! Run 'cinema-cli' (or 'cenima') to start, or 'cinema-cli --doctor' to check your setup."
