"""
Downloader Engine — yt-dlp wrapper with intelligent quality detection,
progress callbacks, and cancellation support.
"""

import os
import sys
import threading
import time
import traceback
from pathlib import Path

import yt_dlp

from .utils import sanitize_filename, apply_filename_template, get_ffmpeg_path


# ─── Data Classes ─────────────────────────────────────────────────────────────────

class TrackInfo:
    """Represents a single audio track with all available metadata."""

    def __init__(self, data: dict):
        self.id = data.get("id", "")
        self.title = data.get("title") or data.get("track") or "Unknown"
        self.artist = (
            data.get("artist")
            or data.get("creator")
            or data.get("uploader")
            or "Unknown Artist"
        )
        self.album = data.get("album") or ""
        self.duration = data.get("duration") or 0
        self.thumbnail = data.get("thumbnail") or ""
        self.genre = data.get("genre") or ""
        self.upload_date = data.get("upload_date") or ""
        self.description = data.get("description") or ""
        self.webpage_url = data.get("webpage_url") or data.get("url") or ""
        self.track_number = data.get("playlist_index") or 0

        # Quality info — determined by scanning available formats
        self.best_abr = 0        # Best audio bitrate (kbps)
        self.best_asr = 0        # Best audio sample rate (Hz)
        self.best_acodec = ""    # Best audio codec name
        self.best_abr_format = None
        self._extract_quality_info(data)

        # Source platform
        self.source = self._detect_source()

        # Selection state (for GUI)
        self.selected = True

        # Download status
        self.status = "pending"  # pending, downloading, converting, done, error, skipped
        self.error_message = ""
        self.output_path = ""

    def _extract_quality_info(self, data: dict):
        """Scan all available formats to find the best audio quality."""
        formats = data.get("formats") or []
        for fmt in formats:
            acodec = fmt.get("acodec") or "none"
            if acodec == "none":
                continue

            abr = fmt.get("abr") or 0
            asr = fmt.get("asr") or 0

            if abr > self.best_abr:
                self.best_abr = abr
                self.best_acodec = acodec
                self.best_abr_format = fmt

            if asr > self.best_asr:
                self.best_asr = asr

        # Fallback: if no per-format info, use top-level
        if self.best_abr == 0:
            self.best_abr = data.get("abr") or data.get("tbr") or 0
        if self.best_asr == 0:
            self.best_asr = data.get("asr") or 0

    def _detect_source(self) -> str:
        url = self.webpage_url.lower()
        if "soundcloud" in url:
            return "soundcloud"
        elif "youtube" in url or "youtu.be" in url:
            return "youtube"
        return "unknown"

    @property
    def quality_label(self) -> str:
        """Human-readable quality string for the UI."""
        parts = []
        if self.best_abr > 0:
            parts.append(f"{int(self.best_abr)} kbps")
        if self.best_asr > 0:
            if self.best_asr >= 1000:
                parts.append(f"{self.best_asr / 1000:.1f} kHz")
            else:
                parts.append(f"{int(self.best_asr)} Hz")
        return " · ".join(parts) if parts else "Unknown quality"


class PlaylistInfo:
    """Represents a playlist or collection of tracks."""

    def __init__(self, data: dict, tracks: list[TrackInfo], is_playlist: bool = False):
        self.title = data.get("title") or "Unknown Playlist"
        self.uploader = data.get("uploader") or data.get("channel") or ""
        self.thumbnail = data.get("thumbnail") or ""
        self.webpage_url = data.get("webpage_url") or ""
        self.tracks = tracks
        self.track_count = len(tracks)
        self.total_duration = sum(t.duration for t in tracks if t.duration)
        # True when the URL was an actual playlist/set (drives the
        # per-playlist download subfolder); single tracks stay in the root
        self.is_playlist = is_playlist

    @property
    def source(self) -> str:
        if self.tracks:
            return self.tracks[0].source
        url = self.webpage_url.lower()
        if "soundcloud" in url:
            return "soundcloud"
        return "youtube"


# ─── Downloader ───────────────────────────────────────────────────────────────────

