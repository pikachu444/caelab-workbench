"""Installer manifest integrity + engine behavior (no network).

The manifest is the design contract of the install helper: versions,
official URLs, sizes and licenses notes were verified against the
manual installs on the dev machine. These tests pin the parts that
must not silently rot (a missing size, an unmarked Windows entry, a
manual entry that suddenly grows a URL).
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from caelab.solvers.installer.engine import (
    STATUS_ALREADY,
    STATUS_INSTALLED,
    STATUS_SKIPPED_MANUAL,
    Installer,
    load_manifest,
)

EXPECTED_IDS = {
    "freecad", "gmsh", "calculix", "paraview",
    "salome", "codeaster", "opencourant", "fenicsx",
}
LINUX_METHODS = {"archive", "appimage", "micromamba", "manual"}


def test_manifest_covers_all_programs():
    manifest = load_manifest()
    ids = {p["id"] for p in manifest["programs"]}
    assert ids == EXPECTED_IDS
    for prog in manifest["programs"]:
        assert prog["env_var"].startswith("CAELAB_")
        assert prog["version"]


def test_manifest_linux_entries_are_actionable():
    manifest = load_manifest()
    for prog in manifest["programs"]:
        entry = prog["platforms"]["linux"]
        assert entry["method"] in LINUX_METHODS, prog["id"]
        assert entry["executable"], prog["id"]
        if entry["method"] in ("archive", "appimage"):
            assert entry["url"].startswith("https://"), prog["id"]
            assert entry["size"] > 0, prog["id"]
        if entry["method"] == "micromamba":
            assert entry["specs"], prog["id"]
        if entry["method"] == "manual":
            # Manual entries must explain themselves and carry no URL
            # the engine could try to fetch behind the user's back.
            assert "url" not in entry, prog["id"]
            assert entry["manual_reason"], prog["id"]


def test_manifest_windows_entries_marked_unverified():
    manifest = load_manifest()
    for prog in manifest["programs"]:
        win = prog["platforms"]["windows"]
        assert win.get("status") == "unverified-windows", prog["id"]


def test_manual_entry_skips_with_reason(tmp_path):
    installer = Installer(tmp_path)
    results = installer.install(only=["codeaster"])
    assert len(results) == 1
    assert results[0].status == STATUS_SKIPPED_MANUAL
    assert "signed URL" in results[0].detail
    registry = json.loads((tmp_path / "caelab-install-registry.json").read_text())
    assert registry["programs"]["codeaster"]["status"] == STATUS_SKIPPED_MANUAL
    assert "CAELAB_CODEASTER" not in registry["env"]


def test_manual_entry_registers_placed_file(tmp_path):
    entry_file = tmp_path / "codeaster" / "salome_meca-lgpl-2025.1.0-1-20251026-scibian-12.sif"
    entry_file.parent.mkdir(parents=True)
    manifest = load_manifest()
    size = next(p for p in manifest["programs"] if p["id"] == "codeaster")["platforms"]["linux"]["size"]
    with open(entry_file, "wb") as fh:
        fh.truncate(size)  # sparse stand-in with the exact byte count
    installer = Installer(tmp_path)
    results = installer.install(only=["codeaster"])
    assert results[0].status == STATUS_INSTALLED
    wrapper = tmp_path / "codeaster" / "run_aster_sif.sh"
    assert wrapper.exists()
    assert "apptainer exec" in wrapper.read_text()
    registry = json.loads((tmp_path / "caelab-install-registry.json").read_text())
    assert registry["env"]["CAELAB_CODEASTER"] == str(wrapper)


def _tiny_manifest(archive: Path, size: int) -> dict:
    return {
        "schema": 1,
        "bootstrap": {"micromamba": {"linux": {
            "url": "https://example.invalid/mm.tar.bz2",
            "executable": "bin/micromamba"}}},
        "programs": [{
            "id": "tiny", "name": "Tiny", "version": "1.0",
            "env_var": "CAELAB_TINY", "role": "solver",
            "platforms": {"linux": {
                "method": "archive", "url": archive.as_uri(),
                "size": size, "target_dir": "tinydir",
                "executable": "tinydir/bin/tool"}},
        }],
    }


def test_archive_install_and_idempotency(tmp_path):
    tarball = tmp_path / "tiny.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        data = b"#!/bin/sh\necho tiny 1.0\n"
        info = tarfile.TarInfo("bin/tool")
        info.size = len(data)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(data))
    installer = Installer(tmp_path / "root", manifest=_tiny_manifest(tarball, tarball.stat().st_size))
    first = installer.install()
    assert first[0].status == STATUS_INSTALLED
    tool = tmp_path / "root" / "tinydir" / "bin" / "tool"
    assert tool.exists()
    registry = json.loads((tmp_path / "root" / "caelab-install-registry.json").read_text())
    assert registry["env"]["CAELAB_TINY"] == str(tool)
    second = Installer(tmp_path / "root", manifest=_tiny_manifest(tarball, tarball.stat().st_size)).install()
    assert second[0].status == STATUS_ALREADY
