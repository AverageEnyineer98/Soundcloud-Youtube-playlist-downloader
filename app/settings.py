"""
Settings Manager — Persists all user settings to a JSON file.
Handles loading, saving, defaults, and migration of settings.
"""

import json
import os
from pathlib import Path

# ─── Default Settings ───────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    # ── Audio Output ──────────────────────────────────────────
    "output_format": "mp3",          # mp3, wav, flac, ogg, aac, opus, m4a, aiff, wma
    "bitrate": "auto",               # 'auto' = best per track, or '128','160','192','256','320'
    "sample_rate": "auto",           # 'auto' = source native, or '44100','48000','96000'
    "bit_depth": "auto",             # 'auto' = source native, or '16','24','32' (lossless only)

    # ── Download ──────────────────────────────────────────────
    "download_dir": str(Path.home() / "Music" / "SoundWave Downloads"),
    "concurrent_downloads": 2,       # 1-5 simultaneous downloads
    "skip_existing": True,           # Skip if file already exists
    "download_speed_limit": 0,       # KB/s, 0 = unlimited
    "retry_attempts": 3,             # Retries per track on failure
    "keep_original": False,          # Keep original file alongside converted

    # ── Organization ──────────────────────────────────────────
    "playlist_subfolder": True,      # Save playlists into <download_dir>/<playlist title>/
    "organize_by_artist": False,     # Create Artist/Album/ folder structure
    "filename_template": "{artist} - {title}",  # Supports: {artist}, {title}, {number}, {album}, {date}

    # ── Metadata ──────────────────────────────────────────────
    "embed_album_art": True,         # Download and embed cover art
    "normalize_audio": False,        # Apply loudnorm volume normalization

    # ── YouTube Specific ──────────────────────────────────────
    "split_by_chapters": False,      # Split videos by chapters
    "youtube_cookies_browser": "none",  # 'none','chrome','firefox','edge','brave','opera'

    # ── Application ───────────────────────────────────────────
    "theme": "dark",                 # 'dark', 'light', 'system'
    "notification_sound": True,      # Play sound on completion
    "auto_paste_url": True,          # Auto-detect URL from clipboard
    "auto_update_ytdlp": True,       # Check for yt-dlp updates on launch

    # ── Network ───────────────────────────────────────────────
    "proxy": "",                     # HTTP/SOCKS proxy URL
}

# Format options for the UI dropdowns
FORMAT_OPTIONS = ["MP3", "WAV", "FLAC", "OGG", "AAC", "OPUS", "M4A", "AIFF", "WMA"]
BITRATE_OPTIONS = ["Auto (Best)", "320 kbps", "256 kbps", "192 kbps", "160 kbps", "128 kbps"]
SAMPLE_RATE_OPTIONS = ["Auto (Source)", "44100 Hz", "48000 Hz", "96000 Hz"]
BIT_DEPTH_OPTIONS = ["Auto (Source)", "16-bit", "24-bit", "32-bit"]
THEME_OPTIONS = ["Dark", "Light", "System"]
BROWSER_COOKIE_OPTIONS = ["None", "Chrome", "Firefox", "Edge", "Brave", "Opera"]
CONCURRENT_OPTIONS = ["1", "2", "3", "4", "5"]
FILENAME_TEMPLATE_OPTIONS = [
    "{artist} - {title}",
    "{number}. {artist} - {title}",
    "{title}",
    "{number}. {title}",
    "{artist} - {album} - {title}",
]


class SettingsManager:
    """Manages application settings with JSON file persistence."""

    def __init__(self, config_dir=None):
        if config_dir is None:
            config_dir = Path.home() / ".soundwave_downloader"
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "settings.json"
        self.settings = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        """Load settings from JSON file, merging with defaults."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # Merge: saved values override defaults, new defaults are added
                for key in DEFAULT_SETTINGS:
                    if key in saved:
                        self.settings[key] = saved[key]
            except (json.JSONDecodeError, IOError, OSError):
                pass  # Use defaults on error

    def save(self):
        """Persist current settings to JSON file."""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except (IOError, OSError) as e:
            print(f"Warning: Could not save settings: {e}")

    def get(self, key, default=None):
        """Get a setting value."""
        return self.settings.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))

    def set(self, key, value):
        """Set a setting value and auto-save."""
        self.settings[key] = value
        self.save()

    def set_many(self, updates: dict):
        """Set multiple settings at once and save."""
        self.settings.update(updates)
        self.save()

    def reset(self):
        """Reset all settings to defaults."""
        self.settings = dict(DEFAULT_SETTINGS)
        self.save()

    def get_all(self) -> dict:
        """Get a copy of all settings."""
        return dict(self.settings)

    # ── Helper methods for UI mapping ──────────────────────────────

    @staticmethod
    def bitrate_display_to_value(display: str) -> str:
        """Convert UI display string to setting value. e.g. '320 kbps' -> '320'"""
        if "auto" in display.lower() or "best" in display.lower():
            return "auto"
        return display.split()[0]

    @staticmethod
    def bitrate_value_to_display(value: str) -> str:
        """Convert setting value to UI display string. e.g. '320' -> '320 kbps'"""
        if value == "auto":
            return "Auto (Best)"
        return f"{value} kbps"

    @staticmethod
    def sample_rate_display_to_value(display: str) -> str:
        if "auto" in display.lower() or "source" in display.lower():
            return "auto"
        return display.split()[0]

    @staticmethod
    def sample_rate_value_to_display(value: str) -> str:
        if value == "auto":
            return "Auto (Source)"
        return f"{value} Hz"

    @staticmethod
    def bit_depth_display_to_value(display: str) -> str:
        if "auto" in display.lower() or "source" in display.lower():
            return "auto"
        return display.replace("-bit", "").strip()

    @staticmethod
    def bit_depth_value_to_display(value: str) -> str:
        if value == "auto":
            return "Auto (Source)"
        return f"{value}-bit"

    @staticmethod
    def theme_display_to_value(display: str) -> str:
        return display.lower()

    @staticmethod
    def browser_display_to_value(display: str) -> str:
        return display.lower()

    @staticmethod
    def browser_value_to_display(value: str) -> str:
        return value.capitalize() if value != "none" else "None"
