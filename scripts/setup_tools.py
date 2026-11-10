"""Download FFmpeg (with libass + librubberband) and libmpv into ./bin (Windows).

Usage:  python scripts/setup_tools.py
If a download fails, follow the printed manual instructions.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
MPV_API = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "SubVideo-setup"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def ffmpeg() -> bool:
    if (BIN / "ffmpeg.exe").exists() and (BIN / "ffprobe.exe").exists():
        print("✔ FFmpeg đã có")
        return True
    print("Đang tải FFmpeg (~150 MB)…")
    try:
        data = _get(FFMPEG_URL)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                base = name.rsplit("/", 1)[-1]
                if base in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(name) as src, open(BIN / base, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        print("✔ Đã cài FFmpeg vào bin/")
        return True
    except Exception as e:
        print(f"✖ Không tải được FFmpeg: {e}")
        print("  Tự tải bản 'win64-gpl' tại https://github.com/BtbN/FFmpeg-Builds/releases rồi chép ffmpeg.exe, "
              "ffprobe.exe vào thư mục bin/")
        return False


def mpv() -> bool:
    if (BIN / "libmpv-2.dll").exists():
        print("✔ libmpv đã có")
        return True
    print("Đang tải libmpv (trình phát xem trước giống hệt bản xuất)…")
    try:
        rel = json.loads(_get(MPV_API))
        assets = [a for a in rel.get("assets", []) if a["name"].startswith("mpv-dev-x86_64") and a["name"].endswith(".7z")]
        assets.sort(key=lambda a: ("v3" in a["name"], a["name"]))  # prefer the baseline (non-v3) build
        if not assets:
            raise RuntimeError("không thấy bản mpv-dev-x86_64")
        data = _get(assets[0]["browser_download_url"])
        import py7zr

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mpv.7z"
            p.write_bytes(data)
            with py7zr.SevenZipFile(p, "r") as z:
                z.extractall(td)
            dll = next(Path(td).rglob("libmpv-2.dll"), None)
            if dll is None:
                raise RuntimeError("không thấy libmpv-2.dll trong gói")
            shutil.copy2(dll, BIN / "libmpv-2.dll")
        print("✔ Đã cài libmpv-2.dll vào bin/")
        return True
    except Exception as e:
        print(f"✖ Không tải được libmpv: {e}")
        print("  Tự tải 'mpv-dev-x86_64-*.7z' tại https://github.com/shinchiro/mpv-winbuild-cmake/releases, giải nén lấy "
              "libmpv-2.dll chép vào bin/. Không có mpv, SubVideo vẫn chạy với trình phát dự phòng của Qt.")
        return False


def main() -> int:
    BIN.mkdir(exist_ok=True)
    if not sys.platform.startswith("win"):
        print("Script này dành cho Windows. Trên Linux/macOS hãy cài ffmpeg, mpv, rubberband bằng trình quản lý gói.")
        return 0
    ok = ffmpeg()
    mpv()
    print("\n(Tuỳ chọn) Rubber Band R3 cho co giãn giọng chất lượng cao nhất: tải bản Windows tại "
          "https://breakfastquay.com/rubberband/ rồi chép rubberband.exe vào bin/.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
