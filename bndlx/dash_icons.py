"""Small set of original vector icons for the dashboard interface."""
from . import theme
from .restypes import T_CUBE, T_GOBJECT, T_GTYPE, T_LUA_CODE, T_STRINGS, T_TEXT, T_TEXTURE


def kind_for_type(type_id):
    if type_id == T_TEXTURE:
        return 'image'
    if type_id == T_CUBE:
        return 'cube'
    if type_id == T_GTYPE:
        return 'schema'
    if type_id == T_GOBJECT:
        return 'object'
    if type_id in (T_TEXT, T_LUA_CODE, T_STRINGS, 0x008, 0x053, 0x074):
        return 'text'
    if type_id in (0x080, 0x081, 0x082):
        return 'sound'
    if type_id in (0x105, 0x106, 0x207, 0x210):
        return 'vehicle'
    if type_id in (0x050, 0x203, 0x205, 0x20F, 0x214):
        return 'map'
    if type_id in (0x0B0, 0x0B1, 0x0B2, 0x0B3):
        return 'motion'
    return 'file'


def inline(kind, size=17, color=None):
    from imgui_bundle import imgui

    if color is None:
        color = imgui.get_style_color_vec4(imgui.Col_.text_disabled)
    height = max(imgui.get_frame_height(), size + 4)
    pos = imgui.get_cursor_screen_pos()
    center = imgui.ImVec2(pos.x + (size + 4) * 0.5, pos.y + height * 0.5)
    draw(imgui.get_window_draw_list(), kind, center, size, imgui.get_color_u32(color))
    imgui.dummy(imgui.ImVec2(size + 4, height))
    imgui.same_line(0, 3)


