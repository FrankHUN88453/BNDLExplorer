"""Dashboard interface used by the standard application startup."""
import os
import struct
from difflib import SequenceMatcher

import numpy as np
from PIL import Image
from imgui_bundle import hello_imgui, imgui, immvision

from . import app as app_module
from . import explorer, filedialog, hex_editor, ops
from . import theme
from .restypes import name as type_name

BG = imgui.ImVec4(0.012, 0.012, 0.016, 1)
PANEL = imgui.ImVec4(0.028, 0.028, 0.034, 1)
PANEL_ALT = imgui.ImVec4(0.055, 0.055, 0.064, 1)
LINE = imgui.ImVec4(0.13, 0.13, 0.15, 1)
TEXT = imgui.ImVec4(0.92, 0.92, 0.94, 1)
MUTED = imgui.ImVec4(0.49, 0.49, 0.54, 1)
BLUE = imgui.ImVec4(0.54, 0.48, 0.92, 1)
BLUE_HOVER = imgui.ImVec4(0.65, 0.59, 1.0, 1)
GREEN = imgui.ImVec4(0.18, 0.76, 0.56, 1)
GLOW = (0.68, 0.54, 0.98)
GLOW_LIGHT = (0.75, 0.65, 1.0)
LOADING_SCREEN_SECONDS = 3.2
LAUNCH_FADE_SECONDS = 0.9
LOADING_SURFACE = (0.105, 0.105, 0.105)
LOADING_SURFACE_RGB = tuple(round(channel * 255) for channel in LOADING_SURFACE)
HEX_TEXT_ENCODINGS = {
    'Windows (ANSI)': 'cp1252',
    'DOS/IBM-ASCII (OEM)': 'cp437',
    'Macintosh': 'mac_roman',
    'EBCDIC': 'cp037',
}
ORIGINAL_ICONS = {
    'home': theme.I.ICON_FA_HOUSE,
    'folder': theme.FOLDER,
    'bundle': theme.BUNDLE,
    'file': theme.I.ICON_FA_FILE,
    'text': theme.I.ICON_FA_FILE_LINES,
    'object': theme.I.ICON_FA_FILE_CODE,
    'schema': theme.I.ICON_FA_SITEMAP,
    'image': theme.I.ICON_FA_FILE_IMAGE,
    'sound': theme.I.ICON_FA_FILE_AUDIO,
    'cube': theme.I.ICON_FA_CUBE,
    'motion': theme.I.ICON_FA_FILM,
    'vehicle': theme.I.ICON_FA_CAR,
    'map': theme.I.ICON_FA_LOCATION_DOT,
    'save': theme.I.ICON_FA_FLOPPY_DISK,
    'import': theme.I.ICON_FA_FILE_IMPORT,
    'export': theme.I.ICON_FA_FILE_EXPORT,
    'replace': theme.I.ICON_FA_ARROWS_ROTATE,
    'delete': theme.I.ICON_FA_TRASH,
    'more': theme.I.ICON_FA_ELLIPSIS,
}
ORIGINAL_ICON_COLORS = {
    'home': BLUE,
    'folder': imgui.ImVec4(*theme.PALETTE['folder']),
    'bundle': imgui.ImVec4(*theme.BUNDLE_COL),
    'file': MUTED,
    'text': MUTED,
    'object': MUTED,
    'schema': MUTED,
    'image': BLUE,
    'sound': imgui.ImVec4(0.82, 0.30, 0.52, 1),
    'cube': imgui.ImVec4(0.13, 0.60, 0.55, 1),
    'motion': imgui.ImVec4(0.30, 0.45, 0.70, 1),
    'vehicle': imgui.ImVec4(0.30, 0.45, 0.70, 1),
    'map': imgui.ImVec4(0.20, 0.60, 0.30, 1),
    'save': MUTED,
    'import': MUTED,
    'export': MUTED,
    'replace': MUTED,
    'delete': MUTED,
    'more': MUTED,
}


def apply_style(theme_name=None):
    global BG, PANEL, PANEL_ALT, LINE, TEXT, MUTED, BLUE, BLUE_HOVER, GLOW, GLOW_LIGHT
    if theme_name is not None:
        theme.select(theme_name)
    if theme.ACTIVE_THEME == 'Blue':
        BG = imgui.ImVec4(0.012, 0.014, 0.020, 1)
        PANEL = imgui.ImVec4(0.022, 0.030, 0.043, 1)
        PANEL_ALT = imgui.ImVec4(0.045, 0.058, 0.078, 1)
        LINE = imgui.ImVec4(0.12, 0.16, 0.22, 1)
        TEXT = imgui.ImVec4(0.92, 0.95, 0.99, 1)
        MUTED = imgui.ImVec4(0.49, 0.56, 0.66, 1)
        BLUE = imgui.ImVec4(*theme.ACCENT)
        BLUE_HOVER = imgui.ImVec4(0.36, 0.64, 1.0, 1)
        GLOW = (0.30, 0.56, 0.94)
        GLOW_LIGHT = (0.58, 0.75, 1.0)
    elif theme.ACTIVE_THEME == 'White':
        BG = imgui.ImVec4(0.012, 0.012, 0.016, 1)
        PANEL = imgui.ImVec4(0.028, 0.028, 0.034, 1)
        PANEL_ALT = imgui.ImVec4(0.055, 0.055, 0.064, 1)
        LINE = imgui.ImVec4(0.16, 0.16, 0.18, 1)
        TEXT = imgui.ImVec4(0.95, 0.95, 0.97, 1)
        MUTED = imgui.ImVec4(0.55, 0.55, 0.59, 1)
        BLUE = imgui.ImVec4(*theme.ACCENT)
        BLUE_HOVER = imgui.ImVec4(1.0, 1.0, 1.0, 1)
        GLOW = (0.86, 0.88, 0.96)
        GLOW_LIGHT = (0.96, 0.97, 1.0)
    else:
        BG = imgui.ImVec4(0.012, 0.012, 0.016, 1)
        PANEL = imgui.ImVec4(0.028, 0.028, 0.034, 1)
        PANEL_ALT = imgui.ImVec4(0.055, 0.055, 0.064, 1)
        LINE = imgui.ImVec4(0.13, 0.13, 0.15, 1)
        TEXT = imgui.ImVec4(0.92, 0.92, 0.94, 1)
        MUTED = imgui.ImVec4(0.49, 0.49, 0.54, 1)
        BLUE = imgui.ImVec4(*theme.ACCENT)
        BLUE_HOVER = imgui.ImVec4(0.65, 0.59, 1.0, 1)
        GLOW = (0.68, 0.54, 0.98)
        GLOW_LIGHT = (0.75, 0.65, 1.0)
    ORIGINAL_ICON_COLORS['home'] = BLUE
    ORIGINAL_ICON_COLORS['image'] = BLUE
    ORIGINAL_ICON_COLORS['folder'] = imgui.ImVec4(*theme.PALETTE['folder'])
    ORIGINAL_ICON_COLORS['bundle'] = imgui.ImVec4(*theme.BUNDLE_COL)

    style = imgui.get_style()
    style.window_rounding = 0
    style.child_rounding = 12
    style.frame_rounding = 8
    style.popup_rounding = 10
    style.grab_rounding = 5
    style.tab_rounding = 7
    style.scrollbar_rounding = 7
    style.scrollbar_size = 9
    style.frame_border_size = 1
    style.window_border_size = 0
    style.child_border_size = 1
    style.popup_border_size = 1
    style.frame_padding = imgui.ImVec2(9, 6)
    style.item_spacing = imgui.ImVec2(8, 6)
    style.item_inner_spacing = imgui.ImVec2(6, 4)
    style.cell_padding = imgui.ImVec2(8, 5)
    style.window_padding = imgui.ImVec2(12, 10)

    colors = {
        imgui.Col_.text: TEXT,
        imgui.Col_.text_disabled: MUTED,
        imgui.Col_.window_bg: BG,
        imgui.Col_.child_bg: PANEL,
        imgui.Col_.popup_bg: PANEL_ALT,
        imgui.Col_.border: LINE,
        imgui.Col_.frame_bg: PANEL_ALT,
        imgui.Col_.frame_bg_hovered: PANEL_ALT,
        imgui.Col_.frame_bg_active: PANEL_ALT,
        imgui.Col_.title_bg: BG,
        imgui.Col_.title_bg_active: BG,
        imgui.Col_.menu_bar_bg: BG,
        imgui.Col_.scrollbar_bg: BG,
        imgui.Col_.scrollbar_grab: imgui.ImVec4(0.27, 0.31, 0.38, 0.72),
        imgui.Col_.scrollbar_grab_hovered: MUTED,
        imgui.Col_.scrollbar_grab_active: BLUE,
        imgui.Col_.check_mark: BLUE,
        imgui.Col_.slider_grab: BLUE,
        imgui.Col_.slider_grab_active: BLUE_HOVER,
        imgui.Col_.button: PANEL_ALT,
        imgui.Col_.button_hovered: imgui.ImVec4(0.10, 0.12, 0.16, 1),
        imgui.Col_.button_active: imgui.ImVec4(0.13, 0.17, 0.23, 1),
        imgui.Col_.header: PANEL_ALT,
        imgui.Col_.header_hovered: imgui.ImVec4(0.10, 0.13, 0.18, 1),
        imgui.Col_.header_active: imgui.ImVec4(0.14, 0.19, 0.27, 1),
        imgui.Col_.separator: LINE,
        imgui.Col_.separator_hovered: BLUE,
        imgui.Col_.separator_active: BLUE,
        imgui.Col_.tab: PANEL,
        imgui.Col_.tab_hovered: PANEL_ALT,
        imgui.Col_.tab_selected: PANEL_ALT,
        imgui.Col_.tab_selected_overline: BLUE,
        imgui.Col_.tab_dimmed: PANEL,
        imgui.Col_.tab_dimmed_selected: PANEL_ALT,
        imgui.Col_.table_header_bg: PANEL_ALT,
        imgui.Col_.table_border_strong: LINE,
        imgui.Col_.table_border_light: LINE,
        imgui.Col_.table_row_bg: BG,
        imgui.Col_.table_row_bg_alt: PANEL,
        imgui.Col_.text_selected_bg: imgui.ImVec4(BLUE.x, BLUE.y, BLUE.z, 0.36),
        imgui.Col_.drag_drop_target: BLUE,
        imgui.Col_.nav_cursor: BLUE,
        imgui.Col_.modal_window_dim_bg: imgui.ImVec4(0, 0, 0, 0.66),
        imgui.Col_.plot_histogram: BLUE,
    }
    for key, value in colors.items():
        style.set_color_(key, value)


def tint_loading_logo(source, color):
    """Recolor the red logo while preserving its antialiased edges and gray matte."""
    pixels = source.astype(np.float32)
    coverage = np.clip(
        (pixels[..., 0] - LOADING_SURFACE_RGB[0]) / (254 - LOADING_SURFACE_RGB[0]),
        0.0,
        1.0,
    )[..., None]
    tint = np.asarray(color, dtype=np.float32).reshape(1, 1, 3) * 255
    background = np.asarray(LOADING_SURFACE_RGB, dtype=np.float32).reshape(1, 1, 3)
    return np.rint(background + (tint - background) * coverage).astype(np.uint8)


