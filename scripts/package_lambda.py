#!/usr/bin/env python3
"""
AWS Lambda Deployment Packaging Script for Mestvire.

Builds a clean, reproducible deployment_package.zip ready for AWS Lambda upload.
Compatible with Windows, macOS, and Linux.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_ROOT / "build" / "lambda_package"
OUTPUT_ZIP = PROJECT_ROOT / "deployment_package.zip"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
LAMBDA_ENTRYPOINT = PROJECT_ROOT / "lambda_function.py"
SRC_DIR = PROJECT_ROOT / "src"


DATA_DIR = PROJECT_ROOT / "data"


def clean() -> None:
    """Clean previous build artifacts."""
    print("Cleaning previous build artifacts...")
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()
    BUILD_DIR.mkdir(parents=True, exist_ok=True)


def install_dependencies() -> None:
    """Install requirements.txt dependencies into the build directory for Linux x86_64 target."""
    print("Installing production dependencies for AWS Lambda (Linux x86_64)...")
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--platform",
        "manylinux2014_x86_64",
        "--target",
        str(BUILD_DIR),
        "--implementation",
        "cp",
        "--python-version",
        "3.12",
        "--only-binary=:all:",
        "-r",
        str(REQUIREMENTS_FILE),
        "--no-compile",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: pip install failed:\n{result.stderr}")
        sys.exit(1)
    print("Dependencies installed successfully.")


def copy_source_code() -> None:
    """Copy lambda_function.py, src/ package, and data/ directory into build directory."""
    print("Copying application source code and data manifest...")
    # Copy lambda_function.py
    shutil.copy2(LAMBDA_ENTRYPOINT, BUILD_DIR / "lambda_function.py")

    # Copy src/ recursively, ignoring caches
    dest_src = BUILD_DIR / "src"
    shutil.copytree(
        SRC_DIR,
        dest_src,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.pyd"),
    )

    # Copy data/ directory containing companies_manifest.yaml
    dest_data = BUILD_DIR / "data"
    shutil.copytree(
        DATA_DIR,
        dest_data,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.pyd"),
    )
    print("Source code and data directory copied.")


def remove_bloat() -> None:
    """Strip unnecessary cache files and dist-info bloat to minimize zip size."""
    print("Cleaning unnecessary caches and bloat...")
    for root, dirs, files in os.walk(BUILD_DIR):
        for d in list(dirs):
            if d in ("__pycache__", "tests", ".pytest_cache"):
                shutil.rmtree(Path(root) / d, ignore_errors=True)
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                try:
                    (Path(root) / f).unlink()
                except OSError:
                    pass


def create_zip() -> None:
    """Compress the build directory into deployment_package.zip."""
    print(f"Creating deployment zip: {OUTPUT_ZIP.name}...")
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(BUILD_DIR):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(BUILD_DIR)
                zipf.write(file_path, arcname)

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"Package created successfully! Size: {size_mb:.2f} MB")


def main() -> None:
    print(f"{'='*70}")
    print("Mestvire AWS Lambda Package Builder")
    print(f"Root: {PROJECT_ROOT}")
    print(f"{'='*70}")

    clean()
    install_dependencies()
    copy_source_code()
    remove_bloat()
    create_zip()

    print(f"\n{'='*70}")
    print("BUILD COMPLETE!")
    print(f"Deployment artifact: {OUTPUT_ZIP}")
    print("\nNext step: Deploy to AWS Lambda via AWS CLI:")
    print("  aws lambda update-function-code \\")
    print("      --function-name jobs-tracker-scraper \\")
    print(f"      --zip-file fileb://deployment_package.zip")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
