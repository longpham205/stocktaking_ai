#!/usr/bin/env bash

# ============================================================
# Stocktaking AI - Google Colab Setup
# ============================================================
# Purpose:
#   - Create a dedicated Python 3.12 virtual environment (.venv-colab)
#   - Install PyTorch (CUDA/CPU) with pinned packaging tools
#   - Download & extract project assets (weights & data)
#   - Validate assets integrity against assets_manifest.json
# ============================================================

set -Eeuo pipefail

# Trap unexpected errors with line number context
trap 'echo -e "\n[ERROR] Setup stopped at line ${LINENO}\n[ERROR] Command: ${BASH_COMMAND}"' ERR

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DOWNLOAD_DIR="${PROJECT_ROOT}/downloads"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"
MANIFEST_FILE="${PROJECT_ROOT}/assets_manifest.json"
VERIFY_SCRIPT="${PROJECT_ROOT}/scripts/verify_manifest.py"
CONFIG_FILE="${PROJECT_ROOT}/configs/config.yaml"

# Dedicated Python environment for the project
VENV_DIR="${PROJECT_ROOT}/.venv-colab"
PYTHON_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"

WEIGHTS_FILE_ID="1c-vusZjXSafgXFLbeaFzU6MRuBQGS4-k"
DATA_FILE_ID="1QHuoY2Wmo49jKrEcf-wvSfA4mU7YL1o5"

PYTORCH_CUDA_INDEX="https://download.pytorch.org/whl/cu128"
PYTORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
log() {
    echo -e "\n============================================================\n[setup] $1\n============================================================"
}

warn() {
    echo -e "\n[WARNING] $1"
}

fail() {
    echo -e "\n[ERROR] $1"
    exit 1
}

verify_group() {
    local group="$1"
    "${PYTHON_BIN}" "${VERIFY_SCRIPT}" --group "${group}"
}

download_from_drive() {
    local file_id="$1"
    local output_file="$2"
    local output_path="${DOWNLOAD_DIR}/${output_file}"

    log "Downloading ${output_file}"
    echo "Google Drive ID: ${file_id}"
    echo "Output: ${output_path}"

    "${PYTHON_BIN}" -m gdown "https://drive.google.com/uc?id=${file_id}" -O "${output_path}"

    [[ -f "${output_path}" ]] || fail "Download failed: ${output_path}"
    [[ -s "${output_path}" ]] || fail "Downloaded file is empty: ${output_path}"

    echo "Download completed."
    ls -lh "${output_path}"
}

extract_zip() {
    local zip_file="$1"

    [[ -f "${zip_file}" ]] || fail "ZIP file not found: ${zip_file}"

    log "Extracting $(basename "${zip_file}")"

    "${PYTHON_BIN}" - "${zip_file}" "${PROJECT_ROOT}" <<'PY'
import sys
import zipfile
from pathlib import Path

zip_file = Path(sys.argv[1])
project_root = Path(sys.argv[2])

print(f"[INFO] ZIP file: {zip_file}")
print(f"[INFO] Destination: {project_root}")

try:
    with zipfile.ZipFile(zip_file, "r") as z:
        print(f"[INFO] Files in archive: {len(z.infolist())}")

        for info in z.infolist():
            name = info.filename

            # Bỏ file rác do Windows tạo
            if Path(name).name.lower() in {"thumbs.db", "desktop.ini"}:
                continue

            target = project_root / name

            # Directory
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            # Tạo thư mục cha
            target.parent.mkdir(parents=True, exist_ok=True)

            # Giải nén theo từng chunk, không chiếm nhiều RAM
            with z.open(info) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)

    print("[INFO] Extraction completed successfully.")

except zipfile.BadZipFile as e:
    print(f"[ERROR] Invalid ZIP file: {e}")
    sys.exit(1)

except Exception as e:
    print(f"[ERROR] Extraction failed: {type(e).__name__}: {e}")
    sys.exit(1)
PY

    echo "Extraction completed."
}

