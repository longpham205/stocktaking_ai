
"""
Stocktaking AI - Config-Aware Requirements Checker

Usage:
    python scripts/check_requirements.py

The project root, requirements.txt and config.yaml are detected
automatically. No command-line arguments are required.

The checker performs:

1. Static Python import analysis
2. requirements.txt analysis
3. config.yaml backend analysis
4. Dependency inference from enabled backends
5. Required / optional / unused classification
6. System-level dependency detection

Categories:

    [REQUIRED - IMPORT]
    [REQUIRED - CONFIG]
    [OPTIONAL]
    [UNUSED?]
    [SYSTEM]
    [DEV/TEST]
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

import yaml


# ============================================================================
# CONFIGURATION
# ============================================================================

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"

CONFIG_FILE = PROJECT_ROOT / "configs" / "config.yaml"


# ---------------------------------------------------------------------------
# Python files to scan
# ---------------------------------------------------------------------------

# None:
#     scan entire project
#
# Otherwise:
#     scan only these locations.
#
# This is intentionally explicit so data/, weights/, cache/, etc.
# do not affect dependency analysis.

SCAN_PATHS = [
    PROJECT_ROOT / "src",
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "tests",
    PROJECT_ROOT / "run.py",
]


# ---------------------------------------------------------------------------
# Directories to ignore
# ---------------------------------------------------------------------------

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
}


# ============================================================================
# PACKAGE MAPPING
# ============================================================================

# Python import name -> PyPI distribution name.

IMPORT_TO_PACKAGE = {
    # ------------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------------

    "numpy": "numpy",
    "cv2": "opencv-python-headless",
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "matplotlib": "matplotlib",
    "pandas": "pandas",

    # ------------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------------

    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",

    # ------------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------------

    "faiss": "faiss-cpu",

    # ------------------------------------------------------------------------
    # OCR / Barcode
    # ------------------------------------------------------------------------

    "pytesseract": "pytesseract",
    "pyzbar": "pyzbar",
    "easyocr": "easyocr",

    # ------------------------------------------------------------------------
    # Deep Learning
    # ------------------------------------------------------------------------

    "torch": "torch",
    "torchvision": "torchvision",
    "torchaudio": "torchaudio",
    "transformers": "transformers",

    # ------------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------------

    "rfdetr": "rfdetr",
    "supervision": "supervision",

    # ------------------------------------------------------------------------
    # Segmentation
    # ------------------------------------------------------------------------

    "sam2": "sam2",

    # ------------------------------------------------------------------------
    # Testing
    # ------------------------------------------------------------------------

    "pytest": "pytest",
    "pytest_cov": "pytest-cov",
}


# ============================================================================
# OPTIONAL PACKAGES
# ============================================================================

# These packages can exist in the source code without necessarily being
# required by the currently configured runtime backend.
#
# IMPORTANT:
# If config.yaml enables a backend that requires one of these packages,
# the package becomes REQUIRED BY CONFIG.

OPTIONAL_PACKAGES = {
    "torch",
    "torchvision",
    "torchaudio",
    "transformers",
    "rfdetr",
    "supervision",
    "sam2",
    "easyocr",
}


# ============================================================================
# DEVELOPMENT / TEST PACKAGES
# ============================================================================

# These are not required for normal application runtime, but are expected
# to be installed when running the development/test environment.

DEV_TEST_PACKAGES = {
    "pytest",
    "pytest-cov",
}


# ============================================================================
# SYSTEM DEPENDENCIES
# ============================================================================

# Python packages that also require system-level dependencies.

SYSTEM_DEPENDENCIES = {
    "pytesseract": "Tesseract OCR executable",
    "pyzbar": "ZBar system library",
    "tkinter": "Tkinter / python3-tk",
}


# ============================================================================
# CONFIG -> BACKEND -> DEPENDENCY RULES
# ============================================================================

"""
This section is the most important addition compared with the previous
version.

The checker reads configs/config.yaml and determines which backend is
actually enabled.

Example:

    detection:
      backend: rf_detr

Then:

    rf_detr
       |
       +-- rfdetr
       +-- torch
       +-- supervision

