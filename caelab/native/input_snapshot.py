"""Preserve native INP bytes; rewrite only unambiguous INCLUDE filename tokens."""

from __future__ import annotations

import hashlib
from pathlib import Path


def fingerprint(path: str | Path) -> dict | None:
    try:
        stat = Path(path).stat()
        return {key: getattr(stat, key) for key in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_ino", "st_dev")}
    except OSError:
        return None


def _includes(data: bytes, path: Path):
    offset = 0
    for number, line in enumerate(data.splitlines(keepends=True), 1):
        body = line.rstrip(b"\r\n")
        compact = body.replace(b" ", b"").replace(b"\t", b"").upper()
        keyword = compact.split(b",", 1)[0]
        if keyword.startswith(b"**") or not keyword.startswith(b"*"):
            offset += len(line)
            continue
        if keyword in {b"*IMPORT", b"*MAPSOLUTION"} or (keyword == b"*RESTART" and b"READ" in compact.split(b",")[1:]):
            raise ValueError(f"{path}:{number}: {keyword.decode()}의 외부/restart 파일 보존은 아직 지원하지 않습니다.")
        if keyword != b"*INCLUDE":
            if any(part.split(b"=", 1)[0] in {b"INPUT", b"FILE", b"LIBRARY"} for part in compact.split(b",")[1:]):
                raise ValueError(f"{path}:{number}: {keyword.decode()} 외부 파일 참조는 INCLUDE 수집으로 보존할 수 없습니다.")
            offset += len(line)
            continue
        equal = body.find(b"=")
        if equal < 0 or not body[equal + 1:]:
            raise ValueError(f"{path}:{number}: 한 물리 줄에 INCLUDE 파일명을 지정해야 합니다.")
        start, end = equal + 1, len(body)
        while start < end and body[start:start + 1] in (b" ", b"\t"):
            start += 1
        while end > start and body[end - 1:end] in (b" ", b"\t"):
            end -= 1
        raw = body[start:end]
        quoted = raw.startswith(b'"')
        if quoted:
            close = raw.find(b'"', 1)
            if close < 0 or raw[close + 1:].strip():
                raise ValueError(f"{path}:{number}: INCLUDE 따옴표 또는 줄 끝 표기가 모호합니다.")
            end = start + close
            start += 1
        forbidden = (b",", b'"', b"'") if quoted else (b" ", b"\t", b",", b'"', b"'")
        if any(char in body[start:end] for char in forbidden):
            raise ValueError(f"{path}:{number}: 공백/쉼표/따옴표가 있는 INCLUDE 경로를 추측하지 않습니다.")
        try:
            filename = body[start:end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{path}:{number}: INCLUDE 파일명은 UTF-8로 해석할 수 없습니다.") from exc
        if not filename or "\x00" in filename:
            raise ValueError(f"{path}:{number}: INCLUDE 파일명이 비어 있거나 잘못되었습니다.")
        yield offset + start, offset + end, filename, number
        offset += len(line)


def snapshot_input(input_path: str | Path, run_dir: Path, check_cancelled=lambda: None) -> dict:
    root = Path(input_path).resolve()
    solver_cwd = root.parent
    files: dict[Path, dict] = {}
    calls = []

    def visit(path, depth, stack):
        check_cancelled()
        if depth > 9:
            raise ValueError(f"{path}: INCLUDE 깊이가 9단계를 초과합니다.")
        if path in stack:
            raise ValueError(f"{path}: 순환 INCLUDE입니다.")
        if path not in files:
            before = fingerprint(path)
            if before is None or not path.is_file():
                raise ValueError(f"INCLUDE/입력 파일이 없습니다: {path}")
            data = path.read_bytes()
            if fingerprint(path) != before:
                raise ValueError(f"복사 중 원본 파일이 변경되었습니다: {path}")
            index = len(files)
            files[path] = {"data": data, "stat": before, "sha": hashlib.sha256(data).hexdigest(),
                           "source_copy": f"source/{index:06d}.inp",
                           "execution_path": "model.inp" if index == 0 else f"includes/{index:06d}.inp",
                           "includes": list(_includes(data, path))}
        item = files[path]
        for start, end, name, number in item["includes"]:
            target = Path(name)
            target = (solver_cwd / target).resolve() if not target.is_absolute() else target.resolve()
            visit(target, depth + 1, (*stack, path))
            calls.append({"parent_path": str(path), "source_path": str(target), "line": number,
                          "token_start": start, "token_end": end,
                          "original_token_hex": item["data"][start:end].hex(),
                          "execution_target": files[target]["execution_path"], "depth": depth + 1})

    visit(root, 0, ())
    manifest = []
    for path, item in files.items():
        check_cancelled()
        data = item["data"]
        rewritten = data
        for start, end, name, _ in reversed(item["includes"]):
            target = Path(name)
            target = (solver_cwd / target).resolve() if not target.is_absolute() else target.resolve()
            rewritten = rewritten[:start] + files[target]["execution_path"].encode("ascii") + rewritten[end:]
        for relative, content in ((item["source_copy"], data), (item["execution_path"], rewritten)):
            destination = run_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        manifest.append({"source_path": str(path), "source_copy": item["source_copy"],
                         "execution_path": item["execution_path"], "original_sha256": item["sha"],
                         "execution_sha256": hashlib.sha256(rewritten).hexdigest(), "stat": item["stat"]})
    # Recheck every original after the entire collection, not only each read.
    for path, item in files.items():
        check_cancelled()
        if fingerprint(path) != item["stat"] or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha"]:
            raise ValueError(f"수집 중 원본 파일이 변경되었습니다: {path}")
    return {"input_path": str(root), "solver_cwd": str(solver_cwd), "input_revision": files[root]["sha"],
            "files": manifest, "includes": calls, "execution_input": "model.inp"}
