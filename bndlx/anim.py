"""Skeletons (type 0xB2), animations (0xB3) and animation lists (0xB0) of the retail game, and posing a skeleton.

Skeleton (AnimSkel, version 2): u16 version, u16 bone count, u32 bone offset (0x20), u32 bone id offset, u32 IK
offset. A bone is 0x30 bytes: f32x4 position and f32x4 rotation quaternion (x, y, z, w) in model space (the bind
pose), then i32 parent, previous sibling, last child, own index. The ids are u32 name hashes.

Animation (version 2), 0x60-byte header: u32 version 2, u32 keys, u32 bones, u32 rotation tracks, u32 translation
tracks, ..., u32 rotation bytes per key, ..., u32 size (0x2C), f32 keys per second (0x30), u16 rotation codec
(0x34: 0 = f32 quaternions, 1 = 32-bit smallest-three), then nine offsets (0x38): root translation track,
translation tracks, -, per-bone f32 (1.0), per-bone translation track index (0xFF = none), rotation keys,
per-bone u8, per-bone rotation track index, end; u32 own id at 0x5C. Translation keys are f32x4, track after
track; rotation keys are key after key (every track of a key together). Keys are added to the bind pose: the
translation to the bone's local position, the rotation after the bone's local rotation.
Smallest three: bits 30-31 = which component is largest (x, y, z, w), the other three in 10 bits each (bits
20-29, 10-19, 0-9, in x y z w order), each mapped from 0..1023 to -1/sqrt(2)..1/sqrt(2).

AnimationList (AnimationCollection, version 2): u16 version, u16 count, u32 size, u32 animation offsets (0x08),
u32 start / middle / end sound handle arrays (0x0C / 0x10 / 0x14), the animations themselves inside."""
import struct
from dataclasses import dataclass

import numpy as np

T_ANIMLIST, T_SKELETON, T_ANIMATION = 0xB0, 0xB2, 0xB3


class AnimError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------
# quaternions (x, y, z, w), arrays of shape (..., 4)
# ---------------------------------------------------------------------------------------------------------------
def qmul(a, b):
    ax, ay, az, aw = np.moveaxis(a, -1, 0)
    bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz], -1)


def qconj(q):
    return q * np.array([-1.0, -1.0, -1.0, 1.0])


def qrot(q, v):
    """Vectors v turned by the unit quaternions q (broadcasting)."""
    u, w = q[..., :3], q[..., 3:]
    t = 2 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def qmat(q):
    """3x3 matrices (column vectors) of unit quaternions."""
    x, y, z, w = np.moveaxis(q, -1, 0)
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                     np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                     np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], -2)


def unpack_smallest3(words):
    """32-bit smallest-three quaternions -> (N, 4) float (x, y, z, w)."""
    w = np.asarray(words, np.int64)
    big = (w >> 30) & 3
    c = [(((w >> s) & 1023) / 1023.0 * 2 - 1) * 0.7071067811865476 for s in (20, 10, 0)]
    rest = np.sqrt(np.clip(1 - c[0] ** 2 - c[1] ** 2 - c[2] ** 2, 0, 1))
    q = np.zeros((len(w), 4))
    for i in range(4):
        sel = big == i
        others = [k for k in range(4) if k != i]
        for j, k in enumerate(others):
            q[sel, k] = c[j][sel]
        q[sel, i] = rest[sel]
    return q


# ---------------------------------------------------------------------------------------------------------------
# skeleton
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Skeleton:
    parents: np.ndarray        # (n,) int, -1 = root
    pos: np.ndarray            # (n, 3) bind positions in model space
    rot: np.ndarray            # (n, 4) bind rotations in model space
    ids: list                  # u32 name hashes

    @property
    def count(self):
        return len(self.parents)

    def local_bind(self):
        """(local translations, local rotations) of the bind pose, relative to each bone's parent."""
        lt = self.pos.copy()
        lr = self.rot.copy()
        for i, p in enumerate(self.parents):
            if p >= 0:
                inv = qconj(self.rot[p])
                lt[i] = qrot(inv, self.pos[i] - self.pos[p])
                lr[i] = qmul(inv, self.rot[i])
        return lt, lr


