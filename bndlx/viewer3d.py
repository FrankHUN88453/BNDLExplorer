"""3D preview: meshes drawn with OpenGL (PyOpenGL, in imgui-bundle's context) into an offscreen framebuffer
that is shown as an ImGui image. Orbit with the left mouse button, pan with the right / middle one, zoom with
the wheel."""
import ctypes
import math

import numpy as np
from imgui_bundle import imgui

VS = '''
in vec3 aPos;
in vec3 aNrm;
in vec2 aUV;
uniform mat4 uMVP;
uniform mat4 uMV;
out vec3 vN;
out vec2 vUV;
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    vN = mat3(uMV) * aNrm;
    vUV = aUV;
}
'''
FS = '''
in vec3 vN;
in vec2 vUV;
uniform sampler2D uTex;
uniform int uUseTex;
uniform vec3 uTint;
out vec4 frag;
void main() {
    vec3 n = normalize(vN);
    float l = 0.35 + 0.65 * abs(n.z);
    vec4 c = uUseTex == 1 ? texture(uTex, vUV) : vec4(uTint, 1.0);
    frag = vec4(c.rgb * l, 1.0);
}
'''


def _perspective(fov, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov) / 2)
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def _look_at(eye, target, up):
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / max(np.linalg.norm(s), 1e-9)
    u = np.cross(s, f)
    m = np.eye(4, dtype=np.float32)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