def draw(draw_list, kind, center, size, color):
    """Draw a crisp icon on a shared, centered coordinate grid."""
    x, y = center.x, center.y
    unit = size / 24
    stroke = max(1.25, size * 0.085)

    def point(px, py):
        return imgui_vec(x + px * unit, y + py * unit)

    def seg(x1, y1, x2, y2):
        draw_list.add_line(point(x1, y1), point(x2, y2), color, stroke)

    def circle(cx, cy, radius, filled=False):
        if filled:
            draw_list.add_circle_filled(point(cx, cy), radius * unit, color)
        else:
            draw_list.add_circle(point(cx, cy), radius * unit, color, 0, stroke)

    def rect(x1, y1, x2, y2, rounding=2):
        draw_list.add_rect(point(x1, y1), point(x2, y2), color, rounding * unit, stroke, 0)

    if kind in ('file', 'text', 'object', 'schema', 'image', 'sound', 'cube', 'motion'):
        if kind in ('file', 'text'):
            rect(-8, -10, 8, 10)
            for yy in (-4, 0, 4) if kind == 'text' else (-3, 3):
                seg(-5, yy, 5, yy)
        elif kind in ('object', 'schema'):
            seg(0, -4, -5, 4)
            seg(0, -4, 5, 4)
            circle(0, -7, 2.4, True)
            circle(-6, 6, 2.4, True)
            circle(6, 6, 2.4, True)
        elif kind == 'image':
            rect(-10, -8, 10, 8)
            circle(5, -3, 1.5, True)
            seg(-7, 5, -2, 0)
            seg(-2, 0, 1, 3)
            seg(1, 3, 4, 0)
            seg(4, 0, 7, 5)
        elif kind == 'sound':
            seg(-9.5, -3, -5.5, -3)
            seg(-5.5, -3, -1.5, -7)
            seg(-1.5, -7, -1.5, 7)
            seg(-1.5, 7, -5.5, 3)
            seg(-5.5, 3, -9.5, 3)
            seg(-9.5, -3, -9.5, 3)
            circle(1.5, 0, 4)
            circle(1.5, 0, 8)
        elif kind == 'cube':
            seg(0, -9, 8, -4)
            seg(8, -4, 0, 1)
            seg(0, 1, -8, -4)
            seg(-8, -4, 0, -9)
            seg(-8, -4, -8, 5)
            seg(-8, 5, 0, 10)
            seg(0, 10, 8, 5)
            seg(8, 5, 8, -4)
            seg(0, 1, 0, 10)
        else:
            circle(0, 0, 3, True)
            circle(-7, -6, 2, True)
            circle(7, -6, 2, True)
            circle(-7, 6, 2, True)
            circle(7, 6, 2, True)
            seg(-5, -4, -2, -2)
            seg(5, -4, 2, -2)
            seg(-5, 4, -2, 2)
            seg(5, 4, 2, 2)
    elif kind == 'home':
        seg(-10, -2, 0, -10)
        seg(0, -10, 10, -2)
        seg(-7, -4, -7, 9)
        seg(7, -4, 7, 9)
        seg(-7, 9, 7, 9)
        seg(-3, 9, -3, 1)
        seg(-3, 1, 3, 1)
        seg(3, 1, 3, 9)
    elif kind == 'folder':
        seg(-10, -6.5, -3, -6.5)
        seg(-3, -6.5, -1, -3.5)
        seg(-1, -3.5, 10, -3.5)
        seg(10, -3.5, 8, 6.5)
        seg(8, 6.5, -10, 6.5)
        seg(-10, 6.5, -10, -6.5)
    elif kind == 'bundle':
        seg(-9, -5, 0, -10)
        seg(0, -10, 9, -5)
        seg(9, -5, 0, 0)
        seg(0, 0, -9, -5)
        seg(-9, -5, -9, 5)
        seg(-9, 5, 0, 10)
        seg(0, 10, 9, 5)
        seg(9, 5, 9, -5)
        seg(0, 0, 0, 10)
    elif kind == 'save':
        rect(-9, -9, 9, 9)
        seg(-5, -8, -5, -2)
        seg(-5, -2, 5, -2)
        seg(5, -2, 5, -8)
        rect(-5, 2, 5, 7, 1)
    elif kind in ('import', 'export'):
        direction = 1 if kind == 'import' else -1
        seg(0, -direction * 8, 0, direction * 2)
        seg(0, direction * 2, -4, -direction * 2)
        seg(0, direction * 2, 4, -direction * 2)
        seg(-8, direction * 4, -8, direction * 9)
        seg(-8, direction * 9, 8, direction * 9)
        seg(8, direction * 9, 8, direction * 4)
    elif kind == 'replace':
        seg(-8, -4, 8, -4)
        seg(8, -4, 5, -7)
        seg(8, -4, 5, -1)
        seg(8, 4, -8, 4)
        seg(-8, 4, -5, 1)
        seg(-8, 4, -5, 7)
    elif kind == 'delete':
        seg(-8, -6, 8, -6)
        seg(-3, -9, 3, -9)
        seg(-6, -3, -5, 8)
        seg(-5, 8, 5, 8)
        seg(5, 8, 6, -3)
        seg(-2, -2, -2, 6)
        seg(2, -2, 2, 6)
    elif kind == 'more':
        for dx in (-6, 0, 6):
            circle(dx, 0, 1.5, True)
    elif kind == 'search':
        circle(-2, -2, 6)
        seg(2, 2, 8, 8)
    elif kind == 'pause':
        rect(-6, -8, -2, 8, 1)
        rect(2, -8, 6, 8, 1)
    elif kind == 'expand':
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            seg(sx * 3, sy * 9, sx * 9, sy * 9)
            seg(sx * 9, sy * 9, sx * 9, sy * 3)
    elif kind == 'play':
        seg(-6.5, -8, 6.5, 0)
        seg(6.5, 0, -6.5, 8)
        seg(-6.5, 8, -6.5, -8)
    elif kind == 'stop':
        rect(-7, -7, 7, 7, 2)
    elif kind == 'vehicle':
        seg(-9, 3, -7, -3)
        seg(-7, -3, -4, -7)
        seg(-4, -7, 4, -7)
        seg(4, -7, 7, -3)
        seg(7, -3, 9, 3)
        seg(-9, 3, 9, 3)
        seg(-9, 3, -9, 6)
        seg(9, 3, 9, 6)
        seg(-9, 6, 9, 6)
        circle(-5, 7, 1.7, True)
        circle(5, 7, 1.7, True)
    elif kind == 'map':
        circle(0, -3, 6)
        seg(-5, 1, 0, 10)
        seg(5, 1, 0, 10)
        circle(0, -3, 1.7, True)
    else:
        seg(-8, 0, 8, 0)


def resource_badge(draw_list, type_id, center, size, selected=False, alpha=1.0):
    kind = kind_for_type(type_id)
    color = imgui_color((0.38, 0.61, 1.0, 1.0), alpha) if selected else imgui_color(theme.type_icon(type_id)[1], alpha)
    half = size * .5
    draw_list.add_rect_filled(imgui_vec(center.x - half, center.y - half),
                              imgui_vec(center.x + half, center.y + half),
                              imgui_color((0.08, 0.10, 0.18, 1), alpha), 5)
    draw_list.add_rect(imgui_vec(center.x - half, center.y - half),
                       imgui_vec(center.x + half, center.y + half),
                       imgui_color((0.17, 0.21, 0.34, 1), alpha), 5)
    draw(draw_list, kind, center, size * 0.68, color)


def inline_badge(type_id, size=22, selected=False):
    from imgui_bundle import imgui

    height = max(imgui.get_frame_height(), size + 4)
    pos = imgui.get_cursor_screen_pos()
    center = imgui.ImVec2(pos.x + (size + 4) * 0.5, pos.y + height * 0.5)
    resource_badge(imgui.get_window_draw_list(), type_id, center, size, selected)
    imgui.dummy(imgui.ImVec2(size + 4, height))


def imgui_color(color, alpha=1.0):
    from imgui_bundle import imgui

    return imgui.get_color_u32(imgui.ImVec4(color[0], color[1], color[2], color[3] * alpha))


def imgui_vec(x, y):
    from imgui_bundle import imgui

    return imgui.ImVec2(x, y)