def read_skeleton(data, e='<'):
    if len(data) < 0x20:
        raise AnimError('skeleton too short')
    ver, n, bo, io = struct.unpack_from(e + 'HHII', data, 0)
    if ver != 2 or bo + 0x30 * n > len(data):
        raise AnimError(f'skeleton version {ver} is not supported')
    rows = [struct.unpack_from(e + '4f4f4i', data, bo + 0x30 * i) for i in range(n)]
    pos = np.array([r[:3] for r in rows], np.float64).reshape(-1, 3)
    rot = np.array([r[4:8] for r in rows], np.float64).reshape(-1, 4)
    parents = np.array([r[8] for r in rows], np.int64)
    ids = list(struct.unpack_from(e + f'{n}I', data, io)) if io + 4 * n <= len(data) else [0] * n
    return Skeleton(parents, pos, rot, ids)


# ---------------------------------------------------------------------------------------------------------------
# animation
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Animation:
    keys: int
    bones: int
    rate: float                # keys per second
    codec: int
    root: np.ndarray           # (keys, 3) translation of bone 0
    trans: np.ndarray          # (tracks, keys, 3)
    trans_index: np.ndarray    # (bones,) track or 255
    rots: np.ndarray           # (keys, tracks, 4)
    rot_index: np.ndarray      # (bones,) track or 255
    offset: int = 0            # where it starts in its resource

    @property
    def duration(self):
        return (self.keys - 1) / self.rate if self.rate > 0 and self.keys > 1 else 0.0

    @property
    def animated(self):
        """Bones with a translation or rotation track."""
        return int(((self.trans_index != 255) | (self.rot_index != 255)).sum())

    def sample(self, t):
        """(translation deltas (bones, 3), rotation deltas (bones, 4)) at time t (seconds), keys blended."""
        f = min(max(t * self.rate, 0.0), float(self.keys - 1)) if self.keys > 1 else 0.0
        k0 = int(f)
        k1 = min(k0 + 1, self.keys - 1)
        a = f - k0
        dt = np.zeros((self.bones, 3))
        dr = np.tile([0.0, 0.0, 0.0, 1.0], (self.bones, 1))
        if self.bones:
            dt[0] = self.root[k0] * (1 - a) + self.root[k1] * a
        has = self.trans_index != 255
        if has.any():
            ti = self.trans_index[has]
            dt[has] += self.trans[ti, k0] * (1 - a) + self.trans[ti, k1] * a
        has = self.rot_index != 255
        if has.any():
            ri = self.rot_index[has]
            q0, q1 = self.rots[k0, ri], self.rots[k1, ri]
            q1 = np.where((np.sum(q0 * q1, 1) < 0)[:, None], -q1, q1)
            q = q0 * (1 - a) + q1 * a
            dr[has] = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
        return dt, dr


