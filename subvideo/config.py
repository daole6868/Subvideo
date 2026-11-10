"""Application-wide settings (API keys, tool paths, default engines).

Stored in %APPDATA%/SubVideo/settings.json on Windows (~/.config/subvideo on
Linux/macOS).  Keys are stored locally in plain JSON — this is a personal desktop
tool; never commit that file.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent  # repository / install root


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        d = base / "SubVideo"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        d = base / "subvideo"
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "device": "auto",  # auto | cuda | cpu
    "ffmpeg_dir": "",  # folder containing ffmpeg(.exe)/ffprobe(.exe); empty = bin/ or PATH
    "mpv_dir": "",  # folder containing libmpv-2.dll
    "rubberband_path": "",
    "models_dir": "",  # cache folder for downloaded models (empty = library default)
    "hf_token": "",
    "projects_dir": "",
    "recent_projects": [],
    "providers": {
        "anthropic": {"api_key": "", "model": "claude-opus-5-5", "effort": "medium"},
        "openai": {"api_key": "", "model": "gpt-5.1", "base_url": ""},
        "gemini": {"api_key": "", "model": "gemini-2.5-flash"},
        "deepseek": {"api_key": "", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
        "openrouter": {"api_key": "", "model": "", "base_url": "https://openrouter.ai/api/v1"},
        "ollama": {"api_key": "ollama", "model": "qwen3:14b", "base_url": "http://localhost:11434/v1"},
        "custom": {"api_key": "", "model": "", "base_url": "http://localhost:8000/v1"},
    },
    "translate_provider": "gemini",
    "qa_provider": "",  # empty = same as translate
    "defaults": {
        "separator": "audio_separator",
        "separator_model": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "dereverb_model": "",
        "asr": "funasr",
        "diarize": "funasr",
        "vad": "silero",
        "ocr": "rapidocr",
        "tts": "omnivoice",
    },
    "python_qwen": "",  # optional separate Python (venv) for Qwen3-ASR (needs transformers 4.x)
    "python_tts": "",  # optional separate Python (venv) for TTS engines
    "dict_dir": "",  # folder with Hán-Việt dictionaries (Names.txt, VietPhrase.txt, ChinesePhienAmWords.txt)
}


class Settings:
    _instance: "Settings | None" = None

    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "settings.json")
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    @classmethod
    def get(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = Settings()
        return cls._instance

    def load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                _deep_merge(self.data, stored)
            except Exception:
                pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def __getitem__(self, key: str) -> Any:
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def provider(self, name: str) -> dict:
        return self.data.setdefault("providers", {}).setdefault(name, {})

    def default(self, key: str) -> Any:
        return self.data.get("defaults", {}).get(key, DEFAULTS["defaults"].get(key))

    def add_recent(self, path: str) -> None:
        rec = [p for p in self.data.get("recent_projects", []) if p != path]
        rec.insert(0, path)
        self.data["recent_projects"] = rec[:15]
        self.save()

    def device(self) -> str:
        dev = self["device"]
        if dev != "auto":
            return dev
        try:
            import torch  # noqa

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def apply_env(self) -> None:
        """Expose settings to libraries via environment variables."""
        if self["hf_token"]:
            os.environ.setdefault("HF_TOKEN", self["hf_token"])
        if self["models_dir"]:
            md = self["models_dir"]
            os.environ.setdefault("HF_HOME", str(Path(md) / "huggingface"))
            os.environ.setdefault("MODELSCOPE_CACHE", str(Path(md) / "modelscope"))
            os.environ.setdefault("TORCH_HOME", str(Path(md) / "torch"))
        for d in (self["ffmpeg_dir"], self["mpv_dir"], str(APP_DIR / "bin")):
            if d and Path(d).is_dir():
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(str(d))
                    except OSError:
                        pass


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
