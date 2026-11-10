"""Listen: voice activity detection, Chinese speech recognition with
per-character timestamps, speaker diarization and (optional) emotion tags.

Result is written to asr/asr.json:
  {"engine": ..., "sentences": [{"start", "end", "text", "spk", "chars": [[c, s, e]], "emotion"}],
   "vad": [[s, e]], "turns": [[s, e, label]]}
All times in ms.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np

from ..core.audio import energy_vad, read_audio, write_audio
from ..core.jobs import JobContext
from ..core.project import Project
from ..core.textutil import strip_zh_punct, zh_units

_MODELS: dict[str, object] = {}

ASR_ENGINES = [
    ("funasr", "FunASR Paraformer-zh (nhanh, mốc thời gian từng chữ, kèm nhận diện người nói)"),
    ("qwen3", "Qwen3-ASR 1.7B + ForcedAligner (chính xác cao, 22 phương ngữ)"),
    ("whisper", "faster-whisper large-v3 (dự phòng)"),
]
DIARIZE_ENGINES = [
    ("funasr", "CAM++ tích hợp FunASR (chỉ khi dùng FunASR)"),
    ("campp", "CAM++ phân cụm (dùng với mọi engine)"),
    ("pyannote", "pyannote community-1 (cần HF token)"),
    ("none", "Không phân biệt người nói"),
]
VAD_ENGINES = [("silero", "Silero VAD"), ("energy", "Theo năng lượng (không cần AI)")]
EMOTION_TAGS = {"HAPPY": "vui", "SAD": "buồn", "ANGRY": "giận", "NEUTRAL": "", "FEARFUL": "sợ",
                "DISGUSTED": "ghê", "SURPRISED": "ngạc nhiên", "EMO_UNKNOWN": ""}


def _device() -> str:
    from ..config import Settings

    return Settings.get().device()


def _cuda_dev() -> str:
    d = _device()
    return "cuda:0" if d == "cuda" else "cpu"


# ------------------------------------------------------------------- VAD

def run_vad(wav16: np.ndarray, engine: str, ctx: JobContext) -> list[tuple[int, int]]:
    if engine == "silero":
        try:
            import torch
            from silero_vad import get_speech_timestamps, load_silero_vad

            model = _MODELS.get("silero") or load_silero_vad()
            _MODELS["silero"] = model
            ts = get_speech_timestamps(torch.from_numpy(wav16.astype(np.float32)), model, threshold=0.45,
                                       sampling_rate=16000, min_speech_duration_ms=150, min_silence_duration_ms=250,
                                       speech_pad_ms=40, return_seconds=False)
            return [(int(t["start"] / 16), int(t["end"] / 16)) for t in ts]
        except ImportError:
            ctx.warn("Chưa cài silero-vad, dùng VAD theo năng lượng.")
    return energy_vad(wav16, 16000)


def chunk_regions(vad: list[tuple[int, int]], max_ms: int = 25000, max_gap: int = 1200) -> list[tuple[int, int]]:
    chunks: list[list[int]] = []
    for a, b in vad:
        if chunks and a - chunks[-1][1] <= max_gap and b - chunks[-1][0] <= max_ms:
            chunks[-1][1] = b
        else:
            chunks.append([a, b])
    out = []
    for a, b in chunks:  # split very long single regions
        while b - a > max_ms:
            out.append((a, a + max_ms))
            a += max_ms
        out.append((a, b))
    return out


def _units_with_times(text: str, ts: list) -> list[list]:
    units = zh_units(strip_zh_punct(text)) if text else []
    if ts and len(ts) == len(units):
        return [[u, int(t[0]), int(t[1])] for u, t in zip(units, ts)]
    if not units:
        return []
    if ts:
        s0, e0 = int(ts[0][0]), int(ts[-1][1])
    else:
        return []
    step = (e0 - s0) / len(units)
    return [[u, int(s0 + i * step), int(s0 + (i + 1) * step)] for i, u in enumerate(units)]


# ------------------------------------------------------------------ engines

def asr_funasr(audio_path: Path, diarize: bool, ctx: JobContext) -> list[dict]:
    from funasr import AutoModel

    key = f"funasr|{diarize}"
    m = _MODELS.get(key)
    if m is None:
        ctx.progress(0.05, "Tải mô hình FunASR (lần đầu sẽ tải về)…", force=True)
        kw = dict(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc", device=_cuda_dev(),
                  disable_update=True)
        if diarize:
            kw["spk_model"] = "cam++"
        m = AutoModel(**kw)
        _MODELS[key] = m
    ctx.progress(0.15, "Đang nhận dạng tiếng Trung (FunASR)…", force=True)
    gen_kw = dict(input=str(audio_path), batch_size_s=300)
    if not diarize:
        gen_kw["sentence_timestamp"] = True
    r = m.generate(**gen_kw)[0]
    sents = []
    for it in r.get("sentence_info", []) or []:
        text = it.get("text") or it.get("sentence") or ""
        chars = _units_with_times(text, it.get("timestamp") or [])
        spk = it.get("spk")
        sents.append({"start": int(it.get("start", chars[0][1] if chars else 0)),
                      "end": int(it.get("end", chars[-1][2] if chars else 0)),
                      "text": text, "chars": chars,
                      "spk": f"SPEAKER_{int(spk):02d}" if spk is not None and diarize else ""})
    if not sents and r.get("text"):
        chars = _units_with_times(r["text"], r.get("timestamp") or [])
        if chars:
            sents.append({"start": chars[0][1], "end": chars[-1][2], "text": r["text"], "chars": chars, "spk": ""})
    return sents


def asr_qwen3(wav16: np.ndarray, vad: list[tuple[int, int]], ctx: JobContext, model_size: str = "1.7B") -> list[dict]:
    import torch
    from qwen_asr import Qwen3ASRModel

    key = f"qwen3|{model_size}"
    m = _MODELS.get(key)
    if m is None:
        ctx.progress(0.05, "Tải mô hình Qwen3-ASR (lần đầu sẽ tải về)…", force=True)
        dev = _cuda_dev()
        dtype = torch.bfloat16 if dev.startswith("cuda") else torch.float32
        m = Qwen3ASRModel.from_pretrained(f"Qwen/Qwen3-ASR-{model_size}", dtype=dtype, device_map=dev,
                                          max_inference_batch_size=8, max_new_tokens=512,
                                          forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B",
                                          forced_aligner_kwargs=dict(dtype=dtype, device_map=dev))
        _MODELS[key] = m
    sents = []
    chunks = chunk_regions(vad, max_ms=25000)
    for k, (a, b) in enumerate(chunks):
        ctx.progress(0.1 + 0.8 * k / max(1, len(chunks)), f"Qwen3-ASR đoạn {k + 1}/{len(chunks)}")
        pad_a = max(0, a - 150)
        clip = wav16[int(pad_a * 16): int((b + 150) * 16)]
        if len(clip) < 1600:
            continue
        r = m.transcribe(audio=(clip, 16000), language="Chinese", return_time_stamps=True)[0]
        chars = []
        for it in (r.time_stamps or []):
            chars.append([it.text, int(pad_a + it.start_time * 1000), int(pad_a + it.end_time * 1000)])
        if chars and r.text.strip():
            sents.append({"start": chars[0][1], "end": chars[-1][2], "text": r.text, "chars": chars, "spk": ""})
    return sents


def asr_whisper(audio_path: Path, ctx: JobContext, model_name: str = "large-v3") -> list[dict]:
    from faster_whisper import WhisperModel

    key = f"whisper|{model_name}"
    m = _MODELS.get(key)
    if m is None:
        ctx.progress(0.05, "Tải mô hình Whisper…", force=True)
        dev = _device()
        m = WhisperModel(model_name, device=dev, compute_type="float16" if dev == "cuda" else "int8")
        _MODELS[key] = m
    segments, info = m.transcribe(str(audio_path), language="zh", word_timestamps=True, vad_filter=True,
                                  vad_parameters=dict(min_silence_duration_ms=500), beam_size=5,
                                  condition_on_previous_text=False)
    sents = []
    total = max(1.0, getattr(info, "duration", 1.0))
    for s in segments:
        chars = []
        for w in s.words or []:
            units = zh_units(strip_zh_punct(w.word))
            if not units:
                continue
            a, b = w.start * 1000, w.end * 1000
            step = (b - a) / len(units)
            for i, u in enumerate(units):
                chars.append([u, int(a + i * step), int(a + (i + 1) * step)])
        if chars:
            sents.append({"start": chars[0][1], "end": chars[-1][2], "text": s.text.strip(), "chars": chars, "spk": ""})
        ctx.progress(0.1 + 0.8 * min(1.0, s.end / total), "Whisper đang nhận dạng…")
    return sents


# ------------------------------------------------------------- diarization

def diarize_pyannote(audio_path: Path, ctx: JobContext, num_speakers: int | None = None) -> list[list]:
    import torch
    from pyannote.audio import Pipeline

    from ..config import Settings

    p = _MODELS.get("pyannote")
    if p is None:
        token = Settings.get()["hf_token"] or None
        p = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=token)
        if _device() == "cuda":
            p.to(torch.device("cuda"))
        _MODELS["pyannote"] = p
    ctx.progress(0.92, "Phân biệt người nói (pyannote)…", force=True)
    out = p(str(audio_path), num_speakers=num_speakers)
    ann = getattr(out, "exclusive_speaker_diarization", None) or getattr(out, "speaker_diarization", out)
    turns = []
    for item in (ann.itertracks(yield_label=True) if hasattr(ann, "itertracks") else ann):
        if len(item) == 3:
            turn, _, spk = item
        else:
            turn, spk = item
        turns.append([int(turn.start * 1000), int(turn.end * 1000), str(spk)])
    return turns


def diarize_campp(wav16: np.ndarray, sents: list[dict], ctx: JobContext, threshold: float = 0.55,
                  num_speakers: int | None = None) -> None:
    """Speaker embedding per sentence (CAM++), then agglomerative clustering."""
    from funasr import AutoModel
    from scipy.cluster.hierarchy import fcluster, linkage

    m = _MODELS.get("campp")
    if m is None:
        m = AutoModel(model="cam++", device=_cuda_dev(), disable_update=True)
        _MODELS["campp"] = m
    embs, idx = [], []
    with tempfile.TemporaryDirectory(prefix="sv_spk_") as td:
        for k, s in enumerate(sents):
            a, b = int(s["start"] * 16), int(s["end"] * 16)
            if b - a < 16000 * 0.6:
                continue
            clip = wav16[a:b]
            f = Path(td) / f"{k}.wav"
            write_audio(f, clip, 16000, subtype="PCM_16")
            e = m.generate(input=str(f))[0]["spk_embedding"]
            e = e.detach().cpu().numpy().reshape(-1) if hasattr(e, "detach") else np.asarray(e).reshape(-1)
            embs.append(e / (np.linalg.norm(e) + 1e-9))
            idx.append(k)
            ctx.progress(0.9 + 0.08 * k / max(1, len(sents)), "Phân biệt người nói (CAM++)…")
    if len(embs) < 2:
        for s in sents:
            s["spk"] = "SPEAKER_00"
        return
    X = np.stack(embs)
    Z = linkage(X, method="average", metric="cosine")
    labels = fcluster(Z, t=num_speakers, criterion="maxclust") if num_speakers else fcluster(Z, t=threshold, criterion="distance")
    # order labels by first appearance
    order: dict[int, int] = {}
    for lab in labels:
        order.setdefault(int(lab), len(order))
    for k, lab in zip(idx, labels):
        sents[k]["spk"] = f"SPEAKER_{order[int(lab)]:02d}"
    # short sentences: borrow the nearest labelled neighbour
    labelled = [k for k in range(len(sents)) if sents[k].get("spk")]
    for k, s in enumerate(sents):
        if not s.get("spk") and labelled:
            near = min(labelled, key=lambda j: abs(sents[j]["start"] - s["start"]))
            s["spk"] = sents[near]["spk"]


# ------------------------------------------------------------------ emotion

def tag_emotions(wav16: np.ndarray, sents: list[dict], ctx: JobContext) -> None:
    import re

    from funasr import AutoModel

    m = _MODELS.get("sensevoice")
    if m is None:
        m = AutoModel(model="iic/SenseVoiceSmall", device=_cuda_dev(), disable_update=True)
        _MODELS["sensevoice"] = m
    with tempfile.TemporaryDirectory(prefix="sv_emo_") as td:
        for k, s in enumerate(sents):
            a, b = int(s["start"] * 16), int(s["end"] * 16)
            if b - a < 4000:
                continue
            f = Path(td) / f"{k}.wav"
            write_audio(f, wav16[a:b], 16000, subtype="PCM_16")
            raw = m.generate(input=str(f), language="zh", use_itn=False)[0]["text"]
            tags = re.findall(r"<\|([A-Z_]+)\|>", raw)
            emo = next((EMOTION_TAGS[t] for t in tags if t in EMOTION_TAGS), "")
            events = [t for t in tags if t in ("Laughter", "Cry", "Cough", "Breath", "BGM")]
            s["emotion"] = emo
            if events:
                s["events"] = events
            ctx.progress(0.98 * k / max(1, len(sents)), "Nhận diện cảm xúc…")


# -------------------------------------------------------------------- main

def transcribe(project: Project, ctx: JobContext, engine: str = "funasr", diarize: str = "funasr",
               vad_engine: str = "silero", emotion: bool = False, num_speakers: int | None = None,
               qwen_size: str = "1.7B") -> dict:
    from .separate import make_asr_audio

    if not project.asr_wav.exists():
        if not project.original_wav.exists():
            from .separate import extract_original

            extract_original(project, ctx)
        make_asr_audio(project)
    wav16, _ = read_audio(project.asr_wav, mono=True, sr=16000)
    ctx.progress(0.02, "Phát hiện đoạn có tiếng nói (VAD)…", force=True)
    vad = run_vad(wav16, vad_engine, ctx)
    ctx.log(f"VAD: {len(vad)} đoạn có tiếng nói")

    if engine == "qwen3":
        sents = asr_qwen3(wav16, vad, ctx, qwen_size)
    elif engine == "whisper":
        sents = asr_whisper(project.asr_wav, ctx)
    else:
        sents = asr_funasr(project.asr_wav, diarize == "funasr", ctx)

    turns: list = []
    if diarize == "pyannote":
        turns = diarize_pyannote(project.asr_wav, ctx, num_speakers)
    elif diarize == "campp" or (diarize == "funasr" and engine != "funasr"):
        diarize_campp(wav16, sents, ctx, num_speakers=num_speakers)
    if emotion:
        try:
            tag_emotions(wav16, sents, ctx)
        except Exception as e:
            ctx.warn(f"Nhận diện cảm xúc thất bại: {e}")
    data = {"engine": engine, "diarize": diarize, "sentences": sents, "vad": [list(v) for v in vad], "turns": turns}
    project.asr_json.parent.mkdir(parents=True, exist_ok=True)
    project.asr_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    nchar = sum(len(s["chars"]) for s in sents)
    spk = {s.get("spk") for s in sents if s.get("spk")} | {t[2] for t in turns}
    ctx.progress(1.0, f"Nhận dạng xong: {len(sents)} câu, {nchar} chữ, {len(spk)} người nói", force=True)
    return {"sentences": len(sents), "chars": nchar, "speakers": len(spk)}


def import_subtitle_as_asr(project: Project, path: str) -> int:
    """Use an existing subtitle file (e.g. downloaded soft subs) as 'heard' text."""
    from ..core.subtitles import read_subtitles
    from ..core.textutil import to_simplified

    cues = read_subtitles(path)
    sents = []
    for c in cues:
        text = to_simplified(c.text.replace("\n", " "))
        units = zh_units(strip_zh_punct(text))
        if not units:
            continue
        step = (c.end - c.start) / len(units)
        chars = [[u, int(c.start + i * step), int(c.start + (i + 1) * step)] for i, u in enumerate(units)]
        sents.append({"start": c.start, "end": c.end, "text": text, "chars": chars, "spk": ""})
    data = {"engine": "import", "sentences": sents, "vad": [[s["start"], s["end"]] for s in sents], "turns": []}
    project.asr_json.parent.mkdir(parents=True, exist_ok=True)
    project.asr_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return len(sents)