def read_animation(data, base=0, e='<'):
    if base + 0x60 > len(data):
        raise AnimError('animation too short')
    h = struct.unpack_from(e + '12I', data, base)
    ver, keys, bones, nrot, ntr = h[:5]
    stride = h[7]
    rate = struct.unpack_from(e + 'f', data, base + 0x30)[0]
    codec = struct.unpack_from(e + 'H', data, base + 0x34)[0]
    o = [base + x for x in struct.unpack_from(e + '9I', data, base + 0x38)]
    if ver != 2 or codec not in (0, 1) or not (o[0] <= o[1] <= o[2] <= o[8] <= len(data)):
        raise AnimError('this animation layout is not supported (the PS3 prototype uses an older one)')
    if stride != nrot * (16 if codec == 0 else 4) or o[1] - o[0] != keys * 16 or o[2] - o[1] != ntr * keys * 16:
        raise AnimError('unexpected animation layout')
    root = np.frombuffer(data, e + 'f4', keys * 4, o[0]).reshape(keys, 4)[:, :3].astype(np.float64)
    trans = np.frombuffer(data, e + 'f4', ntr * keys * 4, o[1]).reshape(keys, ntr, 4)[:, :, :3]
    trans = trans.transpose(1, 0, 2).astype(np.float64)                  # stored key after key -> (tracks, keys, 3)
    ti = np.frombuffer(data, np.uint8, bones, o[4]).astype(np.int64)
    ri = np.frombuffer(data, np.uint8, bones, o[7]).astype(np.int64)
    if codec == 0:
        rots = np.frombuffer(data, e + 'f4', keys * nrot * 4, o[5]).reshape(keys, nrot, 4).astype(np.float64)
    else:
        rots = unpack_smallest3(np.frombuffer(data, e + 'u4', keys * nrot, o[5])).reshape(keys, nrot, 4)
    if (ti[ti != 255] >= max(ntr, 1)).any() or (ri[ri != 255] >= max(nrot, 1)).any():
        raise AnimError('track index out of range')
    return Animation(keys, bones, float(rate), codec, root, trans, ti, rots, ri, base)


def read_animation_list(data, e='<'):
    """[Animation] of an AnimationList (the animations are stored inside it)."""
    ver, n = struct.unpack_from(e + 'HH', data, 0)
    table = struct.unpack_from(e + 'I', data, 8)[0]
    if ver != 2 or table + 4 * n > len(data):
        raise AnimError(f'animation list version {ver} is not supported')
    return [read_animation(data, struct.unpack_from(e + 'I', data, table + 4 * k)[0], e) for k in range(n)]


def animations_of(res, e):
    """The animations of an Animation or AnimationList resource."""
    if res.type == T_ANIMLIST:
        return read_animation_list(res.data(0), e)
    return [read_animation(res.data(0), 0, e)]


# ---------------------------------------------------------------------------------------------------------------
# posing and skinning
# ---------------------------------------------------------------------------------------------------------------
def pose(skel, anim=None, t=0.0):
    """Model-space (positions (n, 3), rotations (n, 4)) of every bone at time t (the bind pose without anim)."""
    lt, lr = skel.local_bind()
    if anim is not None and anim.bones == skel.count:
        dt, dr = anim.sample(t)
        lt = lt + dt
        lr = qmul(lr, dr)
    n = skel.count
    wt = np.zeros((n, 3))
    wr = np.tile([0.0, 0.0, 0.0, 1.0], (n, 1))
    for i in range(n):                         # parents come before their children
        p = skel.parents[i]
        if p < 0:
            wt[i], wr[i] = lt[i], lr[i]
        else:
            wt[i] = wt[p] + qrot(wr[p], lt[i])
            wr[i] = qmul(wr[p], lr[i])
    return wt, wr


def skin(pos, nrm, joints, weights, skel, wt, wr):
    """Vertices moved from the bind pose to the posed bones (linear blend skinning). joints (N, 4) bone indices,
    weights (N, 4) summing to 1."""
    delta = qmul(wr, qconj(skel.rot))                  # bind -> posed rotation of each bone
    m = qmat(delta)                                    # (n, 3, 3)
    tr = wt - np.einsum('nij,nj->ni', m, skel.pos)     # posed = m @ bind + tr
    j = np.clip(joints, 0, skel.count - 1)
    w = weights[..., None]
    out_p = np.zeros_like(pos, dtype=np.float64)
    out_n = np.zeros_like(pos, dtype=np.float64)
    for k in range(joints.shape[1]):
        mk = m[j[:, k]]
        out_p += w[:, k] * (np.einsum('nij,nj->ni', mk, pos) + tr[j[:, k]])
        if nrm is not None:
            out_n += w[:, k] * np.einsum('nij,nj->ni', mk, nrm)
    if nrm is not None:
        out_n /= np.maximum(np.linalg.norm(out_n, axis=1, keepdims=True), 1e-12)
    return out_p, (out_n if nrm is not None else None)
