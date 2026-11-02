"""Preserve native INP bytes; rewrite only unambiguous INCLUDE filename tokens."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath


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


def _rad_includes(data: bytes, path: Path, *, included=False):
    """Radioss INCLUDE names are relative to the solver cwd, never the parent deck."""
    lines = data.splitlines()
    for index, line in enumerate(lines):
        if included and re.match(br"\s*#enddata\b", line, re.I):
            break
        match = re.match(br"\s*#include\b(.*)$", line, re.I)
        if not match:
            continue
        token = match.group(1).strip()
        if not token:
            cursor = index + 1
            while cursor < len(lines) and (not lines[cursor].strip() or lines[cursor].lstrip().startswith(b"#")):
                if re.match(br"\s*#enddata\b", lines[cursor], re.I):
                    break
                cursor += 1
            token = lines[cursor].strip() if cursor < len(lines) else b""
        if len(line) > 500 or not token or any(c in token for c in (b" ", b"\t", b"'", b'"', b"\\", b"\x00")):
            raise ValueError(f"{path}:{index + 1}: 공백/따옴표/역슬래시 없는 상대 INCLUDE 경로가 필요합니다.")
        try:
            name = token.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{path}:{index + 1}: INCLUDE 경로가 UTF-8이 아닙니다.") from exc
        relative = PurePosixPath(name)
        if not relative.parts or relative.is_absolute() or ".." in relative.parts or ":" in name:
            raise ValueError(f"{path}:{index + 1}: 실행 폴더 안의 상대 INCLUDE만 지원합니다: {name}")
        if relative.parts[0].casefold() in {".source", "results", "job.json", "job.log", "starter.log", "engine.log", ".start-solver"}:
            raise ValueError(f"{path}:{index + 1}: 실행 기록과 충돌하는 INCLUDE입니다: {name}")
        yield name, index + 1


def snapshot_radioss_input(input_path: str | Path, run_dir: Path, check_cancelled=lambda: None) -> dict:
    """Snapshot a serial Starter/Engine run-1 bundle without rewriting any bytes.

    The initial execution profile is *_0000.rad + *_0001.rad and relative
    #include files. Restart continuation and arbitrary auxiliary input formats
    are not inferred. Every captured original participates in stale detection.
    """
    root = Path(input_path).resolve()
    match = re.fullmatch(r"([^\s/\\]+)_0000\.rad", root.name, re.I)
    if not match:
        raise ValueError("Radioss 입력은 <runname>_0000.rad Starter 파일이어야 합니다.")
    name = match.group(1)
    engine = root.with_name(name + "_0001.rad")
    base = root.parent
    files, calls = {}, []

    def visit(path, stack=(), included=False):
        check_cancelled()
        if len(stack) > 9 or path in stack:
            raise ValueError(f"INCLUDE 순환 또는 9단계 깊이 초과: {path}")
        if not path.is_relative_to(base):
            raise ValueError(f"INCLUDE가 입력 묶음 밖을 가리킵니다: {path}")
        if path in files:
            return
        before = fingerprint(path)
        if before is None or not path.is_file():
            raise ValueError(f"Starter/Engine/INCLUDE 파일이 없습니다: {path}")
        data = path.read_bytes()
        if fingerprint(path) != before:
            raise ValueError(f"수집 중 원본이 변경되었습니다: {path}")
        relative = path.relative_to(base).as_posix()
        # Case-fold collision is unsafe when the same bundle is moved to Windows.
        if any(item["execution_path"].casefold() == relative.casefold() for item in files.values()):
            raise ValueError(f"대소문자만 다른 입력 파일 경로입니다: {relative}")
        files[path] = {"data": data, "stat": before, "execution_path": relative,
                       "source_copy": f".source/{len(files):06d}.rad", "sha": hashlib.sha256(data).hexdigest()}
        for token, line in _rad_includes(data, path, included=included):
            unresolved = base / token
            target = unresolved.resolve()
            if target != unresolved.absolute():
                raise ValueError(f"INCLUDE symlink 별칭은 실행 사본에 보존할 수 없습니다: {token}")
            if target in stack + (path,):
                raise ValueError(f"순환 INCLUDE입니다: {target}")
            visit(target, (*stack, path), True)
            calls.append({"parent_path": str(path), "source_path": str(target), "line": line,
                          "execution_target": token, "depth": len(stack) + 1})

    visit(root)
    visit(engine)
    run_cards = re.findall(br"(?im)^\s*/RUN/([^/\r\n]+)/([0-9]+)\s*$", files[engine]["data"])
    if run_cards != [(name.encode("utf-8"), b"1")]:
        raise ValueError(f"Engine에는 /RUN/{name}/1 한 개가 필요합니다. Restart 연속 실행은 지원하지 않습니다.")
    manifest = []
    for path, item in files.items():
        check_cancelled()
        for relative in (item["source_copy"], item["execution_path"]):
            target = run_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item["data"])
        manifest.append({"source_path": str(path), "source_copy": item["source_copy"],
                         "execution_path": item["execution_path"], "stat": item["stat"],
                         "original_sha256": item["sha"], "execution_sha256": item["sha"]})
    for path, item in files.items():
        check_cancelled()
        if fingerprint(path) != item["stat"] or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha"]:
            raise ValueError(f"수집 중 원본이 변경되었습니다: {path}")
    return {"input_path": str(root), "solver_cwd": str(base), "input_revision": files[root]["sha"],
            "files": manifest, "includes": calls, "execution_input": root.name,
            "engine_input": engine.name, "run_name": name}
