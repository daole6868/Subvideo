"""Vocal / background separation.

Engines:
  * audio_separator - UVR models (BS-RoFormer / Mel-Band RoFormer / MDX / Demucs) via
                      python-audio-separator. Best quality.
  * demucs          - Demucs htdemucs_ft (run through audio-separator).
  * none            - no separation (original audio is used as background).
Optionally a second pass removes reverb/echo from the vocal stem; the dry vocal
is only used for speech recognition and voice-clone references, the mix keeps
the natural (wet) vocal.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core import media
from ..core.audio import read_audio, write_audio
from ..core.jobs import JobContext
from ..core.project import Project

VOCAL_MODELS = [
    ("model_bs_roformer_ep_368_sdr_12.9628.ckpt", "BS-RoFormer ep368 (khuyên dùng, rất sạch)"),
    ("model_bs_roformer_ep_317_sdr_12.9755.ckpt", "BS-RoFormer ep317"),
    ("vocals_mel_band_roformer.ckpt", "Mel-Band RoFormer vocals (SDR giọng cao nhất)"),
    ("MDX23C-8KFFT-InstVoc_HQ.ckpt", "MDX23C InstVoc HQ (nhẹ hơn)"),
    ("htdemucs_ft.yaml", "Demucs htdemucs_ft"),
]
DEREVERB_MODELS = [
    ("", "Không khử vang"),
    ("dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt", "Mel-RoFormer khử vang (mạnh)"),
    ("dereverb_mel_band_roformer_less_aggressive_anvuew_sdr_18.8050.ckpt", "Mel-RoFormer khử vang (nhẹ)"),
    ("dereverb-echo_mel_band_roformer_sdr_13.4843_v2.ckpt", "Khử vang + tiếng vọng"),
    ("UVR-DeEcho-DeReverb.pth", "UVR DeEcho-DeReverb (VR)"),
]
# stem names produced by each dereverb model: (dry, wet)
_DEREVERB_STEMS = {
    "dereverb-echo_mel_band_roformer_sdr_13.4843_v2.ckpt": ("dry", "no dry"),
}

_CACHE: dict[str, object] = {}


def extract_original(project: Project, ctx: JobContext) -> Path:
    if not project.video_path.exists():
        raise FileNotFoundError(f"Không thấy video: {project.video_path}")
    if not project.media.has_audio:
        raise RuntimeError("Video không có âm thanh.")
    out = project.original_wav
    media.extract_audio(project.video_path, out, sr=48000, channels=2, ctx=ctx, duration_ms=project.media.duration_ms)
    return out


def _separator(model_dir: str | None, out_dir: Path):
    from audio_separator.separator import Separator

    key = f"{model_dir}|{out_dir}"
    sep = _CACHE.get(key)
    if sep is None:
        kwargs = dict(output_dir=str(out_dir), output_format="WAV", sample_rate=48000)
        if model_dir:
            kwargs["model_file_dir"] = model_dir
        sep = Separator(**kwargs)
        _CACHE.clear()
        _CACHE[key] = sep
    return sep


def _run_audio_separator(src: Path, out_dir: Path, model: str, names: dict[str, str], ctx: JobContext,
                         model_dir: str | None) -> dict[str, Path]:
    sep = _separator(model_dir, out_dir)
    if getattr(sep, "_sv_loaded", None) != model:
        ctx.progress(0.02, f"Tải mô hình {model} (lần đầu sẽ tải về)…", force=True)
        sep.load_model(model_filename=model)
        sep._sv_loaded = model
    files = sep.separate(str(src), custom_output_names=names)
    out: dict[str, Path] = {}
    for f in files:
        p = Path(f)
        if not p.is_absolute():
            p = out_dir / p
        out[p.stem.lower()] = p
    return out


def _normalize_stem(src: Path, dst: Path) -> Path:
    """Ensure a 48 kHz stereo WAV at a fixed path (moves the file when it already is)."""
    import os

    from ..core.audio import audio_info, resample, to_channels

    frames, sr, ch = audio_info(src)
    if sr == 48000 and ch == 2:
        os.replace(src, dst)
        return dst
    x, sr = read_audio(src)
    if sr != 48000:
        x = resample(x, sr, 48000)
    write_audio(dst, to_channels(x, 2), 48000)
    if src.resolve() != dst.resolve():
        src.unlink(missing_ok=True)
    return dst


def _subtract(a: Path, b: Path, dst: Path) -> None:
    """dst = a - b, streamed block by block (both 48 kHz stereo)."""
    import soundfile as sf

    with sf.SoundFile(str(a)) as fa, sf.SoundFile(str(b)) as fb, \
            sf.SoundFile(str(dst), "w", samplerate=48000, channels=2, subtype="FLOAT") as fo:
        while True:
            xa = fa.read(48000 * 20, dtype="float32", always_2d=True)
            xb = fb.read(len(xa), dtype="float32", always_2d=True)
            if len(xa) == 0:
                break
            if len(xb) < len(xa):
                import numpy as np

                xb = np.concatenate([xb, np.zeros((len(xa) - len(xb), xb.shape[1]), np.float32)])
            fo.write(xa - xb)


def make_asr_audio(project: Project) -> Path:
    src = project.vocals_dry_wav if project.vocals_dry_wav.exists() else (
        project.vocals_wav if project.vocals_wav.exists() else project.original_wav)
    x, _ = read_audio(src, mono=True, sr=16000)
    write_audio(project.asr_wav, x, 16000, subtype="PCM_16")
    return project.asr_wav


def separate(project: Project, ctx: JobContext, engine: str = "audio_separator", model: str = "",
             dereverb_model: str = "", model_dir: str | None = None) -> dict:
    with ctx.sub(0.0, 0.08):
        if not project.original_wav.exists():
            extract_original(project, ctx)
    tmp = project.path("audio", "_sep")
    info = {"engine": engine}
    if engine == "none":
        for p in (project.vocals_wav, project.instrumental_wav, project.vocals_dry_wav):
            p.unlink(missing_ok=True)
        make_asr_audio(project)
        ctx.log("Bỏ qua tách giọng: dùng âm thanh gốc.")
        return info
    tmp.mkdir(parents=True, exist_ok=True)
    with ctx.sub(0.08, 0.75):
        ctx.progress(0.0, "Đang tách giọng nói khỏi nhạc nền…", force=True)
        if engine == "demucs":
            model = "htdemucs_ft.yaml"
        model = model or VOCAL_MODELS[0][0]
        outs = _run_audio_separator(project.original_wav, tmp, model,
                                    {"Vocals": "vocals", "Instrumental": "instrumental"}, ctx, model_dir)
        info["model"] = model
        v = outs.get("vocals") or next((p for k, p in outs.items() if "voc" in k), None)
        ins = outs.get("instrumental") if len(outs) <= 2 else None  # 4-stem models: derive below
        if v is None:
            raise RuntimeError(f"Mô hình {model} không trả về phần giọng nói: {list(outs)}")
        _normalize_stem(v, project.vocals_wav)
        if ins is not None and Path(ins).exists():
            _normalize_stem(Path(ins), project.instrumental_wav)
        else:  # background = original - vocals (keeps every non-vocal sound)
            _subtract(project.original_wav, project.vocals_wav, project.instrumental_wav)
        for f in outs.values():
            if f.exists() and f.parent == tmp:
                f.unlink(missing_ok=True)
    project.vocals_dry_wav.unlink(missing_ok=True)
    if dereverb_model:
        with ctx.sub(0.75, 0.95):
            ctx.progress(0.0, "Khử vang giọng nói (dùng cho nhận dạng & mẫu giọng)…", force=True)
            try:
                dry_name, wet_name = _DEREVERB_STEMS.get(dereverb_model, ("noreverb", "reverb"))
                outs = _run_audio_separator(project.vocals_wav, tmp, dereverb_model,
                                            {dry_name: "vocals_dry", wet_name: "vocals_reverb"}, ctx, model_dir)
                dry = outs.get("vocals_dry")
                if dry and dry.exists():
                    _normalize_stem(dry, project.vocals_dry_wav)
                wet = outs.get("vocals_reverb")
                if wet is not None:
                    wet.unlink(missing_ok=True)
                info["dereverb"] = dereverb_model
            except Exception as e:  # dereverb is optional
                ctx.warn(f"Khử vang thất bại, bỏ qua: {e}")
    make_asr_audio(project)
    shutil.rmtree(tmp, ignore_errors=True)
    ctx.progress(1.0, "Tách giọng xong", force=True)
    return info
