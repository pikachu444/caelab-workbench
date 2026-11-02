"""Static packaging approval must require the rendering dependencies it advertises."""

import pytest
from scripts import build_windows_bundle as builder

RENDERING_FILES = (
    '_internal/PySide6/Qt6Svg.dll',
    '_internal/PySide6/plugins/iconengines/qsvgicon.dll',
    '_internal/PySide6/plugins/imageformats/qsvg.dll',
    '_internal/vtkmodules/vtkRenderingOpenGL2.cp313-win_amd64.pyd',
)


def _add_rendering_files(bundle):
    for relative in RENDERING_FILES:
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'static presence fixture; never loaded')


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    icon = source / 'apps/workbench_qt/icons/folder.svg'
    icon.parent.mkdir(parents=True)
    icon.write_text('<svg/>', encoding='utf-8')
    monkeypatch.setattr(builder, 'SOURCE', source)
    root = tmp_path / 'bundle'
    for relative in (
        'CAELabWorkbench.exe', '_internal/python313.dll', '_internal/PySide6/Qt6Core.dll',
        '_internal/PySide6/plugins/platforms/qwindows.dll',
        '_internal/vtkmodules/vtkCommonCore.pyd',
        '_internal/caelab/solvers/installer/manifest.json',
        '_internal/apps/workbench_qt/icons/folder.svg',
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'static presence fixture; never loaded')
    return root


@pytest.mark.parametrize('missing', RENDERING_FILES)
def test_bundle_without_rendering_dependency_is_not_approved(bundle, missing):
    _add_rendering_files(bundle)
    (bundle / missing).unlink()
    checks = builder._static_checks(bundle)
    original_checks = ('executable', 'native_dlls', 'python_runtime', 'qt_core',
                       'qt_windows_platform', 'vtk_core_extension', 'solver_manifest', 'svg_icons')
    assert all(checks[name] for name in original_checks)
    assert not all(checks.values())


def test_complete_rendering_files_allow_static_approval(bundle):
    _add_rendering_files(bundle)
    assert all(builder._static_checks(bundle).values())
