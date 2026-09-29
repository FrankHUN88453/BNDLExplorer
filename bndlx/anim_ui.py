"""Skeleton and animation views: a car's damage animations played on the car itself (its body is skinned to the
damage skeleton), other animations on their skeleton with the paths their bones follow."""
import time
import traceback

import numpy as np
from imgui_bundle import imgui

from . import anim, mesh, theme

JOINT_COLOUR = (0.35, 0.85, 1.0)
PATH_COLOUR = (0.95, 0.75, 0.25)


def joint_markers(wt, size):
    """Octahedra at the joints and lines from each joint to its parent (vertex positions only; see
    skeleton_meshes for the triangles)."""
    oct_v = mesh._OCTA_V.astype(np.float64)
    return (wt[:, None, :] + oct_v[None] * size).reshape(-1, 3)


def skeleton_meshes(skel, wt, size, root_lines=True):
    n = skel.count
    t = (mesh._OCTA_T[None] + 6 * np.arange(n)[:, None, None]).reshape(-1, 3).astype(np.uint32)
    joints = mesh.MeshData(joint_markers(wt, size).astype(np.float32), None, t, 0, None, tint=JOINT_COLOUR,
                           nrm=np.tile(mesh._OCTA_V, (n, 1)), overlay=True)
    bones = bone_lines(skel, wt, root_lines)
    k = np.arange(n)
    lines = mesh.MeshData(bones.astype(np.float32), None, np.stack([k, k + n, k + n], 1).astype(np.uint32), 0, None,
                          tint=JOINT_COLOUR, nrm=np.full((2 * n, 3), 0.577, np.float32), wire=True, overlay=True)
    return [joints, lines]


def bone_lines(skel, wt, root_lines=True):
    """Line ends: joint i and its parent (the root to itself; without root_lines, the bones hanging from the root
    too: the car damage skeleton has most parts on the root, a star of lines to the car's floor)."""
    par = np.where(skel.parents >= 0, skel.parents, np.arange(skel.count))
    if not root_lines:
        par = np.where(skel.parents == 0, np.arange(skel.count), par)
    return np.concatenate([wt, wt[par]])


def axis_lines(wt, wr, turned, length):
    """Line ends along the local z axis (forward) of the bones that have a rotation track, collapsed on the joint
    for the others."""
    tip = wt + anim.qrot(wr, np.array([0.0, 0.0, 1.0])) * length
    return np.concatenate([wt, np.where(turned[:, None], tip, wt)])


def path_mesh(skel, a, steps=64):
    """The paths the animated bones follow over the whole animation (static lines)."""
    ts = np.linspace(0, a.duration, max(2, min(steps, a.keys * 2)))
    allpts = np.array([anim.pose(skel, a, t)[0] for t in ts])            # (steps, bones, 3)
    moving = np.nonzero(np.ptp(allpts, axis=0).max(1) > 1e-4)[0]          # bones that go somewhere
    if len(ts) < 2 or not len(moving):
        return None
    pts = allpts[:, moving]
    s, m = len(ts), len(moving)
    p = pts.reshape(-1, 3)
    i = np.arange(s - 1)[:, None] * m + np.arange(m)[None, :]
    lines = np.stack([i.ravel(), (i + m).ravel(), (i + m).ravel()], 1).astype(np.uint32)
    return mesh.MeshData(p.astype(np.float32), None, lines, 0, None, tint=PATH_COLOUR,
                         nrm=np.full((len(p), 3), 0.577, np.float32), wire=True, overlay=True)


def where_label(skel, a):
    """Where on the car an animation acts (front / rear, left / right), from its animated bones."""
    moving = [i for i in range(a.bones) if i and (a.trans_index[i] != 255 or a.rot_index[i] != 255)]
    if not moving:
        return ''
    c = skel.pos[moving].mean(0)
    fr = 'front' if c[2] > 0.6 else 'rear' if c[2] < -0.6 else ''
    lr = 'left' if c[0] > 0.35 else 'right' if c[0] < -0.35 else ''
    return ' '.join(x for x in (fr, lr) if x) or 'middle'


