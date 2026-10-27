#!/usr/bin/env python3
"""Git Diff & Documentation Synchronization Enforcement Script.

Enforces that whenever production code in `caelab/` or `apps/` is modified,
the corresponding documentation (`README.md`, `CURRENT_STATE.md`, or `docs/`)
must also be updated in the same commit / branch diff.

Can be run:
- In Git pre-push hook: python scripts/check_doc_sync.py --pre-push
- In Git pre-commit hook: python scripts/check_doc_sync.py --pre-commit
- In CI workflows: python scripts/check_doc_sync.py --ci
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def get_changed_files(mode: str) -> list[str]:
    """Get list of changed files based on enforcement mode.
    
    Fails closed: if git fails to determine diff, raises RuntimeError rather than silently passing.
    """
    if mode == "pre-commit":
        cmd = ["git", "diff", "--cached", "--name-only"]
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Git pre-commit diff failed (code {res.returncode}): {res.stderr.strip()}")
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]

    # For pre-push and CI, try comparing against upstream base branch
    candidate_refs = [
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        ["git", "diff", "--name-only", "main...HEAD"],
        ["git", "diff", "--name-only", "HEAD~1...HEAD"],
    ]

    last_error = ""
    for cmd in candidate_refs:
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if res.returncode == 0:
            files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            # If we got a valid non-empty diff, return it
            if files:
                return files
            # If empty diff against base branch, still return empty (branch is up to date)
            return []
        last_error = res.stderr.strip()

    raise RuntimeError(f"Unable to determine git diff against any base ref: {last_error}")


def check_doc_synchronization(changed_files: list[str]) -> bool:
    """Verify that code modifications are paired with documentation updates."""
    if not changed_files:
        return True

    code_prefixes = ("caelab/", "apps/")
    doc_markers = ("README.md", "CURRENT_STATE.md", "docs/")

    code_changes = [f for f in changed_files if any(f.startswith(p) for p in code_prefixes)]
    doc_changes = [f for f in changed_files if any(f.startswith(m) or f == m for m in doc_markers)]

    if code_changes and not doc_changes:
        print("\n" + "=" * 70)
        print(" [DOC SYNC ERROR] Code modified without documentation update!")
        print("=" * 70)
        print("The following code files were modified:")
        for f in code_changes[:10]:
            print(f"  - {f}")
        if len(code_changes) > 10:
            print(f"  ... and {len(code_changes) - 10} more.")
        print("\nRequired action:")
        print("  Whenever production code changes, you MUST update at least one of:")
        print("  - README.md")
        print("  - CURRENT_STATE.md")
        print("  - docs/ (spec, tutorial, or benchmark)")
        print("=" * 70 + "\n")
        return False

    print(f"[DOC SYNC OK] Code changes ({len(code_changes)}) paired with doc changes ({len(doc_changes)}).")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce code-doc synchronization.")
    parser.add_argument("--mode", choices=["pre-commit", "pre-push", "ci"], default="ci")
    args = parser.parse_args()

    try:
        changed = get_changed_files(args.mode)
    except Exception as e:
        print(f"\n[DOC SYNC FATAL] Git verification failed: {e}\n", file=sys.stderr)
        return 1

    if not check_doc_synchronization(changed):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
