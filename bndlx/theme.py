"""Look and feel close to the Windows 11 File Explorer: colours, metrics, fonts and file-type icons."""
import os

from imgui_bundle import hello_imgui, icons_fontawesome_6 as fa, imgui

from .restypes import (T_CUBE, T_GOBJECT, T_GTYPE, T_STRINGS, T_TEXT, T_TEXTURE)

ACCENT = (0.0, 0.373, 0.722, 1.0)            # Windows 11 default accent #005FB8

LIGHT = {
    'bg': (0.953, 0.953, 0.953, 1), 'content': (1, 1, 1, 1), 'nav': (0.976, 0.976, 0.976, 1),
    'text': (0.106, 0.106, 0.106, 1), 'dim': (0.43, 0.43, 0.43, 1), 'border': (0.898, 0.898, 0.898, 1),
    'sel': (0.80, 0.91, 1.0, 1), 'hover': (0.898, 0.953, 1.0, 1), 'sel_hover': (0.75, 0.88, 1.0, 1),
    'frame': (1, 1, 1, 1), 'frame_hover': (0.98, 0.98, 0.98, 1), 'button': (0.953, 0.953, 0.953, 0),
    'button_hover': (0.0, 0.0, 0.0, 0.06), 'button_active': (0.0, 0.0, 0.0, 0.10), 'tab': (0.93, 0.93, 0.93, 1),
    'tab_sel': (1, 1, 1, 1), 'tab_hover': (0.965, 0.965, 0.965, 1), 'scroll': (0.55, 0.55, 0.55, 0.55),
    'popup': (0.985, 0.985, 0.985, 1), 'header': (1, 1, 1, 1), 'folder': (0.97, 0.76, 0.24, 1),
    'cut': 0.45,
}
DARK = {
    'bg': (0.125, 0.125, 0.125, 1), 'content': (0.098, 0.098, 0.098, 1), 'nav': (0.11, 0.11, 0.11, 1),
    'text': (1, 1, 1, 1), 'dim': (0.62, 0.62, 0.62, 1), 'border': (0.2, 0.2, 0.2, 1),
    'sel': (0.30, 0.30, 0.30, 1), 'hover': (0.18, 0.18, 0.18, 1), 'sel_hover': (0.35, 0.35, 0.35, 1),
    'frame': (0.17, 0.17, 0.17, 1), 'frame_hover': (0.21, 0.21, 0.21, 1), 'button': (0, 0, 0, 0),
    'button_hover': (1, 1, 1, 0.07), 'button_active': (1, 1, 1, 0.11), 'tab': (0.15, 0.15, 0.15, 1),
    'tab_sel': (0.2, 0.2, 0.2, 1), 'tab_hover': (0.18, 0.18, 0.18, 1), 'scroll': (0.6, 0.6, 0.6, 0.5),
    'popup': (0.17, 0.17, 0.17, 1), 'header': (0.098, 0.098, 0.098, 1), 'folder': (0.97, 0.76, 0.24, 1),
    'cut': 0.45,
}
PALETTE = LIGHT


def v4(c):
    return imgui.ImVec4(*c)


def col(name):
    return v4(PALETTE[name])


