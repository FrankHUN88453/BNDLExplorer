"""Texture thumbnails for the large-icons view: decoded on a worker thread (a small mip level), uploaded to
OpenGL on the GUI thread, kept in a small LRU cache."""
import collections
import queue
import threading

import numpy as np
from imgui_bundle import imgui, immvision

from . import raster

SIZE = 96
LIMIT = 800


class Thumbs:
    def __init__(self):
        self.tex = collections.OrderedDict()     # key -> GlTexture | None (failed)
        self.pending = set()
        self.jobs = queue.Queue()
        self.done = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        while True:
            key, res, platform = self.jobs.get()
            img = None
            try:
                inf = raster.info(res, platform)
                mip = 0
                while mip + 1 < inf.mips and max(inf.w >> mip, inf.h >> mip) > SIZE * 2:
                    mip += 1
                img = raster.decode(res, platform, mip)
                h, w = img.shape[:2]
                step = max(1, max(h, w) // SIZE)
                img = np.ascontiguousarray(img[::step, ::step])
            except Exception:
                img = None
            self.done.put((key, img))

    def pump(self, budget=24):
        """Upload finished thumbnails (GUI thread)."""
        for _ in range(budget):
            try:
                key, img = self.done.get_nowait()
            except queue.Empty:
                break
            self.pending.discard(key)
            self.tex[key] = immvision.GlTexture(img) if img is not None else None
            while len(self.tex) > LIMIT:
                self.tex.popitem(last=False)

    def get(self, key, res, platform):
        """GlTexture or None (not ready / failed); queues the decode on first request."""
        if key in self.tex:
            self.tex.move_to_end(key)
            return self.tex[key]
        if key not in self.pending:
            self.pending.add(key)
            self.jobs.put((key, res, platform))
        return None

    def draw(self, gl, box):
        """Draw a texture centred in a box x box square at the cursor."""
        w, h = gl.image_size
        s = min(box / max(w, 1), box / max(h, 1))
        dw, dh = max(1, int(w * s)), max(1, int(h * s))
        p = imgui.get_cursor_pos()
        imgui.set_cursor_pos(imgui.ImVec2(p.x + (box - dw) / 2, p.y + (box - dh) / 2))
        imgui.image(imgui.ImTextureRef(gl.texture_id), imgui.ImVec2(dw, dh))
