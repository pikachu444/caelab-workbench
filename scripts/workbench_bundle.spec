from pathlib import Path
import json
import sys

from PyInstaller.utils.hooks import copy_metadata
from PyInstaller.building.utils import format_binaries_and_datas


source = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(source))
from scripts.bundled_optional_metadata import collect_optional_metadata
datas = [
    (str(source / "apps/workbench_qt/icons"), "apps/workbench_qt/icons"),
    (str(source / "caelab/solvers/installer/manifest.json"), "caelab/solvers/installer"),
]
# The MDO algorithms record these distributions' versions in saved artifacts.
for distribution in ("numpy", "scipy", "scikit-learn", "pymoo", "SALib"):
    datas += copy_metadata(distribution)

analysis = Analysis(
    [str(source / "apps/workbench_qt/desktop_entry.py")],
    pathex=[str(source)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "apps.workbench_qt.__main__",
        "apps.workbench_qt.native_analysis",
        "apps.workbench_qt.bundle_diagnostics",
        "apps.workbench_qt.expert_window",
        "apps.workbench_qt.mdo_studio.__main__",
        "caelab.native.windows_gate",
        "caelab.process_entry",
        # Explicit importlib targets in mdo/surrogate.py and mdo/sensitivity.py.
        "scipy.interpolate",
        "sklearn.gaussian_process",
        "sklearn.gaussian_process.kernels",
        "SALib.sample.sobol",
        "SALib.sample.morris",
        "SALib.analyze.sobol",
        "SALib.analyze.morris",
    ],
    hooksconfig={"matplotlib": {"backends": ["QtAgg"]}},
    runtime_hooks=[str(source / "scripts/pyi_runtime_hook.py")],
    # The application uses PySide6. Optional dependency hooks must not collect
    # another Qt binding from the build machine's installed packages.
    excludes=["PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
provenance = collect_optional_metadata(analysis, copy_metadata=copy_metadata,
                                       expand_datas=format_binaries_and_datas)
(Path(workpath) / 'collected-package-provenance.json').write_text(
    json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="CAELabWorkbench",
    console=False,
    debug=False,
    strip=False,
    upx=False,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="CAELabWorkbench",
)
