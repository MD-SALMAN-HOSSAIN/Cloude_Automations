"""Neon "sting" motion graphics for Viral Clip Make.

Inspired by the Cylance Sting motion study: rotating neon arcs, scan lines,
glitch type and particle bursts.  Everything is drawn on a tkinter Canvas, so
the animations travel with the packaged desktop app and need no extra assets.
"""
from __future__ import annotations

import math
import random
import tkinter as tk

BG = "#050813"
PANEL = "#080d1c"
CYAN = "#33e6ff"
PINK = "#ff3d6e"
PURPLE = "#7c5cff"
GREEN = "#3ddc97"
TEXT = "#eef0f6"
MUTED = "#8895b3"


def _mix(a: str, b: str, t: float) -> str:
    """Mix two #rrggbb colours (t=0 -> a, t=1 -> b)."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#{:02x}{:02x}{:02x}".format(
        int(ar + (br - ar) * t), int(ag + (bg - ag) * t), int(ab + (bb - ab) * t)
    )


def _chamfer_rect(w: int, h: int, cut: int = 18):
    return [cut, 0, w - cut, 0, w, cut, w, h - cut,
            w - cut, h, cut, h, 0, h - cut, 0, cut]


def _partial_polyline(canvas, points, progress: float, **kwargs):
    """Draw a polyline revealed left-to-right (used for the check mark)."""
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


class StingSplash:
    """Animated boot screen shown while the main window is being built."""

    def __init__(self, master: tk.Misc, min_duration_ms: int = 1550):
        self.master = master
        self.min_steps = max(10, int(min_duration_ms / 30))
        self.step = 0
        self.status = "INITIALIZING LOCAL ENGINE"
        self._finishing = False
        self._finish_at = 10 ** 9
        self._callback = None
        self._closed = False

        self.w, self.h = 720, 400
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", "#000000")
        except tk.TclError:
            pass
        self.win.configure(bg="#000000")

        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{self.w}x{self.h}+{(sw - self.w) // 2}+{(sh - self.h) // 2 - 24}")
        self.canvas = tk.Canvas(self.win, width=self.w, height=self.h,
                                bg="#000000", highlightthickness=0)
        self.canvas.pack()
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-alpha", 0.0)

        self._particles = [
            {"x": random.uniform(0, self.w), "y": random.uniform(28, self.h - 58),
             "speed": random.uniform(0.7, 2.6), "size": random.choice((1, 1, 2)),
             "phase": random.uniform(0, 6.28)}
            for _ in range(46)
        ]
        self._tick()

    def set_status(self, text: str):
        self.status = str(text)
        self._draw()

    def finish(self, callback=None, min_delay_ms: int = 420):
        """Keep the sting on screen for its minimum time, then fade to the app."""
        if self._finishing or self._closed:
            return
        self._finishing = True
        self._callback = callback
        remaining = max(0, self.min_steps - self.step)
        self._finish_at = self.step + max(4, remaining, int(min_delay_ms / 30))
        self.status = "READY TO GO VIRAL"
        self._draw()

    def _tick(self):
        if self._closed:
            return
        try:
            self._draw()
            self.step += 1
            if self._finishing and self.step >= self._finish_at:
                self._fade_out()
            else:
                self.win.after(30, self._tick)
        except tk.TclError:
            pass

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w, h = self.w, self.h
        p = min(1.0, self.step / self.min_steps)
        cx, cy = w // 2, 158

        c.create_polygon(_chamfer_rect(w, h), fill=PANEL, outline="#1b2740", width=1)
        # Blueprint grid.
        for gx in range(24, w, 40):
            c.create_line(gx, 8, gx, h - 8, fill="#0c1529")
        for gy in range(16, h, 40):
            c.create_line(8, gy, w - 8, gy, fill="#0c1529")

        # Corner brackets.
        for x1, y1, x2, y2, color in (
            (14, 18, 60, 18, CYAN), (14, 18, 14, 54, CYAN),
            (w - 14, h - 18, w - 60, h - 18, PINK),
            (w - 14, h - 18, w - 14, h - 54, PINK),
        ):
            c.create_line(x1, y1, x2, y2, fill=color, width=2)

        # Moving data particles.
        for dot in self._particles:
            dot["x"] -= dot["speed"]
            if dot["x"] < 6:
                dot["x"] = w - 6
                dot["y"] = random.uniform(28, h - 58)
            y = dot["y"] + math.sin(self.step * 0.08 + dot["phase"]) * 3
            c.create_rectangle(dot["x"], y, dot["x"] + dot["size"],
                               y + dot["size"], fill=_mix(CYAN, MUTED, 0.35), outline="")

        # Rotating neon arcs.
        colors = (CYAN, _mix(CYAN, PURPLE, 0.5), PINK, PURPLE)
        for i in range(4):
            r = 28 + ((self.step * 1.7 + i * 36) % 142)
            c.create_arc(cx - r, cy - r, cx + r, cy + r,
                         start=self.step * 2.2 + i * 47, extent=74,
                         style="arc", outline=colors[i], width=2)

        # Pulsing hexagonal core.
        angle = math.radians(self.step * 2.6)
        core = []
        for k in range(6):
            radius = 36 if k % 2 == 0 else 15
            core.append((cx + math.cos(angle + k * math.pi / 3) * radius,
                         cy + math.sin(angle + k * math.pi / 3) * radius))
        c.create_polygon(core, outline=CYAN, width=2, fill="")
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=PINK, outline="")
        c.create_line(cx - 116, cy, cx - 64, cy, fill="#263a5c")
        c.create_line(cx + 64, cy, cx + 116, cy, fill="#263a5c")

        # Scan line.
        scan_y = 34 + ((self.step * 7) % (h - 78))
        c.create_line(18, scan_y, w - 18, scan_y, fill=_mix(CYAN, PINK, 0.35), width=1)
        c.create_rectangle(18, scan_y - 2, w - 18, scan_y + 2,
                           fill=_mix(CYAN, PINK, 0.35), stipple="gray25", outline="")

        # Glitch wordmark.
        jitter = random.randint(-2, 2) if self.step < self.min_steps else 0
        title = "VIRAL CLIP MAKE"
        c.create_text(cx + 2 + jitter, 238, text=title, anchor="center",
                      fill=PINK, font=("Segoe UI", 27, "bold"))
        c.create_text(cx - 2 - jitter, 238, text=title, anchor="center",
                      fill=CYAN, font=("Segoe UI", 27, "bold"))
        c.create_text(cx, 238, text=title, anchor="center",
                      fill=TEXT, font=("Segoe UI", 27, "bold"))
        c.create_text(cx, 267, text="LOCAL SHORTS ENGINE  //  VIRAL STING BOOT",
                      anchor="center", fill=MUTED, font=("Consolas", 10))

        # Boot progress.
        x0, x1, y = 92, w - 92, 326
        c.create_rectangle(x0, y, x1, y + 7, fill="#111a2e", outline="")
        filled = int((x1 - x0) * p)
        for px in range(x0, x0 + filled, 9):
            c.create_rectangle(px, y, min(px + 9, x0 + filled), y + 7,
                               fill=_mix(CYAN, PINK, (px - x0) / max(1, x1 - x0)),
                               outline="")
        c.create_text(x0, y + 22, anchor="w", text=self.status,
                      fill=TEXT, font=("Consolas", 10))
        c.create_text(x1, y + 22, anchor="e", text=f"{int(p * 100):3d}%",
                      fill=CYAN, font=("Consolas", 10, "bold"))

        # Brief flash right before the real window takes over.
        if self._finishing and self.step >= self._finish_at - 3:
            c.create_rectangle(0, 0, w, h, fill="#ffffff", stipple="gray25", outline="")

        # Fade the splash in over the first few frames.
        if self.step < 8:
            self.win.attributes("-alpha", self.step / 8)

    def _fade_out(self, alpha: float = 1.0):
        if self._closed:
            return
        try:
            self.win.attributes("-alpha", max(0.0, alpha))
            if alpha > 0.06:
                self.win.after(24, lambda: self._fade_out(alpha - 0.12))
                return
            callback = self._callback
            self._closed = True
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            if callback:
                callback()
        except tk.TclError:
            pass


class StingSuccess:
    """Full-window celebration after a render finishes."""

    def __init__(self, master: tk.Misc, count: int, output_dir: str,
                 duration_ms: int = 3000, callback=None):
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
        try:
            self.win.attributes("-transparentcolor", "#000000")
        except tk.TclError:
            pass
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

    def close(self, callback=None):
        if self._closed:
            return
        self._callback = callback or self._callback
        self._closed = True
        self._fade_out()

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

        # Particle burst.
        for dot in self._particles:
            radius = 38 + min(220, t * dot["speed"])
            x = cx + math.cos(dot["angle"]) * radius
            y = cy + math.sin(dot["angle"]) * radius
            c.create_oval(x - dot["size"], y - dot["size"],
                          x + dot["size"], y + dot["size"],
                          fill=_mix(CYAN, PINK, t), outline="")

        # Check badge.
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
                      anchor="center", fill=GREEN,
                      font=("Consolas", 16, "bold"))

        path = self.output_dir
        if len(path) > 72:
            path = "…" + path[-71:]
        c.create_text(cx, cy + 152, text=path, anchor="center", fill=MUTED,
                      font=("Consolas", 11))
        c.create_text(cx, h - 46, text="Click anywhere to continue", anchor="center",
                      fill=MUTED, font=("Consolas", 10))

        # Sweep line under the message.
        bar_w = 420
        x0, x1, y = cx - bar_w // 2, cx + bar_w // 2, h - 70
        c.create_rectangle(x0, y, x1, y + 4, fill="#142033", outline="")
        sweep = int(bar_w * min(1.0, t * 1.35))
        for px in range(x0, x0 + sweep, 8):
            c.create_rectangle(px, y, min(px + 8, x0 + sweep), y + 4,
                               fill=_mix(CYAN, PINK, (px - x0) / bar_w), outline="")

        if self.step < 8:
            self.win.attributes("-alpha", self.step / 8)
        elif self.step > self.total_steps - 10:
            self.win.attributes("-alpha", max(0.0, (self.total_steps - self.step) / 10))

    def _fade_out(self):
        try:
            callback = self._callback
            try:
                self.win.grab_release()
            except tk.TclError:
                pass
            self.win.destroy()
            if callback:
                callback()
        except tk.TclError:
            pass


class StingToast:
    """Small confirmation popup used when a preset is applied."""

    def __init__(self, master: tk.Misc, text: str, duration_ms: int = 2400):
        self.master = master
        self.text = str(text)
        self.total_steps = max(10, int(duration_ms / 30))
        self.step = 0
        self.w, self.h = 470, 68
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", "#000000")
        except tk.TclError:
            pass
        self.win.configure(bg="#000000")
        try:
            x = master.winfo_rootx() + max(0, (master.winfo_width() - self.w) // 2)
            y = master.winfo_rooty() + max(0, master.winfo_height() - 130)
        except tk.TclError:
            x, y = 100, 100
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self.canvas = tk.Canvas(self.win, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.deiconify()
        self.win.lift()
        self._tick()

    def _tick(self):
        try:
            self._draw()
            self.step += 1
            if self.step >= self.total_steps:
                self.win.destroy()
            else:
                self.win.after(30, self._tick)
        except tk.TclError:
            pass

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w, h = self.w, self.h
        c.create_polygon(_chamfer_rect(w, h, 12), fill="#0c1424", outline="#2b3242")
        c.create_rectangle(0, 0, 5, h, fill=PINK, outline="")
        c.create_text(20, 28, anchor="w", text=self.text,
                      fill=TEXT, font=("Segoe UI", 12, "bold"))
        c.create_text(20, 48, anchor="w", text="SAVED AS YOUR DEFAULT SETTINGS",
                      fill=MUTED, font=("Consolas", 9))
        p = self.step / self.total_steps
        c.create_rectangle(w - 110, 28, w - 20, 34, fill="#1a2438", outline="")
        c.create_rectangle(w - 110, 28, w - 110 + int(90 * p), 34, fill=CYAN, outline="")
