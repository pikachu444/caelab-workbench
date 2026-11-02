"""Run before stock Qt hooks, including their QtCore import."""

import sys

from caelab.process_entry import remember_caller_qt_environment

remember_caller_qt_environment()

if len(sys.argv) > 1 and sys.argv[1] == "--caelab-solver-gate":
    from caelab.native.windows_gate import main
    raise SystemExit(main(sys.argv[2:]))
