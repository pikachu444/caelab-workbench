#!/usr/bin/env python3
"""Install repository Git hooks for automated documentation enforcement."""

from pathlib import Path
import os
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".git" / "hooks"

PRE_PUSH_SCRIPT = """#!/usr/bin/env sh
# Automated Documentation Enforcement Pre-Push Hook
echo "[Git Hook] Checking code-to-documentation synchronization..."
python scripts/check_doc_sync.py --mode pre-push
if [ $? -ne 0 ]; then
    echo "[Git Hook] PUSH REJECTED: Code modified without corresponding documentation updates!"
    exit 1
fi
echo "[Git Hook] Documentation synchronization verified."
exit 0
"""


def install_hooks() -> None:
    if not HOOKS_DIR.exists():
        print(f"[WARN] .git/hooks directory not found at {HOOKS_DIR}")
        return

    hook_file = HOOKS_DIR / "pre-push"
    hook_file.write_text(PRE_PUSH_SCRIPT, encoding="utf-8")
    try:
        hook_file.chmod(0o755)
    except Exception:
        pass
    print(f"[SUCCESS] Installed documentation enforcement pre-push hook at {hook_file}")


if __name__ == "__main__":
    install_hooks()
