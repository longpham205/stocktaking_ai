#!/usr/bin/env bash

# ============================================================
# Stocktaking AI - Google Colab Setup
# ============================================================
# Purpose:
#   - Prepare Google Colab environment and install dependencies
#   - Conditionally install PyTorch (CUDA/CPU) with pinned packaging tools
#   - Download & extract assets (weights & data) from Google Drive
#   - Verify asset integrity against manifest
# ============================================================

set -Eeuo pipefail

# Trap unexpected errors with line number context
trap 'echo -e "\n[error] Setup stopped at line ${LINENO}\n[error] Command: ${BASH_COMMAND}"' ERR

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DOWNLOAD_DIR="${PROJECT_ROOT}/downloads"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"
MANIFEST_FILE="${PROJECT_ROOT}/assets_manifest.json"
VERIFY_SCRIPT="${PROJECT_ROOT}/scripts/verify_manifest.py"
CONFIG_FILE="${PROJECT_ROOT}/configs/config.yaml"

WEIGHTS_FILE_ID="1c-vusZjXSafgXFLbeaFzU6MRuBQGS4-k"
DATA_FILE_ID="1QHuoY2Wmo49jKrEcf-wvSfA4mU7YL1o5"

PYTORCH_CUDA_INDEX="https://download.pytorch.org/whl/cu128"
PYTORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"

# ------------------------------------------------------------
# Logging & Helper Functions
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
    python3 "${VERIFY_SCRIPT}" --group "${group}"
}

download_from_drive() {
    local file_id="$1"
    local output_file="$2"
    local output_path="${DOWNLOAD_DIR}/${output_file}"

    log "Downloading ${output_file}"
    echo "Google Drive ID: ${file_id}"
    echo "Output: ${output_path}"

    python3 -m gdown "https://drive.google.com/uc?id=${file_id}" -O "${output_path}"

    [[ -f "${output_path}" ]] || fail "Download failed: ${output_path}"
    [[ -s "${output_path}" ]] || fail "Downloaded file is empty: ${output_path}"

    echo "Download completed."
    ls -lh "${output_path}"
}

extract_zip() {
    local zip_file="$1"

    [[ -f "${zip_file}" ]] || fail "ZIP file not found: ${zip_file}"

    log "Extracting $(basename "${zip_file}")"
    unzip -oq "${zip_file}" -d "${PROJECT_ROOT}"
    echo "Extraction completed."
}

# ------------------------------------------------------------
# Initialization & Project Verification
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
# Environment & System Dependencies
# ------------------------------------------------------------
log "Checking Python"
python3 --version

PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

[[ "${PYTHON_MAJOR}" -eq 3 ]] || fail "Python 3 is required."

if [[ "${PYTHON_MINOR}" -lt 11 || "${PYTHON_MINOR}" -ge 13 ]]; then
    warn "Project targets Python >=3.11,<3.13."
    warn "Detected Python ${PYTHON_MAJOR}.${PYTHON_MINOR}."
fi

log "Installing Linux system packages"
apt-get update -y
apt-get install -y \
    libzbar0 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    unzip \
    git
echo "System packages: OK"

log "Preparing pip / setuptools"
python3 -m pip install --upgrade pip
python3 -m pip install "setuptools==78.1.0" wheel "jedi>=0.16"

echo -e "\nPackaging versions:"
python3 -m pip --version
python3 -c "import setuptools; print('setuptools:', setuptools.__version__)"
python3 -c "import jedi; print('jedi:', jedi.__version__)"

# ------------------------------------------------------------
# GPU Detection & PyTorch Setup
# ------------------------------------------------------------
log "Checking NVIDIA GPU"
HAS_GPU=0

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    HAS_GPU=1
    echo -e "\nNVIDIA GPU detected:"
    nvidia-smi -L
fi

echo -e "\nRuntime mode: $((HAS_GPU == 1)) ? 'GPU' : 'CPU'"

log "Installing PyTorch"
if [[ "${HAS_GPU}" -eq 1 ]]; then
    echo -e "Installing CUDA-enabled PyTorch...\nIndex: ${PYTORCH_CUDA_INDEX}"
    python3 -m pip install --index-url "${PYTORCH_CUDA_INDEX}" torch torchvision
else
    echo -e "Installing CPU-only PyTorch...\nIndex: ${PYTORCH_CPU_INDEX}"
    python3 -m pip install --index-url "${PYTORCH_CPU_INDEX}" torch torchvision
fi

log "Verifying PyTorch"
python3 - <<'PY'
import torch

print("PyTorch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
print("Torch CUDA version:", torch.version.cuda)
print("Device:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")
PY

log "Installing project dependencies"
python3 -m pip install -r "${REQUIREMENTS_FILE}"

log "Re-checking PyTorch after requirements.txt"
python3 - <<'PY'
import torch
import setuptools

print("PyTorch:", torch.__version__)
print("setuptools:", setuptools.__version__)
print("CUDA available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))
PY

log "Installing gdown"
python3 -m pip install "gdown"
python3 -m gdown --version

# ------------------------------------------------------------
# Project Configuration
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
            echo 'Keeping current GPU configuration.'
        fi
    fi

    echo -e "\nCurrent device configuration:"
    grep -n "device:" "${CONFIG_FILE}" || true
else
    warn "Config file not found: ${CONFIG_FILE}"
fi

mkdir -p "${DOWNLOAD_DIR}"

# ------------------------------------------------------------
# Download Assets (Weights & Data)
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
# Final Verification & Environment Summary
# ------------------------------------------------------------
log "Final asset verification"

echo -e "\n========== WEIGHTS =========="
verify_group "weights" && echo "PASS" || fail "Weights verification failed."

echo -e "\n============ DATA ============"
verify_group "data" && echo "PASS" || fail "Data verification failed."

log "Environment summary"
echo -e "\nProject:\n  ${PROJECT_ROOT}"
echo -e "\nPython:"
python3 --version

echo -e "\nPyTorch:"
python3 -c "import torch; print(torch.__version__)"

echo -e "\nCUDA:"
python3 -c "import torch; print(torch.cuda.is_available())"

if [[ "${HAS_GPU}" -eq 1 ]]; then
    echo -e "\nGPU:"
    nvidia-smi -L
fi

echo -e "\nAssets:\n  weights: VERIFIED\n  data:    VERIFIED"
echo -e "\n============================================================"
echo "              COLAB SETUP COMPLETED SUCCESSFULLY"
echo -e "============================================================"
echo -e "\nNext step:\n\n  python3 run.py --help\n\nor run your Colab notebook.\n"
echo "============================================================"