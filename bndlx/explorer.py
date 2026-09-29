"""The Explorer-like window chrome: tabs, address bar, command bar, navigation pane, the resource / folder
views (details and large icons), the preview pane frame and the status bar."""
import os
import struct
import tempfile
import time

from imgui_bundle import hello_imgui, imgui

from . import filedialog, ops, theme, winclip
from . import names as N
from .restypes import T_TEXTURE, name as type_name
from .theme import I, icon_text

PAYLOAD = 'BNDLX_RES'
BUNDLE_EXT = ('.bndl', '.bundle')


def human(n):
    if n < 1024:
        return f'{n} bytes'
    for unit in ('KB', 'MB', 'GB'):
        n /= 1024.0
        if n < 1024:
            return f'{n:.1f} {unit}'
    return f'{n:.1f} TB'


def short_size(n):
    return f'{max(1, (n + 1023) // 1024):,} KB'.replace(',', ' ') if n else '0 KB'


class Browser:
    """A tab that shows Home or a folder of the disk."""

    def __init__(self, loc=('home',)):
        self.loc = loc
        self.back = []
        self.fwd = []
        self.search = ''
        self.sel = set()
        self.uid = id(self)

    def go(self, loc):
        if loc != self.loc:
            self.back.append(self.loc)
            self.fwd.clear()
            self.loc = loc
            self.sel = set()
            self.search = ''

    def title(self):
        if self.loc[0] == 'home':
            return 'Home'
        return os.path.basename(self.loc[1].rstrip('\\/')) or self.loc[1]


def folder_entries(path, cache):
    """[(name, is_dir, size, mtime, platform)] of subfolders and bundles, cached by the folder's mtime."""
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        return []
    hit = cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    out = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir():
                        out.append((e.name, True, 0, e.stat().st_mtime, ''))
                    elif e.name.lower().endswith(BUNDLE_EXT):
                        st = e.stat()
                        plat = ''
                        try:
                            with open(e.path, 'rb') as f:
                                h = f.read(8)
                            if h[:4] == b'bnd2':
                                p = struct.unpack('>H', h[6:8])[0] if h[4] == 0 else struct.unpack('<H', h[6:8])[0]
                                plat = {1: 'PC', 2: 'PS3'}.get(p, '?')
                        except OSError:
                            pass
                        out.append((e.name, False, st.st_size, st.st_mtime, plat))
                except OSError:
                    continue
    except OSError:
        return []
    out.sort(key=lambda x: (not x[1], x[0].lower()))
    cache[path] = (mt, out)
    return out


