"""OpenGL renderer of the UI editor preview: a screen drawn like the game draws it, into a texture.

Draw list ops, in the game's element order (positions in the target's pixels, y down):
  ('quad', texture, [4 corners], [4 uvs], rgba, blend)   blend 'alpha' or 'add' (UIAdditiveShader: src * a + dst)
  ('mask', texture, [4 corners], [4 uvs])                a UIElement_Mask: the following quads keep only where
                                                         the mask texture's alpha is (masks nest: multiplied)
  ('unmask',)                                            a mask element that ends the last mask
Corners / uvs go top-left, top-right, bottom-right, bottom-left. Text is drawn as quads of rendered text images
(text_image), so labels are masked and ordered like everything else.
"""
import ctypes

import numpy as np

VS = '''
in vec2 aPos;
in vec2 aUV;
uniform vec2 uSize;
out vec2 vUV;
void main() {
    vUV = aUV;
    gl_Position = vec4(aPos.x / uSize.x * 2.0 - 1.0, 1.0 - aPos.y / uSize.y * 2.0, 0.0, 1.0);
}
'''

FS = '''
in vec2 vUV;
uniform sampler2D uTex;
uniform sampler2D uMask;
uniform vec4 uTint;
uniform vec2 uSize;
uniform int uMode;              // 0 draw, 1 mask alpha, 2 copy a mask (red) for multiplying
out vec4 frag;
void main() {
    if (uMode == 1) {
        frag = vec4(texture(uTex, vUV).a);
        return;
    }
    if (uMode == 2) {
        frag = vec4(texture(uTex, gl_FragCoord.xy / uSize).r);
        return;
    }
    vec4 c = texture(uTex, vUV) * uTint;
    c.a *= texture(uMask, gl_FragCoord.xy / uSize).r;
    frag = c;
}
'''


