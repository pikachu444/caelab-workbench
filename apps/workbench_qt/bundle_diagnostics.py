"""Fixed Qt/VTK offscreen smoke check with owned outputs and normal shutdown."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def _verify_png(path: Path, expected_size: tuple[int, int]) -> dict:
    from PySide6.QtGui import QImage

    payload = path.read_bytes()
    if not payload.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError(f'PNG signature check failed: {path.name}')
    image = QImage(str(path))
    if image.isNull() or (image.width(), image.height()) != expected_size:
        raise ValueError(f'PNG decode/size check failed: {path.name}')
    background = image.pixel(0, 0)
    if not any(image.pixel(x, y) != background for y in range(image.height()) for x in range(image.width())):
        raise ValueError(f'PNG has only one pixel value: {path.name}')
    return {'path': path.name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'width': image.width(), 'height': image.height(), 'decoded': True, 'nonuniform': True}


def _close_owned(window, canvas, checks, errors):
    if window is not None:
        try:
            window.Finalize()
            checks['vtk_finalize'] = True
        except Exception as error:
            errors.append(f'VTK cleanup failed: {type(error).__name__}: {error}')
    try:
        canvas.close()
        checks['widget_closed'] = not canvas.isVisible()
    except Exception as error:
        errors.append(f'Qt cleanup failed: {type(error).__name__}: {error}')


def _run(output: Path, report: dict) -> int:
    started = time.monotonic()
    from PySide6 import QtCore, QtGui
    from PySide6.QtWidgets import QApplication, QWidget

    if QApplication.instance() is not None:
        raise ValueError('Diagnostics require a separate process without an existing QApplication.')
    # Only this explicit diagnostic branch changes its own process environment.
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    app = QApplication([sys.executable])
    report['runtime'].update(qt_version=QtCore.qVersion(), qt_platform=app.platformName(),
                             qt_module=QtCore.__file__, qt_library_paths=app.libraryPaths())
    if app.platformName() != 'offscreen':
        raise ValueError('Qt did not select the required offscreen platform.')

    class Canvas(QWidget):
        painted = False
        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor('#20354a'))
            painter.setBrush(QtGui.QColor('#ef9b35'))
            painter.drawEllipse(24, 16, 64, 64)
            painter.end()
            self.painted = True

    canvas = Canvas()
    canvas.resize(128, 96)
    canvas.show()
    errors = report['errors']
    checks = report['checks']

    def perform():
        checks['event_loop_callback'] = True
        window = None
        try:
            qt_path = output / 'qt.png'
            if not canvas.grab().save(str(qt_path), 'PNG') or not canvas.painted:
                raise ValueError('Qt widget paint/PNG save did not complete.')
            report['qt_image'] = _verify_png(qt_path, (128, 96))
            checks['qt_paint_and_png'] = True

            import vtk
            import vtkmodules.vtkCommonCore
            report['runtime'].update(vtk_version=vtk.vtkVersion.GetVTKVersion(),
                                     vtk_module=vtk.__file__, vtk_native_module=vtkmodules.vtkCommonCore.__file__)
            sphere = vtk.vtkSphereSource()
            sphere.SetThetaResolution(12)
            sphere.SetPhiResolution(12)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(sphere.GetOutputPort())
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(.9, .25, .1)
            renderer = vtk.vtkRenderer()
            renderer.SetBackground(.1, .2, .3)
            renderer.AddActor(actor)
            window = vtk.vtkRenderWindow()
            window.SetOffScreenRendering(1)
            window.SetMultiSamples(0)
            window.SetSize(128, 96)
            window.AddRenderer(renderer)
            renderer.ResetCamera()
            window.Render()
            if window.GetOffScreenRendering() != 1:
                raise ValueError('VTK offscreen rendering was not enabled.')
            pixels = vtk.vtkWindowToImageFilter()
            pixels.SetInput(window)
            pixels.ReadFrontBufferOff()
            pixels.Update()
            vtk_path = output / 'vtk.png'
            writer = vtk.vtkPNGWriter()
            writer.SetFileName(str(vtk_path))
            writer.SetInputConnection(pixels.GetOutputPort())
            writer.Write()
            if writer.GetErrorCode():
                raise ValueError('VTK PNG writer reported an error.')
            report['vtk_image'] = _verify_png(vtk_path, (128, 96))
            checks['vtk_render_and_png'] = True
        except Exception as error:
            errors.append(f'{type(error).__name__}: {error}')
        finally:
            _close_owned(window, canvas, checks, errors)
            app.exit(1 if errors else 0)

    def timed_out():
        errors.append('Qt diagnostic deadline exceeded (10 seconds).')
        canvas.close()
        app.exit(1)

    deadline = QtCore.QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(timed_out)
    deadline.start(10000)
    QtCore.QTimer.singleShot(50, perform)
    code = app.exec()
    deadline.stop()
    report['event_loop_exit_code'] = code
    report['elapsed_s'] = time.monotonic() - started
    if report['elapsed_s'] >= 10:
        errors.append('Diagnostic exceeded the ten-second internal budget.')
    required = ('event_loop_callback', 'qt_paint_and_png', 'vtk_render_and_png', 'vtk_finalize', 'widget_closed')
    return 0 if code == 0 and not errors and all(checks.get(name) is True for name in required) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--output', required=True, type=Path, help='New absolute output directory; parent must exist')
    args = parser.parse_args(argv)
    output = args.output
    try:
        if not output.is_absolute() or not output.parent.is_dir():
            raise ValueError('An absolute new directory with an existing parent is required.')
        output.mkdir(exist_ok=False)
    except (OSError, ValueError) as error:
        print(f'Diagnostics output rejected: {error}', file=sys.stderr)
        return 2
    report = {'schema_version': 1, 'status': 'starting', 'checks': {}, 'errors': [],
              'runtime': {'frozen': bool(getattr(sys, 'frozen', False)), 'executable': sys.executable,
                          'bundle_root': getattr(sys, '_MEIPASS', None)},
              'scope': 'Fixed Qt paint and VTK primitive smoke only; not product GUI, native results, solver or clean-PC acceptance',
              'external_deadline': 'Caller must enforce 30 seconds; timeout/forced termination is failure, not normal exit'}
    path = output / 'diagnostics.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    try:
        code = _run(output, report)
    except Exception as error:
        report['errors'].append(f'{type(error).__name__}: {error}')
        code = 1
    # Commit success only after checks, cleanup and the event loop have returned.
    report['status'] = 'passed' if code == 0 else 'failed'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