# ------------------------------------------------------------
# Project Verification
# ------------------------------------------------------------
cd "${PROJECT_ROOT}"

log "Project root"
echo "${PROJECT_ROOT}"

log "Checking project files"
REQUIRED_FILES=(
    "${REQUIREMENTS_FILE}"
    "${MANIFEST_FILE}"
    "${VERIFY_SCRIPT}"
)

for file in "${REQUIRED_FILES[@]}"; do
    [[ -f "${file}" ]] || fail "Missing required file: ${file}"
done
echo "Required project files: OK"

# ------------------------------------------------------------
# System Environment & Linux Packages
# ------------------------------------------------------------
log "Checking Colab Python"
python3 --version
echo -e "\nColab system Python is NOT modified.\nProject will use Python 3.12 inside:\n${VENV_DIR}"

log "Installing Linux system packages"
apt-get update -y
apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    libzbar0 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    unzip \
    git
echo "System packages: OK"

log "Checking Python 3.12"
command -v python3.12 >/dev/null 2>&1 || fail "python3.12 was not installed successfully."
python3.12 --version

PY312_MAJOR=$(python3.12 -c "import sys; print(sys.version_info.major)")
PY312_MINOR=$(python3.12 -c "import sys; print(sys.version_info.minor)")

if [[ "${PY312_MAJOR}" -ne 3 || "${PY312_MINOR}" -ne 12 ]]; then
    fail "Expected Python 3.12, found ${PY312_MAJOR}.${PY312_MINOR}"
fi

# ------------------------------------------------------------
# Virtual Environment & Packaging Tools
# ------------------------------------------------------------
log "Creating Python 3.12 virtual environment"
if [[ -d "${VENV_DIR}" ]]; then
    echo -e "Existing Colab virtual environment found:\n${VENV_DIR}"
else
    python3.12 -m venv "${VENV_DIR}"
fi

[[ -x "${PYTHON_BIN}" ]] || fail "Virtual environment Python not found."

echo -e "\nVirtual environment Python:"
"${PYTHON_BIN}" --version

log "Preparing pip / setuptools"
"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install "setuptools==78.1.0" wheel "jedi>=0.16"

echo -e "\nPackaging versions:"
"${PYTHON_BIN}" -m pip --version
"${PYTHON_BIN}" -c "import setuptools; print('setuptools:', setuptools.__version__)"
"${PYTHON_BIN}" -c "import jedi; print('jedi:', jedi.__version__)"

# ------------------------------------------------------------
# GPU Detection & PyTorch Installation
# ------------------------------------------------------------
log "Checking NVIDIA GPU"
HAS_GPU=0

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    HAS_GPU=1
    echo -e "\nNVIDIA GPU detected:"
    nvidia-smi -L
fi

echo -e "\nRuntime mode: $( [[ "${HAS_GPU}" -eq 1 ]] && echo "GPU" || echo "CPU" )"

log "Installing PyTorch"
if [[ "${HAS_GPU}" -eq 1 ]]; then
    echo -e "Installing CUDA-enabled PyTorch...\nIndex: ${PYTORCH_CUDA_INDEX}"
    "${PYTHON_BIN}" -m pip install --index-url "${PYTORCH_CUDA_INDEX}" torch torchvision
else
    echo -e "Installing CPU-only PyTorch...\nIndex: ${PYTORCH_CPU_INDEX}"
    "${PYTHON_BIN}" -m pip install --index-url "${PYTORCH_CPU_INDEX}" torch torchvision
fi

log "Verifying PyTorch"
"${PYTHON_BIN}" - <<'PY'
import torch
import sys

