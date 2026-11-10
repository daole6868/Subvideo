"""Vietnamese dubbing voices.

Engines:
  * omnivoice - k2-fsa OmniVoice (zero-shot clone, voice design, exact
                duration control -> best for lip-timing). Apache-2.0.
  * vieneu    - VieNeu-TTS v3 Turbo (native Vietnamese, 48 kHz, preset voices
                and cloning from 3-8 s; no speed control -> stretched later).
  * edge      - Microsoft Edge online voices (free, quick tests).
  * beep      - placeholder tones, only to test the pipeline without AI.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..core.audio import read_audio, resample, trim_silence, write_audio
from ..core.jobs import JobContext
from ..core.project import Character, Project, Segment
from ..core.textutil import count_vi_syllables, normalize_vi_for_tts, short_hash

SR = 48000

ENGINES = [
    ("omnivoice", "OmniVoice (nhái giọng, điều khiển chính xác thời lượng)"),
    ("vieneu", "VieNeu-TTS (giọng Việt tự nhiên, 25 giọng mẫu + nhái giọng)"),
    ("edge", "Edge TTS (online, miễn phí, để thử nhanh)"),
    ("beep", "Giọng thử (tiếng bíp) — chỉ để kiểm tra quy trình"),
]
VIENEU_PRESETS = ["Hải Đăng", "Mai Anh", "Trúc Ly", "Thiện Minh", "Thùy Dung", "Ngọc Huyền", "Quang Sơn", "Ngọc Trân",
                  "Minh Đức", "Phạm Tuyên", "Thái Sơn", "Xuân Vĩnh", "Thanh Bình", "Ngọc Linh", "Đoan Trang",
                  "Thục Đoan", "Minh Triết", "Mỹ Duyên", "Quỳnh Anh", "Đức Trí", "Kim Thanh", "Adam", "Quốc Tuấn",
                  "Thiền Tâm Đức", "Adam bựa"]
EDGE_VOICES = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]

_ENGINE_CACHE: dict[str, "TTSEngine"] = {}


@dataclass
class Voice:
    ref_audio: str = ""
    ref_text: str = ""
    preset: str = ""
    instruct: str = ""

    def key(self) -> tuple:
        return (self.ref_audio, self.ref_text, self.preset, self.instruct)


class TTSEngine:
    name = "base"
    supports_duration = False

    def synth(self, text: str, voice: Voice, speed: float = 1.0, duration: float | None = None) -> tuple[np.ndarray, int]:
        raise NotImplementedError


class OmniVoiceEngine(TTSEngine):
    name = "omnivoice"
    supports_duration = True

    def __init__(self, num_step: int = 32):
        import torch
        from omnivoice import OmniVoice

        from ..config import Settings

        dev = Settings.get().device()
        self.model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cuda:0" if dev == "cuda" else "cpu",
                                               dtype=torch.float16 if dev == "cuda" else torch.float32)
        self.num_step = num_step
        self.prompts: dict[tuple, object] = {}

    def _prompt(self, v: Voice):
        if not v.ref_audio:
            return None
        k = (v.ref_audio, v.ref_text)
        if k not in self.prompts:
            self.prompts[k] = self.model.create_voice_clone_prompt(v.ref_audio, v.ref_text or None)
        return self.prompts[k]

    def synth(self, text, voice, speed=1.0, duration=None):
        kw = dict(text=text, language="vi", num_step=self.num_step)
        p = self._prompt(voice)
        if p is not None:
            kw["voice_clone_prompt"] = p
        elif voice.instruct:
            kw["instruct"] = voice.instruct
        if duration:
            kw["duration"] = float(duration)
            kw["postprocess_output"] = False  # keep the exact length
        elif abs(speed - 1.0) > 1e-3:
            kw["speed"] = float(speed)
        audios = self.model.generate(**kw)
        return np.asarray(audios[0], dtype=np.float32), int(self.model.sampling_rate)


class VieNeuEngine(TTSEngine):
    name = "vieneu"
    supports_duration = False

    def __init__(self):
        from vieneu import Vieneu

        self.tts = Vieneu()
        self.sr = int(getattr(self.tts, "sample_rate", 48000))

    def synth(self, text, voice, speed=1.0, duration=None):
        if voice.ref_audio:
            wav = self.tts.infer(text, ref_audio=voice.ref_audio)
        else:
            wav = self.tts.infer(text, voice=voice.preset or "Hải Đăng")
        x = np.asarray(wav, dtype=np.float32).reshape(-1)
        if abs(speed - 1.0) > 0.02:  # engine has no speed control: stretch keeping pitch
            from .stretch import stretch

            x = stretch(x, self.sr, speed)
        return x, self.sr


class EdgeEngine(TTSEngine):
    name = "edge"

    def synth(self, text, voice, speed=1.0, duration=None):
        import edge_tts

        rate = int(round((speed - 1.0) * 100))
        name = voice.preset if voice.preset.startswith("vi-") else EDGE_VOICES[0]
        with tempfile.TemporaryDirectory(prefix="sv_edge_") as td:
            out = Path(td) / "a.mp3"
            com = edge_tts.Communicate(text, name, rate=f"{rate:+d}%")
            com.save_sync(str(out))
            try:
                x, sr = read_audio(out, mono=True)
            except Exception:
                from ..core import media

                wav = Path(td) / "a.wav"
                media.run([media.ffmpeg(), "-hide_banner", "-y", "-i", str(out), "-ac", "1", "-ar", "48000", str(wav)])
                x, sr = read_audio(wav, mono=True)
        return x, sr


class BeepEngine(TTSEngine):
    """Syllable-rhythm tones; lets the whole pipeline be tested without AI."""

    name = "beep"
    supports_duration = True

    def synth(self, text, voice, speed=1.0, duration=None):
        n = max(1, count_vi_syllables(text))
        dur = duration or n / (5.0 * max(0.3, speed))
        sr = SR
        t = np.arange(int(dur * sr)) / sr
        f0 = 180.0 + (hash(voice.key()) % 120)
        syl = (np.sin(np.pi * (t * n / dur) % np.pi) ** 2)
        x = 0.25 * np.sin(2 * np.pi * f0 * t) * syl
        return x.astype(np.float32), sr


def get_engine(name: str) -> TTSEngine:
    eng = _ENGINE_CACHE.get(name)
    if eng is None:
        if name == "omnivoice":
            eng = OmniVoiceEngine()
        elif name == "vieneu":
            eng = VieNeuEngine()
        elif name == "edge":
            eng = EdgeEngine()
        elif name == "beep":
            eng = BeepEngine()
        else:
            raise ValueError(f"Engine lồng tiếng không hợp lệ: {name}")
        _ENGINE_CACHE[name] = eng
    return eng


# ---------------------------------------------------------------- voices

def voice_for(project: Project, seg: Segment | None, ch: Character | None) -> Voice:
    if ch is None:
        return Voice(preset=project.param("tts", "default_preset", ""))
    ref = ch.voice_ref
    if seg is not None and seg.emotion and ch.emotion_refs.get(seg.emotion):
        ref = ch.emotion_refs[seg.emotion]
    instruct = ch.voice_instruct
    if not instruct and not ref:
        g = {"nam": "male", "nữ": "female"}.get(ch.gender, "")
        a = {"trẻ em": "child", "thiếu niên": "teenager", "thanh niên": "young adult", "trung niên": "middle-aged",
             "già": "elderly"}.get(ch.age, "")
        instruct = ", ".join(x for x in (g, a) if x)
    return Voice(str(project.abs(ref)) if ref else "", ch.voice_ref_text, ch.voice_preset, instruct)


def engine_for(project: Project, ch: Character | None) -> str:
    return (ch.tts_engine if ch and ch.tts_engine else "") or project.param("tts", "engine", "omnivoice")


def tts_key(project: Project, seg: Segment) -> str:
    ch = project.character_for_segment(seg)
    v = voice_for(project, seg, ch)
    return short_hash(engine_for(project, ch), normalize_vi_for_tts(seg.vi_dub), v.key(), ch.speed if ch else 1.0)


def synthesize(project: Project, ctx: JobContext, only_ids=None, force: bool = False) -> dict:
    segs = [s for s in project.dub_segments() if only_ids is None or s.id in set(only_ids)]
    raw_dir = project.path("tts", "raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    made = skipped = failed = 0
    for k, s in enumerate(segs):
        key = tts_key(project, s)
        f = raw_dir / f"{s.id:05d}_{key}.wav"
        if not force and s.tts_key == key and s.tts_file and project.abs(s.tts_file).is_file():
            skipped += 1
            continue
        ch = project.character_for_segment(s)
        name = engine_for(project, ch)
        ctx.progress(k / max(1, len(segs)), f"Lồng tiếng câu {s.id} ({name}) {k + 1}/{len(segs)}")
        try:
            eng = get_engine(name)
            x, sr = eng.synth(normalize_vi_for_tts(s.vi_dub), voice_for(project, s, ch), speed=ch.speed if ch else 1.0)
            x = resample(x.reshape(-1), sr, SR)
            i0, i1 = trim_silence(x, SR)
            x = x[i0:i1]
            peak = float(np.abs(x).max()) if len(x) else 0.0
            if peak > 0:
                x = x * (0.89 / peak) if peak > 0.89 else x
            for old in raw_dir.glob(f"{s.id:05d}_*.wav"):
                if old != f:
                    old.unlink(missing_ok=True)
            write_audio(f, x, SR)
            s.tts_file = project.rel(f)
            s.tts_ms = int(round(len(x) * 1000 / SR))
            s.tts_key = key
            s.final_key = ""
            s.remove_flag("tts_error")
            made += 1
        except ImportError as e:
            raise RuntimeError(f"Chưa cài engine '{name}': {e}. Xem tab Cài đặt → Thư viện AI.") from e
        except Exception as e:
            s.add_flag("tts_error")
            s.note = f"Lỗi TTS: {e}"
            failed += 1
            ctx.warn(f"Câu {s.id}: lỗi tạo giọng: {e}")
        if made and made % 20 == 0:
            project.save()
    ctx.progress(1.0, f"Lồng tiếng: tạo {made}, giữ nguyên {skipped}, lỗi {failed}", force=True)
    return {"made": made, "skipped": skipped, "failed": failed}


def duration_synth(project: Project):
    """Callable used by the timing step to regenerate a line at an exact duration."""

    def _f(seg: Segment, duration_s: float):
        ch = project.character_for_segment(seg)
        name = engine_for(project, ch)
        eng = get_engine(name)
        if not eng.supports_duration:
            return None
        x, sr = eng.synth(normalize_vi_for_tts(seg.vi_dub), voice_for(project, seg, ch), duration=duration_s)
        return resample(x.reshape(-1), sr, SR)

    return _f


def preview_voice(project: Project, ch: Character | None, text: str, out: Path, engine: str | None = None) -> Path:
    name = engine or engine_for(project, ch)
    x, sr = get_engine(name).synth(normalize_vi_for_tts(text), voice_for(project, None, ch), speed=ch.speed if ch else 1.0)
    write_audio(out, resample(x.reshape(-1), sr, SR), SR)
    return out


# ------------------------------------------------------- reference clips

def extract_voice_refs(project: Project, ctx: JobContext, min_ms: int = 3000, max_ms: int = 9000,
                       overwrite: bool = False) -> int:
    """Pick a clean 3-9 s line of each character as voice-clone reference."""
    src = project.vocals_dry_wav if project.vocals_dry_wav.exists() else project.vocals_wav
    if not src.exists():
        raise RuntimeError("Cần tách giọng trước để lấy mẫu giọng sạch.")
    segs = sorted([s for s in project.segments if s.kind == "speech"], key=lambda s: s.start)
    vdir = project.path("voices")
    vdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for ch in project.characters:
        if ch.voice_ref and not overwrite and project.abs(ch.voice_ref).exists():
            continue
        cands = []
        for i, s in enumerate(segs):
            if project.character_for_segment(s) is not ch:
                continue
            d = s.duration
            if d < min_ms * 0.6:
                continue
            gap_prev = s.start - segs[i - 1].end if i > 0 else 9999
            gap_next = segs[i + 1].start - s.end if i + 1 < len(segs) else 9999
            if gap_prev < 150 or gap_next < 150:
                continue  # risk of other voices
            score = -abs(min(d, max_ms) - 6000) / 1000.0 + s.confidence * 2 - (2 if s.flags else 0)
            cands.append((score, s))
        if not cands:
            continue
        cands.sort(key=lambda c: -c[0])
        best = cands[0][1]
        a = max(0, best.start - 60)
        b = min(best.start + max_ms, best.end + 80)
        x, sr = read_audio(src, mono=True, start_ms=a, end_ms=b)
        out = vdir / f"{ch.id}.wav"
        write_audio(out, x, sr)
        ch.voice_ref = project.rel(out)
        ch.voice_ref_text = best.zh
        n += 1
        # emotion-specific references
        for emo in {s.emotion for _, s in cands if s.emotion}:
            es = next((s for _, s in cands if s.emotion == emo), None)
            if es is None:
                continue
            xa, _ = read_audio(src, mono=True, start_ms=max(0, es.start - 60), end_ms=min(es.start + max_ms, es.end + 80))
            eo = vdir / f"{ch.id}_{short_hash(emo)}.wav"
            write_audio(eo, xa, sr)
            ch.emotion_refs[emo] = project.rel(eo)
        ctx.progress(n / max(1, len(project.characters)), f"Lấy mẫu giọng: {ch.display_name()}")
    return n


def verify_clips(project: Project, ctx: JobContext, threshold: float = 0.72, model_name: str = "large-v3-turbo") -> int:
    """Back-check dubbed clips with Vietnamese ASR to catch mispronounced / garbled lines."""
    from faster_whisper import WhisperModel

    from ..config import Settings
    from ..core.textutil import similarity

    dev = Settings.get().device()
    m = WhisperModel(model_name, device=dev, compute_type="float16" if dev == "cuda" else "int8")
    bad = 0
    segs = [s for s in project.dub_segments() if s.tts_file and project.abs(s.tts_file).exists()]
    for k, s in enumerate(segs):
        segments, _ = m.transcribe(str(project.abs(s.tts_file)), language="vi", beam_size=1)
        heard = " ".join(x.text for x in segments).strip().lower()
        want = normalize_vi_for_tts(s.vi_dub).lower()
        sim = similarity(heard, want)
        if sim < threshold:
            s.add_flag("tts_check")
            s.note = f"Kiểm tra giọng: nghe được “{heard}” (giống {sim:.0%})"
            bad += 1
        else:
            s.remove_flag("tts_check")
        ctx.progress((k + 1) / max(1, len(segs)), f"Kiểm tra giọng lồng {k + 1}/{len(segs)}")
    return bad


def release_models() -> None:
    _ENGINE_CACHE.clear()
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
