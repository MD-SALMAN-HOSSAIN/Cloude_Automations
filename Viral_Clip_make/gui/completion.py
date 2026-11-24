"""A single celebration animation played after a clipping task completes.

Everything is drawn with tkinter Canvas primitives, so it travels with the
packaged desktop app and needs no external image or video assets.
"""
from __future__ import annotations

import math
import random
import tkinter as tk

PANEL = "#080d1c"
CYAN = "#33e6ff"
PINK = "#ff3d6e"
PURPLE = "#7c5cff"
GREEN = "#3ddc97"
TEXT = "#eef0f6"
MUTED = "#8895b3"


def _mix(a: str, b: str, t: float) -> str:
    """Blend two ``#rrggbb`` colours (t=0 -> a, t=1 -> b)."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#{:02x}{:02x}{:02x}".format(
        int(ar + (br - ar) * t), int(ag + (bg - ag) * t), int(ab + (bb - ab) * t)
    )


def _partial_polyline(canvas, points, progress: float, **kwargs):
    """Reveal a polyline left-to-right (used to draw the check mark)."""
    progress = max(0.0, min(1.0, progress))
    total = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    target = total * progress
    drawn = 0.0
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        length = math.dist((x1, y1), (x2, y2))
        if drawn >= target:
            break
        if drawn + length <= target:
            canvas.create_line(x1, y1, x2, y2, **kwargs)
            drawn += length
        else:
            part = (target - drawn) / length
            canvas.create_line(x1, y1, x1 + (x2 - x1) * part,
                               y1 + (y2 - y1) * part, **kwargs)
            break


class CompletionAnimation:
    """A short 'clips ready' celebration shown once the render finishes."""

    def __init__(self, master: tk.Misc, count: int, output_dir: str,
                 duration_ms: int = 2600, callback=None):
        self.master = master
        self.count = max(0, int(count or 0))
        self.output_dir = str(output_dir or "")
        self.total_steps = max(20, int(duration_ms / 30))
        self.step = 0
        self._closed = False
        self._callback = callback

        self.w = max(640, master.winfo_width())
        self.h = max(480, master.winfo_height())
        px = master.winfo_rootx()
        py = master.winfo_rooty()
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#000000")
        self.win.geometry(f"{self.w}x{self.h}+{px}+{py}")
        self.canvas = tk.Canvas(self.win, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-alpha", 0.0)
        try:
            self.win.grab_set()
        except tk.TclError:
            pass
        self.win.bind("<Button-1>", lambda _event: self.close())
        self.win.bind("<Escape>", lambda _event: self.close())

        self._particles = [
            {"angle": i * (2 * math.pi / 34) + random.uniform(-0.08, 0.08),
             "speed": random.uniform(115, 250), "size": random.choice((2, 2, 3))}
            for i in range(34)
        ]
        self._tick()

    def close(self):
        """Dismiss the overlay (also called automatically when it finishes)."""
        if self._closed:
            return
        self._closed = True
        callback = self._callback
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        if callback:
            callback()

    def _tick(self):
        if self._closed:
            return
        try:
            self._draw()
            self.step += 1
            if self.step >= self.total_steps:
                self.close()
            else:
                self.win.after(30, self._tick)
        except tk.TclError:
            pass

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w, h = self.w, self.h
        cx, cy = w // 2, h // 2 - 26
        t = self.step / self.total_steps
        c.create_rectangle(0, 0, w, h, fill=PANEL, outline="")

        # Grid and expanding shockwave rings.
        for gx in range(0, w, 56):
            c.create_line(gx, 0, gx, h, fill="#0a1122")
        for gy in range(0, h, 56):
            c.create_line(0, gy, w, gy, fill="#0a1122")
        for i in range(4):
            r = 46 + ((self.step * 4.4 + i * 70) % 430)
            color = (CYAN, _mix(CYAN, PURPLE, 0.5), PINK, PURPLE)[i]
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=2)

        # Radial light spokes.
        for i in range(24):
            a = math.radians(self.step * 1.6 + i * 15)
            r1 = 62 + min(60, self.step * 1.4)
            r2 = r1 + 44 + (self.step % 7) * 3
            c.create_line(cx + math.cos(a) * r1, cy + math.sin(a) * r1,
                          cx + math.cos(a) * r2, cy + math.sin(a) * r2,
                          fill=_mix(PINK, CYAN, (i % 4) / 4), width=2)

        # Confetti burst.
        for dot in self._particles:
            radius = 38 + min(220, t * dot["speed"])
            x = cx + math.cos(dot["angle"]) * radius
            y = cy + math.sin(dot["angle"]) * radius
            c.create_oval(x - dot["size"], y - dot["size"],
                          x + dot["size"], y + dot["size"],
                          fill=_mix(CYAN, PINK, t), outline="")

        # Green check badge, drawn in.
        badge = 42 + max(0, 12 - self.step) * 2
        c.create_oval(cx - badge, cy - badge, cx + badge, cy + badge,
                      fill="#0b1b22", outline=GREEN, width=3)
        _partial_polyline(c, [(cx - 18, cy + 2), (cx - 5, cy + 16), (cx + 22, cy - 16)],
                          min(1.0, self.step / 15), fill=GREEN, width=6)

        # Glitch headline.
        jitter = random.randint(-2, 2) if self.step < 20 else 0
        c.create_text(cx + 2 + jitter, cy + 82, text="CLIPS READY", anchor="center",
                      fill=PINK, font=("Segoe UI", 38, "bold"))
        c.create_text(cx - 2 - jitter, cy + 82, text="CLIPS READY", anchor="center",
                      fill=CYAN, font=("Segoe UI", 38, "bold"))
        c.create_text(cx, cy + 82, text="CLIPS READY", anchor="center",
                      fill=TEXT, font=("Segoe UI", 38, "bold"))
        noun = "SHORT" if self.count == 1 else "SHORTS"
        c.create_text(cx, cy + 122, text=f"{self.count} {noun} GENERATED",
                      anchor="center", fill=GREEN, font=("Consolas", 16, "bold"))

        path = self.output_dir
        if len(path) > 72:
            path = "…" + path[-71:]
        c.create_text(cx, cy + 152, text=path, anchor="center", fill=MUTED,
                      font=("Consolas", 11))
        c.create_text(cx, h - 46, text="Click anywhere to continue", anchor="center",
                      fill=MUTED, font=("Consolas", 10))

        # Progress sweep under the message.
        bar_w = 420
        x0, y = cx - bar_w // 2, h - 70
        c.create_rectangle(x0, y, x0 + bar_w, y + 4, fill="#142033", outline="")
        sweep = int(bar_w * min(1.0, t * 1.35))
        for px in range(x0, x0 + sweep, 8):
            c.create_rectangle(px, y, min(px + 8, x0 + sweep), y + 4,
                               fill=_mix(CYAN, PINK, (px - x0) / bar_w), outline="")

        # Fade in / fade out.
        if self.step < 8:
            self.win.attributes("-alpha", self.step / 8)
        elif self.step > self.total_steps - 10:
            self.win.attributes("-alpha",
                                max(0.0, (self.total_steps - self.step) / 10))
