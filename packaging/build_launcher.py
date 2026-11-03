"""Build the standalone ModLauncher.exe with PyInstaller (one file, no console).

usage: python packaging\\build_launcher.py      -> dist\\ModLauncher.exe
Put it next to BNDLExplorer.exe (More > Mod Launcher starts it) or into the game folder (it then opens that folder).
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
    entry = os.path.join(ROOT, 'build', 'modlauncher_main.py')
    os.makedirs(os.path.dirname(entry), exist_ok=True)
    with open(entry, 'w', encoding='utf-8') as f:
        f.write('from bndlx.launcher import main\nmain()\n')
    args = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'ModLauncher',
        '--icon', os.path.join(HERE, 'bndlexplorer.ico'),
        '--distpath', os.path.join(ROOT, 'dist'),
        '--workpath', os.path.join(ROOT, 'build', 'launcher'),
        '--specpath', os.path.join(ROOT, 'build'),
        '--paths', ROOT,
        '--collect-submodules', 'imgui_bundle',
        '--add-binary', f'{os.path.join(IB, "glfw3.dll")}{sep}imgui_bundle',
        '--add-data', f'{os.path.join(IB, "assets")}{sep}imgui_bundle/assets',
        '--exclude-module', 'tkinter',
        '--exclude-module', 'matplotlib',
        '--exclude-module', 'scipy',
        '--exclude-module', 'OpenGL',
        '--exclude-module', 'soundfile',
        '--exclude-module', 'capstone',
        entry,
    ]
    subprocess.check_call(args, cwd=ROOT)
    print('built', os.path.join(ROOT, 'dist', 'ModLauncher.exe'))


if __name__ == '__main__':
    main()