class UIRenderer:
    def __init__(self):
        self.gl = None
        self.size = (0, 0)
        self.error = None

    def _init(self):
        from OpenGL import GL
        self.gl = GL
        ver = GL.glGetString(GL.GL_SHADING_LANGUAGE_VERSION) or b'1.30'
        try:
            major, minor = [int(x) for x in ver.split()[0].split(b'.')[:2]]
        except ValueError:
            major, minor = 1, 30
        head = '#version 330 core\n' if (major, minor) >= (3, 30) else '#version 130\n'
        prog = GL.glCreateProgram()
        for kind, src in ((GL.GL_VERTEX_SHADER, VS), (GL.GL_FRAGMENT_SHADER, FS)):
            sh = GL.glCreateShader(kind)
            GL.glShaderSource(sh, head + src)
            GL.glCompileShader(sh)
            if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
                raise RuntimeError(GL.glGetShaderInfoLog(sh).decode('latin1', 'replace'))
            GL.glAttachShader(prog, sh)
        GL.glBindAttribLocation(prog, 0, 'aPos')
        GL.glBindAttribLocation(prog, 1, 'aUV')
        GL.glLinkProgram(prog)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(prog).decode('latin1', 'replace'))
        self.prog = prog
        self.loc = {n: GL.glGetUniformLocation(prog, n) for n in ('uSize', 'uTex', 'uMask', 'uTint', 'uMode')}
        self.vao = GL.glGenVertexArrays(1)
        self.vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(self.vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, 4 * 4 * 4, None, GL.GL_DYNAMIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 16, ctypes.c_void_p(0))
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, GL.GL_FALSE, 16, ctypes.c_void_p(8))
        GL.glBindVertexArray(0)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self.white = self._texture(np.full((1, 1, 4), 255, np.uint8))
        self.fbos = {}

    def _texture(self, img):
        GL = self.gl
        t = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, t)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        h, w = img.shape[:2]
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE,
                        np.ascontiguousarray(img, np.uint8))
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return t

    def texture(self, img):
        """A GL texture of an RGBA image (the caller deletes it with free)."""
        if self.gl is None:
            self._init()
        return self._texture(img)

    def free(self, tex):
        if self.gl is not None and tex:
            self.gl.glDeleteTextures([tex])

    def _target(self, name, w, h):
        GL = self.gl
        hit = self.fbos.get(name)
        if hit is not None and hit[2] == (w, h):
            return hit
        if hit is not None:
            GL.glDeleteFramebuffers(1, [hit[0]])
            GL.glDeleteTextures([hit[1]])
        fbo = GL.glGenFramebuffers(1)
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self.fbos[name] = hit = (fbo, tex, (w, h))
        return hit

    def _quad(self, pts, uvs):
        GL = self.gl
        v = np.array([[p[0], p[1], t[0], t[1]] for p, t in zip((pts[0], pts[1], pts[3], pts[2]),
                                                                (uvs[0], uvs[1], uvs[3], uvs[2]))], np.float32)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, v.nbytes, v)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

    def render(self, w, h, ops, background=(0.11, 0.12, 0.14, 1.0)):
        """Draw the ops into a w x h texture; returns its GL id (shown with uv (0, 1) - (1, 0))."""
        try:
            if self.gl is None:
                self._init()
            saved = self._save()
            try:
                return self._render(int(w), int(h), ops, background)
            finally:
                self._restore(saved)
        except Exception as ex:          # no GL 3 / driver trouble: the editor falls back to the simple drawing
            self.error = str(ex)
            return None

    def _save(self):
        """The GL state the app's own drawing relies on (the frame is drawn after this, by ImGui)."""
        GL = self.gl
        g = GL.glGetIntegerv
        return {
            'fb': int(g(GL.GL_FRAMEBUFFER_BINDING)), 'vp': [int(x) for x in g(GL.GL_VIEWPORT)],
            'scissor_box': [int(x) for x in g(GL.GL_SCISSOR_BOX)],
            'clear': [float(x) for x in GL.glGetFloatv(GL.GL_COLOR_CLEAR_VALUE)],
            'prog': int(g(GL.GL_CURRENT_PROGRAM)), 'vao': int(g(GL.GL_VERTEX_ARRAY_BINDING)),
            'buf': int(g(GL.GL_ARRAY_BUFFER_BINDING)), 'active': int(g(GL.GL_ACTIVE_TEXTURE)),
            'tex': int(g(GL.GL_TEXTURE_BINDING_2D)),
            'blend_fn': [int(g(x)) for x in (GL.GL_BLEND_SRC_RGB, GL.GL_BLEND_DST_RGB, GL.GL_BLEND_SRC_ALPHA,
                                             GL.GL_BLEND_DST_ALPHA)],
            'on': {cap: bool(GL.glIsEnabled(cap)) for cap in (GL.GL_BLEND, GL.GL_SCISSOR_TEST, GL.GL_DEPTH_TEST,
                                                              GL.GL_CULL_FACE)},
        }

    def _restore(self, st):
        GL = self.gl
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, st['fb'])
        GL.glViewport(*st['vp'])
        GL.glScissor(*st['scissor_box'])
        GL.glClearColor(*st['clear'])
        GL.glUseProgram(st['prog'])
        GL.glBindVertexArray(st['vao'])
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, st['buf'])
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glActiveTexture(st['active'])
        GL.glBindTexture(GL.GL_TEXTURE_2D, st['tex'])
        GL.glBlendFuncSeparate(*st['blend_fn'])
        for cap, on in st['on'].items():
            (GL.glEnable if on else GL.glDisable)(cap)

    def _render(self, w, h, ops, background):
        GL = self.gl
        colour = self._target('colour', w, h)
        mask = self._target('mask', w, h)
        tmp = self._target('tmp', w, h)
        prev_fb = GL.glGetIntegerv(GL.GL_FRAMEBUFFER_BINDING)
        prev_vp = GL.glGetIntegerv(GL.GL_VIEWPORT)
        GL.glViewport(0, 0, w, h)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glDisable(GL.GL_SCISSOR_TEST)
        GL.glUseProgram(self.prog)
        GL.glBindVertexArray(self.vao)
        GL.glUniform2f(self.loc['uSize'], float(w), float(h))
        GL.glUniform1i(self.loc['uTex'], 0)
        GL.glUniform1i(self.loc['uMask'], 1)
        full = [(0, 0), (w, 0), (w, h), (0, h)]
        unit = [(0, 0), (1, 0), (1, 1), (0, 1)]
        stack = []

        def build_mask():
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, mask[0])
            GL.glClearColor(1, 1, 1, 1)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            for _, tex, pts, uvs in stack:
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, tmp[0])
                GL.glClearColor(0, 0, 0, 0)
                GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                GL.glDisable(GL.GL_BLEND)
                GL.glUniform1i(self.loc['uMode'], 1)
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
                self._quad(pts, uvs)
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, mask[0])
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_ZERO, GL.GL_SRC_COLOR)              # mask *= this mask
                GL.glUniform1i(self.loc['uMode'], 2)
                GL.glBindTexture(GL.GL_TEXTURE_2D, tmp[1])
                self._quad(full, unit)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, colour[0])

        build_mask()
        GL.glClearColor(*background)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        GL.glEnable(GL.GL_BLEND)
        for op in ops:
            kind = op[0]
            if kind == 'quad':
                _, tex, pts, uvs, rgba, blend = op
                GL.glUniform1i(self.loc['uMode'], 0)
                if blend == 'add':
                    GL.glBlendFuncSeparate(GL.GL_SRC_ALPHA, GL.GL_ONE, GL.GL_ZERO, GL.GL_ONE)
                else:
                    GL.glBlendFuncSeparate(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA, GL.GL_ONE,
                                           GL.GL_ONE_MINUS_SRC_ALPHA)
                GL.glUniform4f(self.loc['uTint'], *[float(x) for x in rgba])
                GL.glActiveTexture(GL.GL_TEXTURE1)
                GL.glBindTexture(GL.GL_TEXTURE_2D, mask[1])
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex or self.white)
                self._quad(pts, uvs)
            elif kind == 'mask':
                stack.append(op)
                build_mask()
                GL.glEnable(GL.GL_BLEND)
            elif kind == 'unmask':
                if stack:
                    stack.pop()
                    build_mask()
                    GL.glEnable(GL.GL_BLEND)
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)
        GL.glDisable(GL.GL_BLEND)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, int(prev_fb))
        GL.glViewport(*[int(x) for x in prev_vp])
        return colour[1]


