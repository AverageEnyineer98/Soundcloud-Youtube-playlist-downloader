"""
PyInstaller Build Script — Creates a standalone .exe for SoundWave Downloader.

Usage:
    python build.py

This script:
1. Generates a PyInstaller .spec file
2. Builds the standalone executable
3. The resulting .exe will be in the dist/ directory

Notes:
- FFmpeg must be bundled separately or installed by the user
- To bundle FFmpeg, place ffmpeg.exe in a 'ffmpeg/' directory and uncomment the datas line
"""

import os
import sys
import subprocess
import shutil

# Windows consoles often use cp1252, which can't print emoji — don't let a
# status message crash the build script after a successful build
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def build():
    """Build the standalone executable."""
    print("=" * 60)
    print("  SoundWave Downloader — Build Script")
    print("=" * 60)
    print()

    # Check PyInstaller
    try:
        import PyInstaller
        print(f"[BUILD] PyInstaller version: {PyInstaller.__version__}")
    except ImportError:
        print("[BUILD] ERROR: PyInstaller not installed.")
        print("[BUILD] Install with: pip install pyinstaller")
        sys.exit(1)

    # Check for icon
    icon_path = os.path.join("assets", "icon.ico")
    icon_arg = f"--icon={icon_path}" if os.path.exists(icon_path) else ""

    # customtkinter ships .json theme files and assets that PyInstaller's
    # import analysis misses — without them the frozen exe crashes on launch
    import customtkinter
    ctk_path = os.path.dirname(customtkinter.__file__)

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                      # Single .exe file
        "--windowed",                     # No console window
        "--name", "SoundWave Downloader",
        "--add-data", f"app;app",         # Include app package
        "--add-data", f"{ctk_path};customtkinter",
        "--noconfirm",                    # Overwrite previous build output
    ]

    if icon_arg:
        cmd.append(icon_arg)

    # Bundle FFmpeg if available
    ffmpeg_dir = os.path.join("ffmpeg")
    if os.path.exists(ffmpeg_dir):
        cmd.extend(["--add-binary", f"{ffmpeg_dir};ffmpeg"])
        print(f"[BUILD] Bundling FFmpeg from: {ffmpeg_dir}")

    # Hidden imports for yt-dlp (it uses dynamic imports)
    hidden_imports = [
        "yt_dlp",
        "mutagen",
        "mutagen.mp3",
        "mutagen.id3",
        "mutagen.flac",
        "mutagen.oggvorbis",
        "mutagen.mp4",
        "mutagen.wave",
        "mutagen.aiff",
        "mutagen.oggopus",
        "customtkinter",
        "PIL",
        "PIL.Image",
        "requests",
    ]
    for imp in hidden_imports:
        cmd.extend(["--hidden-import", imp])

    # Add the main script
    cmd.append("main.py")

    print(f"[BUILD] Command: {' '.join(cmd)}")
    print()
    print("[BUILD] Building... This may take a few minutes.")
    print()

    # Run PyInstaller
    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

    if result.returncode == 0:
        dist_path = os.path.join("dist", "SoundWave Downloader.exe")
        if os.path.exists(dist_path):
            size_mb = os.path.getsize(dist_path) / (1024 * 1024)
            print()
            print("=" * 60)
            print(f"  ✅ Build successful!")
            print(f"  📦 Output: {os.path.abspath(dist_path)}")
            print(f"  📏 Size: {size_mb:.1f} MB")
            print("=" * 60)
        else:
            print("[BUILD] ✅ Build completed but exe not found at expected path.")
            print("[BUILD] Check the dist/ directory.")
    else:
        print()
        print("[BUILD] ❌ Build failed! Check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    build()
