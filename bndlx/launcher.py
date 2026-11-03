"""Mod Launcher: mod packages (.zip) in a load order for one game folder; Apply builds the game files from their
originals with the enabled packages in that order (conflicts are asked about), then the game can be started. The
order can be exported and imported (mod_order.json). Make a package turns the changes of a game folder (the files
that differ from their .orig) into a package.

python -m bndlx.launcher [game folder] [--screenshot out.png]
"""
import argparse
import json
import os
import sys
import traceback

from imgui_bundle import hello_imgui, imgui

from . import dragdrop, theme
from .labels import config_dir
from .modpack_ui import ModsView, PackMaker

APP = 'Mod Launcher'
VERSION = '0.1'


def cfg_path():
    return os.path.join(config_dir(), 'launcher.json')


class Launcher:
    def __init__(self, root=None):
        try:
            with open(cfg_path(), encoding='utf-8') as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            self.cfg = {}
        self.view = ModsView(self.cfg, self.save_cfg)
        self.maker = PackMaker(self.cfg, self.save_cfg)
        self.maker.tool = f'{APP} {VERSION}'
        self.tab = 'order'
        self.want_tab = None                   # a tab to bring to the front (tests)
        root = root or self.guess_root()
        if root:
            self.view.set_root(root)
            self.maker.root = self.view.root
        self.dropped = []

    def save_cfg(self):
        try:
            os.makedirs(os.path.dirname(cfg_path()), exist_ok=True)
            with open(cfg_path(), 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=1)
        except OSError:
            pass

    def guess_root(self):
        """The folder the launcher sits in (when it is put into the game folder), else the last one."""
        here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else sys.argv[0]))
        for d in (here, os.getcwd()):
            if os.path.isfile(os.path.join(d, 'NFS13.exe')):
                return d
        last = (self.cfg.get('mod_roots') or [None])[0]
        return last if last and os.path.isdir(last) else None

    def post_init(self):
        try:
            dragdrop.install_drop(hello_imgui.get_glfw_window_address(), self.dropped.extend)
        except Exception:
            traceback.print_exc()

    def gui(self):
        if self.dropped and self.view.profile is not None:
            paths, self.dropped = self.dropped, []
            self.view.add([p for p in paths if p.lower().endswith('.zip')])
        self.view.job.pump()
        self.maker.job.pump()
        pick = {t: (imgui.TabItemFlags_.set_selected if self.want_tab == t else 0) for t in ('order', 'make')}
        self.want_tab = None
        if imgui.begin_tab_bar('##launcher'):
            if imgui.begin_tab_item('Load order', None, pick['order'])[0]:
                self.tab = 'order'
                self.view.draw(launcher=True)
                imgui.end_tab_item()
            if imgui.begin_tab_item('Make a package', None, pick['make'])[0]:
                if self.tab != 'make' and self.maker.root is None:
                    self.maker.root = self.view.root
                self.tab = 'make'
                self.maker.draw(self.cfg.get('mod_roots', []))
                imgui.end_tab_item()
            imgui.end_tab_bar()


def run(root=None, screenshot=None, frames=20, script=None):
    app = Launcher(root)
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = f'{APP} {VERSION} (BNDL Explorer)'
    params.app_window_params.window_geometry.size = (1280, 760)
    params.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.provide_full_screen_window
    params.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
    params.ini_filename = 'BNDLExplorer/launcher_imgui.ini'
    params.imgui_window_params.background_color = imgui.ImVec4(*theme.PALETTE['bg'])
    params.callbacks.post_init = app.post_init
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.setup_imgui_style = theme.apply
    count = [0]

    def gui():
        app.gui()
        count[0] += 1
        if script is not None:
            script(app, count[0])
        elif screenshot and count[0] > frames:
            hello_imgui.get_runner_params().app_shall_exit = True

    params.callbacks.show_gui = gui
    hello_imgui.run(params)
    if screenshot:
        import numpy as np
        from PIL import Image
        img = hello_imgui.final_app_window_screenshot()
        Image.fromarray(np.asarray(img)[..., :3]).save(screenshot)
    return app


def main():
    ap = argparse.ArgumentParser(prog='ModLauncher', description='Mod packages in a load order for NFS Most Wanted '
                                                                 '(2012), made with BNDL Explorer')
    ap.add_argument('root', nargs='?', help='the game folder')
    ap.add_argument('--screenshot', help='render the window, save it to this PNG and exit (for tests)')
    a = ap.parse_args()
    run(a.root, a.screenshot)


if __name__ == '__main__':
    main()