Likewise:

    retrieval:
      backend: siglip2

Then:

    siglip2
       |
       +-- transformers
       +-- torch

The package names below are normalized internally.
"""

BACKEND_DEPENDENCIES: dict[str, set[str]] = {

    # ------------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------------

    "rf_detr": {
        "rfdetr",
        "torch",
        "supervision",
    },

    "rfdetr": {
        "rfdetr",
        "torch",
        "supervision",
    },

    "yolo": {
        "ultralytics",
        "torch",
    },

    "ultralytics": {
        "ultralytics",
        "torch",
    },

    "groundingdino": {
        "groundingdino",
        "torch",
    },

    "mock_contour": set(),

    "mock": set(),

    # ------------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------------

    "siglip2": {
        "transformers",
        "torch",
    },

    "mock_visual_embedding": set(),

    # ------------------------------------------------------------------------
    # Refinement
    # ------------------------------------------------------------------------

    "sam2": {
        "sam2",
        "torch",
    },

    "none": set(),

    # ------------------------------------------------------------------------
    # OCR
    # ------------------------------------------------------------------------

    "easyocr": {
        "easyocr",
        "torch",
    },

    "pytesseract": {
        "pytesseract",
    },
}


# ============================================================================
# PYTHON STANDARD LIBRARY
# ============================================================================

STDLIB_MODULES = {
    "abc",
    "argparse",
    "ast",
    "asyncio",
    "base64",
    "collections",
    "concurrent",
    "contextlib",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "enum",
    "functools",
    "glob",
    "hashlib",
    "http",
    "importlib",
    "inspect",
    "io",
    "itertools",
    "json",
    "logging",
    "math",
    "multiprocessing",
    "os",
    "pathlib",
    "pickle",
    "platform",
    "random",
    "re",
    "shutil",
    "sqlite3",
    "statistics",
    "string",
    "subprocess",
    "sys",
    "tempfile",
    "textwrap",
    "threading",
    "time",
    "traceback",
    "types",
    "typing",
    "typing_extensions",
    "unittest",
    "urllib",
    "uuid",
    "warnings",
    "weakref",
    "xml",
    "zipfile",
}


# ============================================================================
# HELPERS
# ============================================================================

def normalize_package_name(name: str) -> str:
    """
    Normalize a package name for comparison.

    Examples:

        PyYAML
        pyyaml

        pytest_cov
        pytest-cov
    """

    return re.sub(
        r"[-_.]+",
        "-",
        name.strip().lower(),
    )


def root_package(import_name: str) -> str:
    """Return top-level Python package."""

    return import_name.split(".")[0]


def is_stdlib(import_name: str) -> bool:
    """Check whether an import belongs to Python stdlib."""

    root = root_package(import_name)

    if root in STDLIB_MODULES:
        return True

    stdlib_names = getattr(
        sys,
        "stdlib_module_names",
        set(),
    )

    return root in stdlib_names


def is_excluded(path: Path) -> bool:
    """Check whether a path belongs to an excluded directory."""

    try:
        relative = path.relative_to(
            PROJECT_ROOT
        )
    except ValueError:
        return True

    return any(
        part in EXCLUDED_DIRS
        for part in relative.parts
    )


def is_local_import(module_name: str) -> bool:
    """
    Detect project-local imports.

    Examples:

        src.core.config
        src.detector
        scripts.foo
    """

    if not module_name:
        return False

    parts = module_name.split(".")

    candidate = PROJECT_ROOT.joinpath(
        *parts
    )

    if candidate.with_suffix(".py").exists():
        return True

    if (candidate / "__init__.py").exists():
        return True

    first = PROJECT_ROOT / parts[0]

    if first.is_dir():

        if (first / "__init__.py").exists():
            return True

        if any(first.glob("*.py")):
            return True

    return False


# ============================================================================
# PYTHON SCANNER
# ============================================================================

def scan_python_file(path: Path) -> set[str]:
    """
    Extract imports using Python AST.

    Supports:

        import numpy
        import numpy as np
        from numpy import array
        from numpy.linalg import norm
    """

    imports: set[str] = set()

    try:

        source = path.read_text(
            encoding="utf-8"
        )

    except UnicodeDecodeError:

        try:

            source = path.read_text(
                encoding="utf-8-sig"
            )

        except Exception as exc:

            print(
                f"[WARN] Cannot read "
                f"{path}: {exc}"
            )

            return imports

    except Exception as exc:

        print(
            f"[WARN] Cannot read "
            f"{path}: {exc}"
        )

        return imports

    try:

        tree = ast.parse(
            source,
            filename=str(path),
        )

    except SyntaxError as exc:

        print(
            f"[WARN] Syntax error: "
            f"{path} "
            f"(line {exc.lineno}: "
            f"{exc.msg})"
        )

        return imports

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):

            for alias in node.names:
                imports.add(alias.name)

        elif isinstance(node, ast.ImportFrom):

            # Relative imports are project-local.
            if node.level > 0:
                continue

            if node.module:
                imports.add(node.module)

    return imports


def get_python_files() -> list[Path]:
    """Return Python files configured for scanning."""

    files: set[Path] = set()

    # Scan entire project.
    if SCAN_PATHS is None:

        for path in PROJECT_ROOT.rglob("*.py"):

            if not is_excluded(path):
                files.add(path)

        return sorted(files)

    # Scan explicit locations.
    for scan_path in SCAN_PATHS:

        if not scan_path.exists():
            continue

        if scan_path.is_file():

            if (
                scan_path.suffix == ".py"
                and not is_excluded(scan_path)
            ):
                files.add(scan_path)

        else:

            for path in scan_path.rglob("*.py"):

                if not is_excluded(path):
                    files.add(path)

    return sorted(files)


def scan_project() -> dict[str, set[Path]]:
    """
    Scan the project.

    Returns:

        {
            "numpy": {
                Path("src/foo.py"),
                Path("src/bar.py"),
            }
        }
    """

    result: dict[str, set[Path]] = {}

    for path in get_python_files():

        imports = scan_python_file(path)

        for imported in imports:

            top_level = root_package(
                imported
            )

            result.setdefault(
                top_level,
                set(),
            ).add(path)

    return result


# ============================================================================
# REQUIREMENTS PARSER
# ============================================================================

def parse_requirements() -> tuple[
    set[str],
    dict[str, str],
]:
    """
    Parse requirements.txt.

    Supports:

        numpy
        numpy>=1.26
        numpy>=1.26,<3.0
        PyYAML==6.0
        package; python_version >= "3.11"

    Lines such as:

        -r requirements-core.txt
        --extra-index-url ...

    are ignored.
    """

    packages: set[str] = set()

    original: dict[str, str] = {}

    if not REQUIREMENTS_FILE.exists():

        print(
            "[ERROR] Requirements file not found:"
        )

        print(
            f"        {REQUIREMENTS_FILE}"
        )

        return packages, original

    try:

        lines = REQUIREMENTS_FILE.read_text(
            encoding="utf-8"
        ).splitlines()

    except Exception as exc:

        print(
            f"[ERROR] Cannot read "
            f"{REQUIREMENTS_FILE}: {exc}"
        )

        return packages, original

    for raw_line in lines:

        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if line.startswith("-"):
            continue

        line = line.split(
            "#",
            1
        )[0].strip()

        if not line:
            continue

        # Remove environment markers.
        package_part = line.split(
            ";",
            1
        )[0].strip()

        match = re.match(
            r"^([A-Za-z0-9_.-]+)"
            r"(?:\s*(?:==|!=|<=|>=|~=|<|>|===).*)?$",
            package_part,
        )

        if not match:

            print(
                "[INFO] Skipping complex "
                f"requirement: {line}"
            )

            continue

        package_name = match.group(1)

        normalized = normalize_package_name(
            package_name
        )

        packages.add(normalized)

        original[normalized] = line

    return packages, original


# ============================================================================
# CONFIG.YAML
# ============================================================================

def load_config() -> dict[str, Any]:
    """Load configs/config.yaml."""

    if not CONFIG_FILE.exists():

        print(
            "[WARN] Config file not found:"
        )

        print(
            f"       {CONFIG_FILE}"
        )

        return {}

    try:

        with CONFIG_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = yaml.safe_load(file)

    except Exception as exc:

        print(
            f"[WARN] Cannot parse config: "
            f"{exc}"
        )

        return {}

    if not isinstance(data, dict):

        print(
            "[WARN] config.yaml root is not "
            "a mapping/object."
        )

        return {}

    return data


def get_nested(
    data: dict[str, Any],
    *keys: str,
) -> Any:
    """Safely retrieve nested config values."""

    current: Any = data

    for key in keys:

        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def detect_config_backends(
    config: dict[str, Any],
) -> dict[str, str]:
    """
    Detect enabled backends from config.yaml.

    Known locations:

        detection.backend
        retrieval.backend
        refinement.backend

    Also detects:

        ocr.backend

    Returns:

        {
            "detection": "rf_detr",
            "retrieval": "siglip2",
            "refinement": "sam2",
            "ocr": "easyocr",
        }
    """

    backend_paths = {
        "detection": (
            "detection",
            "backend",
        ),
        "retrieval": (
            "retrieval",
            "backend",
        ),
        "refinement": (
            "refinement",
            "backend",
        ),
        "ocr": (
            "ocr",
            "backend",
        ),
    }

    result: dict[str, str] = {}

    for name, keys in backend_paths.items():

        value = get_nested(
            config,
            *keys,
        )

        if isinstance(value, str):

            result[name] = value.strip().lower()

    return result


def infer_config_dependencies(
    backends: dict[str, str],
) -> dict[str, set[str]]:
    """
    Infer required packages from enabled config backends.

    Returns:

        {
            "rf_detr": {"rfdetr", "torch", "supervision"},
            "siglip2": {"transformers", "torch"},
        }
    """

    result: dict[str, set[str]] = {}

    for component, backend in backends.items():

        dependencies = (
            BACKEND_DEPENDENCIES.get(
                backend,
                set(),
            )
        )

        result[
            f"{component}:{backend}"
        ] = dependencies.copy()

    return result


# ============================================================================
# CLASSIFICATION
# ============================================================================

def classify_imports(
    imports_to_files: dict[str, set[Path]],
) -> tuple[
    set[str],
    set[str],
    set[str],
]:
    """Classify imports into stdlib/local/third-party."""

    stdlib: set[str] = set()

    local: set[str] = set()

    third_party: set[str] = set()

    for import_name in imports_to_files:

        if is_stdlib(import_name):

            stdlib.add(import_name)

        elif is_local_import(import_name):

            local.add(import_name)

        else:

            third_party.add(import_name)

    return (
        stdlib,
        local,
        third_party,
    )


def import_to_package(
    import_name: str,
) -> str:
    """Map Python import to PyPI package."""

    return IMPORT_TO_PACKAGE.get(
        import_name,
        import_name,
    )


# ============================================================================
# REPORT
# ============================================================================

def print_section(title: str) -> None:

    print()

    print(
        "=" * 72
    )

    print(
        f" {title}"
    )

    print(
        "=" * 72
    )


def main() -> int:

    # ------------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------------

    print_section(
        "STOCKTAKING AI - REQUIREMENTS CHECK"
    )

    print(
        f"Project root : "
        f"{PROJECT_ROOT}"
    )

    print(
        f"Requirements : "
        f"{REQUIREMENTS_FILE}"
    )

    print(
        f"Config       : "
        f"{CONFIG_FILE}"
    )

    print(
        f"Python       : "
        f"{sys.version.split()[0]}"
    )

    print(
        f"Executable   : "
        f"{sys.executable}"
    )

    # ------------------------------------------------------------------------
    # Scan source
    # ------------------------------------------------------------------------

    python_files = get_python_files()

    imports_to_files = scan_project()

    stdlib, local, third_party = (
        classify_imports(
            imports_to_files
        )
    )

    # ------------------------------------------------------------------------
    # Parse requirements
    # ------------------------------------------------------------------------

    declared, requirement_lines = (
        parse_requirements()
    )

    # ------------------------------------------------------------------------
    # Load config
    # ------------------------------------------------------------------------

    config = load_config()

    backends = detect_config_backends(
        config
    )

    config_dependency_groups = (
        infer_config_dependencies(
            backends
        )
    )

    # ------------------------------------------------------------------------
    # Convert third-party imports -> packages
    # ------------------------------------------------------------------------

    detected_packages: set[str] = set()

    import_package_map: dict[str, str] = {}

    for import_name in third_party:

        package = import_to_package(
            import_name
        )

        normalized = (
            normalize_package_name(
                package
            )
        )

        detected_packages.add(
            normalized
        )

        import_package_map[
            import_name
        ] = package

    # ------------------------------------------------------------------------
    # Dependencies required by config
    # ------------------------------------------------------------------------

    config_required_packages: set[str] = set()

    for dependencies in (
        config_dependency_groups.values()
    ):

        for package in dependencies:

            config_required_packages.add(
                normalize_package_name(
                    package
                )
            )

    # ------------------------------------------------------------------------
    # All required packages
    # ------------------------------------------------------------------------

    required_by_import = (
        detected_packages
        - {
            normalize_package_name(
                package
            )
            for package in OPTIONAL_PACKAGES
        }
        - {
            normalize_package_name(
                package
            )
            for package in DEV_TEST_PACKAGES
        }
    )

    required_packages = (
        required_by_import
        | config_required_packages
    )

    # ------------------------------------------------------------------------
    # Missing
    # ------------------------------------------------------------------------

    missing_required = (
        required_packages - declared
    )

    # Optional packages imported but not declared.
    missing_optional = (
        detected_packages
        - declared
        - required_packages
        - {
            normalize_package_name(
                package
            )
            for package in DEV_TEST_PACKAGES
        }
    )

    # ------------------------------------------------------------------------
    # Dev/test packages
    # ------------------------------------------------------------------------

    dev_test_normalized = {
        normalize_package_name(
            package
        )
        for package in DEV_TEST_PACKAGES
    }

    missing_dev_test = (
        dev_test_normalized
        - declared
        & detected_packages
    )

    # ------------------------------------------------------------------------
    # Potentially unused requirements
    # ------------------------------------------------------------------------

    used_or_required = (
        detected_packages
        | config_required_packages
    )

    potentially_unused = (
        declared
        - used_or_required
        - dev_test_normalized
    )

    # ------------------------------------------------------------------------
    # System dependencies
    # ------------------------------------------------------------------------

    system_dependencies = []

    for import_name in third_party:

        if import_name in SYSTEM_DEPENDENCIES:

            system_dependencies.append(
                (
                    import_name,
                    SYSTEM_DEPENDENCIES[
                        import_name
                    ],
                )
            )

    # ========================================================================
    # SUMMARY
    # ========================================================================

    print_section(
        "SCAN SUMMARY"
    )

    print(
        f"Python files scanned     : "
        f"{len(python_files)}"
    )

    print(
        f"Unique imports detected  : "
        f"{len(imports_to_files)}"
    )

    print(
        f"Stdlib imports           : "
        f"{len(stdlib)}"
    )

    print(
        f"Local imports            : "
        f"{len(local)}"
    )

    print(
        f"Third-party imports      : "
        f"{len(third_party)}"
    )

    print(
        f"Requirements entries     : "
        f"{len(declared)}"
    )

    # ========================================================================
    # CONFIG BACKENDS
    # ========================================================================

    print_section(
        "CONFIGURED BACKENDS"
    )

    if not backends:

        print(
            "  [INFO] No backend configuration "
            "detected."
        )

    else:

        for component, backend in backends.items():

            print(
                f"  {component:<12} -> "
                f"{backend}"
            )

            dependencies = (
                config_dependency_groups.get(
                    f"{component}:{backend}",
                    set(),
                )
            )

            if dependencies:

                print(
                    " " * 17
                    + "requires: "
                    + ", ".join(
                        sorted(
                            dependencies
                        )
                    )
                )

    # ========================================================================
    # THIRD PARTY
    # ========================================================================

    print_section(
        "THIRD-PARTY IMPORTS"
    )

    if not third_party:

        print(
            "  (none)"
        )

    else:

        for import_name in sorted(
            third_party
        ):

            package = (
                import_package_map[
                    import_name
                ]
            )

            normalized = (
                normalize_package_name(
                    package
                )
            )

            if normalized in config_required_packages:

                status = (
                    "REQUIRED BY CONFIG"
                )

            elif normalized in dev_test_normalized:

                status = (
                    "DEV/TEST"
                )

            elif normalized in {
                normalize_package_name(
                    package
                )
                for package in OPTIONAL_PACKAGES
            }:

                status = (
                    "OPTIONAL"
                )

            else:

                status = (
                    "REQUIRED BY IMPORT"
                )

            print(
                f"  [{status:<18}] "
                f"{import_name:<20} -> "
                f"{package}"
            )

    # ========================================================================
    # MISSING REQUIRED
    # ========================================================================

    print_section(
        "MISSING REQUIRED PACKAGES"
    )

    if missing_required:

        for package in sorted(
            missing_required
        ):

            reasons = []

            if package in config_required_packages:
                reasons.append(
                    "config"
                )

            if package in required_by_import:
                reasons.append(
                    "import"
                )

            reason_text = ", ".join(
                reasons
            )

            print(
                f"  [MISSING] "
                f"{package:<20} "
                f"({reason_text})"
            )

    else:

        print(
            "  [OK] No missing required "
            "packages detected."
        )

    # ========================================================================
    # MISSING OPTIONAL
    # ========================================================================

    print_section(
        "MISSING OPTIONAL PACKAGES"
    )

    if missing_optional:

        for package in sorted(
            missing_optional
        ):

            print(
                f"  [OPTIONAL] "
                f"{package}"
            )

    else:

        print(
            "  [OK] No missing optional "
            "packages detected."
        )

    # ========================================================================
    # DEV / TEST
    # ========================================================================

    print_section(
        "DEV / TEST DEPENDENCIES"
    )

    for package in sorted(
        dev_test_normalized
    ):

        if package in declared:

            print(
                f"  [DECLARED] "
                f"{requirement_lines.get(package, package)}"
            )

        elif package in detected_packages:

            print(
                f"  [MISSING] "
                f"{package}"
            )

        else:

            print(
                f"  [OPTIONAL DEV] "
                f"{package}"
            )

    # ========================================================================
    # REQUIREMENTS.TXT
    # ========================================================================

    print_section(
        "REQUIREMENTS.TXT"
    )

    for package in sorted(
        declared
    ):

        expression = (
            requirement_lines.get(
                package,
                package,
            )
        )

        if package in config_required_packages:

            status = (
                "REQUIRED-CONFIG"
            )

        elif package in detected_packages:

            status = (
                "USED"
            )

        elif package in dev_test_normalized:

            status = (
                "DEV/TEST"
            )

        else:

            status = (
                "UNUSED?"
            )

        print(
            f"  [{status:<15}] "
            f"{expression}"
        )

    # ========================================================================
    # SYSTEM DEPENDENCIES
    # ========================================================================

    print_section(
        "SYSTEM-LEVEL DEPENDENCIES"
    )

    if system_dependencies:

        for import_name, description in sorted(
            system_dependencies
        ):

            print(
                f"  [SYSTEM] "
                f"{import_name}"
            )

            print(
                f"           {description}"
            )

    else:

        print(
            "  [OK] No known system-level "
            "dependencies detected."
        )

    # ========================================================================
    # FINAL RESULT
    # ========================================================================

    print_section(
        "FINAL RESULT"
    )

    if missing_required:

        print(
            "[FAIL] requirements.txt is "
            "missing required dependencies."
        )

        print()

        print(
            "Missing:"
        )

        for package in sorted(
            missing_required
        ):

            print(
                f"  - {package}"
            )

        return 1

    if missing_optional:

        print(
            "[WARN] Required dependencies "
            "appear complete."
        )

        print(
            "       Optional dependencies are "
            "not declared."
        )

        return 0

    print(
        "[PASS] requirements.txt appears "
        "complete for the configured "
        "runtime and detected imports."
    )

    return 0


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":

    raise SystemExit(
        main()
    )
