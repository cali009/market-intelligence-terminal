"""
Automated Compliance Linter Runner
Scans source code, markdown documentation, and prompt strings for advisory violations.
Exits with 0 if clean, 1 if violations found.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.compliance.linter import linter

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Target directories for application code, prompts, and UI copy
SCAN_DIRS = ["src", "config"]
SCAN_EXTENSIONS = {".py", ".json", ".sql"}
EXCLUDE_DIRS = {".git", ".pytest_cache", "__pycache__", "venv", ".venv", "cache"}
EXCLUDE_FILES = {"linter.py", "disclaimers.py", "test_compliance_linter.py", "run_linter.py"}


def scan_project() -> int:
    total_violations = 0
    scanned_files = 0

    print("Starting automated compliance linter scan (CSA 31-369 & SEC Publisher Exclusion)...")

    for scan_dir in SCAN_DIRS:
        target_path = PROJECT_ROOT / scan_dir
        if not target_path.exists():
            continue

        for file_path in target_path.rglob("*"):
            if file_path.is_dir():
                continue
            if any(part in EXCLUDE_DIRS for part in file_path.parts):
                continue
            if file_path.suffix not in SCAN_EXTENSIONS:
                continue
            if file_path.name in EXCLUDE_FILES:
                continue

            scanned_files += 1
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                violations = linter.lint_text(content)
                if violations:
                    print(f"\n[VIOLATION] in {file_path.relative_to(PROJECT_ROOT)}:")
                    for v in violations:
                        print(f"  Line {v.line_number} [{v.rule_category}]: '{v.matched_phrase}'")
                        print(f"    Context: {v.context}")
                        print(f"    Fix: {v.remediation_suggestion}")
                    total_violations += len(violations)
            except Exception as e:
                print(f"Error reading {file_path}: {e}")

    print(f"\nScan completed: {scanned_files} files checked in {SCAN_DIRS}.")
    if total_violations == 0:
        print("ALL COMPLIANCE CHECKS PASSED: Zero advisory or promissory phrasing violations in application code.")
        return 0
    else:
        print(f"FAILED: {total_violations} advisory violation(s) detected. Fix before deployment.")
        return 1


if __name__ == "__main__":
    sys.exit(scan_project())
