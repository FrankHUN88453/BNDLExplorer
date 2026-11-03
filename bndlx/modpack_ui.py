"""The Mod Launcher's two tabs: ModsView (the load order of a game folder: list, details, conflicts, apply, restore,
order files, start the game) and PackMaker (a package from the changed files of a game folder). The work is done in
modpack.py; this file draws and runs jobs."""
import os
import subprocess
import threading
import traceback

from imgui_bundle import imgui

from . import filedialog
from . import modpack as M
from .restypes import name as type_name

ZIP = [('Mod packages (*.zip)', '*.zip'), ('All files', '*.*')]
ORDER = [('Mod order (*.json)', '*.json'), ('All files', '*.*')]
GREEN = imgui.ImVec4(0.45, 0.85, 0.5, 1.0)
YELLOW = imgui.ImVec4(1.0, 0.8, 0.3, 1.0)
RED = imgui.ImVec4(1.0, 0.45, 0.4, 1.0)
MUTED = imgui.ImVec4(0.6, 0.6, 0.65, 1.0)


def short(path, n=60):
    return path if len(path) <= n else '...' + path[-n + 3:]


class Job:
    """One background job at a time: fn(progress) in a thread, done(result) on the GUI thread (pump)."""

    def __init__(self):
        self.thread = None
        self.text = ''
        self.progress = 0.0
        self.result = None

    def busy(self):
        return self.thread is not None

    def start(self, text, fn, done):
        if self.thread is not None:
            return False
        self.text, self.progress = text, 0.0

        def prog(i, n):
            self.progress = i / max(n, 1)

        def work():
            try:
                self.result = (done, fn(prog), None)
            except Exception as ex:
                traceback.print_exc()
                self.result = (done, None, ex)

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return True

    def pump(self):
        if self.thread is not None and not self.thread.is_alive():
            self.thread = None
            done, value, error = self.result
            self.result = None
            done(value, error)

    def draw(self):
        if self.thread is not None:
            imgui.progress_bar(self.progress, imgui.ImVec2(-1, 0), self.text)


