"""Installed-home discovery and launch contracts; no solver execution claims."""
import json
import os
from pathlib import Path

import pytest

from caelab.connections import program_statuses, radioss_run_requests


def _installation(tmp_path, suffix=None):
    home = tmp_path / "solver installation"
    (home / "exec").mkdir(parents=True)
    suffix = suffix or ("win64.exe" if os.name == "nt" else "linux64_gf")
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
    monkeypatch.delenv("KMP_STACKSIZE", raising=False)
    monkeypatch.delenv("RAD_H3D_PATH", raising=False)
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
        if os.name == "nt":
            assert request.env["KMP_STACKSIZE"] == "400m"
            assert request.env["RAD_H3D_PATH"] == str(home / "extlib/h3d/lib/win64")
        else:
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


@pytest.mark.parametrize("explicit", ["home", "starter"])
@pytest.mark.parametrize("path_source", ["caller", "empty", "registry"])
def test_windows_stages_keep_packaged_libraries_and_effective_caller_environment(tmp_path, monkeypatch, explicit, path_source):
    import caelab.connections as connections

    home, (starter, _) = _installation(tmp_path, "win64.exe")
    monkeypatch.setattr(connections, "host_platform", lambda: "windows")
    monkeypatch.setenv("CAELAB_OPENCOURANT", str(home if explicit == "home" else starter))
    caller = str(tmp_path / "caller libraries")
    if path_source == "registry":
        monkeypatch.delenv("PATH", raising=False)
    else:
        monkeypatch.setenv("PATH", caller if path_source == "caller" else "")
    monkeypatch.setenv("KMP_STACKSIZE", "128m")
    monkeypatch.setenv("RAD_H3D_PATH", str(tmp_path / "caller h3d"))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"root": str(tmp_path), "runtime_env": {"opencourant": {
        "PATH": "registry-libraries", "KMP_STACKSIZE": "64m", "RAD_H3D_PATH": "registry-h3d",
    }}}))
    before = dict(os.environ)
    status = next(item for item in program_statuses(registry) if item.spec.id == "opencourant")
    assert status.available, status.reason
    effective_path = {"caller": [caller], "empty": [], "registry": [str(tmp_path / "registry-libraries")]}[path_source]
    for _, request in radioss_run_requests(status, tmp_path / "run", Path("case_0000.rad")):
        paths = request.env["PATH"].split(os.pathsep)
        assert paths == [str(home / "extlib/intelOneAPI_runtime/win64"),
                         str(home / "extlib/hm_reader/win64"), str(home / "extlib/h3d/lib/win64")] + effective_path
        assert request.env["KMP_STACKSIZE"] == "128m"
        assert request.env["RAD_H3D_PATH"] == str(tmp_path / "caller h3d")
    assert dict(os.environ) == before