def apply(dark=False):
    global PALETTE
    PALETTE = DARK if dark else LIGHT
    p = PALETTE
    s = imgui.get_style()
    s.window_rounding = 0
    s.child_rounding = 6
    s.frame_rounding = 4
    s.popup_rounding = 8
    s.grab_rounding = 4
    s.tab_rounding = 6
    s.scrollbar_rounding = 6
    s.scrollbar_size = 12
    s.frame_border_size = 1
    s.window_border_size = 0
    s.child_border_size = 1
    s.popup_border_size = 1
    s.tab_border_size = 0
    s.frame_padding = imgui.ImVec2(8, 5)
    s.item_spacing = imgui.ImVec2(8, 6)
    s.item_inner_spacing = imgui.ImVec2(6, 4)
    s.cell_padding = imgui.ImVec2(8, 4)
    s.window_padding = imgui.ImVec2(10, 8)
    s.indent_spacing = 18
    s.selectable_text_align = imgui.ImVec2(0, 0.5)
    C = imgui.Col_
    sc = s.set_color_
    sc(C.text, v4(p['text']))
    sc(C.text_disabled, v4(p['dim']))
    sc(C.window_bg, v4(p['bg']))
    sc(C.child_bg, v4(p['content']))
    sc(C.popup_bg, v4(p['popup']))
    sc(C.border, v4(p['border']))
    sc(C.border_shadow, imgui.ImVec4(0, 0, 0, 0))
    sc(C.frame_bg, v4(p['frame']))
    sc(C.frame_bg_hovered, v4(p['frame_hover']))
    sc(C.frame_bg_active, v4(p['frame']))
    sc(C.title_bg, v4(p['bg']))
    sc(C.title_bg_active, v4(p['bg']))
    sc(C.menu_bar_bg, v4(p['bg']))
    sc(C.scrollbar_bg, imgui.ImVec4(0, 0, 0, 0))
    sc(C.scrollbar_grab, v4(p['scroll']))
    sc(C.scrollbar_grab_hovered, v4(p['dim']))
    sc(C.scrollbar_grab_active, v4(p['dim']))
    sc(C.check_mark, v4(ACCENT))
    sc(C.slider_grab, v4(ACCENT))
    sc(C.slider_grab_active, v4(ACCENT))
    sc(C.button, v4(p['button']))
    sc(C.button_hovered, v4(p['button_hover']))
    sc(C.button_active, v4(p['button_active']))
    sc(C.header, v4(p['sel']))
    sc(C.header_hovered, v4(p['hover']))
    sc(C.header_active, v4(p['sel_hover']))
    sc(C.separator, v4(p['border']))
    sc(C.separator_hovered, v4(ACCENT))
    sc(C.separator_active, v4(ACCENT))
    sc(C.resize_grip, imgui.ImVec4(0, 0, 0, 0))
    sc(C.resize_grip_hovered, v4(ACCENT))
    sc(C.resize_grip_active, v4(ACCENT))
    sc(C.tab, v4(p['tab']))
    sc(C.tab_hovered, v4(p['tab_hover']))
    sc(C.tab_selected, v4(p['tab_sel']))
    sc(C.tab_selected_overline, v4(p['tab_sel']))
    sc(C.tab_dimmed, v4(p['tab']))
    sc(C.tab_dimmed_selected, v4(p['tab_sel']))
    sc(C.table_header_bg, v4(p['header']))
    sc(C.table_border_strong, v4(p['border']))
    sc(C.table_border_light, v4(p['border']))
    sc(C.table_row_bg, imgui.ImVec4(0, 0, 0, 0))
    sc(C.table_row_bg_alt, imgui.ImVec4(0, 0, 0, 0))
    sc(C.text_selected_bg, v4((*ACCENT[:3], 0.35)))
    sc(C.drag_drop_target, v4(ACCENT))
    sc(C.nav_cursor, v4(ACCENT))
    sc(C.modal_window_dim_bg, imgui.ImVec4(0, 0, 0, 0.25))
    sc(C.plot_histogram, v4(ACCENT))


# ---------------------------------------------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------------------------------------------
FONTS = {'mono': None, 'big_icons': None}
WINFONTS = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')


def _icons(size):
    """Font Awesome 6 for our icons, then Font Awesome 4 for the glyphs the image viewer (immvision) uses."""
    for f in ('fonts/Font_Awesome_6_Free-Solid-900.otf', 'fonts/fontawesome-webfont.ttf'):
        try:
            hello_imgui.load_font(f, size, hello_imgui.FontLoadingParams(merge_to_last_font=True))
        except Exception:
            pass


def load_fonts():
    """Segoe UI (the system font, as Explorer uses it) + Font Awesome 6 icons; Consolas for hex."""
    seg = os.path.join(WINFONTS, 'segoeui.ttf')
    try:
        if os.path.exists(seg):
            hello_imgui.load_font(seg, 16.0, hello_imgui.FontLoadingParams(inside_assets=False))
        else:
            hello_imgui.load_font('fonts/DroidSans.ttf', 15.0)
    except Exception:
        hello_imgui.load_font('fonts/DroidSans.ttf', 15.0)
    _icons(14.0)
    mono = os.path.join(WINFONTS, 'consola.ttf')
    try:
        if os.path.exists(mono):
            FONTS['mono'] = hello_imgui.load_font(mono, 15.0, hello_imgui.FontLoadingParams(inside_assets=False))
        else:
            FONTS['mono'] = hello_imgui.load_font('fonts/Inconsolata-Medium.ttf', 15.0)
    except Exception:
        FONTS['mono'] = None
    try:
        FONTS['big_icons'] = hello_imgui.load_font('fonts/Font_Awesome_6_Free-Solid-900.otf', 44.0)
    except Exception:
        FONTS['big_icons'] = None