class ModsView:
    def __init__(self, cfg, save_cfg):
        self.cfg, self.save_cfg = cfg, save_cfg
        self.root = None
        self.profile = None
        self.cache = {}            # zip path -> ((mtime, size), Package or ModError)
        self.sel = None
        self.job = Job()
        self.status = ''
        self.ask = None            # [(a md5, b md5, [Conflict])] still to settle before an apply
        self.after_ask = None      # what to run once they are settled
        self.outside = None        # files changed outside, waiting for the user
        self.report = None
        self.start_fn = None       # tests replace the game start
        self.started = None

    # -- state ------------------------------------------------------------------------------------------------
    def set_root(self, root):
        root = os.path.abspath(root) if root else None
        if root == self.root:
            return
        self.root, self.sel, self.report = root, None, None
        self.profile = M.Profile.load(root) if root else None
        if root:
            recent = [r for r in self.cfg.get('mod_roots', []) if os.path.normcase(r) != os.path.normcase(root)]
            self.cfg['mod_roots'] = [root] + recent[:9]
            self.save_cfg()

    def pkg(self, entry):
        try:
            st = os.stat(entry.path)
            stamp = (st.st_mtime, st.st_size)
        except OSError:
            return M.ModError(f'{entry.path} is missing')
        hit = self.cache.get(entry.path)
        if hit is None or hit[0] != stamp:
            try:
                p = M.Package(entry.path)
            except M.ModError as ex:
                p = ex
            hit = self.cache[entry.path] = (stamp, p)
            if isinstance(p, M.Package):
                entry.md5, entry.name = p.md5, p.name
        return hit[1]

    def enabled(self):
        out = []
        plat = M.game_platform(self.root) if self.root else 'PC'
        for m in self.profile.mods:
            p = self.pkg(m)
            if m.enabled and isinstance(p, M.Package) and p.platform == plat:
                out.append(p)
        return out

    def conflicts(self):
        return M.conflicts(self.enabled())

    def add(self, paths):
        n, bad = 0, []
        for p in paths:
            if not p.lower().endswith('.zip'):
                continue
            try:
                self.profile.add(p)
                n += 1
            except M.ModError as ex:
                bad.append(str(ex))
        if n:
            self.profile.save()
        self.status = f'{n} package(s) added' + (('; ' + '; '.join(bad)) if bad else '')
        return n

    def move(self, i, j):
        mods = self.profile.mods
        if 0 <= i < len(mods) and 0 <= j < len(mods) and i != j:
            m = mods.pop(i)
            mods.insert(j, m)
            self.sel = j
            self.profile.save()

    # -- applying ---------------------------------------------------------------------------------------------
    def request_apply(self, start_after=False):
        """Settle the conflicts first (ask which mod wins), then apply."""
        und = self.profile.undecided(self.conflicts())
        self.after_ask = lambda: self.run_apply(False, start_after)
        if und:
            self.ask = sorted(([a, b, cs] for (a, b), cs in und.items()), key=lambda x: (x[0], x[1]))
        else:
            self.after_ask()

    def run_apply(self, force, start_after=False):
        prof = self.profile
        self.profile.save()

        def done(rep, err):
            if err is not None:
                self.status = f'Apply failed: {err}'
                return
            self.report = rep
            self.status = (f'Applied: {len(rep.written)} files built, {len(rep.restored)} restored, '
                           f'{len(rep.removed)} removed, {len(rep.conflicts)} conflicts, {len(rep.errors)} errors')
            if rep.outside:
                self.outside = (rep.outside, start_after)
            elif start_after and not rep.errors:
                self.start_game()
        self.job.start('Building the game files...', lambda prog: M.apply(prof, prog, force), done)

    def run_restore(self):
        prof = self.profile

        def done(rep, err):
            if err is not None:
                self.status = f'Restore failed: {err}'
                return
            self.report = rep
            self.status = (f'The original game files are back ({len(rep.restored)} restored, {len(rep.removed)} '
                           f'removed)' + (f'; {len(rep.outside)} files with own changes were left alone'
                                          if rep.outside else ''))
        self.job.start('Restoring the original game files...', lambda prog: M.restore_all(prof, prog), done)

    def start_game(self):
        root = self.root
        if self.start_fn is not None:
            self.started = self.start_fn(root)
            return
        appid = ''
        try:
            with open(os.path.join(root, 'steam_appid.txt'), encoding='utf-8') as f:
                appid = f.read().strip()
        except OSError:
            pass
        exe = os.path.join(root, 'NFS13.exe')
        try:
            if appid.isdigit():
                os.startfile(f'steam://rungameid/{appid}')
                self.status = 'Starting the game through Steam (keep the EA app offline)'
            elif os.path.isfile(exe):
                subprocess.Popen([exe], cwd=root)
                self.status = 'Starting NFS13.exe'
            else:
                self.status = 'No NFS13.exe in this folder'
        except OSError as ex:
            self.status = f'Cannot start the game: {ex}'

    # -- drawing ----------------------------------------------------------------------------------------------
    def draw(self, launcher=False):
        self.job.pump()
        self.draw_root()
        if self.profile is None:
            imgui.text_wrapped('Choose the game folder (where NFS13.exe is, or the PS3 prototype\'s USRDIR folder).')
            self.modals()
            return
        busy = self.job.busy()
        avail = imgui.get_content_region_avail()
        right = min(520.0, avail.x * 0.42)
        bottom = 2 * imgui.get_frame_height_with_spacing() + 8
        imgui.begin_child('##modlist', imgui.ImVec2(avail.x - right - 8, avail.y - bottom), imgui.ChildFlags_.borders)
        self.draw_list(busy)
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child('##moddetails', imgui.ImVec2(0, avail.y - bottom), imgui.ChildFlags_.borders)
        if imgui.begin_tab_bar('##modtabs'):
            if imgui.begin_tab_item('Package')[0]:
                self.draw_details()
                imgui.end_tab_item()
            found = self.conflicts()
            if imgui.begin_tab_item(f'Conflicts ({len(found)})###conflicts')[0]:
                self.draw_conflicts(found)
                imgui.end_tab_item()
            if self.report is not None and imgui.begin_tab_item('Last apply')[0]:
                self.draw_report(self.report)
                imgui.end_tab_item()
            imgui.end_tab_bar()
        imgui.end_child()
        imgui.begin_disabled(busy)
        if imgui.button('Apply (build the game files)'):
            self.request_apply()
        imgui.same_line()
        if launcher:
            if imgui.button('Apply and start the game'):
                self.request_apply(start_after=True)
            imgui.same_line()
        if imgui.button('Restore the original game'):
            self.run_restore()
        imgui.same_line()
        if imgui.button('Export order...'):
            p = filedialog.save_file('Export the mod order', 'mod_order.json', ORDER, 'json')
            if p:
                self.profile.export_order(p)
                self.status = f'Order written to {p}'
        imgui.same_line()
        if imgui.button('Import order...'):
            p = filedialog.open_file('Import a mod order', self.cfg.get('mod_order', ''), ORDER)
            if p:
                try:
                    missing = self.profile.import_order(p, [os.path.dirname(m.path) for m in self.profile.mods])
                    self.profile.save()
                    self.cfg['mod_order'] = p
                    self.save_cfg()
                    self.status = 'Order imported' + (f'; not found: {", ".join(missing)}' if missing else '')
                except M.ModError as ex:
                    self.status = str(ex)
        imgui.end_disabled()
        self.job.draw()
        if self.status:
            imgui.text_wrapped(self.status)
        self.modals()

    def draw_root(self):
        roots = self.cfg.get('mod_roots', [])
        imgui.text('Game folder')
        imgui.same_line()
        imgui.set_next_item_width(min(640.0, imgui.get_content_region_avail().x - 260))
        if imgui.begin_combo('##modroot', self.root or '(choose)'):
            for r in roots:
                if imgui.selectable(r, r == self.root)[0]:
                    self.set_root(r)
            imgui.end_combo()
        imgui.same_line()
        if imgui.button('Browse...'):
            p = filedialog.pick_folder('The game folder (where NFS13.exe is)')
            if p:
                self.set_root(p)
        if self.root:
            imgui.same_line()
            plat = M.game_platform(self.root)
            ok = os.path.isfile(os.path.join(self.root, 'NFS13.exe')) or plat == 'PS3'
            imgui.text_colored(GREEN if ok else YELLOW, f'{plat} game' if ok else 'no NFS13.exe here')

    def draw_list(self, busy):
        mods = self.profile.mods
        imgui.text(f'Load order: {len(mods)} packages, later ones win')
        imgui.same_line()
        imgui.begin_disabled(busy)
        if imgui.small_button('Add packages...'):
            ps = filedialog.open_files('Add mod packages', self.cfg.get('mod_add', ''), ZIP)
            if ps:
                self.cfg['mod_add'] = ps[0]
                self.save_cfg()
                self.add(ps)
        imgui.end_disabled()
        imgui.same_line()
        imgui.text_disabled('(or drop .zip files here)')
        found = self.conflicts()
        per = {}
        for c in found:
            for k, md5 in enumerate(c.md5s):
                per.setdefault(md5, [0, 0])[0 if k == len(c.md5s) - 1 else 1] += 1
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.scroll_y
        table_h = max(80.0, imgui.get_content_region_avail().y - imgui.get_frame_height_with_spacing() - 6)
        if imgui.begin_table('##modtable', 6, flags, imgui.ImVec2(0, table_h)):
            imgui.table_setup_column('On', imgui.TableColumnFlags_.width_fixed, 28)
            imgui.table_setup_column('#', imgui.TableColumnFlags_.width_fixed, 28)
            imgui.table_setup_column('Package')
            imgui.table_setup_column('Version', imgui.TableColumnFlags_.width_fixed, 70)
            imgui.table_setup_column('Files', imgui.TableColumnFlags_.width_fixed, 50)
            imgui.table_setup_column('Conflicts', imgui.TableColumnFlags_.width_fixed, 90)
            imgui.table_headers_row()
            move = None
            for i, m in enumerate(mods):
                p = self.pkg(m)
                imgui.table_next_row()
                imgui.table_next_column()
                ch, en = imgui.checkbox(f'##en{i}', m.enabled)
                if ch and not busy:
                    m.enabled = en
                    self.profile.save()
                imgui.table_next_column()
                imgui.text(f'{i + 1}')
                imgui.table_next_column()
                label = p.name if isinstance(p, M.Package) else (m.name or os.path.basename(m.path))
                if imgui.selectable(f'{label}##row{i}', self.sel == i,
                                    imgui.SelectableFlags_.span_all_columns | imgui.SelectableFlags_.allow_overlap)[0]:
                    self.sel = i
                if imgui.begin_drag_drop_source():
                    imgui.set_drag_drop_payload_py_id('MODROW', i)
                    imgui.text(label)
                    imgui.end_drag_drop_source()
                if imgui.begin_drag_drop_target():
                    pl = imgui.accept_drag_drop_payload_py_id('MODROW')
                    if pl is not None:
                        move = (pl.data_id, i)
                    imgui.end_drag_drop_target()
                if imgui.is_item_hovered():
                    imgui.set_tooltip(m.path + ('' if isinstance(p, M.Package) else '\n' + str(p)))
                imgui.table_next_column()
                imgui.text(p.manifest.get('mod_version', '') if isinstance(p, M.Package) else '')
                imgui.table_next_column()
                if isinstance(p, M.Package):
                    imgui.text(str(len(p.files)))
                else:
                    imgui.text_colored(RED, 'error')
                imgui.table_next_column()
                won, lost = per.get(m.md5, [0, 0]) if m.enabled else (0, 0)
                if won or lost:
                    imgui.text_colored(YELLOW, f'{won} won, {lost} lost' if lost else f'{won} won')
                    if imgui.is_item_hovered():
                        imgui.set_tooltip('elements another enabled package changes too; see the Conflicts tab')
            imgui.end_table()
            if move is not None and not busy:
                self.move(*move)
        sel = self.sel if self.sel is not None and self.sel < len(mods) else None
        imgui.begin_disabled(sel is None or busy)
        for label, delta in (('Top', -10 ** 6), ('Up', -1), ('Down', 1), ('Bottom', 10 ** 6)):
            if imgui.small_button(label) and sel is not None:
                self.move(sel, max(0, min(len(mods) - 1, sel + delta)))
            imgui.same_line()
        if imgui.small_button('Remove from the list') and sel is not None:
            del mods[sel]
            self.sel = None
            self.profile.save()
            self.status = 'Removed from the list: Apply to take its changes out of the game'
        imgui.end_disabled()

    def draw_details(self):
        mods = self.profile.mods
        if self.sel is None or self.sel >= len(mods):
            imgui.text_disabled('Select a package.')
            return
        m = mods[self.sel]
        p = self.pkg(m)
        if not isinstance(p, M.Package):
            imgui.text_colored(RED, str(p))
            return
        mf = p.manifest
        imgui.text(p.name)
        parts = [mf.get('mod_version', ''), f'by {mf["author"]}' if mf.get('author') else '', p.platform,
                 mf.get('created', ''), mf.get('tool', '')]
        imgui.text_disabled('  |  '.join(x for x in parts if x))
        if mf.get('description'):
            imgui.text_wrapped(mf['description'])
        plat = M.game_platform(self.root)
        if p.platform != plat:
            imgui.text_colored(RED, f'A {p.platform} package: it is left out of this {plat} game.')
        imgui.separator()
        imgui.text(f'{len(p.files)} files')
        for f in p.files:
            if f['kind'] == 'bundle':
                imgui.bullet_text(f'{f["path"]}: {len(f.get("resources", []))} resources'
                                  + (f', {len(f["removed"])} removed' if f.get('removed') else ''))
                if imgui.is_item_hovered() and f.get('resources'):
                    imgui.set_tooltip('\n'.join(f'{r.get("name") or r["key"]}  ({type_name(r.get("type", 0))})'
                                                for r in f['resources'][:30])
                                      + ('\n...' if len(f['resources']) > 30 else ''))
            else:
                imgui.bullet_text(f'{f["path"]}: the whole file ({f.get("size", 0) // 1024} KB)'
                                  + ('' if f.get('base_md5') else ', new'))

    def draw_conflicts(self, found):
        if not found:
            imgui.text_disabled('The enabled packages change different things: no conflicts.')
            return
        imgui.text_wrapped('These elements are changed by more than one enabled package; the one loaded later wins '
                           '(green). Reorder the list, or let Apply ask you.')
        if imgui.small_button('Ask me again for every pair'):
            self.profile.decided = []
            self.profile.save()
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.scroll_y
        if imgui.begin_table('##conftable', 3, flags):
            imgui.table_setup_column('File')
            imgui.table_setup_column('Element')
            imgui.table_setup_column('Packages (load order)')
            imgui.table_headers_row()
            for c in found:
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(c.path)
                imgui.table_next_column()
                imgui.text(c.label or 'the whole file')
                imgui.table_next_column()
                for k, n in enumerate(c.mods):
                    if k:
                        imgui.same_line()
                        imgui.text_disabled('<')
                        imgui.same_line()
                    if k == len(c.mods) - 1:
                        imgui.text_colored(GREEN, n)
                    else:
                        imgui.text_disabled(n)
            imgui.end_table()

    @staticmethod
    def draw_report(rep):
        imgui.text(f'{len(rep.written)} files built, {len(rep.restored)} back to the original, '
                   f'{len(rep.removed)} removed')
        for title, items, col in (('Errors', [f'{a}: {b}' for a, b in rep.errors], RED),
                                  ('Left out', [f'{a}: {b}' for a, b in rep.skipped], YELLOW),
                                  ('Changed outside the launcher (left alone)', rep.outside, YELLOW),
                                  ('Built', rep.written, None), ('Restored', rep.restored, None),
                                  ('Removed', rep.removed, None)):
            if items and imgui.collapsing_header(f'{title} ({len(items)})', imgui.TreeNodeFlags_.default_open
                                                 if col is not None else 0):
                for x in items:
                    if col is not None:
                        imgui.text_colored(col, x)
                    else:
                        imgui.text(x)

    # -- questions --------------------------------------------------------------------------------------------
    def names(self):
        return {m.md5: (m.name or os.path.basename(m.path)) for m in self.profile.mods} if self.profile else {}

    def answer(self, winner, loser):
        """The user picked the winner of the first open question."""
        self.profile.decide(winner, loser)
        self.profile.save()
        self.ask = self.ask[1:]
        und = self.profile.undecided(self.conflicts())
        self.ask = [q for q in self.ask if (q[0], q[1]) in und]
        if not self.ask:
            self.ask = None
            if self.after_ask:
                fn, self.after_ask = self.after_ask, None
                fn()

    def modals(self):
        if self.ask:
            imgui.open_popup('Mods in conflict###modask')
        vp = imgui.get_main_viewport()
        imgui.set_next_window_size(imgui.ImVec2(min(820.0, vp.work_size.x - 40), 0), imgui.Cond_.appearing)
        if imgui.begin_popup_modal('Mods in conflict###modask', None, imgui.WindowFlags_.always_auto_resize)[0]:
            if not self.ask:
                imgui.close_current_popup()
            else:
                a, b, cs = self.ask[0]
                nm = self.names()
                na, nb = nm.get(a, '?'), nm.get(b, '?')
                imgui.text_wrapped(f'"{na}" and "{nb}" change the same {len(cs)} element(s). The package loaded '
                                   'later wins them:')
                imgui.begin_child('##askitems', imgui.ImVec2(760, min(260.0, 24.0 * len(cs) + 12)),
                                  imgui.ChildFlags_.borders)
                for c in cs[:200]:
                    imgui.bullet_text(f'{c.path}:  {c.label or "the whole file"}')
                if len(cs) > 200:
                    imgui.text_disabled(f'... and {len(cs) - 200} more')
                imgui.end_child()
                imgui.text_disabled(f'{len(self.ask) - 1} more pair(s) after this one' if len(self.ask) > 1 else '')
                if imgui.button(f'Load "{na}" later (it wins)'):
                    self.answer(a, b)
                imgui.same_line()
                if imgui.button(f'Load "{nb}" later (it wins)'):
                    self.answer(b, a)
                imgui.same_line()
                if imgui.button('Cancel'):
                    self.ask, self.after_ask = None, None
                    imgui.close_current_popup()
            imgui.end_popup()
        if self.outside:
            imgui.open_popup('Files with own changes###modoutside')
        if imgui.begin_popup_modal('Files with own changes###modoutside', None,
                                   imgui.WindowFlags_.always_auto_resize)[0]:
            if not self.outside:
                imgui.close_current_popup()
            else:
                paths, start_after = self.outside
                imgui.text_wrapped('These files were changed outside the launcher (an edit saved in BNDL Explorer, '
                                   'another installer). They were left as they are:')
                for p in paths[:40]:
                    imgui.bullet_text(p)
                imgui.text_wrapped('Make your own edits into a package first (the Make a package tab), or overwrite '
                                   'them with the mods.')
                if imgui.button('Overwrite them'):
                    self.outside = None
                    self.run_apply(True, start_after)
                    imgui.close_current_popup()
                imgui.same_line()
                if imgui.button('Leave them'):
                    self.outside = None
                    if start_after:
                        self.start_game()
                    imgui.close_current_popup()
            imgui.end_popup()