class Dashboard:
    """Dashboard shell using App's existing bundle, editor, and preview operations."""

    def __init__(self, app):
        self.app = app
        self.deferred = []               # navigation asked for while drawing: done at the start of the next frame
        self.hex_links = {}              # Hex Editor file of a resource chunk -> (doc uid, resource id, chunk)
        self.app.dashboard = self
        self.app.dashboard_icons = False
        self.styled = False
        self.loading_screen_enabled = True
        self.loading_screen_started = None
        self.loading_logo = None
        self.loading_logo_source = None
        self.loading_logo_color = None
        self.launch_fade_enabled = True
        self.launch_fade_started = None
        self.search = ''
        self.hover_amounts = {}
        self.surface_hover_amounts = {}
        self.ambient_light_origin = imgui.ImVec2(0, 0)
        self.click_effects = {}
        self.content_token = None
        self.folder_entered_at = 0.0
        self.folder_entries_rendered = []
        self.folder_exit_entries = []
        self.folder_exit_started = 0.0
        self.content_transition_started = 0.0
        self.inspector_resource_token = None
        self.inspector_resource_started = 0.0
        self.resource_entered_at = 0.0
        self.resource_entries_rendered = []
        self.resource_exit_entries = []
        self.resource_exit_started = 0.0
        self.category_token = None
        self.category_entered_at = 0.0
        self.nav_indicator_key = None
        self.nav_indicator_rect = None
        self.nav_target_rect = None
        self.nav_transition_from = None
        self.nav_transition_started = 0.0
        self.menu_group_started = {}
        self.menu_group_indices = {}
        self.menu_frame_items = []
        self.menu_last_items = []
        self.menu_last_groups = {}
        self.menu_group_exits = {}
        self.menu_exit_items = []
        self.menu_was_open = False
        self.menu_exit_started = 0.0
        self.menu_popup_rect = None
        self.menu_exit_rect = None
        self.workspace = 'bundles'
        self.workspace_transition_started = 0.0
        self.inspector_expanded = False
        self.dashboard_sidebar_width = 280.0
        self.sidebar_collapsed = False
        self.dashboard_inspector_width = 380.0
        self.hex_documents = []
        self.hex_document = None
        self.hex_inspector_width = 270.0
        self.hex_offset_input = '0'
        self.hex_edit_input = ''
        self.hex_selection_anchor = 0
        self.hex_cell_buffers = {}
        self.hex_ascii_buffers = {}
        self.hex_text_encoding = 'Windows (ANSI)'
        self.hex_offset_base = 'Hexadecimal'
        self.hex_visible_columns = 'Hex and text'
        self.hex_group_size = 1
        self.hex_bytes_per_row = 16
        self.hex_display_bytes_per_row = 16
        self.hex_fit_width = False
        self.hex_show_file_actions = True
        self.hex_show_edit_actions = True
        self.hex_show_analysis_actions = True
        self.hex_inspector_visible = True
        self.hex_results_visible = False
        self.hex_search_input = ''
        self.hex_replace_input = ''
        self.hex_search_results = []
        self.hex_search_pattern = b''
        self.hex_results_visible = False
        self.hex_search_mode = 'HEX'
        self.hex_search_reverse = False
        self.hex_overwrite = True
        self.hex_match = None
        self.hex_analysis = None
        self.hex_analysis_document = None
        self.hex_analysis_revision = -1
        self.hex_status = ''
        self.hex_crc_polynomial = '1021'
        self.hex_crc_initial = 'FFFF'
        self.hex_crc_xor_out = '0000'
        self.app.dashboard_ui = self

    @staticmethod
    def color(r, g, b, a):
        return imgui.get_color_u32(imgui.ImVec4(r, g, b, a))

    @staticmethod
    def original_icon_data(kind, type_id=None):
        if type_id is not None:
            glyph, color = theme.type_icon(type_id)
            return glyph, imgui.ImVec4(*color)
        return ORIGINAL_ICONS.get(kind, theme.I.ICON_FA_FILE), ORIGINAL_ICON_COLORS.get(kind, MUTED)

    def draw_original_icon(self, draw_list, kind, center, color=None, type_id=None, alpha=1.0):
        glyph, default_color = self.original_icon_data(kind, type_id)
        if color is None:
            color = default_color
        text_size = imgui.calc_text_size(glyph)
        tint = imgui.ImVec4(color.x, color.y, color.z, color.w * alpha)
        draw_list.add_text(
            imgui.ImVec2(center.x - text_size.x * 0.5, center.y - text_size.y * 0.5),
            imgui.get_color_u32(tint),
            glyph,
        )

    @staticmethod
    def original_icon_label(kind, label, type_id=None):
        glyph, _ = Dashboard.original_icon_data(kind, type_id)
        return f'{glyph}  {label}'

    def glass_surface(self, pos, size, rounding=12, opacity=1.0, key=None):
        draw = imgui.get_window_draw_list()
        end = imgui.ImVec2(pos.x + size.x, pos.y + size.y)
        key = key or f'surface:{round(pos.x)}:{round(pos.y)}'
        mouse = imgui.get_mouse_pos()
        hovered = pos.x <= mouse.x <= end.x and pos.y <= mouse.y <= end.y
        amount = self.surface_hover_amounts.get(key, 0.0)
        dt = min(imgui.get_io().delta_time, 0.05)
        amount += ((1.0 if hovered else 0.0) - amount) * min(1.0, dt * 5.0)
        self.surface_hover_amounts[key] = amount
        surface_center = imgui.ImVec2((pos.x + end.x) * 0.5, (pos.y + end.y) * 0.5)
        light_distance = np.hypot(
            surface_center.x - self.ambient_light_origin.x,
            surface_center.y - self.ambient_light_origin.y,
        )
        light_amount = max(0.0, 1.0 - light_distance / 1250.0)
        border_light = 0.10 + light_amount * 0.22 + amount * 0.08
        draw.add_rect_filled(pos, end, self.color(0.018, 0.018, 0.024, 0.96 * opacity), rounding)
        draw.add_rect(
            pos,
            end,
            self.color(
                0.27 + GLOW[0] * light_amount * 0.28,
                0.25 + GLOW[1] * light_amount * 0.24,
                0.32 + GLOW[2] * light_amount * 0.25,
                border_light * opacity,
            ),
            rounding,
            1.0,
        )
        inset = rounding + 5
        left = pos.x + inset
        right = end.x - inset
        if right > left:
            draw.add_line(
                imgui.ImVec2(left, pos.y + 1),
                imgui.ImVec2(right, pos.y + 1),
                self.color(
                    GLOW_LIGHT[0], GLOW_LIGHT[1], GLOW_LIGHT[2],
                    (0.035 + light_amount * 0.09 + amount * 0.025) * opacity,
                ),
                1.0,
            )

    def draw_frame_light(self, pos, size, rounding=12):
        draw = imgui.get_window_draw_list()
        right = pos.x + size.x
        bottom = pos.y + size.y
        source = self.ambient_light_origin
        pulse = 0.94 + 0.06 * (0.5 + 0.5 * np.sin(imgui.get_time() * 0.38))
        inset = max(3.0, rounding * 0.72)
        top_start = imgui.ImVec2(pos.x + inset, pos.y + 1)
        top_end = imgui.ImVec2(right - inset, pos.y + 1)
        left_start = imgui.ImVec2(pos.x + 1, pos.y + inset)
        left_end = imgui.ImVec2(pos.x + 1, bottom - inset)
        right_start = imgui.ImVec2(right - 1, pos.y + inset)
        right_end = imgui.ImVec2(right - 1, bottom - inset)
        bottom_start = imgui.ImVec2(pos.x + inset, bottom - 1)
        bottom_end = imgui.ImVec2(right - inset, bottom - 1)

        def edge(start, end, segments, edge_scale):
            for index in range(segments):
                t0 = index / segments
                t1 = (index + 1) / segments
                p0 = imgui.ImVec2(
                    start.x + (end.x - start.x) * t0,
                    start.y + (end.y - start.y) * t0,
                )
                p1 = imgui.ImVec2(
                    start.x + (end.x - start.x) * t1,
                    start.y + (end.y - start.y) * t1,
                )
                midpoint = imgui.ImVec2((p0.x + p1.x) * 0.5, (p0.y + p1.y) * 0.5)
                distance = np.hypot(midpoint.x - source.x, midpoint.y - source.y)
                source_strength = np.exp(-distance / 1900.0)
                alpha = (0.025 + 0.34 * source_strength) * edge_scale * pulse
                draw.add_line(
                    p0,
                    p1,
                    self.color(GLOW_LIGHT[0], GLOW_LIGHT[1], GLOW_LIGHT[2], alpha),
                    1.4,
                )
                if edge_scale >= 0.8:
                    glow_alpha = alpha * 0.42
                    horizontal = abs(end.x - start.x) >= abs(end.y - start.y)
                    glow_p0 = imgui.ImVec2(
                        p0.x,
                        p0.y + 2 if horizontal else p0.y,
                    )
                    glow_p1 = imgui.ImVec2(
                        p1.x,
                        p1.y + 2 if horizontal else p1.y,
                    )
                    draw.add_line(
                        glow_p0,
                        glow_p1,
                        self.color(GLOW[0], GLOW[1], GLOW[2], glow_alpha),
                        3.0,
                    )

        edge(top_start, top_end, max(16, int(size.x / 12)), 1.0)
        edge(left_start, left_end, max(12, int(size.y / 14)), 0.92)
        edge(right_start, right_end, max(12, int(size.y / 14)), 0.40)
        edge(bottom_start, bottom_end, max(16, int(size.x / 12)), 0.30)

    def drag_splitter(self, key, height):
        width = 8.0
        imgui.invisible_button(f'##splitter-{key}', imgui.ImVec2(width, height))
        minimum = imgui.get_item_rect_min()
        maximum = imgui.get_item_rect_max()
        hovered = imgui.is_item_hovered()
        active = imgui.is_item_active()
        draw = imgui.get_window_draw_list()
        draw.add_rect_filled(
            imgui.ImVec2(minimum.x + 3, minimum.y + 5),
            imgui.ImVec2(maximum.x - 3, maximum.y - 5),
            self.color(BLUE.x, BLUE.y, BLUE.z, 0.52 if active else 0.28 if hovered else 0.14),
            3,
        )
        if hovered or active:
            draw.add_rect_filled(
                minimum,
                maximum,
                self.color(BLUE.x, BLUE.y, BLUE.z, 0.26 if active else 0.15),
                4,
            )
        center_y = (minimum.y + maximum.y) * 0.5
        for offset in (-6, 0, 6):
            draw.add_circle_filled(
                imgui.ImVec2((minimum.x + maximum.x) * 0.5, center_y + offset),
                1.2,
                self.color(BLUE.x, BLUE.y, BLUE.z, 0.82 if hovered or active else 0.42),
            )
        if active:
            return imgui.get_io().mouse_delta.x
        return 0.0

    def ambient_background(self):
        draw = imgui.get_window_draw_list()
        pos = imgui.get_window_pos()
        size = imgui.get_window_size()
        now = imgui.get_time()
        width, height = size.x, size.y
        self.ambient_light_origin = imgui.ImVec2(pos.x + 8, pos.y + 6)
        pulse = 0.5 + 0.5 * np.sin(now * 0.38)
        draw.add_rect_filled_multi_color(
            pos, imgui.ImVec2(pos.x + width, pos.y + height),
            self.color(GLOW[0] * 0.63, GLOW[1] * 0.61, GLOW[2] * 0.77, 0.94 + pulse * 0.04),
            self.color(GLOW[0] * 0.42, GLOW[1] * 0.41, GLOW[2] * 0.60, 0.94 + pulse * 0.04),
            self.color(GLOW[0] * 0.18, GLOW[1] * 0.17, GLOW[2] * 0.28, 0.95 + pulse * 0.03),
            self.color(GLOW[0] * 0.21, GLOW[1] * 0.20, GLOW[2] * 0.33, 0.94 + pulse * 0.04),
        )
        source = self.ambient_light_origin
        radius = max(850.0, min(width, height) * 1.35) * (0.97 + pulse * 0.06)
        for ring in range(44, 0, -1):
            fraction = ring / 44
            alpha = (1 - fraction) ** 1.5 * (0.19 + pulse * 0.04)
            draw.add_circle_filled(
                source,
                radius * fraction,
                self.color(GLOW[0], GLOW[1], GLOW[2], alpha),
                64,
            )
        inset = imgui.ImVec2(pos.x + 8, pos.y + 6)
        inset_end = imgui.ImVec2(pos.x + width - 8, pos.y + height - 8)
        draw.add_rect_filled(inset, inset_end, self.color(0.012, 0.012, 0.016, 1), 16)
        draw.add_rect(
            inset,
            inset_end,
            self.color(
                0.12 + GLOW[0] * 0.12 + pulse * 0.025,
                0.11 + GLOW[1] * 0.10 + pulse * 0.02,
                0.16 + GLOW[2] * 0.13 + pulse * 0.035,
                0.94,
            ),
            16,
            1,
        )

    def animated_button(self, label, key):
        clicked = imgui.button(label)
        hovered = imgui.is_item_hovered()
        minimum = imgui.get_item_rect_min()
        maximum = imgui.get_item_rect_max()
        if clicked:
            mouse = imgui.get_mouse_pos()
            self.click_effects[key] = (imgui.get_time(), mouse.x, mouse.y)
        amount = self.hover_amounts.get(key, 0.0)
        dt = min(imgui.get_io().delta_time, 0.05)
        target = 1.0 if hovered else 0.0
        amount += (target - amount) * min(1.0, dt * 14.0)
        if amount < 0.005:
            self.hover_amounts.pop(key, None)
        else:
            self.hover_amounts[key] = amount
        if amount > 0.01:
            minimum = imgui.get_item_rect_min()
            maximum = imgui.get_item_rect_max()
            draw = imgui.get_window_draw_list()
            alpha = amount * (0.11 + 0.05 * (0.5 + 0.5 * np.sin(imgui.get_time() * 5)))
            draw.add_rect(
                minimum, maximum, self.color(BLUE.x, BLUE.y, BLUE.z, alpha), 8, 1.0
            )
            sweep = (imgui.get_time() * 0.35 + (sum(map(ord, key)) % 100) / 100) % 1
            x = minimum.x + (maximum.x - minimum.x) * sweep
            draw.add_line(
                imgui.ImVec2(max(minimum.x + 5, x - 18), maximum.y - 1),
                imgui.ImVec2(min(maximum.x - 5, x + 18), maximum.y - 1),
                self.color(BLUE.x, BLUE.y, BLUE.z, amount * 0.28),
                1.5,
            )
        self.draw_click_effect(key, minimum, maximum)
        return clicked

    def draw_click_effect(self, key, minimum, maximum):
        effect = self.click_effects.get(key)
        if effect is None:
            return
        started, x, y = effect
        elapsed = imgui.get_time() - started
        duration = 0.48
        if elapsed >= duration:
            self.click_effects.pop(key, None)
            return
        progress = min(1.0, elapsed / duration)
        radius = 5 + (maximum.x - minimum.x) * 0.58 * (1 - (1 - progress) ** 3)
        alpha = 0.34 * (1 - progress) ** 1.8
        draw = imgui.get_window_draw_list()
        draw.push_clip_rect(minimum, maximum, True)
        draw.add_circle(
            imgui.ImVec2(x, y), radius,
            self.color(BLUE.x, BLUE.y, BLUE.z, alpha),
            36, max(1.0, 2.4 * (1 - progress)),
        )
        draw.pop_clip_rect()

    def draw_resource_exit(self, clip_min, clip_max):
        if not self.resource_exit_entries:
            return
        progress = (imgui.get_time() - self.resource_exit_started) / 0.34
        if progress >= 1:
            self.resource_exit_entries = []
            return
        progress = max(0.0, progress)
        eased = progress * progress * (3 - 2 * progress)
        alpha = 0.52 * (1 - eased)
        offset = 16 * eased
        draw = imgui.get_window_draw_list()
        draw.push_clip_rect(clip_min, clip_max, True)
        for x, y, name, type_id in self.resource_exit_entries:
            if y < clip_min.y - 26 or y > clip_max.y:
                continue
            icon_center = imgui.ImVec2(x + offset + 12, y + 12)
            self.draw_original_icon(draw, 'file', icon_center, type_id=type_id, alpha=alpha)
            draw.add_text(
                imgui.ImVec2(x + offset + 27, y + 2),
                self.color(0.70, 0.78, 0.93, alpha),
                name,
            )
        draw.pop_clip_rect()

    @staticmethod
    def current_content_token(app):
        if app.cur is not None:
            return ('bundle', app.cur.uid, app.cur.type_filter, app.cur.filter, tuple(app.cur.sort))
        if isinstance(app.tab, explorer.Browser) and app.tab.loc[0] == 'folder':
            return ('folder', app.tab.loc[1], app.tab.search)
        return ('home',)

    def sync_content_transition(self):
        token = self.current_content_token(self.app)
        if token == self.content_token:
            self.folder_entries_rendered = []
            return
        now = imgui.get_time()
        self.content_transition_started = now
        if self.content_token is not None and self.content_token[0] == 'bundle':
            self.resource_exit_entries = list(self.resource_entries_rendered)
            self.resource_exit_started = now
        else:
            self.resource_exit_entries = []
        if self.content_token is not None and self.content_token[0] == 'folder':
            self.folder_exit_entries = list(self.folder_entries_rendered)
            self.folder_exit_started = now
        else:
            self.folder_exit_entries = []
        self.folder_entries_rendered = []
        if token[0] == 'folder':
            self.folder_entered_at = now
        elif token[0] == 'bundle':
            self.resource_entered_at = now
        self.content_token = token
        self.folder_entries_rendered = []
        self.resource_entries_rendered = []

    def nav_item(self, key, selected, clicked=False):
        minimum = imgui.get_item_rect_min()
        maximum = imgui.get_item_rect_max()
        if clicked or imgui.is_item_clicked(0):
            mouse = imgui.get_mouse_pos()
            effect_key = f'nav:{key}'
            self.click_effects[effect_key] = (imgui.get_time(), mouse.x, mouse.y)
            self.draw_click_effect(effect_key, minimum, maximum)
        if selected:
            rect = (minimum.x, minimum.y, maximum.x, maximum.y)
            if self.nav_indicator_key != key:
                self.nav_indicator_key = key
                self.nav_transition_from = self.nav_indicator_rect or rect
                self.nav_transition_started = imgui.get_time()
            self.nav_target_rect = rect

    def draw_nav_indicator(self):
        if self.nav_target_rect is None:
            return
        now = imgui.get_time()
        source = self.nav_transition_from or self.nav_target_rect
        target = self.nav_target_rect
        progress = min(1.0, max(0.0, (now - self.nav_transition_started) / 0.24))
        eased = 1 - (1 - progress) ** 3
        current = tuple(start + (end - start) * eased for start, end in zip(source, target))
        self.nav_indicator_rect = current
        if progress >= 1:
            self.nav_transition_from = target
        left, top, right, bottom = current
        draw = imgui.get_window_draw_list()
        draw.add_rect_filled(
            imgui.ImVec2(left + 2, top + 1), imgui.ImVec2(right - 2, bottom - 1),
            self.color(BLUE.x, BLUE.y, BLUE.z, 0.08), 7,
        )
        draw.add_rect(
            imgui.ImVec2(left + 2, top + 1), imgui.ImVec2(right - 2, bottom - 1),
            self.color(BLUE.x, BLUE.y, BLUE.z, 0.16 + 0.10 * (1 - progress)), 7, 1,
        )
        draw.add_rect_filled(
            imgui.ImVec2(left + 2, top + 5), imgui.ImVec2(left + 4, bottom - 5),
            self.color(BLUE.x, BLUE.y, BLUE.z, 0.86), 2,
        )

    def sidebar_icon_selectable(self, kind, label, key, selected, type_id=None):
        flags = (
            imgui.TreeNodeFlags_.span_avail_width
            | imgui.TreeNodeFlags_.open_on_arrow
            | imgui.TreeNodeFlags_.frame_padding
            | imgui.TreeNodeFlags_.leaf
            | imgui.TreeNodeFlags_.no_tree_push_on_open
        )
        if selected:
            flags |= imgui.TreeNodeFlags_.selected
        imgui.tree_node_ex(f'##sidebar-{key}', flags)
        clicked = imgui.is_item_clicked() and not imgui.is_item_toggled_open()
        imgui.same_line(0, 2)
        if type_id is None:
            glyph, color = self.original_icon_data(kind)
        else:
            glyph, color = self.original_icon_data('file', type_id)
        if selected and type_id is not None:
            color = (BLUE.x, BLUE.y, BLUE.z, 1.0)
        theme.icon_text(glyph, (color.x, color.y, color.z, color.w))
        imgui.same_line(0, 6)
        imgui.text(label)
        return clicked

    def collapsed_sidebar(self):
        a = self.app
        icon_width = 38.0
        if imgui.button(
            f'{theme.I.ICON_FA_HOUSE}##sidebar-home',
            imgui.ImVec2(icon_width, 34),
        ):
            self.navigate_home()
        if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
            imgui.set_tooltip('Home')

        if a.docs:
            imgui.separator()
            for document in a.docs:
                popup_id = f'collapsed-bundle-menu-{document.uid}'
                icon, _ = self.original_icon_data('bundle')
                if imgui.button(
                    f'{icon}##collapsed-bundle-{document.uid}',
                    imgui.ImVec2(icon_width, 34),
                ):
                    self.defer(a.show_doc, document)
                    a.set_type_filter(document, None)
                    imgui.open_popup(popup_id)
                if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
                    imgui.set_tooltip(f'{document.name} [{document.b.platform}]')
                if imgui.begin_popup(popup_id):
                    imgui.text_disabled(document.name)
                    imgui.separator()
                    counts = {}
                    for resource in document.b.resources:
                        counts[resource.type] = counts.get(resource.type, 0) + 1
                    for type_id in sorted(counts, key=type_name):
                        icon, color = self.original_icon_data('folder')
                        imgui.text_colored(
                            imgui.ImVec4(color.x, color.y, color.z, color.w),
                            icon,
                        )
                        imgui.same_line(0, 6)
                        selected = (
                            a.cur is document and document.type_filter == type_id
                        )
                        if imgui.selectable(
                            f'{type_name(type_id)}  {counts[type_id]}##flyout-{document.uid}-{type_id}',
                            selected,
                        )[0]:
                            self.defer(a.show_doc, document)
                            a.set_type_filter(document, type_id)
                    imgui.end_popup()

        pins = a.pinned()
        if pins:
            imgui.separator()
            for index, path in enumerate(pins):
                icon, _ = self.original_icon_data('folder')
                if imgui.button(
                    f'{icon}##collapsed-folder-{index}',
                    imgui.ImVec2(icon_width, 34),
                ):
                    self.defer(a.go_folder, path)
                if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
                    imgui.set_tooltip(os.path.basename(path.rstrip('\\/')) or path)

        imgui.separator()
        if imgui.button(
            f'{theme.I.ICON_FA_FOLDER_PLUS}##collapsed-add-folder',
            imgui.ImVec2(icon_width, 34),
        ):
            path = filedialog.pick_folder('Add a folder to the navigation pane')
            if path:
                pins = a.cfg.setdefault('nav_folders', [])
                if path not in pins:
                    pins.append(path)
                    a.save_cfg()
        if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
            imgui.set_tooltip('Add folder')

    def menu_frame_start(self):
        self.menu_group_indices = {}
        self.menu_frame_items = []

    def menu_row_start(self, group):
        now = imgui.get_time()
        started = self.menu_group_started.setdefault(group, now)
        index = self.menu_group_indices.get(group, 0)
        self.menu_group_indices[group] = index + 1
        delay = min(index * 0.018, 0.28)
        progress = min(1.0, max(0.0, (now - started - delay) / 0.24))
        eased = 1 - (1 - progress) ** 3
        imgui.push_style_var(imgui.StyleVar_.alpha, 0.12 + eased * 0.88)
        return now, progress, eased

    def menu_row_finish(self, group, label, activated, now, progress, eased, expanded=False):
        if activated or imgui.is_item_clicked(0):
            mouse = imgui.get_mouse_pos()
            self.click_effects[f'menu:{group}:{label}'] = (now, mouse.x, mouse.y)
        minimum = imgui.get_item_rect_min()
        maximum = imgui.get_item_rect_max()
        self.menu_frame_items.append((group, minimum.x, minimum.y, maximum.x, maximum.y, label))
        self.draw_click_effect(f'menu:{group}:{label}', minimum, maximum)
        if progress < 1 or expanded:
            draw = imgui.get_window_draw_list()
            alpha = (1 - eased) * 0.14 + (0.10 if expanded else 0)
            draw.add_line(
                imgui.ImVec2(minimum.x + 7, maximum.y - 1),
                imgui.ImVec2(maximum.x - 7, maximum.y - 1),
                self.color(0.56, 0.75, 1.0, alpha),
                1.0,
            )
        imgui.pop_style_var()

    def dashboard_menu_item(self, label, shortcut='', selected=False, enabled=True, group='more'):
        now, progress, eased = self.menu_row_start(group)
        activated, value = imgui.menu_item(label, shortcut, selected, enabled)
        self.menu_row_finish(group, label, activated, now, progress, eased)
        return activated, value

    def dashboard_begin_menu(self, label, enabled=True, group='more'):
        self.menu_row_start(group)
        opened = imgui.begin_menu(label, enabled)
        imgui.pop_style_var()
        return opened

    def finish_menu_frame(self, opened):
        now = imgui.get_time()
        if opened:
            popup_pos = imgui.get_window_pos()
            popup_size = imgui.get_window_size()
            if popup_size.x > 0 and popup_size.y > 0:
                self.menu_popup_rect = (
                    popup_pos.x, popup_pos.y,
                    popup_pos.x + popup_size.x, popup_pos.y + popup_size.y,
                )
            current_groups = {}
            for row in self.menu_frame_items:
                current_groups.setdefault(row[0], []).append(row[1:])
            for group, rows in self.menu_last_groups.items():
                if group not in current_groups:
                    self.menu_group_exits[group] = (now, rows)
            for group in current_groups:
                self.menu_group_exits.pop(group, None)
            self.menu_last_groups = current_groups
            self.menu_last_items = list(self.menu_frame_items)
            self.menu_was_open = True
            self.draw_menu_group_exits()
            return
        if self.menu_was_open:
            self.menu_exit_items = [row[1:] for row in self.menu_last_items]
            self.menu_exit_started = now
            self.menu_exit_rect = self.menu_popup_rect
            self.menu_was_open = False
            self.menu_group_started.clear()
            self.menu_group_exits.clear()
            self.menu_last_groups.clear()
        self.draw_menu_group_exits()
        if not self.menu_exit_items:
            return
        progress = (now - self.menu_exit_started) / 0.22
        if progress >= 1:
            self.menu_exit_items = []
            return
        eased = max(0.0, progress)
        alpha = (1 - eased) ** 2 * 0.52
        draw = imgui.get_window_draw_list()
        if self.menu_exit_rect is None:
            return
        left, top, right, bottom = self.menu_exit_rect
        minimum = imgui.ImVec2(left, top + 10 * eased)
        maximum = imgui.ImVec2(right, bottom + 10 * eased)
        draw.push_clip_rect(minimum, maximum, True)
        draw.add_rect_filled(
            minimum, maximum, self.color(0.055, 0.07, 0.13, alpha * 0.72), 11,
        )
        draw.add_rect(
            minimum, maximum, self.color(0.48, 0.62, 0.88, alpha * 0.40), 11, 1,
        )
        for left, top, right, bottom, label in self.menu_exit_items:
            offset = 8 * eased
            minimum = imgui.ImVec2(left + offset, top)
            maximum = imgui.ImVec2(right + offset, bottom)
            draw.add_text(
                imgui.ImVec2(left + 12 + offset, top + 3),
                self.color(0.76, 0.83, 0.96, alpha),
                label,
            )
        draw.pop_clip_rect()

    def draw_menu_group_exits(self):
        now = imgui.get_time()
        draw = imgui.get_window_draw_list()
        pos = imgui.get_window_pos()
        size = imgui.get_window_size()
        draw.push_clip_rect(pos, imgui.ImVec2(pos.x + size.x, pos.y + size.y), True)
        for group, (started, rows) in list(self.menu_group_exits.items()):
            progress = (now - started) / 0.20
            if progress >= 1:
                self.menu_group_exits.pop(group, None)
                continue
            alpha = 0.48 * (1 - max(0.0, progress)) ** 2
            offset = 10 * max(0.0, progress)
            for left, top, right, bottom, label in rows:
                draw.add_text(
                    imgui.ImVec2(left + 12 + offset, top + 3),
                    self.color(0.72, 0.80, 0.94, alpha),
                    label,
                )
        draw.pop_clip_rect()

    def draw_folder_exit(self, clip_min, clip_max):
        if not self.folder_exit_entries:
            return
        progress = (imgui.get_time() - self.folder_exit_started) / 0.36
        if progress >= 1:
            self.folder_exit_entries = []
            return
        progress = max(0.0, progress)
        eased = progress * progress * (3 - 2 * progress)
        alpha = 0.52 * (1 - eased)
        draw = imgui.get_window_draw_list()
        draw.push_clip_rect(clip_min, clip_max, True)
        for x, y, name, kind in self.folder_exit_entries:
            if y < clip_min.y - 26 or y > clip_max.y:
                continue
            offset = 18 * eased
            color = self.color(0.68, 0.78, 0.96, alpha)
            icon_color = self.color(0.50, 0.67, 0.95, alpha)
            center = imgui.ImVec2(x + offset + 10, y + 10)
            self.draw_original_icon(draw, kind, center, icon_color)
            draw.add_text(imgui.ImVec2(x + offset + 23, y + 2), color, name)
        draw.pop_clip_rect()

    def selected_accent(self, selected):
        if not selected:
            return
        minimum = imgui.get_item_rect_min()
        maximum = imgui.get_item_rect_max()
        pulse = 0.78 + 0.22 * (0.5 + 0.5 * np.sin(imgui.get_time() * 3.0))
        imgui.get_window_draw_list().add_rect_filled(
            imgui.ImVec2(minimum.x, minimum.y + 4),
            imgui.ImVec2(minimum.x + 3, maximum.y - 4),
            self.color(BLUE.x, BLUE.y, BLUE.z, pulse),
            2,
        )

    def gui(self):
        a = self.app
        if not self.styled:
            selected_theme = a.cfg.get('theme', 'Violet')
            if selected_theme not in theme.PALETTES:
                selected_theme = 'Violet'
            apply_style(selected_theme)
            self.styled = True
        if self.loading_screen_enabled and self.draw_loading_screen():
            return
        pending, self.deferred = self.deferred, []
        for fn, args in pending:
            fn(*args)
        a.pump_job()
        a.update_roots()
        a.pump_drops()
        a.audio_guard()
        a.thumbs.pump()
        if self.workspace != 'experimental':
            a.shortcuts()
        if a.want_select is not None and a.cur is not None:
            a.goto(a.want_select, a.cur)
            a.want_select = None
        now = imgui.get_time()
        self.click_effects = {
            key: effect for key, effect in self.click_effects.items()
            if now - effect[0] < 0.48
        }

        self.ambient_background()
        self.header()
        self.workspace_switcher()
        if self.workspace == 'experimental':
            self.hex_editor_workspace()
            self.footer()
            a.texture_window()
            a.model_textures_window()
            a.modals()
            a.genesys_string_window()
            a.results_window()
            a.soundtrack_window()
            a.port_window()
            a.gameplay_reference_window()
            a.exit_guard()
            self.draw_launch_fade()
            return
        self.toolbar()
        avail = imgui.get_content_region_avail()
        height = max(160.0, avail.y - 48)
        if self.inspector_expanded and a.cur is not None and a.focused()[1] is not None:
            card_pos = imgui.get_cursor_screen_pos()
            expanded_height = max(160.0, avail.y - 48)
            self.glass_surface(
                card_pos, imgui.ImVec2(avail.x, expanded_height), key='inspector-expanded'
            )
            imgui.begin_child(
                'dashboard-inspector-expanded',
                imgui.ImVec2(avail.x, expanded_height),
                imgui.ChildFlags_.borders,
            )
            self.inspector()
            imgui.end_child()
            self.draw_frame_light(card_pos, imgui.ImVec2(avail.x, expanded_height))
            a.drop_on(a.cur)
            self.footer()
            a.texture_window()
            a.model_textures_window()
            a.modals()
            a.genesys_string_window()
            a.results_window()
            a.soundtrack_window()
            a.port_window()
            a.gameplay_reference_window()
            a.exit_guard()
            self.draw_launch_fade()
            return
        has_inspector = a.cur is not None
        splitter_w = 8.0
        gap = 5.0
        sidebar_min_width = 72.0 if self.sidebar_collapsed else 150.0
        sidebar_max_width = max(
            sidebar_min_width,
            avail.x - (250.0 + (210.0 if has_inspector else 0.0))
            - (splitter_w + gap) * (2 if has_inspector else 1),
        )
        side_w = min(
            max(sidebar_min_width, self.dashboard_sidebar_width),
            sidebar_max_width,
        ) if not self.sidebar_collapsed else sidebar_min_width
        detail_w = min(
            max(210.0, self.dashboard_inspector_width),
            max(210.0, avail.x - side_w - 250.0 - (splitter_w + gap) * 2),
        ) if has_inspector else 0.0

        card_pos = imgui.get_cursor_screen_pos()
        self.glass_surface(card_pos, imgui.ImVec2(side_w, height), key='sidebar')
        imgui.begin_child('dashboard-sidebar', imgui.ImVec2(side_w, height), imgui.ChildFlags_.borders)
        imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(8, 5))
        imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(8, 5))
        self.sidebar()
        imgui.pop_style_var(2)
        imgui.end_child()
        self.draw_frame_light(card_pos, imgui.ImVec2(side_w, height))
        imgui.same_line(0, gap)
        if not self.sidebar_collapsed:
            side_delta = self.drag_splitter('dashboard-sidebar', height)
            if side_delta:
                self.dashboard_sidebar_width = min(
                    max(150.0, side_w + side_delta),
                    sidebar_max_width,
                )
                side_w = self.dashboard_sidebar_width
            imgui.same_line(0, gap)

        center_w = max(
            250.0,
            avail.x - side_w - detail_w - (splitter_w + gap) * (2 if has_inspector else 1),
        )
        card_pos = imgui.get_cursor_screen_pos()
        self.glass_surface(card_pos, imgui.ImVec2(center_w, height), key='main')
        imgui.begin_child('dashboard-main', imgui.ImVec2(center_w, height), imgui.ChildFlags_.borders)
        self.sync_content_transition()
        content_pos = imgui.get_cursor_screen_pos()
        progress = min(1.0, max(0.0, (imgui.get_time() - self.content_transition_started) / 0.30))
        eased = 1 - (1 - progress) ** 3
        slide = 13 * (1 - eased)
        imgui.set_cursor_screen_pos(imgui.ImVec2(content_pos.x + slide, content_pos.y))
        imgui.push_style_var(imgui.StyleVar_.alpha, 0.16 + 0.84 * eased)
        self.main_panel()
        imgui.pop_style_var()
        self.draw_folder_exit(
            card_pos,
            imgui.ImVec2(card_pos.x + center_w, card_pos.y + height),
        )
        self.draw_resource_exit(
            card_pos,
            imgui.ImVec2(card_pos.x + center_w, card_pos.y + height),
        )
        imgui.end_child()
        self.draw_frame_light(card_pos, imgui.ImVec2(center_w, height))

        if has_inspector and a.cur is not None:
            imgui.same_line(0, gap)
            inspector_delta = self.drag_splitter('dashboard-inspector', height)
            if inspector_delta:
                self.dashboard_inspector_width = min(
                    max(210.0, detail_w - inspector_delta),
                    max(210.0, avail.x - side_w - 250.0 - (splitter_w + gap) * 2),
                )
                detail_w = self.dashboard_inspector_width
            imgui.same_line(0, gap)
            card_pos = imgui.get_cursor_screen_pos()
            self.glass_surface(card_pos, imgui.ImVec2(detail_w, height), key='inspector')
            imgui.begin_child('dashboard-inspector', imgui.ImVec2(0, height), imgui.ChildFlags_.borders)
            self.inspector()
            imgui.end_child()
            self.draw_frame_light(card_pos, imgui.ImVec2(detail_w, height))
            a.drop_on(a.cur)

        self.footer()
        a.texture_window()
        a.model_textures_window()
        a.modals()
        a.genesys_string_window()
        a.results_window()
        a.soundtrack_window()
        a.port_window()
        a.gameplay_reference_window()
        if a.drag is not None:
            payload = imgui.get_drag_drop_payload_py_id()
            if payload is None or payload.type != app_module.PAYLOAD:
                a.drag = None
                a.drag_out_done = False
            else:
                a.drag_out()
        a.exit_guard()
        self.draw_launch_fade()

    def launch_fade_alpha(self, now):
        if not self.launch_fade_enabled:
            return 0.0
        if self.launch_fade_started is None:
            self.launch_fade_started = now
        progress = min(1.0, max(0.0, (now - self.launch_fade_started) / LAUNCH_FADE_SECONDS))
        if progress >= 1.0:
            self.launch_fade_enabled = False
            return 0.0
        return (1.0 - progress) ** 2

    def draw_launch_fade(self):
        alpha = self.launch_fade_alpha(imgui.get_time())
        if alpha <= 0.0:
            return
        display = imgui.get_io().display_size
        draw = imgui.get_foreground_draw_list()
        background = theme.PALETTE['bg']
        draw.add_rect_filled(
            imgui.ImVec2(0, 0),
            display,
            self.color(background[0], background[1], background[2], alpha),
        )

    def loading_screen_active(self, now):
        if not self.loading_screen_enabled:
            return False
        if self.loading_screen_started is None:
            self.loading_screen_started = now
        elapsed = now - self.loading_screen_started
        if elapsed >= LOADING_SCREEN_SECONDS - 1e-6:
            self.loading_screen_enabled = False
            return False
        return True

    def draw_loading_screen(self):
        now = imgui.get_time()
        if not self.loading_screen_active(now):
            return False
        elapsed = now - self.loading_screen_started
        display = imgui.get_io().display_size
        draw = imgui.get_foreground_draw_list()
        fade = min(1.0, max(0.0, (LOADING_SCREEN_SECONDS - elapsed) / 0.32))
        alpha = min(1.0, elapsed / 0.24, fade)
        panel_width = min(
            display.x * 0.607,
            max(230.0, display.y - 48.0) * 1.84,
            1160.0,
        )
        panel_width = max(280.0, panel_width)
        panel_height = panel_width / 1.84
        x = (display.x - panel_width) * 0.5
        y = (display.y - panel_height) * 0.5
        panel_min = imgui.ImVec2(x, y)
        panel_max = imgui.ImVec2(x + panel_width, y + panel_height)
        draw.add_rect_filled(
            imgui.ImVec2(0, 0),
            display,
            self.color(*LOADING_SURFACE, alpha),
        )
        for layer in range(5, 0, -1):
            spread = layer * 3.0
            draw.add_rect_filled(
                imgui.ImVec2(panel_min.x - spread, panel_min.y - spread),
                imgui.ImVec2(panel_max.x + spread, panel_max.y + spread),
                self.color(GLOW[0], GLOW[1], GLOW[2], alpha * (0.012 / layer)),
                18 + spread,
            )
        draw.add_rect_filled(
            panel_min,
            panel_max,
            self.color(*LOADING_SURFACE, alpha),
            18,
        )
        draw.add_rect(
            panel_min,
            panel_max,
            self.color(0.22, 0.22, 0.23, alpha * 0.72),
            18,
            1.0,
        )

        logo_color = tuple(round(channel * 255) for channel in (BLUE.x, BLUE.y, BLUE.z))
        if self.loading_logo_source is None:
            logo_path = os.path.join(os.path.dirname(__file__), 'assets', 'loading_logo.png')
            with Image.open(logo_path) as logo:
                self.loading_logo_source = np.array(
                    logo.convert('RGB'), dtype=np.uint8, order='C', copy=True
                )
        if self.loading_logo is None or self.loading_logo_color != logo_color:
            logo_pixels = tint_loading_logo(
                self.loading_logo_source,
                tuple(channel / 255 for channel in logo_color),
            )
            self.loading_logo = immvision.GlTexture(logo_pixels)
            self.loading_logo_color = logo_color
        logo_width = panel_width * 0.30
        logo_height = logo_width * self.loading_logo.image_size[1] / self.loading_logo.image_size[0]
        logo_center = imgui.ImVec2(display.x * 0.5, display.y * 0.5)
        logo_min = imgui.ImVec2(logo_center.x - logo_width * 0.5, logo_center.y - logo_height * 0.5)
        logo_max = imgui.ImVec2(logo_center.x + logo_width * 0.5, logo_center.y + logo_height * 0.5)
        draw.add_image(
            imgui.ImTextureRef(self.loading_logo.texture_id),
            logo_min,
            logo_max,
            col=imgui.get_color_u32(imgui.ImVec4(1, 1, 1, alpha)),
        )

        dot_y = display.y * 0.5 + panel_height * 0.27
        for index in range(3):
            phase = (elapsed * 1.45 - index * 0.22) % 1.0
            dot_alpha = alpha * (0.22 + 0.68 * (0.5 + 0.5 * np.sin(phase * np.pi * 2)))
            draw.add_circle_filled(
                imgui.ImVec2(display.x * 0.5 + (index - 1) * 13 * panel_width / 830.0, dot_y),
                2.2 * panel_width / 830.0,
                imgui.get_color_u32(
                    imgui.ImVec4(BLUE.x, BLUE.y, BLUE.z, dot_alpha)
                ),
            )
        return True

    def header(self):
        a = self.app
        pos = imgui.get_cursor_screen_pos()
        width = imgui.get_content_region_avail().x
        draw = imgui.get_window_draw_list()
        self.glass_surface(pos, imgui.ImVec2(width, 62), 12, 1.15, key='header')
        logo = imgui.ImVec2(pos.x + 14, pos.y + 13)
        draw.add_rect_filled(logo, imgui.ImVec2(logo.x + 36, logo.y + 36),
                             self.color(0.22, 0.20, 0.28, 1), 10)
        self.draw_original_icon(draw, 'bundle', imgui.ImVec2(logo.x + 18, logo.y + 18), TEXT)
        imgui.set_cursor_screen_pos(imgui.ImVec2(pos.x + 62, pos.y + 11))
        imgui.text_colored(TEXT, 'BNDL EXPLORER')
        imgui.set_cursor_screen_pos(imgui.ImVec2(pos.x + 62, pos.y + 34))
        if self.workspace == 'experimental':
            subtitle = (f'{os.path.basename(self.hex_document.path)}  /  {explorer.short_size(self.hex_document.size)}'
                        if self.hex_document else 'Hex Editor workspace')
            state = 'UNSAVED CHANGES' if self.hex_document and self.hex_document.modified else 'HEX EDITOR'
            state_color = imgui.ImVec4(1, 0.68, 0.28, 1) if state == 'UNSAVED CHANGES' else BLUE
        elif a.cur is not None:
            subtitle = f'{a.cur.name}  /  {a.cur.b.platform}  /  {len(a.cur.b.resources):,} resources'
            state = 'UNSAVED CHANGES' if a.cur.modified else 'READY'
            state_color = imgui.ImVec4(1, 0.68, 0.28, 1) if a.cur.modified else GREEN
        elif isinstance(a.tab, explorer.Browser) and a.tab.loc[0] == 'folder':
            subtitle, state, state_color = a.tab.loc[1], 'BROWSING', BLUE
        else:
            subtitle, state, state_color = 'Bundle workspace', 'READY', GREEN
        imgui.text_disabled(explorer.ellipsis(subtitle, 74))
        state_w = imgui.calc_text_size(state).x
        imgui.set_cursor_screen_pos(imgui.ImVec2(pos.x + width - state_w - 18, pos.y + 23))
        pulse = 0.55 + 0.45 * (0.5 + 0.5 * np.sin(imgui.get_time() * 3.2))
        draw.add_circle_filled(
            imgui.ImVec2(pos.x + width - state_w - 27, pos.y + 30),
            5.5, self.color(state_color.x, state_color.y, state_color.z, 0.13 * pulse), 24,
        )
        draw.add_circle_filled(
            imgui.ImVec2(pos.x + width - state_w - 27, pos.y + 30),
            2.4, imgui.get_color_u32(state_color), 16,
        )
        imgui.set_cursor_screen_pos(imgui.ImVec2(pos.x + width - state_w - 17, pos.y + 23))
        imgui.text_colored(state_color, state)
        imgui.set_cursor_screen_pos(pos)
        imgui.dummy(imgui.ImVec2(width, 62))
        self.draw_frame_light(pos, imgui.ImVec2(width, 62), 12)
        self.draw_frame_light(pos, imgui.ImVec2(width, 62), 12)
        imgui.spacing()

    def workspace_switcher(self):
        imgui.text_disabled('WORKSPACES')
        tab_flags = (
            imgui.TabBarFlags_.reorderable
            | imgui.TabBarFlags_.fitting_policy_scroll
        )
        imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(12, 7))
        if imgui.begin_tab_bar('dashboard-workspaces', tab_flags):
            workspaces = (
                ('bundles', theme.BUNDLE, 'Bundle Explorer'),
                ('experimental', theme.I.ICON_FA_FILE_CODE, 'EXPERIMENTAL'),
            )
            for key, icon, label in workspaces:
                selected, _ = imgui.begin_tab_item(
                    f'{icon}  {label}###workspace-{key}',
                    None,
                )
                if selected:
                    self.activate_workspace(key, imgui.get_time())
                    imgui.end_tab_item()
            imgui.end_tab_bar()
        imgui.pop_style_var()
        imgui.separator()

    def activate_workspace(self, key, now):
        if self.workspace == key:
            return
        self.workspace = key
        self.workspace_transition_started = now
        self.content_transition_started = now

    def open_resource_in_hex(self, d, r, k):
        """Edit chunk k of a resource in the Hex Editor workspace (a working file; Save writes it back into the
        resource, undoable with Ctrl+Z in the bundle)."""
        folder = os.path.join(app_module.config_dir(), 'hexedit')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f'{os.path.splitext(d.name)[0]}_{r.id:016X}_chunk{k}.bin')
        stale = next((x for x in self.hex_documents if os.path.normcase(x.path) == os.path.normcase(path)), None)
        if stale is not None:                # start again from the resource as it is now
            stale.close()
            self.hex_documents.remove(stale)
            if self.hex_document is stale:
                self.hex_document = None
        with open(path, 'wb') as f:
            f.write(bytes(r.data(k)))
        self.hex_links[os.path.normcase(os.path.abspath(path))] = (d.uid, r.id, k)
        self._open_hex_document(path)
        self.activate_workspace('experimental', imgui.get_time())
        self.hex_status = (f'Chunk {k} of {r.id:#x} ({d.name}): Save writes it back into the resource '
                           '(then save the bundle).')

    def _write_back_hex(self, saved_path):
        """A saved Hex Editor file that is a resource chunk: put it back into the resource."""
        link = self.hex_links.get(os.path.normcase(os.path.abspath(saved_path)))
        if link is None:
            return None
        d = self.app.doc_by_uid(link[0])
        r = d.b.find(link[1]) if d is not None else None
        if r is None:
            return 'the bundle or resource is not open any more: the edit stays in the file only'
        with open(saved_path, 'rb') as f:
            data = f.read()
        if data == bytes(r.data(link[2])):
            return 'no change'
        d.checkpoint(f'hex edit {r.id:#x}', [r.id])
        r.set_data(link[2], data)
        self.app.changed(d, r)
        return f'written back into {r.id:#x} in {d.name} (Ctrl+Z there undoes it; save the bundle to keep it)'

    def _open_hex_document(self, path=None):
        path = path or filedialog.open_file('Open file in Hex Editor', self.app.cfg.get('last_bundle', ''),
                                            filedialog.ANY)
        if not path:
            return
        try:
            norm_path = os.path.normcase(os.path.abspath(path))
            document = next((item for item in self.hex_documents
                             if os.path.normcase(item.path) == norm_path), None)
            if document is None:
                document = hex_editor.HexDocument(path)
                self.hex_documents.append(document)
            if self.hex_document is not document:
                self.hex_ascii_buffers.clear()
                self.hex_cell_buffers.clear()
            self.hex_document = document
            document.cursor = min(document.cursor, max(0, document.size - 1))
            self.hex_match = None
            self.hex_search_results = []
            self.hex_analysis = None
            self.hex_status = f'Opened {document.path} ({explorer.short_size(document.size)}).'
        except OSError as exc:
            self.hex_status = f'Cannot open file: {exc}'

    def _hex_pattern(self, value):
        mode = self.hex_search_mode
        if mode == 'HEX':
            return bytes.fromhex(value)
        if mode == 'TEXT':
            codec = HEX_TEXT_ENCODINGS[self.hex_text_encoding]
            return value.encode(codec)
        if mode == 'UTF-8':
            return value.encode('utf-8')
        if mode == 'UTF-16 LE':
            return value.encode('utf-16-le')
        if mode == 'UTF-16 BE':
            return value.encode('utf-16-be')
        if mode in ('U8', 'U16 LE', 'U32 LE', 'U64 LE'):
            size = {'U8': 1, 'U16 LE': 2, 'U32 LE': 4, 'U64 LE': 8}[mode]
            return int(value.strip(), 0).to_bytes(size, 'little', signed=False)
        if mode in ('F32 LE', 'F64 LE'):
            return struct.pack('<f' if mode == 'F32 LE' else '<d', float(value))
        raise ValueError(f'Unsupported search mode: {mode}')

    def _hex_find(self):
        document = self.hex_document
        if document is None:
            return
        try:
            pattern = self._hex_pattern(self.hex_search_input)
            if self.hex_match and document.read(*self.hex_match) == pattern:
                match_offset = self.hex_match[0]
                start = match_offset - 1 if self.hex_search_reverse else match_offset + len(pattern)
            else:
                start = document.cursor
            found = document.find(pattern, start, reverse=self.hex_search_reverse)
            if found < 0:
                self.hex_match = None
                self.hex_status = 'No more matches.'
                return
            document.cursor = found
            document.selection = (found, found + len(pattern))
            self.hex_selection_anchor = found
            self.hex_match = (found, pattern)
            self.hex_offset_input = self._format_hex_offset(found, self.hex_offset_base)
            self.hex_status = f'Found at {self._format_hex_offset(found, self.hex_offset_base)}.'
        except (ValueError, OverflowError) as exc:
            self.hex_status = f'Invalid search value: {exc}'

    def _hex_find_all(self):
        document = self.hex_document
        if document is None or self.app.job is not None:
            return
        try:
            pattern = self._hex_pattern(self.hex_search_input)
            if not pattern:
                raise ValueError('search value cannot be empty')
        except (ValueError, OverflowError) as exc:
            self.hex_status = f'Invalid search value: {exc}'
            return
        snapshot = document.snapshot()
        revision = document.revision
        limit = 10000

        def find_matches(progress):
            matches = []
            start = 0
            while start <= snapshot.size - len(pattern) and len(matches) < limit:
                found = snapshot.find(pattern, start)
                if found < 0:
                    break
                matches.append(found)
                start = found + 1
            return matches

        def completed(matches):
            if self.hex_document is not document or document.revision != revision:
                self.hex_status = 'File changed during search; run Find all again.'
                return
            self.hex_search_results = matches
            self.hex_search_pattern = pattern
            self.hex_results_visible = True
            suffix = ' (limit reached)' if len(matches) == limit else ''
            self.hex_status = f'Found {len(matches):,} match(es){suffix}.'

        self.app.run_job('Finding all matches...', find_matches, completed)

    def _hex_replace(self):
        document = self.hex_document
        if document is None:
            return
        try:
            pattern = self._hex_pattern(self.hex_search_input)
            replacement = self._hex_pattern(self.hex_replace_input)
            if not self.hex_match or document.read(*self.hex_match) != pattern:
                self.hex_status = 'Find a match before replacing.'
                return
            offset, _ = self.hex_match
            document.replace(offset, len(pattern), replacement)
            self._clear_hex_edit_buffers()
            self.hex_match = None
            self.hex_status = f'Replaced {len(pattern)} byte(s) at 0x{offset:X}.'
        except (ValueError, OverflowError) as exc:
            self.hex_status = f'Invalid replacement: {exc}'

    def _hex_save(self, save_as=False):
        document = self.hex_document
        if document is None:
            return
        if self.app.job is not None:
            self.hex_status = 'Wait for the current background task before saving.'
            return
        path = document.path
        if save_as:
            path = filedialog.save_file('Save edited file as', path, filedialog.ANY)
            if not path:
                return
        def completed(saved_path):
            self.hex_status = f'Saved {saved_path}.'
            back = self._write_back_hex(saved_path)
            if back:
                self.hex_status += f' Resource chunk {back}.'

        self.app.run_job(
            f'Saving {os.path.basename(path)}...',
            lambda progress: document.save(path, progress),
            completed,
        )

    def _hex_selection(self):
        document = self.hex_document
        if document is None:
            return 0, 0
        start, end = sorted(document.selection)
        if start == end and document.cursor < document.size:
            return document.cursor, document.cursor + 1
        return start, end

    def _commit_hex_cell(self, offset, value):
        document = self.hex_document
        if document is None or not value:
            return False
        if len(value) > 2:
            self.hex_status = 'Enter one byte (one or two hexadecimal digits) per cell.'
            return False
        try:
            byte = int(value, 16)
        except ValueError:
            self.hex_status = 'Byte values must be one or two hexadecimal digits.'
            return False
        if offset >= document.size:
            return False
        if document.read(offset, 1) != bytes((byte,)):
            document.replace(offset, 1, bytes((byte,)))
            self.hex_status = f'Wrote {byte:02X} at 0x{offset:X}.'
        document.cursor = offset
        document.selection = (offset, offset + 1)
        self.hex_selection_anchor = offset
        self.hex_offset_input = self._format_hex_offset(offset, self.hex_offset_base)
        return True

    def _commit_ascii_row(self, offset, original, edited):
        document = self.hex_document
        if document is None:
            return False
        codec = HEX_TEXT_ENCODINGS[self.hex_text_encoding]
        try:
            edited_bytes = edited.encode(codec)
        except UnicodeEncodeError:
            self.hex_status = f'Text cannot be represented in {self.hex_text_encoding}.'
            return False
        if any(not character.isprintable() for character in edited):
            self.hex_status = 'Text edits can only contain printable characters.'
            return False
        original_bytes = document.read(offset, len(original))
        if len(original_bytes) != len(original):
            self.hex_status = 'The byte row changed before the text edit was applied.'
            return False
        updated = bytearray()
        matcher = SequenceMatcher(None, original, edited, autojunk=False)
        for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if operation == 'equal':
                updated.extend(original_bytes[old_start:old_end])
            elif operation in ('replace', 'insert'):
                updated.extend(edited_bytes[new_start:new_end])
        if updated != original_bytes:
            document.replace(offset, len(original_bytes), updated)
            document.cursor = offset
            document.selection = (offset, offset + len(updated))
            self.hex_selection_anchor = offset
            self.hex_offset_input = self._format_hex_offset(offset, self.hex_offset_base)
            self._clear_hex_edit_buffers()
            self.hex_status = f'Updated ASCII bytes at 0x{offset:X}.'
        return True

    @staticmethod
    def _decode_hex_row(data, encoding):
        codec = HEX_TEXT_ENCODINGS[encoding]
        return ''.join(
            character if character.isprintable() else '.'
            for value in data
            for character in (bytes((value,)).decode(codec, errors='replace'),)
        )

    @staticmethod
    def _format_hex_offset(offset, base):
        if base == 'Decimal':
            return f'{offset:08d}'
        if base == 'Octal':
            return f'{offset:08o}'
        return f'{offset:08X}'

    @staticmethod
    def _parse_hex_offset(value, base):
        radix = {'Hexadecimal': 16, 'Decimal': 10, 'Octal': 8}[base]
        return int(value, radix)

    def _effective_hex_bytes_per_row(self, grid_width):
        if not self.hex_fit_width:
            return self.hex_bytes_per_row
        show_hex = self.hex_visible_columns != 'Text'
        show_text = self.hex_visible_columns != 'Hex'
        byte_area = max(1.0, grid_width - 104.0)
        if show_hex and show_text:
            byte_area = max(1.0, byte_area - 300.0)
        column_width = 36.0 + 4.0 / self.hex_group_size if show_hex else 9.0
        return max(1, min(64, int(byte_area // column_width)))

    def _hex_setting_combo(self, label, attribute, options, width=180):
        current = getattr(self, attribute)
        preview = str(current)
        imgui.set_next_item_width(width)
        changed = False
        if imgui.begin_combo(f'{label}##{attribute}', preview):
            for option in options:
                if imgui.selectable(str(option), option == current)[0]:
                    setattr(self, attribute, option)
                    changed = option != current
                    if changed and attribute == 'hex_text_encoding':
                        self.hex_ascii_buffers.clear()
                    elif changed and attribute == 'hex_offset_base' and self.hex_document:
                        self.hex_offset_input = self._format_hex_offset(
                            self.hex_document.cursor, option
                        )
                if option == current:
                    imgui.set_item_default_focus()
            imgui.end_combo()
        return changed

    def _hex_copy(self, cut=False):
        document = self.hex_document
        if document is None:
            return
        start, end = self._hex_selection()
        if end - start > 16 * 1024 * 1024:
            self.hex_status = 'Clipboard selection is limited to 16 MiB.'
            return
        data = document.read(start, end - start)
        imgui.set_clipboard_text(data.hex(' ').upper())
        if cut and end > start:
            document.replace(start, end - start, b'')
            self._clear_hex_edit_buffers()
            self.hex_status = f'Cut {end - start} byte(s).'
        else:
            self.hex_status = f'Copied {end - start} byte(s) as hex.'

    def _hex_paste(self):
        document = self.hex_document
        if document is None:
            return
        try:
            data = bytes.fromhex(imgui.get_clipboard_text() or '')
            if not data:
                self.hex_status = 'Clipboard does not contain hex bytes.'
                return
            start, end = self._hex_selection()
            delete_length = end - start
            if not delete_length and self.hex_overwrite:
                delete_length = min(len(data), document.size - start)
            document.replace(start, delete_length, data)
            self._clear_hex_edit_buffers()
            self.hex_status = f'Pasted {len(data)} byte(s) at 0x{start:X}.'
        except ValueError as exc:
            self.hex_status = f'Clipboard is not hex data: {exc}'

    def _hex_close_document(self):
        document = self.hex_document
        if document is None:
            return
        if document.modified:
            self.hex_status = 'Save the file before closing it; unsaved edits are kept.'
            return
        if self.app.job is not None:
            self.hex_status = 'Wait for the current analysis or comparison to finish before closing.'
            return
        document.close()
        self.hex_documents.remove(document)
        self.hex_document = self.hex_documents[-1] if self.hex_documents else None
        self.hex_ascii_buffers.clear()
        self.hex_cell_buffers.clear()
        self.hex_analysis = None
        self.hex_match = None
        self.hex_search_results = []
        self.hex_status = 'Closed file.'

    def _clear_hex_edit_buffers(self):
        self.hex_ascii_buffers.clear()
        self.hex_cell_buffers.clear()

    def _hex_compare(self):
        document = self.hex_document
        if document is None:
            return
        path = filedialog.open_file('Compare with file', document.path, filedialog.ANY)
        if not path:
            return

        def compare_files(progress):
            other = hex_editor.HexDocument(path)
            try:
                return document.snapshot().compare(other, progress)
            finally:
                other.close()

        def completed(result):
            if result['first_difference'] is None:
                message = 'Files are identical.' if result['left_size'] == result['right_size'] else 'Common bytes are identical; file sizes differ.'
            else:
                message = (f'First difference at 0x{result["first_difference"]:X}; '
                           f'{result["differing_bytes"]:,} differing byte(s) in common range.')
            self.hex_status = (f'{message} Sizes: {explorer.short_size(result["left_size"])} / '
                               f'{explorer.short_size(result["right_size"])}.')

        self.app.run_job(f'Comparing with {os.path.basename(path)}...', compare_files, completed)

    def _hex_analyze(self):
        document = self.hex_document
        if document is None:
            return
        try:
            custom_crc = (
                int(self.hex_crc_polynomial, 16),
                int(self.hex_crc_initial, 16),
                int(self.hex_crc_xor_out, 16),
            )
            if any(value < 0 or value > 0xffff for value in custom_crc) or not custom_crc[0]:
                raise ValueError('CRC-16 parameters must be 1..FFFF (polynomial) and 0..FFFF (initial/xor-out).')
        except ValueError as exc:
            self.hex_status = f'Invalid custom CRC-16 parameters: {exc}'
            return
        snapshot = document.snapshot()
        revision = document.revision

        def completed(result):
            if document.revision != revision:
                self.hex_status = 'File changed during analysis; run Analyze again for current data.'
                return
            self.hex_analysis = result
            self.hex_analysis_document = document.path
            self.hex_analysis_revision = revision
            self.hex_status = 'Checksum and byte-frequency analysis complete.'

        self.app.run_job(
            'Analyzing file...',
            lambda progress: snapshot.analyze(progress, custom_crc),
            completed,
        )

    def _hex_keyboard(self):
        io = imgui.get_io()
        if io.want_text_input or not imgui.is_window_focused():
            return
        document = self.hex_document
        if io.key_ctrl and io.key_alt and imgui.is_key_pressed(imgui.Key.d, False):
            self.hex_inspector_visible = not self.hex_inspector_visible
        elif io.key_ctrl and io.key_alt and imgui.is_key_pressed(imgui.Key.c, False):
            self._hex_analyze()
        elif io.key_ctrl and io.key_alt and imgui.is_key_pressed(imgui.Key.s, False):
            self.hex_results_visible = not self.hex_results_visible
            if self.hex_results_visible and not self.hex_search_results:
                self._hex_find_all()
        elif io.key_alt and imgui.is_key_pressed(imgui.Key.f7, False):
            self.hex_results_visible = not self.hex_results_visible
        elif io.key_ctrl and imgui.is_key_pressed(imgui.Key.o, False):
            self._open_hex_document()
        elif io.key_ctrl and imgui.is_key_pressed(imgui.Key.s, False):
            self._hex_save()
        elif io.key_ctrl and imgui.is_key_pressed(imgui.Key.z, False):
            if self.hex_document and self.hex_document.undo():
                self._clear_hex_edit_buffers()
                self.hex_status = 'Undid the last edit.'
        elif io.key_ctrl and imgui.is_key_pressed(imgui.Key.y, False):
            if self.hex_document and self.hex_document.redo():
                self._clear_hex_edit_buffers()
                self.hex_status = 'Redid the last edit.'
        elif io.key_ctrl and imgui.is_key_pressed(imgui.Key.c, False):
            self._hex_copy()
        elif io.key_ctrl and imgui.is_key_pressed(imgui.Key.x, False):
            self._hex_copy(cut=True)
        elif io.key_ctrl and imgui.is_key_pressed(imgui.Key.v, False):
            self._hex_paste()
        elif io.key_ctrl and imgui.is_key_pressed(imgui.Key.a, False) and document:
            document.selection = (0, document.size)
            self.hex_selection_anchor = 0
        elif document and imgui.is_key_pressed(imgui.Key.home, False):
            self._hex_move_cursor(0, document, io.key_shift)
        elif document and imgui.is_key_pressed(imgui.Key.delete, False):
            start, end = self._hex_selection()
            if end > start:
                document.replace(start, end - start, b'')
                self._clear_hex_edit_buffers()
        elif document and any(imgui.is_key_pressed(key, False) for key in (
                imgui.Key.left_arrow, imgui.Key.right_arrow, imgui.Key.up_arrow,
                imgui.Key.down_arrow, imgui.Key.page_up, imgui.Key.page_down,
                imgui.Key.home, imgui.Key.end)):
            if imgui.is_key_pressed(imgui.Key.left_arrow, False):
                offset = document.cursor - 1
            elif imgui.is_key_pressed(imgui.Key.right_arrow, False):
                offset = document.cursor + 1
            elif imgui.is_key_pressed(imgui.Key.up_arrow, False):
                offset = document.cursor - self.hex_display_bytes_per_row
            elif imgui.is_key_pressed(imgui.Key.down_arrow, False):
                offset = document.cursor + self.hex_display_bytes_per_row
            elif imgui.is_key_pressed(imgui.Key.page_up, False):
                offset = document.cursor - self.hex_display_bytes_per_row * 16
            elif imgui.is_key_pressed(imgui.Key.page_down, False):
                offset = document.cursor + self.hex_display_bytes_per_row * 16
            else:
                offset = document.size - 1
            self._hex_move_cursor(offset, document, io.key_shift)

    def _hex_move_cursor(self, offset, document, extend_selection=False):
        if not document.size:
            return
        offset = min(max(offset, 0), document.size - 1)
        document.cursor = offset
        document.selection = (
            (self.hex_selection_anchor, offset + 1)
            if extend_selection else (offset, offset + 1)
        )
        if not extend_selection:
            self.hex_selection_anchor = offset
        self.hex_offset_input = self._format_hex_offset(offset, self.hex_offset_base)

    def _hex_search_results_panel(self, document):
        imgui.text_colored(TEXT, 'SEARCH RESULTS')
        if not self.hex_search_results:
            imgui.text_disabled('No matches yet. Enter a value and choose Find all.')
            return
        imgui.text_disabled(f'{len(self.hex_search_results):,} match(es)')
        clipper = imgui.ListClipper()
        clipper.begin(len(self.hex_search_results))
        while clipper.step():
            for index in range(clipper.display_start, clipper.display_end):
                offset = self.hex_search_results[index]
                selected = document.cursor == offset
                if imgui.selectable(
                    f'{self._format_hex_offset(offset, self.hex_offset_base)}##match-{offset}',
                    selected,
                )[0]:
                    document.cursor = offset
                    length = len(self.hex_search_pattern) if self.hex_search_pattern else 1
                    document.selection = (offset, min(document.size, offset + length))
                    self.hex_selection_anchor = offset
                    self.hex_offset_input = self._format_hex_offset(
                        offset, self.hex_offset_base
                    )

    def _hex_inspector(self, document):
        imgui.text_colored(TEXT, 'DATA INSPECTOR')
        imgui.text_disabled(
            f'Offset {self._format_hex_offset(document.cursor, self.hex_offset_base)}'
        )
        data = document.read(document.cursor, 8)
        data += b'\0' * (8 - len(data))
        for title, endian in (('Little endian', '<'), ('Big endian', '>')):
            imgui.text_colored(BLUE, title)
            imgui.text(f'U8  {data[0]}     I8  {int.from_bytes(data[:1], "little", signed=True)}')
            imgui.text(f'U16 {struct.unpack(endian + "H", data[:2])[0]}     I16 {struct.unpack(endian + "h", data[:2])[0]}')
            imgui.text(f'U32 {struct.unpack(endian + "I", data[:4])[0]}')
            imgui.text(f'I32 {struct.unpack(endian + "i", data[:4])[0]}')
            imgui.text(f'U64 {struct.unpack(endian + "Q", data)[0]}')
            imgui.text(f'F32 {struct.unpack(endian + "f", data[:4])[0]:.7g}')
            imgui.text(f'F64 {struct.unpack(endian + "d", data)[0]:.7g}')
        if (self.hex_analysis and self.hex_analysis_document == document.path
                and self.hex_analysis_revision == document.revision):
            imgui.separator()
            imgui.text_colored(TEXT, 'CHECKSUMS')
            for name in ('Checksum-8', 'Checksum-16', 'CRC32', 'Custom CRC-16', 'MD5', 'SHA-1', 'SHA-512'):
                imgui.text_disabled(name)
                imgui.text_wrapped(self.hex_analysis[name])
            imgui.text_colored(TEXT, 'MOST COMMON BYTES')
            histogram = self.hex_analysis['Histogram']
            for value in sorted(range(256), key=histogram.__getitem__, reverse=True)[:8]:
                imgui.text(f'{value:02X}   {histogram[value]:,}')
        imgui.separator()
        imgui.text_colored(TEXT, 'CUSTOM CRC-16')
        imgui.set_next_item_width(70)
        _, self.hex_crc_polynomial = imgui.input_text_with_hint(
            'Poly', '1021', self.hex_crc_polynomial, imgui.InputTextFlags_.chars_hexadecimal,
        )
        imgui.set_next_item_width(70)
        _, self.hex_crc_initial = imgui.input_text_with_hint(
            'Init', 'FFFF', self.hex_crc_initial, imgui.InputTextFlags_.chars_hexadecimal,
        )
        imgui.set_next_item_width(70)
        _, self.hex_crc_xor_out = imgui.input_text_with_hint(
            'Xor-out', '0000', self.hex_crc_xor_out, imgui.InputTextFlags_.chars_hexadecimal,
        )

    def hex_editor_workspace(self):
        self._hex_keyboard()
        available = imgui.get_content_region_avail()
        progress = min(1.0, max(0.0, (imgui.get_time() - self.workspace_transition_started) / 0.48))
        eased = 1 - (1 - progress) ** 3
        content_pos = imgui.get_cursor_screen_pos()
        imgui.set_cursor_screen_pos(imgui.ImVec2(content_pos.x + 18 * (1 - eased), content_pos.y))
        imgui.push_style_var(imgui.StyleVar_.alpha, 0.10 + 0.90 * eased)
        panel_pos = imgui.get_cursor_screen_pos()
        panel_size = imgui.ImVec2(available.x, max(180, available.y - 28))
        self.glass_surface(panel_pos, panel_size, key='hex-pane')
        imgui.begin_child('hex-editor-workspace', panel_size, imgui.ChildFlags_.borders)
        document = self.hex_document
        if self.app.job is not None:
            imgui.text_colored(TEXT, 'HEX EDITOR')
            imgui.text_disabled('File operation in progress. Editing is paused until it completes.')
            imgui.end_child()
            self.draw_frame_light(panel_pos, panel_size)
            imgui.pop_style_var()
            return
        imgui.text_colored(TEXT, 'HEX EDITOR')
        imgui.same_line()
        imgui.text_disabled('Lazy mapped file editing · staged until Save')
        if self.animated_button('Open file', 'hex-open'):
            self._open_hex_document()
        if document is not None:
            if self.hex_show_file_actions:
                imgui.same_line()
                save_label = 'Save*' if document.modified else 'Save'
                if self.animated_button(save_label, 'hex-save'):
                    self._hex_save()
                imgui.same_line()
                if self.animated_button('Save As', 'hex-save-as'):
                    self._hex_save(save_as=True)
            if self.hex_show_edit_actions:
                imgui.same_line()
                if self.animated_button('Undo', 'hex-undo') and document.undo():
                    self._clear_hex_edit_buffers()
                    self.hex_status = 'Undid the last edit.'
                imgui.same_line()
                if self.animated_button('Redo', 'hex-redo') and document.redo():
                    self._clear_hex_edit_buffers()
                    self.hex_status = 'Redid the last edit.'
            if self.hex_show_analysis_actions:
                imgui.same_line()
                if self.animated_button('Compare', 'hex-compare'):
                    self._hex_compare()
                imgui.same_line()
                if self.animated_button('Analyze', 'hex-analyze') and self.app.job is None:
                    self._hex_analyze()
            imgui.same_line()
            if self.animated_button('Close file', 'hex-close'):
                self._hex_close_document()
                document = self.hex_document
                if document is None:
                    imgui.end_child()
                    imgui.pop_style_var()
                    return
            imgui.same_line()
            if self.animated_button('View options', 'hex-view-options'):
                imgui.open_popup('hex-view-options-popup')
            if imgui.begin_popup('hex-view-options-popup'):
                imgui.text_disabled('TOOLBARS')
                _, self.hex_show_file_actions = imgui.checkbox(
                    'Save actions', self.hex_show_file_actions
                )
                _, self.hex_show_edit_actions = imgui.checkbox(
                    'Edit actions', self.hex_show_edit_actions
                )
                _, self.hex_show_analysis_actions = imgui.checkbox(
                    'Compare and analysis', self.hex_show_analysis_actions
                )
                imgui.separator()
                imgui.text_disabled('DISPLAY')
                self._hex_setting_combo(
                    'Text encoding', 'hex_text_encoding',
                    tuple(HEX_TEXT_ENCODINGS), 190,
                )
                self._hex_setting_combo(
                    'Offset base', 'hex_offset_base',
                    ('Hexadecimal', 'Decimal', 'Octal'), 150,
                )
                self._hex_setting_combo(
                    'Visible columns', 'hex_visible_columns',
                    ('Hex and text', 'Text', 'Hex'), 170,
                )
                self._hex_setting_combo(
                    'Byte group size', 'hex_group_size', (1, 2, 4, 8, 16), 150,
                )
                self._hex_setting_combo(
                    'Bytes per row', 'hex_bytes_per_row', (8, 16, 24, 32, 48, 64), 150,
                )
                _, self.hex_fit_width = imgui.checkbox(
                    'Fit bytes to window width', self.hex_fit_width
                )
                _, self.hex_inspector_visible = imgui.checkbox(
                    'Show data inspector', self.hex_inspector_visible
                )
                _, self.hex_results_visible = imgui.checkbox(
                    'Show search results', self.hex_results_visible
                )
                imgui.end_popup()
            if self.hex_show_file_actions and self.hex_documents:
                imgui.same_line()
                imgui.set_next_item_width(min(320, available.x * 0.35))
                preview = os.path.basename(document.path) if document else 'Choose open file'
                if imgui.begin_combo('Open files', preview):
                    for item in self.hex_documents:
                        selected = item is document
                        if imgui.selectable(os.path.basename(item.path), selected)[0]:
                            if self.hex_document is not item:
                                self.hex_ascii_buffers.clear()
                                self.hex_cell_buffers.clear()
                                self.hex_search_results = []
                                self.hex_search_pattern = b''
                            self.hex_document = item
                            self.hex_analysis = None
                            self.hex_match = None
                        if selected:
                            imgui.set_item_default_focus()
                    imgui.end_combo()
                    document = self.hex_document
            if document is None:
                imgui.text_disabled('Choose an open file to edit.')
            else:
                imgui.text_disabled(f'{document.path}  ·  {explorer.short_size(document.size)}'
                                    f'{"  ·  UNSAVED" if document.modified else ""}')
                imgui.set_next_item_width(110)
                changed, self.hex_offset_input = imgui.input_text_with_hint(
                    '##hex-goto', f'Offset ({self.hex_offset_base.lower()})',
                    self.hex_offset_input,
                )
                imgui.same_line()
                if self.animated_button('Go', 'hex-goto'):
                    try:
                        offset = self._parse_hex_offset(
                            self.hex_offset_input or '0', self.hex_offset_base
                        )
                        if offset >= document.size and document.size:
                            raise ValueError('offset is past end of file')
                        document.cursor = offset
                        document.selection = (offset, offset + 1 if offset < document.size else offset)
                        self.hex_selection_anchor = offset
                    except ValueError as exc:
                        self.hex_status = f'Invalid offset: {exc}'
                imgui.same_line()
                if self.animated_button('Copy', 'hex-copy'):
                    self._hex_copy()
                imgui.same_line()
                if self.animated_button('Cut', 'hex-cut'):
                    self._hex_copy(cut=True)
                imgui.same_line()
                if self.animated_button('Paste', 'hex-paste'):
                    self._hex_paste()
                imgui.same_line()
                if self.animated_button('Overwrite mode' if self.hex_overwrite else 'Insert mode', 'hex-mode'):
                    self.hex_overwrite = not self.hex_overwrite
                imgui.same_line()
                imgui.set_next_item_width(180)
                _, self.hex_edit_input = imgui.input_text_with_hint(
                    '##hex-edit', 'Hex bytes to write', self.hex_edit_input,
                    imgui.InputTextFlags_.chars_hexadecimal | imgui.InputTextFlags_.auto_select_all,
                )
                imgui.same_line()
                if self.animated_button('Write', 'hex-write'):
                    try:
                        data = bytes.fromhex(self.hex_edit_input)
                        if not data:
                            raise ValueError('enter at least one byte')
                        delete_length = min(len(data), document.size - document.cursor) if self.hex_overwrite else 0
                        document.replace(document.cursor, delete_length, data)
                        self._clear_hex_edit_buffers()
                        self.hex_edit_input = ''
                        self.hex_status = f'Wrote {len(data)} byte(s) at 0x{document.cursor - len(data):X}.'
                    except ValueError as exc:
                        self.hex_status = f'Invalid hex bytes: {exc}'
                imgui.separator()
                imgui.set_next_item_width(180)
                _, self.hex_search_input = imgui.input_text_with_hint('##hex-search', 'Find value', self.hex_search_input)
                imgui.same_line()
                imgui.set_next_item_width(120)
                if imgui.begin_combo('##hex-mode', self.hex_search_mode):
                    for mode in ('HEX', 'TEXT', 'UTF-8', 'UTF-16 LE', 'UTF-16 BE', 'U8', 'U16 LE', 'U32 LE', 'U64 LE', 'F32 LE', 'F64 LE'):
                        if imgui.selectable(mode, mode == self.hex_search_mode)[0]:
                            self.hex_search_mode = mode
                    imgui.end_combo()
                imgui.same_line()
                _, self.hex_search_reverse = imgui.checkbox('Reverse', self.hex_search_reverse)
                imgui.same_line()
                if self.animated_button('Find next', 'hex-find'):
                    self._hex_find()
                imgui.same_line()
                if self.animated_button('Find all', 'hex-find-all') and self.app.job is None:
                    self._hex_find_all()
                imgui.same_line()
                imgui.set_next_item_width(180)
                _, self.hex_replace_input = imgui.input_text_with_hint(
                    '##hex-replace', 'Replace with', self.hex_replace_input
                )
                imgui.same_line()
                if self.animated_button('Replace', 'hex-replace'):
                    self._hex_replace()
                imgui.separator()
                content_available = imgui.get_content_region_avail()
                inspector_width = (
                    min(
                        max(180.0, self.hex_inspector_width),
                        max(180.0, content_available.x - 360.0),
                    )
                    if self.hex_inspector_visible else 0.0
                )
                grid_width = max(
                    100.0,
                    content_available.x - inspector_width
                    - (26.0 if self.hex_inspector_visible else 0.0),
                )
                bytes_per_row = self._effective_hex_bytes_per_row(grid_width)
                self.hex_display_bytes_per_row = bytes_per_row
                show_hex = self.hex_visible_columns != 'Text'
                show_text = self.hex_visible_columns != 'Hex'
                imgui.begin_child('hex-byte-grid', imgui.ImVec2(grid_width, 0),
                                  imgui.ChildFlags_.borders)
                flags = (imgui.TableFlags_.scroll_y | imgui.TableFlags_.borders_inner_v
                         | imgui.TableFlags_.resizable)
                column_count = 1 + (bytes_per_row if show_hex else 0) + (1 if show_text else 0)
                if imgui.begin_table('hex-byte-table', column_count, flags):
                    imgui.table_setup_column('OFFSET', imgui.TableColumnFlags_.width_fixed, 92)
                    if show_hex:
                        for value in range(bytes_per_row):
                            column_width = 40 if (value + 1) % self.hex_group_size == 0 else 36
                            imgui.table_setup_column(
                                f'{value:02X}', imgui.TableColumnFlags_.width_fixed, column_width
                            )
                    if show_text:
                        imgui.table_setup_column('TEXT', imgui.TableColumnFlags_.width_stretch)
                    imgui.table_headers_row()
                    row_count = (document.size + bytes_per_row - 1) // bytes_per_row
                    clipper = imgui.ListClipper()
                    clipper.begin(row_count)
                    selection_start, selection_end = sorted(document.selection)
                    while clipper.step():
                        for row in range(clipper.display_start, clipper.display_end):
                            offset = row * bytes_per_row
                            data = document.read(
                                offset, min(bytes_per_row, document.size - offset)
                            )
                            imgui.table_next_row()
                            imgui.table_next_column()
                            imgui.text_disabled(
                                self._format_hex_offset(offset, self.hex_offset_base)
                            )
                            if show_hex:
                                for index, value in enumerate(data):
                                    imgui.table_next_column()
                                    byte_offset = offset + index
                                    buffer = self.hex_cell_buffers.get(
                                        byte_offset, f'{value:02X}'
                                    )
                                    imgui.set_next_item_width(31)
                                    changed, buffer = imgui.input_text(
                                        f'##hex-byte-{byte_offset}',
                                        buffer,
                                        imgui.InputTextFlags_.chars_hexadecimal
                                        | imgui.InputTextFlags_.auto_select_all,
                                    )
                                    if changed:
                                        self.hex_cell_buffers[byte_offset] = buffer
                                    commit = changed and len(buffer) == 2
                                    if imgui.is_item_deactivated_after_edit():
                                        buffer = self.hex_cell_buffers.pop(
                                            byte_offset, buffer
                                        )
                                        commit = bool(buffer)
                                    if commit:
                                        self.hex_cell_buffers.pop(byte_offset, None)
                                        self._commit_hex_cell(byte_offset, buffer)
                                        self.hex_ascii_buffers.pop(offset, None)
                                    if imgui.is_item_clicked():
                                        if imgui.get_io().key_shift:
                                            document.selection = (
                                                self.hex_selection_anchor, byte_offset + 1
                                            )
                                        else:
                                            document.selection = (
                                                byte_offset, byte_offset + 1
                                            )
                                            self.hex_selection_anchor = byte_offset
                                        document.cursor = byte_offset
                                        self.hex_offset_input = self._format_hex_offset(
                                            byte_offset, self.hex_offset_base
                                        )
                                for _ in range(len(data), bytes_per_row):
                                    imgui.table_next_column()
                            if show_text:
                                imgui.table_next_column()
                                text_value = self._decode_hex_row(data, self.hex_text_encoding)
                                text_buffer = self.hex_ascii_buffers.get(offset, text_value)
                                imgui.set_next_item_width(-1)
                                changed, text_buffer = imgui.input_text(
                                    f'##hex-ascii-{offset}', text_buffer
                                )
                                if changed:
                                    self.hex_ascii_buffers[offset] = text_buffer
                                if imgui.is_item_deactivated_after_edit():
                                    text_buffer = self.hex_ascii_buffers.pop(
                                        offset, text_buffer
                                    )
                                    self._commit_ascii_row(offset, text_value, text_buffer)
                                if imgui.is_item_clicked():
                                    document.cursor = offset
                                    document.selection = (offset, offset + len(data))
                                    self.hex_selection_anchor = offset
                                    self.hex_offset_input = self._format_hex_offset(
                                        offset, self.hex_offset_base
                                    )
                    imgui.end_table()
                imgui.end_child()
                if self.hex_inspector_visible:
                    imgui.same_line()
                    inspector_delta = self.drag_splitter(
                        'hex-inspector', max(100.0, content_available.y)
                    )
                    if inspector_delta:
                        self.hex_inspector_width = min(
                            max(180.0, inspector_width - inspector_delta),
                            max(180.0, content_available.x - 360.0),
                        )
                        inspector_width = self.hex_inspector_width
                    imgui.same_line()
                    imgui.begin_child(
                        'hex-inspector', imgui.ImVec2(0, 0), imgui.ChildFlags_.borders
                    )
                    if self.hex_results_visible:
                        self._hex_search_results_panel(document)
                    else:
                        self._hex_inspector(document)
                    imgui.end_child()
        else:
            imgui.spacing()
            imgui.text_disabled('Open any file to inspect, search, edit, compare, and analyze its bytes.')
            imgui.text_disabled('The source is mapped lazily. Edits remain staged until you explicitly save.')
        if self.hex_status:
            imgui.separator()
            imgui.text_wrapped(self.hex_status)
        imgui.end_child()
        self.draw_frame_light(panel_pos, panel_size)
        imgui.pop_style_var()

    def toolbar(self):
        a = self.app
        d = a.cur
        _, selected = a.focused()
        imgui.push_style_color(imgui.Col_.button, PANEL_ALT)
        imgui.push_style_color(imgui.Col_.button_hovered, imgui.ImVec4(0.12, 0.12, 0.14, 1))
        imgui.push_style_color(imgui.Col_.button_active, imgui.ImVec4(0.18, 0.17, 0.21, 1))
        if self.animated_button(
            self.original_icon_label('bundle', 'Open bundle'), 'toolbar-open'
        ):
            a.action_open()
        imgui.pop_style_color(3)
        imgui.same_line()
        if self.animated_button(
            self.original_icon_label('save', 'Save'), 'toolbar-save'
        ) and d is not None:
            a.action_save(d)
        imgui.same_line()
        if self.animated_button(
            self.original_icon_label('import', 'Import'), 'toolbar-import'
        ) and d is not None and not d.b.truncated:
            a.action_import_resources(d)
        imgui.same_line()
        if self.animated_button(
            self.original_icon_label('export', 'Export'), 'toolbar-export'
        ) and selected is not None:
            imgui.open_popup('dashboard-export')
        if imgui.begin_popup('dashboard-export'):
            a.export_items(d, selected)
            imgui.end_popup()
        imgui.same_line()
        if self.animated_button(
            self.original_icon_label('replace', 'Replace'), 'toolbar-replace'
        ) and d is not None and selected is not None and not d.b.truncated:
            a.action_replace(d, selected)
        imgui.same_line()
        if self.animated_button(
            self.original_icon_label('delete', 'Delete'), 'toolbar-delete'
        ) and d is not None and d.sel and not d.b.truncated:
            a.modal = {'kind': 'confirm_delete', 'doc': d.uid, 'ids': sorted(d.sel)}
        imgui.same_line()
        if self.animated_button(
            self.original_icon_label('more', 'More ...'), 'toolbar-more'
        ):
            self.menu_group_started.clear()
            imgui.open_popup('dashboard-more')
        more_open = imgui.begin_popup('dashboard-more')
        if more_open:
            self.menu_frame_start()
            if self.dashboard_begin_menu('Theme', group='theme'):
                for theme_name in theme.PALETTES:
                    if self.dashboard_menu_item(
                        theme_name,
                        selected=theme.ACTIVE_THEME == theme_name,
                        group='theme-options',
                    )[0]:
                        self.app.cfg['theme'] = theme_name
                        self.app.save_cfg()
                        theme.select(theme_name)
                        theme.apply()
                        apply_style()
                imgui.end_menu()
            imgui.separator()
            a.more_items()
            imgui.end_popup()
        self.finish_menu_frame(more_open)
        imgui.separator()

    def sidebar_light_effect(self):
        pos = imgui.get_window_pos()
        size = imgui.get_window_size()
        draw = imgui.get_window_draw_list()
        clip_min = imgui.ImVec2(pos.x + 1, pos.y + 1)
        clip_max = imgui.ImVec2(pos.x + size.x - 1, pos.y + size.y - 1)
        center = self.ambient_light_origin
        radius = max(900.0, size.y * 1.8)
        pulse = 0.94 + 0.06 * (0.5 + 0.5 * np.sin(imgui.get_time() * 0.38))
        draw.push_clip_rect(clip_min, clip_max, True)
        for ring in range(72, 0, -1):
            fraction = ring / 72
            alpha = (1 - fraction) ** 1.5 * 0.012 * pulse
            draw.add_circle_filled(
                center,
                radius * fraction,
                self.color(GLOW[0], GLOW[1], GLOW[2], alpha),
                48,
            )
        for ring in range(20, 0, -1):
            fraction = ring / 20
            draw.add_circle_filled(
                center,
                240 * fraction,
                self.color(0.82, 0.78, 1.0, 0.004 * pulse),
                48,
            )
        draw.pop_clip_rect()

    def sidebar(self):
        a = self.app
        self.nav_target_rect = None
        sidebar_width = imgui.get_window_width()
        self.sidebar_light_effect()
        toggle_x = sidebar_width - 42.0 if not self.sidebar_collapsed else (sidebar_width - 22.0) * 0.5
        if not self.sidebar_collapsed:
            header_y = imgui.get_cursor_pos_y()
            header_pos = imgui.get_cursor_screen_pos()
            header_draw = imgui.get_window_draw_list()
            header_draw.add_rect_filled(
                header_pos,
                imgui.ImVec2(header_pos.x + sidebar_width - 28, header_pos.y + 58),
                self.color(0.035, 0.035, 0.048, 0.76),
                10,
            )
            header_draw.add_rect(
                header_pos,
                imgui.ImVec2(header_pos.x + sidebar_width - 28, header_pos.y + 58),
                self.color(GLOW[0] * 0.50, GLOW[1] * 0.52, GLOW[2] * 0.50, 0.52),
                10,
                1,
            )
            logo_pos = imgui.ImVec2(header_pos.x + 10, header_pos.y + 10)
            header_draw.add_rect_filled(
                logo_pos,
                imgui.ImVec2(logo_pos.x + 36, logo_pos.y + 36),
                self.color(0.12 + GLOW[0] * 0.12, 0.11 + GLOW[1] * 0.10,
                           0.16 + GLOW[2] * 0.13, 1),
                9,
            )
            self.draw_original_icon(
                header_draw,
                'bundle',
                imgui.ImVec2(logo_pos.x + 18, logo_pos.y + 18),
                TEXT,
            )
            imgui.set_cursor_screen_pos(imgui.ImVec2(header_pos.x + 55, header_pos.y + 10))
            imgui.text_colored(TEXT, 'BNDL EXPLORER')
            imgui.set_cursor_screen_pos(imgui.ImVec2(header_pos.x + 55, header_pos.y + 31))
            imgui.text_disabled('BUNDLE WORKSPACE')
            imgui.set_cursor_screen_pos(header_pos)
            imgui.dummy(imgui.ImVec2(sidebar_width - 28, 58))
            header_bottom = imgui.get_cursor_pos_y()
            imgui.set_cursor_pos_y(header_y)
        imgui.set_cursor_pos_x(max(0.0, toggle_x))
        toggle_icon = (
            theme.I.ICON_FA_ANGLE_RIGHT
            if self.sidebar_collapsed else theme.I.ICON_FA_ANGLE_LEFT
        )
        if imgui.small_button(f'{toggle_icon}##toggle-sidebar'):
            self.sidebar_collapsed = not self.sidebar_collapsed
        if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
            imgui.set_tooltip(
                'Expand sidebar' if self.sidebar_collapsed else 'Collapse sidebar'
            )
        if self.sidebar_collapsed:
            self.collapsed_sidebar()
            return
        imgui.set_cursor_pos_y(max(header_bottom, imgui.get_cursor_pos_y()))

        imgui.push_style_var(imgui.StyleVar_.frame_padding, (8, 5))
        imgui.push_style_var(imgui.StyleVar_.item_spacing, (8, 5))
        imgui.spacing()
        imgui.text_disabled('MAIN')
        current_is_home = isinstance(a.tab, explorer.Browser) and a.tab.loc[0] == 'home'
        home_clicked = self.sidebar_icon_selectable(
            'home', 'Home', 'home', current_is_home
        )
        self.nav_item('home', current_is_home, home_clicked)
        if home_clicked:
            self.navigate_home()
        self.selected_accent(current_is_home)

        open_clicked = self.sidebar_icon_selectable(
            'bundle', 'Open bundle', 'open-bundle', False
        )
        if open_clicked:
            a.action_open()
        experimental_selected = self.workspace == 'experimental'
        experimental_clicked = self.sidebar_icon_selectable(
            'file', 'EXPERIMENTAL', 'experimental', experimental_selected
        )
        if experimental_clicked:
            self.activate_workspace('experimental', imgui.get_time())
        self.selected_accent(experimental_selected)

        imgui.spacing()
        if a.docs:
            imgui.text_disabled('OPEN BUNDLES')
            for d in a.docs:
                label = f'{d.name}  [{d.b.platform}]'
                bundle_selected = a.cur is d
                bundle_clicked = self.sidebar_icon_selectable(
                    'bundle', label, f'bundle-{d.uid}', bundle_selected
                )
                self.nav_item(f'bundle:{d.uid}', bundle_selected, bundle_clicked)
                if bundle_clicked:
                    self.defer(a.show_doc, d)
                    a.set_type_filter(d, None)
                self.selected_accent(a.cur is d)
                a.drop_on(d)
                if a.cur is d:
                    imgui.indent(12)
                    counts = {}
                    for resource in d.b.resources:
                        counts[resource.type] = counts.get(resource.type, 0) + 1
                    type_ids = sorted(counts, key=type_name)
                    category_token = (d.uid, tuple(type_ids))
                    if category_token != self.category_token:
                        self.category_token = category_token
                        self.category_entered_at = imgui.get_time()
                    for category_index, type_id in enumerate(type_ids):
                        category_delay = min(category_index * 0.045, 0.32)
                        category_progress = min(
                            1.0,
                            max(0.0, (imgui.get_time() - self.category_entered_at - category_delay) / 0.30),
                        )
                        category_eased = 1 - (1 - category_progress) ** 3
                        imgui.push_style_var(imgui.StyleVar_.alpha, 0.12 + 0.88 * category_eased)
                        selected = d.type_filter == type_id
                        category_clicked = self.sidebar_icon_selectable(
                            'folder',
                            f'{type_name(type_id)}  {counts[type_id]}',
                            f'category-{d.uid}-{type_id}',
                            selected,
                        )
                        category_min = imgui.get_item_rect_min()
                        category_max = imgui.get_item_rect_max()
                        effect_key = f'category:{d.uid}:{type_id}'
                        if category_clicked:
                            mouse = imgui.get_mouse_pos()
                            self.click_effects[effect_key] = (imgui.get_time(), mouse.x, mouse.y)
                        self.draw_click_effect(effect_key, category_min, category_max)
                        if category_clicked:
                            a.set_type_filter(d, None if selected else type_id)
                        self.selected_accent(selected)
                        imgui.pop_style_var()
                    imgui.unindent(12)
        else:
            imgui.text_disabled('OPEN BUNDLES')
            imgui.text_disabled('Open a bundle to browse its resources.')

        imgui.spacing()
        imgui.text_disabled('FOLDERS')
        for i, path in enumerate(a.pinned()):
            label = os.path.basename(path.rstrip('\\/')) or path
            selected = isinstance(a.tab, explorer.Browser) and a.tab.loc == ('folder', path)
            folder_clicked = self.sidebar_icon_selectable(
                'folder', label, f'folder-{i}', selected
            )
            self.nav_item(f'folder:{os.path.normcase(path)}', selected, folder_clicked)
            if folder_clicked:
                self.defer(a.go_folder, path)
            self.selected_accent(selected)
            if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
                imgui.set_tooltip(path)
        self.draw_nav_indicator()
        imgui.spacing()
        callout_height = 130.0
        callout_y = imgui.get_window_height() - callout_height - 24.0
        imgui.set_cursor_pos_y(max(imgui.get_cursor_pos_y(), callout_y))
        imgui.begin_child(
            'sidebar-callout',
            imgui.ImVec2(-1, callout_height),
            imgui.ChildFlags_.borders,
        )
        imgui.text_colored(TEXT, 'Ready to explore?')
        imgui.text_wrapped('Open a bundle to browse and inspect its resources.')
        if self.animated_button(
            self.original_icon_label('bundle', 'Open bundle'),
            'sidebar-open-bundle',
        ):
            a.action_open()
        imgui.same_line()
        if imgui.small_button(f'{theme.I.ICON_FA_FOLDER_PLUS}##sidebar-add-folder'):
            path = filedialog.pick_folder('Add a folder to the navigation pane')
            if path:
                pins = a.cfg.setdefault('nav_folders', [])
                if path not in pins:
                    pins.append(path)
                    a.save_cfg()
        if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
            imgui.set_tooltip('Add folder')
        imgui.end_child()
        imgui.pop_style_var(2)

    def defer(self, fn, *args):
        """Do fn(*args) at the start of the next frame. Changing the open bundle / folder while the frame is being
        drawn left the panels drawn for the old one half done (an ImGui child never ended: the app aborted, e.g.
        on Home while a bundle was open)."""
        self.deferred.append((fn, args))

    def navigate_home(self):
        self.defer(self.app.go_folder, None)

    def main_panel(self):
        a = self.app
        if a.cur is not None:
            self.bundle_resources(a.cur)
        elif isinstance(a.tab, explorer.Browser) and a.tab.loc[0] == 'folder':
            self.folder_contents(a.tab)
        else:
            self.home_contents()

    def home_contents(self):
        a = self.app
        imgui.text_colored(TEXT, 'Your bundles')
        imgui.text_disabled('Open a bundle to browse its resources and inspect field data.')
        imgui.spacing()
        imgui.push_style_color(imgui.Col_.button, PANEL_ALT)
        imgui.push_style_color(imgui.Col_.button_hovered, imgui.ImVec4(0.12, 0.12, 0.14, 1))
        if self.animated_button(
            self.original_icon_label('bundle', 'Open a bundle'), 'home-open'
        ):
            a.action_open()
        imgui.pop_style_color(2)
        imgui.spacing()
        imgui.text_disabled('RECENT FILES')
        recents = [path for path in a.cfg.get('recent', []) if os.path.exists(path)]
        if not recents:
            imgui.text_disabled('Bundles opened on this computer will appear here.')
            return
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
        if imgui.begin_table('dashboard-recents', 3, flags):
            imgui.table_setup_column('Bundle', imgui.TableColumnFlags_.width_stretch, 2)
            imgui.table_setup_column('Location', imgui.TableColumnFlags_.width_stretch, 3)
            imgui.table_setup_column('Size', imgui.TableColumnFlags_.width_fixed, 85)
            imgui.table_headers_row()
            for i, path in enumerate(recents):
                imgui.table_next_row()
                imgui.table_next_column()
                icon, _ = self.original_icon_data('bundle')
                if imgui.selectable(
                                    f'{icon}  {os.path.basename(path)}##recent{i}', False,
                                    imgui.SelectableFlags_.span_all_columns
                                    | imgui.SelectableFlags_.allow_double_click)[0]:
                    if imgui.is_mouse_double_clicked(0):
                        self.defer(a.open_path, path)
                imgui.table_next_column()
                imgui.text_disabled(os.path.dirname(path))
                imgui.table_next_column()
                try:
                    imgui.text(explorer.short_size(os.path.getsize(path)))
                except OSError:
                    imgui.text_disabled('Unavailable')
            imgui.end_table()

    def bundle_resources(self, d):
        a = self.app
        self.resource_entries_rendered = []
        imgui.text_colored(TEXT, d.name)
        imgui.same_line()
        imgui.text_colored(BLUE, d.b.platform)
        imgui.same_line()
        imgui.text_disabled(f'{len(d.b.resources):,} resources')
        imgui.set_next_item_width(-1)
        imgui.text_disabled('FILTER RESOURCES')
        changed, d.filter = imgui.input_text('##dashboard-resource-search', d.filter)
        if changed:
            d.invalidate()
        rows = a.visible_rows(d)
        imgui.spacing()
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
                 | imgui.TableFlags_.resizable | imgui.TableFlags_.sortable | imgui.TableFlags_.hideable)
        if not imgui.begin_table('dashboard-resources', 4, flags):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column('RESOURCE', imgui.TableColumnFlags_.width_stretch, 2.4)
        imgui.table_setup_column('ID', imgui.TableColumnFlags_.width_fixed, 145)
        imgui.table_setup_column('TYPE', imgui.TableColumnFlags_.width_stretch, 1.3)
        imgui.table_setup_column('SIZE', imgui.TableColumnFlags_.width_fixed, 75)
        imgui.table_headers_row()
        sort_specs = imgui.table_get_sort_specs()
        if sort_specs is not None and sort_specs.specs_dirty and sort_specs.specs_count:
            spec = sort_specs.get_specs(0)
            column_to_sort = {0: 0, 1: 1, 2: 2, 3: 4}
            sort_column = column_to_sort.get(spec.column_index)
            if sort_column is not None:
                d.sort = (sort_column, spec.sort_direction == imgui.SortDirection.ascending)
                d.invalidate()
            sort_specs.specs_dirty = False
        clipper = imgui.ListClipper()
        clipper.begin(len(rows))
        now = imgui.get_time()
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                resource = rows[i]
                row_delay = min((i - clipper.display_start) * 0.026, 0.38)
                row_progress = min(
                    1.0,
                    max(0.0, (now - self.resource_entered_at - row_delay) / 0.32),
                )
                row_eased = 1 - (1 - row_progress) ** 3
                row_alpha = 0.12 + 0.88 * row_eased
                row_slide = 13 * (1 - row_eased)
                imgui.table_next_row()
                imgui.table_next_column()
                selected = resource.id in d.sel
                row_pos = imgui.get_cursor_screen_pos()
                imgui.push_style_var(imgui.StyleVar_.alpha, row_alpha)
                row_clicked = imgui.selectable(
                    f'##resource-{resource.id}', selected,
                    imgui.SelectableFlags_.span_all_columns
                    | imgui.SelectableFlags_.allow_overlap
                    | imgui.SelectableFlags_.allow_double_click,
                    imgui.ImVec2(0, imgui.get_frame_height()),
                )[0]
                row_max = imgui.get_item_rect_max()
                a.item_events(d, rows, i, resource)
                display_name = a.display_name(d, resource)[0]
                self.resource_entries_rendered.append(
                    (row_pos.x, row_pos.y, display_name, resource.type)
                )
                click_key = f'resource:{d.uid}:{resource.id}'
                if row_clicked:
                    mouse = imgui.get_mouse_pos()
                    self.click_effects[click_key] = (now, mouse.x, mouse.y)
                self.draw_click_effect(
                    click_key,
                    row_pos,
                    row_max,
                )
                self.draw_original_icon(
                    imgui.get_window_draw_list(), 'file',
                    imgui.ImVec2(row_pos.x + 12 + row_slide,
                                 row_pos.y + imgui.get_frame_height() * 0.5),
                    BLUE if selected else None,
                    type_id=resource.type,
                )
                imgui.set_cursor_screen_pos(
                    imgui.ImVec2(row_pos.x + 26 + row_slide, row_pos.y)
                )
                a.name_text(d, resource)
                imgui.table_next_column()
                imgui.text_disabled(ops.id_text(resource.id))
                imgui.table_next_column()
                imgui.text_disabled(type_name(resource.type))
                imgui.table_next_column()
                size = sum(resource.size(k) for k in range(4))
                imgui.text_disabled(explorer.short_size(size))
                imgui.pop_style_var()
        imgui.end_table()
        a.background_menu(d)

    def inspector(self):
        a = self.app
        doc, resource = a.focused()
        if doc is a.cur and resource is not None:
            token = (doc.uid, resource.id)
            now = imgui.get_time()
            if token != self.inspector_resource_token:
                self.inspector_resource_token = token
                self.inspector_resource_started = now
            progress = min(
                1.0,
                max(0.0, (now - self.inspector_resource_started) / 0.28),
            )
            alpha = 0.08 + 0.92 * (1 - (1 - progress) ** 3)
            imgui.push_style_var(imgui.StyleVar_.alpha, alpha)
            a.details_panel()
            imgui.pop_style_var()
            return
        self.inspector_resource_token = None
        d = a.cur
        imgui.text_colored(TEXT, 'Bundle overview')
        imgui.text_disabled(explorer.ellipsis(d.path or d.name, 42))
        imgui.separator()
        stats = (
            ('PLATFORM', d.b.platform),
            ('VERSION', str(d.b.version)),
            ('RESOURCES', f'{len(d.b.resources):,}'),
            ('FILE FLAGS', f'{d.b.flags:#x}'),
            ('ROOT RESOURCE', f'{d.b.root_id:016X}' if d.b.root_id else 'None'),
        )
        for label, value in stats:
            imgui.text_disabled(label)
            imgui.text(value)
            imgui.spacing()
        counts = {}
        for item in d.b.resources:
            counts[item.type] = counts.get(item.type, 0) + 1
        imgui.separator()
        imgui.text_disabled('RESOURCE TYPES')
        for type_id in sorted(counts, key=lambda item: -counts[item]):
            imgui.text(type_name(type_id))
            imgui.same_line()
            text_w = imgui.calc_text_size(str(counts[type_id])).x
            imgui.set_cursor_pos_x(imgui.get_window_width() - text_w - 20)
            imgui.text_disabled(str(counts[type_id]))

    def folder_contents(self, browser):
        a = self.app
        path = browser.loc[1]
        imgui.text_colored(TEXT, os.path.basename(path.rstrip('\\/')) or path)
        imgui.text_disabled(path)
        imgui.set_next_item_width(-1)
        changed, browser.search = imgui.input_text('Filter this folder', browser.search)
        entries = explorer.folder_entries(path, a.folder_cache)
        if browser.search:
            term = browser.search.casefold()
            entries = [entry for entry in entries if term in entry[0].casefold()]
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
        if not imgui.begin_table('dashboard-folder', 3, flags):
            return
        imgui.table_setup_column('NAME', imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_column('TYPE', imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_column('SIZE', imgui.TableColumnFlags_.width_fixed, 80)
        imgui.table_headers_row()
        now = imgui.get_time()
        for i, (name, is_dir, size, modified, platform) in enumerate(entries):
            full = os.path.join(path, name)
            delay = min(i * 0.024, 0.36)
            progress = (now - self.folder_entered_at - delay) / 0.34
            progress = min(1.0, max(0.0, progress))
            eased = 1 - (1 - progress) ** 3
            row_alpha = 0.12 + 0.88 * eased
            slide = 15 * (1 - eased)
            imgui.table_next_row()
            imgui.table_next_column()
            column_pos = imgui.get_cursor_screen_pos()
            imgui.set_cursor_screen_pos(imgui.ImVec2(column_pos.x + slide, column_pos.y))
            imgui.push_style_var(imgui.StyleVar_.alpha, row_alpha)
            selected = full in browser.sel
            icon_kind = 'folder' if is_dir else 'bundle' if platform else 'file'
            icon, _ = self.original_icon_data(icon_kind)
            clicked = imgui.selectable(f'{icon}  {name}##entry{i}', selected,
                               imgui.SelectableFlags_.span_all_columns
                               | imgui.SelectableFlags_.allow_double_click)[0]
            item_min = imgui.get_item_rect_min()
            item_max = imgui.get_item_rect_max()
            self.folder_entries_rendered.append((item_min.x, item_min.y, name, icon_kind))
            row_key = f'folder:{os.path.normcase(full)}'
            if clicked:
                mouse = imgui.get_mouse_pos()
                self.click_effects[row_key] = (now, mouse.x, mouse.y)
                browser.sel = {full}
                if imgui.is_mouse_double_clicked(0):
                    self.defer(a.go_folder if is_dir else a.open_path, full)
            self.draw_click_effect(row_key, item_min, item_max)
            self.selected_accent(selected)
            imgui.table_next_column()
            imgui.text_disabled('Folder' if is_dir else 'Sound stream' if platform == 'SPS'
                                else f'Bundle [{platform}]' if platform else 'Bundle')
            imgui.table_next_column()
            imgui.text_disabled('—' if is_dir else explorer.short_size(size))
            imgui.pop_style_var()
        imgui.end_table()

    def footer(self):
        a = self.app
        imgui.separator()
        if a.busy:
            imgui.progress_bar(a.busy[1], imgui.ImVec2(180, 0))
            imgui.same_line()
            imgui.text(a.busy[0])
        else:
            if a.cur is not None:
                rows = a.visible_rows(a.cur)
                imgui.text_disabled(f'{len(rows):,} resources')
                if a.cur.sel:
                    imgui.same_line()
                    imgui.text_disabled(f'· {len(a.cur.sel)} selected')
            imgui.same_line()
            imgui.text_disabled('·')
            imgui.same_line()
            imgui.text_disabled(a.status)


def run(paths=(), screenshot=None, frames=12, select=None, tab=None, expand=False, view=None, folder=None,
        type_filter=None):
    immvision.use_rgb_color_order()
    app = app_module.App(paths, select)
    app.test_tab = tab
    app.expand_all = expand
    if view:
        app.cfg['view'] = view
    if folder:
        app.go_folder(folder)
    if type_filter is not None and app.cur is not None:
        app.cur.type_filter = type_filter

    selected_theme = app.cfg.get('theme', 'Violet')
    if selected_theme not in theme.PALETTES:
        selected_theme = 'Violet'
    theme.select(selected_theme)
    dashboard = Dashboard(app)
    dashboard.loading_screen_enabled = not bool(screenshot)
    dashboard.launch_fade_enabled = not bool(screenshot)
    params = app_module.runner_params(app)
    params.callbacks.setup_imgui_style = apply_style
    frame_count = [0]
    errors = {'last': None}

    def show_gui():
        if errors['last'] is None:
            # a frame that fails half way must not abort the app: ImGui closes what was left open and logs it
            io = imgui.get_io()
            io.config_error_recovery = True
            io.config_error_recovery_enable_assert = False
            io.config_error_recovery_enable_tooltip = False
            errors['last'] = 0.0
        try:
            dashboard.gui()
        except Exception as e:
            app_module.log_error()
            now = imgui.get_time()
            if now - errors['last'] > 2.0:              # one message, not one per frame
                app.status = f'Error (written to {app_module.ERROR_LOG}): {type(e).__name__}: {e}'
            errors['last'] = now
        if screenshot:
            ready = app.preview_ready()
            if ready and app.job is None and not app.thumbs.pending:
                frame_count[0] += 1
            if frame_count[0] > frames:
                hello_imgui.get_runner_params().app_shall_exit = True
                app.exit_ok = True

    params.callbacks.show_gui = show_gui
    hello_imgui.run(params)
    if app.job is not None and app.job.is_alive():
        app.job.join()
    for document in dashboard.hex_documents:
        document.close()
    if app.names.dirty:
        try:
            app.names.save()
        except OSError:
            pass
    if screenshot:
        image = hello_imgui.final_app_window_screenshot()
        from PIL import Image
        Image.fromarray(np.asarray(image)[..., :3]).save(screenshot)