# ---------------------------------------------------------------------------------------------------------------
# icons
# ---------------------------------------------------------------------------------------------------------------
I = fa
TYPE_ICONS = {
    T_TEXTURE: (fa.ICON_FA_FILE_IMAGE, (0.16, 0.55, 0.87, 1)),
    0x002: (fa.ICON_FA_PALETTE, (0.73, 0.36, 0.73, 1)),
    0x003: (fa.ICON_FA_TABLE_LIST, (0.45, 0.45, 0.45, 1)),
    0x004: (fa.ICON_FA_GEARS, (0.45, 0.45, 0.45, 1)),
    0x005: (fa.ICON_FA_CUBE, (0.13, 0.60, 0.55, 1)),
    0x006: (fa.ICON_FA_GEAR, (0.45, 0.45, 0.45, 1)),
    0x007: (fa.ICON_FA_GEAR, (0.45, 0.45, 0.45, 1)),
    0x008: (fa.ICON_FA_CODE, (0.40, 0.40, 0.55, 1)),
    T_GTYPE: (fa.ICON_FA_SITEMAP, (0.52, 0.42, 0.82, 1)),
    T_GOBJECT: (fa.ICON_FA_FILE_CODE, (0.90, 0.52, 0.14, 1)),
    0x030: (fa.ICON_FA_FONT, (0.30, 0.30, 0.30, 1)),
    0x050: (fa.ICON_FA_MAP_LOCATION_DOT, (0.20, 0.60, 0.30, 1)),
    0x051: (fa.ICON_FA_CUBES, (0.13, 0.60, 0.55, 1)),
    T_CUBE: (fa.ICON_FA_SWATCHBOOK, (0.88, 0.33, 0.24, 1)),
    0x053: (fa.ICON_FA_CODE, (0.40, 0.40, 0.55, 1)),
    0x060: (fa.ICON_FA_SHAPES, (0.45, 0.45, 0.45, 1)),
    T_TEXT: (fa.ICON_FA_FILE_LINES, (0.35, 0.40, 0.48, 1)),
    0x074: (fa.ICON_FA_FILE_CODE, (0.25, 0.45, 0.75, 1)),
    0x080: (fa.ICON_FA_FILE_AUDIO, (0.82, 0.30, 0.52, 1)),
    0x081: (fa.ICON_FA_FILE_AUDIO, (0.82, 0.30, 0.52, 1)),
    0x082: (fa.ICON_FA_MUSIC, (0.82, 0.30, 0.52, 1)),
    0x0B0: (fa.ICON_FA_FILM, (0.30, 0.45, 0.70, 1)),
    0x0B1: (fa.ICON_FA_FILM, (0.30, 0.45, 0.70, 1)),
    0x0B2: (fa.ICON_FA_PERSON_WALKING, (0.30, 0.45, 0.70, 1)),
    0x0B3: (fa.ICON_FA_FILM, (0.30, 0.45, 0.70, 1)),
    0x106: (fa.ICON_FA_CAR, (0.85, 0.20, 0.20, 1)),
    0x105: (fa.ICON_FA_CAR, (0.85, 0.20, 0.20, 1)),
    0x207: (fa.ICON_FA_CAR, (0.82, 0.30, 0.52, 1)),
    T_STRINGS: (fa.ICON_FA_LANGUAGE, (0.18, 0.50, 0.82, 1)),
    0x203: (fa.ICON_FA_ROAD, (0.40, 0.40, 0.40, 1)),
    0x205: (fa.ICON_FA_MAP, (0.20, 0.60, 0.30, 1)),
    0x20F: (fa.ICON_FA_TREE, (0.20, 0.60, 0.30, 1)),
    0x210: (fa.ICON_FA_CAR_BURST, (0.90, 0.52, 0.14, 1)),
    0x214: (fa.ICON_FA_TREE, (0.20, 0.60, 0.30, 1)),
    0x303: (fa.ICON_FA_SHAPES, (0.45, 0.45, 0.45, 1)),
}
FOLDER = fa.ICON_FA_FOLDER
BUNDLE = fa.ICON_FA_BOX_ARCHIVE
BUNDLE_COL = (0.78, 0.55, 0.20, 1)


def type_icon(t):
    return TYPE_ICONS.get(t, (fa.ICON_FA_FILE, (0.55, 0.55, 0.55, 1)))


def icon_text(icon, colour, alpha=1.0):
    c = list(colour)
    c[3] *= alpha
    imgui.text_colored(imgui.ImVec4(*c), icon)
