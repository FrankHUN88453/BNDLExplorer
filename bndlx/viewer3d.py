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
in vec4 aTan;
in float aAO;
in vec2 aUV2;
uniform mat4 uMVP;
uniform mat4 uMV;
out vec3 vN;
out vec2 vUV;
out vec3 vPos;
out vec4 vT;
out float vAO;
out vec2 vUV2;
void main() {
    vUV2 = aUV2;
    gl_Position = uMVP * vec4(aPos, 1.0);
    vN = mat3(uMV) * aNrm;
    vT = vec4(mat3(uMV) * aTan.xyz, aTan.w);
    vPos = (uMV * vec4(aPos, 1.0)).xyz;
    vUV = aUV;
    vAO = aAO;
}
'''
# Two looks: the plain one (texture x a head light), and the shaded one, close to the game's physically based
# vehicle / world shading: albedo (car paint mixed under the livery), normal map (RGB tangent-space normal,
# A roughness), specular map (RGB F0, A metalness), vertex AO, a key light, a sky / ground ambient and sky
# reflections; GGX with Schlick Fresnel, clear coat on paint; ACES tone mapping.
FS = '''
in vec3 vN;
in vec2 vUV;
in vec3 vPos;
in vec4 vT;
in float vAO;
in vec2 vUV2;
uniform sampler2D uTex;
uniform sampler2D uNormal;
uniform sampler2D uSpec;
uniform sampler2D uLights;
uniform int uUseLights;
uniform vec4 uLightsOn;
uniform mat4 uLightColours;
uniform int uLightsEmit;
uniform int uBlend;
uniform float uOpacity;
uniform vec3 uGlassTint;
uniform int uUseTex;
uniform int uAlphaTest;
uniform int uShaded;
uniform int uUseNormal;
uniform int uUseSpec;
uniform int uPaint;
uniform int uSpecMode;
uniform int uSpecAlpha;
uniform int uNormalMode;
uniform vec3 uTint;
uniform vec3 uSpecColour;
uniform float uRough;
uniform vec3 uPaintColour;
uniform vec3 uUp;
uniform float uFlipY;
out vec4 frag;
const float PI = 3.14159265;
vec3 sky(vec3 d) {
    float h = dot(d, uUp);
    vec3 ground = vec3(0.10, 0.095, 0.09);
    vec3 horizon = vec3(0.62, 0.64, 0.66);
    vec3 zenith = vec3(0.32, 0.45, 0.66);
    return h < 0.0 ? mix(horizon * 0.45, ground, clamp(-h * 4.0, 0.0, 1.0))
                   : mix(horizon, zenith, pow(clamp(h, 0.0, 1.0), 0.6));
}
vec3 aces(vec3 x) {
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}
void main() {
    vec4 c = uUseTex == 1 ? texture(uTex, vUV) : vec4(uTint, 1.0);
    if (uAlphaTest == 1 && (uNormalMode == 4 ? texture(uNormal, vUV).a : uUseTex == 1 ? c.a : 1.0) < 0.5)
        discard;                                        // the cut-out (normal map A on two wheel / badge shaders)
    vec3 n = normalize(vN);
    if (uShaded == 0) {
        float l = 0.35 + 0.65 * abs(n.z);
        frag = vec4(c.rgb * l, 1.0);
        return;
    }
    if (uBlend == 2) {                                  // colouring glass: multiplies what is behind
        frag = vec4(pow(uGlassTint, vec3(1.0 / 2.2)), 1.0);
        return;
    }
    vec3 v = normalize(-vPos);
    if (dot(n, v) < 0.0) n = -n;                      // two-sided
    vec3 albedo = pow(c.rgb, vec3(2.2));
    if (uPaint == 1) {
        vec3 paint = pow(uPaintColour, vec3(2.2));
        albedo = uUseTex == 1 ? mix(paint, albedo, c.a) : paint;
    }
    float rough = uRough;
    if (uUseNormal == 1) {
        vec4 nm = texture(uNormal, vUV);
        if (uNormalMode == 0 || uNormalMode >= 3) {     // 1, 2: the map holds only a roughness
            vec3 t = normalize(vT.xyz - n * dot(n, vT.xyz));
            vec3 b = cross(n, t) * vT.w;
            vec3 tn = nm.rgb * 2.0 - 1.0;
            tn.y *= uFlipY;
            n = normalize(t * tn.x + b * tn.y + n * max(tn.z, 0.05));
        }
        if (uSpecMode == 0 && uNormalMode < 3)
            rough = uNormalMode == 1 ? nm.g : nm.a;
    }
    vec3 f0 = uSpecColour;
    float metal = 0.0;
    float coat = 0.0;
    if (uUseSpec == 1) {
        vec4 s = texture(uSpec, vUV);
        if (uSpecMode == 0) {
            f0 = pow(s.rgb, vec3(2.2));
            if (uSpecAlpha == 1)
                coat = s.a;                             // mirror coat (the Alpha badge / wheel shaders)
            else if (uSpecAlpha == 2)
                metal = s.a;
            else if (uSpecAlpha == 3)
                rough = s.a;
        } else if (uSpecMode == 1) {
            f0 = vec3(0.02 + 0.2 * s.r);
            rough = 1.0 - 0.85 * s.g;
        } else {
            f0 = pow(s.rgb, vec3(2.2)) * 0.25;
        }
    }
    rough = clamp(rough, 0.04, 1.0);
    float ao = mix(1.0, vAO, 0.85);
    vec3 l = normalize(vec3(-0.45, 0.75, 0.55));
    vec3 h = normalize(l + v);
    float nl = max(dot(n, l), 0.0), nv = max(dot(n, v), 1e-3), nh = max(dot(n, h), 0.0), vh = max(dot(v, h), 0.0);
    float a2 = rough * rough * rough * rough;
    float d = a2 / (PI * pow(nh * nh * (a2 - 1.0) + 1.0, 2.0));
    float k = (rough + 1.0) * (rough + 1.0) / 8.0;
    float g = nl / (nl * (1.0 - k) + k) * nv / (nv * (1.0 - k) + k);
    vec3 f = f0 + (1.0 - f0) * pow(1.0 - vh, 5.0);
    vec3 spec = d * g * f / max(4.0 * nl * nv, 1e-3);
    vec3 kd = (1.0 - f) * (1.0 - metal);
    vec3 light = vec3(2.6);
    vec3 col = (kd * albedo / PI + spec) * light * nl;
    vec3 r = reflect(-v, n);
    vec3 fr = f0 + (max(vec3(1.0 - rough), f0) - f0) * pow(1.0 - nv, 5.0);
    vec3 env = mix(sky(r), (sky(n) + sky(r)) * 0.5, rough);
    col += (albedo * (1.0 - metal) * sky(n) * 0.9 + env * fr * (1.0 - rough * 0.6)) * ao;
    if (coat > 0.0) {                                   // as the game: albedo * (1 - R), the reflection * R
        float rc = coat + (1.0 - coat) * pow(1.0 - nv, 5.0);
        col = col * (1.0 - rc) + sky(r) * rc * ao;
    }
    if (uPaint == 1) {                                  // clear coat
        float cf = 0.04 + 0.96 * pow(1.0 - nv, 5.0);
        float cd = 0.0025 / (PI * pow(nh * nh * (0.0025 - 1.0) + 1.0, 2.0));
        col = col * (1.0 - cf) + (sky(r) * cf + cd * cf * light * nl * 0.25) * ao;
    }
    if (uUseLights == 1) {                              // the light masks in their material colours
        vec4 lm = texture(uLights, vUV2) * uLightsOn;
        vec3 e = uLightColours[0].rgb * lm.r + uLightColours[1].rgb * lm.g + uLightColours[2].rgb * lm.b
                 + uLightColours[3].rgb * lm.a;
        col += uLightsEmit == 1 ? e : e * albedo;       // Lightmap shaders: lit by the lamp, not glowing
    }
    if (uBlend == 1) {                                  // glass: its own colour over what is behind + reflections
        float fg = 0.04 + 0.96 * pow(1.0 - nv, 5.0);
        float op = uUseTex == 1 ? max(uOpacity, c.a) : uOpacity;
        vec3 tint = uUseTex == 1 ? albedo : uGlassTint;
        float gd = 0.0004 / (PI * pow(nh * nh * (0.0004 - 1.0) + 1.0, 2.0));
        vec3 rgb = tint * op * (sky(n) * 0.9 + light * nl / PI) + (sky(r) + gd * light * nl * 0.1) * fg;
        float alpha = clamp(op + fg * (1.0 - op), 0.0, 1.0);
        frag = vec4(pow(aces(rgb * 1.1), vec3(1.0 / 2.2)), alpha);
        return;
    }
    frag = vec4(pow(aces(col * 1.1), vec3(1.0 / 2.2)), 1.0);
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
        self.meshes = []            # [(vao, vbo, ibo, count, tex, tint, alpha test, wire, overlay, material)]
        self.uvs = []               # UVs, tangents and AO of each mesh (for update_vertices)
        self.shaded = True          # the materials look (normal / specular maps, paint); False: texture x head light
        self.paint = (0.55, 0.05, 0.05)
        self.flip_y = -1.0          # normal map green: DirectX convention
        self.lights_on = [0.0, 0.0, 0.0, 0.0]    # light mask channels lit: R brake, G running, B head, A tail
        self.textures = {}          # texture key -> gl id
        self.key = None
        self.yaw, self.pitch, self.dist = 0.6, 0.35, 1.0
        self.center = np.zeros(3, np.float32)
        self.pan = np.zeros(3, np.float32)
        self.radius = 1.0
        self.fit = 2.6              # start distance in radii
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
        for i, name in enumerate(('aPos', 'aNrm', 'aUV', 'aTan', 'aAO', 'aUV2')):
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
        self.uvs = []
        self.key = None

    def update_vertices(self, changes):
        """New positions / normals for meshes already on the GPU (animation): {mesh index: (pos, nrm)}, the vertex
        counts unchanged."""
        if self.gl is None:
            return
        GL = self.gl
        for i, (pos, nrm) in changes.items():
            if i >= len(self.meshes):
                continue
            uv, tan, ao, uv2 = self.uvs[i]
            data = np.ascontiguousarray(np.concatenate([pos, nrm, uv, tan, ao[:, None], uv2], 1), np.float32)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.meshes[i][1])
            GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, data.nbytes, data)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)

    def set_meshes(self, key, meshes, texture_images, fit=2.6, keep_view=False, bounds=None):
        """meshes: [MeshData]; texture_images: {texture id: RGBA array or None}; fit: start distance in radii;
        keep_view: the same object changed (keep the camera); bounds: (lo, hi) to frame instead of every mesh."""
        if self.gl is None:
            self._init()
        GL = self.gl
        self.clear()
        self.key = key
        lo = np.min([m.pos.min(0) for m in meshes], 0) if meshes else np.zeros(3)
        hi = np.max([m.pos.max(0) for m in meshes], 0) if meshes else np.ones(3)
        if bounds is not None:
            lo, hi = np.asarray(bounds[0], np.float64), np.asarray(bounds[1], np.float64)
        if not keep_view:
            self.center = ((lo + hi) / 2).astype(np.float32)
            self.radius = float(max(np.linalg.norm(hi - lo) / 2, 1e-3))
            self.fit = fit
            self.dist = self.radius * fit
            self.pan = np.zeros(3, np.float32)
        for tid, img in texture_images.items():
            if tid not in self.textures and img is not None:
                self.textures[tid] = self._texture(img)
        for i, m in enumerate(meshes):
            nrm = m.normals()
            uv = m.uv if m.uv is not None else np.zeros((len(m.pos), 2), np.float32)
            material = getattr(m, 'normal_tex', None) or getattr(m, 'spec_tex', None) or getattr(m, 'paint', False)
            tan = (m.tangents() if getattr(m, 'normal_tex', None) and getattr(m, 'normal_mode', 0) in (0, 3, 4)
                   else np.tile(np.float32([1, 0, 0, 1]), (len(m.pos), 1)))
            ao = m.ao if getattr(m, 'ao', None) is not None else np.ones(len(m.pos), np.float32)
            uv2 = getattr(m, 'lights_uv', None)
            uv2 = uv2 if uv2 is not None else uv
            data = np.ascontiguousarray(np.concatenate([m.pos, nrm, uv, tan, ao[:, None], uv2], 1), np.float32)
            idx = np.ascontiguousarray(m.tris.ravel(), np.uint32)
            vao = GL.glGenVertexArrays(1)
            GL.glBindVertexArray(vao)
            vbo, ibo = GL.glGenBuffers(2)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo)
            GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW)
            for loc, (n, off) in enumerate(((3, 0), (3, 12), (2, 24), (4, 32), (1, 48), (2, 52))):
                GL.glEnableVertexAttribArray(loc)
                GL.glVertexAttribPointer(loc, n, GL.GL_FLOAT, GL.GL_FALSE, 60, ctypes.c_void_p(off))
            GL.glBindVertexArray(0)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            mat = {'normal': self.textures.get(getattr(m, 'normal_tex', None)),
                   'spec': self.textures.get(getattr(m, 'spec_tex', None)),
                   'f0': getattr(m, 'spec', None) or (0.04, 0.04, 0.04),
                   'rough': getattr(m, 'rough', None) or (0.25 if getattr(m, 'paint', False) else 0.55),
                   'paint': bool(getattr(m, 'paint', False)), 'spec_mode': int(getattr(m, 'spec_mode', 0)),
                   'spec_alpha': int(getattr(m, 'spec_alpha', 0)), 'normal_mode': int(getattr(m, 'normal_mode', 0)),
                   'lit': not (getattr(m, 'wire', False) or getattr(m, 'overlay', False)), 'has': bool(material),
                   'blend': int(getattr(m, 'blend', 0)), 'opacity': float(getattr(m, 'opacity', 1.0)),
                   'glass': tuple(getattr(m, 'glass_tint', None) or (0.012, 0.014, 0.016)),
                   'lights': self.textures.get(getattr(m, 'lights_tex', None)),
                   'light_colours': np.array(getattr(m, 'light_colours', None) or ((0, 0, 0),) * 4, np.float32),
                   'emit': bool(getattr(m, 'lights_emit', True)),
                   'centre': m.pos.mean(0) if len(m.pos) else np.zeros(3)}
            self.meshes.append((vao, vbo, ibo, len(idx), self.textures.get(m.texture), m.tint,
                                getattr(m, 'alpha_test', False), getattr(m, 'wire', False),
                                getattr(m, 'overlay', False), mat))
            self.uvs.append((uv, tan, ao, uv2))

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
        loc = {n: GL.glGetUniformLocation(self.prog, n) for n in (
            'uTex', 'uNormal', 'uSpec', 'uUseTex', 'uTint', 'uAlphaTest', 'uShaded', 'uUseNormal', 'uUseSpec',
            'uPaint', 'uSpecColour', 'uRough', 'uPaintColour', 'uUp', 'uFlipY', 'uSpecMode', 'uLights', 'uUseLights',
            'uLightsOn', 'uBlend', 'uOpacity', 'uGlassTint', 'uLightColours', 'uLightsEmit', 'uSpecAlpha',
            'uNormalMode')}
        GL.glUniform1i(loc['uTex'], 0)
        GL.glUniform1i(loc['uNormal'], 1)
        GL.glUniform1i(loc['uSpec'], 2)
        GL.glUniform3f(loc['uPaintColour'], *self.paint)
        GL.glUniform3f(loc['uUp'], *(mv[:3, :3] @ up))
        GL.glUniform1f(loc['uFlipY'], self.flip_y)
        GL.glUniform1i(loc['uLights'], 3)
        GL.glUniform4f(loc['uLightsOn'], *self.lights_on)
        cleared = False
        see_through = self.shaded and not self.wire

        def order(x):
            # opaque first, then glass from far to near (blending), then the overlays
            if x[8]:
                return (2, 0.0)
            if see_through and x[9]['blend']:
                c = x[9]['centre']
                return (1, float(mv[2, :3] @ c + mv[2, 3]))
            return (0, 0.0)

        blending = False
        for vao, _, _, count, tex, tint, alpha, wire, over, mat in sorted(self.meshes, key=order):
            if over and not cleared:          # overlays last, over the rest (markers inside a car stay visible)
                GL.glClear(GL.GL_DEPTH_BUFFER_BIT)
                cleared = True
            glass = see_through and mat['blend'] and not over and not wire
            if glass != blending:
                if glass:
                    GL.glEnable(GL.GL_BLEND)
                    GL.glDepthMask(GL.GL_FALSE)
                else:
                    GL.glDisable(GL.GL_BLEND)
                    GL.glDepthMask(GL.GL_TRUE)
                blending = glass
            if glass:
                if mat['blend'] == 2:
                    GL.glBlendFunc(GL.GL_DST_COLOR, GL.GL_ZERO)
                else:
                    GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glUniform1i(loc['uBlend'], mat['blend'] if glass else 0)
            GL.glUniform1f(loc['uOpacity'], mat['opacity'])
            GL.glUniform3f(loc['uGlassTint'], *mat['glass'])
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE if self.wire or wire else GL.GL_FILL)
            use = bool(tex) and self.use_tex and not self.wire and not wire
            shaded = self.shaded and mat['lit'] and not self.wire
            maps = shaded and self.use_tex
            GL.glUniform1i(loc['uUseTex'], 1 if use else 0)
            GL.glUniform1i(loc['uAlphaTest'], 1 if alpha else 0)
            GL.glUniform3f(loc['uTint'], *tint)
            GL.glUniform1i(loc['uShaded'], 1 if shaded else 0)
            GL.glUniform1i(loc['uUseNormal'], 1 if maps and mat['normal'] else 0)
            GL.glUniform1i(loc['uUseSpec'], 1 if maps and mat['spec'] else 0)
            GL.glUniform1i(loc['uPaint'], 1 if shaded and mat['paint'] else 0)
            GL.glUniform3f(loc['uSpecColour'], *mat['f0'])
            GL.glUniform1f(loc['uRough'], mat['rough'])
            GL.glUniform1i(loc['uSpecMode'], mat['spec_mode'])
            GL.glUniform1i(loc['uSpecAlpha'], mat['spec_alpha'])
            GL.glUniform1i(loc['uNormalMode'], mat['normal_mode'])
            lights = shaded and mat['lights'] and any(self.lights_on)
            GL.glUniform1i(loc['uUseLights'], 1 if lights else 0)
            if lights:
                lc = np.zeros((4, 4), np.float32)
                lc[:, :3] = mat['light_colours']
                GL.glUniformMatrix4fv(loc['uLightColours'], 1, GL.GL_FALSE, lc)
                GL.glUniform1i(loc['uLightsEmit'], 1 if mat['emit'] else 0)
            cut = mat['normal_mode'] == 4 and mat['normal'] and alpha and not self.wire and not wire
            if cut and not (maps and mat['normal']):
                GL.glUniform1i(loc['uNormalMode'], 4)
            elif not cut and mat['normal_mode'] == 4:
                GL.glUniform1i(loc['uNormalMode'], 3)
            for unit, t in ((0, tex if use else self.white), (1, mat['normal'] if (maps or cut) and mat['normal'] else
                                                               self.white), (2, mat['spec'] if maps and mat['spec']
                                                                             else self.white),
                            (3, mat['lights'] if lights else self.white)):
                GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
                GL.glBindTexture(GL.GL_TEXTURE_2D, t)
            GL.glBindVertexArray(vao)
            GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT, None)
        GL.glBindVertexArray(0)
        GL.glDisable(GL.GL_BLEND)
        GL.glDepthMask(GL.GL_TRUE)
        for unit in (3, 2, 1, 0):
            GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
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
            self.dist = self.radius * self.fit
