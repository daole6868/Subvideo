"""Application settings: API keys, tool paths, AI libraries."""

from __future__ import annotations

import importlib.util
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QFormLayout, QHeaderView, QLineEdit, QMessageBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ...pipeline.llm import PROVIDERS
from ..theme import apply_theme
from ..widgets.common import PathEdit, button, combo, combo_value, hint, hrow, primary, set_combo
from .base import BasePage

LIBS = [
    ("Tách giọng (UVR/RoFormer)", "audio_separator", "pip install \"audio-separator[gpu]\""),
    ("Nhận dạng FunASR / SenseVoice / CAM++", "funasr", "pip install funasr modelscope"),
    ("VAD Silero", "silero_vad", "pip install silero-vad"),
    ("Qwen3-ASR (môi trường riêng)", "qwen_asr", "pip install qwen-asr  (venv riêng: cần transformers 4.57)"),
    ("faster-whisper", "faster_whisper", "pip install faster-whisper"),
    ("pyannote (phân biệt người nói)", "pyannote.audio", "pip install pyannote.audio"),
    ("RapidOCR", "rapidocr", "pip install rapidocr onnxruntime"),
    ("PaddleOCR", "paddleocr", "pip install paddleocr paddlepaddle-gpu"),
    ("OmniVoice", "omnivoice", "pip install omnivoice"),
    ("VieNeu-TTS", "vieneu", "pip install vieneu"),
    ("Edge TTS", "edge_tts", "pip install edge-tts"),
    ("yt-dlp", "yt_dlp", "pip install -U yt-dlp"),
    ("Claude (Anthropic SDK)", "anthropic", "pip install anthropic"),
    ("OpenAI / DeepSeek / Ollama", "openai", "pip install openai"),
    ("Gemini", "google.genai", "pip install google-genai"),
    ("Trình phát mpv (python-mpv)", "mpv", "pip install mpv  + libmpv-2.dll trong bin/"),
    ("PyTorch", "torch", "xem install_windows.bat (bản CUDA)"),
]