class Downloader:
    """
    Main download engine. Uses yt-dlp to analyze URLs, download tracks,
    and convert to the desired format with intelligent quality selection.
    """

    # SoundCloud rate-limits bursts of API calls with HTTP 403/429 for a few
    # minutes. When that happens, hammering on only makes the block longer —
    # all workers must back off together.
    COOLDOWN_SCHEDULE = [60, 120, 300]  # seconds, per successive retry

    def __init__(self, settings_manager):
        self.settings = settings_manager
        self._cancel_event = threading.Event()
        # base filename -> track id, so two tracks that sanitize to the same
        # name never write to the same file (concurrent downloads would
        # corrupt each other's .part files and one track silently vanishes)
        self._name_registry: dict[str, str] = {}
        self._name_lock = threading.Lock()
        # Shared rate-limit cooldown across all worker threads
        self._cooldown_until = 0.0
        self._cooldown_lock = threading.Lock()

    def cancel(self):
        """Signal all running operations to cancel."""
        self._cancel_event.set()

    def reset_cancel(self):
        """Reset the cancellation signal and per-run state."""
        self._cancel_event.clear()
        with self._name_lock:
            self._name_registry.clear()

    @staticmethod
    def _is_rate_limited(error: Exception) -> bool:
        """Heuristic: does this error look like a temporary API block?"""
        msg = str(error)
        return any(marker in msg for marker in ("403", "429", "Too Many Requests"))

    def _trigger_cooldown(self, seconds: float):
        """Ask every worker to pause until `seconds` from now."""
        with self._cooldown_lock:
            self._cooldown_until = max(
                self._cooldown_until, time.monotonic() + seconds
            )

    def _wait_for_cooldown(self) -> bool:
        """Block while a cooldown is active. Returns False if cancelled."""
        while True:
            with self._cooldown_lock:
                remaining = self._cooldown_until - time.monotonic()
            if remaining <= 0:
                return True
            if self._cancel_event.wait(timeout=min(remaining, 1.0)):
                return False

    def _claim_unique_name(self, base_name: str, track_id: str) -> str:
        """Reserve a filename for this track, suffixing on collision."""
        with self._name_lock:
            candidate = base_name
            counter = 2
            while self._name_registry.get(candidate, track_id) != track_id:
                candidate = f"{base_name} ({counter})"
                counter += 1
            self._name_registry[candidate] = track_id
            return candidate

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ── URL Analysis ──────────────────────────────────────────

    def _base_ydl_opts(self) -> dict:
        """Options shared by analysis and download YoutubeDL instances."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "no_color": True,
            "socket_timeout": 30,
        }

        ffmpeg_path = get_ffmpeg_path()
        if ffmpeg_path:
            opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)

        proxy = self.settings.get("proxy")
        if proxy:
            opts["proxy"] = proxy

        cookies_browser = self.settings.get("youtube_cookies_browser", "none")
        if cookies_browser != "none":
            opts["cookiesfrombrowser"] = (cookies_browser,)

        return opts

    def analyze_url(self, url: str, progress_callback=None) -> PlaylistInfo:
        """
        Extract track/playlist info from a URL without downloading.

        Playlists are analyzed in two phases so the UI gets fast, honest
        progress instead of one long opaque network call:
          1. A flat extraction lists the entries (1-2 API calls).
          2. Full metadata for each entry is resolved concurrently,
             reporting progress per completed track.

        Args:
            url: SoundCloud or YouTube URL
            progress_callback: Optional callable(current, total) for progress

        Returns:
            PlaylistInfo with all tracks and metadata

        Raises:
            Exception on failure
        """
        flat_opts = {
            **self._base_ydl_opts(),
            "extract_flat": "in_playlist",
            "skip_download": True,
            "ignoreerrors": True,
        }

        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            raise Exception(
                "Could not extract info from this URL. "
                "The content may be private, deleted, or geo-restricted."
            )

        if "entries" not in info:
            # Single track — already fully extracted by the flat pass
            track = TrackInfo(info)
            track.track_number = 1
            return PlaylistInfo(
                {"title": track.title, "uploader": track.artist, **info},
                [track],
            )

        entries = [e for e in info["entries"] if e is not None]
        total = len(entries)
        if progress_callback and total:
            progress_callback(0, total)

        # Flat SoundCloud entries carry no title/duration, so resolve full
        # metadata per track. Doing it concurrently is ~4x faster than
        # yt-dlp's sequential playlist extraction and lets us report real
        # progress and honor cancellation between tracks.
        resolve_opts = {
            **self._base_ydl_opts(),
            "skip_download": True,
        }

        def resolve(entry: dict) -> dict:
            entry_url = entry.get("url") or entry.get("webpage_url")
            if not entry_url:
                return entry
            # One retry after a shared cooldown if we hit the rate limit —
            # otherwise a 403 burst turns the whole list into "Unknown" stubs.
            for attempt in range(2):
                if self._cancel_event.is_set() or not self._wait_for_cooldown():
                    return entry
                try:
                    with yt_dlp.YoutubeDL(resolve_opts) as ydl:
                        full = ydl.extract_info(entry_url, download=False)
                    # Keep album info from the flat entry (url_transparent data)
                    if full:
                        for key in ("album", "album_artist"):
                            if entry.get(key) and not full.get(key):
                                full[key] = entry[key]
                        return full
                    return entry
                except Exception as e:
                    if attempt == 0 and self._is_rate_limited(e):
                        self._trigger_cooldown(self.COOLDOWN_SCHEDULE[0])
                        continue
                    return entry  # Fall back to the flat stub
            return entry

        from concurrent.futures import ThreadPoolExecutor, as_completed

        resolved: list[dict | None] = [None] * total
        done = 0
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_index = {
                executor.submit(resolve, entry): i for i, entry in enumerate(entries)
            }
            for future in as_completed(future_to_index):
                i = future_to_index[future]
                try:
                    resolved[i] = future.result()
                except Exception:
                    resolved[i] = entries[i]
                done += 1
                if progress_callback:
                    progress_callback(done, total)
                if self._cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

        tracks = []
        for i, data in enumerate(resolved):
            if data is None:
                continue
            track = TrackInfo(data)
            track.track_number = i + 1
            # Use playlist title as album if track has no album
            if not track.album:
                track.album = info.get("title", "")
            tracks.append(track)

        return PlaylistInfo(info, tracks, is_playlist=True)

    # ── Download ──────────────────────────────────────────────

    def download_track(
        self,
        track: TrackInfo,
        output_dir: str,
        output_format: str,
        quality_settings: dict,
        progress_callback=None,
    ) -> str | None:
        """
        Download a single track and convert to the desired format.

        Args:
            track: TrackInfo with the track to download
            output_dir: Directory to save the output file
            output_format: Target format (mp3, wav, flac, etc.)
            quality_settings: Dict with 'bitrate', 'sample_rate', 'bit_depth' keys
            progress_callback: Optional callable(dict) for progress updates

        Returns:
            Path to the downloaded file, or None on failure
        """
        if self._cancel_event.is_set():
            return None

        preferred_codec = output_format.lower()

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Build the output filename (unique per track, even if two tracks
        # sanitize to the same "Artist - Title")
        template = self.settings.get("filename_template", "{artist} - {title}")
        base_name = apply_filename_template(template, track)
        base_name = self._claim_unique_name(base_name, track.id or track.webpage_url)

        # Check skip existing
        expected_path = os.path.join(output_dir, f"{base_name}.{preferred_codec}")
        if self.settings.get("skip_existing", True) and os.path.exists(expected_path):
            track.status = "skipped"
            track.output_path = expected_path
            if progress_callback:
                progress_callback({"status": "skipped", "progress": 1.0, "speed": 0})
            return expected_path

        # ── yt-dlp options ────────────────────────────────────

        # We use a controlled output template with sanitized name
        outtmpl = os.path.join(output_dir, f"{base_name}.%(ext)s")

        ydl_opts = {
            **self._base_ydl_opts(),
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "overwrites": True,
            "postprocessors": [],
            "writethumbnail": self.settings.get("embed_album_art", True),
        }

        # ── Audio extraction postprocessor ────────────────────

        audio_pp = {
            "key": "FFmpegExtractAudio",
            "preferredcodec": preferred_codec,
        }

        # Intelligent quality: determine the best quality for this specific track
        bitrate = quality_settings.get("bitrate", "auto")
        is_lossy = preferred_codec not in ("wav", "flac", "aiff")

        if is_lossy:
            if bitrate == "auto":
                # Use the source's best bitrate, capped at 320
                if track.best_abr > 0:
                    target_br = min(int(track.best_abr), 320)
                    # Round up to nearest standard bitrate
                    standard_rates = [128, 160, 192, 256, 320]
                    target_br = min(r for r in standard_rates if r >= target_br)
                    audio_pp["preferredquality"] = str(target_br)
                else:
                    audio_pp["preferredquality"] = "0"  # yt-dlp best
            else:
                audio_pp["preferredquality"] = str(bitrate)
        else:
            # Lossless — no bitrate, but use best quality
            audio_pp["preferredquality"] = "0"

        ydl_opts["postprocessors"].append(audio_pp)

        # ── FFmpeg post-processor args ────────────────────────

        pp_args = []

        # Sample rate
        sample_rate = quality_settings.get("sample_rate", "auto")
        if sample_rate == "auto":
            # Use source sample rate if known
            if track.best_asr > 0:
                pp_args.extend(["-ar", str(int(track.best_asr))])
        else:
            pp_args.extend(["-ar", str(sample_rate)])

        # Bit depth (lossless only). FFmpeg has no 24-bit sample_fmt —
        # bit depth must be selected via the PCM codec (pcm_s24le etc.).
        if not is_lossy and preferred_codec in ("wav", "aiff"):
            bit_depth = quality_settings.get("bit_depth", "auto")
            suffix = "le" if preferred_codec == "wav" else "be"
            depth_map = {"16": f"pcm_s16{suffix}", "24": f"pcm_s24{suffix}", "32": f"pcm_s32{suffix}"}
            if bit_depth in depth_map:
                pp_args.extend(["-c:a", depth_map[bit_depth]])
            # 'auto' keeps yt-dlp's default (16-bit) — source audio from
            # SoundCloud/YouTube is 16-bit anyway; padding to 24-bit only
            # inflates file size.

        if pp_args:
            # yt-dlp matches these keys lowercased against the postprocessor
            # key ('extractaudio'), not the class name 'FFmpegExtractAudio'.
            ydl_opts["postprocessor_args"] = {"extractaudio": pp_args}

        # ── Metadata embedding ────────────────────────────────

        ydl_opts["postprocessors"].append({"key": "FFmpegMetadata"})

        # EmbedThumbnail only supports: mp3, mkv/mka, ogg/opus/flac, m4a/mp4
        # WAV, AIFF, WMA, AAC are NOT supported and will crash yt-dlp
        embed_thumb_formats = ("mp3", "ogg", "opus", "flac", "m4a")
        if self.settings.get("embed_album_art", True) and preferred_codec in embed_thumb_formats:
            ydl_opts["postprocessors"].append({"key": "EmbedThumbnail"})
        elif preferred_codec not in embed_thumb_formats:
            # Don't request thumbnail download if we can't embed it
            ydl_opts["writethumbnail"] = False

        # ── Volume normalization ──────────────────────────────

        if self.settings.get("normalize_audio", False):
            # Add loudnorm filter via postprocessor args
            norm_args = ydl_opts.get("postprocessor_args", {})
            existing = norm_args.get("extractaudio", [])
            existing.extend(["-af", "loudnorm=I=-14:TP=-1:LRA=11"])
            norm_args["extractaudio"] = existing
            ydl_opts["postprocessor_args"] = norm_args

        # ── Network settings ─────────────────────────────────
        # (proxy/cookies/socket_timeout come from _base_ydl_opts)

        speed_limit = self.settings.get("download_speed_limit", 0)
        if speed_limit > 0:
            ydl_opts["ratelimit"] = speed_limit * 1024  # KB/s → B/s

        retries = self.settings.get("retry_attempts", 3)
        ydl_opts["retries"] = retries
        ydl_opts["fragment_retries"] = retries
        ydl_opts["extractor_retries"] = retries

        # ── Progress hook ─────────────────────────────────────

        # yt-dlp fires this hook for every downloaded block — on a fast
        # connection that's hundreds of calls per second, which floods the
        # GUI queue and starves the Tk mainloop. Throttle to ~10 updates/sec.
        last_emit = [0.0]

        def progress_hook(d):
            if self._cancel_event.is_set():
                raise yt_dlp.utils.DownloadCancelled("Download cancelled by user")

            if d["status"] == "downloading" and progress_callback:
                now = time.monotonic()
                if now - last_emit[0] < 0.1:
                    return
                last_emit[0] = now
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0
                progress_callback({
                    "status": "downloading",
                    "progress": downloaded / total if total > 0 else 0,
                    "speed": speed,
                    "downloaded": downloaded,
                    "total": total,
                })
            elif d["status"] == "finished" and progress_callback:
                progress_callback({
                    "status": "converting",
                    "progress": 1.0,
                    "speed": 0,
                })

        ydl_opts["progress_hooks"] = [progress_hook]

        # ── Track the final output file ───────────────────────

        final_filepath = [None]

        def post_hook(filepath):
            final_filepath[0] = filepath

        ydl_opts["post_hooks"] = [post_hook]

        # ── Execute download ──────────────────────────────────

        try:
            track.status = "downloading"

            # Retry on rate-limit errors (403/429) with a shared cooldown:
            # SoundCloud blocks bursts of requests for a few minutes, which
            # otherwise makes every remaining track in the playlist fail
            # instantly ("stops working mid-playlist").
            max_attempts = 1 + max(0, int(self.settings.get("retry_attempts", 3)))
            for attempt in range(max_attempts):
                if not self._wait_for_cooldown():
                    raise yt_dlp.utils.DownloadCancelled("Download cancelled by user")
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([track.webpage_url])
                    break
                except yt_dlp.utils.DownloadCancelled:
                    raise
                except Exception as e:
                    is_last = attempt >= max_attempts - 1
                    if not self._is_rate_limited(e) or is_last:
                        raise
                    schedule = self.COOLDOWN_SCHEDULE
                    cooldown = schedule[min(attempt, len(schedule) - 1)]
                    self._trigger_cooldown(cooldown)
                    if progress_callback:
                        progress_callback({
                            "status": "waiting",
                            "progress": 0,
                            "speed": 0,
                            "retry_in": cooldown,
                            "attempt": attempt + 1,
                        })

            # Determine output file
            output_file = final_filepath[0]
            if output_file and os.path.exists(output_file):
                track.status = "done"
                track.output_path = output_file
                if progress_callback:
                    progress_callback({"status": "done", "progress": 1.0, "speed": 0})
                return output_file

            # Fallback: search for the file
            for ext in [preferred_codec, "mp3", "wav", "flac", "ogg", "m4a", "opus"]:
                candidate = os.path.join(output_dir, f"{base_name}.{ext}")
                if os.path.exists(candidate):
                    track.status = "done"
                    track.output_path = candidate
                    if progress_callback:
                        progress_callback({"status": "done", "progress": 1.0, "speed": 0})
                    return candidate

            raise FileNotFoundError("Downloaded file not found after processing")

        except yt_dlp.utils.DownloadCancelled:
            track.status = "error"
            track.error_message = "Cancelled"
            return None

        except Exception as e:
            track.status = "error"
            track.error_message = str(e)
            if progress_callback:
                try:
                    progress_callback({
                        "status": "error",
                        "progress": 0,
                        "speed": 0,
                        "error": str(e),
                    })
                except Exception:
                    pass
            # Use ascii-safe printing to avoid UnicodeEncodeError on Windows cp1252
            try:
                print(f"Error downloading '{track.title}': {e}")
            except UnicodeEncodeError:
                safe_title = track.title.encode('ascii', errors='replace').decode('ascii')
                print(f"Error downloading '{safe_title}': {e}")
            traceback.print_exc()
            return None

        finally:
            # Clean up thumbnail files left by yt-dlp
            self._cleanup_thumbnails(output_dir, base_name)

    # ── Helpers ───────────────────────────────────────────────

    def _cleanup_thumbnails(self, directory: str, base_name: str):
        """Remove leftover thumbnail files from yt-dlp."""
        for ext in [".jpg", ".png", ".webp", ".jpeg"]:
            thumb_path = os.path.join(directory, f"{base_name}{ext}")
            if os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except OSError:
                    pass

    def get_output_dir(
        self,
        track: TrackInfo | None = None,
        playlist_title: str | None = None,
    ) -> str:
        """
        Get the output directory. When a playlist title is given (and the
        setting is enabled), tracks go into a subfolder named after the
        playlist; artist/album organization nests inside it.
        """
        base_dir = self.settings.get("download_dir", str(Path.home() / "Music"))

        if playlist_title and self.settings.get("playlist_subfolder", True):
            base_dir = os.path.join(base_dir, sanitize_filename(playlist_title))

        if self.settings.get("organize_by_artist", False) and track:
            artist_dir = sanitize_filename(track.artist)
            if track.album:
                album_dir = sanitize_filename(track.album)
                return os.path.join(base_dir, artist_dir, album_dir)
            return os.path.join(base_dir, artist_dir)

        return base_dir
