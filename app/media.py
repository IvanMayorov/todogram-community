"""Downloading files from Telegram and cutting audio / frames from video with ffmpeg."""
import asyncio
import io
import logging
import shutil
import tempfile
from pathlib import Path

from aiogram import Bot

log = logging.getLogger(__name__)

TG_LIMIT = 20 * 1024 * 1024  # Bot API getFile limit
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


async def download(bot: Bot, file_id: str) -> bytes:
    buf = io.BytesIO()
    await bot.download(file_id, destination=buf)
    return buf.getvalue()


def prune_cache(cache_dir: str, days: int) -> tuple[int, int]:
    """Remove cached files not opened for `days` (and leftovers of interrupted downloads). (files, bytes) removed."""
    import time
    d = Path(cache_dir)
    if not d.is_dir():
        return 0, 0
    cutoff, n, size = time.time() - days * 86400, 0, 0
    for f in d.iterdir():
        try:
            st = f.stat()
            if f.is_file() and (st.st_mtime < cutoff or (f.name.endswith(".part") and st.st_mtime < time.time() - 3600)):
                f.unlink()
                n, size = n + 1, size + st.st_size
        except OSError:
            pass
    return n, size


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-400:])
    return out


async def video_audio_and_frames(data: bytes, frames: int = 3) -> tuple[bytes | None, list[bytes]]:
    """Return (mp3 audio or None if no sound, list of JPEG frames)."""
    if not HAS_FFMPEG:
        return None, []
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.mp4"
        src.write_bytes(data)
        try:
            dur = float((await _run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(src))).decode().strip() or 0)
        except Exception:
            dur = 0
        shots = []
        for k in range(frames):
            t = dur * (k + 1) / (frames + 1) if dur else k
            out = Path(d) / f"f{k}.jpg"
            try:
                await _run("ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                           "-vf", "scale='min(1024,iw)':-2", "-q:v", "4", "-y", str(out))
                shots.append(out.read_bytes())
            except Exception as e:
                log.info("frame %s failed: %s", k, e)
        audio = None
        a = Path(d) / "a.mp3"
        try:
            await _run("ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", "-y", str(a))
            if a.exists() and a.stat().st_size > 1000:
                audio = a.read_bytes()
        except Exception:
            pass  # GIFs and silent videos have no audio track
        return audio, shots