def _installed(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


class SettingsPage(BasePage):
    title = "Cài đặt"
    icon = "⚙️"
    subtitle = "Khoá API, đường dẫn công cụ, thư viện AI. Lưu tại máy của bạn (không gửi đi đâu)."

    def build(self):
        tabs = QTabWidget()
        # --- API
        api = QWidget()
        al = QVBoxLayout(api)
        top = QFormLayout()
        self.default_provider = combo(PROVIDERS)
        top.addRow("Dịch mặc định bằng:", self.default_provider)
        al.addLayout(top)
        self.prov_widgets: dict[str, dict] = {}
        ptabs = QTabWidget()
        for key, label in PROVIDERS:
            w = QWidget()
            f = QFormLayout(w)
            d = {"api_key": QLineEdit(), "model": QLineEdit()}
            d["api_key"].setEchoMode(QLineEdit.Password)
            f.addRow("Khoá API:", d["api_key"])
            f.addRow("Model:", d["model"])
            if key not in ("anthropic", "gemini"):
                d["base_url"] = QLineEdit()
                f.addRow("Địa chỉ máy chủ (base URL):", d["base_url"])
            if key == "anthropic":
                d["effort"] = combo([("low", "low"), ("medium", "medium (mặc định)"), ("high", "high"), ("xhigh", "xhigh")])
                f.addRow("Mức suy nghĩ (effort):", d["effort"])
            b = button("Kiểm tra kết nối")
            b.clicked.connect(lambda _=False, k=key: self._ping(k))
            f.addRow("", b)
            self.prov_widgets[key] = d
            ptabs.addTab(w, label)
        al.addWidget(ptabs)
        al.addWidget(hint("Gợi ý: Gemini / DeepSeek rẻ, hiểu tiếng Trung tốt; Claude / OpenAI giữ văn phong và xưng hô ổn định. "
                          "Hãy thử vài model trên cùng một tập rồi chọn. Ollama chạy model trên máy (cần GPU mạnh)."))
        al.addStretch(1)
        tabs.addTab(api, "Dịch (API / local)")
        # --- paths
        pw = QWidget()
        pf = QFormLayout(pw)
        self.device = combo([("auto", "Tự động"), ("cuda", "GPU NVIDIA (CUDA)"), ("cpu", "CPU")])
        self.ffmpeg = PathEdit(mode="dir", placeholder="để trống = thư mục bin/ của SubVideo hoặc PATH")
        self.mpv = PathEdit(mode="dir", placeholder="thư mục chứa libmpv-2.dll (trình phát chính xác như bản xuất)")
        self.rubber = PathEdit(mode="file", placeholder="rubberband.exe (co giãn giọng chất lượng cao R3)")
        self.models = PathEdit(mode="dir", placeholder="nơi lưu mô hình AI tải về (cần nhiều GB)")
        self.dicts = PathEdit(mode="dir", placeholder="thư mục Names.txt / ChinesePhienAmWords.txt (Hán-Việt)")
        self.hf = QLineEdit()
        self.hf.setEchoMode(QLineEdit.Password)
        self.py_qwen = PathEdit(mode="file", placeholder="python.exe của venv riêng cho Qwen3-ASR (tuỳ chọn)")
        self.py_tts = PathEdit(mode="file", placeholder="python.exe của venv riêng cho TTS (tuỳ chọn)")
        self.theme = combo([("dark", "Tối"), ("light", "Sáng")])
        self.theme.currentIndexChanged.connect(lambda: apply_theme(QApplication.instance(), combo_value(self.theme)))
        pf.addRow("Thiết bị chạy AI:", self.device)
        pf.addRow("FFmpeg:", self.ffmpeg)
        pf.addRow("mpv (libmpv):", self.mpv)
        pf.addRow("Rubber Band:", self.rubber)
        pf.addRow("Thư mục mô hình:", self.models)
        pf.addRow("Từ điển Hán-Việt:", self.dicts)
        pf.addRow("Hugging Face token:", self.hf)
        pf.addRow("Python cho Qwen3-ASR:", self.py_qwen)
        pf.addRow("Python cho TTS:", self.py_tts)
        pf.addRow("Giao diện:", self.theme)
        tabs.addTab(pw, "Đường dẫn && công cụ")
        # --- libs
        lw = QWidget()
        ll = QVBoxLayout(lw)
        self.libs = QTableWidget(0, 3)
        self.libs.setHorizontalHeaderLabels(["Chức năng", "Trạng thái", "Lệnh cài"])
        self.libs.verticalHeader().setVisible(False)
        self.libs.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.libs.setEditTriggers(QTableWidget.NoEditTriggers)
        ll.addWidget(self.libs)
        b_chk = button("Kiểm tra lại")
        b_chk.clicked.connect(self._check_libs)
        ll.addLayout(hrow(b_chk, None))
        ll.addWidget(hint(f"Python đang dùng: {sys.executable}\nOmniVoice (transformers ≥ 5) và Qwen3-ASR (transformers 4.57) "
                          "xung đột: cài Qwen3-ASR vào venv riêng rồi chọn python.exe đó ở tab Đường dẫn."))
        tabs.addTab(lw, "Thư viện AI")
        self.body.addWidget(tabs, 1)
        b_save = primary("💾 Lưu cài đặt")
        b_save.clicked.connect(self._save)
        self.body.addLayout(hrow(None, b_save))
        self._load()
        self._check_libs()

    def _load(self):
        s = self.main.settings
        set_combo(self.default_provider, s["translate_provider"])
        for key, d in self.prov_widgets.items():
            cfg = s.provider(key)
            d["api_key"].setText(cfg.get("api_key", ""))
            d["model"].setText(cfg.get("model", ""))
            if "base_url" in d:
                d["base_url"].setText(cfg.get("base_url", ""))
            if "effort" in d:
                set_combo(d["effort"], cfg.get("effort", "medium"))
        set_combo(self.device, s["device"])
        self.ffmpeg.setText(s["ffmpeg_dir"])
        self.mpv.setText(s["mpv_dir"])
        self.rubber.setText(s["rubberband_path"])
        self.models.setText(s["models_dir"])
        self.dicts.setText(s["dict_dir"])
        self.hf.setText(s["hf_token"])
        self.py_qwen.setText(s["python_qwen"])
        self.py_tts.setText(s["python_tts"])
        set_combo(self.theme, s["theme"])

    def _save(self):
        s = self.main.settings
        s["translate_provider"] = combo_value(self.default_provider)
        for key, d in self.prov_widgets.items():
            cfg = s.provider(key)
            cfg["api_key"] = d["api_key"].text().strip()
            cfg["model"] = d["model"].text().strip()
            if "base_url" in d:
                cfg["base_url"] = d["base_url"].text().strip()
            if "effort" in d:
                cfg["effort"] = combo_value(d["effort"])
        s["device"] = combo_value(self.device)
        s["ffmpeg_dir"] = self.ffmpeg.text()
        s["mpv_dir"] = self.mpv.text()
        s["rubberband_path"] = self.rubber.text()
        s["models_dir"] = self.models.text()
        s["dict_dir"] = self.dicts.text()
        s["hf_token"] = self.hf.text().strip()
        s["python_qwen"] = self.py_qwen.text()
        s["python_tts"] = self.py_tts.text()
        s["theme"] = combo_value(self.theme)
        s.save()
        s.apply_env()
        from ...core import media

        media.find_tool.cache_clear()
        if not self.main.jobs.busy():
            self.main.jobs.restart_workers()  # workers re-read settings / PATH
        self.main.statusBar().showMessage("Đã lưu cài đặt", 3000)

    def _check_libs(self):
        self.libs.setRowCount(len(LIBS))
        for i, (label, mod, cmd) in enumerate(LIBS):
            ok = _installed(mod)
            self.libs.setItem(i, 0, QTableWidgetItem(label))
            it = QTableWidgetItem("✔ Đã cài" if ok else "— Chưa cài")
            it.setForeground(Qt.green if ok else Qt.gray)
            self.libs.setItem(i, 1, it)
            self.libs.setItem(i, 2, QTableWidgetItem(cmd))
        self.libs.resizeColumnsToContents()
        self.libs.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

    def _ping(self, key: str):
        self._save()
        d = self.prov_widgets[key]

        def done(ok, res):
            (QMessageBox.information if ok else QMessageBox.warning)(self, "Kiểm tra kết nối",
                                                                     ("✔ Kết nối được: " if ok else "✖ ") + res)

        self.main.run_job("llm_ping", {"provider": key, "model": d["model"].text().strip()}, f"Kiểm tra {key}", done,
                          project_required=False)

    def set_busy(self, busy: bool) -> None:
        pass
