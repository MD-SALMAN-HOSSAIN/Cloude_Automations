from __future__ import annotations

import os
import queue
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core.ai import GEMINI_DEFAULT_MODEL
from core.config import Config
from core.pipeline import run_pipeline
from core.util import Logger

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

BG = "#0d0f15"
CARD = "#171b26"
CARD2 = "#1e2331"
BORDER = "#2b3242"
TEXT = "#eef0f6"
MUTED = "#98a1b6"
PINK = "#ff3d6e"
PINK_HOVER = "#ff6289"
PURPLE = "#7c5cff"
GREEN = "#3ddc97"
YELLOW = "#ffd166"
RED = "#ff6b6b"

# Fonts are created once a Tk root exists (see _init_fonts in App.__init__).
FONT = FONT_SMALL = FONT_TITLE = FONT_MONO = None


def _init_fonts():
    global FONT, FONT_SMALL, FONT_TITLE, FONT_MONO
    FONT = ctk.CTkFont(family="Segoe UI", size=13)
    FONT_SMALL = ctk.CTkFont(family="Segoe UI", size=11)
    FONT_TITLE = ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
    FONT_MONO = ctk.CTkFont(family="Consolas", size=12)

PROVIDERS = {
    "Local (free - no key)": "local",
    "Gemini API": "gemini",
    "OpenAI / Ollama / OpenRouter": "openai",
}
PROVIDER_KEYS = {v: k for k, v in PROVIDERS.items()}

ASPECTS = ["9:16", "1:1", "4:5", "3:4", "16:9", "Custom"]
ASPECT_SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
                "3:4": (1080, 1440), "16:9": (1920, 1080)}

MODE_LABELS = {"Fast": "fast", "Balanced": "balanced", "Best": "best"}
MODE_FROM_KEY = {v: k for k, v in MODE_LABELS.items()}

FRAMING_LABELS = {
    "AI picks best (recommended)": "auto",
    "Fit whole frame (blurred)": "fit_blur",
    "Fit whole frame (black bars)": "fit_black",
    "Follow subject (crops sides)": "track",
}
FRAMING_FROM_KEY = {v: k for k, v in FRAMING_LABELS.items()}

MODES = {
    "fast": {
        "whisper": "tiny", "crf": 26, "preset": "veryfast", "face": False,
        "normalize": False, "face_interval": 1.0, "candidates": 300,
        "desc": "Fastest — small model, simpler crop, quicker render. Great for previews.",
    },
    "balanced": {
        "whisper": "base", "crf": 20, "preset": "medium", "face": True,
        "normalize": True, "face_interval": 0.5, "candidates": 600,
        "desc": "Recommended — tracked framing, word captions and clean audio.",
    },
    "best": {
        "whisper": "small", "crf": 16, "preset": "slow", "face": True,
        "normalize": True, "face_interval": 0.35, "candidates": 900,
        "desc": "Highest quality — more accurate words, cleaner picture, slower render.",
    },
}