class ExplorerUI:
    """Mixin for App: everything that frames the content like Windows File Explorer."""

    # -- tabs / navigation ------------------------------------------------------------------------------------
    @property
    def cur(self):
        from .app import Doc
        return self.tab if isinstance(self.tab, Doc) else None

    def show_doc(self, d):
        if d not in self.tabs:
            self.tabs.append(d)
        self.tab = d
        self._select_tab = id(d)

    def browser(self):
        """The current tab if it is a folder tab, else the first folder tab (a new one if there is none)."""
        if isinstance(self.tab, Browser):
            return self.tab
        for t in self.tabs:
            if isinstance(t, Browser):
                return t
        b = Browser()
        self.tabs.insert(0, b)
        return b

    def go_folder(self, path):
        b = self.browser()
        b.go(('folder', path) if path else ('home',))
        self.tab = b
        self._select_tab = id(b)

    def go_up(self):
        t = self.tab
        if t is None:
            return
        if isinstance(t, Browser):
            if t.loc[0] == 'folder':
                parent = os.path.dirname(t.loc[1].rstrip('\\/'))
                t.go(('folder', parent) if parent and parent != t.loc[1] else ('home',))
        elif t.type_filter is not None:
            self.set_type_filter(t, None)
        elif t.path:
            self.go_folder(os.path.dirname(t.path))

    def set_type_filter(self, d, tf):
        if d.type_filter != tf:
            d.back.append(d.type_filter)
            d.fwd.clear()
            d.type_filter = tf
            d.invalidate()

    def go_back(self):
        t = self.tab
        if isinstance(t, Browser) and t.back:
            t.fwd.append(t.loc)
            t.loc = t.back.pop()
        elif t is not None and not isinstance(t, Browser) and t.back:
            t.fwd.append(t.type_filter)
            t.type_filter = t.back.pop()
            t.invalidate()

    def go_forward(self):
        t = self.tab
        if isinstance(t, Browser) and t.fwd:
            t.back.append(t.loc)
            t.loc = t.fwd.pop()
        elif t is not None and not isinstance(t, Browser) and t.fwd:
            t.back.append(t.type_filter)
            t.type_filter = t.fwd.pop()
            t.invalidate()

    def pinned(self):
        pins = self.cfg.get('nav_folders')
        if pins is None:
            pins = []
            for p in self.cfg.get('recent', []):
                if 'bndlx_' in p or not os.path.exists(p):
                    continue
                root = os.path.dirname(p)
                cur = root
                for _ in range(4):                       # the game's root folder, if the bundle lies inside it
                    if os.path.exists(os.path.join(cur, 'GLOBALEFFECTS.BNDL')):
                        root = cur
                        break
                    cur = os.path.dirname(cur)
                if root and root not in pins and os.path.isdir(root):
                    pins.append(root)
            self.cfg['nav_folders'] = pins[:6]
        return [p for p in self.cfg['nav_folders'] if os.path.isdir(p)]

    # -- frame --------------------------------------------------------------------------------------------------
    def gui(self):
        if not self._themed:
            theme.apply(self.cfg.get('theme', 'dark') == 'dark')
            self._themed = True
        self.pump_job()
        self.pump_drops()
        self.thumbs.pump()
        self.shortcuts()
        if self.want_select is not None and self.cur is not None:
            self.goto(self.want_select, self.cur)
            self.want_select = None
        self.tab_strip()
        self.address_bar()
        self.command_bar()
        avail = imgui.get_content_region_avail()
        h = avail.y - imgui.get_frame_height() - 6
        nav_w = min(max(150.0, self.cfg.get('nav_w', 250)), avail.x * 0.45)
        imgui.push_style_color(imgui.Col_.child_bg, theme.col('nav'))
        imgui.begin_child('nav', imgui.ImVec2(nav_w, h), imgui.ChildFlags_.borders)
        self.nav_pane()
        imgui.end_child()
        imgui.pop_style_color()
        self.splitter('nav_w', h)
        show_prev = self.cfg.get('preview', True) and self.cur is not None
        rest = imgui.get_content_region_avail().x
        if show_prev:
            main_w = self.cfg.get('main_w', rest * 0.55)
            main_w = min(max(280.0, main_w), rest - 280)
            imgui.begin_child('main', imgui.ImVec2(main_w, h), imgui.ChildFlags_.borders)
        else:
            imgui.begin_child('main', imgui.ImVec2(0, h), imgui.ChildFlags_.borders)
        p0 = imgui.get_window_pos()
        sz = imgui.get_window_size()
        self.list_rect = (p0.x, p0.y, p0.x + sz.x, p0.y + sz.y)
        self.row_rects = []
        self.main_view()
        imgui.end_child()
        if self.cur is not None:
            self.drop_on(self.cur)
        if show_prev:
            self.splitter('main_w', h, main_w)
            imgui.begin_child('details_panel', imgui.ImVec2(0, h), imgui.ChildFlags_.borders)
            p0 = imgui.get_window_pos()
            sz = imgui.get_window_size()
            self.details_rect = (p0.x, p0.y, p0.x + sz.x, p0.y + sz.y)
            self.details_panel()
            imgui.end_child()
        else:
            self.details_rect = None
        self.status_bar()
        self.modals()
        self.results_window()
        if self.drag is not None:
            payload = imgui.get_drag_drop_payload_py_id()
            if payload is None or payload.type != PAYLOAD:
                self.drag = None
                self.drag_out_done = False
            else:
                self.drag_out()
        self.exit_guard()

    def splitter(self, key, h, current=None):
        """A thin vertical bar between two panes; dragging it changes cfg[key] (the left pane's width)."""
        imgui.same_line(0, 0)
        imgui.invisible_button(f'##split_{key}', imgui.ImVec2(6, h))
        if imgui.is_item_hovered() or imgui.is_item_active():
            imgui.set_mouse_cursor(imgui.MouseCursor_.resize_ew)
        if imgui.is_item_active():
            base = self.cfg.get(key, current if current is not None else 250)
            if current is not None and key not in self.cfg:
                base = current
            self.cfg[key] = base + imgui.get_io().mouse_delta.x
        if imgui.is_item_deactivated():
            self.save_cfg()
        imgui.same_line(0, 0)

    # -- tab strip ----------------------------------------------------------------------------------------------
    def tab_strip(self):
        from .app import Doc
        flags = imgui.TabBarFlags_.reorderable | imgui.TabBarFlags_.fitting_policy_scroll | imgui.TabBarFlags_.auto_select_new_tabs
        imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(12, 7))
        if imgui.begin_tab_bar('tabs', flags):
            for t in list(self.tabs):
                if isinstance(t, Doc):
                    label = f'{theme.BUNDLE}  {t.name}{" *" if t.modified else ""}  [{t.b.platform}]###tab{id(t)}'
                else:
                    ic = I.ICON_FA_HOUSE if t.loc[0] == 'home' else theme.FOLDER
                    label = f'{ic}  {t.title()}###tab{id(t)}'
                flg = imgui.TabItemFlags_.set_selected if self._select_tab == id(t) else 0
                sel, keep = imgui.begin_tab_item(label, True, flg)
                if isinstance(t, Doc):
                    self.drop_on(t)
                    if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal) and t.path:
                        imgui.set_tooltip(t.path)
                if sel:
                    if self._select_tab is None or self._select_tab == id(t):
                        self.tab = t
                    imgui.end_tab_item()
                if keep is False:
                    self.close_tab(t)
            if imgui.tab_item_button(I.ICON_FA_PLUS, imgui.TabItemFlags_.trailing | imgui.TabItemFlags_.no_tooltip):
                b = Browser()
                self.tabs.append(b)
                self.tab = b
                self._select_tab = id(b)
            imgui.end_tab_bar()
        imgui.pop_style_var()
        if self._select_tab is not None and self.tab is not None and self._select_tab == id(self.tab):
            self._select_tab = None
        if not self.tabs:
            b = Browser()
            self.tabs.append(b)
            self.tab = b

    def close_tab(self, t):
        from .app import Doc
        if isinstance(t, Doc):
            self.close_doc(t)
        else:
            self.tabs.remove(t)
            if self.tab is t:
                self.tab = self.tabs[-1] if self.tabs else None

    # -- address bar --------------------------------------------------------------------------------------------
    def nav_button(self, icon, tip, enabled=True):
        imgui.begin_disabled(not enabled)
        clicked = imgui.button(f'{icon}##{tip}', imgui.ImVec2(32, 0))
        imgui.end_disabled()
        if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled | imgui.HoveredFlags_.delay_short):
            imgui.set_tooltip(tip)
        return clicked and enabled

    def crumbs(self):
        """[(label, action)] for the address bar."""
        t = self.tab
        out = []

        def folder_parts(path):
            parts = []
            p = os.path.abspath(path)
            while True:
                head, tail = os.path.split(p)
                if tail:
                    parts.append((tail, p))
                    p = head
                else:
                    parts.append((head.rstrip('\\/') or head, head))
                    break
            return list(reversed(parts))

        if t is None:
            return out
        if isinstance(t, Browser):
            if t.loc[0] == 'home':
                return [('Home', lambda: None)]
            for label, p in folder_parts(t.loc[1]):
                out.append((label, lambda p=p: self.go_folder(p)))
            return out
        if t.path:
            for label, p in folder_parts(os.path.dirname(t.path)):
                out.append((label, lambda p=p: self.go_folder(p)))
        out.append((t.name, lambda d=t: self.set_type_filter(d, None)))
        if t.type_filter is not None:
            out.append((type_name(t.type_filter), lambda: None))
        return out

    def address_bar(self):
        t = self.tab
        can_back = t is not None and bool(t.back)
        can_fwd = t is not None and bool(t.fwd)
        imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0)
        if self.nav_button(I.ICON_FA_ARROW_LEFT, 'Back (Alt+Left)', can_back):
            self.go_back()
        imgui.same_line(0, 2)
        if self.nav_button(I.ICON_FA_ARROW_RIGHT, 'Forward (Alt+Right)', can_fwd):
            self.go_forward()
        imgui.same_line(0, 2)
        if self.nav_button(I.ICON_FA_ARROW_UP, 'Up (Alt+Up)', t is not None and not (isinstance(t, Browser) and t.loc[0] == 'home')):
            self.go_up()
        imgui.same_line(0, 2)
        if self.nav_button(I.ICON_FA_ROTATE_RIGHT, 'Refresh (F5)'):
            self.refresh()
        imgui.pop_style_var()
        imgui.same_line()
        avail = imgui.get_content_region_avail().x
        search_w = min(300.0, max(160.0, avail * 0.26))
        addr_w = avail - search_w - 8
        # address box
        if self.addr_edit is not None:
            imgui.set_next_item_width(addr_w)
            if self.addr_focus:
                imgui.set_keyboard_focus_here()
                self.addr_focus = False
            ch, self.addr_edit = imgui.input_text('##addr', self.addr_edit, imgui.InputTextFlags_.enter_returns_true
                                                  | imgui.InputTextFlags_.auto_select_all)
            if ch:
                self.address_go(self.addr_edit.strip().strip('"'))
                self.addr_edit = None
            elif imgui.is_item_deactivated() or imgui.is_key_pressed(imgui.Key.escape):
                self.addr_edit = None
        else:
            p0 = imgui.get_cursor_screen_pos()
            fh = imgui.get_frame_height()
            dl = imgui.get_window_draw_list()
            dl.add_rect_filled(p0, imgui.ImVec2(p0.x + addr_w, p0.y + fh), imgui.get_color_u32(imgui.Col_.frame_bg), 4)
            dl.add_rect(p0, imgui.ImVec2(p0.x + addr_w, p0.y + fh), imgui.get_color_u32(imgui.Col_.border), 4)
            imgui.push_clip_rect(p0, imgui.ImVec2(p0.x + addr_w - 4, p0.y + fh), True)
            imgui.set_cursor_screen_pos(imgui.ImVec2(p0.x + 6, p0.y))
            imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0)
            imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(0, 0))
            imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(5, imgui.get_style().frame_padding.y))
            ic = theme.BUNDLE if self.cur is not None else (I.ICON_FA_HOUSE if isinstance(t, Browser) and t.loc[0] == 'home' else theme.FOLDER)
            imgui.align_text_to_frame_padding()
            icon_text(ic, theme.BUNDLE_COL if self.cur is not None else theme.PALETTE['folder'])
            crumbs = self.crumbs()
            if len(crumbs) > 6:
                crumbs = [('…', crumbs[-7][1])] + crumbs[-6:]
            for i, (label, act) in enumerate(crumbs):
                imgui.same_line()
                imgui.text_disabled(f' {I.ICON_FA_CHEVRON_RIGHT} ')
                imgui.same_line()
                if imgui.button(f'{label}##crumb{i}'):
                    act()
            imgui.pop_style_var(3)
            imgui.pop_clip_rect()
            imgui.set_cursor_screen_pos(p0)
            imgui.invisible_button('##addrbg', imgui.ImVec2(addr_w, fh))
            if imgui.is_item_clicked():
                self.addr_edit = self.address_text()
                self.addr_focus = True
            imgui.set_cursor_screen_pos(imgui.ImVec2(p0.x + addr_w, p0.y))
            imgui.dummy(imgui.ImVec2(0, fh))
        imgui.same_line(0, 8)
        imgui.set_next_item_width(-1)
        if self.search_focus:
            imgui.set_keyboard_focus_here()
            self.search_focus = False
        t = self.tab                   # a crumb or button above may have switched the tab this frame
        name = (self.cur.name if self.cur is not None else (t.title() if t is not None else ''))
        if t is not None:
            if self.cur is not None:
                ch, self.cur.filter = imgui.input_text_with_hint('##search', f'{I.ICON_FA_MAGNIFYING_GLASS}  Search {name}', self.cur.filter)
            else:
                ch, t.search = imgui.input_text_with_hint('##search', f'{I.ICON_FA_MAGNIFYING_GLASS}  Search {name}', t.search)

    def address_text(self):
        t = self.tab
        if isinstance(t, Browser):
            return t.loc[1] if t.loc[0] == 'folder' else ''
        if t is not None and t.path:
            return t.path
        return ''

    def address_go(self, text):
        if not text:
            self.go_folder(None)
        elif os.path.isdir(text):
            self.go_folder(os.path.abspath(text))
        elif os.path.isfile(text):
            self.open_path(text)
        else:
            self.status = f'Windows cannot find "{text}".'

    def refresh(self):
        self.folder_cache.clear()
        if self.cur is not None:
            self.cur.invalidate()
            self.cur.summary.clear()

    # -- command bar --------------------------------------------------------------------------------------------
    def cmd(self, icon, text=None, tip=None, enabled=True):
        imgui.begin_disabled(not enabled)
        label = f'{icon}  {text}' if text else icon
        clicked = imgui.button(f'{label}##cmd{tip or text}')
        imgui.end_disabled()
        if tip and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled | imgui.HoveredFlags_.delay_short):
            imgui.set_tooltip(tip)
        imgui.same_line(0, 2)
        return clicked and enabled

    def sep(self):
        imgui.same_line(0, 6)
        p = imgui.get_cursor_screen_pos()
        fh = imgui.get_frame_height()
        imgui.get_window_draw_list().add_line(imgui.ImVec2(p.x, p.y + 4), imgui.ImVec2(p.x, p.y + fh - 4),
                                              imgui.get_color_u32(imgui.Col_.separator), 1)
        imgui.dummy(imgui.ImVec2(1, fh))
        imgui.same_line(0, 6)

    def command_bar(self):
        d = self.cur
        dd, r = self.focused()
        sel = self.selected(d) if d is not None else []
        ro = d is None or d.b.truncated
        imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0)
        imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(9, 6))
        if self.cmd(I.ICON_FA_FOLDER_OPEN, 'Open', 'Open bundles (Ctrl+O)'):
            self.action_open()
        if self.cmd(I.ICON_FA_FLOPPY_DISK, 'Save', 'Save (Ctrl+S)', d is not None and d.modified and not ro):
            self.action_save(d)
        self.sep()
        if self.cmd(I.ICON_FA_SCISSORS, None, 'Cut (Ctrl+X)', bool(sel) and not ro):
            self.clip_copy(d, cut=True)
        if self.cmd(I.ICON_FA_COPY, None, 'Copy (Ctrl+C): paste in another bundle, or as files in Explorer', bool(sel)):
            self.clip_copy(d)
        if self.cmd(I.ICON_FA_PASTE, None, 'Paste (Ctrl+V)', d is not None and not ro):
            self.clip_paste(d)
        if self.cmd(I.ICON_FA_PEN, None, 'Change id (F2)', r is not None and not ro):
            self.start_rename(d, r)
        if self.cmd(I.ICON_FA_TRASH_CAN, None, 'Delete (Del)', bool(sel) and not ro):
            self.modal = {'kind': 'confirm_delete', 'doc': d.uid, 'ids': [x.id for x in sel]}
        self.sep()
        if self.cmd(I.ICON_FA_FILE_IMPORT, 'Import', 'Add resources from .bres files', d is not None and not ro):
            self.action_import_resources(d)
        if self.cmd(I.ICON_FA_FILE_EXPORT, 'Export', 'Export the selected resource', r is not None):
            imgui.open_popup('export_menu')
        if self.cmd(I.ICON_FA_RIGHT_LEFT, 'Replace', 'Replace the selected resource from a file', r is not None and not ro):
            self.action_replace(d, r)
        if imgui.begin_popup('export_menu'):
            self.export_items(dd, r)
            imgui.end_popup()
        self.sep()
        if self.cmd(I.ICON_FA_ARROW_RIGHT_ARROW_LEFT, 'Convert', 'Save a PS3 bundle for PC or a PC bundle for PS3', d is not None):
            self.action_convert(d, 'PC' if d.b.platform == 'PS3' else 'PS3')
        if self.cmd(I.ICON_FA_ARROW_UP_WIDE_SHORT, 'Sort', 'Sort by', d is not None):
            imgui.open_popup('sort_menu')
        if imgui.begin_popup('sort_menu'):
            self.sort_items(d)
            imgui.end_popup()
        if self.cmd(I.ICON_FA_TABLE_CELLS_LARGE if self.cfg.get('view') == 'icons' else I.ICON_FA_LIST, 'View', 'Layout'):
            imgui.open_popup('view_menu')
        if imgui.begin_popup('view_menu'):
            self.view_items()
            imgui.end_popup()
        if self.cmd(I.ICON_FA_ELLIPSIS, None, 'See more'):
            imgui.open_popup('more_menu')
        if imgui.begin_popup('more_menu'):
            self.more_items()
            imgui.end_popup()
        right = imgui.get_content_region_avail().x
        prev_on = self.cfg.get('preview', True)
        w = imgui.calc_text_size('  Details').x + 40
        if right > w:
            imgui.same_line(imgui.get_cursor_pos_x() + right - w)
            if prev_on:
                imgui.push_style_color(imgui.Col_.button, imgui.get_style_color_vec4(imgui.Col_.button_active))
            if self.cmd(I.ICON_FA_TABLE_COLUMNS if hasattr(I, 'ICON_FA_TABLE_COLUMNS') else I.ICON_FA_TABLE_LIST, 'Details',
                        'Show or hide the preview / details pane'):
                self.cfg['preview'] = not prev_on
                self.save_cfg()
            if prev_on:
                imgui.pop_style_color()
        imgui.new_line()
        imgui.pop_style_var(2)
        imgui.separator()

    def sort_items(self, d):
        if d is None:
            return
        cols = ['Name', 'Id', 'Type', 'Description', 'Size', 'Imports']
        for i, c in enumerate(cols):
            if imgui.menu_item(c, '', d.sort[0] == i)[0]:
                d.sort = (i, d.sort[1])
                d.invalidate()
        imgui.separator()
        if imgui.menu_item('Ascending', '', d.sort[1])[0]:
            d.sort = (d.sort[0], True)
            d.invalidate()
        if imgui.menu_item('Descending', '', not d.sort[1])[0]:
            d.sort = (d.sort[0], False)
            d.invalidate()

    def view_items(self):
        v = self.cfg.get('view', 'details')
        if imgui.menu_item(f'{I.ICON_FA_TABLE_CELLS_LARGE}  Large icons', 'Ctrl+Shift+2', v == 'icons')[0]:
            self.cfg['view'] = 'icons'
            self.save_cfg()
        if imgui.menu_item(f'{I.ICON_FA_LIST}  Details', 'Ctrl+Shift+6', v == 'details')[0]:
            self.cfg['view'] = 'details'
            self.save_cfg()
        imgui.separator()
        if imgui.menu_item('Preview / details pane', 'Alt+P', self.cfg.get('preview', True))[0]:
            self.cfg['preview'] = not self.cfg.get('preview', True)
            self.save_cfg()
        imgui.separator()
        dark = self.cfg.get('theme', 'dark') == 'dark'
        if imgui.menu_item('Dark theme (default)', '', dark)[0]:
            self.cfg['theme'] = 'dark'
            theme.apply(True)
            self.save_cfg()
        if imgui.menu_item('Light theme', '', not dark)[0]:
            self.cfg['theme'] = 'light'
            theme.apply(False)
            self.save_cfg()

    def more_items(self):
        d = self.cur
        if imgui.menu_item('Open by path...', 'Ctrl+L', False)[0]:
            self.modal = {'kind': 'open_path', 'text': self.cfg.get('last_bundle', '')}
        if imgui.begin_menu('Recent bundles', bool(self.cfg.get('recent'))):
            for p in self.cfg.get('recent', []):
                if imgui.menu_item(p, '', False)[0]:
                    self.open_path(p)
            imgui.end_menu()
        if imgui.menu_item('Save as...', 'Ctrl+Shift+S', False, d is not None)[0]:
            self.action_save_as(d)
        imgui.separator()
        if imgui.menu_item('Undo', 'Ctrl+Z', False, d is not None and bool(d.undo))[0]:
            self.undo(d)
        if imgui.menu_item('Redo', 'Ctrl+Y', False, d is not None and bool(d.redo))[0]:
            self.redo(d)
        if imgui.menu_item('Select all', 'Ctrl+A', False, d is not None)[0]:
            d.sel = {r.id for r in self.visible_rows(d)}
        imgui.separator()
        if imgui.menu_item('Convert to PC (save as)...', '', False, d is not None and d.b.platform == 'PS3')[0]:
            self.action_convert(d, 'PC')
        if imgui.menu_item('Convert to PS3 (save as)...', '', False, d is not None and d.b.platform == 'PC')[0]:
            self.action_convert(d, 'PS3')
        if imgui.menu_item('Extract all resources...', '', False, d is not None)[0]:
            self.action_extract(d)
        if imgui.menu_item('Import resources from folder...', '', False, d is not None and not d.b.truncated)[0]:
            self.action_import_folder(d)
        imgui.separator()
        if imgui.menu_item('Find names (scan the game folders)...', '', False, self.job is None)[0]:
            self.action_find_names()
        if imgui.menu_item('Find in all open bundles...', 'Ctrl+Shift+F', False, bool(self.docs))[0]:
            self.modal = {'kind': 'find', 'text': self.find_text}
        if imgui.menu_item('Go to id...', 'Ctrl+G', False, bool(self.docs))[0]:
            self.modal = {'kind': 'goto', 'text': ''}
        if imgui.menu_item('Bundle properties...', 'Alt+Enter', False, d is not None)[0]:
            self.modal = {'kind': 'properties', 'doc': d.uid}
        imgui.separator()
        if imgui.begin_menu('Options'):
            dark = self.cfg.get('theme', 'dark') == 'dark'
            if imgui.menu_item('Dark theme (default)', '', dark)[0]:
                self.cfg['theme'] = 'dark'
                theme.apply(True)
                self.save_cfg()
            if imgui.menu_item('Light theme', '', not dark)[0]:
                self.cfg['theme'] = 'light'
                theme.apply(False)
                self.save_cfg()
            imgui.separator()
            ext = self.cfg.get('drag_texture_ext', '.png')
            if imgui.menu_item('Drag / copy textures out as PNG', '', ext == '.png')[0]:
                self.cfg['drag_texture_ext'] = '.png'
                self.save_cfg()
            if imgui.menu_item('Drag / copy textures out as DDS', '', ext == '.dds')[0]:
                self.cfg['drag_texture_ext'] = '.dds'
                self.save_cfg()
            imgui.separator()
            ext = self.cfg.get('texture_ext', '.dds')
            if imgui.menu_item('Extract all: textures as DDS', '', ext == '.dds')[0]:
                self.cfg['texture_ext'] = '.dds'
                self.save_cfg()
            if imgui.menu_item('Extract all: textures as PNG', '', ext == '.png')[0]:
                self.cfg['texture_ext'] = '.png'
                self.save_cfg()
            imgui.end_menu()
        if imgui.menu_item('How to use', 'F1', False)[0]:
            from .app import HELP
            self.modal = {'kind': 'message', 'title': 'How to use', 'text': HELP}
        if imgui.menu_item('About', '', False)[0]:
            from .app import ABOUT
            self.modal = {'kind': 'message', 'title': 'About', 'text': ABOUT}
        imgui.separator()
        if imgui.menu_item('Exit', 'Alt+F4', False)[0]:
            hello_imgui.get_runner_params().app_shall_exit = True

    # -- navigation pane ----------------------------------------------------------------------------------------
    def nav_item(self, key, icon, colour, label, selected=False, leaf=True, open_default=False):
        flags = imgui.TreeNodeFlags_.span_avail_width | imgui.TreeNodeFlags_.open_on_arrow | imgui.TreeNodeFlags_.frame_padding
        if leaf:
            flags |= imgui.TreeNodeFlags_.leaf | imgui.TreeNodeFlags_.no_tree_push_on_open
        if selected:
            flags |= imgui.TreeNodeFlags_.selected
        if open_default:
            flags |= imgui.TreeNodeFlags_.default_open
        opened = imgui.tree_node_ex(f'##{key}', flags)
        clicked = imgui.is_item_clicked() and not imgui.is_item_toggled_open()
        imgui.same_line(0, 2)
        icon_text(icon, colour)
        imgui.same_line(0, 6)
        imgui.text(label)
        return opened and not leaf, clicked

    def nav_pane(self):
        t = self.tab
        home_sel = isinstance(t, Browser) and t.loc[0] == 'home'
        _, cl = self.nav_item('home', I.ICON_FA_HOUSE, (0.2, 0.5, 0.85, 1), 'Home', home_sel)
        if cl:
            self.go_folder(None)
        if self.docs:
            imgui.spacing()
            imgui.text_disabled('Open bundles')
            for d in self.docs:
                sel = self.cur is d and d.type_filter is None
                opened, cl = self.nav_item(f'doc{d.uid}', theme.BUNDLE, theme.BUNDLE_COL,
                                           f'{d.name}{" *" if d.modified else ""}  [{d.b.platform}]', sel, leaf=False,
                                           open_default=True)
                self.drop_on(d)
                if cl:
                    self.show_doc(d)
                    self.set_type_filter(d, None)
                if imgui.begin_popup_context_item(f'navctx{d.uid}'):
                    if imgui.menu_item('Save', '', False, d.modified)[0]:
                        self.action_save(d)
                    if imgui.menu_item('Close', '', False)[0]:
                        self.close_doc(d)
                    if imgui.menu_item('Open file location', '', False, bool(d.path))[0]:
                        self.go_folder(os.path.dirname(d.path))
                    if imgui.menu_item('Properties', '', False)[0]:
                        self.modal = {'kind': 'properties', 'doc': d.uid}
                    imgui.end_popup()
                if opened:
                    counts = {}
                    for r in d.b.resources:
                        counts[r.type] = counts.get(r.type, 0) + 1
                    for ty in sorted(counts, key=type_name):
                        ic, colr = theme.type_icon(ty)
                        _, cl = self.nav_item(f'doc{d.uid}t{ty}', theme.FOLDER, theme.PALETTE['folder'],
                                              f'{type_name(ty)} ({counts[ty]})', self.cur is d and d.type_filter == ty)
                        if cl:
                            self.show_doc(d)
                            self.set_type_filter(d, ty)
                    imgui.tree_pop()
        imgui.spacing()
        imgui.text_disabled('Folders')
        for i, root in enumerate(list(self.pinned())):
            self.folder_node(root, f'pin{i}', top=True)
        imgui.spacing()
        if imgui.small_button(f'{I.ICON_FA_FOLDER_PLUS}  Add a folder'):
            p = filedialog.pick_folder('Add a folder to the navigation pane (e.g. the game folder)')
            if p and p not in self.cfg['nav_folders']:
                self.cfg['nav_folders'].append(p)
                self.save_cfg()

    def folder_node(self, path, key, top=False):
        t = self.tab
        sel = isinstance(t, Browser) and t.loc == ('folder', path)
        label = (os.path.basename(path.rstrip('\\/')) or path)
        if top:
            label = f'{label}'
        opened, cl = self.nav_item(key, theme.FOLDER, theme.PALETTE['folder'], label, sel, leaf=False)
        if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
            imgui.set_tooltip(path)
        if cl:
            self.go_folder(path)
        if top and imgui.begin_popup_context_item(f'pinctx{key}'):
            if imgui.menu_item('Remove from the navigation pane', '', False)[0]:
                self.cfg['nav_folders'].remove(path)
                self.save_cfg()
            imgui.end_popup()
        if opened:
            for name, is_dir, *_ in folder_entries(path, self.folder_cache):
                if is_dir:
                    self.folder_node(os.path.join(path, name), f'{key}/{name}')
            imgui.tree_pop()

    # -- main view ----------------------------------------------------------------------------------------------
    def main_view(self):
        t = self.tab
        if t is None:
            return
        if self.cur is not None:
            if self.cfg.get('view') == 'icons':
                self.icons_view(self.cur)
            else:
                self.details_view(self.cur)
            self.background_menu(self.cur)
        elif t.loc[0] == 'home':
            self.home_view(t)
        else:
            self.folder_view(t)

    def home_view(self, t):
        imgui.spacing()
        imgui.text('Quick access')
        imgui.separator()
        pins = self.pinned()
        if not pins:
            imgui.text_disabled('Add the game folder with "Add a folder" in the navigation pane, or open a bundle.')
        for i, p in enumerate(pins):
            if i:
                imgui.same_line()
            imgui.begin_group()
            pos = imgui.get_cursor_pos()
            if imgui.selectable(f'##pin{i}', False, 0, imgui.ImVec2(230, 52))[0]:
                self.go_folder(p)
            imgui.set_cursor_pos(imgui.ImVec2(pos.x + 8, pos.y + 6))
            if theme.FONTS['big_icons'] is not None:
                imgui.push_font(theme.FONTS['big_icons'], 30.0)
            icon_text(theme.FOLDER, theme.PALETTE['folder'])
            if theme.FONTS['big_icons'] is not None:
                imgui.pop_font()
            imgui.set_cursor_pos(imgui.ImVec2(pos.x + 52, pos.y + 6))
            imgui.text(os.path.basename(p.rstrip('\\/')) or p)
            imgui.set_cursor_pos(imgui.ImVec2(pos.x + 52, pos.y + 26))
            imgui.text_disabled(ellipsis(p, 26))
            imgui.end_group()
        imgui.spacing()
        imgui.spacing()
        imgui.text('Resource names')
        imgui.separator()
        st = self.names.stats
        if st:
            imgui.text(f'{st["named"]:,} of {st["resources"]:,} resources have names ({st["exact"]:,} exact names from the game data).'.replace(',', ' '))
            imgui.text_disabled('Scanned: ' + '; '.join(self.names.scanned))
        else:
            imgui.text_wrapped('Bundles store resources by number only. "Find names" scans the game folders in the navigation pane '
                               '(add the PS3 prototype folder too, if you have it: its debug data names many retail resources) '
                               'and works out names for the Name column. It takes one or two minutes.')
        if imgui.button(f'{I.ICON_FA_MAGNIFYING_GLASS}  Find names' if not st else f'{I.ICON_FA_ROTATE_RIGHT}  Scan again') and self.job is None:
            self.action_find_names()
        imgui.spacing()
        imgui.spacing()
        imgui.text('Recent bundles')
        imgui.separator()
        rec = [p for p in self.cfg.get('recent', []) if os.path.exists(p) and (not t.search or t.search.lower() in p.lower())]
        flags = imgui.TableFlags_.resizable | imgui.TableFlags_.scroll_y | imgui.TableFlags_.pad_outer_x
        if rec and imgui.begin_table('recent', 3, flags):
            imgui.table_setup_column('Name', imgui.TableColumnFlags_.width_stretch, 2)
            imgui.table_setup_column('Location', imgui.TableColumnFlags_.width_stretch, 3)
            imgui.table_setup_column('Size', imgui.TableColumnFlags_.width_fixed, 90)
            imgui.table_headers_row()
            for i, p in enumerate(rec):
                imgui.table_next_row()
                imgui.table_next_column()
                if imgui.selectable(f'##rec{i}', False, imgui.SelectableFlags_.span_all_columns
                                    | imgui.SelectableFlags_.allow_double_click | imgui.SelectableFlags_.allow_overlap)[0]:
                    if imgui.is_mouse_double_clicked(0):
                        self.open_path(p)
                imgui.same_line(0, 0)
                icon_text(theme.BUNDLE, theme.BUNDLE_COL)
                imgui.same_line()
                imgui.text(os.path.basename(p))
                imgui.table_next_column()
                imgui.text_disabled(os.path.dirname(p))
                imgui.table_next_column()
                try:
                    imgui.text(short_size(os.path.getsize(p)))
                except OSError:
                    imgui.text_disabled('missing')
            imgui.end_table()
        elif not rec:
            imgui.text_disabled('Bundles you open show up here.')

    def folder_view(self, t):
        path = t.loc[1]
        ents = folder_entries(path, self.folder_cache)
        s = t.search.lower()
        if s:
            ents = [e for e in ents if s in e[0].lower()]
        if not ents:
            imgui.text_disabled('This folder has no subfolders or bundles.' if not s else 'No items match your search.')
        flags = (imgui.TableFlags_.resizable | imgui.TableFlags_.scroll_y | imgui.TableFlags_.pad_outer_x
                 | imgui.TableFlags_.hideable)
        if ents and imgui.begin_table('folder', 4, flags):
            imgui.table_setup_scroll_freeze(0, 1)
            imgui.table_setup_column('Name', imgui.TableColumnFlags_.width_stretch, 3)
            imgui.table_setup_column('Date modified', imgui.TableColumnFlags_.width_fixed, 140)
            imgui.table_setup_column('Type', imgui.TableColumnFlags_.width_fixed, 120)
            imgui.table_setup_column('Size', imgui.TableColumnFlags_.width_fixed, 90)
            imgui.table_headers_row()
            clipper = imgui.ListClipper()
            clipper.begin(len(ents))
            while clipper.step():
                for i in range(clipper.display_start, clipper.display_end):
                    name, is_dir, size, mt, plat = ents[i]
                    full = os.path.join(path, name)
                    imgui.table_next_row()
                    imgui.table_next_column()
                    sel = full in t.sel
                    if imgui.selectable(f'##f{i}', sel, imgui.SelectableFlags_.span_all_columns
                                        | imgui.SelectableFlags_.allow_double_click | imgui.SelectableFlags_.allow_overlap)[0]:
                        t.sel = {full}
                        if imgui.is_mouse_double_clicked(0):
                            if is_dir:
                                self.go_folder(full)
                            else:
                                self.open_path(full)
                    if not is_dir and imgui.begin_popup_context_item(f'fctx{i}'):
                        if imgui.menu_item('Open', '', False)[0]:
                            self.open_path(full)
                        if imgui.menu_item('Copy path', '', False)[0]:
                            imgui.set_clipboard_text(full)
                        imgui.end_popup()
                    imgui.same_line(0, 0)
                    if is_dir:
                        icon_text(theme.FOLDER, theme.PALETTE['folder'])
                    else:
                        icon_text(theme.BUNDLE, theme.BUNDLE_COL)
                    imgui.same_line()
                    imgui.text(name)
                    imgui.table_next_column()
                    imgui.text_disabled(time.strftime('%Y. %m. %d. %H:%M', time.localtime(mt)))
                    imgui.table_next_column()
                    imgui.text_disabled('File folder' if is_dir else f'Bundle ({plat})' if plat else 'Bundle')
                    imgui.table_next_column()
                    if not is_dir:
                        imgui.text(short_size(size))
            imgui.end_table()

    def item_events(self, d, rows, i, r):
        """Selection, double click, context menu and drag source of the item just drawn."""
        if imgui.is_item_clicked(0) or imgui.is_item_clicked(1):
            io = imgui.get_io()
            if imgui.is_item_clicked(1):
                if r.id not in d.sel:
                    d.sel = {r.id}
                    d.anchor = r.id
            elif io.key_shift and d.anchor is not None:
                ids = [x.id for x in rows]
                a = ids.index(d.anchor) if d.anchor in ids else i
                lo, hi = sorted((a, i))
                d.sel = set(ids[lo:hi + 1]) | (d.sel if io.key_ctrl else set())
            elif io.key_ctrl:
                d.sel ^= {r.id}
                d.anchor = r.id
            else:
                d.sel = {r.id}
                d.anchor = r.id
            self.focus = (d.uid, r.id)
            if imgui.is_mouse_double_clicked(0) and not self.cfg.get('preview', True):
                self.cfg['preview'] = True
        if imgui.begin_popup_context_item(f'ctx{r.id}'):
            self.focus = (d.uid, r.id)
            self.item_menu(d, r)
            imgui.end_popup()
        if imgui.begin_drag_drop_source():
            ids = sorted(d.sel) if r.id in d.sel else [r.id]
            self.drag = (d.uid, ids)
            self.drag_out_done = False
            imgui.set_drag_drop_payload_py_id(PAYLOAD, d.uid)
            ic, colr = theme.type_icon(r.type)
            icon_text(ic, colr)
            imgui.same_line()
            imgui.text(f'{len(ids)} items' if len(ids) > 1 else f'{ops.id_text(r.id)}')
            imgui.text_disabled('Copy to another bundle, or drop outside the window to save as files')
            imgui.end_drag_drop_source()

    def name_text(self, d, r, alpha=1.0):
        """The display name, coloured by how sure it is; tooltip with the full name."""
        nm, kind, tip = self.display_name(d, r)
        a = alpha * (1.0 if kind == 'exact' else 0.82 if kind == 'derived' else 0.6)
        self.faded_text(nm, a, dim=(kind == 'id'))
        if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
            src = {'exact': 'name from the game data', 'derived': 'name worked out from the data', 'id': ''}[kind]
            imgui.set_tooltip(f'{tip}\n{ops.id_text(r.id)}' + (f'\n{src}' if src else ''))

    def details_view(self, d):
        rows = self.visible_rows(d)
        flags = (imgui.TableFlags_.resizable | imgui.TableFlags_.scroll_y | imgui.TableFlags_.sortable
                 | imgui.TableFlags_.hideable | imgui.TableFlags_.reorderable | imgui.TableFlags_.pad_outer_x)
        if not imgui.begin_table('res2', 6, flags):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column('Name', imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.default_sort
                                 | imgui.TableColumnFlags_.no_hide, 3.0)
        imgui.table_setup_column('Id', imgui.TableColumnFlags_.width_fixed, 150)
        imgui.table_setup_column('Type', imgui.TableColumnFlags_.width_fixed, 120)
        imgui.table_setup_column('Description', imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.default_hide, 1.5)
        imgui.table_setup_column('Size', imgui.TableColumnFlags_.width_fixed | imgui.TableColumnFlags_.prefer_sort_descending, 70)
        imgui.table_setup_column('Imports', imgui.TableColumnFlags_.width_fixed | imgui.TableColumnFlags_.default_hide, 55)
        imgui.table_headers_row()
        specs = imgui.table_get_sort_specs()
        if specs is not None and specs.specs_dirty:
            if specs.specs_count:
                sp = specs.get_specs(0)
                d.sort = (sp.column_index, sp.sort_direction == imgui.SortDirection.ascending)
            specs.specs_dirty = False
            rows = self.visible_rows(d)
        self.scroll_into_view(d, rows, imgui.get_frame_height())
        cut = self.clip_cut_ids(d)
        clipper = imgui.ListClipper()
        clipper.begin(len(rows))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                r = rows[i]
                imgui.table_next_row()
                imgui.table_next_column()
                selected = r.id in d.sel
                renaming = self.renaming is not None and self.renaming[:2] == (d.uid, r.id)
                imgui.selectable(f'##r{r.id}', selected, imgui.SelectableFlags_.span_all_columns
                                 | imgui.SelectableFlags_.allow_overlap | imgui.SelectableFlags_.allow_double_click,
                                 imgui.ImVec2(0, imgui.get_frame_height() - 4))
                rmin, rmax = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                self.row_rects.append((rmin.y, rmax.y, r.id))
                self.item_events(d, rows, i, r)
                alpha = theme.PALETTE['cut'] if r.id in cut else 1.0
                imgui.same_line(0, 0)
                ic, colr = theme.type_icon(r.type)
                icon_text(ic, colr, alpha)
                imgui.same_line()
                self.name_text(d, r, alpha)
                imgui.table_next_column()
                if renaming:
                    self.rename_box(d, r)
                else:
                    self.faded_text(ops.id_text(r.id), alpha, dim=True)
                imgui.table_next_column()
                self.faded_text(type_name(r.type), alpha, dim=True)
                imgui.table_next_column()
                summ = self.summary(d, r)
                if r.name and N.GC_RE.match(r.name) is None and r.name != self.display_name(d, r)[2]:
                    self.faded_text(r.name, alpha, dim=True)
                else:
                    self.faded_text(summ, alpha, dim=True)
                imgui.table_next_column()
                size = r.size(0) + r.size(1) + r.size(2) + r.size(3)
                txt = short_size(size)
                imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x - imgui.calc_text_size(txt).x)
                self.faded_text(txt, alpha, dim=True)
                imgui.table_next_column()
                if r.import_count:
                    self.faded_text(str(r.import_count), alpha, dim=True)
        imgui.end_table()
        ys = sorted(r[0] for r in self.row_rects if len(r) == 3)
        if len(ys) > 2:
            self.row_pitch = (ys[-1] - ys[0]) / (len(ys) - 1)

    def faded_text(self, s, alpha=1.0, dim=False):
        c = imgui.get_style_color_vec4(imgui.Col_.text_disabled if dim else imgui.Col_.text)
        imgui.text_colored(imgui.ImVec4(c.x, c.y, c.z, c.w * alpha), s)

    def scroll_into_view(self, d, rows, row_h):
        """Scroll the table so the row of d.scroll_to is visible (row pitch measured on the last frame)."""
        target = getattr(d, 'scroll_to', None)
        if target is None:
            return
        pitch = getattr(self, 'row_pitch', 0) or (row_h + imgui.get_style().cell_padding.y * 2 - 4)
        idx = next((i for i, r in enumerate(rows) if r.id == target), None)
        if idx is not None:
            y = idx * pitch
            top = imgui.get_scroll_y()
            h = imgui.get_window_height() - row_h * 3
            if y < top or y > top + h:
                imgui.set_scroll_y(max(0.0, y - h / 2))
        d.scroll_to = None

    def icons_view(self, d):
        rows = self.visible_rows(d)
        tile_w, tile_h, box = 116.0, 138.0, 84.0
        avail = imgui.get_content_region_avail().x
        per = max(1, int((avail + 6) // (tile_w + 6)))
        n_lines = (len(rows) + per - 1) // per
        cut = self.clip_cut_ids(d)
        target = getattr(d, 'scroll_to', None)
        if target is not None:
            idx = next((i for i, r in enumerate(rows) if r.id == target), None)
            if idx is not None:
                imgui.set_scroll_y((idx // per) * (tile_h + 6))
            d.scroll_to = None
        clipper = imgui.ListClipper()
        clipper.begin(n_lines, tile_h + imgui.get_style().item_spacing.y)
        while clipper.step():
            for line in range(clipper.display_start, clipper.display_end):
                for k in range(per):
                    i = line * per + k
                    if i >= len(rows):
                        break
                    r = rows[i]
                    if k:
                        imgui.same_line(0, 6)
                    imgui.begin_group()
                    pos = imgui.get_cursor_pos()
                    imgui.push_id(f't{r.id}')
                    imgui.selectable('##tile', r.id in d.sel, imgui.SelectableFlags_.allow_double_click
                                     | imgui.SelectableFlags_.allow_overlap, imgui.ImVec2(tile_w, tile_h))
                    rmin, rmax = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                    self.row_rects.append((rmin.y, rmax.y, r.id, rmin.x, rmax.x))
                    if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
                        tip = [type_name(r.type), self.summary(d, r), human(sum(r.size(c) for c in range(4)))]
                        imgui.set_tooltip('\n'.join(t for t in tip if t))
                    self.item_events(d, rows, i, r)
                    imgui.pop_id()
                    fade = theme.PALETTE['cut'] if r.id in cut else 1.0
                    gl = None
                    if r.type == T_TEXTURE and not r.missing:
                        gl = self.thumbs.get((d.uid, r.id, id(r.data(0)), id(r.data(d.b.gfx_chunk))), r, d.b.platform)
                    if gl is not None:
                        imgui.set_cursor_pos(imgui.ImVec2(pos.x + (tile_w - box) / 2, pos.y + 6))
                        self.thumbs.draw(gl, box)
                    else:
                        ic, colr = theme.type_icon(r.type)
                        big = theme.FONTS['big_icons']
                        if big is not None:
                            imgui.push_font(big, 48.0)
                        sz = imgui.calc_text_size(ic)
                        imgui.set_cursor_pos(imgui.ImVec2(pos.x + (tile_w - sz.x) / 2, pos.y + 6 + (box - sz.y) / 2))
                        icon_text(ic, colr, fade)
                        if big is not None:
                            imgui.pop_font()
                    y = pos.y + box + 10
                    for ln in wrap2(self.display_name(d, r)[0], tile_w - 8):
                        w = imgui.calc_text_size(ln).x
                        imgui.set_cursor_pos(imgui.ImVec2(pos.x + (tile_w - w) / 2, y))
                        self.faded_text(ln, fade)
                        y += imgui.get_text_line_height()
                    imgui.set_cursor_pos(pos)
                    imgui.dummy(imgui.ImVec2(tile_w, tile_h))
                    imgui.end_group()

    def background_menu(self, d):
        if imgui.begin_popup_context_window('bgctx', imgui.PopupFlags_.mouse_button_right | imgui.PopupFlags_.no_open_over_items):
            if imgui.begin_menu(f'{I.ICON_FA_TABLE_CELLS_LARGE}  View'):
                self.view_items()
                imgui.end_menu()
            if imgui.begin_menu(f'{I.ICON_FA_ARROW_UP_WIDE_SHORT}  Sort by'):
                self.sort_items(d)
                imgui.end_menu()
            if imgui.menu_item(f'{I.ICON_FA_ROTATE_RIGHT}  Refresh', 'F5', False)[0]:
                self.refresh()
            imgui.separator()
            if imgui.menu_item(f'{I.ICON_FA_PASTE}  Paste', 'Ctrl+V', False, not d.b.truncated)[0]:
                self.clip_paste(d)
            if imgui.menu_item(f'{I.ICON_FA_ARROW_ROTATE_LEFT}  Undo', 'Ctrl+Z', False, bool(d.undo))[0]:
                self.undo(d)
            imgui.separator()
            if imgui.menu_item(f'{I.ICON_FA_FILE_IMPORT}  Import resources (.bres)...', '', False, not d.b.truncated)[0]:
                self.action_import_resources(d)
            if imgui.menu_item(f'{I.ICON_FA_FOLDER_OPEN}  Import from folder...', '', False, not d.b.truncated)[0]:
                self.action_import_folder(d)
            if imgui.menu_item(f'{I.ICON_FA_FILE_EXPORT}  Extract all...', '', False)[0]:
                self.action_extract(d)
            imgui.separator()
            if imgui.menu_item(f'{I.ICON_FA_CIRCLE_INFO}  Properties', 'Alt+Enter', False)[0]:
                self.modal = {'kind': 'properties', 'doc': d.uid}
            imgui.end_popup()

    def item_menu(self, d, r):
        ro = d.b.truncated
        sel = self.selected(d)
        if imgui.menu_item(f'{I.ICON_FA_EYE}  Open', 'Enter', False)[0]:
            self.cfg['preview'] = True
        imgui.separator()
        if imgui.menu_item(f'{I.ICON_FA_SCISSORS}  Cut', 'Ctrl+X', False, not ro)[0]:
            self.clip_copy(d, cut=True)
        if imgui.menu_item(f'{I.ICON_FA_COPY}  Copy', 'Ctrl+C', False)[0]:
            self.clip_copy(d)
        if imgui.menu_item(f'{I.ICON_FA_PEN}  Change id', 'F2', False, not ro)[0]:
            self.start_rename(d, r)
        if imgui.menu_item(f'{I.ICON_FA_TRASH_CAN}  Delete' + (f' ({len(sel)})' if len(sel) > 1 else ''), 'Del', False, not ro)[0]:
            self.modal = {'kind': 'confirm_delete', 'doc': d.uid, 'ids': [x.id for x in sel] or [r.id]}
        imgui.separator()
        if imgui.begin_menu(f'{I.ICON_FA_FILE_EXPORT}  Export'):
            self.export_items(d, r)
            imgui.end_menu()
        if imgui.menu_item(f'{I.ICON_FA_RIGHT_LEFT}  Replace from file...', '', False, not ro)[0]:
            self.action_replace(d, r)
        if imgui.begin_menu(f'{I.ICON_FA_FILE_ARROW_UP}  Replace chunk (.bin)', not ro):
            for k in range(4):
                if imgui.menu_item(f'Chunk {k}', '', False)[0]:
                    p = filedialog.open_file(f'Replace chunk {k}', self.cfg.get('last_import', ''), filedialog.BIN)
                    if p:
                        self.replace_from_file(d, r, p, {'chunk': k})
            imgui.end_menu()
        if imgui.menu_item(f'{I.ICON_FA_CLONE if hasattr(I, "ICON_FA_CLONE") else I.ICON_FA_COPY}  Duplicate with new id...', '', False, not ro)[0]:
            self.modal = {'kind': 'change_id', 'doc': d.uid, 'rid': r.id, 'text': f'{r.id:016X}', 'dup': True}
        others = [o for o in self.docs if o is not d]
        if imgui.begin_menu(f'{I.ICON_FA_SHARE if hasattr(I, "ICON_FA_SHARE") else I.ICON_FA_ARROW_RIGHT}  Copy to', bool(others)):
            for o in others:
                if imgui.menu_item(f'{o.name}  [{o.b.platform}]', '', False)[0]:
                    self.copy_between(d, o, [x.id for x in sel] or [r.id])
            imgui.end_menu()
        imgui.separator()
        if imgui.menu_item(f'{I.ICON_FA_LINK}  Find resources that use this', '', False)[0]:
            self.find_users(r.id)
        if imgui.menu_item(f'{I.ICON_FA_HASHTAG}  Copy id', '', False)[0]:
            imgui.set_clipboard_text('\n'.join(f'{x.id:016X}' for x in (sel or [r])))
        imgui.separator()
        if imgui.menu_item(f'{I.ICON_FA_CIRCLE_INFO}  Properties', 'Alt+Enter', False)[0]:
            self.cfg['preview'] = True
            self.test_tab = 1

    def export_items(self, d, r):
        ok = d is not None and r is not None
        if not ok:
            imgui.text_disabled('Select a resource first')
            return
        if r.type == T_TEXTURE:
            if imgui.menu_item('As DDS...', '', False)[0]:
                self.action_export(d, r, 'dds')
            if imgui.menu_item('As PNG...', '', False)[0]:
                self.action_export(d, r, 'png')
        if r.type == 0x52 and imgui.menu_item('As PNG (256 x 16)...', '', False)[0]:
            self.action_export(d, r, 'png')
        if r.type == 0x70 and imgui.menu_item('As text...', '', False)[0]:
            self.action_export(d, r, 'text')
        if r.type in (0x05, 0x51) and imgui.menu_item('As glTF (.glb) with textures...', '', False)[0]:
            self.action_export(d, r, 'glb')
        if r.type == 0x81 and imgui.menu_item('As WAV...', '', False)[0]:
            self.action_export(d, r, 'wav')
        if r.type == 0x201 and imgui.menu_item('As CSV (translations)...', '', False)[0]:
            self.action_export(d, r, 'csv')
        if imgui.menu_item('As resource file (.bres)...', '', False)[0]:
            self.action_export(d, r, 'bres')
        if imgui.begin_menu('Raw chunk (.bin)'):
            for k in range(4):
                if imgui.menu_item(f'Chunk {k} ({human(len(r.data(k)))})', '', False, bool(r.data(k)))[0]:
                    self.action_export(d, r, f'chunk{k}')
            imgui.end_menu()

    # -- rename (change id) -------------------------------------------------------------------------------------
    def start_rename(self, d, r):
        if d is None or r is None:
            return
        if self.cfg.get('view') == 'icons':
            self.modal = {'kind': 'change_id', 'doc': d.uid, 'rid': r.id, 'text': f'{r.id:016X}', 'dup': False}
        else:
            self.renaming = (d.uid, r.id, ops.id_text(r.id), True)
            d.scroll_to = r.id

    def rename_box(self, d, r):
        uid, rid, text, first = self.renaming
        if first:
            imgui.set_keyboard_focus_here()
            self.renaming = (uid, rid, text, False)
        imgui.set_next_item_width(-1)
        ch, text = imgui.input_text('##rename', text, imgui.InputTextFlags_.enter_returns_true
                                    | imgui.InputTextFlags_.chars_hexadecimal | imgui.InputTextFlags_.auto_select_all)
        if ch:
            self.renaming = None
            try:
                from .app import parse_id
                nid = parse_id(text)
                if nid != r.id:
                    self.action_change_id(d, r, nid)
            except ValueError as e:
                self.status = str(e)
        elif imgui.is_key_pressed(imgui.Key.escape) or (not first and imgui.is_item_deactivated()):
            self.renaming = None
        else:
            self.renaming = (uid, rid, text, False)

    # -- clipboard ----------------------------------------------------------------------------------------------
    def clip_cut_ids(self, d):
        c = self.clip
        return set(c['ids']) if c and c['cut'] and c['doc'] == d.uid else set()

    def clip_copy(self, d, cut=False):
        if d is None or not d.sel:
            return
        ids = sorted(d.sel)
        self.clip = {'doc': d.uid, 'ids': ids, 'cut': cut}
        text = '\n'.join(f'{i:016X}' for i in ids)
        n_files = 0
        if len(ids) <= 64:                      # also as files, so they can be pasted in Explorer
            folder = tempfile.mkdtemp(prefix='bndlx_clip_')
            self.clip_dir = folder
            paths = []
            ext = self.cfg.get('drag_texture_ext', '.png')
            for rid in ids:
                r = d.b.find(rid)
                if r is not None and not r.missing:
                    try:
                        paths.append(ops.export_native(d.b, r, folder, ext))
                    except Exception:
                        pass
            if paths and winclip.set_files(paths, self.hwnd, text):
                n_files = len(paths)
        if not n_files:
            imgui.set_clipboard_text(text)
        verb = 'Cut' if cut else 'Copied'
        self.status = f'{verb} {len(ids)} item(s).' + (' Paste them in another bundle (Ctrl+V), or as files in Explorer.' if n_files else '')

    def clip_paste(self, d):
        if d is None or d.b.truncated:
            return
        files = winclip.get_files(self.hwnd)
        own = getattr(self, 'clip_dir', None)
        if files and not (own and all(os.path.normcase(f).startswith(os.path.normcase(own)) for f in files)):
            self.paste_files(d, files)
            return
        c = self.clip
        if not c:
            self.status = 'Nothing to paste.'
            return
        src = self.doc_by_uid(c['doc'])
        if src is None:
            self.status = 'The bundle the items were copied from is closed.'
            return
        if src is d:
            if len(c['ids']) == 1:
                r = d.b.find(c['ids'][0])
                if r is not None:
                    self.modal = {'kind': 'change_id', 'doc': d.uid, 'rid': r.id, 'text': f'{r.id:016X}', 'dup': True}
            else:
                self.status = 'Ids are unique in a bundle: paste into another bundle, or duplicate one item at a time.'
            return
        self.copy_between(src, d, c['ids'])
        if c['cut']:
            src.checkpoint(f'cut {len(c["ids"])}', list(c['ids']))
            for rid in c['ids']:
                if d.b.find(rid) is not None:
                    src.b.remove(rid)
            self.changed(src)
            self.clip = None

    def paste_files(self, d, files):
        """Files copied in Explorer: bundles open, .bres add, <id>.<ext> replace; one file onto the selection."""
        with self.drop_lock:
            self.dropped.extend(files)
        self._paste_target = (d, self.focused()[1] if len(files) == 1 else None)

    # -- keyboard -----------------------------------------------------------------------------------------------
    def shortcuts(self):
        io = imgui.get_io()
        if self.modal is not None or self.busy is not None:
            return
        ctrl, shift, alt = io.key_ctrl, io.key_shift, io.key_alt
        d = self.cur
        k = lambda key: imgui.is_key_pressed(key, False)  # noqa: E731
        if ctrl and k(imgui.Key.o):
            self.action_open()
        elif ctrl and shift and k(imgui.Key.s):
            self.action_save_as(d)
        elif ctrl and k(imgui.Key.s):
            self.action_save(d)
        elif ctrl and k(imgui.Key.w) and self.tab is not None:
            self.close_tab(self.tab)
        elif ctrl and k(imgui.Key.t):
            b = Browser()
            self.tabs.append(b)
            self.tab = b
            self._select_tab = id(b)
        elif ctrl and (k(imgui.Key.l)) or (alt and k(imgui.Key.d)):
            self.addr_edit = self.address_text()
            self.addr_focus = True
        elif ctrl and shift and k(imgui.Key.f):
            self.modal = {'kind': 'find', 'text': self.find_text}
        elif ctrl and (k(imgui.Key.f) or k(imgui.Key.e)):
            self.search_focus = True
        elif ctrl and k(imgui.Key.g):
            self.modal = {'kind': 'goto', 'text': ''}
        elif alt and k(imgui.Key.left_arrow):
            self.go_back()
        elif alt and k(imgui.Key.right_arrow):
            self.go_forward()
        elif alt and k(imgui.Key.up_arrow):
            self.go_up()
        elif alt and k(imgui.Key.p):
            self.cfg['preview'] = not self.cfg.get('preview', True)
        elif alt and k(imgui.Key.enter) and d is not None:
            self.modal = {'kind': 'properties', 'doc': d.uid}
        elif k(imgui.Key.f5):
            self.refresh()
        elif k(imgui.Key.f1):
            from .app import HELP
            self.modal = {'kind': 'message', 'title': 'How to use', 'text': HELP}
        elif ctrl and shift and k(imgui.Key._2):
            self.cfg['view'] = 'icons'
        elif ctrl and shift and k(imgui.Key._6):
            self.cfg['view'] = 'details'
        elif ctrl and k(imgui.Key.z) and d is not None:
            self.undo(d)
        elif ctrl and k(imgui.Key.y) and d is not None:
            self.redo(d)
        if io.want_text_input or self.renaming is not None:
            return
        if k(imgui.Key.backspace):
            self.go_back()
        if d is None:
            return
        if k(imgui.Key.delete) and d.sel and not d.b.truncated:
            self.modal = {'kind': 'confirm_delete', 'doc': d.uid, 'ids': sorted(d.sel)}
        elif ctrl and k(imgui.Key.c):
            self.clip_copy(d)
        elif ctrl and k(imgui.Key.x) and not d.b.truncated:
            self.clip_copy(d, cut=True)
        elif ctrl and k(imgui.Key.v):
            self.clip_paste(d)
        elif ctrl and k(imgui.Key.a):
            d.sel = {r.id for r in self.visible_rows(d)}
        elif k(imgui.Key.f2) and self.focused()[1] is not None and not d.b.truncated:
            self.start_rename(d, self.focused()[1])
        elif k(imgui.Key.enter) and self.focused()[1] is not None:
            self.cfg['preview'] = True
        else:
            step = 0
            per = 1
            if self.cfg.get('view') == 'icons':
                per = max(1, len({x[3] for x in self.row_rects if len(x) > 3}))
            if imgui.is_key_pressed(imgui.Key.down_arrow):
                step = per
            elif imgui.is_key_pressed(imgui.Key.up_arrow):
                step = -per
            elif per > 1 and imgui.is_key_pressed(imgui.Key.right_arrow):
                step = 1
            elif per > 1 and imgui.is_key_pressed(imgui.Key.left_arrow):
                step = -1
            elif imgui.is_key_pressed(imgui.Key.page_down):
                step = 20 * per
            elif imgui.is_key_pressed(imgui.Key.page_up):
                step = -20 * per
            elif k(imgui.Key.home):
                step = -10 ** 9
            elif k(imgui.Key.end):
                step = 10 ** 9
            if step:
                rows = self.visible_rows(d)
                if rows:
                    ids = [r.id for r in rows]
                    cur = ids.index(self.focus[1]) if self.focus and self.focus[1] in ids else -1
                    i = min(max(cur + step, 0), len(ids) - 1)
                    d.sel = {ids[i]}
                    d.anchor = ids[i]
                    self.focus = (d.uid, ids[i])
                    d.scroll_to = ids[i]

    # -- status bar ---------------------------------------------------------------------------------------------
    def status_bar(self):
        d = self.cur
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + 2)
        if self.busy:
            imgui.progress_bar(self.busy[1], imgui.ImVec2(200, 0))
            imgui.same_line()
            imgui.text(self.busy[0])
            return
        if d is not None:
            rows = self.visible_rows(d)
            imgui.text(f'{len(rows)} items')
            if d.sel:
                sz = sum(r.size(0) + r.size(1) + r.size(2) + r.size(3) for r in d.b.resources if r.id in d.sel)
                imgui.same_line(0, 14)
                imgui.text_disabled('|')
                imgui.same_line(0, 14)
                imgui.text(f'{len(d.sel)} item{"s" if len(d.sel) > 1 else ""} selected  {human(sz)}')
            imgui.same_line(0, 14)
            imgui.text_disabled('|')
            imgui.same_line(0, 14)
        elif isinstance(self.tab, Browser) and self.tab.loc[0] == 'folder':
            n = len(folder_entries(self.tab.loc[1], self.folder_cache))
            imgui.text(f'{n} items')
            imgui.same_line(0, 14)
            imgui.text_disabled('|')
            imgui.same_line(0, 14)
        right_w = 70
        avail = imgui.get_content_region_avail().x - right_w
        msg = self.status
        while msg and imgui.calc_text_size(msg).x > avail and len(msg) > 4:
            msg = msg[:-5] + '…'
        imgui.text_disabled(msg)
        imgui.same_line(imgui.get_window_width() - right_w)
        imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0)
        imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(5, 1))
        v = self.cfg.get('view', 'details')
        for mode, ic, tip in (('details', I.ICON_FA_LIST, 'Details'), ('icons', I.ICON_FA_TABLE_CELLS_LARGE, 'Large icons')):
            if v == mode:
                imgui.push_style_color(imgui.Col_.button, imgui.get_style_color_vec4(imgui.Col_.button_active))
            if imgui.small_button(f'{ic}##v{mode}'):
                self.cfg['view'] = mode
                self.save_cfg()
            if v == mode:
                imgui.pop_style_color()
            if imgui.is_item_hovered():
                imgui.set_tooltip(tip)
            imgui.same_line(0, 2)
        imgui.new_line()
        imgui.pop_style_var(2)


def wrap2(text, width):
    """At most two lines that fit `width`, the second one shortened with an ellipsis."""
    words = text.replace('_', '_\u200b').split(' ')
    lines, cur = [], ''
    for w in words:
        cand = (cur + ' ' + w).strip()
        if imgui.calc_text_size(cand.replace('\u200b', '')).x <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    lines = [ln.replace('\u200b', '') for ln in lines]
    out = []
    for ln in lines[:2]:
        while imgui.calc_text_size(ln).x > width and len(ln) > 2:
            ln = ln[:-2] + '…'
        out.append(ln)
    if len(lines) > 2:
        ln = out[1]
        while imgui.calc_text_size(ln + '…').x > width and len(ln) > 1:
            ln = ln[:-1]
        out[1] = ln + '…'
    return out


def ellipsis(s, n):
    return s if len(s) <= n else '…' + s[-(n - 1):]
