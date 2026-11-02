"""Port window (mixed into the App): put the data of a PS3 bundle that the PC game can read into the game's own bundle.

Three steps (the source bundle, the game bundle, where to save), then "Scan the game and preview" shows exactly what
will be taken from the source and what stays as the game has it (nothing is written), and "Write" saves it. All the
decisions are made in port.py; this file only draws the window and starts the background jobs."""
from imgui_bundle import imgui

from . import filedialog, port
from .restypes import name as type_name

FILTERS = ('Everything', 'Taken from the source', 'Kept as the game has it', 'Only in the source')
GREEN = (0.45, 0.85, 0.5, 1.0)
RED = (1.0, 0.45, 0.4, 1.0)
ORANGE = (1.0, 0.75, 0.3, 1.0)
ACCENT = (0.55, 0.75, 1.0, 1.0)


def _col(c):
    return imgui.ImVec4(*c)


class PortUI:
    """Mixin for App: self.port_ui holds the window state (None = closed)."""

    # -- state -----------------------------------------------------------------------------------------------
    def open_port(self, d=None):
        """Open the window; a PS3 bundle that is open (d, or the current one) becomes the source."""
        ui = getattr(self, 'port_ui', None)
        if ui is None:
            ui = self.port_ui = {'src': '', 'dst': '', 'out': '', 'out_auto': True, 'lenient': False,
                                 'src_info': None, 'dst_info': None, 'result': None, 'notes': [], 'saved': None,
                                 'report': '', 'filter': 0, 'search': '', 'rows': None}
        d = d if d is not None else getattr(self, 'cur', None)
        if (d is not None and getattr(d, 'path', None) and getattr(d.b, 'platform', None) == 'PS3'
                and getattr(d.b, 'kind', '') != 'sps'):
            self.port_set(ui, 'src', d.path)
        ui['focus'] = True

    def port_set(self, ui, key, value):
        """Change one of the three file names; whatever was previewed or saved no longer applies."""
        ui[key] = value
        ui['result'] = ui['saved'] = ui['rows'] = None
        if key == 'src':
            ui['src_info'] = port.inspect(value)
            if not ui['dst']:
                guess = port.suggest_target(value)
                if guess:
                    self.port_set(ui, 'dst', guess)
        elif key == 'dst':
            ui['dst_info'] = port.inspect(value)
            if ui['out_auto'] and value:
                ui['out'] = port.suggest_output(value)
        else:
            ui['out_auto'] = False

    def port_preview(self):
        ui = self.port_ui
        if ui is None or self.job is not None:
            return
        src, dst, lenient = ui['src'], ui['dst'], ui['lenient']
        ui['result'] = ui['saved'] = ui['rows'] = None

        def job(progress):
            def status(text):
                if self.busy:
                    self.busy[0] = text
            return port.plan(src, dst, lenient=lenient, progress=progress, status=status)

        def done(out):
            ui['result'], ui['notes'] = out
            ui['filter'] = 0
            res = ui['result']
            self.status = (f'Port preview: {len(res.replaced)} resource(s) from the source, '
                           f'{len(res.kept)} stay as the game has them. Nothing is written yet.')

        self.run_job('Scanning the game and checking every resource...', job, done)

    def port_write(self):
        ui = self.port_ui
        if ui is None or ui['result'] is None or self.job is not None:
            return
        res, out = ui['result'], ui['out']

        def job(progress):
            return port.write(res, out)

        def done(report):
            ui['saved'], ui['report'] = out, report
            self.status = f'Saved the ported bundle: {out}'

        self.run_job('Saving the ported bundle...', job, done)

    # -- window ----------------------------------------------------------------------------------------------
    def port_window(self):
        ui = getattr(self, 'port_ui', None)
        if ui is None:
            return
        imgui.set_next_window_size(imgui.ImVec2(920, 700), imgui.Cond_.first_use_ever)
        if ui.pop('focus', False):
            imgui.set_next_window_focus()
        opened, keep = imgui.begin('Port into a game bundle###port', True)
        if opened:
            self.port_body(ui)
        imgui.end()
        if keep is False:
            self.port_ui = None

    def port_step(self, title, text):
        imgui.spacing()
        imgui.text_colored(_col(ACCENT), title)
        imgui.text_disabled(text)

    def port_path(self, ui, key, info_key, hint, title, save=False):
        busy = self.busy is not None
        imgui.set_next_item_width(-120)
        ch, v = imgui.input_text_with_hint(f'##port_{key}', hint, ui[key])
        if ch:
            self.port_set(ui, key, v)
        imgui.same_line()
        imgui.begin_disabled(busy)
        if imgui.button(f'Browse...##port_{key}', imgui.ImVec2(110, 0)):
            start = ui[key] or self.cfg.get('last_bundle', '')
            if save:
                p = filedialog.save_file(title, start, filedialog.BUNDLES, 'BNDL')
            else:
                p = filedialog.open_file(title, start, filedialog.BUNDLES)
            if p:
                self.port_set(ui, key, p)
        imgui.end_disabled()
        info = ui[info_key] if info_key else None
        if isinstance(info, str):
            imgui.text_colored(_col(RED), 'This file ' + info + '.')
        elif info:
            imgui.text_disabled(f'{info[0]} bundle, {info[1]} resources')

    def port_body(self, ui):
        busy = self.busy is not None
        imgui.text_wrapped('Take the data of a PS3 bundle that the PC game can use and put it into the PC game\'s own '
                           'bundle. The result has exactly the game bundle\'s resources (nothing is added or '
                           'removed); whatever cannot be converted safely stays as the game has it.')

        self.port_step('1.  Source bundle', 'The file with the data you want: the PS3 beta bundle. '
                                            'Keep it in its PS3 game folder so the PS3 data types can be found.')
        self.port_path(ui, 'src', 'src_info', 'the PS3 bundle (for example the beta ENVIRONMENT.BNDL)',
                       'Choose the source bundle (PS3)')
        self.port_step('2.  Game bundle', 'The same bundle of the game you want to play (PC). It decides which '
                                          'resources exist, and the game folder around it is scanned.')
        self.port_path(ui, 'dst', 'dst_info', 'the PC game\'s bundle of the same name',
                       'Choose the PC game bundle to port into')
        self.port_step('3.  Save the result as', 'A new file. The game bundle is never changed: copy the result into '
                                                 'the game yourself once you are happy with it.')
        self.port_path(ui, 'out', None, 'where to save the ported bundle', 'Save the ported bundle as', save=True)

        imgui.spacing()
        ch, v = imgui.checkbox('Also convert objects whose data the schema does not fully describe, '
                               'when that data is clearly float32 (a guess)', ui['lenient'])
        if ch:
            ui['lenient'] = v
            ui['result'] = ui['saved'] = ui['rows'] = None
        if imgui.is_item_hovered():
            imgui.set_tooltip('Some Genesys objects (for example the sky of an EnvironmentKeyframe) have a table of\n'
                              'floats that the schema does not list. Off: those objects stay as the game has them.\n'
                              'On: they are converted when every value is clearly a sensible float. It is a guess,\n'
                              'so the preview says which objects used it.')
        imgui.separator()

        todo = port.problems(ui['src'], ui['src_info'], ui['dst'], ui['dst_info'], ui['out'])
        res = ui['result']
        imgui.begin_disabled(bool(todo) or busy)
        if imgui.button('Scan the game and preview', imgui.ImVec2(230, 0)):
            self.port_preview()
        imgui.end_disabled()
        if imgui.is_item_hovered():
            imgui.set_tooltip('Reads the game folder (once; remembered afterwards) and shows what would be taken\n'
                              'from the source and what stays. Nothing is written.')
        imgui.same_line()
        imgui.begin_disabled(res is None or bool(todo) or busy)
        if imgui.button('Write the ported bundle', imgui.ImVec2(230, 0)):
            self.port_write()
        imgui.end_disabled()
        if todo:
            for t in todo[:4]:
                imgui.text_disabled(t)
        elif res is None:
            imgui.text_disabled('Next: Scan the game and preview. Nothing is written until you press Write.')
        else:
            self.port_results(ui, res)

    def port_rows(self, ui, res):
        key = (id(res), ui['filter'], ui['search'].strip().lower())
        if ui['rows'] is not None and ui['rows'][0] == key:
            return ui['rows'][1]
        want = (None, 'replace', 'keep', 'source-only')[ui['filter']]
        q = key[2]
        rows = []
        for d in res.decisions:
            if want and d.action != want:
                continue
            if q and q not in f'{d.id:#x} {type_name(d.type)} {d.reason} {d.note}'.lower():
                continue
            rows.append(d)
        ui['rows'] = (key, rows)
        return rows

    def port_results(self, ui, res):
        imgui.spacing()
        imgui.text_colored(_col(GREEN), f'{len(res.replaced)} resource(s) will come from the source bundle.')
        imgui.text(f'{len(res.kept)} stay exactly as the game has them.')
        if res.source_only:
            imgui.text_disabled(f'{len(res.source_only)} exist only in the source and are not added.')
        guessed = [d for d in res.replaced if 'float' in d.note]
        if guessed:
            imgui.text_colored(_col(ORANGE), f'{len(guessed)} object(s) used the float guess (see the table).')
        for w in res.warnings:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_col(ORANGE), 'Warning: ' + w)
            imgui.pop_text_wrap_pos()
        for n in ui['notes']:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_disabled(n)
            imgui.pop_text_wrap_pos()

        if ui['saved']:
            imgui.spacing()
            imgui.text_colored(_col(GREEN), f'Saved: {ui["saved"]}')
            imgui.text_disabled(f'Per-resource report: {ui["report"]}')
            imgui.push_text_wrap_pos(0.0)
            imgui.text('Next: copy this file over the game\'s bundle (keep a backup of the original), or open it '
                       'here to look at it.')
            imgui.pop_text_wrap_pos()
            if imgui.button('Open the result here'):
                self.open_path(ui['saved'])

        imgui.spacing()
        counts = (len(res.decisions), len(res.replaced), len(res.kept), len(res.source_only))
        for i, label in enumerate(FILTERS):
            if i:
                imgui.same_line()
            if imgui.radio_button(f'{label} ({counts[i]})##pf{i}', ui['filter'] == i):
                ui['filter'] = i
        imgui.set_next_item_width(-1)
        _, ui['search'] = imgui.input_text_with_hint('##port_search', 'search by id, type or reason', ui['search'])
        rows = self.port_rows(ui, res)
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
                 | imgui.TableFlags_.resizable)
        if imgui.begin_table('port_rows', 4, flags, imgui.ImVec2(0, -4)):
            imgui.table_setup_scroll_freeze(0, 1)
            imgui.table_setup_column('Resource', imgui.TableColumnFlags_.width_fixed, 150)
            imgui.table_setup_column('Type', imgui.TableColumnFlags_.width_fixed, 120)
            imgui.table_setup_column('Result', imgui.TableColumnFlags_.width_fixed, 150)
            imgui.table_setup_column('Why', imgui.TableColumnFlags_.width_stretch, 1.0)
            imgui.table_headers_row()
            clipper = imgui.ListClipper()
            clipper.begin(len(rows))
            while clipper.step():
                for i in range(clipper.display_start, clipper.display_end):
                    d = rows[i]
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.text(f'{d.id:#x}')
                    imgui.table_next_column()
                    imgui.text(type_name(d.type))
                    imgui.table_next_column()
                    if d.action == 'replace':
                        imgui.text_colored(_col(GREEN), 'from the source')
                    elif d.action == 'keep':
                        imgui.text('the game\'s own')
                    else:
                        imgui.text_disabled('not added')
                    imgui.table_next_column()
                    why = d.reason or d.note
                    if d.reason and d.note:
                        why += f' ({d.note})'
                    imgui.text_unformatted(why)
                    if imgui.is_item_hovered() and len(why) > 60:
                        imgui.set_tooltip(why)
            imgui.end_table()
