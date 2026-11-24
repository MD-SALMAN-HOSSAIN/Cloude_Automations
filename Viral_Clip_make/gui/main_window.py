from __future__ import annotations

import os
import queue
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core import cleanup
from core.ai import CLAUDE_DEFAULT_BASE, CLAUDE_DEFAULT_MODEL, GEMINI_DEFAULT_MODEL
from core.config import Config, app_data_dir
from core.layout import DEFAULT_AI_PROMPT
from core.pipeline import run_pipeline
from core.util import Logger, clamp
from gui.completion import CompletionAnimation

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

BG = "#0d0f15"
CARD = "#171b26"
CARD2 = "#1e2331"
BORDER = "#2b3242"
TEXT = "#eef0f6"
MUTED = "#98a1b6"
DIM_MUTED = "#4c515d"  # muted text at ~45% opacity over BG
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
    "Anthropic / Claude API": "claude",
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

FACECAM_CORNERS = {"Auto (detect)": "auto", "Top left": "left", "Top right": "right"}
CORNER_FROM_KEY = {v: k for k, v in FACECAM_CORNERS.items()}

LAYOUT_OPTIONS = ["Single video", "Split-screen gaming (facecam top)"]
LAYOUT_IS_SPLIT = {LAYOUT_OPTIONS[0]: False, LAYOUT_OPTIONS[1]: True}
LAYOUT_LABEL_FROM_KEY = {v: k for k, v in LAYOUT_IS_SPLIT.items()}
CAP_POS_OPTIONS = ["At split line", "Lower third of gameplay"]
CAP_POS_KEYS = {CAP_POS_OPTIONS[0]: "seam", CAP_POS_OPTIONS[1]: "lower_third"}
CAP_POS_LABELS = {v: k for k, v in CAP_POS_KEYS.items()}

DEVICE_LABELS = {"Auto (recommended)": "auto", "CPU only": "cpu", "GPU (NVIDIA)": "cuda"}
DEVICE_FROM_KEY = {v: k for k, v in DEVICE_LABELS.items()}
ENCODER_LABELS = {"Auto (GPU if available)": "auto", "CPU only": "cpu", "GPU only": "gpu"}
ENCODER_FROM_KEY = {v: k for k, v in ENCODER_LABELS.items()}

BROWSER_LABELS = {
    "Auto (try installed browsers)": "auto",
    "None (anonymous only)": "none",
    "Firefox": "firefox",
    "Chrome": "chrome",
    "Edge": "edge",
    "Brave": "brave",
    "Opera": "opera",
    "Vivaldi": "vivaldi",
}
BROWSER_FROM_KEY = {v: k for k, v in BROWSER_LABELS.items()}
CLIENT_LABELS = {
    "Auto (recommended)": "default",
    "TV": "tv",
    "Android": "android",
    "iOS": "ios",
    "Mobile web": "mweb",
    "Web Safari": "web_safari",
}
CLIENT_FROM_KEY = {v: k for k, v in CLIENT_LABELS.items()}

MODES = {
    "fast": {
        "whisper": "tiny", "crf": 26, "preset": "veryfast", "face": False,
        "normalize": False, "face_interval": 1.0, "candidates": 300,
        "download_height": 480,
        "desc": "Fastest — smaller download and speech model, simpler crop, quicker render.",
    },
    "balanced": {
        "whisper": "base", "crf": 20, "preset": "medium", "face": True,
        "normalize": True, "face_interval": 0.5, "candidates": 600,
        "download_height": 720,
        "desc": "Recommended — quick 720p download, tracked framing and clean audio.",
    },
    "best": {
        "whisper": "small", "crf": 16, "preset": "slow", "face": True,
        "normalize": True, "face_interval": 0.35, "candidates": 900,
        "download_height": 1080,
        "desc": "Highest quality — 1080p source, more accurate words, slower render.",
    },
}

# One-click "best settings for going viral".  This is saved as the user's
# default so every future creation starts from the same strong setup.
VIRAL_PRESET = {
    "clips": 10,
    "min_seconds": 10,
    "max_seconds": 25,
    "language": "",
    "mode": "Best",
    "whisper": "small",
    "aspect": "9:16",
    "framing": "AI picks best (recommended)",
    "layout": "Split-screen gaming (facecam top)",
    "download_height": 1080,
    "crf": 16,
    "render_preset": "slow",
    "face_interval": 0.35,
    "candidates": 900,
    "auto_style": True,
}

