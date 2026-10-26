"""The launcher can import MDO contracts without optional backends or GUI."""
import os
from pathlib import Path
import subprocess
import sys


def test_mdo_imports_without_optional_backends():
    source = r'''
import importlib.abc
import sys

blocked = ('scipy', 'sklearn', 'pymoo', 'SALib', 'PySide6', 'vtk', 'apps', 'caelab.numerical')
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('Eager optional or retired dependency: ' + fullname)
sys.meta_path.insert(0, BlockOptional())
from caelab.mdo import variables, objectives, constraints, doe
from caelab.mdo import surrogate, optimize, pareto, sensitivity
'''
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, '-c', source], cwd=root,
                            env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
