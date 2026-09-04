#!/usr/bin/env bash

# ============================================================
# Stocktaking AI - Google Colab Asset Setup
# ============================================================
# Purpose:
#   - Use the existing Google Colab Python environment
#   - Install only required Linux/system tools
#   - Install gdown for Google Drive downloads
#   - Download & extract project assets (weights & data)
#   - Validate assets integrity against assets_manifest.json
#
# NOTE:
#   Python packages are NOT installed here.
#   Install them separately in a Colab notebook cell:
#
#       !python -m pip install -r requirements.txt
#
#   PyTorch should also be installed separately in the notebook.
# ============================================================

set -Eeuo pipefail

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

WEIGHTS_FILE_ID="1c-vusZjXSafgXFLbeaFzU6MRuBQGS4-k"
DATA_FILE_ID="1QHuoY2Wmo49jKrEcf-wvSfA4mU7YL1o5"

WEIGHTS_ZIP="${DOWNLOAD_DIR}/weights.zip"
DATA_ZIP="${DOWNLOAD_DIR}/data.zip"

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
log() {
    echo -e "\n============================================================"
    echo "[setup] $1"
    echo "============================================================"
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

    python "${VERIFY_SCRIPT}" --group "${group}"
}

download_from_drive() {
    local file_id="$1"
    local output_file="$2"
    local output_path="${DOWNLOAD_DIR}/${output_file}"

    log "Downloading ${output_file}"

    echo "Google Drive ID: ${file_id}"
    echo "Output: ${output_path}"

    python -m gdown \
        "https://drive.google.com/uc?id=${file_id}" \
        -O "${output_path}"

    [[ -f "${output_path}" ]] || fail "Download failed: ${output_path}"
    [[ -s "${output_path}" ]] || fail "Downloaded file is empty: ${output_path}"

    echo "Download completed."
    ls -lh "${output_path}"
}

extract_zip() {
    local zip_file="$1"

    [[ -f "${zip_file}" ]] || fail "ZIP file not found: ${zip_file}"

    log "Extracting $(basename "${zip_file}")"

    python - "${zip_file}" "${PROJECT_ROOT}" <<'PY'
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

            # Ignore Windows junk files
            if Path(name).name.lower() in {
                "thumbs.db",
                "desktop.ini",
            }:
                continue

            target = project_root / name

            # Directory
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            # Create parent directory
            target.parent.mkdir(parents=True, exist_ok=True)

            # Extract chunk-by-chunk to avoid high RAM usage
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
    print(
        f"[ERROR] Extraction failed: "
        f"{type(e).__name__}: {e}"
    )
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
# Colab Python
# ------------------------------------------------------------
log "Checking Colab Python"

python --version

echo "Python executable:"
python -c "import sys; print(sys.executable)"

echo
echo "Python packages are NOT installed by this script."
echo "Install them separately from requirements.txt in Colab."

# ------------------------------------------------------------
# Linux System Packages
# ------------------------------------------------------------
log "Installing Linux system packages"

apt-get update -y

apt-get install -y \
    libzbar0 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    unzip

echo "System packages: OK"

# ------------------------------------------------------------
# gdown
# ------------------------------------------------------------
log "Installing gdown"

python -m pip install -q gdown

echo
python -m gdown --version

# ------------------------------------------------------------
# Project Device Configuration
# ------------------------------------------------------------
log "Configuring project device"

if [[ -f "${CONFIG_FILE}" ]]; then

    CONFIG_BACKUP="${CONFIG_FILE}.colab.orig"

    # Detect GPU
    HAS_GPU=0

    if command -v nvidia-smi >/dev/null 2>&1 \
        && nvidia-smi -L >/dev/null 2>&1; then

        HAS_GPU=1

        echo "NVIDIA GPU detected:"
        nvidia-smi -L
    fi

    if [[ "${HAS_GPU}" -eq 0 ]]; then

        [[ -f "${CONFIG_BACKUP}" ]] || \
            cp "${CONFIG_FILE}" "${CONFIG_BACKUP}"

        sed -i \
            's/device: "cuda"/device: "cpu"/g' \
            "${CONFIG_FILE}"

        echo 'Configured device: "cpu"'

    else

        if [[ -f "${CONFIG_BACKUP}" ]]; then
            cp "${CONFIG_BACKUP}" "${CONFIG_FILE}"
            echo 'Restored original GPU configuration.'
        else
            echo 'GPU detected. Keeping current configuration.'
        fi

    fi

    echo
    echo "Current device configuration:"
    grep -n "device:" "${CONFIG_FILE}" || true

else
    warn "Config file not found: ${CONFIG_FILE}"
fi

# ------------------------------------------------------------
# Prepare Download Directory
# ------------------------------------------------------------
mkdir -p "${DOWNLOAD_DIR}"

# ------------------------------------------------------------
# Asset Setup - Weights
# ------------------------------------------------------------
log "Checking weights"

if verify_group "weights"; then

    echo
    echo "Weights already exist and match assets_manifest.json."
    echo "Skipping weights download."

else

    warn "Weights are missing or do not match the manifest."

    download_from_drive \
        "${WEIGHTS_FILE_ID}" \
        "weights.zip"

    extract_zip "${WEIGHTS_ZIP}"

    log "Verifying weights"

    if verify_group "weights"; then
        echo "Weights verification: PASSED"
    else
        fail "Weights verification FAILED."
    fi

fi

# ------------------------------------------------------------
# Asset Setup - Data
# ------------------------------------------------------------
log "Checking data"

if verify_group "data"; then

    echo
    echo "Data already exists and matches assets_manifest.json."
    echo "Skipping data download."

else

    warn "Data is missing or does not match the manifest."

    download_from_drive \
        "${DATA_FILE_ID}" \
        "data.zip"

    extract_zip "${DATA_ZIP}"

    log "Verifying data"

    if verify_group "data"; then
        echo "Data verification: PASSED"
    else
        fail "Data verification FAILED."
    fi

fi

# ------------------------------------------------------------
# Final Asset Verification
# ------------------------------------------------------------
log "Final asset verification"

echo
echo "========== WEIGHTS =========="

if verify_group "weights"; then
    echo "PASS"
else
    fail "Weights verification failed."
fi

echo
echo "============ DATA ============"

if verify_group "data"; then
    echo "PASS"
else
    fail "Data verification failed."
fi

# ------------------------------------------------------------
# Environment Summary
# ------------------------------------------------------------
log "Environment summary"

echo
echo "Project:"
echo "  ${PROJECT_ROOT}"

echo
echo "Python:"
python --version

echo
echo "Python executable:"
python -c "import sys; print(sys.executable)"

echo
echo "Assets:"
echo "  weights: VERIFIED"
echo "  data:    VERIFIED"

echo
echo "============================================================"
echo "       COLAB ASSET SETUP COMPLETED SUCCESSFULLY"
echo "============================================================"

echo
echo "Python dependencies were NOT installed by setup_colab.sh."

echo
echo "Run this in a Colab cell:"
echo
echo "  !python -m pip install -r requirements.txt"

echo
echo "Then verify your Python packages."

echo
echo "============================================================"