class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.cfg = Config()
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.checking = False
        self.publishing = False
        self.upload_checking = False
        self.result = None
        _init_fonts()
        self._build()
        self._fade_in()

    # ------------------------------------------------------------------
    def _fade_in(self, alpha: float = 0.0):
        self.root.attributes("-alpha", min(1.0, alpha))
        if alpha < 1.0:
            self.root.after(20, lambda: self._fade_in(alpha + 0.09))

    def _build(self):
        self.root.title("Viral Clip Make — Modern Free Shorts Maker")
        self.root.geometry("1080x950")
        self.root.minsize(980, 800)
        self.root.configure(fg_color=BG)
        self.root.resizable(True, True)

        self._header()
        self.tabs = ctk.CTkTabview(
            self.root, corner_radius=18, fg_color=BG,
            segmented_button_fg_color=CARD,
            segmented_button_selected_color=(PINK, PINK),
            segmented_button_selected_hover_color=PINK_HOVER,
            segmented_button_unselected_color=CARD2,
            segmented_button_unselected_hover_color="#2a3040",
            text_color=MUTED,
        )
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.tab_make = self.tabs.add("  Make Clips  ")
        self.tab_adv = self.tabs.add("  AI & Output  ")
        self.tab_pub = self.tabs.add("  Publish  ")

        self._make_tab()
        self._advanced_tab()
        self._publish_tab()
        self._load_advanced()
        self._load_publish()

    def _header(self):
        canvas = ctk.CTkCanvas(self.root, height=94, bg=BG, highlightthickness=0, bd=0)
        canvas.pack(fill="x")
        # subtle gradient glow behind the title
        for i in range(90):
            ratio = i / 89
            r = int(0x17 + (0x24 - 0x17) * ratio)
            g = int(0x14 + (0x18 - 0x14) * ratio)
            b = int(0x26 + (0x33 - 0x26) * ratio)
            canvas.create_rectangle(i * 12, 0, i * 12 + 13, 78,
                                    fill=f"#{r:02x}{g:02x}{b:02x}", outline="")
        canvas.create_text(10, 30, anchor="w", text="VIRAL CLIP MAKE",
                           fill=TEXT, font=("Segoe UI", 28, "bold"))
        canvas.create_text(12, 62, anchor="w",
                           text="Long video in. Viral vertical clips out. Free local engine, optional AI, any aspect ratio.",
                           fill=MUTED, font=("Segoe UI", 12))
        # gradient accent line
        for i in range(180):
            ratio = i / 179
            r = int(0x7c + (0xff - 0x7c) * ratio)
            g = int(0x5c + (0x3d - 0x5c) * ratio)
            b = int(0xff + (0x6e - 0xff) * ratio)
            canvas.create_rectangle(i * 6, 86, i * 6 + 7, 91,
                                    fill=f"#{r:02x}{g:02x}{b:02x}", outline="")

    def _card(self, parent, title: str):
        card = ctk.CTkFrame(parent, corner_radius=16, fg_color=CARD,
                            border_width=1, border_color=BORDER)
        ctk.CTkLabel(card, text=title, font=FONT_TITLE, text_color=PINK).pack(
            anchor="w", padx=18, pady=(14, 4))
        return card

    # ------------------------------------------------------------------
    def _make_tab(self):
        tab = self.tab_make

        src = self._card(tab, "VIDEO SOURCE")
        row = ctk.CTkFrame(src, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(4, 6))
        self.source_var = ctk.StringVar()
        ctk.CTkEntry(row, textvariable=self.source_var, height=40, corner_radius=10,
                     placeholder_text="Paste a YouTube link or browse for a video file...",
                     font=FONT).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(row, text="Browse", width=84, height=40, corner_radius=10,
                      fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
                      command=self.browse_file).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="Paste", width=76, height=40, corner_radius=10,
                      fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
                      command=self.paste_link).pack(side="left")
        src.pack(fill="x", pady=(12, 10))

        settings = self._card(tab, "QUICK SETTINGS")
        grid = ctk.CTkFrame(settings, fg_color="transparent")
        grid.pack(fill="x", padx=18, pady=(4, 8))

        self.clips_var = ctk.StringVar(value=str(self.cfg.get("general.clips_per_video", 5)))
        self.min_var = ctk.StringVar(value=str(self.cfg.get("general.min_seconds", 12)))
        self.max_var = ctk.StringVar(value=str(self.cfg.get("general.max_seconds", 45)))
        self.lang_var = ctk.StringVar(value=str(self.cfg.get("general.language", "")))
        self.whisper_var = ctk.StringVar(value=str(self.cfg.get("whisper.model", "base")))

        fields = [
            ("Clips", self.clips_var, 80), ("Min sec", self.min_var, 80),
            ("Max sec", self.max_var, 80),
        ]
        for i, (label, var, width) in enumerate(fields):
            ctk.CTkLabel(grid, text=label, font=FONT_SMALL, text_color=MUTED).grid(row=0, column=i * 2, sticky="w", padx=(0, 6))
            ctk.CTkEntry(grid, textvariable=var, width=width, height=36,
                         corner_radius=9, font=FONT).grid(row=1, column=i * 2, sticky="w", padx=(0, 12), pady=(3, 10))

        ctk.CTkLabel(grid, text="Speech model", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=6, sticky="w")
        ctk.CTkComboBox(grid, variable=self.whisper_var, values=["tiny", "base", "small", "medium"],
                        width=110, height=36, corner_radius=9, state="readonly",
                        font=FONT, dropdown_font=FONT).grid(row=1, column=6, sticky="w", padx=(0, 12), pady=(3, 10))
        ctk.CTkLabel(grid, text="Language", font=FONT_SMALL, text_color=MUTED).grid(row=0, column=7, sticky="w")
        ctk.CTkEntry(grid, textvariable=self.lang_var, width=110, height=36,
                     corner_radius=9, font=FONT, placeholder_text="auto").grid(row=1, column=7, sticky="w", pady=(3, 10))

        frame_row = ctk.CTkFrame(settings, fg_color="transparent")
        frame_row.pack(fill="x", padx=18, pady=(0, 2))
        ctk.CTkLabel(frame_row, text="Clip style / framing", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w")
        self.framing_var = ctk.StringVar(value=FRAMING_FROM_KEY.get(
            str(self.cfg.get("video.framing", "auto")), "AI picks best (recommended)"))
        ctk.CTkComboBox(
            frame_row, variable=self.framing_var, values=list(FRAMING_LABELS),
            width=420, height=36, corner_radius=9, state="readonly",
            font=FONT, dropdown_font=FONT, command=self._on_framing,
        ).pack(anchor="w", pady=(3, 2))
        ctk.CTkLabel(
            frame_row,
            text="AI picks best measures the subject per clip and chooses crop vs full frame "
                 "(uses your AI provider when set, otherwise the free local engine).",
            font=FONT_SMALL, text_color=MUTED, wraplength=840, justify="left",
        ).pack(anchor="w")

        out_row = ctk.CTkFrame(settings, fg_color="transparent")
        out_row.pack(fill="x", padx=18, pady=(0, 6))
        self.out_var = ctk.StringVar(value=str(self.cfg.get("general.output_dir")))
        ctk.CTkLabel(out_row, text="Output folder", font=FONT_SMALL, text_color=MUTED).pack(anchor="w")
        r2 = ctk.CTkFrame(out_row, fg_color="transparent")
        r2.pack(fill="x", pady=(3, 0))
        ctk.CTkEntry(r2, textvariable=self.out_var, height=38, corner_radius=10,
                     font=FONT).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(r2, text="Change", width=84, height=38, corner_radius=10,
                      fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
                      command=self.browse_output).pack(side="left")

        switches = ctk.CTkFrame(settings, fg_color="transparent")
        switches.pack(fill="x", padx=18, pady=(8, 14))
        self.face_var = ctk.BooleanVar(value=bool(self.cfg.get("features.face_track", True)))
        self.cap_var = ctk.BooleanVar(value=bool(self.cfg.get("features.captions", True)))
        self.hook_var = ctk.BooleanVar(value=bool(self.cfg.get("features.hook", True)))
        self.norm_var = ctk.BooleanVar(value=bool(self.cfg.get("features.normalize_audio", True)))
        for text, var in (("Face tracking", self.face_var), ("Captions", self.cap_var),
                          ("Hook overlay", self.hook_var), ("Normalize audio", self.norm_var)):
            switch = ctk.CTkSwitch(switches, text=text, variable=var, onvalue=True, offvalue=False,
                                   font=FONT_SMALL, progress_color=PINK,
                                   fg_color="#33394a")
            switch.pack(side="left", padx=(0, 18))
            if text == "Face tracking":
                self.face_switch = switch
        settings.pack(fill="x", pady=(0, 10))

        self.go_btn = ctk.CTkButton(
            tab, text="MAKE VIRAL CLIPS", height=54, corner_radius=14,
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            fg_color=PINK, hover_color=PINK_HOVER, command=self.start,
        )
        self.go_btn.pack(fill="x", pady=(2, 12))

        prog = ctk.CTkFrame(tab, fg_color="transparent")
        prog.pack(fill="x", pady=(0, 8))
        self.progress = ctk.CTkProgressBar(prog, height=10, corner_radius=6,
                                           fg_color="#202634", progress_color=PINK)
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.status = ctk.CTkLabel(prog, text="Ready", width=220, anchor="e",
                                   font=FONT_SMALL, text_color=MUTED)
        self.status.pack(side="right")

        logcard = ctk.CTkFrame(tab, corner_radius=16, fg_color="#0b0e14",
                               border_width=1, border_color=BORDER)
        logcard.pack(fill="both", expand=True)
        ctk.CTkLabel(logcard, text="ACTIVITY", font=FONT_TITLE,
                     text_color=PURPLE).pack(anchor="w", padx=18, pady=(12, 2))
        self.logbox = ctk.CTkTextbox(logcard, corner_radius=12, font=FONT_MONO,
                                     fg_color="#080a0f", text_color=TEXT)
        self.logbox.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        for tag, color in (("INFO", MUTED), ("OK", GREEN), ("WARN", YELLOW),
                           ("ERROR", RED), ("HEAD", PURPLE)):
            self.logbox.tag_config(tag, foreground=color)

        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.pack(fill="x", pady=(10, 0))
        self.open_btn = ctk.CTkButton(bottom, text="Open output folder", height=38,
                                      corner_radius=10, fg_color=CARD2,
                                      hover_color="#2c3344", text_color=TEXT,
                                      state="disabled", command=self.open_output)
        self.open_btn.pack(side="left")
        self.upload_btn = ctk.CTkButton(
            bottom, text="Preview & publish", height=38, corner_radius=10,
            fg_color="#2f6f5e", hover_color="#3d8a75", text_color=TEXT,
            state="disabled", command=self.preview_clicked,
        )
        self.upload_btn.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(bottom, text="Free local mode by default · no account · no watermark",
                     font=FONT_SMALL, text_color=MUTED).pack(side="right")
        self._log("HEAD", "Welcome! Paste a link or pick a video, then press MAKE VIRAL CLIPS.")

    # ------------------------------------------------------------------
    def _advanced_tab(self):
        scroll = ctk.CTkScrollableFrame(self.tab_adv, corner_radius=16,
                                        fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        ai = self._card(scroll, "OPTIONAL AI / API  —  skip this for the free local engine")
        self.provider_var = ctk.StringVar(value=PROVIDER_KEYS.get(
            str(self.cfg.get("ai.provider", "local")), "Local (free - no key)"))
        ctk.CTkLabel(ai, text="Provider", font=FONT_SMALL, text_color=MUTED).pack(anchor="w", padx=18, pady=(4, 2))
        ctk.CTkComboBox(ai, variable=self.provider_var, values=list(PROVIDERS),
                        width=430, height=40, corner_radius=10, state="readonly",
                        font=FONT, dropdown_font=FONT,
                        command=self._on_provider).pack(anchor="w", padx=18, pady=(0, 12))

        self.key_var = ctk.StringVar(value=str(self.cfg.get("ai.api_key", "") or ""))
        self.base_var = ctk.StringVar(value=str(self.cfg.get("ai.base_url", "")))
        self.ai_model_var = ctk.StringVar(value=str(self.cfg.get("ai.model", "")))

        pairs = [
            ("API key", self.key_var, "Paste your key (stored only on this PC)"),
            ("Base URL", self.base_var, "http://localhost:11434/v1"),
            ("Model", self.ai_model_var, "gemini-2.5-flash / llama3.1"),
        ]
        for label, var, placeholder in pairs:
            ctk.CTkLabel(ai, text=label, font=FONT_SMALL, text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 2))
            show = "•" if label == "API key" else ""
            ctk.CTkEntry(ai, textvariable=var, height=40, corner_radius=10,
                         placeholder_text=placeholder, font=FONT,
                         show=show).pack(fill="x", padx=18, pady=(0, 10))

        ctk.CTkLabel(ai, text=(
            "Gemini: use a free key from aistudio.google.com  ·  Ollama / LM Studio: use the local URL above  ·  "
            "OpenRouter: https://openrouter.ai/api/v1. If an API fails, the app falls back to free local scoring."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=880,
            justify="left").pack(anchor="w", padx=18, pady=(2, 10))

        conn = ctk.CTkFrame(ai, corner_radius=12, fg_color=CARD2)
        conn.pack(fill="x", padx=18, pady=(0, 16))
        self.conn_dot = ctk.CTkLabel(conn, text="●", font=ctk.CTkFont(size=16),
                                     text_color=MUTED)
        self.conn_dot.pack(side="left", padx=(16, 6))
        self.conn_label = ctk.CTkLabel(conn, text="Not checked — press Check connection",
                                       font=FONT_SMALL, text_color=MUTED)
        self.conn_label.pack(side="left", fill="x", expand=True)
        self.conn_btn = ctk.CTkButton(
            conn, text="Check connection", width=158, height=36, corner_radius=10,
            fg_color="#2f6f5e", hover_color="#3d8a75", text_color=TEXT,
            font=FONT_SMALL, command=self.check_connection_clicked,
        )
        self.conn_btn.pack(side="right", padx=10, pady=8)

        for var in (self.key_var, self.base_var, self.ai_model_var):
            var.trace_add("write", lambda *_: self._on_ai_field_change())
        ai.pack(fill="x", pady=(0, 12))

        out = self._card(scroll, "OUTPUT SIZE")
        self.aspect_var = ctk.StringVar(value=str(self.cfg.get("video.aspect", "9:16")))
        if self.aspect_var.get() == "custom":
            self.aspect_var.set("Custom")
        ctk.CTkLabel(out, text="Aspect ratio", font=FONT_SMALL, text_color=MUTED).pack(anchor="w", padx=18, pady=(4, 4))
        self.aspect_seg = ctk.CTkSegmentedButton(
            out, values=ASPECTS, variable=self.aspect_var, height=40,
            corner_radius=10, font=FONT_SMALL,
            selected_color=PINK, selected_hover_color=PINK_HOVER,
            unselected_color=CARD2, unselected_hover_color="#2c3344",
            text_color=TEXT, command=self._on_aspect,
        )
        self.aspect_seg.pack(fill="x", padx=18, pady=(0, 12))

        dims = ctk.CTkFrame(out, fg_color="transparent")
        dims.pack(fill="x", padx=18, pady=(0, 14))
        self.cw_var = ctk.StringVar(value=str(self.cfg.get("video.custom_width", 1080)))
        self.ch_var = ctk.StringVar(value=str(self.cfg.get("video.custom_height", 1920)))
        ctk.CTkLabel(dims, text="Custom width", font=FONT_SMALL, text_color=MUTED).pack(side="left")
        self.cw_entry = ctk.CTkEntry(dims, textvariable=self.cw_var, width=90, height=36,
                                     corner_radius=9, font=FONT)
        self.cw_entry.pack(side="left", padx=(8, 16))
        ctk.CTkLabel(dims, text="Custom height", font=FONT_SMALL, text_color=MUTED).pack(side="left")
        self.ch_entry = ctk.CTkEntry(dims, textvariable=self.ch_var, width=90, height=36,
                                     corner_radius=9, font=FONT)
        self.ch_entry.pack(side="left", padx=(8, 0))
        out.pack(fill="x", pady=(0, 12))

        qual = self._card(scroll, "SPEED / QUALITY")
        key = str(self.cfg.get("quality.mode", "balanced"))
        self.mode_var = ctk.StringVar(value=MODE_FROM_KEY.get(key, "Balanced"))
        ctk.CTkLabel(qual, text="Render mode", font=FONT_SMALL, text_color=MUTED).pack(anchor="w", padx=18, pady=(4, 4))
        self.mode_seg = ctk.CTkSegmentedButton(
            qual, values=list(MODE_LABELS.keys()), variable=self.mode_var, height=40,
            corner_radius=10, font=FONT_SMALL,
            selected_color=PURPLE, selected_hover_color="#9178ff",
            unselected_color=CARD2, unselected_hover_color="#2c3344",
            text_color=TEXT, command=self._on_mode,
        )
        self.mode_seg.pack(fill="x", padx=18, pady=(0, 10))
        self.mode_desc = ctk.CTkLabel(qual, text="", font=FONT_SMALL,
                                      text_color=MUTED, wraplength=880, justify="left")
        self.mode_desc.pack(anchor="w", padx=18, pady=(0, 16))
        qual.pack(fill="x", pady=(0, 12))

        save_card = ctk.CTkFrame(scroll, corner_radius=16, fg_color=CARD,
                                 border_width=1, border_color=BORDER)
        save_row = ctk.CTkFrame(save_card, fg_color="transparent")
        save_row.pack(fill="x", padx=18, pady=16)
        self.save_btn = ctk.CTkButton(
            save_row, text="Save settings", width=170, height=44, corner_radius=12,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            fg_color=PURPLE, hover_color="#9178ff", command=self.save_clicked,
        )
        self.save_btn.pack(side="left")
        self.saved_label = ctk.CTkLabel(save_row, text="", font=FONT_SMALL, text_color=GREEN)
        self.saved_label.pack(side="left", padx=16)
        ctk.CTkLabel(save_row, text="Saves AI keys, aspect ratio and speed mode for every future creation.",
                     font=FONT_SMALL, text_color=MUTED).pack(side="left", padx=(0, 0))
        save_card.pack(fill="x")

    # ------------------------------------------------------------------
    def _publish_tab(self):
        from core.upload_post import PLATFORMS
        self.PLATFORMS = PLATFORMS

        scroll = ctk.CTkScrollableFrame(self.tab_pub, corner_radius=16, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # ---------------- Step 1: connect ----------------
        setup = self._card(scroll, "STEP 1 — CONNECT ONCE  (no technical setup)")
        ctk.CTkLabel(setup, text=(
            "Paste your Upload-Post API key once (from app.upload-post.com → API Keys). Then press\n"
            "Connect — a page opens where you simply sign in to TikTok / Instagram / YouTube.\n"
            "The app creates your profile and checks the connection automatically."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=880,
            justify="left").pack(anchor="w", padx=18, pady=(4, 8))

        kr = ctk.CTkFrame(setup, fg_color="transparent")
        kr.pack(fill="x", padx=18, pady=(0, 6))
        ctk.CTkLabel(kr, text="Upload-Post API key", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w")
        row = ctk.CTkFrame(kr, fg_color="transparent")
        row.pack(fill="x", pady=(3, 0))
        self.up_key_var = ctk.StringVar(value=str(self.cfg.get("upload.api_key", "") or ""))
        self.up_key_entry = ctk.CTkEntry(row, textvariable=self.up_key_var, height=40,
                                        corner_radius=10, show="•", font=FONT,
                                        placeholder_text="Paste your Upload-Post API key")
        self.up_key_entry.pack(side="left", fill="x", expand=True)
        self.up_show_key = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row, text="Show", variable=self.up_show_key, width=60,
                        font=FONT_SMALL, command=self._toggle_upload_key,
                        fg_color=PINK, border_color="#33394a").pack(side="left", padx=(8, 0))
        ctk.CTkButton(row, text="Get key", width=90, height=40, corner_radius=10,
                      font=FONT_SMALL, fg_color=CARD2, hover_color="#2c3344",
                      text_color=TEXT, command=self.open_upload_post_site).pack(
                          side="left", padx=(8, 0))

        actions = ctk.CTkFrame(setup, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(10, 4))
        self.up_connect_btn = ctk.CTkButton(
            actions, text="Connect my social accounts", height=46, corner_radius=12,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            fg_color="#2f6f5e", hover_color="#3d8a75", text_color=TEXT,
            command=self.connect_clicked,
        )
        self.up_connect_btn.pack(side="left")
        self.up_open_page_btn = ctk.CTkButton(
            actions, text="Open connect page again", height=46, corner_radius=12,
            font=FONT_SMALL, fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
            state="disabled", command=self.open_connect_page_clicked,
        )
        self.up_open_page_btn.pack(side="left", padx=(10, 0))
        ctk.CTkButton(actions, text="Check key", height=46, corner_radius=12,
                      font=FONT_SMALL, fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
                      command=self.check_upload_clicked).pack(side="left", padx=(10, 0))

        conn = ctk.CTkFrame(setup, corner_radius=12, fg_color=CARD2)
        conn.pack(fill="x", padx=18, pady=(8, 6))
        self.up_dot = ctk.CTkLabel(conn, text="●", font=ctk.CTkFont(size=16), text_color=MUTED)
        self.up_dot.pack(side="left", padx=(16, 6))
        self.up_status = ctk.CTkLabel(conn, text="Not connected yet", font=FONT_SMALL,
                                      text_color=MUTED)
        self.up_status.pack(side="left", fill="x", expand=True)
        self.up_stop_btn = ctk.CTkButton(conn, text="Stop auto-check", width=140, height=34,
                                         corner_radius=10, font=FONT_SMALL, fg_color="#33394a",
                                         hover_color="#41495d", text_color=TEXT,
                                         state="disabled", command=self.stop_watching_clicked)
        self.up_stop_btn.pack(side="right", padx=10, pady=7)

        self.up_profiles = ctk.CTkTextbox(setup, height=104, corner_radius=10,
                                          fg_color="#0b0e14", font=FONT_SMALL, text_color=TEXT)
        self.up_profiles.pack(fill="x", padx=18, pady=(0, 6))
        self.up_profiles.insert("1.0", "Connected accounts will appear here automatically.")
        self.up_profiles.configure(state="disabled")
        ctk.CTkButton(setup, text="Refresh connected accounts", height=34, corner_radius=10,
                      font=FONT_SMALL, fg_color="#33394a", hover_color="#41495d",
                      text_color=TEXT, command=self.refresh_accounts_clicked).pack(
                          anchor="w", padx=18, pady=(0, 16))
        setup.pack(fill="x", pady=(0, 12))

        # ---------------- Step 2: destination ----------------
        prefs = self._card(scroll, "STEP 2 — WHERE TO POST")
        ctk.CTkLabel(prefs, text="Your profile (created automatically)", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(4, 2))
        self.up_user_var = ctk.StringVar(value=str(self.cfg.get("upload.user", "") or ""))
        ctk.CTkEntry(prefs, textvariable=self.up_user_var, height=40, corner_radius=10,
                     font=FONT, placeholder_text="Created when you press Connect").pack(
                         fill="x", padx=18, pady=(0, 10))

        ctk.CTkLabel(prefs, text="Post to (only connected accounts can be selected)",
                     font=FONT_SMALL, text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 4))
        self.up_platform_vars = {}
        self.up_platform_boxes = {}
        saved_platforms = self.cfg.get("upload.platforms", []) or []
        if isinstance(saved_platforms, str):
            saved_platforms = [p.strip() for p in saved_platforms.split(",") if p.strip()]
        pf = ctk.CTkFrame(prefs, fg_color="transparent")
        pf.pack(fill="x", padx=18, pady=(0, 10))
        for i, (key, label) in enumerate(PLATFORMS.items()):
            var = ctk.BooleanVar(value=key in saved_platforms)
            box = ctk.CTkCheckBox(pf, text=label, variable=var, font=FONT_SMALL,
                                  fg_color=PINK, border_color="#33394a")
            box.grid(row=i // 3, column=i % 3, sticky="w", pady=5, padx=(0, 26))
            self.up_platform_vars[key] = var
            self.up_platform_boxes[key] = box

        ctk.CTkLabel(prefs, text="Hashtags / caption ending", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 2))
        self.up_suffix_var = ctk.StringVar(value=str(self.cfg.get("upload.caption_suffix", "") or ""))
        ctk.CTkEntry(prefs, textvariable=self.up_suffix_var, height=40, corner_radius=10,
                     font=FONT, placeholder_text="#shorts #viral").pack(fill="x", padx=18, pady=(0, 10))

        sw = ctk.CTkFrame(prefs, fg_color="transparent")
        sw.pack(fill="x", padx=18, pady=(0, 12))
        self.up_auto_var = ctk.BooleanVar(value=bool(self.cfg.get("upload.auto_publish", False)))
        self.up_wait_var = ctk.BooleanVar(value=bool(self.cfg.get("upload.wait_for_status", True)))
        ctk.CTkSwitch(sw, text="Auto-upload every new clip", variable=self.up_auto_var,
                      font=FONT_SMALL, progress_color=PINK,
                      fg_color="#33394a").pack(side="left", padx=(0, 20))
        ctk.CTkSwitch(sw, text="Wait for publish confirmation", variable=self.up_wait_var,
                      font=FONT_SMALL, progress_color=PINK,
                      fg_color="#33394a").pack(side="left")

        save_row = ctk.CTkFrame(prefs, fg_color="transparent")
        save_row.pack(fill="x", padx=18, pady=(4, 16))
        ctk.CTkButton(save_row, text="Save settings", width=170, height=44, corner_radius=12,
                      font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
                      fg_color=PURPLE, hover_color="#9178ff",
                      command=self.save_clicked).pack(side="left")
        self.up_saved_label = ctk.CTkLabel(save_row, text="", font=FONT_SMALL, text_color=GREEN)
        self.up_saved_label.pack(side="left", padx=16)
        prefs.pack(fill="x", pady=(0, 12))

        # ---------------- Step 3: preview & publish ----------------
        now = self._card(scroll, "STEP 3 — PREVIEW, THEN PUBLISH")
        ctk.CTkLabel(now, text=(
            "Make clips first. Then preview each clip with its caption and destination before it goes live."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=880,
            justify="left").pack(anchor="w", padx=18, pady=(4, 8))
        self.up_preview_btn = ctk.CTkButton(
            now, text="Preview & publish clips", height=48, corner_radius=12,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            fg_color="#2f6f5e", hover_color="#3d8a75", text_color=TEXT,
            state="disabled", command=self.preview_clicked,
        )
        self.up_preview_btn.pack(fill="x", padx=18, pady=(0, 8))
        self.up_publish_btn = ctk.CTkButton(
            now, text="Publish all without preview", height=34, corner_radius=10,
            font=FONT_SMALL, fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
            state="disabled", command=self.upload_last_clicked,
        )
        self.up_publish_btn.pack(fill="x", padx=18, pady=(0, 10))
        self.up_progress = ctk.CTkProgressBar(now, height=8, corner_radius=5,
                                              fg_color="#202634", progress_color=GREEN)
        self.up_progress.set(0)
        self.up_progress.pack(fill="x", padx=18, pady=(0, 12))
        self.up_log = ctk.CTkTextbox(now, height=150, corner_radius=10,
                                     fg_color="#080a0f", font=FONT_MONO, text_color=TEXT)
        self.up_log.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        now.pack(fill="both", expand=True)

    def _load_publish(self):
        self.up_key_var.trace_add("write", lambda *_: self._reset_upload_status())
        self.connected_accounts = {}
        self.connecting = False
        self.watch_until = 0.0
        self.connect_link = ""
        self.preview_window = None

    def _toggle_upload_key(self):
        self.up_key_entry.configure(show="" if self.up_show_key.get() else "•")

    def open_upload_post_site(self):
        webbrowser.open("https://app.upload-post.com/")

    def _reset_upload_status(self, *_):
        if getattr(self, "up_dot", None) is None:
            return
        self.up_dot.configure(text_color=MUTED)
        self.up_status.configure(text="Key changed — press Connect again", text_color=MUTED)
        self.up_open_page_btn.configure(state="disabled")

    def _upload_log(self, level, message):
        self.up_log.insert("end", f"[{level}] {message}\n")
        self.up_log.see("end")

    # -------- one-click social sign-in --------
    def connect_clicked(self):
        if self.upload_checking or self.connecting:
            return
        self._save_settings()
        if not self.up_key_var.get().strip():
            messagebox.showwarning(
                "Upload-Post key needed",
                "Get a key at app.upload-post.com → API Keys, paste it above, then press Connect.",
            )
            self.up_key_entry.focus()
            return

        user = self.up_user_var.get().strip() or ("viralclip_" + uuid.uuid4().hex[:8])
        self.up_user_var.set(user)
        self.cfg.set("upload.user", user)
        self.cfg.save()

        self.upload_checking = True
        self.up_connect_btn.configure(state="disabled", text="CONNECTING…")
        self.up_dot.configure(text_color=YELLOW)
        self.up_status.configure(text="Checking your key and creating your profile…",
                                 text_color=YELLOW)
        threading.Thread(target=self._connect_worker, args=(user,), daemon=True).start()
        self.root.after(80, self._poll)

    def _connect_worker(self, user: str):
        try:
            from core.upload_post import (check_connection, create_profile,
                                          generate_connect_url)
            status = check_connection(self.cfg)
            if not status["ok"]:
                self.q.put(("connect_error", status["message"]))
                return
            create_profile(self.cfg, user, "Viral Clip Make")
            link = generate_connect_url(self.cfg, user,
                                        self.cfg.get("upload.platforms") or [])
            self.q.put(("connect_ready", link))
        except Exception as exc:
            self.q.put(("connect_error", " ".join(str(exc).split())[:300]))

    def open_connect_page_clicked(self):
        if self.connect_link:
            webbrowser.open(self.connect_link)
            self._upload_log("INFO", "Connect page opened again in your browser.")
            self.start_watching()
        else:
            self.connect_clicked()

    def stop_watching_clicked(self):
        self.connecting = False
        self.up_stop_btn.configure(state="disabled")
        self.up_status.configure(text="Auto-check stopped — press Refresh anytime",
                                 text_color=MUTED)

    def start_watching(self):
        self.connecting = True
        self.watch_until = time.monotonic() + 300
        self.up_stop_btn.configure(state="normal")
        self._watch_tick()

    def _watch_tick(self):
        if not self.connecting:
            return
        if time.monotonic() > self.watch_until:
            self.stop_watching_clicked()
            return
        self.root.after(6000, self._watch_tick)
        threading.Thread(target=self._watch_worker, daemon=True).start()

    def _watch_worker(self):
        try:
            from core.upload_post import connected_accounts
            accounts = connected_accounts(self.cfg, self.up_user_var.get().strip())
            self.q.put(("watch_accounts", accounts))
        except Exception as exc:
            self.q.put(("watch_accounts", {"error": " ".join(str(exc).split())[:200]}))
        self.root.after(80, self._poll)

    def refresh_accounts_clicked(self):
        self._save_settings()
        if not self.up_user_var.get().strip():
            messagebox.showinfo("Not connected yet", "Press Connect first to create your profile.")
            return
        self.up_status.configure(text="Refreshing connected accounts…", text_color=YELLOW)
        threading.Thread(target=self._watch_worker, daemon=True).start()
        self.root.after(80, self._poll)

    def _render_accounts(self, new: set | None = None):
        accounts = self.connected_accounts
        lines = []
        for platform, label in self.PLATFORMS.items():
            info = accounts.get(platform)
            if info and info.get("connected"):
                handle = info.get("handle") or info.get("display_name") or "connected"
                extra = "  (reconnect needed)" if info.get("reauth_required") else ""
                lines.append(f"  ✓ {label:<12} @{handle}{extra}")
            else:
                lines.append(f"  · {label:<12} not connected yet")
        if not accounts:
            lines.append("\nNo accounts connected yet — sign in on the page that opened in your browser.")
        self.up_profiles.configure(state="normal")
        self.up_profiles.delete("1.0", "end")
        self.up_profiles.insert("1.0", "\n".join(lines))
        self.up_profiles.configure(state="disabled")

        for platform, box in self.up_platform_boxes.items():
            info = accounts.get(platform)
            if info and info.get("connected"):
                handle = info.get("handle") or info.get("display_name") or "connected"
                box.configure(state="normal", text=f"{self.PLATFORMS[platform]}  @{handle}")
                self.up_platform_vars[platform].set(True)
            else:
                self.up_platform_vars[platform].set(False)
                box.configure(state="disabled", text=self.PLATFORMS[platform])

        if new:
            self.up_dot.configure(text_color=GREEN)
            self.up_status.configure(text="Connected: " + ", ".join(sorted(new)),
                                     text_color=GREEN)
        elif accounts:
            self.up_dot.configure(text_color=GREEN)
            self.up_status.configure(
                text="Connected: " + ", ".join(sorted(accounts)), text_color=GREEN)
        else:
            self.up_dot.configure(text_color=YELLOW)
            self.up_status.configure(text="Waiting for sign-in… (checking automatically)",
                                     text_color=YELLOW)

    def check_upload_clicked(self):
        if self.upload_checking:
            return
        self._save_settings()
        self.upload_checking = True
        self.up_dot.configure(text_color=YELLOW)
        self.up_status.configure(text="Checking key…", text_color=YELLOW)
        threading.Thread(target=self._upload_check_worker, daemon=True).start()
        self.root.after(80, self._poll)

    def _upload_check_worker(self):
        try:
            from core.upload_post import check_connection
            status = check_connection(self.cfg)
        except Exception as exc:
            status = {"ok": False, "message": str(exc)[:250], "profiles": []}
        self.q.put(("upload_check", status))

    # -------- publish (with or without preview) --------
    def _validate_publish(self) -> bool:
        if self.publishing:
            return False
        self._save_settings()
        if not self.result:
            messagebox.showinfo("Make clips first", "Create your clips, then come back to publish them.")
            return False
        if not self.cfg.get("upload.api_key"):
            messagebox.showwarning("Upload-Post", "Paste your Upload-Post key in Step 1 first.")
            self.tabs.set("  Publish  ")
            return False
        if not self.cfg.get("upload.user"):
            messagebox.showwarning("Upload-Post", "Press Connect in Step 1 to create your profile.")
            self.tabs.set("  Publish  ")
            return False
        if not self.cfg.get("upload.platforms"):
            messagebox.showwarning("Upload-Post", "Select at least one connected platform in Step 2.")
            self.tabs.set("  Publish  ")
            return False
        return True

    def preview_clicked(self):
        if not self._validate_publish():
            return
        self.tabs.set("  Publish  ")
        self.preview_window = PublishPreview(self, self.result["clips"])

    def upload_last_clicked(self):
        if not self._validate_publish():
            return

        self.publishing = True
        self.upload_btn.configure(state="disabled", text="UPLOADING…")
        self.up_preview_btn.configure(state="disabled", text="UPLOADING…")
        self.up_publish_btn.configure(state="disabled", text="UPLOADING…")
        self.up_progress.set(0)
        self.up_log.delete("1.0", "end")
        self.tabs.set("  Publish  ")
        self._upload_log("INFO", "Starting upload...")
        threading.Thread(target=self._upload_worker, daemon=True).start()
        self.root.after(80, self._poll)

    def _upload_worker(self):
        from core.upload_post import publish_clips
        from core.util import Logger

        log = Logger(lambda level, msg: self.q.put(("publish_log", level, msg)))
        clips = self.result["clips"]
        try:
            results = publish_clips(
                [Path(c["path"]) for c in clips], self.cfg, log,
                titles=[c["title"] for c in clips],
                on_progress=lambda pct, msg: self.q.put(("publish_progress", pct, msg)),
            )
            self.q.put(("publish_done", results))
        except Exception as exc:
            self.q.put(("publish_error", str(exc)))

    # ------------------------------------------------------------------
    def _load_advanced(self):
        self._on_provider()
        self._on_aspect()
        self._on_mode()
        self._on_framing()

    def _on_provider(self, _value=None):
        provider = PROVIDERS.get(self.provider_var.get(), "local")
        if provider == "gemini":
            current = self.ai_model_var.get().strip()
            if not current or current.startswith(("llama", "gpt", "openai/", "mistral")):
                self.ai_model_var.set(GEMINI_DEFAULT_MODEL)
        elif provider == "openai":
            if not self.base_var.get() or "localhost" in self.base_var.get():
                if not self.base_var.get():
                    self.base_var.set("http://localhost:11434/v1")
            if not self.ai_model_var.get() or self.ai_model_var.get().startswith("gemini"):
                self.ai_model_var.set("llama3.1")

    def _on_framing(self, _value=None):
        framing = FRAMING_LABELS.get(self.framing_var.get(), "auto")
        self.cfg.set("video.framing", framing)
        if getattr(self, "face_switch", None) is not None:
            # Auto framing measures the subject, so keep tracking available.
            state = "normal" if framing in ("track", "auto") else "disabled"
            self.face_switch.configure(state=state)

    def _on_aspect(self, _value=None):
        custom = self.aspect_var.get() == "Custom"
        state = "normal" if custom else "disabled"
        self.cw_entry.configure(state=state)
        self.ch_entry.configure(state=state)
        if not custom and self.aspect_var.get() in ASPECT_SIZES:
            w, h = ASPECT_SIZES[self.aspect_var.get()]
            self.cw_var.set(str(w))
            self.ch_var.set(str(h))

    def _on_mode(self, _value=None):
        preset = MODES.get(MODE_LABELS.get(self.mode_var.get(), "balanced"), MODES["balanced"])
        self.whisper_var.set(preset["whisper"])
        self.face_var.set(preset["face"])
        self.norm_var.set(preset["normalize"])
        self.cfg.set("video.crf", preset["crf"])
        self.cfg.set("video.preset", preset["preset"])
        self.cfg.set("speed.face_interval", preset["face_interval"])
        self.cfg.set("speed.candidates", preset["candidates"])
        self.mode_desc.configure(text=preset["desc"])

    # ------------------------------------------------------------------
    def _reset_connection(self, *_):
        if getattr(self, "conn_dot", None) is None:
            return
        self.conn_dot.configure(text_color=MUTED)
        self.conn_label.configure(text="Not checked — settings changed, check again",
                                  text_color=MUTED)

    def _on_ai_field_change(self, *_):
        # Auto-switch the provider when a recognisable key is pasted, so a Google
        # AI Studio key is never sent to a local Ollama server by mistake.
        key = self.key_var.get().strip().strip('"').strip("'")
        if key.startswith(("AIza", "AQ.")):
            if self.provider_var.get() != "Gemini API":
                self.provider_var.set("Gemini API")
                self._on_provider()
        elif key.startswith(("sk-", "sk_")):
            if self.provider_var.get() != "OpenAI / Ollama / OpenRouter":
                self.provider_var.set("OpenAI / Ollama / OpenRouter")
                self._on_provider()
        self._reset_connection()

    def check_connection_clicked(self):
        if self.checking:
            return
        self._save_settings()  # test exactly what creation will use
        self.checking = True
        self.conn_btn.configure(state="disabled", text="Checking…")
        self.conn_dot.configure(text_color=YELLOW)
        self.conn_label.configure(text="Checking…", text_color=YELLOW)
        threading.Thread(target=self._connection_worker, daemon=True).start()
        self.root.after(80, self._poll)

    def _connection_worker(self):
        try:
            from core.ai import test_connection
            status = test_connection(self.cfg)
        except Exception as exc:
            status = {"ok": False, "message": str(exc)[:180], "latency_ms": 0}
        self.q.put(("connection", status))

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
    def _int(self, var, default: int) -> int:
        try:
            return int(str(var.get()).strip())
        except (ValueError, TypeError):
            return default

    def _save_settings(self):
        self._on_mode()
        self.cfg.update({
            "general.clips_per_video": self._int(self.clips_var, 5),
            "general.min_seconds": self._int(self.min_var, 12),
            "general.max_seconds": self._int(self.max_var, 45),
            "general.language": self.lang_var.get().strip(),
            "general.output_dir": self.out_var.get().strip(),
            "whisper.model": self.whisper_var.get(),
            "video.framing": FRAMING_LABELS.get(self.framing_var.get(), "auto"),
            "features.face_track": bool(self.face_var.get()),
            "features.captions": bool(self.cap_var.get()),
            "features.hook": bool(self.hook_var.get()),
            "features.normalize_audio": bool(self.norm_var.get()),
            "ai.provider": PROVIDERS.get(self.provider_var.get(), "local"),
            "ai.api_key": self.key_var.get().strip(),
            "ai.base_url": self.base_var.get().strip(),
            "ai.model": self.ai_model_var.get().strip(),
            "quality.mode": MODE_LABELS.get(self.mode_var.get(), "balanced"),
            "video.aspect": "custom" if self.aspect_var.get() == "Custom" else self.aspect_var.get(),
            "video.custom_width": self._int(self.cw_var, 1080),
            "video.custom_height": self._int(self.ch_var, 1920),
            "upload.api_key": self.up_key_var.get().strip(),
            "upload.user": self.up_user_var.get().strip(),
            "upload.platforms": [k for k, v in self.up_platform_vars.items() if v.get()],
            "upload.caption_suffix": self.up_suffix_var.get().strip(),
            "upload.auto_publish": bool(self.up_auto_var.get()),
            "upload.wait_for_status": bool(self.up_wait_var.get()),
        })
        self.cfg.save()

    def save_clicked(self):
        self._save_settings()
        self.saved_label.configure(text="✓ Saved — ready for your next creation")
        if getattr(self, "up_saved_label", None) is not None:
            self.up_saved_label.configure(text="✓ Saved — ready for your next creation")
        self.root.after(2600, lambda: self._clear_saved_labels())

    def _clear_saved_labels(self):
        self.saved_label.configure(text="")
        if getattr(self, "up_saved_label", None) is not None:
            self.up_saved_label.configure(text="")

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
        self.go_btn.configure(state="disabled", text="WORKING…", fg_color="#7a3048")
        self.open_btn.configure(state="disabled")
        self.upload_btn.configure(state="disabled")
        self.up_preview_btn.configure(state="disabled")
        self.up_publish_btn.configure(state="disabled")
        self.progress.set(0)
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
                    self.progress.set(item[1] / 100.0)
                    self.status.configure(text=item[2][:32])
                elif kind == "connection":
                    self.checking = False
                    st = item[1]
                    self.conn_btn.configure(state="normal", text="Check connection")
                    if st["ok"]:
                        self.conn_dot.configure(text_color=GREEN)
                        self.conn_label.configure(
                            text=f"Connected · {st['latency_ms']} ms · {st['message']}",
                            text_color=GREEN,
                        )
                    else:
                        self.conn_dot.configure(text_color=RED)
                        self.conn_label.configure(text=f"Failed · {st['message']}",
                                                  text_color=RED)
                elif kind == "upload_check":
                    self.upload_checking = False
                    st = item[1]
                    if st["ok"]:
                        self.up_dot.configure(text_color=GREEN)
                        self.up_status.configure(text=st["message"], text_color=GREEN)
                        profiles = st.get("profiles") or []
                        current = self.up_user_var.get().strip()
                        chosen = next((p for p in profiles if p["username"] == current),
                                      profiles[0] if profiles else None)
                        if chosen:
                            if not current:
                                self.up_user_var.set(chosen["username"])
                            self.connected_accounts = chosen.get("accounts") or {}
                            self._render_accounts()
                        else:
                            self.up_profiles.configure(state="normal")
                            self.up_profiles.delete("1.0", "end")
                            self.up_profiles.insert("1.0", "No profiles found yet — press Connect.")
                            self.up_profiles.configure(state="disabled")
                    else:
                        self.up_dot.configure(text_color=RED)
                        self.up_status.configure(text=f"Failed · {st['message']}", text_color=RED)
                        self.up_profiles.configure(state="normal")
                        self.up_profiles.delete("1.0", "end")
                        self.up_profiles.insert("1.0", st["message"])
                        self.up_profiles.configure(state="disabled")
                elif kind == "connect_ready":
                    self.upload_checking = False
                    self.up_connect_btn.configure(state="normal",
                                                  text="Connect my social accounts")
                    self.connect_link = item[1]["url"]
                    self.up_open_page_btn.configure(state="normal")
                    webbrowser.open(self.connect_link)
                    self.up_dot.configure(text_color=GREEN)
                    self.up_status.configure(
                        text="Browser opened — sign in there. Checking automatically…",
                        text_color=GREEN,
                    )
                    self._upload_log("INFO", "Connect page opened in your browser "
                                              f"(link valid {item[1].get('duration', '48h')}).")
                    self.start_watching()
                elif kind == "connect_error":
                    self.upload_checking = False
                    self.up_connect_btn.configure(state="normal",
                                                  text="Connect my social accounts")
                    self.up_dot.configure(text_color=RED)
                    self.up_status.configure(text=f"Could not connect · {item[1][:120]}",
                                             text_color=RED)
                    messagebox.showerror("Could not connect", item[1][:500])
                elif kind == "watch_accounts":
                    data = item[1]
                    if isinstance(data, dict) and "error" in data:
                        self._upload_log("WARN", f"Connection check: {data['error']}")
                    else:
                        previous = set(self.connected_accounts)
                        self.connected_accounts = {a["platform"]: a for a in data}
                        self._render_accounts(new=(set(self.connected_accounts) - previous))
                elif kind == "preview_published":
                    sent = item[1]
                    self._upload_log(
                        "OK",
                        f"Published to {', '.join(sent.get('platforms', [])) or 'social media'}"
                        f" — {sent.get('status') or 'started'}",
                    )
                    if self.preview_window is not None:
                        try:
                            self.preview_window.destroy()
                        except Exception:
                            pass
                        self.preview_window = None
                    messagebox.showinfo(
                        "Published",
                        f"Your clip is on its way!\n\n"
                        f"Platforms: {', '.join(sent.get('platforms', []))}\n"
                        f"Status: {sent.get('status') or 'started'}\n\n"
                        f"Check the Publish log for details.",
                    )
                elif kind == "preview_publish_error":
                    if self.preview_window is not None:
                        self.preview_window.status_label.configure(
                            text=f"Upload failed: {item[1][:160]}", text_color=RED)
                        self.preview_window.publish_btn.configure(
                            state="normal", text="PUBLISH THIS CLIP")
                    self._upload_log("ERROR", item[1])
                elif kind == "publish_log":
                    self._upload_log(item[1], item[2])
                elif kind == "publish_progress":
                    self.up_progress.set(item[1] / 100.0)
                elif kind == "publish_done":
                    self.publishing = False
                    self.upload_btn.configure(state="normal", text="Preview & publish")
                    self.up_preview_btn.configure(state="normal", text="Preview & publish clips")
                    self.up_publish_btn.configure(state="normal",
                                                  text="Publish all without preview")
                    self.up_progress.set(1.0)
                    results = item[1]
                    posted = sum(1 for r in results if r.get("request_id"))
                    failed = len(results) - posted
                    self._upload_log("OK", f"Finished: {posted} upload(s) started, {failed} failed.")
                    messagebox.showinfo("Publishing finished",
                                        f"{posted} clip(s) sent to Upload-Post.\n"
                                        f"{failed} failed (see the Publish log).")
                elif kind == "publish_error":
                    self.publishing = False
                    self.upload_btn.configure(state="normal", text="Preview & publish")
                    self.up_preview_btn.configure(state="normal", text="Preview & publish clips")
                    self.up_publish_btn.configure(state="normal",
                                                  text="Publish all without preview")
                    self._upload_log("ERROR", item[1])
                    messagebox.showerror("Publishing failed", item[1][:500])
                elif kind == "done":
                    self.result = item[1]
                    self.running = False
                    self.progress.set(1.0)
                    self.status.configure(text="Finished")
                    self.go_btn.configure(state="normal", text="MAKE VIRAL CLIPS", fg_color=PINK)
                    self.open_btn.configure(state="normal")
                    self.upload_btn.configure(state="normal", text="Preview & publish")
                    self.up_preview_btn.configure(state="normal", text="Preview & publish clips")
                    self.up_publish_btn.configure(state="normal",
                                                  text="Publish all without preview")
                    self._log("OK", f"Finished! {len(self.result['clips'])} clips in {self.result['output_dir']}")
                    messagebox.showinfo(
                        "Done!",
                        f"Created {len(self.result['clips'])} clips!\n\n{self.result['output_dir']}\n\n"
                        "Open the folder to watch and upload them.",
                    )
                    if self.checking or self.upload_checking or self.publishing:
                        self.root.after(80, self._poll)
                    return
                elif kind == "error":
                    self.running = False
                    self.status.configure(text="Failed")
                    self.go_btn.configure(state="normal", text="MAKE VIRAL CLIPS", fg_color=PINK)
                    self._log("ERROR", item[1])
                    messagebox.showerror("Could not finish", item[1][:600])
                    if self.checking or self.upload_checking or self.publishing:
                        self.root.after(80, self._poll)
                    return
        except queue.Empty:
            pass
        if self.running or self.checking or self.upload_checking or self.publishing:
            self.root.after(80, self._poll)


class PublishPreview(ctk.CTkToplevel):
    """A simple, non-technical 'see it before it goes live' window."""

    def __init__(self, app: App, clips: list[dict]):
        super().__init__(app.root)
        self.app = app
        self.clips = clips
        self.index = 0
        self.thumb_image = None
        self.title("Preview before publishing — Viral Clip Make")
        self.geometry("790x760")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.transient(app.root)
        self.grab_set()
        self.lift()
        self._build()
        self._show()

    def _build(self):
        ctk.CTkLabel(self, text="PREVIEW BEFORE PUBLISH",
                     font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
                     text_color=TEXT).pack(pady=(18, 2))
        ctk.CTkLabel(self, text="Check the clip, edit the caption, choose where it goes — then publish.",
                     font=FONT_SMALL, text_color=MUTED).pack(pady=(0, 10))

        main = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16)
        main.pack(fill="both", expand=True, padx=20, pady=6)

        left = ctk.CTkFrame(main, fg_color=CARD2, corner_radius=12)
        left.pack(side="left", fill="y", padx=(14, 10), pady=14)
        self.thumb_label = ctk.CTkLabel(left, text="Loading preview…",
                                        font=FONT_SMALL, text_color=MUTED)
        self.thumb_label.pack(padx=14, pady=14)
        self.file_label = ctk.CTkLabel(left, text="", font=FONT_SMALL,
                                       text_color=MUTED, wraplength=250, justify="left")
        self.file_label.pack(padx=14, pady=(0, 14))

        right = ctk.CTkFrame(main, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(4, 14), pady=14)
        ctk.CTkLabel(right, text="Clip title", font=FONT_TITLE, text_color=TEXT).pack(anchor="w")
        self.title_label = ctk.CTkLabel(right, text="", font=ctk.CTkFont(size=16, weight="bold"),
                                        text_color=TEXT, wraplength=400, justify="left")
        self.title_label.pack(anchor="w", pady=(2, 14))

        ctk.CTkLabel(right, text="Caption (edit it if you like)", font=FONT_TITLE,
                     text_color=TEXT).pack(anchor="w")
        self.caption_box = ctk.CTkTextbox(right, height=170, corner_radius=10,
                                          fg_color="#0b0e14", font=FONT, text_color=TEXT)
        self.caption_box.pack(fill="x", pady=(2, 14))

        ctk.CTkLabel(right, text="Publishing to", font=FONT_TITLE, text_color=TEXT).pack(anchor="w")
        self.dest_label = ctk.CTkLabel(right, text="", font=FONT_SMALL,
                                       text_color=GREEN, wraplength=400, justify="left")
        self.dest_label.pack(anchor="w", pady=(2, 14))
        self.status_label = ctk.CTkLabel(right, text="Nothing has been uploaded yet.",
                                         font=FONT_SMALL, text_color=YELLOW,
                                         wraplength=400, justify="left")
        self.status_label.pack(anchor="w")

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=20, pady=(6, 18))
        self.prev_btn = ctk.CTkButton(bottom, text="← Previous", width=110, height=42,
                                      corner_radius=10, fg_color=CARD2, hover_color="#2c3344",
                                      text_color=TEXT, font=FONT_SMALL, command=self.previous)
        self.prev_btn.pack(side="left")
        self.next_btn = ctk.CTkButton(bottom, text="Next →", width=100, height=42,
                                      corner_radius=10, fg_color=CARD2, hover_color="#2c3344",
                                      text_color=TEXT, font=FONT_SMALL, command=self.next)
        self.next_btn.pack(side="left", padx=(8, 0))
        self.play_btn = ctk.CTkButton(bottom, text="Play full clip", width=130, height=42,
                                      corner_radius=10, fg_color=CARD2, hover_color="#2c3344",
                                      text_color=TEXT, font=FONT_SMALL, command=self.play)
        self.play_btn.pack(side="left", padx=(8, 0))
        self.counter = ctk.CTkLabel(bottom, text="", font=FONT_SMALL, text_color=MUTED)
        self.counter.pack(side="left", padx=(12, 0))
        ctk.CTkButton(bottom, text="Cancel", width=90, height=42, corner_radius=10,
                      fg_color="#33394a", hover_color="#41495d", text_color=TEXT,
                      font=FONT_SMALL, command=self.destroy).pack(side="right", padx=(8, 0))
        self.publish_btn = ctk.CTkButton(
            bottom, text="PUBLISH THIS CLIP", width=190, height=42, corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color="#2f6f5e", hover_color="#3d8a75", text_color=TEXT,
            command=self.publish,
        )
        self.publish_btn.pack(side="right")

    @staticmethod
    def _size(path: Path) -> str:
        try:
            mb = path.stat().st_size / 1024 / 1024
            return f"{mb:.1f} MB"
        except OSError:
            return ""

    def _show(self):
        clip = self.clips[self.index]
        path = Path(clip["path"])
        title = str(clip.get("title") or path.stem)
        self.title_label.configure(text=title)

        suffix = str(self.app.cfg.get("upload.caption_suffix", "") or "").strip()
        caption = f"{title.strip()} {suffix}".strip()
        self.caption_box.delete("1.0", "end")
        self.caption_box.insert("1.0", caption)

        thumb = path.with_suffix(".jpg")
        if thumb.exists():
            try:
                from PIL import Image
                image = Image.open(thumb).convert("RGB")
                factor = 500 / float(image.height)
                dimensions = (max(1, int(image.width * factor)), 500)
                self.thumb_image = ctk.CTkImage(light_image=image, dark_image=image,
                                                size=dimensions)
                self.thumb_label.configure(image=self.thumb_image, text="")
            except Exception:
                self.thumb_label.configure(image=None, text="Preview unavailable")
        else:
            self.thumb_label.configure(image=None, text="No thumbnail saved")
        self.file_label.configure(text=f"{path.name}\n{self._size(path)}")

        destinations = []
        for platform, var in self.app.up_platform_vars.items():
            if var.get():
                handle = self.app.connected_accounts.get(platform, {}).get("handle", "")
                label = self.app.PLATFORMS.get(platform, platform)
                destinations.append(f"  • {label}" + (f"  @{handle}" if handle else ""))
        self.dest_label.configure(text="\n".join(destinations) or
                                  "  • Select platforms in Step 2")

        self.counter.configure(text=f"Clip {self.index + 1} of {len(self.clips)}")
        self.prev_btn.configure(state="normal" if self.index > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.index < len(self.clips) - 1 else "disabled")
        self.status_label.configure(text="Nothing has been uploaded yet.", text_color=YELLOW)
        self.publish_btn.configure(state="normal", text="PUBLISH THIS CLIP")

    def previous(self):
        if self.index > 0:
            self.index -= 1
            self._show()

    def next(self):
        if self.index < len(self.clips) - 1:
            self.index += 1
            self._show()

    def play(self):
        path = Path(self.clips[self.index]["path"])
        if path.exists():
            os.startfile(str(path))  # noqa - Windows

    def publish(self):
        caption = self.caption_box.get("1.0", "end").strip()
        if not caption:
            messagebox.showwarning("Empty caption", "Write a caption before publishing.")
            return
        clip = self.clips[self.index]
        self.publish_btn.configure(state="disabled", text="PUBLISHING…")
        self.status_label.configure(text="Sending to Upload-Post…", text_color=YELLOW)
        threading.Thread(target=self._publish_worker, args=(clip, caption), daemon=True).start()
        self.app.root.after(80, self.app._poll)

    def _publish_worker(self, clip: dict, caption: str):
        try:
            from core.upload_post import poll_status, upload_clip
            sent = upload_clip(Path(clip["path"]), self.app.cfg,
                               str(clip.get("title") or ""), caption=caption)
            if self.app.cfg.get("upload.wait_for_status", True) and sent.get("request_id"):
                final = poll_status(sent["request_id"], self.app.cfg)
                sent["status"] = final.get("status", "")
            self.app.q.put(("preview_published", sent))
        except Exception as exc:
            self.app.q.put(("preview_publish_error", " ".join(str(exc).split())[:400]))
        self.app.root.after(80, self.app._poll)


def launch():
    root = ctk.CTk()
    App(root)
    root.mainloop()
