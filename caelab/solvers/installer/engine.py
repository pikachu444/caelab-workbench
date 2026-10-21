"""Install helper for the CAE Lab workbench (001 solver-install chat).

Reads ``manifest.json`` (same directory) and performs, per program:
download -> unpack/place -> register -> verify. Registration writes
``<root>/caelab-install-registry.json`` (machine readable: CAELAB_*
paths plus per-program runtime env) and ``<root>/caelab-env.sh``.

Design rules honored here:
- Programs whose download is gated behind a short-lived signed URL
  (Code_Aster .sif) are ``manual`` entries: the engine never tries to
  fetch them; it registers a file the user placed by hand, or reports
  the entry as skipped with the reason.
- Windows entries are data only in this engine run (status
  ``unverified-windows``); they are listed, never executed here.
- Third-party test assets (OpenCourant QA decks, CC BY-NC) are not
  part of any entry and are never downloaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
REGISTRY_NAME = "caelab-install-registry.json"
ENV_SCRIPT_NAME = "caelab-env.sh"

STATUS_INSTALLED = "installed"
STATUS_ALREADY = "already-installed"
STATUS_SKIPPED_MANUAL = "skipped-manual"
STATUS_UNVERIFIED_WINDOWS = "unverified-windows"
STATUS_FAILED = "failed"


def load_manifest(path: Path | None = None) -> dict:
    with open(path or MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class ProgramResult:
    program_id: str
    status: str
    detail: str = ""
    path: str = ""
    warnings: list[str] = field(default_factory=list)


class InstallError(RuntimeError):
    pass


def _fmt_bytes(n: int) -> str:
    return f"{n:,} B"


class Installer:
    def __init__(
        self,
        root: Path,
        manifest: dict | None = None,
        platform: str = "linux",
        log=print,
    ):
        self.root = Path(root).expanduser()
        self.manifest = manifest if manifest is not None else load_manifest()
        self.platform = platform
        self.log = log
        self.cache = self.root / ".cache" / "downloads"
        self.registry: dict = {
            "schema": 1,
            "root": str(self.root),
            "env": {},
            "runtime_env": {},
            "programs": {},
        }

    # ------------------------------------------------------------ planning
    def programs(self) -> list[dict]:
        return self.manifest["programs"]

    def entry(self, prog: dict) -> dict | None:
        return prog.get("platforms", {}).get(self.platform)

    # ------------------------------------------------------------ download
    def _request(self, url: str, headers: dict | None = None, byte_range: str | None = None):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "caelab-installer/1.0")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if byte_range:
            req.add_header("Range", f"bytes={byte_range}")
        return req

    def download(self, url: str, size: int | None, sha256: str | None,
                 headers: dict | None = None) -> Path:
        """Download to the cache with parallel ranges when possible.

        Part files persist in the cache, so re-running resumes instead
        of restarting. Size (and sha256 when the manifest has one) are
        verified before the file is handed to the unpack step.
        """
        self.cache.mkdir(parents=True, exist_ok=True)
        name = url.rsplit("/", 1)[-1].split("?")[0]
        dest = self.cache / name
        if dest.exists() and size and dest.stat().st_size == size:
            self._check_sha(dest, sha256)
            return dest
        total = size
        ranged = False
        if total:
            try:
                with urllib.request.urlopen(self._request(url, headers, "0-0"), timeout=60) as resp:
                    ranged = resp.status == 206
                    crange = resp.headers.get("Content-Range", "")
                    if "/" in crange:
                        total = int(crange.rsplit("/", 1)[-1])
            except Exception:
                ranged = False
        if total and ranged and total > 8 * 1024 * 1024:
            self._download_ranged(url, total, headers, dest)
        else:
            self._download_stream(url, headers, dest)
        if total and dest.stat().st_size != total:
            raise InstallError(
                f"size mismatch for {name}: got {_fmt_bytes(dest.stat().st_size)}, "
                f"expected {_fmt_bytes(total)}")
        self._check_sha(dest, sha256)
        return dest

    def _check_sha(self, path: Path, sha256: str | None) -> None:
        if not sha256:
            return
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != sha256:
            raise InstallError(f"sha256 mismatch for {path.name}")

    def _download_stream(self, url: str, headers: dict | None, dest: Path) -> None:
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(self._request(url, headers), timeout=120) as resp, \
                open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh, 1 << 20)
        tmp.replace(dest)

    def _download_ranged(self, url: str, total: int, headers: dict | None, dest: Path) -> None:
        parts_dir = self.cache / (dest.name + ".parts")
        parts_dir.mkdir(exist_ok=True)
        n = 8
        chunk = (total + n - 1) // n

        def fetch(i: int) -> None:
            start = i * chunk
            end = min(start + chunk - 1, total - 1)
            want = end - start + 1
            part = parts_dir / f"part{i:02d}"
            if part.exists() and part.stat().st_size == want:
                return
            with urllib.request.urlopen(
                    self._request(url, headers, f"{start}-{end}"), timeout=300) as resp, \
                    open(part, "wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
            if part.stat().st_size != want:
                raise InstallError(f"range part {i} size mismatch")

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(fetch, range(n)))
        with open(dest, "wb") as out:
            for i in range(n):
                with open(parts_dir / f"part{i:02d}", "rb") as fh:
                    shutil.copyfileobj(fh, out, 1 << 20)
        shutil.rmtree(parts_dir, ignore_errors=True)

    # ------------------------------------------------------------ unpack
    def _unpack(self, archive: Path, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(target_dir)
        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tf:
                tf.extractall(target_dir, filter="data")
        else:
            raise InstallError(f"unknown archive type: {archive.name}")

    def _ensure_micromamba(self) -> Path:
        exe = self.root / self.manifest["bootstrap"]["micromamba"]["linux"]["executable"]
        if exe.exists():
            return exe
        boot = self.manifest["bootstrap"]["micromamba"]["linux"]
        tarball = self.download(boot["url"], None, None)
        staging = self.root / ".cache" / "micromamba-extract"
        staging.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball) as tf:
            tf.extractall(staging, filter="data")
        found = staging / "bin" / "micromamba"
        exe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found, exe)
        exe.chmod(0o755)
        return exe

    # ------------------------------------------------------------ post steps
    def _post_salome_psutil(self, prog_root: Path) -> list[str]:
        """Copy psutil into the Salome KERNEL script dir.

        The salome launcher resets PYTHONPATH, so a normally installed
        psutil is invisible to it; the copy inside the tree is the
        measured workaround from the dev machine.
        """
        warnings: list[str] = []
        kernel_bins = list(prog_root.glob("BINARIES-*/KERNEL/bin/salome"))
        if not kernel_bins:
            return ["salome_psutil: KERNEL/bin/salome not found, skipped"]
        staging = self.root / ".cache" / "psutil-pkg"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(staging), "psutil"],
            check=True)
        for pkg in staging.iterdir():
            dest = kernel_bins[0] / pkg.name
            if pkg.is_dir():
                shutil.copytree(pkg, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(pkg, dest)
        return warnings

    def _post_codeaster_wrapper(self, prog_root: Path, entry: dict) -> list[str]:
        """Write the run_aster wrapper that enters the .sif via Apptainer."""
        sif = prog_root / entry["file"]
        inside = entry.get("aster_inside", "run_aster")
        wrapper = prog_root / "run_aster_sif.sh"
        wrapper.write_text(
            "#!/bin/sh\n"
            "# Generated by caelab installer: run Code_Aster inside the\n"
            "# SALOME-MECA .sif with Apptainer. Extra args are passed on.\n"
            f'exec apptainer exec "{sif}" {inside} "$@"\n')
        wrapper.chmod(0o755)
        return []

    # ------------------------------------------------------------ per-method
    def _install_archive(self, prog: dict, entry: dict) -> ProgramResult:
        target = self.root / entry["target_dir"]
        archive = self.download(entry["url"], entry.get("size"),
                                entry.get("sha256"), entry.get("headers"))
        self._unpack(archive, target)
        return ProgramResult(prog["id"], STATUS_INSTALLED)

    def _install_appimage(self, prog: dict, entry: dict) -> ProgramResult:
        target = self.root / entry["target_dir"]
        target.mkdir(parents=True, exist_ok=True)
        image = self.download(entry["url"], entry.get("size"), entry.get("sha256"),
                              entry.get("headers"))
        local = target / image.name
        if not local.exists():
            shutil.copy2(image, local)
        local.chmod(0o755)
        proc = subprocess.run([str(local), "--appimage-extract"],
                              cwd=target, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            raise InstallError(f"AppImage extract failed: {proc.stderr[-500:]}")
        return ProgramResult(prog["id"], STATUS_INSTALLED)

    def _install_micromamba(self, prog: dict, entry: dict) -> ProgramResult:
        mm = self._ensure_micromamba()
        prefix = self.root / entry["target_dir"]
        env = dict(os.environ, MAMBA_ROOT_PREFIX=str(self.root / "mamba-root"))
        proc = subprocess.run(
            [str(mm), "create", "-y", "-p", str(prefix),
             "-c", entry.get("channel", "conda-forge"), *entry["specs"]],
            env=env, capture_output=True, text=True, timeout=7200)
        if proc.returncode != 0:
            raise InstallError(f"micromamba create failed: {proc.stderr[-800:]}")
        return ProgramResult(prog["id"], STATUS_INSTALLED)

    def _install_manual(self, prog: dict, entry: dict) -> ProgramResult:
        target = self.root / entry["target_dir"]
        placed = target / entry["file"]
        if not placed.exists():
            return ProgramResult(
                prog["id"], STATUS_SKIPPED_MANUAL,
                entry.get("manual_reason", "manual download required"))
        if entry.get("size") and placed.stat().st_size != entry["size"]:
            return ProgramResult(
                prog["id"], STATUS_FAILED,
                f"placed file size mismatch: {_fmt_bytes(placed.stat().st_size)} "
                f"!= {_fmt_bytes(entry['size'])}")
        result = ProgramResult(prog["id"], STATUS_INSTALLED,
                               "manually placed file registered")
        for step in entry.get("post_steps", []):
            result.warnings += self._run_post_step(step, target, entry)
        return result

    def _run_post_step(self, step: str, target: Path, entry: dict) -> list[str]:
        if step == "salome_psutil":
            return self._post_salome_psutil(target)
        if step == "codeaster_wrapper":
            return self._post_codeaster_wrapper(target, entry)
        return [f"unknown post step {step}"]

    def _apt_prereqs(self, prog: dict, entry: dict, system_deps: bool) -> list[str]:
        packages = entry.get("apt_packages")
        if not packages:
            return []
        if not system_deps:
            return [f"{prog['id']}: {len(packages)} apt packages listed in the "
                    f"manifest; rerun with --system-deps to install them"]
        warnings: list[str] = []
        for pkg in packages:
            proc = subprocess.run(
                ["apt-get", "install", "-y", "--no-install-recommends", pkg],
                capture_output=True, text=True,
                env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
            if proc.returncode != 0:
                warnings.append(f"apt package failed (non-fatal): {pkg}")
        return warnings

    # ------------------------------------------------------------ main flow
    def install_one(self, prog: dict, system_deps: bool = False) -> ProgramResult:
        pid = prog["id"]
        entry = self.entry(prog)
        if entry is None:
            return ProgramResult(pid, STATUS_FAILED, f"no {self.platform} entry")
        if self.platform == "windows":
            return ProgramResult(pid, STATUS_UNVERIFIED_WINDOWS,
                                 entry.get("note", "Windows entry listed only"))
        exe_rel = entry.get("executable")
        exe_path = self.root / exe_rel if exe_rel else None
        if exe_path is not None and exe_path.exists() and entry.get("method") != "manual":
            result = ProgramResult(pid, STATUS_ALREADY)
        else:
            method = entry.get("method")
            try:
                if method == "archive":
                    result = self._install_archive(prog, entry)
                elif method == "appimage":
                    result = self._install_appimage(prog, entry)
                elif method == "micromamba":
                    result = self._install_micromamba(prog, entry)
                elif method == "manual":
                    result = self._install_manual(prog, entry)
                else:
                    result = ProgramResult(pid, STATUS_FAILED, f"unsupported method {method}")
            except Exception as exc:  # keep other programs going
                result = ProgramResult(pid, STATUS_FAILED, str(exc))
            if result.status == STATUS_INSTALLED and method != "manual":
                for step in entry.get("post_steps", []):
                    target = self.root / entry["target_dir"]
                    result.warnings += self._run_post_step(step, target, entry)
        result.warnings += self._apt_prereqs(prog, entry, system_deps)
        if exe_path is not None:
            result.path = str(exe_path)
        if result.status in (STATUS_INSTALLED, STATUS_ALREADY):
            self._register(prog, entry, result)
        else:
            self.registry["programs"][pid] = {
                "version": prog.get("version"), "status": result.status,
                "detail": result.detail}
        return result

    def install(self, only: list[str] | None = None,
                system_deps: bool = False) -> list[ProgramResult]:
        self.root.mkdir(parents=True, exist_ok=True)
        results: list[ProgramResult] = []
        for prog in self.programs():
            if only and prog["id"] not in only:
                continue
            self.log(f"[{prog['id']}] installing...")
            result = self.install_one(prog, system_deps=system_deps)
            results.append(result)
            self.log(f"[{prog['id']}] {result.status} {result.detail}")
            for warning in result.warnings:
                self.log(f"[{prog['id']}] warning: {warning}")
        self._write_registry()
        return results

    # ------------------------------------------------------------ registry
    def _register(self, prog: dict, entry: dict, result: ProgramResult) -> None:
        env_var = prog.get("env_var")
        if env_var and result.path:
            self.registry["env"][env_var] = result.path
        runtime = entry.get("runtime_env")
        if runtime:
            target = str(self.root / entry["target_dir"])
            resolved = {}
            for key, value in runtime.items():
                resolved[key] = (value.replace("{root}", str(self.root))
                                 .replace("{target}", target))
            self.registry["runtime_env"][prog["id"]] = resolved
        self.registry["programs"][prog["id"]] = {
            "version": prog.get("version"), "status": result.status,
            "path": result.path, "detail": result.detail}

    def _write_registry(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / REGISTRY_NAME, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh, indent=2, ensure_ascii=False)
        lines = ["# Generated by caelab installer; source this to use the",
                 "# workbench with the programs under this root."]
        for var, path in sorted(self.registry["env"].items()):
            lines.append(f'export {var}="{path}"')
        for pid, envs in sorted(self.registry["runtime_env"].items()):
            for key, value in sorted(envs.items()):
                lines.append(f'export {key}="{value}"  # {pid}')
        (self.root / ENV_SCRIPT_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ verify
    def verify(self) -> list[tuple[str, bool, str]]:
        """Best-effort version probes for already-registered programs."""
        registry_path = self.root / REGISTRY_NAME
        registered: dict = {}
        if registry_path.exists():
            registered = json.loads(registry_path.read_text(encoding="utf-8"))
        probes: dict[str, list[str]] = {
            "freecad": ["{exe}", "--version"],
            "gmsh": ["{exe}", "--version"],
            "paraview": ["{exe}", "--version"],
            "fenicsx": ["{exe}", "-c", "import dolfinx; print(dolfinx.__version__)"],
        }
        out: list[tuple[str, bool, str]] = []
        envs = registered.get("env", {})
        by_var = {p.get("env_var"): p["id"] for p in self.programs()}
        for var, exe in sorted(envs.items()):
            pid = by_var.get(var, var)
            argv = probes.get(pid)
            if argv is None:
                out.append((pid, Path(exe).exists(), "presence check only"))
                continue
            argv = [exe if a == "{exe}" else a for a in argv]
            env = dict(os.environ)
            for key, value in registered.get("runtime_env", {}).get(pid, {}).items():
                env[key] = value + (":" + env[key] if key in env and env[key] else "")
            try:
                proc = subprocess.run(argv, capture_output=True, text=True,
                                      timeout=120, env=env)
                text = (proc.stdout + proc.stderr).strip().splitlines()
                out.append((pid, proc.returncode == 0, text[0] if text else ""))
            except Exception as exc:
                out.append((pid, False, str(exc)))
        return out
