#!/bin/bash

# =============================================================================
# setup.command - Stocktaking AI: native macOS double-click setup
# Must be run on macOS. Main logic delegates to scripts/setup.py.
# Re-running is safe; existing venv and assets are reused.
# =============================================================================

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="venv"
PYTHON_BIN=""

log()  { printf '\n[setup] %s\n' "$1"; }
warn() { printf '\n[warn] %s\n' "$1"; }
err()  { printf '\n[error] %s\n' "$1" >&2; }
die()  { err "$1"; exit "${2:-1}"; }

# --- 0. macOS Check ---
OS_NAME="$(uname -s)"
if [[ "$OS_NAME" != "Darwin" ]]; then
    die "This file is intended for macOS only. Detected: $OS_NAME"
fi
log "Detected macOS."

# --- 1. Find Python 3.11 / 3.12 ---
find_python() {
    local candidate version major minor

    for candidate in python3.12 python3.11 python3 python; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi

        version="$(
            "$candidate" \
                -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' \
                2>/dev/null || true
        )"
        major="${version%%.*}"
        minor="${version##*.}"

        if [[ "$major" == "3" && ( "$minor" == "11" || "$minor" == "12" ) ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

PYTHON_BIN="$(find_python)" || die "Python 3.11 or 3.12 not found. Install Python 3.11 or 3.12 first."

PY_VERSION="$(
    "$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")'
)"
log "Using Python $PY_VERSION ($PYTHON_BIN)"

# --- 2. Project Sanity Checks ---
for req_file in requirements.txt run.py assets_manifest.json scripts/setup.py scripts/verify_manifest.py; do
    if [[ ! -f "$req_file" ]]; then
        die "$req_file not found."
    fi
done

# --- 3. macOS System Dependencies ---
if command -v brew >/dev/null 2>&1; then
    log "Homebrew detected."

    if ! command -v unzip >/dev/null 2>&1; then
        warn "unzip not found. Installing unzip with Homebrew..."
        brew install unzip
    fi

    if ! brew list zbar >/dev/null 2>&1; then
        warn "ZBar not detected. Installing zbar for barcode support..."
        brew install zbar
    fi
else
    warn "Homebrew was not found. Continuing without Homebrew."
    warn "If barcode support fails, install ZBar manually."
fi

# --- 4. Python Virtual Environment ---
VENV_PYTHON="$PROJECT_ROOT/$VENV_DIR/bin/python"

if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating virtual environment in $VENV_DIR ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
else
    log "Virtual environment $VENV_DIR already exists, reusing it."
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
    die "Could not find $VENV_PYTHON"
fi
log "Using virtual environment: $VENV_PYTHON"

# --- 5. Upgrade pip ---
log "Upgrading pip ..."
"$VENV_PYTHON" -m pip install --upgrade pip

# --- 6. Run Portable Python Setup ---
log "Running scripts/setup.py ..."
"$VENV_PYTHON" "$PROJECT_ROOT/scripts/setup.py" --platform macos

# --- 7. Final Result ---
log "Setup completed successfully."

echo
echo "============================================================"
echo " Stocktaking AI setup completed successfully."
echo "============================================================"
echo

# Keep Terminal open when launched by Finder
read -r -p "Press Enter to close this window..."