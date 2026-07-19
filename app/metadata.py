"""
Metadata Handler — Post-download metadata verification and tagging.
Uses mutagen to read/write tags for various audio formats.
yt-dlp handles most metadata embedding; this module fills in gaps.
"""

import os
import traceback
from pathlib import Path

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import (
        ID3, TIT2, TPE1, TALB, TCON, TDRC, TRCK, COMM, TBPM, APIC, ID3NoHeaderError
    )
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.wave import WAVE
    from mutagen.aiff import AIFF
    from mutagen.oggopus import OggOpus
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


class MetadataHandler:
    """
    Handles post-download metadata verification and enrichment.
    Fills in any metadata that yt-dlp may have missed.
    """

    # Map of file extensions to handler methods
    FORMAT_HANDLERS = {}

    def __init__(self):
        if HAS_MUTAGEN:
            self.FORMAT_HANDLERS = {
                ".mp3": self._tag_mp3,
                ".flac": self._tag_flac,
                ".ogg": self._tag_ogg,
                ".opus": self._tag_opus,
                ".m4a": self._tag_m4a,
                ".aac": self._tag_m4a,
                ".wav": self._tag_wav,
                ".aiff": self._tag_aiff,
                ".aif": self._tag_aiff,
            }

    def tag_file(self, filepath: str, track_info) -> bool:
        """
        Apply/verify metadata tags on a downloaded audio file.

        Args:
            filepath: Path to the audio file
            track_info: TrackInfo object with metadata

        Returns:
            True if tagging succeeded, False otherwise
        """
        if not HAS_MUTAGEN:
            print("Warning: mutagen not installed, skipping metadata tagging")
            return False

        if not os.path.exists(filepath):
            print(f"Warning: File not found for tagging: {filepath}")
            return False

        ext = Path(filepath).suffix.lower()
        handler = self.FORMAT_HANDLERS.get(ext)

        if handler is None:
            print(f"Warning: No metadata handler for format: {ext}")
            return False

        try:
            handler(filepath, track_info)
            return True
        except Exception as e:
            print(f"Warning: Failed to tag '{filepath}': {e}")
            traceback.print_exc()
            return False

    # ── Format-specific taggers ───────────────────────────────

    def _tag_mp3(self, filepath: str, track):
        """Tag MP3 files using ID3v2."""
        try:
            audio = MP3(filepath, ID3=ID3)
        except ID3NoHeaderError:
            audio = MP3(filepath)
            audio.add_tags()

        tags = audio.tags
        if tags is None:
            audio.add_tags()
            tags = audio.tags

        # Only set tags that aren't already present
        if not tags.get("TIT2"):
            tags.add(TIT2(encoding=3, text=track.title))
        if not tags.get("TPE1"):
            tags.add(TPE1(encoding=3, text=track.artist))
        if not tags.get("TALB") and track.album:
            tags.add(TALB(encoding=3, text=track.album))
        if not tags.get("TCON") and track.genre:
            tags.add(TCON(encoding=3, text=track.genre))
        if not tags.get("TDRC") and track.upload_date:
            year = track.upload_date[:4] if len(track.upload_date) >= 4 else track.upload_date
            tags.add(TDRC(encoding=3, text=year))
        if not tags.get("TRCK") and track.track_number:
            tags.add(TRCK(encoding=3, text=str(track.track_number)))
        if not tags.getall("COMM") and track.description:
            # Truncate very long descriptions
            desc = track.description[:500] if len(track.description) > 500 else track.description
            tags.add(COMM(encoding=3, lang="eng", desc="", text=desc))

        audio.save()

    def _tag_flac(self, filepath: str, track):
        """Tag FLAC files using Vorbis Comments."""
        audio = FLAC(filepath)

        if not audio.get("title"):
            audio["title"] = track.title
        if not audio.get("artist"):
            audio["artist"] = track.artist
        if not audio.get("album") and track.album:
            audio["album"] = track.album
        if not audio.get("genre") and track.genre:
            audio["genre"] = track.genre
        if not audio.get("date") and track.upload_date:
            audio["date"] = track.upload_date[:4]
        if not audio.get("tracknumber") and track.track_number:
            audio["tracknumber"] = str(track.track_number)
        if not audio.get("comment") and track.description:
            audio["comment"] = track.description[:500]

        audio.save()

    def _tag_ogg(self, filepath: str, track):
        """Tag OGG Vorbis files."""
        audio = OggVorbis(filepath)

        if not audio.get("title"):
            audio["title"] = [track.title]
        if not audio.get("artist"):
            audio["artist"] = [track.artist]
        if not audio.get("album") and track.album:
            audio["album"] = [track.album]
        if not audio.get("genre") and track.genre:
            audio["genre"] = [track.genre]
        if not audio.get("date") and track.upload_date:
            audio["date"] = [track.upload_date[:4]]
        if not audio.get("tracknumber") and track.track_number:
            audio["tracknumber"] = [str(track.track_number)]

        audio.save()

    def _tag_opus(self, filepath: str, track):
        """Tag OGG Opus files."""
        audio = OggOpus(filepath)

        if not audio.get("title"):
            audio["title"] = [track.title]
        if not audio.get("artist"):
            audio["artist"] = [track.artist]
        if not audio.get("album") and track.album:
            audio["album"] = [track.album]
        if not audio.get("genre") and track.genre:
            audio["genre"] = [track.genre]
        if not audio.get("date") and track.upload_date:
            audio["date"] = [track.upload_date[:4]]
        if not audio.get("tracknumber") and track.track_number:
            audio["tracknumber"] = [str(track.track_number)]

        audio.save()

    def _tag_m4a(self, filepath: str, track):
        """Tag M4A/AAC files using MP4 atoms."""
        audio = MP4(filepath)
        tags = audio.tags
        if tags is None:
            audio.add_tags()
            tags = audio.tags

        if not tags.get("\xa9nam"):
            tags["\xa9nam"] = [track.title]
        if not tags.get("\xa9ART"):
            tags["\xa9ART"] = [track.artist]
        if not tags.get("\xa9alb") and track.album:
            tags["\xa9alb"] = [track.album]
        if not tags.get("\xa9gen") and track.genre:
            tags["\xa9gen"] = [track.genre]
        if not tags.get("\xa9day") and track.upload_date:
            tags["\xa9day"] = [track.upload_date[:4]]
        if not tags.get("trkn") and track.track_number:
            tags["trkn"] = [(track.track_number, 0)]
        if not tags.get("\xa9cmt") and track.description:
            tags["\xa9cmt"] = [track.description[:500]]

        audio.save()

    def _tag_wav(self, filepath: str, track):
        """Tag WAV files using ID3 (limited support)."""
        try:
            audio = WAVE(filepath)
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags

            if not tags.get("TIT2"):
                tags.add(TIT2(encoding=3, text=track.title))
            if not tags.get("TPE1"):
                tags.add(TPE1(encoding=3, text=track.artist))
            if not tags.get("TALB") and track.album:
                tags.add(TALB(encoding=3, text=track.album))
            if not tags.get("TCON") and track.genre:
                tags.add(TCON(encoding=3, text=track.genre))
            if not tags.get("TRCK") and track.track_number:
                tags.add(TRCK(encoding=3, text=str(track.track_number)))

            audio.save()
        except Exception:
            # WAV tagging is flaky, don't fail the whole download
            pass

    def _tag_aiff(self, filepath: str, track):
        """Tag AIFF files using ID3."""
        try:
            audio = AIFF(filepath)
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags

            if not tags.get("TIT2"):
                tags.add(TIT2(encoding=3, text=track.title))
            if not tags.get("TPE1"):
                tags.add(TPE1(encoding=3, text=track.artist))
            if not tags.get("TALB") and track.album:
                tags.add(TALB(encoding=3, text=track.album))
            if not tags.get("TCON") and track.genre:
                tags.add(TCON(encoding=3, text=track.genre))
            if not tags.get("TRCK") and track.track_number:
                tags.add(TRCK(encoding=3, text=str(track.track_number)))

            audio.save()
        except Exception:
            pass


