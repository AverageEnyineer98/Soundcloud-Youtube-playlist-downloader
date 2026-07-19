"""
SoundWave Downloader — Modern GUI built with CustomTkinter.
Features a dark theme, track list with checkboxes, progress bars,
format/quality selectors, and a full settings dialog.
"""

import os
import sys
import queue
import threading
import traceback
import winsound
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import customtkinter as ctk

from . import __version__, __app_name__
from .settings import (
    SettingsManager,
    FORMAT_OPTIONS,
    BITRATE_OPTIONS,
    SAMPLE_RATE_OPTIONS,
    BIT_DEPTH_OPTIONS,
    THEME_OPTIONS,
    BROWSER_COOKIE_OPTIONS,
    CONCURRENT_OPTIONS,
    FILENAME_TEMPLATE_OPTIONS,
)
from .downloader import Downloader, TrackInfo, PlaylistInfo
from .metadata import MetadataHandler
from .utils import (
    validate_url,
    format_duration,
    format_size,
    format_bitrate,
    check_ffmpeg,
    get_ffmpeg_version,
    get_ytdlp_version,
    open_folder,
)


# ─── Color Palette ────────────────────────────────────────────────────────────────

COLORS = {
    "accent":        "#7C3AED",    # Purple accent
    "accent_hover":  "#6D28D9",    # Darker purple on hover
    "accent_light":  "#A78BFA",    # Light purple for text
    "success":       "#10B981",    # Green
    "warning":       "#F59E0B",    # Amber
    "error":         "#EF4444",    # Red
    "info":          "#3B82F6",    # Blue
    "surface":       "#1E1B2E",    # Card background
    "surface_alt":   "#252236",    # Alternate row
    "text_primary":  "#E8E8F0",    # Primary text
    "text_secondary":"#9CA3AF",    # Muted text
    "border":        "#3F3B54",    # Border color
    "soundcloud":    "#FF5500",    # SoundCloud orange
    "youtube":       "#FF0000",    # YouTube red
}


# ─── Track Item Widget ────────────────────────────────────────────────────────────

class TrackItemWidget(ctk.CTkFrame):
    """A single track row in the track list with checkbox, info, and status."""

    def __init__(self, master, track: TrackInfo, index: int, **kwargs):
        is_alt = index % 2 == 1
        super().__init__(
            master,
            fg_color=COLORS["surface_alt"] if is_alt else COLORS["surface"],
            corner_radius=6,
            height=44,
            **kwargs,
        )
        self.track = track
        self.grid_columnconfigure(2, weight=1)  # Title column expands

        # Checkbox
        self.checkbox_var = ctk.BooleanVar(value=track.selected)
        self.checkbox = ctk.CTkCheckBox(
            self,
            text="",
            variable=self.checkbox_var,
            command=self._on_toggle,
            width=24,
            checkbox_width=20,
            checkbox_height=20,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
        )
        self.checkbox.grid(row=0, column=0, padx=(10, 4), pady=6)

        # Track number
        self.num_label = ctk.CTkLabel(
            self,
            text=str(track.track_number).zfill(2),
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=COLORS["text_secondary"],
            width=28,
        )
        self.num_label.grid(row=0, column=1, padx=(0, 6))

        # Title + Artist
        display_text = f"{track.artist}  —  {track.title}"
        self.title_label = ctk.CTkLabel(
            self,
            text=display_text,
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        self.title_label.grid(row=0, column=2, padx=(0, 8), pady=6, sticky="ew")

        # Source quality badge
        source_color = COLORS["soundcloud"] if track.source == "soundcloud" else COLORS["youtube"]
        quality_text = track.quality_label if track.quality_label != "Unknown quality" else "—"
        self.quality_label = ctk.CTkLabel(
            self,
            text=quality_text,
            font=ctk.CTkFont(size=11),
            text_color=source_color,
            width=90,
        )
        self.quality_label.grid(row=0, column=3, padx=(0, 8))

        # Duration
        self.duration_label = ctk.CTkLabel(
            self,
            text=format_duration(track.duration),
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=COLORS["text_secondary"],
            width=50,
        )
        self.duration_label.grid(row=0, column=4, padx=(0, 8))

        # Status indicator
        self.status_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
            width=70,
        )
        self.status_label.grid(row=0, column=5, padx=(0, 10))

    def _on_toggle(self):
        self.track.selected = self.checkbox_var.get()

    def set_status(self, status: str):
        """Update the status indicator."""
        status_map = {
            "pending":      ("⏳", COLORS["text_secondary"]),
            "downloading":  ("⬇️", COLORS["info"]),
            "converting":   ("🔄", COLORS["warning"]),
            "done":         ("✅", COLORS["success"]),
            "error":        ("❌", COLORS["error"]),
            "skipped":      ("⏭️", COLORS["text_secondary"]),
        }
        icon, color = status_map.get(status, ("", COLORS["text_secondary"]))
        self.status_label.configure(text=icon, text_color=color)

    def set_selected(self, selected: bool):
        self.checkbox_var.set(selected)
        self.track.selected = selected