_FONTS = {}


def text_image(text, px, bold=True, spacing=0.0, italic=0.0):
    """White text on transparent, `px` pixels high (PIL; Arial as a stand-in for the game's fonts); spacing:
    extra pixels between characters, italic: slant in degrees."""
    from PIL import Image, ImageDraw, ImageFont
    px = max(4, int(round(px)))
    key = (px, bold)
    font = _FONTS.get(key)
    if font is None:
        for name in (('arialbd.ttf', 'segoeuib.ttf') if bold else ('arial.ttf', 'segoeui.ttf')):
            try:
                font = ImageFont.truetype(name, px)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        _FONTS[key] = font
    box = font.getbbox(text or ' ')
    extra = int(round(spacing * max(0, len(text) - 1)))
    slant = abs(np.tan(np.radians(italic))) if italic else 0.0
    h = max(1, int(px * 1.25))
    w = max(1, box[2] - min(0, box[0]) + 2 + max(0, extra) + int(slant * h))
    img = Image.new('RGBA', (w, h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    y = (h - px) // 2 - box[1] // 4
    if spacing:
        x = 1 - min(0, box[0])
        for ch in text:
            draw.text((x, y), ch, font=font, fill=(255, 255, 255, 255))
            x += font.getlength(ch) + spacing
    else:
        draw.text((1 - min(0, box[0]), y), text, font=font, fill=(255, 255, 255, 255))
    if slant:
        img = img.transform(img.size, Image.AFFINE, (1, slant if italic > 0 else -slant,
                                                     -slant * h if italic > 0 else 0, 0, 1, 0), Image.BICUBIC)
    return np.array(img, np.uint8)              # a writable copy (GlTexture takes no read-only arrays)