print("Python:", sys.version)
print("PyTorch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
print("Torch CUDA version:", torch.version.cuda)
print("Device:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")
PY

log "Installing project dependencies"
"${PYTHON_BIN}" -m pip install -r "${REQUIREMENTS_FILE}"

log "Re-checking PyTorch after requirements.txt"
"${PYTHON_BIN}" - <<'PY'
import torch
import setuptools
import sys

print("Python:", sys.version)
print("PyTorch:", torch.__version__)
print("setuptools:", setuptools.__version__)
print("CUDA available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))
PY

log "Installing gdown"
"${PYTHON_BIN}" -m pip install gdown
"${PYTHON_BIN}" -m gdown --version

# ------------------------------------------------------------
# Project Device Configuration
# ------------------------------------------------------------
log "Configuring project device"
if [[ -f "${CONFIG_FILE}" ]]; then
    CONFIG_BACKUP="${CONFIG_FILE}.colab.orig"

    if [[ "${HAS_GPU}" -eq 0 ]]; then
        [[ -f "${CONFIG_BACKUP}" ]] || cp "${CONFIG_FILE}" "${CONFIG_BACKUP}"
        sed -i 's/device: "cuda"/device: "cpu"/g' "${CONFIG_FILE}"
        echo 'Configured device: "cpu"'
    else
        if [[ -f "${CONFIG_BACKUP}" ]]; then
            cp "${CONFIG_BACKUP}" "${CONFIG_FILE}"
            echo 'Restored original GPU configuration.'
        else
            echo -e 'GPU detected.\nKeeping current configuration.'
        fi
    fi

    echo -e "\nCurrent device configuration:"
    grep -n "device:" "${CONFIG_FILE}" || true
else
    warn "Config file not found: ${CONFIG_FILE}"
fi

mkdir -p "${DOWNLOAD_DIR}"

# ------------------------------------------------------------
# Asset Setup (Weights & Data)
# ------------------------------------------------------------
log "Checking weights"
if verify_group "weights"; then
    echo -e "Weights already exist and match assets_manifest.json.\nSkipping weights download."
else
    warn "Weights are missing or do not match the manifest."
    download_from_drive "${WEIGHTS_FILE_ID}" "weights.zip"
    extract_zip "${DOWNLOAD_DIR}/weights.zip"

    log "Verifying weights"
    verify_group "weights" && echo "Weights verification: PASSED" || fail "Weights verification FAILED."
fi

log "Checking data"
if verify_group "data"; then
    echo -e "Data already exists and matches assets_manifest.json.\nSkipping data download."
else
    warn "Data is missing or does not match the manifest."
    download_from_drive "${DATA_FILE_ID}" "data.zip"
    extract_zip "${DOWNLOAD_DIR}/data.zip"

    log "Verifying data"
    verify_group "data" && echo "Data verification: PASSED" || fail "Data verification FAILED."
fi

# ------------------------------------------------------------
# Final Asset Verification
# ------------------------------------------------------------
log "Final asset verification"

echo -e "\n========== WEIGHTS =========="
verify_group "weights" && echo "PASS" || fail "Weights verification failed."

echo -e "\n============ DATA ============"
verify_group "data" && echo "PASS" || fail "Data verification failed."

# ------------------------------------------------------------
# Environment Summary
# ------------------------------------------------------------
log "Environment summary"

echo -e "\nProject:\n  ${PROJECT_ROOT}"

echo -e "\nPython:"
"${PYTHON_BIN}" --version

echo -e "\nPython executable:\n${PYTHON_BIN}"

echo -e "\nPyTorch:"
"${PYTHON_BIN}" -c "import torch; print(torch.__version__)"

echo -e "\nCUDA:"
"${PYTHON_BIN}" -c "import torch; print(torch.cuda.is_available())"

if [[ "${HAS_GPU}" -eq 1 ]]; then
    echo -e "\nGPU:"
    nvidia-smi -L
fi

echo -e "\nAssets:\n  weights: VERIFIED\n  data:    VERIFIED"

echo -e "\n============================================================"
echo "       COLAB SETUP COMPLETED SUCCESSFULLY"
echo "============================================================"
echo -e "\nProject Python:\n  ${PYTHON_BIN}"
echo -e "\nRun project:\n\n  ${PYTHON_BIN} run.py --help\n"
echo "============================================================"