"""
SoundWave Downloader — Entry Point
A modern audio downloader for SoundCloud and YouTube with intelligent quality selection.
"""

import sys
import os
import threading
import traceback


def check_and_update_ytdlp():
    """Check for yt-dlp updates in a background thread."""
    def updater():
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True,
                text=True,
                timeout=180,  # pip regularly needs >30s; a short timeout made every check "fail"
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode == 0 and "Successfully installed" in result.stdout:
                print(f"[SoundWave] yt-dlp updated: {result.stdout.strip().split()[-1]}")
            else:
                print("[SoundWave] yt-dlp is up to date.")
        except Exception as e:
            print(f"[SoundWave] Auto-update check failed: {e}")

    thread = threading.Thread(target=updater, daemon=True)
    thread.start()


def check_dependencies():
    """Verify that all required dependencies are installed."""
    missing = []

    try:
        import yt_dlp
    except ImportError:
        missing.append("yt-dlp")

    try:
        import customtkinter
    except ImportError:
        missing.append("customtkinter")

    try:
        import mutagen
    except ImportError:
        missing.append("mutagen")

    try:
        from PIL import Image
    except ImportError:
        missing.append("Pillow")

    if missing:
        print(f"[SoundWave] Missing dependencies: {', '.join(missing)}")
        print(f"[SoundWave] Install them with: pip install {' '.join(missing)}")
        return False

    return True


def main():
    """Main entry point for SoundWave Downloader."""
    # Ensure dependencies are available
    if not check_dependencies():
        print("\n[SoundWave] ERROR: Missing required packages. Run:")
        print("  pip install -r requirements.txt")
        input("\nPress Enter to exit...")
        sys.exit(1)

    # Import app components (after dependency check)
    from app.settings import SettingsManager
    from app.gui import App
    from app.utils import check_ffmpeg

    # Initialize settings
    settings = SettingsManager()

    # Check FFmpeg
    if not check_ffmpeg():
        print("[SoundWave] WARNING: FFmpeg not found in PATH.")
        print("[SoundWave] Audio conversion features require FFmpeg.")
        print("[SoundWave] Download FFmpeg from: https://ffmpeg.org/download.html")
        print("[SoundWave] Or install via: winget install ffmpeg")
        print()

    # Auto-update yt-dlp if enabled
    if settings.get("auto_update_ytdlp", True):
        # Only auto-update when running from source (not frozen exe)
        if not getattr(sys, "frozen", False):
            check_and_update_ytdlp()

    # Launch the GUI
    try:
        app = App(settings_manager=settings)
        app.mainloop()
    except Exception as e:
        print(f"\n[SoundWave] Fatal error: {e}")
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    main()
