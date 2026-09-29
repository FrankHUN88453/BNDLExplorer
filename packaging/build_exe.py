"""Build the standalone BNDLExplorer.exe with PyInstaller (one file, no console).

usage: python packaging\build_exe.py      -> dist\BNDLExplorer.exe
"""
import os
import subprocess
import sys

import imgui_bundle

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IB = os.path.dirname(imgui_bundle.__file__)


def main():
    sep = os.pathsep
    args = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'BNDLExplorer',
        '--icon', os.path.join(HERE, 'bndlexplorer.ico'),
        '--distpath', os.path.join(ROOT, 'dist'),
        '--workpath', os.path.join(ROOT, 'build'),
        '--specpath', os.path.join(ROOT, 'build'),
        # imgui-bundle: the native module, its GLFW dll and its assets (fonts); the demos are left out
        '--collect-submodules', 'imgui_bundle',
        '--add-binary', f'{os.path.join(IB, "glfw3.dll")}{sep}imgui_bundle',
        '--add-data', f'{os.path.join(IB, "assets")}{sep}imgui_bundle/assets',
        # soundfile: libsndfile (with mpg123 / LAME) for the sounds
        '--collect-all', 'soundfile',
        '--collect-all', '_soundfile_data',
        # PyOpenGL for the 3D preview (its platform / array plugins are loaded by name)
        '--collect-submodules', 'OpenGL',
        '--exclude-module', 'tkinter',
        '--exclude-module', 'matplotlib',
        '--exclude-module', 'scipy',
        os.path.join(ROOT, 'BNDLExplorer.pyw'),
    ]
    subprocess.check_call(args, cwd=ROOT)
    print('built', os.path.join(ROOT, 'dist', 'BNDLExplorer.exe'))


if __name__ == '__main__':
    main()
