from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.config import Config
from core.pipeline import run_pipeline
from core.util import Logger

BG = "#0f1117"
PANEL = "#1a1e29"
TEXT = "#e8eaf0"
MUTED = "#9aa3b8"
ACCENT = "#ff3d6e"
ACCENT2 = "#7c5cff"
GREEN = "#3ddc97"
YELLOW = "#ffd166"
RED = "#ff6b6b"

PROVIDERS = {
    "Local (free - no key)": "local",
    "Gemini API": "gemini",
    "OpenAI-compatible (Ollama / LM Studio / OpenRouter)": "openai",
}
PROVIDER_KEYS = {v: k for k, v in PROVIDERS.items()}

ASPECTS = {
    "9:16 (Shorts / TikTok / Reels)": "9:16",
    "1:1 (Square)": "1:1",
    "4:5 (Portrait)": "4:5",
    "3:4 (Portrait)": "3:4",
    "16:9 (Landscape)": "16:9",
    "Custom size": "custom",
}
ASPECT_KEYS = {v: k for k, v in ASPECTS.items()}
ASPECT_SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
                "3:4": (1080, 1440), "16:9": (1920, 1080)}

MODES = {
    "Fast (turbo)": {
        "whisper": "tiny", "crf": 26, "preset": "veryfast", "face": False,
        "normalize": False, "face_interval": 1.0, "candidates": 300,
        "desc": "Fastest. Smaller model, simpler crop, quicker render - lower quality.",
    },
    "Balanced": {
        "whisper": "base", "crf": 20, "preset": "medium", "face": True,
        "normalize": True, "face_interval": 0.5, "candidates": 600,
        "desc": "Recommended. Good speed with tracked framing and clean audio.",
    },
    "Best quality": {
        "whisper": "small", "crf": 16, "preset": "slow", "face": True,
        "normalize": True, "face_interval": 0.35, "candidates": 900,
        "desc": "Slowest. More accurate words and the cleanest final picture.",
    },
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = Config()
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.result = None
        self._build_style()
        self._build_ui()
        self._load_advanced()

    # ------------------------------------------------------------------
    def _build_style(self):
        self.root.title("Viral Clip Make  —  100% Free Viral Shorts Maker")
        self.root.geometry("990x880")
        self.root.minsize(900, 730)
        self.root.configure(bg=BG)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("OnBg.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Head.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 18, "bold"))
        style.configure("PanelTitle.TLabel", background=PANEL, foreground=ACCENT2,
                        font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TEntry", fieldbackground="#11131a", foreground=TEXT,
                        insertcolor=TEXT, bordercolor="#33394a")
        style.configure("TCombobox", fieldbackground="#11131a", foreground=TEXT,
                        background="#11131a", arrowcolor=TEXT)
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TSpinbox", fieldbackground="#11131a", foreground=TEXT, arrowcolor=TEXT)
        style.configure("TProgressbar", background=ACCENT, troughcolor="#11131a",
                        bordercolor="#11131a", lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("Go.TButton", background=ACCENT, foreground="#ffffff",
                        font=("Segoe UI", 12, "bold"), borderwidth=0, padding=10)
        style.map("Go.TButton", background=[("active", "#ff547d"), ("disabled", "#5a2b3a")])
        style.configure("Small.TButton", font=("Segoe UI", 9), padding=5)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#191d27", foreground=MUTED,
                        padding=(14, 7), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", PANEL)],
                  foreground=[("selected", TEXT)])

    # ------------------------------------------------------------------
    def _build_ui(self):
        main = ttk.Frame(self.root, padding=(18, 12))
        main.pack(fill="both", expand=True)

        header = tk.Canvas(main, height=78, bg=BG, highlightthickness=0)
        header.pack(fill="x")
        header.create_text(4, 26, anchor="w", text="VIRAL CLIP MAKE",
                           fill=TEXT, font=("Segoe UI", 25, "bold"))
        header.create_text(6, 56, anchor="w",
                           text="Turn any long video into viral shorts  ·  free local mode  ·  optional AI  ·  any aspect ratio",
                           fill=MUTED, font=("Segoe UI", 10))
        header.create_rectangle(0, 74, 990, 78, fill=ACCENT, outline="")

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True, pady=(10, 0))
        tab_make = ttk.Frame(self.notebook)
        tab_adv = ttk.Frame(self.notebook)
        self.notebook.add(tab_make, text="  Make Clips  ")
        self.notebook.add(tab_adv, text="  AI & Output  ")

        self._build_make_tab(tab_make)
        self._build_advanced_tab(tab_adv)

    # ------------------------------------------------------------------
    def _build_make_tab(self, tab):
        src = ttk.Frame(tab, style="Panel.TFrame", padding=13)
        src.pack(fill="x", pady=(8, 6))
        ttk.Label(src, text="VIDEO SOURCE", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.source_var = tk.StringVar()
        ttk.Entry(src, textvariable=self.source_var, font=("Segoe UI", 11)).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(7, 3))
        ttk.Label(src, text="Paste a YouTube / online link, or choose a video file on your PC.",
                  style="Hint.TLabel").grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Button(src, text="Browse file", style="Small.TButton",
                   command=self.browse_file).grid(row=1, column=3, padx=(8, 4))
        ttk.Button(src, text="Paste link", style="Small.TButton",
                   command=self.paste_link).grid(row=1, column=4)
        src.columnconfigure(0, weight=1)

        settings = ttk.Frame(tab, style="Panel.TFrame", padding=13)
        settings.pack(fill="x", pady=6)
        ttk.Label(settings, text="QUICK SETTINGS", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")

        def lab(row, col, text):
            ttk.Label(settings, text=text).grid(row=row, column=col, sticky="w", pady=(9, 2))

        self.clips_var = tk.IntVar(value=int(self.cfg.get("general.clips_per_video", 5)))
        lab(1, 0, "Clips per video")
        ttk.Spinbox(settings, from_=1, to=15, width=6, textvariable=self.clips_var).grid(row=2, column=0, sticky="w")

        self.min_var = tk.IntVar(value=int(self.cfg.get("general.min_seconds", 12)))
        lab(1, 1, "Min seconds")
        ttk.Spinbox(settings, from_=8, to=30, width=6, textvariable=self.min_var).grid(row=2, column=1, sticky="w")

        self.max_var = tk.IntVar(value=int(self.cfg.get("general.max_seconds", 45)))
        lab(1, 2, "Max seconds")
        ttk.Spinbox(settings, from_=15, to=60, width=6, textvariable=self.max_var).grid(row=2, column=2, sticky="w")

        self.whisper_var = tk.StringVar(value=str(self.cfg.get("whisper.model", "base")))
        lab(1, 3, "Speech model")
        ttk.Combobox(settings, textvariable=self.whisper_var, width=10, state="readonly",
                     values=["tiny", "base", "small", "medium"]).grid(row=2, column=3, sticky="w")

        self.lang_var = tk.StringVar(value=str(self.cfg.get("general.language", "")))
        lab(1, 4, "Language")
        ttk.Entry(settings, textvariable=self.lang_var, width=10).grid(row=2, column=4, sticky="w")

        self.out_var = tk.StringVar(value=str(self.cfg.get("general.output_dir")))
        lab(3, 0, "Output folder")
        ttk.Entry(settings, textvariable=self.out_var).grid(row=4, column=0, columnspan=4, sticky="ew")
        ttk.Button(settings, text="Change", style="Small.TButton",
                   command=self.browse_output).grid(row=4, column=4, padx=(8, 0))

        flags = ttk.Frame(settings, style="Panel.TFrame")
        flags.grid(row=5, column=0, columnspan=5, sticky="w", pady=(11, 0))
        self.face_var = tk.BooleanVar(value=bool(self.cfg.get("features.face_track", True)))
        self.cap_var = tk.BooleanVar(value=bool(self.cfg.get("features.captions", True)))
        self.hook_var = tk.BooleanVar(value=bool(self.cfg.get("features.hook", True)))
        self.norm_var = tk.BooleanVar(value=bool(self.cfg.get("features.normalize_audio", True)))
        ttk.Checkbutton(flags, text="Face tracking", variable=self.face_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(flags, text="Captions", variable=self.cap_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(flags, text="Hook overlay", variable=self.hook_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(flags, text="Normalize audio", variable=self.norm_var).pack(side="left")
        settings.columnconfigure(0, weight=1)

        action = ttk.Frame(tab, padding=(0, 4))
        action.pack(fill="x")
        self.go_btn = ttk.Button(action, text="MAKE VIRAL CLIPS", style="Go.TButton", command=self.start)
        self.go_btn.pack(fill="x", ipady=5)

        prog_frame = ttk.Frame(tab)
        prog_frame.pack(fill="x", pady=(7, 2))
        self.progress = ttk.Progressbar(prog_frame, mode="determinate", maximum=100)
        self.progress.pack(fill="x", side="left", expand=True)
        self.status = ttk.Label(prog_frame, text="Ready", width=26, anchor="e",
                                background=BG, foreground=MUTED, font=("Segoe UI", 9))
        self.status.pack(side="right", padx=(8, 0))

        log_frame = ttk.Frame(tab, style="Panel.TFrame", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(6, 0))
        ttk.Label(log_frame, text="ACTIVITY", style="PanelTitle.TLabel").pack(anchor="w")
        self.logbox = tk.Text(log_frame, height=9, bg="#0b0d13", fg=TEXT,
                              font=("Consolas", 9), wrap="word", relief="flat",
                              insertbackground=TEXT, padx=8, pady=6)
        self.logbox.pack(fill="both", expand=True, pady=(6, 0))
        for tag, color in (("INFO", MUTED), ("OK", GREEN), ("WARN", YELLOW),
                           ("ERROR", RED), ("HEAD", ACCENT2)):
            self.logbox.tag_config(tag, foreground=color)

        bottom = ttk.Frame(tab)
        bottom.pack(fill="x", pady=(7, 0))
        self.open_btn = ttk.Button(bottom, text="Open output folder", state="disabled",
                                   command=self.open_output)
        self.open_btn.pack(side="left")
        ttk.Label(bottom, text="Free local mode by default  ·  no account  ·  no watermark",
                  background=BG, foreground=MUTED, font=("Segoe UI", 9)).pack(side="right")

    # ------------------------------------------------------------------
    def _build_advanced_tab(self, tab):
        ai = ttk.Frame(tab, style="Panel.TFrame", padding=13)
        ai.pack(fill="x", pady=(8, 6))
        ttk.Label(ai, text="OPTIONAL AI / API  (skip this for 100% free local mode)",
                  style="PanelTitle.TLabel").pack(anchor="w")

        self.provider_var = tk.StringVar(value=PROVIDER_KEYS.get(
            str(self.cfg.get("ai.provider", "local")), "Local (free - no key)"))
        row = ttk.Frame(ai, style="Panel.TFrame"); row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Provider", width=12).pack(side="left")
        ttk.Combobox(row, textvariable=self.provider_var, state="readonly", width=48,
                     values=list(PROVIDERS)).pack(side="left")
        self.provider_var.trace_add("write", self._on_provider)

        self.key_var = tk.StringVar(value=str(self.cfg.get("ai.api_key", "") or ""))
        row = ttk.Frame(ai, style="Panel.TFrame"); row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="API key", width=12).pack(side="left")
        self.key_entry = ttk.Entry(row, textvariable=self.key_var, show="•", width=52)
        self.key_entry.pack(side="left")
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Show", variable=self.show_key,
                        command=self._toggle_key).pack(side="left", padx=(6, 0))

        self.base_var = tk.StringVar(value=str(self.cfg.get("ai.base_url", "")))
        row = ttk.Frame(ai, style="Panel.TFrame"); row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Base URL", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.base_var, width=52).pack(side="left")

        self.ai_model_var = tk.StringVar(value=str(self.cfg.get("ai.model", "")))
        row = ttk.Frame(ai, style="Panel.TFrame"); row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Model", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.ai_model_var, width=52).pack(side="left")

        ttk.Label(ai, text=(
            "Examples: Gemini -> gemini-2.5-flash  |  Ollama -> llama3.1, URL http://localhost:11434/v1  "
            "|  OpenRouter -> openai/gpt-4o-mini, URL https://openrouter.ai/api/v1\n"
            "Keys are stored only in this PC's settings file. If the API fails, the app automatically "
            "falls back to the free local moment picker."
        ), style="Hint.TLabel", justify="left", wraplength=880).pack(anchor="w", pady=(8, 0))

        out = ttk.Frame(tab, style="Panel.TFrame", padding=13)
        out.pack(fill="x", pady=6)
        ttk.Label(out, text="OUTPUT SIZE", style="PanelTitle.TLabel").pack(anchor="w")
        row = ttk.Frame(out, style="Panel.TFrame"); row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Aspect ratio", width=12).pack(side="left")
        self.aspect_var = tk.StringVar(value=ASPECT_KEYS.get(
            str(self.cfg.get("video.aspect", "9:16")), "9:16 (Shorts / TikTok / Reels)"))
        ttk.Combobox(row, textvariable=self.aspect_var, state="readonly", width=30,
                     values=list(ASPECTS)).pack(side="left")
        self.aspect_var.trace_add("write", self._on_aspect)

        self.cw_var = tk.IntVar(value=int(self.cfg.get("video.custom_width", 1080)))
        self.ch_var = tk.IntVar(value=int(self.cfg.get("video.custom_height", 1920)))
        row = ttk.Frame(out, style="Panel.TFrame"); row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Custom size", width=12).pack(side="left")
        self.cw_spin = ttk.Spinbox(row, from_=320, to=3840, width=8, textvariable=self.cw_var)
        self.cw_spin.pack(side="left")
        ttk.Label(row, text="x").pack(side="left", padx=5)
        self.ch_spin = ttk.Spinbox(row, from_=320, to=3840, width=8, textvariable=self.ch_var)
        self.ch_spin.pack(side="left")

        qual = ttk.Frame(tab, style="Panel.TFrame", padding=13)
        qual.pack(fill="x", pady=6)
        ttk.Label(qual, text="SPEED / QUALITY", style="PanelTitle.TLabel").pack(anchor="w")
        row = ttk.Frame(qual, style="Panel.TFrame"); row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Mode", width=12).pack(side="left")
        self.mode_var = tk.StringVar(value=str(self.cfg.get("quality.mode", "Balanced")))
        if self.mode_var.get() not in MODES:
            self.mode_var.set("Balanced")
        ttk.Combobox(row, textvariable=self.mode_var, state="readonly", width=28,
                     values=list(MODES)).pack(side="left")
        self.mode_var.trace_add("write", self._apply_mode)

        self.mode_desc = ttk.Label(qual, text="", style="Hint.TLabel", wraplength=860, justify="left")
        self.mode_desc.pack(anchor="w", pady=(8, 0))

        ttk.Label(tab, text=(
            "Tip: choose Fast (turbo) for quick previews, then Best quality for clips you want to publish. "
            "You can still fine-tune the speech model and checkboxes in the Make Clips tab."
        ), style="OnBg.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(12, 0))

    # ------------------------------------------------------------------
    def _load_advanced(self):
        self._on_provider()
        self._on_aspect()
        self._apply_mode()

    def _on_provider(self, *_):
        provider = PROVIDERS.get(self.provider_var.get(), "local")
        if provider == "gemini" and not self.ai_model_var.get():
            self.ai_model_var.set("gemini-2.5-flash")
        elif provider == "openai":
            if not self.base_var.get() or "localhost" in self.base_var.get():
                self.base_var.set("http://localhost:11434/v1")
            if not self.ai_model_var.get() or self.ai_model_var.get().startswith("gemini"):
                self.ai_model_var.set("llama3.1")

    def _toggle_key(self):
        self.key_entry.configure(show="" if self.show_key.get() else "•")

    def _on_aspect(self, *_):
        key = ASPECTS.get(self.aspect_var.get(), "9:16")
        custom = key == "custom"
        self.cw_spin.configure(state="normal" if custom else "disabled")
        self.ch_spin.configure(state="normal" if custom else "disabled")
        if not custom and key in ASPECT_SIZES:
            w, h = ASPECT_SIZES[key]
            self.cw_var.set(w)
            self.ch_var.set(h)

    def _apply_mode(self, *_):
        preset = MODES.get(self.mode_var.get(), MODES["Balanced"])
        self.whisper_var.set(preset["whisper"])
        self.face_var.set(preset["face"])
        self.norm_var.set(preset["normalize"])
        self.cfg.set("video.crf", preset["crf"])
        self.cfg.set("video.preset", preset["preset"])
        self.cfg.set("speed.face_interval", preset["face_interval"])
        self.cfg.set("speed.candidates", preset["candidates"])
        self.mode_desc.configure(text=preset["desc"])

    # ------------------------------------------------------------------
    def _log(self, level, message):
        self.logbox.insert("end", f"[{level}] {message}\n", level)
        self.logbox.see("end")

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Choose a video file",
            filetypes=[("Video files", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v"), ("All files", "*.*")],
        )
        if path:
            self.source_var.set(path)

    def paste_link(self):
        try:
            text = self.root.clipboard_get().strip()
            if text:
                self.source_var.set(text)
            else:
                messagebox.showinfo("Clipboard", "Your clipboard is empty.")
        except Exception:
            messagebox.showinfo("Clipboard", "Could not read the clipboard.")

    def browse_output(self):
        path = filedialog.askdirectory(title="Choose output folder", initialdir=self.out_var.get() or None)
        if path:
            self.out_var.set(path)

    def open_output(self):
        if self.result and Path(self.result["output_dir"]).exists():
            os.startfile(self.result["output_dir"])  # noqa - Windows

    # ------------------------------------------------------------------
    def _save_settings(self):
        self._apply_mode()
        self.cfg.update({
            "general.clips_per_video": int(self.clips_var.get()),
            "general.min_seconds": int(self.min_var.get()),
            "general.max_seconds": int(self.max_var.get()),
            "general.language": self.lang_var.get().strip(),
            "general.output_dir": self.out_var.get().strip(),
            "whisper.model": self.whisper_var.get(),
            "features.face_track": bool(self.face_var.get()),
            "features.captions": bool(self.cap_var.get()),
            "features.hook": bool(self.hook_var.get()),
            "features.normalize_audio": bool(self.norm_var.get()),
            "ai.provider": PROVIDERS.get(self.provider_var.get(), "local"),
            "ai.api_key": self.key_var.get().strip(),
            "ai.base_url": self.base_var.get().strip(),
            "ai.model": self.ai_model_var.get().strip(),
            "quality.mode": self.mode_var.get(),
            "video.aspect": ASPECTS.get(self.aspect_var.get(), "9:16"),
            "video.custom_width": int(self.cw_var.get()),
            "video.custom_height": int(self.ch_var.get()),
        })
        self.cfg.save()

    def start(self):
        if self.running:
            return
        source = self.source_var.get().strip().strip('"')
        if not source:
            messagebox.showwarning("Missing source", "Paste a link or choose a video file first.")
            return
        self._save_settings()
        out = Path(self.out_var.get().strip())
        try:
            out.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Output folder", f"Cannot use that folder:\n{exc}")
            return

        self.running = True
        self.result = None
        self.go_btn.configure(state="disabled", text="WORKING...")
        self.open_btn.configure(state="disabled")
        self.progress["value"] = 0
        self.logbox.delete("1.0", "end")
        self._log("HEAD", f"Starting: {source}")
        threading.Thread(target=self._worker, args=(source,), daemon=True).start()
        self.root.after(80, self._poll)

    def _worker(self, source: str):
        log = Logger(lambda level, msg: self.q.put(("log", level, msg)))
        progress = lambda pct, msg: self.q.put(("progress", pct, msg))
        try:
            self.q.put(("done", run_pipeline(source, self.cfg, log, progress)))
        except Exception as exc:
            self.q.put(("error", str(exc)))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1], item[2])
                elif kind == "progress":
                    self.progress["value"] = item[1]
                    self.status.configure(text=item[2][:34])
                elif kind == "done":
                    self.result = item[1]
                    self.running = False
                    self.progress["value"] = 100
                    self.status.configure(text="Finished")
                    self.go_btn.configure(state="normal", text="MAKE VIRAL CLIPS")
                    self.open_btn.configure(state="normal")
                    self._log("OK", f"Finished! {len(self.result['clips'])} clips in {self.result['output_dir']}")
                    messagebox.showinfo(
                        "Done!",
                        f"Created {len(self.result['clips'])} clips!\n\n{self.result['output_dir']}\n\n"
                        "Open the folder to watch and upload them.",
                    )
                    return
                elif kind == "error":
                    self.running = False
                    self.status.configure(text="Failed")
                    self.go_btn.configure(state="normal", text="MAKE VIRAL CLIPS")
                    self._log("ERROR", item[1])
                    messagebox.showerror("Could not finish", item[1][:600])
                    return
        except queue.Empty:
            pass
        if self.running:
            self.root.after(80, self._poll)


def launch():
    root = tk.Tk()
    App(root)
    root.mainloop()
