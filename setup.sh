#!/usr/bin/env bash
# =============================================================================
# setup.sh - Stocktaking AI: Linux / macOS / WSL setup
# Run from project root: chmod +x setup.sh && ./setup.sh
# Main setup logic delegates to scripts/setup.py.
# =============================================================================

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR=".venv"
PYTHON_BIN="${PYTHON_BIN:-}"

log()  { printf '\n[setup] %s\n' "$1"; }
warn() { printf '\n[warn] %s\n' "$1"; }
err()  { printf '\n[error] %s\n' "$1" >&2; }
die()  { err "$1"; exit "${2:-1}"; }

# --- 0. Find Supported Python & Sanity Checks ---
find_python() {
    if [[ -n "$PYTHON_BIN" ]] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        printf '%s\n' "$PYTHON_BIN"
        return 0
    fi

    local candidate version major minor

    for candidate in python3.12 python3.11 python3 python python3.13 python3.10; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi

        version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
        major="${version%%.*}"
        minor="${version##*.}"

        if [[ "$major" == "3" && ( "$minor" == "11" || "$minor" == "12" ) ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

PYTHON_BIN="$(find_python)" || die "Python 3.11 or 3.12 not found. Install one first."

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
log "Using Python $PY_VERSION ($PYTHON_BIN)"

for req_file in requirements.txt run.py assets_manifest.json scripts/setup.py; do
    if [[ ! -f "$req_file" ]]; then
        die "Required file '$req_file' not found. Run this script from the project root."
    fi
done

# --- 1. System-level Dependencies ---
if command -v apt-get >/dev/null 2>&1; then
    log "Debian/Ubuntu detected."

    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    elif [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        SUDO=""
    else
        SUDO=""
        warn "sudo is unavailable and the current user is not root."
    fi

    if [[ -n "$SUDO" || "${EUID:-$(id -u)}" -eq 0 ]]; then
        log "Installing system packages: libzbar0, python3-tk, unzip ..."
        $SUDO apt-get update -y
        $SUDO apt-get install -y libzbar0 python3-tk unzip
    else
        warn "Skipping apt package installation."
        warn "Install libzbar0, python3-tk, and unzip manually if required."
    fi
else
    case "$(uname -s)" in
        Darwin)
            log "macOS detected."
            if ! command -v unzip >/dev/null 2>&1; then
                die "unzip not found. Install it before continuing."
            fi
            warn "macOS does not use apt-get. Install ZBar separately if pyzbar fails."
            ;;
        *)
            log "Non-Debian Unix detected."
            if ! command -v unzip >/dev/null 2>&1; then
                die "unzip not found. Install it before continuing."
            fi
            ;;
    esac
fi

# --- 2. Create Virtual Environment ---
VENV_PYTHON="$PROJECT_ROOT/$VENV_DIR/bin/python"

if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating virtual environment in $VENV_DIR ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
else
    log "Virtual environment $VENV_DIR already exists, reusing it."
fi

[[ -x "$VENV_PYTHON" ]] || die "Could not find $VENV_PYTHON"

# --- 3. Upgrade Pip ---
log "Upgrading pip ..."
"$VENV_PYTHON" -m pip install --upgrade pip

# --- 4. Delegate to Python Setup Script ---
log "Running scripts/setup.py ..."
"$VENV_PYTHON" "$PROJECT_ROOT/scripts/setup.py" --platform unix
status=$?

if [[ $status -ne 0 ]]; then
    die "Python setup failed with exit code $status." "$status"
fi

log "Setup completed successfully."