class Viewer:
    def __init__(self):
        self.gl = None
        self.prog = None
        self.fbo = self.color = self.depth = None
        self.size = (0, 0)
        self.meshes = []            # [(vao, vbo, ibo, count, tex, tint)]
        self.textures = {}          # texture key -> gl id
        self.key = None
        self.yaw, self.pitch, self.dist = 0.6, 0.35, 1.0
        self.center = np.zeros(3, np.float32)
        self.pan = np.zeros(3, np.float32)
        self.radius = 1.0
        self.wire = False
        self.use_tex = True
        self.z_up = False
        self.error = None

    # -- GL setup ---------------------------------------------------------------------------------------------
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
        for i, name in enumerate(('aPos', 'aNrm', 'aUV')):
            GL.glBindAttribLocation(prog, i, name)
        GL.glLinkProgram(prog)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(prog).decode('latin1', 'replace'))
        self.prog = prog
        self.white = self._texture(np.full((1, 1, 4), 255, np.uint8))

    def _texture(self, img):
        GL = self.gl
        t = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, t)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        h, w = img.shape[:2]
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE,
                        np.ascontiguousarray(img, np.uint8))
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return t

    def _fbo(self, w, h):
        GL = self.gl
        if self.size == (w, h) and self.fbo:
            return
        if self.fbo:
            GL.glDeleteFramebuffers(1, [self.fbo])
            GL.glDeleteTextures([self.color])
            GL.glDeleteRenderbuffers(1, [self.depth])
        self.fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        self.color = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.color)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.color, 0)
        self.depth = GL.glGenRenderbuffers(1)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, self.depth)
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_DEPTH_COMPONENT24, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_DEPTH_ATTACHMENT, GL.GL_RENDERBUFFER, self.depth)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self.size = (w, h)

    # -- content ----------------------------------------------------------------------------------------------
    def clear(self):
        if self.gl is None:
            return
        GL = self.gl
        for vao, vbo, ibo, *_ in self.meshes:
            GL.glDeleteVertexArrays(1, [vao])
            GL.glDeleteBuffers(2, [vbo, ibo])
        self.meshes = []
        self.key = None

    def set_meshes(self, key, meshes, texture_images):
        """meshes: [MeshData]; texture_images: {texture id: RGBA array or None}."""
        if self.gl is None:
            self._init()
        GL = self.gl
        self.clear()
        self.key = key
        lo = np.min([m.pos.min(0) for m in meshes], 0) if meshes else np.zeros(3)
        hi = np.max([m.pos.max(0) for m in meshes], 0) if meshes else np.ones(3)
        self.center = ((lo + hi) / 2).astype(np.float32)
        self.radius = float(max(np.linalg.norm(hi - lo) / 2, 1e-3))
        self.dist = self.radius * 2.6
        self.pan = np.zeros(3, np.float32)
        for tid, img in texture_images.items():
            if tid not in self.textures and img is not None:
                self.textures[tid] = self._texture(img)
        for i, m in enumerate(meshes):
            nrm = m.normals()
            uv = m.uv if m.uv is not None else np.zeros((len(m.pos), 2), np.float32)
            data = np.ascontiguousarray(np.concatenate([m.pos, nrm, uv], 1), np.float32)
            idx = np.ascontiguousarray(m.tris.ravel(), np.uint32)
            vao = GL.glGenVertexArrays(1)
            GL.glBindVertexArray(vao)
            vbo, ibo = GL.glGenBuffers(2)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo)
            GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW)
            for loc, (n, off) in enumerate(((3, 0), (3, 12), (2, 24))):
                GL.glEnableVertexAttribArray(loc)
                GL.glVertexAttribPointer(loc, n, GL.GL_FLOAT, GL.GL_FALSE, 32, ctypes.c_void_p(off))
            GL.glBindVertexArray(0)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            self.meshes.append((vao, vbo, ibo, len(idx), self.textures.get(m.texture), m.tint))

    # -- drawing ----------------------------------------------------------------------------------------------
    def render(self, w, h):
        GL = self.gl
        self._fbo(w, h)
        prev_vp = GL.glGetIntegerv(GL.GL_VIEWPORT)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glViewport(0, 0, w, h)
        GL.glClearColor(0.13, 0.13, 0.14, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_SCISSOR_TEST)
        GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE if self.wire else GL.GL_FILL)
        up = np.array([0, 0, 1] if self.z_up else [0, 1, 0], np.float32)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        if self.z_up:
            d = np.array([cp * math.cos(self.yaw), cp * math.sin(self.yaw), sp], np.float32)
        else:
            d = np.array([cp * math.sin(self.yaw), sp, cp * math.cos(self.yaw)], np.float32)
        target = self.center + self.pan
        mv = _look_at(target + d * self.dist, target, up)
        proj = _perspective(45.0, w / max(h, 1), max(self.dist * 0.01, 1e-3), self.dist + self.radius * 4)
        mvp = proj @ mv
        GL.glUseProgram(self.prog)
        GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.prog, 'uMVP'), 1, GL.GL_TRUE, mvp)
        GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.prog, 'uMV'), 1, GL.GL_TRUE, mv)
        GL.glUniform1i(GL.glGetUniformLocation(self.prog, 'uTex'), 0)
        loc_use = GL.glGetUniformLocation(self.prog, 'uUseTex')
        loc_tint = GL.glGetUniformLocation(self.prog, 'uTint')
        GL.glActiveTexture(GL.GL_TEXTURE0)
        for vao, _, _, count, tex, tint in self.meshes:
            use = bool(tex) and self.use_tex and not self.wire
            GL.glUniform1i(loc_use, 1 if use else 0)
            GL.glUniform3f(loc_tint, *tint)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex if use else self.white)
            GL.glBindVertexArray(vao)
            GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT, None)
        GL.glBindVertexArray(0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glUseProgram(0)
        GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glViewport(*[int(x) for x in prev_vp])
        return self.color

    def widget(self, w, h):
        """Draw the view (w x h) at the cursor and handle the mouse."""
        if self.gl is None or not self.meshes:
            return
        tex = self.render(w, h)
        p0 = imgui.get_cursor_pos()
        imgui.image(imgui.ImTextureRef(tex), imgui.ImVec2(w, h), imgui.ImVec2(0, 1), imgui.ImVec2(1, 0))
        imgui.set_cursor_pos(p0)
        imgui.invisible_button('##view3d', imgui.ImVec2(w, h),
                               imgui.ButtonFlags_.mouse_button_left | imgui.ButtonFlags_.mouse_button_right
                               | imgui.ButtonFlags_.mouse_button_middle)
        io = imgui.get_io()
        if imgui.is_item_active():
            dx, dy = io.mouse_delta.x, io.mouse_delta.y
            if imgui.is_mouse_down(0):
                self.yaw -= dx * 0.01
                self.pitch = max(-1.55, min(1.55, self.pitch + dy * 0.01))
            else:
                s = self.dist * 0.0015
                right = np.array([math.cos(self.yaw), 0, -math.sin(self.yaw)], np.float32) if not self.z_up else \
                    np.array([-math.sin(self.yaw), math.cos(self.yaw), 0], np.float32)
                upv = np.array([0, 0, 1] if self.z_up else [0, 1, 0], np.float32)
                self.pan += (-right * dx + upv * dy) * s
        if imgui.is_item_hovered() and io.mouse_wheel:
            self.dist *= 0.88 ** io.mouse_wheel
        if imgui.is_item_hovered() and imgui.is_mouse_double_clicked(0):
            self.pan[:] = 0
            self.dist = self.radius * 2.6