def verify_metadata(filepath: str) -> dict:
    """
    Read and return existing metadata from an audio file.
    Useful for verification after download.
    """
    if not HAS_MUTAGEN:
        return {}

    ext = Path(filepath).suffix.lower()
    result = {
        "title": "",
        "artist": "",
        "album": "",
        "genre": "",
        "date": "",
        "track_number": "",
        "has_cover_art": False,
    }

    try:
        if ext == ".mp3":
            audio = MP3(filepath, ID3=ID3)
            tags = audio.tags or {}
            result["title"] = str(tags.get("TIT2", ""))
            result["artist"] = str(tags.get("TPE1", ""))
            result["album"] = str(tags.get("TALB", ""))
            result["genre"] = str(tags.get("TCON", ""))
            result["date"] = str(tags.get("TDRC", ""))
            result["track_number"] = str(tags.get("TRCK", ""))
            result["has_cover_art"] = bool(tags.getall("APIC"))

        elif ext == ".flac":
            audio = FLAC(filepath)
            result["title"] = (audio.get("title") or [""])[0]
            result["artist"] = (audio.get("artist") or [""])[0]
            result["album"] = (audio.get("album") or [""])[0]
            result["genre"] = (audio.get("genre") or [""])[0]
            result["date"] = (audio.get("date") or [""])[0]
            result["track_number"] = (audio.get("tracknumber") or [""])[0]
            result["has_cover_art"] = bool(audio.pictures)

        elif ext in (".m4a", ".aac"):
            audio = MP4(filepath)
            tags = audio.tags or {}
            result["title"] = (tags.get("\xa9nam") or [""])[0]
            result["artist"] = (tags.get("\xa9ART") or [""])[0]
            result["album"] = (tags.get("\xa9alb") or [""])[0]
            result["genre"] = (tags.get("\xa9gen") or [""])[0]
            result["date"] = (tags.get("\xa9day") or [""])[0]
            trkn = tags.get("trkn")
            result["track_number"] = str(trkn[0][0]) if trkn else ""
            result["has_cover_art"] = bool(tags.get("covr"))

    except Exception:
        pass

    return result
