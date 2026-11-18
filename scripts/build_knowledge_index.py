"""CLI for verifying offline CAE corpus manifests and building atomic hybrid indexes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from caelab.assist.corpus import build_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify CAE corpus sources/reviews and build an atomic cae-small-bm25-hash-v1 index.",
    )
    parser.add_argument("--manifest", required=True, help="Path to corpus manifest JSON.")
    parser.add_argument("--output", required=True, help="Output root directory for indexes/ and current.json.")
    parser.add_argument("--review-overlay", default=None, help="Optional review overlay JSON path.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--verify-only", action="store_true", help="Verify sources and reviews without writing an index.")
    mode_group.add_argument("--build", action="store_true", help="Verify and atomically build the corpus index (default).")

    args = parser.parse_args(argv)
    try:
        receipt = build_corpus(
            Path(args.manifest),
            output_root=Path(args.output),
            review_overlay_path=Path(args.review_overlay) if args.review_overlay else None,
            verify_only=bool(args.verify_only),
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
