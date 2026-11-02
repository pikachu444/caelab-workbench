"""Installed-home discovery and launch contracts; no solver execution claims."""
import json
import os
from pathlib import Path

import pytest

from caelab.connections import program_statuses, radioss_run_requests


def _installation(tmp_path):
    home = tmp_path / "solver installation"
    (home / "exec").mkdir(parents=True)
    suffix = "win64.exe" if os.name == "nt" else "linux64_gf"
    paths = [home / "exec" / f"{stage}_{suffix}" for stage in ("starter", "engine")]
    for path in paths:
        path.write_bytes(b"installation probe, never executed")
        path.chmod(0o755)
    return home, paths


@pytest.mark.parametrize("explicit", ["home", "starter"])
def test_installed_home_and_explicit_starter_resolve_two_executables(tmp_path, monkeypatch, explicit):
    home, (starter, engine) = _installation(tmp_path)
    monkeypatch.setenv("CAELAB_OPENCOURANT", str(home if explicit == "home" else starter))
    monkeypatch.setenv("LD_LIBRARY_PATH", "/caller/library")
    status = next(item for item in program_statuses(tmp_path / "missing-registry") if item.spec.id == "opencourant")
    assert status.available, status.reason
    assert status.path == str(starter)
    requests = radioss_run_requests(status, tmp_path / "run folder", Path("independent_0000.rad"))
    assert [name for name, _ in requests] == ["starter", "engine"]
    assert requests[0][1].argv == [str(starter), "-i", "independent_0000.rad", "-np", "1"]
    assert requests[1][1].argv == [str(engine), "-i", "independent_0001.rad"]
    for _, request in requests:
        assert request.cwd == tmp_path / "run folder"
        assert request.env["RAD_CFG_PATH"] == str(home / "hm_cfg_files")
        if os.name != "nt":
            assert str(home / "extlib/hm_reader/linux64") in request.env["LD_LIBRARY_PATH"].split(os.pathsep)
            assert "/caller/library" in request.env["LD_LIBRARY_PATH"].split(os.pathsep)


def test_missing_engine_is_unavailable_before_any_process_launch(tmp_path, monkeypatch):
    home, (_, engine) = _installation(tmp_path)
    engine.unlink()
    monkeypatch.setenv("CAELAB_OPENCOURANT", str(home))
    status = next(item for item in program_statuses() if item.spec.id == "opencourant")
    assert not status.available
    assert engine.name in status.reason


def test_registered_home_does_not_depend_on_current_directory(tmp_path, monkeypatch):
    home, (starter, _) = _installation(tmp_path)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"root": str(tmp_path), "env": {"CAELAB_OPENCOURANT": str(home)}}))
    monkeypatch.delenv("CAELAB_OPENCOURANT", raising=False)
    elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    status = next(item for item in program_statuses(registry) if item.spec.id == "opencourant")
    assert status.available and status.source == "registry"
    assert status.path == str(starter)
