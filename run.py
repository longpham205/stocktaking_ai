"""Stocktaking AI - standalone execution entry point.

Usage:
    Inference (single image):
        python run.py --mode infer --image data/query/test_shelf.jpg

    Inference (batch directory):
        python run.py --mode infer --image-dir data/query/

    Validation:
        python run.py --mode validate --benchmark-dir data/benchmark/

    Desktop UI:
        python run.py --mode ui

By default, every mode first runs the offline build pipeline (product
metadata + gallery FAISS index — see src/pipeline/build.py) so the
gallery is always in sync with data/gallery/ before inference/validation
runs. Pass --skip-build to reuse the existing data/metadata/ and
data/cache/ artifacts as-is (faster startup once the gallery is stable).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from src.core.config import load_config
from src.core.logger import setup_logger
from src.inference.infer import InferenceRunner
from src.pipeline.build import run_build
from src.validation.validate import ValidationRunner


def _build_arg_parser() -> argparse.ArgumentParser:
    """Builds the command-line argument parser.

    Returns:
        A configured argparse.ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Stocktaking AI - AI Product Inventory System (Minimal Version 0.1.0)",
    )
    parser.add_argument(
        "--mode",
        choices=["infer", "validate", "ui"],
        required=True,
        help="Operating mode to execute.",
    )
    parser.add_argument("--image", type=str, default=None, help="Path to a single query image (infer mode).")
    parser.add_argument(
        "--image-dir", type=str, default=None, help="Path to a directory of query images (infer mode, batch)."
    )
    parser.add_argument(
        "--benchmark-dir", type=str, default=None, help="Path to the benchmark root directory (validate mode)."
    )
    parser.add_argument("--config", type=str, default=None, help="Optional explicit path to config.yaml.")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip the offline build pipeline (metadata + gallery index) and reuse existing artifacts.",
    )
    return parser


def _run_infer(args: argparse.Namespace) -> int:
    """Executes Inference Mode based on parsed CLI arguments.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Process exit code (0 on success, non-zero on failure).
    """
    config = load_config(args.config)
    logger = setup_logger(
        __name__,
        level=config.logging.level,
        log_dir=str(config.resolve_path(config.logging.log_dir)),
        log_to_file=config.logging.log_to_file,
        log_to_console=config.logging.log_to_console,
    )

    if not args.image and not args.image_dir:
        logger.error("Inference mode requires --image or --image-dir.")
        return 1

    runner = InferenceRunner(config)

    if args.image:
        result = runner.run_single(args.image)
        print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    else:
        results = runner.run_batch(args.image_dir)
        print(f"Processed {len(results)} image(s). See '{config.paths.output_dir}' for exported artifacts.")

    return 0


def _run_validate(args: argparse.Namespace) -> int:
    """Executes Validation Mode based on parsed CLI arguments.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Process exit code (0 on success, non-zero on failure).
    """
    config = load_config(args.config)
    logger = setup_logger(
        __name__,
        level=config.logging.level,
        log_dir=str(config.resolve_path(config.logging.log_dir)),
        log_to_file=config.logging.log_to_file,
        log_to_console=config.logging.log_to_console,
    )

    benchmark_dir = args.benchmark_dir or str(config.resolve_path(config.paths.benchmark_dir))
    if not args.benchmark_dir:
        logger.warning("--benchmark-dir not provided; defaulting to '%s'", benchmark_dir)

    runner = ValidationRunner(config)
    report = runner.run(benchmark_dir)

    summary = {key: value for key, value in report.items() if key not in ("per_image", "per_product", "records")}
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nFull reports written to '{config.paths.output_dir}'.")
    return 0


def _run_ui(args: argparse.Namespace) -> int:
    """Launches the desktop UI application.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Process exit code (always 0; the UI runs its own event loop).
    """
    from src.ui.app import launch_app

    launch_app(args.config)
    return 0


def main() -> int:
    """CLI entry point: runs the offline build pipeline, then dispatches to the requested mode.

    Returns:
        Process exit code.
    """
    parser = _build_arg_parser()
    args = parser.parse_args()

    if not args.skip_build:
        config = load_config(args.config)
        run_build(config)

    if args.mode == "infer":
        return _run_infer(args)
    if args.mode == "validate":
        return _run_validate(args)
    if args.mode == "ui":
        return _run_ui(args)

    parser.error(f"Unknown mode: {args.mode}")  # pragma: no cover - guarded by argparse choices
    return 2


if __name__ == "__main__":
    sys.exit(main())
