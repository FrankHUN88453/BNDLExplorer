"""Soundtrack editor window (mixed into the App): the game's songs and playlists from UI\\SONGS\\SONGS.BNDL;
add songs from audio files, edit artist / title, replace the audio, choose the playlists, reorder, remove, play,
then save (SONGS.BNDL, UI\\LANGUAGE\\*.BNDL and the .SPS files; originals kept as .orig)."""
import os
import re
import threading

from imgui_bundle import imgui

from . import eal3, filedialog, ops, theme
from . import soundtrack as ST


def _clock(seconds):
    seconds = max(0.0, seconds)
    return f'{int(seconds // 60)}:{int(seconds % 60):02d}'


def guess_names(path):
    """'Artist - Title.mp3' -> ('Artist', 'Title'); otherwise ('', file name)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r'^\d+[\s._-]+', '', stem).replace('_', ' ').strip()
    if ' - ' in stem:
        a, t = stem.split(' - ', 1)
        return a.strip(), t.strip()
    return '', stem


class SoundtrackUI:
    """Mixin for App: self.st_ui holds the window state (None = closed)."""

    def open_soundtrack(self, root=None):
        if root is None:
            d = self.cur
            root = ops.game_root(d.path) if d is not None and d.path else None
            if root is None or not os.path.isfile(os.path.join(root, 'UI', 'SONGS', 'SONGS.BNDL')):
                root = next((f for f in self.nav_roots() if os.path.isfile(os.path.join(f, 'UI', 'SONGS', 'SONGS.BNDL'))), None)
        if root is None:
            root = filedialog.pick_folder('The game folder (with UI\\SONGS\\SONGS.BNDL)')
            if not root:
                return
        try:
            model = ST.Soundtrack(root)
        except (ST.SoundtrackError, OSError) as ex:
            self.modal = {'kind': 'message', 'title': 'Soundtrack editor', 'text': str(ex)}
            return
        self.st_ui = {'model': model, 'view': -1, 'filter': '', 'edit': {}, 'confirm': None, 'audio': {}}

    def nav_roots(self):
        try:
            return list(self.pinned())
        except Exception:
            return []

    # -- actions ---------------------------------------------------------------------------------------------
    def st_add(self):
        paths = filedialog.open_files('Add songs (audio files: artist and title are taken from "Artist - Title" names)',
                                      self.cfg.get('last_import', ''), filedialog.AUDIO)
        if not paths:
            return
        self.remember('last_import', paths[0])
        m = self.st_ui['model']
        view = self.st_ui['view']
        lists = ST.MAIN_LISTS if view < 0 else (m.lists[view].fields['own'],)

        def job(progress):
            added, errors = [], []
            for i, p in enumerate(paths):
                progress(i, len(paths))
                artist, title = guess_names(p)
                try:
                    added.append(m.add_song(p, artist or 'Unknown artist', title, lists))
                except Exception as ex:
                    errors.append((p, str(ex)))
            return added, errors

        def done(res):
            added, errors = res
            text = f'{len(added)} song(s) added (not saved yet).'
            if errors:
                text += chr(10) * 2 + chr(10).join(f'{os.path.basename(p)}: {e}' for p, e in errors[:10])
                self.modal = {'kind': 'message', 'title': 'Add songs', 'text': text}
            self.status = text.split(chr(10))[0]

        self.run_job(f'Encoding {len(paths)} song(s)...', job, done)

    def st_replace(self, s):
        p = filedialog.open_file(f'New audio for {s.artist} - {s.title}', self.cfg.get('last_import', ''), filedialog.AUDIO)
        if not p:
            return
        self.remember('last_import', p)
        m = self.st_ui['model']
        self.st_ui['audio'].pop(s.rid, None)
        self.run_job('Encoding...', lambda pr: m.replace_audio(s, p),
                     lambda _: setattr(self, 'status', f'Audio of {s.title} replaced (not saved yet).'))

    def st_save(self):
        m = self.st_ui['model']

        def done(written):
            self.st_ui['audio'].clear()
            self.status = f'Soundtrack saved: {len(written)} file(s) written; originals kept as .orig.'

        self.run_job('Saving the soundtrack...', lambda pr: m.save(pr), done)

    def st_audio(self, s):
        """Decoded audio of a song for the preview (decoded on a thread; None while decoding)."""
        cache = self.st_ui['audio']
        hit = cache.get(s.rid)
        if hit is None:
            cache[s.rid] = 'decoding'

            def work():
                try:
                    data = s.new_sps if s.new_sps is not None else open(s.file, 'rb').read()
                    audio, rate, _ = eal3.decode_sps(data)
                    cache[s.rid] = (audio, rate)
                except Exception as ex:
                    cache[s.rid] = ex

            threading.Thread(target=work, daemon=True).start()
            return None
        return hit if isinstance(hit, tuple) else None

    # -- window ----------------------------------------------------------------------------------------------
    def soundtrack_window(self):
        ui = getattr(self, 'st_ui', None)
        if ui is None:
            return
        m = ui['model']
        imgui.set_next_window_size(imgui.ImVec2(980, 620), imgui.Cond_.first_use_ever)
        title = f'Soundtrack editor{" *" if m.dirty else ""} - {m.root}###soundtrack'
        opened, keep = imgui.begin(title, True)
        if opened:
            self.soundtrack_body(ui, m)
        imgui.end()
        if keep is False:
            if m.dirty and ui.get('confirm') != 'close':
                ui['confirm'] = 'close'
            else:
                self.soundtrack_close()
        if ui.get('confirm') == 'close':
            imgui.open_popup('Unsaved soundtrack changes')
        if imgui.begin_popup_modal('Unsaved soundtrack changes', None, imgui.WindowFlags_.always_auto_resize)[0]:
            imgui.text('The soundtrack has unsaved changes.')
            if imgui.button('Save'):
                self.st_save()
                ui['confirm'] = None
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button('Discard'):
                imgui.close_current_popup()
                self.soundtrack_close()
            imgui.same_line()
            if imgui.button('Cancel'):
                ui['confirm'] = None
                imgui.close_current_popup()
            imgui.end_popup()

    def soundtrack_close(self):
        if self.player.opened and self.player.key and self.player.key[0] == 'song':
            self.player.stop()
        self.st_ui = None

    def soundtrack_body(self, ui, m):
        busy = self.job is not None
        if imgui.button(f'{theme.I.ICON_FA_PLUS}  Add songs...') and not busy:
            self.st_add()
        if imgui.is_item_hovered():
            imgui.set_tooltip('WAV / FLAC / OGG / MP3 / AIFF / .SPS files; "Artist - Title" file names give the names.\n'
                              'New songs go into the playlist shown (All songs: the two soundtrack lists).')
        imgui.same_line()
        imgui.begin_disabled(not m.dirty or busy)
        if imgui.button(f'{theme.I.ICON_FA_FLOPPY_DISK}  Save'):
            self.st_save()
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button('Reload') and not busy:
            if self.player.opened and self.player.key and self.player.key[0] == 'song':
                self.player.stop()
            self.open_soundtrack(m.root)
            return
        imgui.same_line()
        imgui.set_next_item_width(260)
        names = ['All songs'] + [f'{li.name}  ({len(li.songs)})' for li in m.lists]
        ch, v = imgui.combo('##stview', ui['view'] + 1, names)
        if ch:
            ui['view'] = v - 1
        imgui.same_line()
        imgui.set_next_item_width(-1)
        _, ui['filter'] = imgui.input_text_with_hint('##stfilter', 'search artist or title', ui['filter'])
        view = m.lists[ui['view']] if 0 <= ui['view'] < len(m.lists) else None
        if view is None:
            songs = list(m.songs)
        else:
            songs = [m.song(r) for r in view.songs]
            songs = [s for s in songs if s is not None]
        f = ui['filter'].lower()
        if f:
            songs = [s for s in songs if f in s.artist.lower() or f in s.title.lower()]
        main = [li for li in m.lists if li.fields['own'] in ST.MAIN_LISTS]
        cols = 7 + (1 if view is not None else 0)
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
                 | imgui.TableFlags_.resizable)
        if imgui.begin_table('stsongs', cols, flags, imgui.ImVec2(0, -imgui.get_frame_height_with_spacing() * 1.2)):
            imgui.table_setup_scroll_freeze(0, 1)
            if view is not None:
                imgui.table_setup_column('Order', imgui.TableColumnFlags_.width_fixed, 70)
            imgui.table_setup_column('', imgui.TableColumnFlags_.width_fixed, 28)
            imgui.table_setup_column('Artist', imgui.TableColumnFlags_.width_stretch, 1.0)
            imgui.table_setup_column('Title', imgui.TableColumnFlags_.width_stretch, 1.4)
            imgui.table_setup_column('Length', imgui.TableColumnFlags_.width_fixed, 48)
            imgui.table_setup_column('Playlists', imgui.TableColumnFlags_.width_fixed, 150)
            imgui.table_setup_column('Trim', imgui.TableColumnFlags_.width_fixed, 60)
            imgui.table_setup_column('', imgui.TableColumnFlags_.width_fixed, 150)
            imgui.table_headers_row()
            for i, s in enumerate(songs):
                imgui.push_id(f'st{s.rid}')
                imgui.table_next_row()
                if view is not None:
                    imgui.table_next_column()
                    imgui.text(f'{i + 1}')
                    imgui.same_line()
                    if imgui.arrow_button('##up', imgui.Dir.up):
                        m.move(view, s.rid, -1)
                    imgui.same_line()
                    if imgui.arrow_button('##dn', imgui.Dir.down):
                        m.move(view, s.rid, 1)
                imgui.table_next_column()
                playing = self.player.opened and self.player.key == ('song', s.rid)
                if imgui.small_button(theme.I.ICON_FA_STOP if playing else theme.I.ICON_FA_PLAY):
                    if playing:
                        self.player.stop()
                    else:
                        ui['want_play'] = s.rid
                if ui.get('want_play') == s.rid:
                    hit = self.st_audio(s)
                    if hit is not None:
                        ui['want_play'] = None
                        try:
                            self.player.play(hit[0], hit[1], 0, ('song', s.rid))
                        except OSError as ex:
                            self.status = str(ex)
                    elif isinstance(ui['audio'].get(s.rid), Exception):
                        ui['want_play'] = None
                        self.status = f'Cannot play: {ui["audio"][s.rid]}'
                imgui.table_next_column()
                imgui.set_next_item_width(-1)
                fname = os.path.basename(s.file) if s.file else ''
                ch, txt = imgui.input_text_with_hint('##artist', '(no artist)', s.artist, imgui.InputTextFlags_.enter_returns_true)
                if ch:
                    m.set_text(s, artist=txt)
                imgui.table_next_column()
                imgui.set_next_item_width(-1)
                ch, txt = imgui.input_text_with_hint('##title', f'(untitled: {fname})', s.title, imgui.InputTextFlags_.enter_returns_true)
                if ch:
                    m.set_text(s, title=txt)
                if imgui.is_item_hovered() and fname:
                    imgui.set_tooltip(s.file)
                if s.added or s.new_sps is not None:
                    imgui.same_line()
                    imgui.text_disabled('new')
                imgui.table_next_column()
                imgui.text(_clock(s.duration))
                imgui.table_next_column()
                for k, li in enumerate(main):
                    on = s.rid in li.songs
                    ch, on2 = imgui.checkbox(f'##main{k}', on)
                    if imgui.is_item_hovered():
                        imgui.set_tooltip(li.name)
                    if ch:
                        m.set_member(s, li, on2)
                    imgui.same_line()
                if imgui.small_button('Lists...'):
                    imgui.open_popup('lists')
                if imgui.begin_popup('lists'):
                    for k, li in enumerate(m.lists):
                        ch, on2 = imgui.checkbox(f'{li.name} ({len(li.songs)})##l{k}', s.rid in li.songs)
                        if ch:
                            m.set_member(s, li, on2)
                    imgui.end_popup()
                imgui.table_next_column()
                imgui.set_next_item_width(-1)
                ch, tv = imgui.drag_float('##trim', s.fields['trim'], 0.01, -3.0, 3.0, '%.2f')
                if ch:
                    s.fields['trim'] = tv
                    m.dirty = True
                if imgui.is_item_hovered():
                    imgui.set_tooltip('Per-song value from the Song object (-0.9 .. 0.9 on the licensed songs, 0 on the\n'
                                      'others); most likely a loudness correction. New songs get 0.')
                imgui.table_next_column()
                if imgui.small_button('Replace audio...') and not busy:
                    self.st_replace(s)
                imgui.same_line()
                if imgui.small_button(theme.I.ICON_FA_TRASH_CAN):
                    imgui.open_popup('remove')
                if imgui.is_item_hovered():
                    imgui.set_tooltip('Remove this song from the game (from every playlist)')
                if imgui.begin_popup('remove'):
                    imgui.text(f'Remove {s.artist} - {s.title}?')
                    if imgui.button('Remove'):
                        if self.player.key == ('song', s.rid):
                            self.player.stop()
                        m.remove_song(s)
                        imgui.close_current_popup()
                    imgui.same_line()
                    if imgui.button('Cancel'):
                        imgui.close_current_popup()
                    imgui.end_popup()
                imgui.pop_id()
            imgui.end_table()
        n_new = sum(1 for s in m.songs if s.added)
        imgui.text_disabled(f'{len(m.songs)} songs, {len(m.lists)} playlists'
                            + (f', {n_new} new' if n_new else '') + ('  -  unsaved changes' if m.dirty else '')
                            + '.  Artist / title: Enter to apply.  The checkboxes are the two soundtrack playlists.')
