#!/usr/bin/env bash

# ============================================================
# Stocktaking AI - Google Colab Setup
# ============================================================
# Purpose:
#   - Prepare Google Colab environment and install dependencies
#   - Install CUDA/CPU PyTorch conditionally based on GPU presence
#   - Download and extract weights.zip & data.zip from Google Drive
#   - Validate assets integrity via assets_manifest.json
# ============================================================

set -euo pipefail

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
# Logging Helpers
# ------------------------------------------------------------
log() {
    echo -e "\n[setup] $1"
}

warn() {
    echo -e "\n[warning] $1"
}

fail() {
    echo -e "\n[error] $1"
    exit 1
}

# ------------------------------------------------------------
# Asset Helpers
# ------------------------------------------------------------
verify_group() {
    local group="$1"
    python3 "${VERIFY_SCRIPT}" --group "${group}" >/dev/null 2>&1
}

download_from_drive() {
    local file_id="$1"
    local output_file="$2"
    local output_path="${DOWNLOAD_DIR}/${output_file}"

    echo -e "\nDownloading ${output_file}..."
    echo "Google Drive ID: ${file_id}"

    python3 -m gdown "https://drive.google.com/uc?id=${file_id}" -O "${output_path}"

    if [[ ! -f "${output_path}" ]]; then
        fail "Download failed: ${output_path}"
    fi

    echo "Downloaded: ${output_path}"
}

extract_zip() {
    local zip_file="$1"

    if [[ ! -f "${zip_file}" ]]; then
        fail "ZIP file not found: ${zip_file}"
    fi

    log "Extracting $(basename "${zip_file}")..."
    unzip -o "${zip_file}" -d "${PROJECT_ROOT}"
    echo "Extraction completed."
}

# ------------------------------------------------------------
# Initialization & File Verification
# ------------------------------------------------------------
cd "${PROJECT_ROOT}"
log "Project root:"
echo "${PROJECT_ROOT}"

log "Checking project files..."
REQUIRED_FILES=(
    "${REQUIREMENTS_FILE}"
    "${MANIFEST_FILE}"
    "${VERIFY_SCRIPT}"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "${file}" ]]; then
        fail "Missing required file: ${file}"
    fi
done
echo "Required project files: OK"

# ------------------------------------------------------------
# Python & System Dependencies Setup
# ------------------------------------------------------------
log "Checking Python version..."
python3 --version

PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ "${PYTHON_MAJOR}" -ne 3 ]]; then
    fail "Python 3 is required."
fi

if [[ "${PYTHON_MINOR}" -lt 11 || "${PYTHON_MINOR}" -ge 13 ]]; then
    warn "Project targets Python >=3.11,<3.13."
    warn "Detected Python ${PYTHON_MAJOR}.${PYTHON_MINOR}."
fi

log "Installing Linux system packages..."
sudo apt-get update -y
sudo apt-get install -y \
    libzbar0 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    unzip \
    git
echo "System packages: OK"

log "Upgrading pip..."
python3 -m pip install --upgrade pip setuptools wheel

# ------------------------------------------------------------
# GPU Detection & PyTorch Installation
# ------------------------------------------------------------
log "Checking NVIDIA GPU..."
HAS_GPU=0

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    HAS_GPU=1
    echo -e "\nNVIDIA GPU detected:"
    nvidia-smi -L
fi

log "Installing PyTorch..."
if [[ "${HAS_GPU}" -eq 1 ]]; then
    echo "Installing CUDA-enabled PyTorch..."
    python3 -m pip install torch torchvision --index-url "${PYTORCH_CUDA_INDEX}"
else
    echo "No NVIDIA GPU detected."
    echo "Installing CPU-only PyTorch..."
    python3 -m pip install torch torchvision --index-url "${PYTORCH_CPU_INDEX}"
fi

log "Verifying PyTorch..."
python3 - <<'PY'
import torch

print("PyTorch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
print("CUDA version:", torch.version.cuda)

if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))
else:
    print("GPU: CPU")
PY

log "Installing project dependencies..."
python3 -m pip install -r "${REQUIREMENTS_FILE}"

log "Installing gdown..."
python3 -m pip install --upgrade gdown

# ------------------------------------------------------------
# Project Configuration Setup
# ------------------------------------------------------------
if [[ -f "${CONFIG_FILE}" ]]; then
    log "Checking project device configuration..."
    CONFIG_BACKUP="${CONFIG_FILE}.colab.orig"

    if [[ "${HAS_GPU}" -eq 0 ]]; then
        if [[ ! -f "${CONFIG_BACKUP}" ]]; then
            cp "${CONFIG_FILE}" "${CONFIG_BACKUP}"
        fi
        sed -i 's/device: "cuda"/device: "cpu"/g' "${CONFIG_FILE}"
        echo "No GPU detected."
        echo 'Changed device: "cuda" -> device: "cpu"'
    else
        if [[ -f "${CONFIG_BACKUP}" ]]; then
            cp "${CONFIG_BACKUP}" "${CONFIG_FILE}"
            echo "GPU detected."
            echo "Restored original config."
        else
            echo "GPU detected."
            echo "Keeping current configuration."
        fi
    fi
else
    warn "Config file not found: ${CONFIG_FILE}"
fi

mkdir -p "${DOWNLOAD_DIR}"

# ------------------------------------------------------------
# Asset Downloads & Verification
# ------------------------------------------------------------
log "Checking weights..."
if verify_group "weights"; then
    echo "Weights already exist and match assets_manifest.json."
    echo "Skipping weights download."
else
    warn "Weights are missing or do not match the manifest."
    download_from_drive "${WEIGHTS_FILE_ID}" "weights.zip"
    extract_zip "${DOWNLOAD_DIR}/weights.zip"

    log "Verifying weights..."
    if verify_group "weights"; then
        echo "Weights verification: PASSED"
    else
        fail "Weights verification FAILED."
    fi
fi

log "Checking data..."
if verify_group "data"; then
    echo "Data already exists and matches assets_manifest.json."
    echo "Skipping data download."
else
    warn "Data is missing or does not match the manifest."
    download_from_drive "${DATA_FILE_ID}" "data.zip"
    extract_zip "${DOWNLOAD_DIR}/data.zip"

    log "Verifying data..."
    if verify_group "data"; then
        echo "Data verification: PASSED"
    else
        fail "Data verification FAILED."
    fi
fi

# ------------------------------------------------------------
# Final Verification & Environment Summary
# ------------------------------------------------------------
log "Running final asset verification..."

echo -e "\n========== WEIGHTS =========="
if verify_group "weights"; then
    echo "PASS"
else
    echo "FAIL"
    fail "Weights verification failed."
fi

echo -e "\n============ DATA ============"
if verify_group "data"; then
    echo "PASS"
else
    echo "FAIL"
    fail "Data verification failed."
fi

echo "============================================================"
echo "                 COLAB SETUP COMPLETED"
echo "============================================================"
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
echo "============================================================"
echo -e "Next step:\n\n  python3 run.py --help\n\nor run your Colab notebook."
echo "============================================================"