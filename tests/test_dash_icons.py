import unittest
from unittest.mock import Mock

import numpy as np
from imgui_bundle import imgui

from bndlx import dash_icons, new_ui, theme


class EmptyDocument:
    size = 0
    cursor = 0
    selection = None


class RecordingDrawList:
    def __init__(self):
        self.points = []

    def add_line(self, start, end, color, thickness):
        self.points.extend(((start.x, start.y), (end.x, end.y)))

    def add_circle(self, center, radius, color, segments, thickness):
        self.points.extend((
            (center.x - radius, center.y - radius),
            (center.x + radius, center.y + radius),
        ))

    def add_circle_filled(self, center, radius, color):
        self.points.extend((
            (center.x - radius, center.y - radius),
            (center.x + radius, center.y + radius),
        ))

    def add_rect(self, start, end, color, rounding, thickness, flags):
        self.points.extend(((start.x, start.y), (end.x, end.y)))


class DashboardIconTests(unittest.TestCase):
    def test_original_resource_icon_returns_imgui_color(self):
        glyph, color = new_ui.Dashboard.original_icon_data('file', 1)

        self.assertEqual(glyph, theme.type_icon(1)[0])
        self.assertIsInstance(color, imgui.ImVec4)

    def test_experimental_workspace_selection_updates_active_workspace(self):
        dashboard = new_ui.Dashboard.__new__(new_ui.Dashboard)
        dashboard.workspace = 'bundles'
        dashboard.workspace_transition_started = 0.0
        dashboard.content_transition_started = 0.0

        dashboard.activate_workspace('experimental', 2.5)

        self.assertEqual(dashboard.workspace, 'experimental')
        self.assertEqual(dashboard.workspace_transition_started, 2.5)
        self.assertEqual(dashboard.content_transition_started, 2.5)

    def test_blue_theme_is_selectable_and_violet_remains_default(self):
        try:
            theme.select('Blue')
            self.assertEqual(theme.ACTIVE_THEME, 'Blue')
            self.assertEqual(theme.PALETTE, theme.BLUE_THEME)
            self.assertEqual(theme.ACCENT, (0.22, 0.53, 0.96, 1.0))
            theme.select('White')
            self.assertEqual(theme.ACTIVE_THEME, 'White')
            self.assertEqual(theme.PALETTE, theme.WHITE_THEME)
            self.assertEqual(theme.ACCENT, (0.88, 0.89, 0.96, 1.0))
        finally:
            theme.select('Violet')
        self.assertEqual(theme.ACTIVE_THEME, 'Violet')
        self.assertEqual(theme.PALETTE, theme.DARK)

    def test_home_navigation_returns_to_app_home(self):
        dashboard = new_ui.Dashboard.__new__(new_ui.Dashboard)
        dashboard.app = Mock()

        dashboard.navigate_home()

        dashboard.app.go_folder.assert_called_once_with(None)

    def test_hex_home_cursor_movement_is_safe_for_empty_files(self):
        dashboard = new_ui.Dashboard.__new__(new_ui.Dashboard)
        document = EmptyDocument()

        dashboard._hex_move_cursor(0, document)

        self.assertEqual(document.cursor, 0)
        self.assertIsNone(document.selection)

    def test_loading_screen_stays_visible_then_completes(self):
        dashboard = new_ui.Dashboard.__new__(new_ui.Dashboard)
        dashboard.loading_screen_enabled = True
        dashboard.loading_screen_started = None

        self.assertTrue(dashboard.loading_screen_active(10.0))
        self.assertTrue(
            dashboard.loading_screen_active(10.0 + new_ui.LOADING_SCREEN_SECONDS - 0.01)
        )
        self.assertFalse(
            dashboard.loading_screen_active(10.0 + new_ui.LOADING_SCREEN_SECONDS)
        )
        self.assertFalse(dashboard.loading_screen_enabled)

    def test_launch_fade_runs_once_and_completes(self):
        dashboard = new_ui.Dashboard.__new__(new_ui.Dashboard)
        dashboard.launch_fade_enabled = True
        dashboard.launch_fade_started = None

        self.assertEqual(dashboard.launch_fade_alpha(5.0), 1.0)
        self.assertAlmostEqual(
            dashboard.launch_fade_alpha(5.0 + new_ui.LAUNCH_FADE_SECONDS / 2),
            0.25,
        )
        self.assertEqual(
            dashboard.launch_fade_alpha(5.0 + new_ui.LAUNCH_FADE_SECONDS),
            0.0,
        )
        self.assertFalse(dashboard.launch_fade_enabled)
        self.assertEqual(dashboard.launch_fade_alpha(8.0), 0.0)

    def test_loading_logo_uses_selected_theme_color(self):
        source = np.array(
            [[(27, 27, 27), (254, 2, 50)]],
            dtype=np.uint8,
        )

        blue = new_ui.tint_loading_logo(source, (0.22, 0.53, 0.96))

        np.testing.assert_array_equal(blue[0, 0], (27, 27, 27))
        np.testing.assert_array_equal(blue[0, 1], (56, 135, 245))

    def test_icons_share_a_centered_bounding_box(self):
        kinds = (
            'file', 'text', 'object', 'schema', 'image', 'sound', 'cube', 'motion',
            'home', 'folder', 'bundle', 'save', 'import', 'export', 'replace', 'delete',
            'more', 'search', 'pause', 'expand', 'play', 'stop', 'vehicle', 'map',
        )
        center = imgui.ImVec2(100, 100)
        for kind in kinds:
            with self.subTest(kind=kind):
                draw_list = RecordingDrawList()
                dash_icons.draw(draw_list, kind, center, 24, 0xffffffff)
                xs = [point[0] for point in draw_list.points]
                ys = [point[1] for point in draw_list.points]
                self.assertTrue(xs and ys)
                self.assertLessEqual(abs((min(xs) + max(xs)) / 2 - center.x), 1.5)
                self.assertLessEqual(abs((min(ys) + max(ys)) / 2 - center.y), 1.5)


if __name__ == '__main__':
    unittest.main()
