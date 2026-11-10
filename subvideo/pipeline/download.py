"""Multi-platform download via yt-dlp (YouTube, Bilibili, TikTok, Douyin, Facebook...).

Content protected by DRM (paid iQIYI/Youku/WeTV/Mango episodes) cannot be
downloaded and is intentionally not supported.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core import media
from ..core.jobs import Cancelled, JobContext
from ..core.project import Project


def download(project: Project, url: str, ctx: JobContext, cookies_browser: str = "", cookie_file: str = "",
             max_height: int = 0, subtitles: bool = True) -> Path:
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError("Chưa cài yt-dlp (pip install -U yt-dlp)") from e

    out_dir = project.path("source")
    out_dir.mkdir(parents=True, exist_ok=True)

    def hook(d):
        if ctx.cancelled():
            raise Cancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            if total:
                ctx.progress(min(0.95, got / total), f"Đang tải: {got / 1e6:.1f}/{total / 1e6:.1f} MB")
        elif d.get("status") == "finished":
            ctx.progress(0.96, "Đang ghép hình + tiếng")

    fmt = "bv*+ba/b"
    if max_height:
        fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    opts = {
        "format": fmt,
        "merge_output_format": "mkv",
        "outtmpl": str(out_dir / "%(title).80B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "concurrent_fragment_downloads": 4,
        "retries": 5,
    }
    try:
        opts["ffmpeg_location"] = str(Path(media.ffmpeg()).parent)
    except FileNotFoundError:
        pass
    js = _js_runtime()
    if js:
        opts["js_runtimes"] = js  # YouTube needs a JavaScript runtime (Deno or Node.js)
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if subtitles:
        opts.update({
            "writesubtitles": True,
            "writeautomaticsub": False,
            "subtitleslangs": ["zh.*", "zh-Hans", "zh-CN", "zh-Hant", "ai-zh", "vi", "vi.*"],
            "subtitlesformat": "srt/vtt/ass/best",
        })
    ctx.progress(0.0, "Đang lấy thông tin video…", force=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("Không lấy được video từ đường dẫn này.")
        if "entries" in info and info["entries"]:
            info = info["entries"][0]
        path = None
        for rd in info.get("requested_downloads") or []:
            if rd.get("filepath"):
                path = rd["filepath"]
        if not path:
            path = ydl.prepare_filename(info)
            p = Path(path)
            if not p.exists():
                for ext in (".mkv", ".mp4", ".webm"):
                    if p.with_suffix(ext).exists():
                        path = str(p.with_suffix(ext))
                        break
    path = Path(path)
    if not path.exists():
        raise RuntimeError("Tải xong nhưng không tìm thấy file video.")
    project.source_url = url
    project.media = media.probe(path)
    project.media.path = project.rel(path)
    if not project.name or project.name == project.root.name:
        project.name = info.get("title", project.name)[:80]
    subs = sorted(out_dir.glob(path.stem + "*.srt")) + sorted(out_dir.glob(path.stem + "*.vtt")) + sorted(out_dir.glob(path.stem + "*.ass"))
    if subs:
        project.set_param("source", "downloaded_subs", [project.rel(s) for s in subs])
        ctx.log(f"Đã tải kèm {len(subs)} file phụ đề có sẵn: " + ", ".join(s.name for s in subs))
    ctx.progress(1.0, "Tải xong", force=True)
    return path


def _js_runtime() -> dict | None:
    from ..config import APP_DIR

    for name in ("deno", "node"):
        local = APP_DIR / "bin" / (name + ".exe")
        if local.exists():
            return {name: {"path": str(local)}}
        if shutil.which(name):
            return {name: {}}
    return None


def import_local(project: Project, file_path: str, copy: bool = False) -> Path:
    src = Path(file_path)
    if not src.exists():
        raise FileNotFoundError(file_path)
    if copy:
        dst = project.path("source", src.name)
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        src = dst
    project.media = media.probe(src)
    project.media.path = project.rel(src)
    return src
