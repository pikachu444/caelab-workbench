"""One-off black-box GUI trial of the publicly distributed BeidPrep 1.0.1.

No credentials are supplied. The application is not modified or decompiled.
Screenshots, accessibility observations, and real saved decks establish scope;
an attempted action alone is never reported as a successful round trip.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
from html.parser import HTMLParser


EXPECTED_SHA256 = "fcaad661098759221a248cef8141e205f7654b45b88f8357477041329cc75128"
FILE_ID = "1XaGr7w5ydlU__garVY_nQXYsK0EUNJj3"
OUT = Path("evaluation-output").resolve()
RESULT: dict = {"application": "BeidPrep 1.0.1", "credentials_entered": False, "steps": {}}


class DownloadForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "download-form":
            self.action = attrs.get("action")
        elif tag == "input" and attrs.get("type") == "hidden":
            self.fields[attrs["name"]] = attrs.get("value", "")


def write_result():
    (OUT / "result.json").write_text(json.dumps(RESULT, indent=2, ensure_ascii=False), encoding="utf8")


def log(message):
    print(message, flush=True)
    with (OUT / "trial.log").open("a", encoding="utf8") as stream:
        stream.write(str(message) + "\n")


def download(executable):
    import requests

    log("Starting public BeidPrep executable download")
    session = requests.Session()
    url = "https://drive.usercontent.google.com/download"
    params = {"id": FILE_ID, "export": "download", "authuser": "0"}
    response = session.get(url, params=params, stream=True, timeout=(20, 60))
    response.raise_for_status()
    if "text/html" in response.headers.get("Content-Type", ""):
        parser = DownloadForm()
        parser.feed(response.text)
        if parser.action != url or parser.fields.get("id") != FILE_ID:
            raise RuntimeError("Public download did not return the expected confirmation form")
        response = session.get(url, params=parser.fields, stream=True, timeout=(20, 60))
        response.raise_for_status()
    if "text/html" in response.headers.get("Content-Type", ""):
        raise RuntimeError("Public download returned HTML instead of the application")
    digest = hashlib.sha256()
    size = 0
    next_progress = 25 * 1024 * 1024
    with executable.open("wb") as stream:
        for chunk in response.iter_content(1024 * 1024):
            stream.write(chunk)
            digest.update(chunk)
            size += len(chunk)
            if size >= next_progress:
                log(f"Executable download progress: {size // (1024 * 1024)} MiB")
                next_progress += 25 * 1024 * 1024
    if digest.hexdigest() != EXPECTED_SHA256:
        raise RuntimeError("Executable SHA-256 differs from the already acquired public build")
    RESULT["steps"]["download"] = {"status": "passed", "bytes": size, "sha256": digest.hexdigest()}
    write_result()
    log(f"Downloaded and verified {size} bytes")


def snapshot(name, window=None):
    from PIL import ImageGrab
    from pywinauto import Desktop

    log(f"Capturing UI observation: {name}")
    for capture_name, capture in [
        (f"{name}-desktop.png", lambda: ImageGrab.grab(all_screens=True)),
        (f"{name}-window.png", lambda: window.capture_as_image() if window else None),
    ]:
        try:
            image = capture()
            if image is not None:
                image.save(OUT / capture_name)
        except Exception as exc:
            log(f"Screenshot {capture_name}: {exc}")

    observations = {}
    for backend in ("win32", "uia"):
        try:
            windows = Desktop(backend=backend).windows(visible_only=True)
            observations[backend] = []
            for candidate in windows:
                entry = {"title": candidate.window_text(), "class": candidate.class_name(),
                         "pid": candidate.process_id(), "handle": candidate.handle,
                         "rectangle": str(candidate.rectangle())}
                if window and candidate.handle == window.handle:
                    controls = []
                    try:
                        for element in candidate.descendants()[:1000]:
                            item = {"name": element.window_text(), "class": element.class_name(),
                                    "type": element.element_info.control_type if backend == "uia" else "",
                                    "rectangle": str(element.rectangle())}
                            for method in ("get_value", "texts"):
                                with contextlib.suppress(Exception):
                                    item[method] = getattr(element, method)()
                            controls.append(item)
                    except Exception as exc:
                        entry["descendant_error"] = str(exc)
                    entry["controls"] = controls
                observations[backend].append(entry)
        except Exception as exc:
            observations[backend] = {"error": str(exc)}
    (OUT / f"{name}-controls.json").write_text(json.dumps(observations, indent=2, ensure_ascii=False), encoding="utf8")
    return observations


def find_main(timeout=90):
    from pywinauto import Desktop

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = []
        for window in Desktop(backend="win32").windows(visible_only=True):
            with contextlib.suppress(Exception):
                rect = window.rectangle()
                if "beid" in window.window_text().lower() and rect.width() > 500 and rect.height() > 300:
                    matches.append(window)
        if matches:
            return max(matches, key=lambda item: item.rectangle().width() * item.rectangle().height())
        time.sleep(1)
    raise RuntimeError("No visible BeidPrep main window appeared within 90 seconds")


def ui_window(main):
    from pywinauto import Desktop
    return Desktop(backend="uia").window(handle=main.handle).wrapper_object()


def expand_tree(main):
    for _ in range(3):
        changed = False
        for control in ui_window(main).descendants(control_type="TreeItem")[:100]:
            with contextlib.suppress(Exception):
                if control.get_expand_state() == 0:
                    control.expand()
                    changed = True
        if not changed:
            break
        time.sleep(0.5)


def open_file(main, filename):
    from pywinauto import Desktop, keyboard

    main.set_focus()
    main.type_keys("^o", set_foreground=True)
    deadline = time.monotonic() + 12
    dialog = None
    while time.monotonic() < deadline:
        for candidate in Desktop(backend="win32").windows(visible_only=True):
            title = candidate.window_text().lower()
            if candidate.handle != main.handle and ("open" in title or "select" in title):
                dialog = candidate
                break
        if dialog:
            break
        time.sleep(0.5)
    if not dialog:
        raise RuntimeError("Ctrl+O did not expose a file-open dialog")
    dialog.set_focus()
    keyboard.send_keys("%n^a")
    keyboard.send_keys(str(filename), with_spaces=True, pause=0.01)
    keyboard.send_keys("{ENTER}")
    time.sleep(5)
    expand_tree(main)


def find_control_value(control):
    for method in ("get_value", "window_text"):
        with contextlib.suppress(Exception):
            value = str(getattr(control, method)()).strip()
            if value:
                return value
    return ""


def edit_material(main):
    """Edit only an unambiguous visible 210000 field after selecting STEEL."""
    from pywinauto import keyboard

    candidates = [item for item in ui_window(main).descendants(control_type="TreeItem")
                  if "STEEL" in item.window_text().upper() or "PLAS_TAB" in item.window_text().upper()
                  or "LAW36" in item.window_text().upper()]
    if not candidates:
        return {"status": "not_verified", "reason": "Material tree item not exposed through accessibility"}
    candidates[0].double_click_input()
    time.sleep(1)
    snapshot("03-material-selected", main)
    controls = ui_window(main).descendants()
    matched = []
    for control in controls:
        with contextlib.suppress(Exception):
            value = find_control_value(control)
            if control.element_info.control_type == "Edit" and float(value) == 210000.0:
                matched.append(control)
    if not matched:
        for control in controls:
            with contextlib.suppress(Exception):
                if control.element_info.control_type == "Spinner" and float(find_control_value(control)) == 210000.0:
                    matched.append(control)
    if len(matched) != 1:
        return {"status": "not_verified", "reason": f"Unambiguous elastic modulus edit field count: {len(matched)}"}
    target = matched[0]
    try:
        target.set_edit_text("205000")
    except Exception:
        target.set_focus()
        keyboard.send_keys("^a205000{TAB}")
    time.sleep(0.5)
    saves = [button for button in ui_window(main).descendants(control_type="Button")
             if button.window_text().strip().lower() in ("save", "apply", "update", "save changes")]
    if len(saves) == 1:
        try:
            saves[0].invoke()
        except Exception:
            saves[0].click_input()
        time.sleep(1)
    snapshot("04-material-edited", main)
    return {"status": "attempted", "old_E": 210000, "new_E": 205000,
            "verified_only_if_saved_deck_matches": True}


def save_as(main, filename):
    from pywinauto import Desktop, keyboard

    main.set_focus()
    main.type_keys("^+s", set_foreground=True)
    time.sleep(1)
    dialogs = [window for window in Desktop(backend="win32").windows(visible_only=True)
               if window.handle != main.handle and "save" in window.window_text().lower()]
    if not dialogs:
        # v1.0 may use Save rather than Save As; the opened input is a disposable copy.
        main.type_keys("^s", set_foreground=True)
        time.sleep(1)
        dialogs = [window for window in Desktop(backend="win32").windows(visible_only=True)
                   if window.handle != main.handle and "save" in window.window_text().lower()]
    if dialogs:
        dialogs[0].set_focus()
        keyboard.send_keys("%n^a")
        keyboard.send_keys(str(filename), with_spaces=True, pause=0.01)
        keyboard.send_keys("{ENTER}")
        time.sleep(2)
    return filename.exists()


def inspect_deck(filename):
    text = filename.read_text(encoding="utf8", errors="replace")
    cards = [line.strip() for line in text.splitlines() if line.startswith("/")]
    material = re.search(r"/MAT/(?:PLAS_TAB|LAW36)/1\b(.*?)(?=^/|\Z)", text, re.M | re.S)
    return {"bytes": filename.stat().st_size, "cards": cards,
            "has_nodes": "/NODE" in cards, "shell_blocks": sum(card.startswith("/SHELL/") for card in cards),
            "has_material": material is not None,
            "material_contains_new_E": bool(material and re.search(r"\b205000(?:\.0*)?\b", material.group(1))),
            "contains_contact": any(card.startswith("/INTER/TYPE24/") for card in cards)}


def read_reopened_material(main):
    """Read the material editor in a fresh process; do not change its contents."""
    candidates = [item for item in ui_window(main).descendants(control_type="TreeItem")
                  if "STEEL" in item.window_text().upper() or "PLAS_TAB" in item.window_text().upper()
                  or "LAW36" in item.window_text().upper()]
    if not candidates:
        return {"status": "not_verified_by_values", "reason": "Material item not exposed"}
    candidates[0].double_click_input()
    time.sleep(1)
    matched = []
    for control in ui_window(main).descendants():
        with contextlib.suppress(Exception):
            if control.element_info.control_type in ("Edit", "Spinner"):
                value = find_control_value(control)
                if float(value) == 205000.0:
                    matched.append({"value": value, "class": control.class_name(),
                                    "rectangle": str(control.rectangle())})
    snapshot("07-reopened-material-readback", main)
    if not matched:
        return {"status": "not_verified_by_values", "reason": "Saved 205000 field not found"}
    return {"status": "passed_value_readback", "E": 205000, "matching_fields": matched,
            "fresh_application_process": True}


def main():
    if sys.platform != "win32":
        raise RuntimeError("This real application trial requires Windows")
    OUT.mkdir(parents=True, exist_ok=True)
    appdir = Path(os.environ.get("RUNNER_TEMP", OUT.parent)) / "beidprep-trial"
    appdir.mkdir(parents=True, exist_ok=True)
    executable = appdir / "BeidPrepPro2026_v1.0.1.exe"
    source = Path(__file__).parent / "models" / "two_shell_plates_0000.rad"
    disposable = OUT / "input-copy.rad"
    disposable.write_bytes(source.read_bytes())
    process = None
    app_main = None
    log_stream = (OUT / "application.log").open("w", encoding="utf8")
    try:
        download(executable)
        app_environment = {
            key: value for key, value in os.environ.items()
            if not key.upper().startswith(("ACTIONS_", "GITHUB_", "RUNNER_"))
            and key.upper() not in ("GH_TOKEN", "GITHUB_TOKEN", "CI_JOB_TOKEN", "CI_TOKEN")
            and not ("TOKEN" in key.upper() and key.upper().startswith("CI"))
        }
        app_environment.update({"GALLIUM_DRIVER": "llvmpipe", "LIBGL_ALWAYS_SOFTWARE": "true",
                                "QT_OPENGL": "software"})
        RESULT["rendering_environment"] = {
            "Mesa": "24.3.4", "driver": "llvmpipe", "QT_OPENGL": "software",
            "osmesa_dll_present": (appdir / "osmesa.dll").is_file(),
            "opengl32_dll_present": (appdir / "opengl32.dll").is_file(),
        }
        log("Launching BeidPrep with Mesa llvmpipe software rendering")
        process = subprocess.Popen([str(executable)], cwd=appdir, env=app_environment,
                                   stdout=log_stream, stderr=subprocess.STDOUT)
        RESULT["launcher_pid"] = process.pid
        app_main = find_main()
        RESULT["steps"]["launch"] = {"status": "passed", "title": app_main.window_text(),
                                        "main_pid": app_main.process_id(),
                                        "account_details_supplied": False}
        write_result()
        snapshot("01-launched", app_main)
        RESULT["steps"]["authentication"] = {
            "status": "observed", "main_window_reached_without_credentials": True,
            "scope": "This downloaded v1.0.1 build and this run only; not a v2.0/offline/expiry claim"}
        try:
            open_file(app_main, disposable)
            observations = snapshot("02-model-opened", app_main)
            names = json.dumps(observations)
            evidence = [token for token in ("STEEL", "LOWER", "UPPER", "input-copy.rad") if token in names]
            RESULT["steps"]["open_rad"] = {"status": "observed" if evidence else "attempted",
                                               "visible_model_evidence": evidence}
            RESULT["steps"]["material_edit"] = edit_material(app_main)
            saved = OUT / "beidprep-saved.rad"
            save_as(app_main, saved)
            snapshot("05-after-save", app_main)
            actual_saved = saved if saved.exists() else disposable
            original_bytes = source.read_bytes()
            if actual_saved.exists() and (saved.exists() or actual_saved.read_bytes() != original_bytes):
                RESULT["steps"]["save"] = {"status": "passed", "path": actual_saved.name,
                                              "deck": inspect_deck(actual_saved)}
                if RESULT["steps"]["save"]["deck"]["material_contains_new_E"]:
                    RESULT["steps"]["material_edit"]["status"] = "passed_saved_deck"
                subprocess.run(["taskkill", "/PID", str(app_main.process_id()), "/T", "/F"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
                process = subprocess.Popen([str(executable)], cwd=appdir, env=app_environment,
                                           stdout=log_stream, stderr=subprocess.STDOUT)
                app_main = find_main()
                open_file(app_main, actual_saved)
                observations = snapshot("06-reopened", app_main)
                RESULT["steps"]["reopen"] = {"status": "not_verified_by_values", "title": app_main.window_text(),
                                                 "controls_file": "06-reopened-controls.json"}
                RESULT["steps"]["reopen"].update(read_reopened_material(app_main))
            else:
                RESULT["steps"]["save"] = {"status": "not_verified", "reason": "No new or changed saved deck found"}
        except Exception as exc:
            RESULT["interaction_error"] = str(exc)
            log(traceback.format_exc())
            snapshot("interaction-failed", app_main)
    except Exception as exc:
        RESULT["fatal_error"] = str(exc)
        log(traceback.format_exc())
        snapshot("launch-failed", app_main)
    finally:
        write_result()
        log(json.dumps(RESULT, indent=2))
        if app_main:
            with contextlib.suppress(Exception):
                subprocess.run(["taskkill", "/PID", str(app_main.process_id()), "/T", "/F"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        if process and process.poll() is None:
            with contextlib.suppress(Exception):
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        log_stream.close()
    if RESULT.get("fatal_error"):
        return 1
    saved = RESULT["steps"].get("save", {})
    deck = saved.get("deck", {})
    complete = (
        saved.get("status") == "passed"
        and RESULT["steps"].get("material_edit", {}).get("status") == "passed_saved_deck"
        and deck.get("has_nodes") and deck.get("shell_blocks") == 2
        and deck.get("has_material") and deck.get("contains_contact")
        and RESULT["steps"].get("reopen", {}).get("status") == "passed_value_readback"
    )
    if not complete:
        return 2  # Observation bundle exists, but the actual GUI round trip is incomplete.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
