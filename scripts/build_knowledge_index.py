"""CLI for verifying offline CAE corpus manifests and building atomic hybrid indexes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from caelab.assist.corpus import build_corpus, load_corpus_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify CAE corpus sources/reviews and build an atomic cae-small-bm25-hash-v1 index.",
    )
    parser.add_argument("--manifest", help="Corpus manifest JSON. Omit with --verify-only to verify a published index.")
    parser.add_argument("--output", required=True, help="Output root directory for indexes/ and current.json.")
    parser.add_argument("--review-overlay", default=None, help="Optional review overlay JSON path.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--verify-only", action="store_true", help="Verify sources and reviews without writing an index.")
    mode_group.add_argument("--build", action="store_true", help="Verify and atomically build the corpus index (default).")

    vectors = parser.add_mutually_exclusive_group()
    vectors.add_argument("--with-vectors", dest="with_vectors", action="store_true", default=None,
                         help="Build hash vectors or require and verify them in a published index.")
    vectors.add_argument("--no-vectors", dest="with_vectors", action="store_false",
                         help="Build or verify a lexical-only index.")
    args = parser.parse_args(argv)
    if not args.manifest and (not args.verify_only or args.review_overlay):
        parser.error("--manifest is required for a build or a review overlay")
    try:
        if not args.manifest:
            index, metadata = load_corpus_bundle(args.output, with_vectors=args.with_vectors)
            print(json.dumps({
                "status": metadata["verification_status"], "index_digest": index.index_digest,
                "chunk_count": len(index.chunks), "source_statuses": metadata["source_statuses"],
            }, ensure_ascii=False, indent=2))
            return 0
        receipt = build_corpus(
            Path(args.manifest),
            output_root=Path(args.output),
            review_overlay_path=Path(args.review_overlay) if args.review_overlay else None,
            verify_only=args.verify_only,
            with_vectors=args.with_vectors is not False,
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