# ─── Settings Dialog ──────────────────────────────────────────────────────────────

class SettingsWindow(ctk.CTkToplevel):
    """Full settings dialog with all 20 configurable options."""

    def __init__(self, master, settings_manager: SettingsManager):
        super().__init__(master)
        self.settings = settings_manager
        self.title("⚙  Settings")
        self.geometry("580x700")
        self.minsize(520, 600)
        self.resizable(True, True)
        self.transient(master)

        # Check if download is in progress — warn user
        self._is_downloading = getattr(master, 'is_downloading', False)
        if self._is_downloading:
            # Non-modal when downloading to avoid blocking, but show warning
            warn_label = ctk.CTkLabel(
                self,
                text="⚠  Some settings won't take effect until the current download finishes.",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["warning"],
                wraplength=500,
            )
            warn_label.pack(padx=16, pady=(12, 0))
        else:
            self.grab_set()  # Modal only when not downloading

        # Main scrollable area
        self.scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        self.scroll_frame.grid_columnconfigure(1, weight=1)

        row = 0

        # ── Audio Output ──────────────────────────
        row = self._section_header("🎵  Audio Output", row)
        row = self._add_dropdown("Output Format", "output_format",
            FORMAT_OPTIONS, self._format_get, self._format_set, row)
        row = self._add_dropdown("Bitrate", "bitrate",
            BITRATE_OPTIONS, self.settings.bitrate_value_to_display,
            self.settings.bitrate_display_to_value, row)
        row = self._add_dropdown("Sample Rate", "sample_rate",
            SAMPLE_RATE_OPTIONS, self.settings.sample_rate_value_to_display,
            self.settings.sample_rate_display_to_value, row)
        row = self._add_dropdown("Bit Depth (Lossless)", "bit_depth",
            BIT_DEPTH_OPTIONS, self.settings.bit_depth_value_to_display,
            self.settings.bit_depth_display_to_value, row)

        # ── Download ─────────────────────────────
        row = self._section_header("⬇  Download", row)
        row = self._add_dropdown("Concurrent Downloads", "concurrent_downloads",
            CONCURRENT_OPTIONS, lambda v: str(v), lambda v: int(v), row)
        row = self._add_entry("Speed Limit (KB/s, 0=∞)", "download_speed_limit", row, is_int=True)
        row = self._add_entry("Retry Attempts", "retry_attempts", row, is_int=True)
        row = self._add_switch("Skip Existing Files", "skip_existing", row)
        row = self._add_switch("Keep Original File", "keep_original", row)

        # ── Organization ─────────────────────────
        row = self._section_header("📂  Organization", row)
        row = self._add_switch("Playlist Name Subfolder", "playlist_subfolder", row)
        row = self._add_switch("Organize by Artist/Album", "organize_by_artist", row)
        row = self._add_dropdown("Filename Template", "filename_template",
            FILENAME_TEMPLATE_OPTIONS, lambda v: v, lambda v: v, row)

        # ── Metadata ─────────────────────────────
        row = self._section_header("🏷  Metadata", row)
        row = self._add_switch("Embed Album Art", "embed_album_art", row)
        row = self._add_switch("Normalize Audio Volume", "normalize_audio", row)

        # ── YouTube ──────────────────────────────
        row = self._section_header("▶  YouTube", row)
        row = self._add_switch("Split by Chapters", "split_by_chapters", row)
        row = self._add_dropdown("Browser Cookies", "youtube_cookies_browser",
            BROWSER_COOKIE_OPTIONS, self.settings.browser_value_to_display,
            self.settings.browser_display_to_value, row)

        # ── Application ─────────────────────────
        row = self._section_header("🖥  Application", row)
        row = self._add_dropdown("Theme", "theme",
            THEME_OPTIONS, lambda v: v.capitalize(), lambda v: v.lower(), row)
        row = self._add_switch("Notification Sound", "notification_sound", row)
        row = self._add_switch("Auto-Paste URL", "auto_paste_url", row)
        row = self._add_switch("Auto-Update yt-dlp", "auto_update_ytdlp", row)

        # ── Network ──────────────────────────────
        row = self._section_header("🌐  Network", row)
        row = self._add_entry("Proxy (HTTP/SOCKS)", "proxy", row)

        # ── Bottom buttons ────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(8, 16))

        ctk.CTkButton(
            btn_frame, text="Reset to Defaults", fg_color=COLORS["error"],
            hover_color="#DC2626", command=self._reset, width=140,
        ).pack(side="left")

        ctk.CTkButton(
            btn_frame, text="Close", fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"], command=self.destroy, width=100,
        ).pack(side="right")

    # ── Widget Builders ───────────────────────────────────────

    def _section_header(self, text: str, row: int) -> int:
        label = ctk.CTkLabel(
            self.scroll_frame, text=text,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["accent_light"],
            anchor="w",
        )
        label.grid(row=row, column=0, columnspan=2, sticky="w", padx=4, pady=(16, 8))
        return row + 1

    def _add_dropdown(self, label, key, options, to_display, from_display, row) -> int:
        ctk.CTkLabel(
            self.scroll_frame, text=label,
            font=ctk.CTkFont(size=13), text_color=COLORS["text_primary"], anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(12, 8), pady=5)

        current = to_display(self.settings.get(key))
        var = ctk.StringVar(value=current)

        def on_change(value):
            self.settings.set(key, from_display(value))
            # Apply theme immediately
            if key == "theme":
                ctk.set_appearance_mode(from_display(value))

        menu = ctk.CTkOptionMenu(
            self.scroll_frame, values=options, variable=var,
            command=on_change, width=180,
            fg_color=COLORS["surface_alt"], button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        menu.grid(row=row, column=1, sticky="e", padx=(0, 12), pady=5)
        return row + 1

    def _add_switch(self, label, key, row) -> int:
        ctk.CTkLabel(
            self.scroll_frame, text=label,
            font=ctk.CTkFont(size=13), text_color=COLORS["text_primary"], anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(12, 8), pady=5)

        var = ctk.BooleanVar(value=self.settings.get(key, False))

        def on_change():
            self.settings.set(key, var.get())

        switch = ctk.CTkSwitch(
            self.scroll_frame, text="", variable=var, command=on_change,
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_light"],
        )
        switch.grid(row=row, column=1, sticky="e", padx=(0, 12), pady=5)
        return row + 1

    def _add_entry(self, label, key, row, is_int=False) -> int:
        ctk.CTkLabel(
            self.scroll_frame, text=label,
            font=ctk.CTkFont(size=13), text_color=COLORS["text_primary"], anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(12, 8), pady=5)

        var = ctk.StringVar(value=str(self.settings.get(key, "")))

        def on_change(*_):
            val = var.get()
            if is_int:
                try:
                    val = int(val)
                except ValueError:
                    return
            self.settings.set(key, val)

        entry = ctk.CTkEntry(
            self.scroll_frame, textvariable=var, width=180,
            border_color=COLORS["border"],
        )
        entry.grid(row=row, column=1, sticky="e", padx=(0, 12), pady=5)
        entry.bind("<FocusOut>", on_change)
        entry.bind("<Return>", on_change)
        return row + 1

    def _format_get(self, value):
        return value.upper()

    def _format_set(self, value):
        return value.lower()

    def _reset(self):
        self.settings.reset()
        self.destroy()


# ─── Main Application Window ──────────────────────────────────────────────────────

class App(ctk.CTk):
    """Main SoundWave Downloader application window."""

    def __init__(self, settings_manager: SettingsManager = None):
        super().__init__()

        # ── State ─────────────────────────────────────────────
        self.settings_manager = settings_manager or SettingsManager()
        self.downloader = Downloader(self.settings_manager)
        self.metadata_handler = MetadataHandler()
        self.playlist_info: PlaylistInfo | None = None
        self.track_widgets: list[TrackItemWidget] = []
        self.is_analyzing = False
        self.is_downloading = False
        self.progress_queue = queue.Queue()
        self._current_progress_index = None

        # ── Window Setup ──────────────────────────────────────
        self.title(f"🎵  {__app_name__}")
        self.geometry("1000x740")
        self.minsize(850, 620)

        # Apply saved theme
        ctk.set_appearance_mode(self.settings_manager.get("theme", "dark"))
        ctk.set_default_color_theme("blue")

        # ── Build UI ──────────────────────────────────────────
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)  # Track list stretches

        self._create_header()          # Row 0
        self._create_playlist_bar()    # Row 1
        self._create_track_list()      # Row 2
        self._create_output_panel()    # Row 3
        self._create_progress_panel()  # Row 4
        self._create_status_bar()      # Row 5

        # ── Start polling ─────────────────────────────────────
        self._poll_progress_queue()

        # ── Auto-paste from clipboard ─────────────────────────
        if self.settings_manager.get("auto_paste_url", True):
            self.after(600, self._try_auto_paste)

    # ══════════════════════════════════════════════════════════
    # UI Construction
    # ══════════════════════════════════════════════════════════

    def _create_header(self):
        """URL input bar with Analyze button and settings gear."""
        header = ctk.CTkFrame(self, fg_color=COLORS["surface"], corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_columnconfigure(1, weight=1)

        # App title / logo
        title_label = ctk.CTkLabel(
            header,
            text=f"🎵 {__app_name__}",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["accent_light"],
        )
        title_label.grid(row=0, column=0, padx=(16, 12), pady=12)

        # URL entry
        self.url_entry = ctk.CTkEntry(
            header,
            placeholder_text="🔗  Paste a SoundCloud or YouTube URL here...",
            font=ctk.CTkFont(size=14),
            height=40,
            border_color=COLORS["border"],
            fg_color=COLORS["surface_alt"],
        )
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=12)
        self.url_entry.bind("<Return>", lambda e: self._on_analyze())

        # Analyze button
        self.analyze_btn = ctk.CTkButton(
            header,
            text="🔍  Analyze",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            height=40,
            width=120,
            command=self._on_analyze,
        )
        self.analyze_btn.grid(row=0, column=2, padx=(0, 8), pady=12)

        # Settings gear button
        settings_btn = ctk.CTkButton(
            header,
            text="⚙",
            font=ctk.CTkFont(size=18),
            fg_color="transparent",
            hover_color=COLORS["surface_alt"],
            width=40,
            height=40,
            command=self._open_settings,
        )
        settings_btn.grid(row=0, column=3, padx=(0, 12), pady=12)

    def _create_playlist_bar(self):
        """Info bar showing playlist title, track count, total duration."""
        self.playlist_bar = ctk.CTkFrame(self, fg_color=COLORS["surface_alt"], corner_radius=0, height=36)
        self.playlist_bar.grid(row=1, column=0, sticky="ew")
        self.playlist_bar.grid_columnconfigure(0, weight=1)

        self.playlist_info_label = ctk.CTkLabel(
            self.playlist_bar,
            text="  Enter a URL above and click Analyze to get started",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.playlist_info_label.grid(row=0, column=0, sticky="w", padx=16, pady=8)

        # Select/deselect buttons (hidden until tracks loaded)
        self.select_frame = ctk.CTkFrame(self.playlist_bar, fg_color="transparent")
        self.select_frame.grid(row=0, column=1, sticky="e", padx=(0, 12))

        self.select_all_btn = ctk.CTkButton(
            self.select_frame, text="✓ All", width=60, height=28,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=lambda: self._set_all_tracks(True),
        )
        self.deselect_all_btn = ctk.CTkButton(
            self.select_frame, text="✗ None", width=60, height=28,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["border"], hover_color=COLORS["surface"],
            command=lambda: self._set_all_tracks(False),
        )
        # Initially hidden
        self.select_all_btn.pack_forget()
        self.deselect_all_btn.pack_forget()

    def _create_track_list(self):
        """Scrollable list of track items."""
        self.track_list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS["surface"],
            corner_radius=0,
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        self.track_list_frame.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        self.track_list_frame.grid_columnconfigure(0, weight=1)

        # Empty state message
        self.empty_label = ctk.CTkLabel(
            self.track_list_frame,
            text="🎵\n\nNo tracks loaded yet.\nPaste a SoundCloud or YouTube URL above\nand click Analyze to discover tracks.",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_secondary"],
            justify="center",
        )
        self.empty_label.grid(row=0, column=0, pady=60)

    def _create_output_panel(self):
        """Output format, quality, and save location controls."""
        panel = ctk.CTkFrame(self, fg_color=COLORS["surface_alt"], corner_radius=0)
        panel.grid(row=3, column=0, sticky="ew")
        panel.grid_columnconfigure(4, weight=1)  # Save path expands

        pad_y = 10

        # Format selector
        ctk.CTkLabel(
            panel, text="Format:", font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        ).grid(row=0, column=0, padx=(16, 4), pady=pad_y)

        self.format_var = ctk.StringVar(
            value=self.settings_manager.get("output_format", "mp3").upper()
        )
        self.format_menu = ctk.CTkOptionMenu(
            panel, values=FORMAT_OPTIONS, variable=self.format_var,
            width=90, fg_color=COLORS["surface"],
            button_color=COLORS["accent"], button_hover_color=COLORS["accent_hover"],
            command=self._on_format_change,
        )
        self.format_menu.grid(row=0, column=1, padx=(0, 16), pady=pad_y)

        # Quality selector
        ctk.CTkLabel(
            panel, text="Quality:", font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        ).grid(row=0, column=2, padx=(0, 4), pady=pad_y)

        self.quality_var = ctk.StringVar(
            value=SettingsManager.bitrate_value_to_display(
                self.settings_manager.get("bitrate", "auto")
            )
        )
        self.quality_menu = ctk.CTkOptionMenu(
            panel, values=BITRATE_OPTIONS, variable=self.quality_var,
            width=130, fg_color=COLORS["surface"],
            button_color=COLORS["accent"], button_hover_color=COLORS["accent_hover"],
        )
        self.quality_menu.grid(row=0, column=3, padx=(0, 16), pady=pad_y)

        # Save location
        ctk.CTkLabel(
            panel, text="Save to:", font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        ).grid(row=0, column=4, padx=(0, 4), pady=pad_y, sticky="w")

        save_frame = ctk.CTkFrame(panel, fg_color="transparent")
        save_frame.grid(row=0, column=5, sticky="ew", padx=(0, 8), pady=pad_y)
        save_frame.grid_columnconfigure(0, weight=1)

        self.save_dir_var = ctk.StringVar(
            value=self.settings_manager.get("download_dir", "")
        )
        self.save_dir_entry = ctk.CTkEntry(
            save_frame, textvariable=self.save_dir_var,
            font=ctk.CTkFont(size=12),
            border_color=COLORS["border"], fg_color=COLORS["surface"],
        )
        self.save_dir_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        browse_btn = ctk.CTkButton(
            save_frame, text="📁", width=36, height=28,
            fg_color=COLORS["surface"], hover_color=COLORS["border"],
            command=self._browse_save_dir,
        )
        browse_btn.grid(row=0, column=1)

        # Download button
        self.download_btn = ctk.CTkButton(
            panel,
            text="⬇  Download",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            height=38,
            width=150,
            command=self._on_download,
        )
        self.download_btn.grid(row=0, column=6, padx=(8, 16), pady=pad_y)

    def _create_progress_panel(self):
        """Progress bars and download status."""
        self.progress_frame = ctk.CTkFrame(self, fg_color=COLORS["surface"], corner_radius=0, height=80)
        self.progress_frame.grid(row=4, column=0, sticky="ew")
        self.progress_frame.grid_columnconfigure(1, weight=1)

        # Current track info
        self.current_track_label = ctk.CTkLabel(
            self.progress_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.current_track_label.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(8, 2))

        # Track progress bar
        ctk.CTkLabel(
            self.progress_frame, text="Track:", font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"], width=50,
        ).grid(row=1, column=0, padx=(16, 4), pady=2)

        self.track_progress = ctk.CTkProgressBar(
            self.progress_frame,
            progress_color=COLORS["accent"],
            fg_color=COLORS["surface_alt"],
            height=12,
        )
        self.track_progress.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=2)
        self.track_progress.set(0)

        self.track_progress_label = ctk.CTkLabel(
            self.progress_frame, text="0%",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=COLORS["text_secondary"], width=60, anchor="e",
        )
        self.track_progress_label.grid(row=1, column=2, padx=(0, 16), pady=2)

        # Overall progress bar
        ctk.CTkLabel(
            self.progress_frame, text="Overall:", font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"], width=50,
        ).grid(row=2, column=0, padx=(16, 4), pady=(2, 8))

        self.overall_progress = ctk.CTkProgressBar(
            self.progress_frame,
            progress_color=COLORS["success"],
            fg_color=COLORS["surface_alt"],
            height=12,
        )
        self.overall_progress.grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(2, 8))
        self.overall_progress.set(0)

        self.overall_progress_label = ctk.CTkLabel(
            self.progress_frame, text="0%",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=COLORS["text_secondary"], width=60, anchor="e",
        )
        self.overall_progress_label.grid(row=2, column=2, padx=(0, 16), pady=(2, 8))

        # Initially hide progress
        self.progress_frame.grid_remove()

    def _create_status_bar(self):
        """Bottom status bar with app info."""
        status_bar = ctk.CTkFrame(self, fg_color=COLORS["surface_alt"], corner_radius=0, height=28)
        status_bar.grid(row=5, column=0, sticky="ew")
        status_bar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            status_bar, text="Ready",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=16, pady=4)

        # FFmpeg status
        ffmpeg_ok = check_ffmpeg()
        ffmpeg_text = f"FFmpeg: {'✓ ' + get_ffmpeg_version() if ffmpeg_ok else '✗ Not found'}"
        ffmpeg_color = COLORS["success"] if ffmpeg_ok else COLORS["error"]

        ffmpeg_label = ctk.CTkLabel(
            status_bar, text=ffmpeg_text,
            font=ctk.CTkFont(size=11),
            text_color=ffmpeg_color,
        )
        ffmpeg_label.grid(row=0, column=1, padx=8, pady=4)

        # yt-dlp version
        ytdlp_label = ctk.CTkLabel(
            status_bar, text=f"yt-dlp: {get_ytdlp_version()}",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        )
        ytdlp_label.grid(row=0, column=2, padx=8, pady=4)

        # App version
        ver_label = ctk.CTkLabel(
            status_bar, text=f"v{__version__}",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        )
        ver_label.grid(row=0, column=3, padx=(8, 16), pady=4)

    # ══════════════════════════════════════════════════════════
    # Actions
    # ══════════════════════════════════════════════════════════

    def _on_analyze(self):
        """Handle Analyze button click — extract info from the URL."""
        url = self.url_entry.get().strip()
        if not url:
            self._set_status("⚠  Please enter a URL", COLORS["warning"])
            return

        source = validate_url(url)
        if source is None:
            self._set_status("❌  Invalid URL. Please enter a SoundCloud or YouTube URL.", COLORS["error"])
            return

        if self.is_analyzing:
            # Second click cancels the running analysis
            self.downloader.cancel()
            self._set_status("⏹  Cancelling analysis...", COLORS["warning"])
            return

        if self.is_downloading:
            self._set_status("⚠  Wait for the current download to finish first.", COLORS["warning"])
            return

        self.is_analyzing = True
        # A leftover cancel flag from a previous cancel would silently abort
        # this analysis after 0 tracks — always clear it before starting.
        self.downloader.reset_cancel()
        self.analyze_btn.configure(text="✕  Cancel", fg_color=COLORS["error"], hover_color="#DC2626")
        self._set_status(f"🔍  Analyzing {source.capitalize()} URL...", COLORS["info"])
        self._clear_tracks()

        def worker():
            try:
                def progress_cb(current, total):
                    self.progress_queue.put({
                        "type": "analyze_progress",
                        "current": current,
                        "total": total,
                    })

                result = self.downloader.analyze_url(url, progress_callback=progress_cb)
                self.progress_queue.put({"type": "analyze_done", "result": result})
            except Exception as e:
                self.progress_queue.put({"type": "analyze_error", "error": str(e)})

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _on_analyze_complete(self, result: PlaylistInfo):
        """Called when URL analysis is complete."""
        self.is_analyzing = False
        self.analyze_btn.configure(
            text="🔍  Analyze", state="normal",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
        )
        self.playlist_info = result

        # Update playlist bar
        source_icon = "🟠" if result.source == "soundcloud" else "🔴"
        info_text = (
            f"  {source_icon}  {result.title}   │   "
            f"{result.track_count} track{'s' if result.track_count != 1 else ''}   │   "
            f"{format_duration(result.total_duration)}"
        )
        if result.uploader:
            info_text += f"   │   by {result.uploader}"
        self.playlist_info_label.configure(text=info_text, text_color=COLORS["text_primary"])

        # Show select/deselect buttons
        self.select_all_btn.pack(side="left", padx=2)
        self.deselect_all_btn.pack(side="left", padx=2)

        # Populate track list
        self._populate_tracks(result.tracks)

        selected = sum(1 for t in result.tracks if t.selected)
        self._set_status(
            f"✅  Found {result.track_count} track(s). {selected} selected for download.",
            COLORS["success"],
        )

    def _on_analyze_error(self, error: str):
        """Called when URL analysis fails."""
        self.is_analyzing = False
        self.analyze_btn.configure(
            text="🔍  Analyze", state="normal",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
        )
        self._set_status(f"❌  Analysis failed: {error}", COLORS["error"])

    def _on_download(self):
        """Handle Download button click."""
        if self.is_downloading:
            # Cancel
            self.downloader.cancel()
            self.download_btn.configure(text="⬇  Download", fg_color=COLORS["accent"])
            self._set_status("⏹  Cancelling downloads...", COLORS["warning"])
            return

        if not self.playlist_info or not self.playlist_info.tracks:
            self._set_status("⚠  No tracks to download. Analyze a URL first.", COLORS["warning"])
            return

        selected_tracks = [t for t in self.playlist_info.tracks if t.selected]
        if not selected_tracks:
            self._set_status("⚠  No tracks selected for download.", COLORS["warning"])
            return

        # Ensure save directory exists
        save_dir = self.save_dir_var.get().strip()
        if not save_dir:
            self._set_status("⚠  Please select a save directory.", COLORS["warning"])
            return

        # Update settings
        self.settings_manager.set("download_dir", save_dir)

        # Get format and quality
        output_format = self.format_var.get().lower()
        quality_settings = {
            "bitrate": SettingsManager.bitrate_display_to_value(self.quality_var.get()),
            "sample_rate": self.settings_manager.get("sample_rate", "auto"),
            "bit_depth": self.settings_manager.get("bit_depth", "auto"),
        }

        # Start download
        self.is_downloading = True
        self.downloader.reset_cancel()
        self.download_btn.configure(text="⏹  Cancel", fg_color=COLORS["error"])
        self.progress_frame.grid()  # Show progress
        self.track_progress.set(0)
        self.overall_progress.set(0)

        # Snapshot settings at download start so mid-download changes don't affect anything
        concurrent = self.settings_manager.get("concurrent_downloads", 2)

        self._set_status(f"⬇  Downloading {len(selected_tracks)} track(s)...", COLORS["info"])

        def worker():
            completed = 0
            failed = 0
            total = len(selected_tracks)

            try:
                if concurrent <= 1:
                    # Sequential download
                    for i, track in enumerate(selected_tracks):
                        if self.downloader.is_cancelled:
                            break

                        result = self._download_single_track(
                            track, i, total, output_format, quality_settings
                        )
                        if result:
                            completed += 1
                        else:
                            failed += 1

                        self.progress_queue.put({
                            "type": "download_track_done",
                            "track": track,
                            "index": i,
                            "total": total,
                            "completed": completed,
                            "path": result,
                        })
                else:
                    # Concurrent downloads using ThreadPoolExecutor
                    from concurrent.futures import (
                        ThreadPoolExecutor, wait, FIRST_COMPLETED,
                    )

                    with ThreadPoolExecutor(max_workers=concurrent) as executor:
                        future_to_track = {}
                        for i, track in enumerate(selected_tracks):
                            future = executor.submit(
                                self._download_single_track,
                                track, i, total, output_format, quality_settings
                            )
                            future_to_track[future] = (track, i)

                        # Stall safety net: if NO track finishes for 30 min
                        # (rate-limit cooldowns included), assume the workers
                        # are wedged and bail out — otherwise the GUI stays
                        # on "downloading" forever. wait() timeout is per
                        # iteration, so normal long playlists never trip it.
                        pending = set(future_to_track)
                        while pending:
                            done, pending = wait(
                                pending, timeout=1800, return_when=FIRST_COMPLETED
                            )
                            if not done:
                                print("[SoundWave] Download stalled — cancelling remaining tracks.")
                                self.downloader.cancel()
                                executor.shutdown(wait=False, cancel_futures=True)
                                for future in pending:
                                    track, i = future_to_track[future]
                                    failed += 1
                                    track.status = "error"
                                    track.error_message = "Stalled — cancelled by watchdog"
                                break

                            if self.downloader.is_cancelled:
                                executor.shutdown(wait=False, cancel_futures=True)

                            for future in done:
                                track, i = future_to_track[future]
                                try:
                                    result = future.result()
                                except Exception as e:
                                    result = None
                                    track.status = "error"
                                    track.error_message = str(e)

                                if result:
                                    completed += 1
                                else:
                                    failed += 1

                                self.progress_queue.put({
                                    "type": "download_track_done",
                                    "track": track,
                                    "index": i,
                                    "total": total,
                                    "completed": completed,
                                    "path": result,
                                })

                            if self.downloader.is_cancelled:
                                break

            except Exception as e:
                print(f"[SoundWave] Download worker error: {e}")
                traceback.print_exc()
            finally:
                # ALWAYS send completion signal so the GUI never gets stuck
                self.progress_queue.put({
                    "type": "download_all_done",
                    "completed": completed,
                    "total": total,
                    "failed": failed,
                })

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _download_single_track(self, track, index, total, output_format, quality_settings):
        """Download a single track — used by both sequential and concurrent modes."""
        if self.downloader.is_cancelled:
            return None

        out_dir = self.downloader.get_output_dir(track, self._playlist_folder_title())

        # Notify GUI of track start
        self.progress_queue.put({
            "type": "download_track_start",
            "track": track,
            "index": index,
            "total": total,
        })

        def track_progress_cb(info):
            info["type"] = "download_track_progress"
            info["index"] = index
            info["total"] = total
            self.progress_queue.put(info)

        try:
            result_path = self.downloader.download_track(
                track, out_dir, output_format, quality_settings,
                progress_callback=track_progress_cb,
            )

            # Post-download metadata tagging
            if result_path and os.path.exists(result_path):
                try:
                    self.metadata_handler.tag_file(result_path, track)
                except Exception:
                    pass  # Metadata tagging failure shouldn't fail the download

            return result_path

        except Exception as e:
            track.status = "error"
            track.error_message = str(e)
            return None

    def _on_format_change(self, value):
        """Update quality options based on selected format."""
        fmt = value.lower()
        lossless = fmt in ("wav", "flac", "aiff")
        if lossless:
            self.quality_var.set("Auto (Best)")
            self.quality_menu.configure(state="disabled")
        else:
            self.quality_menu.configure(state="normal")

    # ══════════════════════════════════════════════════════════
    # Track List Management
    # ══════════════════════════════════════════════════════════

    def _clear_tracks(self):
        """Remove all track widgets."""
        for widget in self.track_widgets:
            widget.destroy()
        self.track_widgets.clear()
        self.empty_label.grid(row=0, column=0, pady=60)

    def _populate_tracks(self, tracks: list[TrackInfo]):
        """Create track item widgets for all tracks."""
        self.empty_label.grid_remove()
        self._clear_tracks()
        # Re-hide empty label after clear brings it back
        self.empty_label.grid_remove()

        for i, track in enumerate(tracks):
            widget = TrackItemWidget(self.track_list_frame, track, i)
            widget.grid(row=i, column=0, sticky="ew", padx=4, pady=1)
            self.track_widgets.append(widget)

    def _set_all_tracks(self, selected: bool):
        """Select or deselect all tracks."""
        for widget in self.track_widgets:
            widget.set_selected(selected)

    # ══════════════════════════════════════════════════════════
    # Progress Queue Polling
    # ══════════════════════════════════════════════════════════

    def _poll_progress_queue(self):
        """Process messages from worker threads. Crash-resilient."""
        try:
            # Cap messages per tick: if producers outpace the GUI, an
            # unbounded drain loop starves the Tk mainloop and the window
            # freezes ("Not Responding"). Leftovers are picked up next tick.
            for _ in range(100):
                msg = self.progress_queue.get_nowait()
                try:
                    self._handle_progress_message(msg)
                except Exception as e:
                    # Never let a message handling error kill the polling loop
                    print(f"[SoundWave] Error handling progress message: {e}")
                    traceback.print_exc()
        except queue.Empty:
            pass
        except Exception as e:
            print(f"[SoundWave] Error in progress queue: {e}")
        # Always reschedule — this is the heartbeat of the GUI
        self.after(100, self._poll_progress_queue)

    def _handle_progress_message(self, msg: dict):
        """Handle a single progress message from a worker thread."""
        msg_type = msg.get("type")

        if msg_type == "analyze_progress":
            current, total = msg["current"], msg["total"]
            self._set_status(f"🔍  Analyzing... ({current}/{total} tracks)", COLORS["info"])

        elif msg_type == "analyze_done":
            self._on_analyze_complete(msg["result"])

        elif msg_type == "analyze_error":
            self._on_analyze_error(msg["error"])

        elif msg_type == "download_track_start":
            track = msg["track"]
            idx, total = msg["index"], msg["total"]
            # With concurrent downloads several tracks report progress at
            # once; pin the single progress bar to the most recently
            # started one instead of flickering between all of them.
            self._current_progress_index = idx
            self.current_track_label.configure(
                text=f"  ⬇  Track {idx + 1}/{total}:  {track.artist} — {track.title}"
            )
            self.track_progress.set(0)
            self.track_progress_label.configure(text="0%")

            # Update track widget status
            for w in self.track_widgets:
                if w.track is track:
                    w.set_status("downloading")
                    break

        elif msg_type == "download_track_progress":
            if msg.get("index") != getattr(self, "_current_progress_index", None):
                return
            progress = msg.get("progress", 0)
            speed = msg.get("speed", 0)
            status = msg.get("status", "downloading")

            self.track_progress.set(progress)
            pct = int(progress * 100)
            speed_str = format_size(speed) + "/s" if speed else ""

            if status == "converting":
                self.track_progress_label.configure(text="Converting...")
                self.current_track_label.configure(
                    text=self.current_track_label.cget("text").replace("⬇", "🔄")
                )
            elif status == "waiting":
                retry_in = msg.get("retry_in", 0)
                self.track_progress_label.configure(text="Paused")
                self._set_status(
                    f"⏳  Rate limited by SoundCloud — waiting {retry_in}s before retrying...",
                    COLORS["warning"],
                )
            else:
                self.track_progress_label.configure(text=f"{pct}%  {speed_str}")

        elif msg_type == "download_track_done":
            track = msg["track"]
            completed = msg["completed"]
            total = msg["total"]

            # Update overall progress
            overall = completed / total if total > 0 else 0
            self.overall_progress.set(overall)
            self.overall_progress_label.configure(text=f"{int(overall * 100)}%")

            # Update track widget status
            for w in self.track_widgets:
                if w.track is track:
                    w.set_status(track.status)
                    break

        elif msg_type == "download_all_done":
            self.is_downloading = False
            self.download_btn.configure(text="⬇  Download", fg_color=COLORS["accent"])

            completed = msg["completed"]
            total = msg["total"]
            failed = msg.get("failed", 0)
            cancelled = self.downloader.is_cancelled

            if cancelled:
                self._set_status(f"⏹  Download cancelled. {completed}/{total} tracks completed.", COLORS["warning"])
            elif failed > 0:
                self._set_status(
                    f"⚠  Download finished with {failed} error(s). {completed}/{total} tracks saved.",
                    COLORS["warning"],
                )
            else:
                self._set_status(
                    f"✅  Download complete! {completed}/{total} tracks saved.",
                    COLORS["success"],
                )

            # Play notification sound
            if not cancelled and self.settings_manager.get("notification_sound", True):
                try:
                    winsound.MessageBeep(winsound.MB_OK)
                except Exception:
                    pass

            self.track_progress.set(1 if not cancelled else 0)
            self.overall_progress.set(1 if not cancelled else completed / total if total else 0)

            # Show "Open Folder" button in the current track label area —
            # point at the playlist subfolder when one was used
            save_dir = self.downloader.get_output_dir(None, self._playlist_folder_title())
            self.current_track_label.configure(text="")
            if save_dir and os.path.isdir(save_dir) and completed > 0:
                self.current_track_label.configure(
                    text=f"  📂  Files saved to: {save_dir}",
                    cursor="hand2",
                )
                self.current_track_label.bind(
                    "<Button-1>",
                    lambda e: open_folder(save_dir),
                )

    # ══════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════

    def _playlist_folder_title(self) -> str | None:
        """Playlist title to use as the download subfolder, if applicable."""
        if self.playlist_info and self.playlist_info.is_playlist:
            return self.playlist_info.title
        return None

    def _set_status(self, text: str, color: str = COLORS["text_secondary"]):
        """Update the status bar text."""
        self.status_label.configure(text=text, text_color=color)

    def _browse_save_dir(self):
        """Open a folder browser dialog."""
        directory = ctk.filedialog.askdirectory(
            title="Select Download Folder",
            initialdir=self.save_dir_var.get() or str(Path.home()),
        )
        if directory:
            self.save_dir_var.set(directory)
            self.settings_manager.set("download_dir", directory)

    def _open_settings(self):
        """Open the settings dialog."""
        SettingsWindow(self, self.settings_manager)

    def _try_auto_paste(self):
        """Try to auto-paste URL from clipboard."""
        try:
            clipboard = self.clipboard_get()
            if clipboard and validate_url(clipboard):
                current = self.url_entry.get().strip()
                if not current:
                    self.url_entry.delete(0, "end")
                    self.url_entry.insert(0, clipboard)
                    self._set_status("📋  URL auto-pasted from clipboard", COLORS["info"])
        except Exception:
            pass  # Clipboard might not have text
