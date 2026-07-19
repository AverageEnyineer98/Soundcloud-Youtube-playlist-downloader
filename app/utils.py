"""
Utility functions — URL validation, filename sanitization, FFmpeg detection, etc.
"""

import re
import os
import shutil
import subprocess
import sys


# ─── URL Validation ──────────────────────────────────────────────────────────────

SOUNDCLOUD_PATTERNS = [
    r"https?://(?:www\.)?soundcloud\.com/.+",
    r"https?://m\.soundcloud\.com/.+",
    r"https?://on\.soundcloud\.com/.+",
]

YOUTUBE_PATTERNS = [
    r"https?://(?:www\.)?youtube\.com/watch\?.*v=.+",
    r"https?://(?:www\.)?youtube\.com/playlist\?.*list=.+",
    r"https?://youtu\.be/.+",
    r"https?://music\.youtube\.com/.+",
    r"https?://(?:www\.)?youtube\.com/shorts/.+",
]


def validate_url(url: str) -> str | None:
    """
    Validate and classify a URL.
    Returns 'soundcloud', 'youtube', or None if invalid/unsupported.
    """
    url = url.strip()
    if not url:
        return None

    for pattern in SOUNDCLOUD_PATTERNS:
        if re.match(pattern, url, re.IGNORECASE):
            return "soundcloud"

    for pattern in YOUTUBE_PATTERNS:
        if re.match(pattern, url, re.IGNORECASE):
            return "youtube"

    return None


def is_playlist_url(url: str) -> bool:
    """Check if a URL is likely a playlist (vs single track)."""
    url = url.lower()
    if "soundcloud.com" in url and "/sets/" in url:
        return True
    if "youtube.com" in url and "list=" in url:
        return True
    return False


# ─── Filename Handling ────────────────────────────────────────────────────────────

# Characters illegal in Windows filenames
ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are illegal in filenames."""
    if not name:
        return "untitled"

    # Replace illegal characters with underscore
    name = ILLEGAL_CHARS.sub("_", name)

    # Remove leading/trailing dots, spaces, and underscores
    name = name.strip(". _")

    # Collapse multiple underscores/spaces
    name = re.sub(r"[_ ]{2,}", " ", name)

    # Limit length (leave room for extension and path)
    if len(name) > 200:
        name = name[:200].rstrip(". _")

    return name or "untitled"


def apply_filename_template(template: str, track_info) -> str:
    """
    Apply a filename template using track info.
    Supports: {artist}, {title}, {number}, {album}, {date}, {genre}
    """
    replacements = {
        "artist": sanitize_filename(getattr(track_info, "artist", "Unknown Artist")),
        "title": sanitize_filename(getattr(track_info, "title", "Unknown")),
        "number": str(getattr(track_info, "track_number", 0)).zfill(2),
        "album": sanitize_filename(getattr(track_info, "album", "")),
        "date": getattr(track_info, "upload_date", "") or "",
        "genre": sanitize_filename(getattr(track_info, "genre", "")),
    }

    try:
        result = template.format(**replacements)
    except (KeyError, ValueError):
        # Fallback to simple artist - title
        result = f"{replacements['artist']} - {replacements['title']}"

    return sanitize_filename(result)


# ─── Formatting ──────────────────────────────────────────────────────────────────

def format_duration(seconds) -> str:
    """Format seconds into human-readable duration (M:SS or H:MM:SS)."""
    if not seconds:
        return "0:00"
    seconds = int(seconds)
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    else:
        m = seconds // 60
        s = seconds % 60
        return f"{m}:{s:02d}"


def format_size(bytes_size) -> str:
    """Format bytes into human-readable size string."""
    if not bytes_size or bytes_size <= 0:
        return "—"
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(bytes_size) < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"


def format_bitrate(abr) -> str:
    """Format audio bitrate for display."""
    if not abr or abr <= 0:
        return "—"
    return f"{int(abr)} kbps"


def format_sample_rate(asr) -> str:
    """Format audio sample rate for display."""
    if not asr or asr <= 0:
        return "—"
    if asr >= 1000:
        return f"{asr / 1000:.1f} kHz"
    return f"{int(asr)} Hz"


# ─── FFmpeg Detection ─────────────────────────────────────────────────────────────

def get_ffmpeg_path() -> str | None:
    """
    Find FFmpeg executable. Checks:
    1. System PATH
    2. Bundled with app (for PyInstaller builds)
    3. Common Windows install locations
    """
    # Check system PATH
    path = shutil.which("ffmpeg")
    if path:
        return path

    # Check bundled location (PyInstaller)
    if getattr(sys, "frozen", False):
        bundle_dir = sys._MEIPASS
        bundled = os.path.join(bundle_dir, "ffmpeg.exe")
        if os.path.exists(bundled):
            return bundled

    # Check next to the script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for relative in [
        os.path.join(script_dir, "..", "ffmpeg", "ffmpeg.exe"),
        os.path.join(script_dir, "..", "ffmpeg.exe"),
        os.path.join(script_dir, "ffmpeg.exe"),
    ]:
        if os.path.exists(relative):
            return os.path.abspath(relative)

    # Common Windows locations
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    common_paths = [
        os.path.join(local_appdata, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        os.path.join(local_appdata, "ffmpeg", "bin", "ffmpeg.exe"),
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p

    return None


def check_ffmpeg() -> bool:
    """Check if FFmpeg is available and functional."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def get_ffmpeg_version() -> str:
    """Get FFmpeg version string."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return "Not found"
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode == 0:
            first_line = result.stdout.split("\n")[0]
            # Extract version like "ffmpeg version 6.1.1"
            match = re.search(r"version\s+([\S]+)", first_line)
            return match.group(1) if match else first_line.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return "Unknown"


# ─── Misc ─────────────────────────────────────────────────────────────────────────

def open_folder(path: str):
    """Open a folder in the system file explorer."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def get_ytdlp_version() -> str:
    """Get yt-dlp version string."""
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except (ImportError, AttributeError):
        return "Not installed"
