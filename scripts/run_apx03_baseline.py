#!/usr/bin/env python3
"""
scripts/run_apx03_baseline.py — Execution and Build Helper for APX-03 C++ Baseline.

Orchestrates:
1. Portable CMake build of extensions/apx03 if binary is absent or --build requested.
2. Direct invocation of rapidflow_apx03_baseline with structured CLI arguments.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APX03_DIR = REPO_ROOT / "extensions" / "apx03"
BUILD_DIR = APX03_DIR / "build"
BINARY_PATH = BUILD_DIR / "rapidflow_apx03_baseline"
CMAKE_PATH = REPO_ROOT / "external" / "tools" / "cmake-3.28.3-linux-x86_64" / "bin" / "cmake"


def build_binary(clean: bool = False) -> Path:
    """Builds the APX-03 C++ baseline binary using the pinned portable CMake."""
    cmake_bin = str(CMAKE_PATH) if CMAKE_PATH.exists() else "cmake"

    if clean and BUILD_DIR.exists():
        import shutil
        shutil.rmtree(BUILD_DIR)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    config_cmd = [cmake_bin, "-B", str(BUILD_DIR), "-S", str(APX03_DIR)]
    build_cmd = [cmake_bin, "--build", str(BUILD_DIR), "-j4"]

    subprocess.check_call(config_cmd, cwd=str(REPO_ROOT))
    subprocess.check_call(build_cmd, cwd=str(REPO_ROOT))

    if not BINARY_PATH.exists():
        raise RuntimeError(f"Build completed but binary not found at {BINARY_PATH}")
    return BINARY_PATH


def main(argv=None):
    parser = argparse.ArgumentParser(description="APX-03 C++ Baseline Runner")
    parser.add_argument("--build-only", action="store_true", help="Compile the C++ binary and exit")
    parser.add_argument("--clean-build", action="store_true", help="Clean build directory before compiling")
    parser.add_argument("--query", type=str, help="Query graph path")
    parser.add_argument("--data", type=str, help="Data graph path")
    parser.add_argument("--eps-m", type=int, default=0, help="Missing edge threshold (default: 0)")
    parser.add_argument("--eps-x", type=str, default="None", help="External cut threshold (default: None)")
    parser.add_argument("--mode", type=str, default="STRICT", choices=["STRICT", "RELAXED"], help="Mode")
    parser.add_argument("--stream", type=str, default=None, help="Update stream path")
    parser.add_argument("--evaluate-mapping", type=str, default=None, help="Direct evaluation of mapping")
    parser.add_argument("--max-candidates", type=int, default=10000000, help="Max candidates limit")
    parser.add_argument("--output", type=str, default=None, help="JSON output file path")

    args, unknown = parser.parse_known_args(argv)

    if args.build_only or args.clean_build or not BINARY_PATH.exists():
        print(f"[APX-03 RUNNER] Compiling rapidflow_apx03_baseline...")
        build_binary(clean=args.clean_build)
        print(f"[APX-03 RUNNER] Binary ready at {BINARY_PATH}")
        if args.build_only:
            return 0

    if not args.query or not args.data:
        parser.error("--query and --data are required when executing the baseline.")

    cmd = [
        str(BINARY_PATH),
        "--query", args.query,
        "--data", args.data,
        "--eps-m", str(args.eps_m),
        "--eps-x", str(args.eps_x),
        "--mode", args.mode,
        "--max-candidates", str(args.max_candidates),
    ]
    if args.stream:
        cmd.extend(["--stream", args.stream])
    if args.evaluate_mapping:
        cmd.extend(["--evaluate-mapping", args.evaluate_mapping])
    if args.output:
        cmd.extend(["--output", args.output])
    cmd.extend(unknown)

    proc = subprocess.run(cmd)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
