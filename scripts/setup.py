"""Stocktaking AI - Windows E2E setup helper.

Called by setup_e2e.bat from the project root.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

VENV_DIR = PROJECT_ROOT / "venv"
DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
CONFIG_FILE = PROJECT_ROOT / "configs" / "config.yaml"
MANIFEST_FILE = PROJECT_ROOT / "assets_manifest.json"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
RUN_FILE = PROJECT_ROOT / "run.py"
VERIFY_SCRIPT = PROJECT_ROOT / "scripts" / "verify_manifest.py"

# Asset links & indexes
WEIGHTS_FILE_ID = "1c-vusZjXSafgXFLbeaFzU6MRuBQGS4-k"
DATA_FILE_ID = "1QHuoY2Wmo49jKrEcf-wvSfA4mU7YL1o5"

PYTORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


def log(message: str) -> None:
    print(f"\n[setup] {message}")


def warn(message: str) -> None:
    print(f"\n[warn] {message}")


def fail(message: str, code: int = 1) -> None:
    print(f"\n[error] {message}", file=sys.stderr)
    raise SystemExit(code)


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print(f"[cmd] {' '.join(map(str, args))}")
    return subprocess.run(
        [str(x) for x in args],
        cwd=str(cwd or PROJECT_ROOT),
        text=True,
        check=check,
    )


def get_system_python() -> str:
    python = shutil.which("python")
    if not python:
        fail(
            "Python not found. Install Python 3.11 or 3.12 first "
            "(project requires >=3.11,<3.13)."
        )
    return python


def get_python_version(python: str) -> tuple[int, int, int]:
    completed = subprocess.run(
        [
            python,
            "-c",
            "import sys; print(sys.version_info.major, sys.version_info.minor, sys.version_info.micro)",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        fail("Failed to determine Python version.")

    try:
        major, minor, micro = map(int, completed.stdout.strip().split())
    except ValueError:
        fail(f"Could not parse Python version: {completed.stdout.strip()}")

    return major, minor, micro


def ensure_project_files() -> None:
    required = [REQUIREMENTS_FILE, RUN_FILE, MANIFEST_FILE, VERIFY_SCRIPT]
    missing = [
        str(path.relative_to(PROJECT_ROOT))
        for path in required
        if not path.is_file()
    ]

    if missing:
        fail("Missing required project files: " + ", ".join(missing))


def get_venv_python() -> Path:
    return VENV_DIR / "Scripts" / "python.exe"


def ensure_venv(system_python: str) -> Path:
    venv_python = get_venv_python()

    if not VENV_DIR.exists():
        log("Creating virtual environment in .venv ...")
        run([system_python, "-m", "venv", str(VENV_DIR)])
    else:
        log("Virtual environment .venv already exists, reusing it.")

    if not venv_python.is_file():
        fail(f"Could not find virtual environment Python: {venv_python}")

    return venv_python


def detect_nvidia_gpu() -> bool:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return False

    result = subprocess.run(
        [nvidia_smi, "-L"],
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0 or not result.stdout.strip():
        return False

    log("NVIDIA GPU detected:")
    print(result.stdout.strip())
    return True


def install_torch(venv_python: Path, has_gpu: bool) -> None:
    """Install PyTorch build based on CUDA availability."""
    if has_gpu:
        log("NVIDIA GPU detected.")
        log("Installing PyTorch with CUDA 12.8 support ...")
        run([
            venv_python,
            "-m",
            "pip",
            "install",
            "torch",
            "torchvision",
            "--index-url",
            PYTORCH_CUDA_INDEX,
        ])
    else:
        log("No NVIDIA GPU detected.")
        log("Installing CPU-only PyTorch ...")
        run([
            venv_python,
            "-m",
            "pip",
            "install",
            "torch",
            "torchvision",
            "--index-url",
            PYTORCH_CPU_INDEX,
        ])


def verify_torch(venv_python: Path, has_gpu: bool) -> None:
    """Verify PyTorch installation and runtime device access."""
    log("Verifying PyTorch installation ...")

    result = subprocess.run(
        [
            str(venv_python),
            "-c",
            (
                "import torch; "
                "print('PyTorch:', torch.__version__); "
                "print('CUDA available:', torch.cuda.is_available()); "
                "print('CUDA version:', torch.version.cuda); "
                "print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
            ),
        ],
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    if result.stdout:
        print(result.stdout.strip())

    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)

    if result.returncode != 0:
        fail("PyTorch verification failed.")

    if has_gpu:
        cuda_check = subprocess.run(
            [
                str(venv_python),
                "-c",
                "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)",
            ],
            cwd=str(PROJECT_ROOT),
            text=True,
            check=False,
        )

        if cuda_check.returncode != 0:
            fail(
                "NVIDIA GPU was detected, but PyTorch cannot access CUDA.\n"
                "The CUDA-enabled PyTorch installation is not working correctly."
            )

        log("CUDA verification passed.")
    else:
        log("CPU mode verified.")


def patch_config_for_cpu(has_gpu: bool) -> None:
    if not CONFIG_FILE.is_file():
        fail(f"{CONFIG_FILE.relative_to(PROJECT_ROOT)} not found.")

    original = CONFIG_FILE.with_name(CONFIG_FILE.name + ".orig")

    if not has_gpu:
        if not original.exists():
            shutil.copy2(CONFIG_FILE, original)
            log(f"Backed up {CONFIG_FILE.relative_to(PROJECT_ROOT)}")

        text = CONFIG_FILE.read_text(encoding="utf-8")
        updated = text.replace('device: "cuda"', 'device: "cpu"')

        if updated != text:
            CONFIG_FILE.write_text(updated, encoding="utf-8")
            log('No GPU: changed device: "cuda" to device: "cpu"')
        else:
            log('No GPU: no device: "cuda" entry found.')

    elif original.exists():
        log("GPU present: restoring original config.")
        shutil.copy2(original, CONFIG_FILE)


def ensure_gdown(venv_python: Path) -> None:
    log("Installing gdown ...")
    run([venv_python, "-m", "pip", "install", "gdown"])


def verify_group(venv_python: Path, group: str) -> bool:
    result = subprocess.run(
        [str(venv_python), str(VERIFY_SCRIPT), "--group", group],
        cwd=str(PROJECT_ROOT),
        text=True,
        check=False,
    )
    return result.returncode == 0


def validate_file_id(file_id: str, name: str) -> None:
    if not file_id or file_id.startswith("<PASTE_"):
        fail(
            f"{name} is not configured. "
            "Edit WEIGHTS_FILE_ID and DATA_FILE_ID in scripts/setup_e2e.py."
        )


def download_with_gdown(
    venv_python: Path,
    file_id: str,
    output_path: Path,
) -> None:
    url = f"https://drive.google.com/uc?id={file_id}"

    log(f"Downloading {output_path.name} from Google Drive ...")
    run([venv_python, "-m", "gdown", url, "-O", output_path])

    if not output_path.is_file():
        fail(f"Downloaded archive was not found: {output_path}")


def extract_zip(zip_path: Path) -> None:
    log(f"Extracting {zip_path.name} ...")

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(PROJECT_ROOT)
    except zipfile.BadZipFile:
        fail(f"{zip_path.name} is not a valid ZIP archive.")


def download_and_extract(
    venv_python: Path,
    file_id: str,
    out_name: str,
    group: str,
) -> None:
    log(f"Checking manifest for group '{group}' ...")

    if verify_group(venv_python, group):
        log(f"{out_name}: matches assets_manifest.json - skipping download.")
        return

    validate_file_id(
        file_id,
        "WEIGHTS_FILE_ID" if group == "weights" else "DATA_FILE_ID",
    )

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DOWNLOAD_DIR / out_name

    download_with_gdown(venv_python, file_id, output_path)
    extract_zip(output_path)

    log(f"Verifying extracted '{group}' files ...")

    if not verify_group(venv_python, group):
        fail(
            f"{out_name} was extracted but still does not match "
            "assets_manifest.json."
        )

    log(f"{out_name}: verified.")


def main() -> int:
    os.chdir(PROJECT_ROOT)
    log(f"Project root: {PROJECT_ROOT}")

    system_python = get_system_python()
    major, minor, micro = get_python_version(system_python)

    log(f"Using Python {major}.{minor}.{micro} ({system_python})")

    if not ((major == 3 and minor == 11) or (major == 3 and minor == 12)):
        warn(
            f"Project targets Python >=3.11,<3.13. "
            f"Detected {major}.{minor}.{micro}; continuing anyway."
        )

    ensure_project_files()

    warn(
        "Native Windows mode: Linux apt packages are not installed. "
        "If pyzbar/barcode support fails, install a compatible ZBar DLL."
    )

    venv_python = ensure_venv(system_python)

    log("Upgrading pip ...")
    run([venv_python, "-m", "pip", "install", "--upgrade", "pip"])

    has_gpu = detect_nvidia_gpu()
    install_torch(venv_python, has_gpu)
    verify_torch(venv_python, has_gpu)

    log("Installing project dependencies ...")
    run([venv_python, "-m", "pip", "install", "-r", REQUIREMENTS_FILE])

    ensure_gdown(venv_python)
    patch_config_for_cpu(has_gpu)

    download_and_extract(
        venv_python, WEIGHTS_FILE_ID, "weights.zip", "weights"
    )
    download_and_extract(venv_python, DATA_FILE_ID, "data.zip", "data")

    log("Running test suite (pytest) ...")
    test_result = subprocess.run(
        [str(venv_python), "-m", "pytest", "-q"],
        cwd=str(PROJECT_ROOT),
        text=True,
        check=False,
    )

    if test_result.returncode != 0:
        fail(
            "Some tests failed. Check the output above before continuing to UI."
        )

    log("All checks passed. Launching UI ...")
    run([venv_python, RUN_FILE, "--mode", "ui"])

    log("Setup completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())