"""Owned release gate and bundle-isolated external launch; no Qt imports."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

if __package__ in (None, ""):
    # Source launches keep the solver's cwd, so resolve this checkout explicitly.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from caelab.process_entry import external_environment


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        return 2
    gate, payload, log_path = args
    try:
        command = json.loads(payload)
        if not isinstance(command, list) or not command or any(not isinstance(arg, str) or "\0" in arg for arg in command):
            return 2
    except (TypeError, ValueError):
        return 2
    if gate != "-":
        deadline = time.monotonic() + 30
        while not Path(gate).exists():
            if time.monotonic() > deadline:
                return 125
            time.sleep(.02)

    output = None
    try:
        environment = external_environment()
        if os.name == "nt" and getattr(sys, "frozen", False):
            import ctypes
            # The bootloader installed a bundle directory. Restore default
            # searching in this gate only; the GUI parent's DLL state is intact.
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
            api.SetDllDirectoryW.restype = ctypes.c_int
            if not api.SetDllDirectoryW(None):
                raise ctypes.WinError(ctypes.get_last_error())
        if log_path != "-":
            output = Path(log_path).open("ab", buffering=0)
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, env=environment, shell=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return process.wait()
    except (OSError, ValueError) as error:
        if output is not None:
            output.write(f"External launch failed: {type(error).__name__}: {error}\n".encode("utf-8", errors="replace"))
        return 126
    finally:
        if output is not None:
            output.close()


if __name__ == "__main__":
    raise SystemExit(main())