class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.cfg = Config()
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.checking = False
        self.result = None
        self._completion = None
        _init_fonts()
        self._build()
        self._cleanup_startup()

    def _cleanup_startup(self):
        """Remove leftovers from a run that was closed or crashed mid-way."""
        try:
            freed = cleanup.purge_all(self.cfg)
            if freed > 0:
                self._log("INFO",
                          f"Auto-clean removed {freed / 1e6:.1f} MB of old temp/partial files")
        except Exception as exc:
            self._log("WARN", f"Startup cleanup skipped: {exc}")

    def apply_viral_preset(self):
        """Apply and save the one-click settings that give clips the best reach."""
        self.clips_var.set(str(VIRAL_PRESET["clips"]))
        self.min_var.set(str(VIRAL_PRESET["min_seconds"]))
        self.max_var.set(str(VIRAL_PRESET["max_seconds"]))
        self.lang_var.set(VIRAL_PRESET["language"])
        self.mode_var.set(VIRAL_PRESET["mode"])
        self._on_mode()
        self.whisper_var.set(VIRAL_PRESET["whisper"])
        self.face_var.set(True)
        self.cap_var.set(True)
        self.hook_var.set(True)
        self.norm_var.set(True)
        if hasattr(self, "facecam_var"):
            self.facecam_var.set(True)
        if hasattr(self, "layout_var"):
            self.layout_var.set(VIRAL_PRESET["layout"])
            self._on_layout()
        self.framing_var.set(VIRAL_PRESET["framing"])
        self._on_framing()
        self.aspect_var.set(VIRAL_PRESET["aspect"])
        self._on_aspect()
        self.cw_var.set("1080")
        self.ch_var.set("1920")
        if hasattr(self, "device_var"):
            self.device_var.set("Auto (recommended)")
            self._on_device()
        if hasattr(self, "encoder_var"):
            self.encoder_var.set("Auto (GPU if available)")
            self._on_encoder()

        self._save_settings()
        # The render-mode handler keeps crf/preset/face timing in sync; these
        # extras make the preset complete on both app variants.
        self.cfg.update({
            "general.download_height": VIRAL_PRESET["download_height"],
            "video.crf": VIRAL_PRESET["crf"],
            "video.preset": VIRAL_PRESET["render_preset"],
            "speed.face_interval": VIRAL_PRESET["face_interval"],
            "speed.candidates": VIRAL_PRESET["candidates"],
            "ai.auto_style": VIRAL_PRESET["auto_style"],
            "video.aspect": VIRAL_PRESET["aspect"],
            "video.custom_width": 1080,
            "video.custom_height": 1920,
            "video.framing": FRAMING_LABELS[VIRAL_PRESET["framing"]],
            "features.face_track": True,
            "features.captions": True,
            "features.hook": True,
            "features.normalize_audio": True,
        })
        self.cfg.save()

        self._log("OK", "BEST FOR VIRAL preset saved as default: 9:16, 1080p, "
                        "split-screen facecam + gameplay, captions + hook, character lock, 10 clips.")
        if getattr(self, "saved_label", None) is not None:
            self.saved_label.configure(text="✓ Viral preset is now the default")
            self.root.after(2800, lambda: self.saved_label.configure(text=""))
        if getattr(self, "signal_defaults", None) is not None:
            self.signal_defaults.configure(text="Viral preset", text_color=GREEN)

    def _play_completion_animation(self, count: int, output_dir: str):
        """Celebrate once, after the clipping task has finished."""
        try:
            if self._completion is not None:
                self._completion.close()
            self._completion = CompletionAnimation(
                self.root, count, output_dir,
                callback=lambda: setattr(self, "_completion", None),
            )
        except Exception:
            messagebox.showinfo(
                "Done!",
                f"Created {count} clips!\n\n{output_dir}\n\nOpen the folder to watch your clips.",
            )

    def _build(self):
        self.root.title("Viral Clip Make — Modern Free Shorts Maker")
        self.root.geometry("1240x900")
        self.root.minsize(1120, 820)
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

        self._make_tab()
        self._advanced_tab()
        self._load_advanced()

    def _header(self):
        canvas = self.header_canvas = ctk.CTkCanvas(
            self.root, height=94, bg=BG, highlightthickness=0, bd=0)
        canvas.pack(fill="x")
        # subtle gradient glow behind the title
        for i in range(110):
            ratio = i / 109
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
        for i in range(210):
            ratio = i / 209
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

        # Modern two-column workspace: controls on the left, live activity on
        # the right.  The left side scrolls on smaller screens so nothing is
        # hidden behind the log panel.
        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=(4, 2), pady=(8, 4))
        left = ctk.CTkScrollableFrame(body, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right = ctk.CTkFrame(body, fg_color="transparent", width=360)
        right.pack(side="right", fill="y", padx=(12, 2))

        src = self._card(left, "VIDEO SOURCE")
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

        settings = self._card(left, "QUICK SETTINGS")
        grid = ctk.CTkFrame(settings, fg_color="transparent")
        grid.pack(fill="x", padx=18, pady=(4, 8))

        preset_row = ctk.CTkFrame(settings, corner_radius=12, fg_color=CARD2)
        preset_row.pack(fill="x", padx=18, pady=(0, 8))
        self.viral_btn = ctk.CTkButton(
            preset_row, text="⚡ BEST SETTINGS FOR VIRAL", width=210, height=38,
            corner_radius=10,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color=PURPLE, hover_color="#9178ff", text_color=TEXT,
            command=self.apply_viral_preset,
        )
        self.viral_btn.pack(side="left", padx=(12, 12), pady=8)
        ctk.CTkLabel(
            preset_row,
            text=("One click: 9:16 · 1080p · 10 clips · captions + hook · character lock · "
                  "AI framing. Saved as your default."),
            font=FONT_SMALL, text_color=MUTED, wraplength=390, justify="left",
        ).pack(side="left", pady=8)

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

        help_row = ctk.CTkFrame(settings, corner_radius=10, fg_color=CARD2)
        help_row.pack(fill="x", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            help_row,
            text=("Speech model: tiny (fastest) → base (balanced) → small / medium (more accurate, slower). "
                  "They run on your PC and download only once.\n"
                  "Language: leave blank for automatic detection, or type a code like en, hi, es, fr, de, ar."),
            font=FONT_SMALL, text_color=MUTED, wraplength=620, justify="left",
        ).pack(side="left", padx=(14, 6), pady=8)
        ctk.CTkButton(help_row, text="?", width=34, height=34, corner_radius=9,
                      fg_color=PURPLE, hover_color="#9178ff", text_color=TEXT,
                      font=FONT_SMALL, command=self.show_speech_help).pack(side="right", padx=10)

        frame_row = ctk.CTkFrame(settings, fg_color="transparent")
        frame_row.pack(fill="x", padx=18, pady=(0, 4))
        frame_header = ctk.CTkFrame(frame_row, fg_color="transparent")
        frame_header.pack(fill="x")
        ctk.CTkLabel(frame_header, text="Video framing", font=FONT_SMALL,
                     text_color=MUTED).pack(side="left")
        self.framing_off_badge = ctk.CTkLabel(
            frame_header, text="Off — using Layout template only", font=FONT_SMALL,
            text_color=MUTED, fg_color=CARD2, corner_radius=8, padx=8, pady=2)
        self.framing_toggle_var = ctk.BooleanVar(
            value=bool(self.cfg.get("video.framing_enabled", True)))
        ctk.CTkSwitch(
            frame_header, text="", variable=self.framing_toggle_var,
            onvalue=True, offvalue=False, command=self._on_framing_toggle,
            width=52, switch_width=40, switch_height=22,
            progress_color=PINK, fg_color="#33394a",
            button_color=TEXT, button_hover_color="#ffffff",
        ).pack(side="right")
        self.framing_var = ctk.StringVar(value=FRAMING_FROM_KEY.get(
            str(self.cfg.get("video.framing", "auto")), "AI picks best (recommended)"))
        self.framing_segments = ctk.CTkSegmentedButton(
            frame_row, values=list(FRAMING_LABELS), variable=self.framing_var,
            height=36, corner_radius=10, font=FONT_SMALL,
            selected_color=PINK, selected_hover_color=PINK_HOVER,
            unselected_color=CARD2, unselected_hover_color="#2c3344",
            text_color=TEXT, command=self._on_framing,
        )
        self.framing_segments.pack(fill="x", pady=(3, 3))
        self.framing_desc = ctk.CTkLabel(
            frame_row,
            text="Every clip is cropped to keep the game character as the main subject. "
                 "AI (when configured) or the free local tracker finds the character per moment; "
                 "whole-frame looks are never used because they leave the character tiny.",
            font=FONT_SMALL, text_color=MUTED, wraplength=650, justify="left",
        )
        self.framing_desc.pack(anchor="w")
        self._apply_framing_toggle_state(bool(self.framing_toggle_var.get()))

        layout_row = ctk.CTkFrame(settings, fg_color="transparent")
        layout_row.pack(fill="x", padx=18, pady=(6, 2))
        ctk.CTkLabel(layout_row, text="Layout template", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w")
        self.layout_var = ctk.StringVar(value=LAYOUT_LABEL_FROM_KEY.get(
            bool(self.cfg.get("features.split_layout", False)), LAYOUT_OPTIONS[0]))
        ctk.CTkSegmentedButton(
            layout_row, values=LAYOUT_OPTIONS, variable=self.layout_var,
            height=36, corner_radius=10, font=FONT_SMALL,
            selected_color=PURPLE, selected_hover_color="#9178ff",
            unselected_color=CARD2, unselected_hover_color="#2c3344",
            text_color=TEXT, command=self._on_layout,
        ).pack(fill="x", pady=(3, 0))

        # Extra controls only appear for the split-screen template.
        self.split_row = ctk.CTkFrame(settings, corner_radius=10, fg_color=CARD2)
        line1 = ctk.CTkFrame(self.split_row, fg_color="transparent")
        line1.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(line1, text="Facecam height", font=FONT_SMALL,
                     text_color=MUTED).pack(side="left")
        self.fc_ratio_var = ctk.StringVar(value=str(int(round(
            float(self.cfg.get("video.split_facecam_ratio", 0.33)) * 100))))
        ctk.CTkEntry(line1, textvariable=self.fc_ratio_var, width=52, height=30,
                     corner_radius=8, font=FONT_SMALL).pack(side="left", padx=(8, 14))
        ctk.CTkLabel(line1, text="%   Gameplay height", font=FONT_SMALL,
                     text_color=MUTED).pack(side="left")
        self.gp_ratio_var = ctk.StringVar(value=str(int(round(
            float(self.cfg.get("video.split_gameplay_ratio", 0.57)) * 100))))
        ctk.CTkEntry(line1, textvariable=self.gp_ratio_var, width=52, height=30,
                     corner_radius=8, font=FONT_SMALL).pack(side="left", padx=(8, 14))
        ctk.CTkLabel(line1, text="%   Captions", font=FONT_SMALL,
                     text_color=MUTED).pack(side="left")
        self.cap_pos_var = ctk.StringVar(value=CAP_POS_LABELS.get(
            str(self.cfg.get("style.caption_position", "seam") or "seam"),
            CAP_POS_OPTIONS[0]))
        ctk.CTkComboBox(line1, variable=self.cap_pos_var, values=CAP_POS_OPTIONS,
                        width=180, height=30, corner_radius=8, state="readonly",
                        font=FONT_SMALL, dropdown_font=FONT_SMALL).pack(side="left", padx=(8, 0))

        line2 = ctk.CTkFrame(self.split_row, fg_color="transparent")
        line2.pack(fill="x", padx=12, pady=(2, 8))
        ctk.CTkLabel(line2, text="Channel handle", font=FONT_SMALL,
                     text_color=MUTED).pack(side="left")
        self.watermark_var = ctk.StringVar(
            value=str(self.cfg.get("style.watermark", "") or ""))
        ctk.CTkEntry(line2, textvariable=self.watermark_var, width=230, height=30,
                     corner_radius=8, font=FONT_SMALL,
                     placeholder_text="twitch.tv/yourname").pack(side="left", padx=(8, 10))
        ctk.CTkLabel(line2, text=(
            "Facecam on top, tracked gameplay below, blurred safety bars. "
            "Captions sit at the split (or lower third); the handle goes top-left of gameplay."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=250, justify="left").pack(
            side="left")

        out_row = self.out_row = ctk.CTkFrame(settings, fg_color="transparent")
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
        self.facecam_var = ctk.BooleanVar(value=bool(self.cfg.get("features.facecam_pip", False)))
        for text, var in (("Character lock", self.face_var), ("Captions", self.cap_var),
                          ("Hook overlay", self.hook_var), ("Normalize audio", self.norm_var),
                          ("Facecam", self.facecam_var)):
            switch = ctk.CTkSwitch(switches, text=text, variable=var, onvalue=True, offvalue=False,
                                   font=FONT_SMALL, progress_color=PINK,
                                   fg_color="#33394a")
            switch.pack(side="left", padx=(0, 18))
            if text == "Character lock":
                self.face_switch = switch

        fc_row = ctk.CTkFrame(settings, fg_color="transparent")
        fc_row.pack(fill="x", padx=18, pady=(0, 8))
        ctk.CTkLabel(fc_row, text="Facecam corner", font=FONT_SMALL,
                     text_color=MUTED).pack(side="left", padx=(0, 8))
        self.fc_corner_var = ctk.StringVar(value=CORNER_FROM_KEY.get(
            str(self.cfg.get("video.facecam_corner", "auto")), "Auto (detect)"))
        ctk.CTkComboBox(fc_row, variable=self.fc_corner_var,
                        values=list(FACECAM_CORNERS), width=150, height=32,
                        corner_radius=9, state="readonly", font=FONT_SMALL,
                        dropdown_font=FONT_SMALL).pack(side="left")
        ctk.CTkLabel(fc_row, text=(
            "Split-screen: the detected face fills the top panel. Bubble layout: a corner "
            "webcam becomes a circle while the game stays centred. Auto finds the corner."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=330,
            justify="left").pack(side="left", padx=(14, 0))
        settings.pack(fill="x", pady=(0, 10))

        self.go_btn = ctk.CTkButton(
            left, text="MAKE VIRAL CLIPS", height=54, corner_radius=14,
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
            fg_color=PINK, hover_color=PINK_HOVER, command=self.start,
        )
        self.go_btn.pack(fill="x", pady=(2, 12))

        prog = ctk.CTkFrame(left, fg_color="transparent")
        prog.pack(fill="x", pady=(0, 8))
        self.progress = ctk.CTkProgressBar(prog, height=10, corner_radius=6,
                                           fg_color="#202634", progress_color=PINK)
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.status = ctk.CTkLabel(prog, text="Ready", width=220, anchor="e",
                                   font=FONT_SMALL, text_color=MUTED)
        self.status.pack(side="right")

        # Right rail — live activity log plus a compact session readout.
        logcard = ctk.CTkFrame(right, corner_radius=18, fg_color="#0b0e14",
                               border_width=1, border_color=BORDER)
        logcard.pack(fill="both", expand=True)
        log_head = ctk.CTkFrame(logcard, fg_color="transparent")
        log_head.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(log_head, text="ACTIVITY", font=FONT_TITLE,
                     text_color=PURPLE).pack(side="left")
        self.log_status = ctk.CTkLabel(log_head, text="● LIVE",
                                       font=FONT_SMALL, text_color=GREEN)
        self.log_status.pack(side="right")
        ctk.CTkLabel(logcard, text="Render events stream here in real time.",
                     font=FONT_SMALL, text_color=MUTED, wraplength=290,
                     justify="left").pack(anchor="w", padx=16, pady=(0, 8))
        self.logbox = ctk.CTkTextbox(logcard, corner_radius=12, font=FONT_MONO,
                                     fg_color="#080a0f", text_color=TEXT)
        self.logbox.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        for tag, color in (("INFO", MUTED), ("OK", GREEN), ("WARN", YELLOW),
                           ("ERROR", RED), ("HEAD", PURPLE)):
            self.logbox.tag_config(tag, foreground=color)

        signal = ctk.CTkFrame(right, corner_radius=16, fg_color=CARD,
                              border_width=1, border_color=BORDER)
        signal.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(signal, text="SESSION", font=FONT_TITLE,
                     text_color=PINK).pack(anchor="w", padx=16, pady=(12, 6))
        self.signal_defaults = None
        for caption, value, color in (
            ("Engine", "Local · private", GREEN),
            ("Output", "9:16 ready", PURPLE),
            ("Defaults", "Balanced", MUTED),
        ):
            line = ctk.CTkFrame(signal, fg_color="transparent")
            line.pack(fill="x", padx=16, pady=3)
            ctk.CTkLabel(line, text=caption, font=FONT_SMALL,
                         text_color=MUTED, width=72, anchor="w").pack(side="left")
            value_label = ctk.CTkLabel(line, text=value, font=FONT_SMALL,
                                       text_color=color, anchor="w")
            value_label.pack(side="left")
            if caption == "Defaults":
                self.signal_defaults = value_label

        bottom = ctk.CTkFrame(left, fg_color="transparent")
        bottom.pack(fill="x", pady=(10, 0))
        self.open_btn = ctk.CTkButton(bottom, text="Open output folder", height=38,
                                      corner_radius=10, fg_color=CARD2,
                                      hover_color="#2c3344", text_color=TEXT,
                                      state="disabled", command=self.open_output)
        self.open_btn.pack(side="left")
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
        self.claude_url_var = ctk.StringVar(value=str(
            self.cfg.get("ai.claude_base_url", CLAUDE_DEFAULT_BASE) or CLAUDE_DEFAULT_BASE))
        self.ai_model_var = ctk.StringVar(value=str(self.cfg.get("ai.model", "")))

        self.key_label = ctk.CTkLabel(ai, text="API key", font=FONT_SMALL, text_color=MUTED)
        self.key_label.pack(anchor="w", padx=18, pady=(0, 2))
        ctk.CTkEntry(ai, textvariable=self.key_var, height=40, corner_radius=10,
                     placeholder_text="Paste your key (stored only on this PC)",
                     font=FONT, show="•").pack(fill="x", padx=18, pady=(0, 10))

        self.base_row = ctk.CTkFrame(ai, fg_color="transparent")
        ctk.CTkLabel(self.base_row, text="Base URL (OpenAI / Ollama / OpenRouter)",
                     font=FONT_SMALL, text_color=MUTED).pack(anchor="w")
        ctk.CTkEntry(self.base_row, textvariable=self.base_var, height=40,
                     corner_radius=10, placeholder_text="http://localhost:11434/v1",
                     font=FONT).pack(fill="x", pady=(2, 0))
        self.base_row.pack(fill="x", padx=18, pady=(0, 10))

        self.claude_row = ctk.CTkFrame(ai, fg_color="transparent")
        ctk.CTkLabel(self.claude_row, text="Claude API URL",
                     font=FONT_SMALL, text_color=MUTED).pack(anchor="w")
        ctk.CTkEntry(self.claude_row, textvariable=self.claude_url_var, height=40,
                     corner_radius=10, placeholder_text=CLAUDE_DEFAULT_BASE,
                     font=FONT).pack(fill="x", pady=(2, 0))
        # Packed only when the Claude provider is selected (see _on_provider).

        self.model_label = ctk.CTkLabel(ai, text="Model", font=FONT_SMALL, text_color=MUTED)
        self.model_label.pack(anchor="w", padx=18, pady=(0, 2))
        ctk.CTkEntry(ai, textvariable=self.ai_model_var, height=40, corner_radius=10,
                     placeholder_text="claude-sonnet-5-5 / gemini-3.5-flash-lite / llama3.1",
                     font=FONT).pack(fill="x", padx=18, pady=(0, 10))

        ctk.CTkLabel(ai, text=(
            "Claude: paste a key from console.anthropic.com (starts with sk-ant-) and leave the "
            "Claude URL at its default.  ·  Gemini: free key from aistudio.google.com.  ·  "
            "OpenAI / Ollama / OpenRouter: use the Base URL above. If an API fails, the app falls "
            "back to free local scoring."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=880,
            justify="left").pack(anchor="w", padx=18, pady=(2, 10))

        prompt_card = self._card(scroll, "CLIP INSTRUCTIONS / AI PROMPT")
        self.prompt_box = ctk.CTkTextbox(prompt_card, height=110, corner_radius=10,
                                         font=FONT_SMALL)
        self.prompt_box.pack(fill="x", padx=18, pady=(2, 6))
        self.prompt_box.insert("1.0", str(
            self.cfg.get("ai.custom_prompt", DEFAULT_AI_PROMPT) or DEFAULT_AI_PROMPT))
        ctk.CTkLabel(prompt_card, text=(
            "Sent to the AI when a provider is configured: pick high-energy reactions with a hook "
            "in the first 2 seconds, then render them in the 9:16 split-screen gaming layout with "
            "bold captions and yellow action words. Saved as your default."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=880,
            justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        prompt_card.pack(fill="x", pady=(0, 12))

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

        # Only pasting/editing the KEY may auto-switch the provider. Editing the
        # model or URL must never undo a provider the user chose by hand.
        self.key_var.trace_add("write", lambda *_: self._on_ai_field_change())
        for var in (self.base_var, self.claude_url_var, self.ai_model_var):
            var.trace_add("write", lambda *_: self._reset_connection())
        ai.pack(fill="x", pady=(0, 12))

        yt = self._card(scroll, "YOUTUBE DOWNLOAD — ONLY NEEDED IF YOUTUBE BLOCKS YOU")
        self.yt_browser_var = ctk.StringVar(value=BROWSER_FROM_KEY.get(
            str(self.cfg.get("download.cookies_from_browser", "auto")),
            "Auto (try installed browsers)"))
        self.yt_cookie_var = ctk.StringVar(value=str(self.cfg.get("download.cookies_file", "") or ""))
        self.yt_client_var = ctk.StringVar(value=CLIENT_FROM_KEY.get(
            str(self.cfg.get("download.player_client", "default")), "Auto (recommended)"))

        ctk.CTkLabel(yt, text="Browser cookies", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(4, 2))
        ctk.CTkComboBox(yt, variable=self.yt_browser_var, values=list(BROWSER_LABELS),
                        width=430, height=40, corner_radius=10, state="readonly",
                        font=FONT, dropdown_font=FONT).pack(anchor="w", padx=18, pady=(0, 10))

        ctk.CTkLabel(yt, text="Or a cookies.txt file (optional)", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 2))
        cookie_row = ctk.CTkFrame(yt, fg_color="transparent")
        cookie_row.pack(fill="x", padx=18, pady=(0, 8))
        ctk.CTkEntry(cookie_row, textvariable=self.yt_cookie_var, height=40,
                     corner_radius=10, placeholder_text=r"C:\path\to\cookies.txt",
                     font=FONT).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(cookie_row, text="Browse", width=90, height=40, corner_radius=10,
                      fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
                      font=FONT_SMALL, command=self.browse_cookies).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(yt, text="Player client", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 2))
        ctk.CTkComboBox(yt, variable=self.yt_client_var, values=list(CLIENT_LABELS),
                        width=430, height=40, corner_radius=10, state="readonly",
                        font=FONT, dropdown_font=FONT).pack(anchor="w", padx=18, pady=(0, 10))

        ctk.CTkLabel(yt, text=(
            "If you see \"Sign in to confirm you're not a bot\", choose the browser where you are "
            "signed in to YouTube (Firefox works best on Windows). Auto tries Firefox, Chrome and "
            "Edge for you. Cookies are read locally and never uploaded. A cookies.txt file works too."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=880, justify="left").pack(
            anchor="w", padx=18, pady=(0, 10))
        ctk.CTkButton(yt, text="Clean temp files now", height=34, corner_radius=10,
                      fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
                      font=FONT_SMALL, command=self.clean_temp_clicked).pack(
            anchor="w", padx=18, pady=(0, 4))
        ctk.CTkLabel(yt, text=(
            "Partial downloads, unfinished clips and dead caches are deleted automatically when a "
            "run fails and every time the app starts. Finished videos and healthy caches are kept."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=880, justify="left").pack(
            anchor="w", padx=18, pady=(0, 14))
        yt.pack(fill="x", pady=(0, 12))

        hw = self._card(scroll, "HARDWARE — USE YOUR GPU OR CPU")
        self.hw_info = ctk.CTkLabel(hw, text="Detecting hardware…", font=FONT_SMALL,
                                     text_color=YELLOW, wraplength=880, justify="left")
        self.hw_info.pack(anchor="w", padx=18, pady=(2, 6))

        ctk.CTkLabel(hw, text="Speech-to-text device", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w", padx=18)
        self.device_var = ctk.StringVar(value=DEVICE_FROM_KEY.get(
            str(self.cfg.get("whisper.device", "auto")), "Auto (recommended)"))
        ctk.CTkSegmentedButton(
            hw, values=list(DEVICE_LABELS), variable=self.device_var, height=38,
            corner_radius=10, font=FONT_SMALL,
            selected_color=PURPLE, selected_hover_color="#9178ff",
            unselected_color=CARD2, unselected_hover_color="#2c3344",
            text_color=TEXT, command=self._on_device,
        ).pack(fill="x", padx=18, pady=(3, 10))

        ctk.CTkLabel(hw, text="Video encoder", font=FONT_SMALL,
                     text_color=MUTED).pack(anchor="w", padx=18)
        self.encoder_var = ctk.StringVar(value=ENCODER_FROM_KEY.get(
            str(self.cfg.get("render.encoder", "auto")), "Auto (GPU if available)"))
        ctk.CTkSegmentedButton(
            hw, values=list(ENCODER_LABELS), variable=self.encoder_var, height=38,
            corner_radius=10, font=FONT_SMALL,
            selected_color=PURPLE, selected_hover_color="#9178ff",
            unselected_color=CARD2, unselected_hover_color="#2c3344",
            text_color=TEXT, command=self._on_encoder,
        ).pack(fill="x", padx=18, pady=(3, 8))

        ctk.CTkLabel(hw, text=(
            "GPU means an NVIDIA graphics card (CUDA). It makes speech recognition and video "
            "rendering much faster. Auto uses the GPU when present and the CPU otherwise — "
            "there is always a free CPU fallback."
        ), font=FONT_SMALL, text_color=MUTED, wraplength=880, justify="left").pack(
            anchor="w", padx=18, pady=(0, 6))
        ctk.CTkButton(hw, text="What is GPU / CPU?", width=170, height=32, corner_radius=9,
                      fg_color=CARD2, hover_color="#2c3344", text_color=TEXT,
                      font=FONT_SMALL, command=self.show_gpu_help).pack(anchor="w", padx=18, pady=(0, 14))
        hw.pack(fill="x", pady=(0, 12))

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
    # ------------------------------------------------------------------
    def _load_advanced(self):
        self._on_provider()
        self._on_aspect()
        self._on_mode()
        self._on_framing()
        self._on_layout()
        self._on_device()
        self._on_encoder()
        self.root.after(150, self._probe_hardware)

    def show_speech_help(self):
        messagebox.showinfo(
            "Speech model & language — simple guide",
            "SPEECH MODEL (runs 100% on your PC — nothing is uploaded)\n\n"
            "• tiny — ~75 MB, fastest. Good for clear speech; misses some words with accents/noise.\n"
            "• base — ~150 MB, default. Good balance of speed and accuracy for most videos.\n"
            "• small — ~500 MB, noticeably better with accents, noise and multiple languages.\n"
            "• medium — ~1.5 GB, most accurate but slow on a CPU.\n\n"
            "The first time you pick a model it downloads once, then it works offline. "
            "The model only reads the audio to find the exciting moments and word timings "
            "for captions — it never sends your video anywhere.\n\n"
            "LANGUAGE\n\n"
            "Leave the box empty (auto) and Whisper detects the language itself — recommended.\n"
            "If you know it, type a two-letter code to improve accuracy: "
            "en (English), hi (Hindi), es (Spanish), fr (French), de (German), "
            "pt (Portuguese), ar (Arabic), ur (Urdu), ja (Japanese), ko (Korean), zh (Chinese).\n"
            "A wrong code makes results worse, so when unsure leave it blank.",
        )

    def show_gpu_help(self):
        messagebox.showinfo(
            "GPU vs CPU — simple guide",
            "CPU = the processor every computer has. It always works, just slower.\n\n"
            "GPU = the graphics card. An NVIDIA card (CUDA) can make speech-to-text and "
            "video encoding several times faster.\n\n"
            "• Auto (recommended): use the GPU if the app detects one, otherwise CPU.\n"
            "• CPU only: force the processor (useful if a GPU causes problems).\n"
            "• GPU only: prefer the graphics card; the app falls back to CPU automatically "
            "if the GPU cannot do the job.\n\n"
            "AMD / Intel graphics are not used for AI here; they work fine through the CPU path. "
            "Nothing needs installing — NVIDIA drivers are all that is required.",
        )

    def _on_framing_toggle(self, *_):
        """Video framing off = the Layout template is the only layout control."""
        enabled = bool(self.framing_toggle_var.get())
        self.cfg.set("video.framing_enabled", enabled)
        self._apply_framing_toggle_state(enabled)

    def _apply_framing_toggle_state(self, enabled: bool):
        """Dim and disable the framing choices, or restore them (no-op safe)."""
        segments = getattr(self, "framing_segments", None)
        if segments is not None:
            try:
                segments.configure(
                    state="normal" if enabled else "disabled",
                    selected_color=PINK if enabled else DIM_MUTED,
                    selected_hover_color=PINK_HOVER if enabled else DIM_MUTED,
                    unselected_color=CARD2 if enabled else "#171b26",
                    unselected_hover_color="#2c3344" if enabled else "#171b26",
                    text_color=TEXT if enabled else DIM_MUTED,
                )
            except Exception:
                # Older CustomTkinter builds reject some colour keywords; the
                # state change alone still gives pointer-events: none.
                try:
                    segments.configure(state="normal" if enabled else "disabled")
                except Exception:
                    pass
        desc = getattr(self, "framing_desc", None)
        if desc is not None:
            desc.configure(text_color=MUTED if enabled else DIM_MUTED)
        badge = getattr(self, "framing_off_badge", None)
        if badge is not None:
            if enabled:
                if badge.winfo_manager():
                    badge.pack_forget()
            elif not badge.winfo_manager():
                badge.pack(side="left", padx=(8, 0))

    def _on_framing(self, _value=None):
        framing = FRAMING_LABELS.get(self.framing_var.get(), "auto")
        self.cfg.set("video.framing", framing)
        # Character tracking stays on for every framing mode: it is what keeps
        # the game character as the main subject.

    def _on_layout(self, _value=None):
        split = bool(LAYOUT_IS_SPLIT.get(self.layout_var.get(), False))
        self.cfg.set("features.split_layout", split)
        if split and hasattr(self, "facecam_var"):
            self.facecam_var.set(True)  # the top panel needs a detected face
        row = getattr(self, "split_row", None)
        if row is not None:
            if split:
                row.pack(fill="x", padx=18, pady=(2, 6), before=self.out_row)
            else:
                row.pack_forget()

    def _on_device(self, _value=None):
        choice = DEVICE_LABELS.get(self.device_var.get(), "auto")
        self.cfg.set("whisper.device", choice)
        self.cfg.set("whisper.compute_type",
                     "float16" if choice == "cuda" else ("auto" if choice == "auto" else "int8"))

    def _on_encoder(self, _value=None):
        self.cfg.set("render.encoder", ENCODER_LABELS.get(self.encoder_var.get(), "auto"))

    def _probe_hardware(self):
        from core import hardware

        info = hardware.describe()
        gpu = info["name"] or "none detected"
        encoders = ", ".join(info["encoders"]) or "none"
        speech_note = ""
        if info["cuda_detected"] and not info["cuda"]:
            speech_note = " — GPU found, but CUDA runtime missing; CPU is used for speech"
        self.hw_info.configure(
            text=(f"Detected: {gpu}\n"
                  f"Speech: {info['whisper_device'].upper()} ({info['compute']}){speech_note}\n"
                  f"Video: {info['encoder']}  ·  Hardware encoders available: {encoders}"),
            text_color=GREEN if info["cuda"] or info["encoders"] else MUTED,
        )

    def _on_provider(self, _value=None):
        provider = PROVIDERS.get(self.provider_var.get(), "local")
        self.key_label.configure(text="Claude API key" if provider == "claude" else "API key")

        # Only show the URL field that is relevant to the chosen provider.
        self.base_row.pack_forget()
        self.claude_row.pack_forget()
        if provider == "claude":
            self.claude_row.pack(fill="x", padx=18, pady=(0, 10), before=self.model_label)
        else:
            self.base_row.pack(fill="x", padx=18, pady=(0, 10), before=self.model_label)

        if provider == "claude":
            if not self.claude_url_var.get().strip():
                self.claude_url_var.set(CLAUDE_DEFAULT_BASE)
            current = self.ai_model_var.get().strip()
            if not current or current.startswith(("gemini-", "llama", "gpt-", "openai/", "mistral")):
                self.ai_model_var.set(CLAUDE_DEFAULT_MODEL)
        elif provider == "gemini":
            current = self.ai_model_var.get().strip()
            if not current or current.startswith(("claude-", "llama", "gpt", "openai/", "mistral")):
                self.ai_model_var.set(GEMINI_DEFAULT_MODEL)
        elif provider == "openai":
            if not self.base_var.get() or "localhost" in self.base_var.get():
                if not self.base_var.get():
                    self.base_var.set("http://localhost:11434/v1")
            if not self.ai_model_var.get() or self.ai_model_var.get().startswith(("gemini", "claude-")):
                self.ai_model_var.set("llama3.1")

    def _on_aspect(self, _value=None):
        custom = self.aspect_var.get() == "Custom"
        state = "normal" if custom else "disabled"
        self.cw_entry.configure(state=state)
        self.ch_entry.configure(state=state)
        if not custom and self.aspect_var.get() in ASPECT_SIZES:
            w, h = ASPECT_SIZES[self.aspect_var.get()]
            self.cw_var.set(str(w))
            self.ch_var.set(str(h))

    def _sync_mode_settings(self):
        """Apply mode-only render settings without overwriting manual toggles."""
        preset = MODES.get(MODE_LABELS.get(self.mode_var.get(), "balanced"), MODES["balanced"])
        self.cfg.set("video.crf", preset["crf"])
        self.cfg.set("video.preset", preset["preset"])
        self.cfg.set("general.download_height", preset["download_height"])
        self.cfg.set("speed.face_interval", preset["face_interval"])
        self.cfg.set("speed.candidates", preset["candidates"])
        return preset

    def _on_mode(self, _value=None):
        preset = self._sync_mode_settings()
        self.whisper_var.set(preset["whisper"])
        self.face_var.set(preset["face"])
        self.norm_var.set(preset["normalize"])
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
        elif key.startswith("sk-ant-"):
            if self.provider_var.get() != "Anthropic / Claude API":
                self.provider_var.set("Anthropic / Claude API")
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
        # Keep a small disk log so a failed run can be diagnosed after the app
        # is closed (the on-screen box disappears with the window).
        try:
            path = app_data_dir() / "run.log"
            if path.exists() and path.stat().st_size > 512_000:
                path.write_text("", encoding="utf-8")
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {message}\n")
        except Exception:
            pass

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Choose a video file",
            filetypes=[("Video files", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v"), ("All files", "*.*")],
        )
        if path:
            self.source_var.set(path)

    def browse_cookies(self):
        path = filedialog.askopenfilename(
            title="Choose a Netscape cookies.txt file",
            filetypes=[("Cookies file", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.yt_cookie_var.set(path)

    def clean_temp_clicked(self):
        try:
            freed = cleanup.purge_all(self.cfg, log=Logger(lambda lvl, msg: self._log(lvl, msg)))
            messagebox.showinfo(
                "Cleanup done",
                f"Removed {freed / 1e6:.1f} MB of partial downloads, unfinished clips "
                "and stale cache files.\nFinished clips were not touched.",
            )
        except Exception as exc:
            messagebox.showerror("Cleanup failed", str(exc)[:400])

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

    def _pct(self, var, default: float) -> float:
        try:
            return clamp(float(str(var.get()).strip().rstrip("%")), 20.0, 70.0)
        except (ValueError, TypeError):
            return float(default)

    def _save_settings(self):
        self._sync_mode_settings()
        self.cfg.update({
            "general.clips_per_video": self._int(self.clips_var, 5),
            "general.min_seconds": self._int(self.min_var, 12),
            "general.max_seconds": self._int(self.max_var, 45),
            "general.language": self.lang_var.get().strip(),
            "general.output_dir": self.out_var.get().strip(),
            "whisper.model": self.whisper_var.get(),
            "features.face_track": bool(self.face_var.get()),
            "features.captions": bool(self.cap_var.get()),
            "features.hook": bool(self.hook_var.get()),
            "features.normalize_audio": bool(self.norm_var.get()),
            "features.facecam_pip": bool(self.facecam_var.get()),
            "video.facecam_corner": FACECAM_CORNERS.get(self.fc_corner_var.get(), "auto"),
            "features.split_layout": bool(LAYOUT_IS_SPLIT.get(self.layout_var.get(), False)),
            "video.split_facecam_ratio": self._pct(self.fc_ratio_var, 33) / 100.0,
            "video.split_gameplay_ratio": self._pct(self.gp_ratio_var, 57) / 100.0,
            "style.caption_position": CAP_POS_KEYS.get(self.cap_pos_var.get(), "seam"),
            "style.watermark": self.watermark_var.get().strip(),
            "ai.provider": PROVIDERS.get(self.provider_var.get(), "local"),
            "ai.api_key": self.key_var.get().strip(),
            "ai.base_url": self.base_var.get().strip(),
            "ai.claude_base_url": self.claude_url_var.get().strip() or CLAUDE_DEFAULT_BASE,
            "ai.model": self.ai_model_var.get().strip(),
            "ai.custom_prompt": self.prompt_box.get("1.0", "end").strip() or DEFAULT_AI_PROMPT,
            "download.cookies_from_browser": BROWSER_LABELS.get(self.yt_browser_var.get(), "auto"),
            "download.cookies_file": self.yt_cookie_var.get().strip(),
            "download.player_client": CLIENT_LABELS.get(self.yt_client_var.get(), "default"),
            "quality.mode": MODE_LABELS.get(self.mode_var.get(), "balanced"),
            "video.aspect": "custom" if self.aspect_var.get() == "Custom" else self.aspect_var.get(),
            "video.custom_width": self._int(self.cw_var, 1080),
            "video.custom_height": self._int(self.ch_var, 1920),
            "video.framing": FRAMING_LABELS.get(self.framing_var.get(), "fit_blur"),
            "video.framing_enabled": bool(self.framing_toggle_var.get()),
            "whisper.device": DEVICE_LABELS.get(self.device_var.get(), "auto"),
            "whisper.compute_type": str(self.cfg.get("whisper.compute_type", "auto")),
            "render.encoder": ENCODER_LABELS.get(self.encoder_var.get(), "auto"),
        })
        self.cfg.save()

    def save_clicked(self):
        self._save_settings()
        self.saved_label.configure(text="✓ Saved — ready for your next creation")
        self.root.after(2600, lambda: self.saved_label.configure(text=""))

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
        self.progress.set(0)
        self.logbox.delete("1.0", "end")
        self._log("HEAD", f"Starting: {source}")
        from core import hardware
        if self.cfg.get("whisper.device") == "cuda" and hardware.cuda_device_count() == 0:
            self._log("WARN", "No NVIDIA GPU detected — speech will use the CPU automatically.")
        if self.cfg.get("render.encoder") == "gpu" and not hardware.gpu_encoders():
            self._log("WARN", "No hardware video encoder found — rendering will use the CPU.")
        threading.Thread(target=self._worker, args=(source,), daemon=True).start()
        self.root.after(80, self._poll)

    def _worker(self, source: str):
        log = Logger(lambda level, msg: self.q.put(("log", level, msg)))
        progress = lambda pct, msg: self.q.put(("progress", pct, msg))
        try:
            self.q.put(("done", run_pipeline(source, self.cfg, log, progress)))
        except Exception as exc:
            import traceback
            detail = " ".join(str(exc).split()) or exc.__class__.__name__
            self.q.put(("log", "ERROR", f"{detail}\n{traceback.format_exc()}"))
            # The moment a run fails, delete its partial download, unfinished
            # clip files and any cache that no longer has a source video.
            try:
                freed = cleanup.purge_failed_run(self.cfg, log=log)
                if freed:
                    self.q.put(("log", "INFO",
                                f"Auto-clean removed {freed / 1e6:.1f} MB of failed-run temp files"))
            except Exception as clean_exc:
                self.q.put(("log", "WARN", f"Cleanup failed: {clean_exc}"))
            self.q.put(("error", detail))

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
                elif kind == "done":
                    self.result = item[1]
                    self.running = False
                    self.progress.set(1.0)
                    self.status.configure(text="Finished")
                    self.go_btn.configure(state="normal", text="MAKE VIRAL CLIPS", fg_color=PINK)
                    self.open_btn.configure(state="normal")
                    self._log("OK", f"Finished! {len(self.result['clips'])} clips in {self.result['output_dir']}")
                    self._play_completion_animation(
                        len(self.result["clips"]), self.result["output_dir"])
                    if self.checking:
                        self.root.after(80, self._poll)
                    return
                elif kind == "error":
                    self.running = False
                    self.status.configure(text="Failed")
                    self.go_btn.configure(state="normal", text="MAKE VIRAL CLIPS", fg_color=PINK)
                    self._log("ERROR", item[1])
                    messagebox.showerror("Could not finish", item[1][:600])
                    if self.checking:
                        self.root.after(80, self._poll)
                    return
        except queue.Empty:
            pass
        if self.running or self.checking:
            self.root.after(80, self._poll)


_APP = None


def launch():
    """Create and run the app window (the only animation plays on completion)."""
    global _APP
    root = ctk.CTk()
    _APP = App(root)  # keep every Tk variable alive for the whole session
    root.mainloop()