class PackMaker:
    """A package from the files of a game folder that differ from their .orig."""

    def __init__(self, cfg, save_cfg):
        self.cfg, self.save_cfg = cfg, save_cfg
        self.root = None
        self.changes = None
        self.ticked = {}
        self.meta = {'name': '', 'author': cfg.get('mod_author', ''), 'mod_version': '1.0', 'description': ''}
        self.job = Job()
        self.status = ''
        self.tool = ''

    def scan(self, root):
        self.root = root

        def done(ch, err):
            if err is not None:
                self.status = f'Scan failed: {err}'
                return
            self.changes = ch
            self.ticked = {c.path: c.default for c in ch}
            self.status = f'{len(ch)} changed files' if ch else 'No file differs from its .orig in this folder'
        self.job.start('Looking for changed files...', lambda prog: M.scan(root, progress=prog), done)

    def write(self, out):
        chosen = [c for c in self.changes if self.ticked.get(c.path)]
        root, meta, tool = self.root, dict(self.meta), self.tool
        self.cfg['mod_author'] = meta['author']
        self.save_cfg()

        def done(man, err):
            if err is not None:
                self.status = f'Writing the package failed: {err}'
                return
            self.status = (f'Package written: {out} ({len(man["files"])} files, '
                           f'{os.path.getsize(out) // 1024} KB)')
        self.job.start('Writing the package...', lambda prog: M.make_package(root, out, chosen, meta, tool, prog),
                       done)

    def draw(self, roots):
        self.job.pump()
        busy = self.job.busy()
        imgui.text_wrapped('A package holds what differs from the original files (<file>.orig, kept by BNDL Explorer '
                           'and the other tools the first time a file is saved): of a bundle only its changed '
                           'resources, other files whole. It installs on another copy of the game in the Load order '
                           'tab.')
        imgui.text('Game folder')
        imgui.same_line()
        imgui.set_next_item_width(min(620.0, imgui.get_content_region_avail().x - 280))
        if imgui.begin_combo('##packroot', self.root or '(choose)'):
            for r in roots:
                if imgui.selectable(r, r == self.root)[0]:
                    self.root, self.changes = r, None
            imgui.end_combo()
        imgui.same_line()
        if imgui.button('Browse...##pack'):
            p = filedialog.pick_folder('The game folder with the changes')
            if p:
                self.root, self.changes = p, None
        imgui.same_line()
        imgui.begin_disabled(busy or not self.root)
        if imgui.button('Find the changed files'):
            self.scan(self.root)
        imgui.end_disabled()
        if self.changes:
            avail = imgui.get_content_region_avail()
            flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.scroll_y
            if imgui.begin_table('##packfiles', 4, flags, imgui.ImVec2(0, max(120.0, avail.y - 250))):
                imgui.table_setup_column('Pack', imgui.TableColumnFlags_.width_fixed, 40)
                imgui.table_setup_column('File')
                imgui.table_setup_column('Stored as', imgui.TableColumnFlags_.width_fixed, 220)
                imgui.table_setup_column('Note')
                imgui.table_headers_row()
                for c in self.changes:
                    imgui.table_next_row()
                    imgui.table_next_column()
                    _, self.ticked[c.path] = imgui.checkbox(f'##pk{c.path}', self.ticked.get(c.path, False))
                    imgui.table_next_column()
                    imgui.text(c.path)
                    imgui.table_next_column()
                    if c.kind == 'bundle':
                        imgui.text(f'{c.resources} resources' + (f', {c.removed} removed' if c.removed else ''))
                    else:
                        imgui.text(f'whole file, {c.size // 1024} KB')
                    imgui.table_next_column()
                    if c.note:
                        imgui.text_colored(YELLOW, c.note)
                imgui.end_table()
            for k, label in (('name', 'Name'), ('author', 'Author'), ('mod_version', 'Version')):
                imgui.set_next_item_width(320)
                _, self.meta[k] = imgui.input_text(label + '##packmeta', self.meta[k])
            _, self.meta['description'] = imgui.input_text_multiline('Description##packmeta', self.meta['description'],
                                                                     imgui.ImVec2(-120, 70))
            n = sum(1 for v in self.ticked.values() if v)
            imgui.begin_disabled(busy or not n or not self.meta['name'].strip())
            if imgui.button(f'Write the package ({n} files)...'):
                safe = ''.join(ch if ch.isalnum() or ch in ' -_.' else '_' for ch in self.meta['name']).strip()
                p = filedialog.save_file('Save the mod package', (safe or 'mod') + '.zip', ZIP, 'zip')
                if p:
                    self.write(p)
            imgui.end_disabled()
            if not self.meta['name'].strip():
                imgui.same_line()
                imgui.text_disabled('give it a name')
        self.job.draw()
        if self.status:
            imgui.text_wrapped(self.status)

