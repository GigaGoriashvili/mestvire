#!/usr/bin/env python3
"""
Test runner script for Mestvire.
Discovers and executes all test suites in the tests/ directory.
"""

import importlib
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEST_MODULES = [
    "tests.test_config_ssm",
    "tests.test_database_dynamodb",
    "tests.test_lambda_function",
    "tests.test_llm",
    "tests.test_senior_filter",
]


def run_test_module(module_name: str) -> bool:
    """Import and execute all test functions in a test module."""
    print(f"\n{'='*70}")
    print(f"RUNNING: {module_name}")
    print(f"{'='*70}")
    try:
        mod = importlib.import_module(module_name)
    except Exception as e:
        print(f"FAILED TO IMPORT {module_name}: {e}")
        return False

    test_funcs = [
        name for name, obj in mod.__dict__.items()
        if name.startswith("test_") and callable(obj)
    ]

    all_passed = True
    for func_name in test_funcs:
        func = getattr(mod, func_name)
        try:
            func()
        except Exception as e:
            print(f"FAIL: {func_name} in {module_name}: {e}")
            all_passed = False

    return all_passed


def main() -> int:
    print("Mestvire Automated Test Runner")
    print(f"Base Directory: {PROJECT_ROOT}")
    print(f"Discovered {len(TEST_MODULES)} test modules.")

    start_time = time.time()
    results = {}

    for mod_name in TEST_MODULES:
        passed = run_test_module(mod_name)
        results[mod_name] = passed

    duration = time.time() - start_time
    total = len(results)
    passed_count = sum(1 for p in results.values() if p)
    failed_count = total - passed_count

    print(f"\n{'='*70}")
    print("TEST EXECUTION SUMMARY")
    print(f"{'='*70}")
    for mod_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {mod_name}")

    print(f"\nTotal Suites: {total} | Passed: {passed_count} | Failed: {failed_count} | Elapsed: {duration:.2f}s")

    if failed_count > 0:
        print("\nTEST RUN FAILED!")
        return 1

    print("\nALL TEST SUITES PASSED SUCCESSFULLY (100%)!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