class AnimUI:
    """Mixin for App: anim_view (Animation / AnimationList / Skeleton)."""

    def find_skeleton(self, d, bones, prefer=None):
        """A skeleton with `bones` bones: the car's (import 0x4 of its VehicleGraphicsSpec), else any in the open
        bundles. Returns (Skeleton, resource) or (None, None)."""
        cands = [prefer] if prefer is not None else []
        cands += [x for x in d.b.resources if x.type == anim.T_SKELETON]
        cands += [x for o in self.docs if o is not d for x in o.b.resources if x.type == anim.T_SKELETON]
        for r in cands:
            try:
                s = anim.read_skeleton(r.data(0), d.b.e)
            except (anim.AnimError, ValueError):
                continue
            if bones is None or s.count == bones:
                return s, r
        return None, None

    def anim_scene(self, d, r, st):
        """Decode what the view shows: the animations, their skeleton, the car when it is the car's skeleton."""
        e = d.b.e
        if r.type == anim.T_SKELETON:
            anims = []
            skel = anim.read_skeleton(r.data(0), e)
            skel_res = r
        else:
            anims = anim.animations_of(r, e)
            vgs = next((x for x in d.b.resources if x.type == mesh.T_VGS), None)
            car_skel = None
            if vgs is not None:
                sid = {i.offset: i.id for i in vgs.imports()}.get(0x4)
                car_skel = d.b.find(sid) if sid else None
            skel, skel_res = self.find_skeleton(d, anims[0].bones if anims else None, car_skel)
        meshes, skinned = [], {}
        vgs = next((x for x in d.b.resources if x.type == mesh.T_VGS), None)
        car = False
        if skel is not None and vgs is not None and {i.offset: i.id for i in vgs.imports()}.get(0x4) == skel_res.id:
            try:
                meshes, _ = mesh.decode_vgs(d.b, vgs, self.mesh_lib, self.model_bundles(d)[1:], d.path, 0)
                car = True
            except mesh.MeshError:
                meshes = []
            for i, m in enumerate(meshes):
                if m.joints is not None and m.joints.max() < skel.count:
                    skinned[i] = (m.pos.astype(np.float64), m.normals().astype(np.float64))
        sk_at, bounds, pm = None, None, None
        if skel is not None and anims and not car:
            pm = path_mesh(skel, anims[min(st['sel'], len(anims) - 1)])
        if pm is not None:                           # frame the animated bones' paths, not a root far away
            bounds = (pm.pos.min(0), pm.pos.max(0))
            size = max(0.02, float(np.ptp(pm.pos, 0).max()) * 0.01)
        elif car or skel is None:
            size = 0.03
        else:
            bounds = (skel.pos.min(0), skel.pos.max(0))
            size = max(0.02, float(np.ptp(skel.pos, 0).max()) * 0.015)
        if skel is not None:
            sk_at = len(meshes)
            meshes = meshes + skeleton_meshes(skel, skel.pos, size, root_lines=False)
            n = skel.count
            k = np.arange(n)
            meshes.append(mesh.MeshData(np.concatenate([skel.pos, skel.pos]).astype(np.float32), None,
                                        np.stack([k, k + n, k + n], 1).astype(np.uint32), 0, None,
                                        tint=(1.0, 0.35, 0.3), nrm=np.full((2 * n, 3), 0.577, np.float32),
                                        wire=True, overlay=True))              # forward axes, filled in when posed
            if pm is not None:
                meshes.append(pm)
        texs = {t: self.texture_image(d, t, 1024) for t in {m.texture for m in meshes if m.texture}}
        return {'anims': anims, 'skel': skel, 'skel_res': skel_res, 'meshes': meshes, 'texs': texs,
                'skinned': skinned, 'sk_at': sk_at, 'size': size, 'car': car, 'bounds': bounds}

    def anim_view(self, d, r):
        st = getattr(self, 'anim_st', None)
        if st is None:
            st = self.anim_st = {'key': None, 'sel': 0, 't': 0.0, 'play': False, 'loop': True, 'speed': 1.0,
                                 'bones': True, 'scene': None, 'error': None, 'posed': None, 'last': 0.0}
        base = (d.uid, r.id, id(r.data(0)))
        if st.get('base') != base:                     # another resource: start at its first animation
            st.update(base=base, sel=0, t=0.0, play=False)
        key = base + (st['sel'],)
        if st['key'] != key:
            st['key'], st['posed'], st['error'] = key, None, None
            try:
                st['scene'] = self.anim_scene(d, r, st)
            except Exception as ex:
                traceback.print_exc()
                st['scene'], st['error'] = None, str(ex)
        if st['error']:
            imgui.text_wrapped(f'Cannot show this animation: {st["error"]}')
            return
        sc = st['scene']
        anims, skel = sc['anims'], sc['skel']
        a = anims[min(st['sel'], len(anims) - 1)] if anims else None
        # --- summary and choice
        if r.type == anim.T_SKELETON:
            imgui.text(f'Skeleton: {skel.count} bones' + (' (the car damage skeleton, shown on the car)'
                                                          if sc['car'] else ''))
        else:
            if len(anims) > 1:
                imgui.set_next_item_width(260)
                labels = [f'{k}: {where_label(skel, x) if skel is not None and sc["car"] else "animation"} '
                          f'({x.duration:.1f} s, {x.animated} bones)' for k, x in enumerate(anims)]
                ch, sel = imgui.combo('##animsel', st['sel'], labels)
                if ch:
                    st['sel'], st['t'] = sel, 0.0
            imgui.text(f'{a.keys} keys at {a.rate:g} per second ({a.duration:.2f} s), {a.bones} bones, '
                       f'{a.animated} animated, rotations {"f32" if a.codec == 0 else "packed"}')
            if skel is None:
                imgui.text_colored(imgui.ImVec4(1, 0.7, 0.3, 1), f'No skeleton with {a.bones} bones in the open '
                                   'bundles: open the bundle that has it to see the animation.')
            elif sc['car']:
                imgui.push_text_wrap_pos(0.0)
                imgui.text_disabled('A damage animation of this car: played on the car, whose body is skinned to '
                                    'the damage skeleton (the game plays it as far as the damage goes).')
                imgui.pop_text_wrap_pos()
        if skel is None:
            return
        # --- transport
        if a is not None:
            label = f'{theme.I.ICON_FA_PAUSE}  Pause' if st['play'] else f'{theme.I.ICON_FA_PLAY}  Play'
            if imgui.button(label + '##animplay'):
                if not st['play'] and st['t'] >= a.duration:
                    st['t'] = 0.0
                st['play'] = not st['play']
                st['last'] = time.perf_counter()
            imgui.same_line()
            if imgui.button(f'{theme.I.ICON_FA_BACKWARD_STEP}##animrew'):
                st['t'], st['play'] = 0.0, False
            imgui.same_line()
            imgui.set_next_item_width(max(120, imgui.get_content_region_avail().x - 330))
            ch, t = imgui.slider_float('##animt', st['t'], 0.0, max(a.duration, 1e-3), '%.2f s')
            if ch:
                st['t'], st['play'] = t, False
            imgui.same_line()
            _, st['loop'] = imgui.checkbox('Loop', st['loop'])
            imgui.same_line()
            imgui.set_next_item_width(70)
            _, st['speed'] = imgui.drag_float('##animspeed', st['speed'], 0.01, 0.05, 4.0, '%.2fx')
            if st['play']:
                now = time.perf_counter()
                st['t'] += (now - st['last']) * st['speed']
                st['last'] = now
                if st['t'] > a.duration:
                    if st['loop'] and a.duration > 0:
                        st['t'] %= a.duration
                    else:
                        st['t'], st['play'] = a.duration, False
        v = self.viewer_for(sc, st, key)
        if v is None:
            return
        _, v.use_tex = imgui.checkbox('Textures', v.use_tex)
        imgui.same_line()
        _, v.wire = imgui.checkbox('Wireframe', v.wire)
        imgui.same_line()
        ch, st['bones'] = imgui.checkbox('Bones', st['bones'])
        if ch:
            st['posed'] = None
        imgui.same_line()
        if imgui.button('Reset view##anim'):
            v.pan[:] = 0
            v.dist = v.radius * v.fit
            v.yaw, v.pitch = 0.6, 0.35
        # --- pose
        pk = (st['t'], st['sel'], st['bones'])
        if st['posed'] != pk:
            st['posed'] = pk
            wt, wr = anim.pose(skel, a, st['t'])
            changes = {}
            for i, (bp, bn) in sc['skinned'].items():
                m = sc['meshes'][i]
                p, n = anim.skin(bp, bn, m.joints, m.weights, skel, wt, wr)
                changes[i] = (p.astype(np.float32), n.astype(np.float32))
            n = skel.count
            if st['bones']:
                jp, lp = joint_markers(wt, sc['size']), bone_lines(skel, wt, root_lines=False)
            else:                                   # hidden: every vertex on one point draws nothing
                jp, lp = np.zeros((6 * n, 3)), np.zeros((2 * n, 3))
            show_axes = a is not None and not sc['car'] and st['bones']
            turned = (a.rot_index != 255) if show_axes else np.zeros(n, bool)
            ap = axis_lines(wt, wr, turned, sc['size'] * 8)
            changes[sc['sk_at']] = (jp.astype(np.float32), np.tile(mesh._OCTA_V, (n, 1)).astype(np.float32))
            changes[sc['sk_at'] + 1] = (lp.astype(np.float32), np.full((2 * n, 3), 0.577, np.float32))
            changes[sc['sk_at'] + 2] = (ap.astype(np.float32), np.full((2 * n, 3), 0.577, np.float32))
            v.update_vertices(changes)
        avail = imgui.get_content_region_avail()
        extra = int(imgui.get_frame_height_with_spacing() * 7) if r.type == anim.T_SKELETON else 0
        v.widget(max(64, int(avail.x) - 4), max(64, int(avail.y) - 24 - extra))
        imgui.text_disabled('Left drag: turn, right / middle drag: move, wheel: zoom, double click: fit')
        if r.type == anim.T_SKELETON:
            self.bone_table(skel)

    def viewer_for(self, sc, st, key):
        """The 3D viewer holding this scene (uploaded again when another view used it)."""
        if self.viewer is None:
            from .viewer3d import Viewer
            self.viewer = Viewer()
        v = self.viewer
        if v.key != ('anim',) + key:
            try:
                v.set_meshes(('anim',) + key, sc['meshes'], sc['texs'], 2.2 if sc['car'] else 1.6,
                             keep_view=bool(v.key and v.key[:1] == ('anim',) and v.key[1:4] == key[:3]),
                             bounds=sc['bounds'])
            except Exception as ex:
                traceback.print_exc()
                imgui.text_wrapped(f'OpenGL: {ex}')
                return None
            self.model['uploaded'] = None         # the model view uploads its own meshes again
            st['posed'] = None
        return v

    def bone_table(self, skel):
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
                 | imgui.TableFlags_.resizable)
        if not imgui.begin_table('bones', 4, flags, imgui.ImVec2(0, imgui.get_content_region_avail().y)):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column('#', imgui.TableColumnFlags_.width_fixed, 28)
        imgui.table_setup_column('Parent', imgui.TableColumnFlags_.width_fixed, 50)
        imgui.table_setup_column('Position (x left, y up, z forward; m)', imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_column('Name hash', imgui.TableColumnFlags_.width_fixed, 90)
        imgui.table_headers_row()
        for i in range(skel.count):
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(str(i))
            imgui.table_next_column()
            imgui.text(str(skel.parents[i]) if skel.parents[i] >= 0 else '-')
            imgui.table_next_column()
            imgui.text('%.3f  %.3f  %.3f' % tuple(skel.pos[i]))
            imgui.table_next_column()
            imgui.text(f'{skel.ids[i]:08X}')
        imgui.end_table()
