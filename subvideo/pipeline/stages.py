"""Registry of runnable stages.  A stage = function(project, params, ctx) -> str.

Stages run in the worker process (see subvideo/worker.py) so heavy models stay
loaded between jobs and a crash never takes the UI down.  They load the project
from disk, modify it and the worker saves it afterwards.
"""

from __future__ import annotations

from typing import Callable

from ..core.jobs import JobContext
from ..core.project import Project

StageFn = Callable[[Project, dict, JobContext], str]
STAGES: dict[str, tuple[str, StageFn]] = {}

# Steps the automatic run performs, in order (never includes export).
AUTO_STEPS = [
    ("extract", "Trích xuất âm thanh"),
    ("separate", "Tách giọng & nhạc nền"),
    ("transcribe", "Nghe & nhận dạng lời"),
    ("ocr", "Quét phụ đề (OCR)"),
    ("fusion", "Hợp nhất phụ đề"),
    ("bible", "Phân tích nhân vật & xưng hô"),
    ("translate", "Dịch"),
    ("qa", "Soát bản dịch"),
    ("voice_refs", "Lấy mẫu giọng nhân vật"),
    ("tts", "Lồng tiếng"),
    ("timing", "Căn nhịp"),
    ("mix", "Trộn âm"),
]


def stage(name: str, label: str):
    def deco(fn: StageFn) -> StageFn:
        STAGES[name] = (label, fn)
        return fn

    return deco


def run_stage(name: str, project: Project, params: dict, ctx: JobContext) -> str:
    if name not in STAGES:
        raise KeyError(f"Không có bước '{name}'")
    return STAGES[name][1](project, params or {}, ctx)


def _p(project: Project, step: str, params: dict, key: str, default=None):
    if key in params:
        return params[key]
    return project.param(step, key, default)


def _settings_default(key: str, fallback):
    try:
        from ..config import Settings

        return Settings.get().default(key) or fallback
    except Exception:
        return fallback


# ------------------------------------------------------------------ source

@stage("import_media", "Nhập video")
def _import_media(project, params, ctx):
    from .download import import_local

    p = import_local(project, params["file"], copy=bool(params.get("copy")))
    project.mark("source", "done", p.name)
    return f"Đã nhập {p.name} ({project.media.width}x{project.media.height}, {project.media.fps:.3f} fps)"


@stage("download", "Tải video")
def _download(project, params, ctx):
    from .download import download

    p = download(project, params["url"], ctx, params.get("cookies_browser", ""), params.get("cookie_file", ""),
                 int(params.get("max_height", 0) or 0), bool(params.get("subtitles", True)))
    project.mark("source", "done", p.name)
    return f"Đã tải {p.name}"


@stage("extract", "Trích xuất âm thanh")
def _extract(project, params, ctx):
    from .separate import extract_original, make_asr_audio

    if project.original_wav.exists() and not params.get("force"):
        if not project.asr_wav.exists():
            make_asr_audio(project)
        return "Âm thanh gốc đã có"
    extract_original(project, ctx)
    make_asr_audio(project)
    return "Đã trích xuất âm thanh gốc"


# --------------------------------------------------------------- separate

@stage("separate", "Tách giọng & nhạc nền")
def _separate(project, params, ctx):
    from ..config import Settings
    from .separate import separate

    info = separate(project, ctx,
                    engine=_p(project, "separate", params, "engine", _settings_default("separator", "audio_separator")),
                    model=_p(project, "separate", params, "model", _settings_default("separator_model", "")),
                    dereverb_model=_p(project, "separate", params, "dereverb", _settings_default("dereverb_model", "")),
                    model_dir=(Settings.get()["models_dir"] or None) and str(Settings.get()["models_dir"]) + "/separator")
    project.mark("separate", "done", info.get("model", info.get("engine", "")))
    return "Đã tách giọng nói và nhạc nền"


# -------------------------------------------------------------- transcribe

@stage("transcribe", "Nghe & nhận dạng lời")
def _transcribe(project, params, ctx):
    from .transcribe import transcribe

    ns = _p(project, "transcribe", params, "num_speakers", 0)
    r = transcribe(project, ctx,
                   engine=_p(project, "transcribe", params, "engine", _settings_default("asr", "funasr")),
                   diarize=_p(project, "transcribe", params, "diarize", _settings_default("diarize", "funasr")),
                   vad_engine=_p(project, "transcribe", params, "vad", _settings_default("vad", "silero")),
                   emotion=bool(_p(project, "transcribe", params, "emotion", False)),
                   num_speakers=int(ns) if ns else None)
    project.mark("transcribe", "done", f"{r['sentences']} câu, {r['speakers']} người nói")
    return f"Nhận dạng: {r['sentences']} câu, {r['chars']} chữ, {r['speakers']} người nói"


@stage("import_subs_asr", "Dùng phụ đề có sẵn làm lời nghe")
def _import_subs_asr(project, params, ctx):
    from .transcribe import import_subtitle_as_asr

    n = import_subtitle_as_asr(project, params["path"])
    project.mark("transcribe", "done", f"nhập {n} câu")
    return f"Đã nhập {n} câu từ phụ đề"


# --------------------------------------------------------------------- OCR

@stage("ocr", "Quét phụ đề (OCR)")
def _ocr(project, params, ctx):
    from .ocr import scan_video

    if not project.ocr.rois:
        project.mark("ocr", "todo", "bỏ qua (chưa chọn vùng)")
        return "Bỏ qua OCR: chưa chọn vùng phụ đề"
    rng = params.get("range")
    events = scan_video(project, ctx, _p(project, "ocr", params, "engine", project.ocr.engine),
                        bool(_p(project, "ocr", params, "cjk_only", True)), tuple(rng) if rng else None)
    project.mark("ocr", "done", f"{len(events)} dòng")
    return f"OCR: {len(events)} dòng phụ đề"


# ------------------------------------------------------------------ fusion

@stage("scenes", "Phát hiện cắt cảnh")
def _scenes(project, params, ctx):
    from .scenes import detect_shots

    project.shots = detect_shots(project, ctx, _p(project, "fusion", params, "shot_engine", "builtin"))
    return f"{len(project.shots)} điểm cắt cảnh"


@stage("fusion", "Hợp nhất phụ đề")
def _fusion(project, params, ctx):
    from .fusion import build_segments

    lo = 0.0
    if _p(project, "fusion", params, "detect_shots", True) and not project.shots:
        with ctx.sub(0.0, 0.3):
            _scenes(project, params, ctx)
        lo = 0.3
    with ctx.sub(lo, 1.0):
        segs = build_segments(project, ctx, mode=_p(project, "fusion", params, "mode", "auto"),
                              display_from=_p(project, "fusion", params, "display_from", "speech"),
                              subtitle_file=_p(project, "fusion", params, "subtitle_file", ""),
                              max_chars=int(_p(project, "fusion", params, "max_chars", 16)))
    keep = bool(params.get("keep_translations", True))
    if keep and project.segments:
        _carry_translations(project.segments, segs)
    project.save(backup=True)
    project.segments = segs
    project.mark("fusion", "done", f"{len(segs)} dòng")
    flagged = sum(1 for s in segs if s.flags)
    return f"Đã tạo {len(segs)} dòng phụ đề ({flagged} dòng cần xem lại)"


def _carry_translations(old, new) -> None:
    """Keep translations of lines whose Chinese text did not change."""

    by_text = {}
    for s in old:
        if s.vi_sub:
            by_text.setdefault(s.zh, s)
    for s in new:
        o = by_text.get(s.zh)
        if o is None:
            continue
        s.vi_sub, s.vi_dub, s.character, s.addressee = o.vi_sub, o.vi_dub, o.character, o.addressee


@stage("display_times", "Tính lại thời gian hiển thị phụ đề")
def _display(project, params, ctx):
    from ..core.subtitles import compute_display_times

    for s in project.segments:
        if not params.get("keep_manual") or s.disp_start is None:
            s.disp_start = s.disp_end = None
    compute_display_times([s for s in project.segments if s.disp_start is None], project.media.fps or 25.0,
                          project.display, project.shots)
    return "Đã tính lại thời gian hiển thị"


# -------------------------------------------------------------- translate

def _client(project, params, step="translate"):
    from .llm import LLMClient

    provider = params.get("provider") or project.param(step, "provider") or None
    model = params.get("model") or project.param(step, "model") or None
    return LLMClient(provider, model)


@stage("bible", "Phân tích nhân vật & xưng hô")
def _bible(project, params, ctx):
    from .translate import analyze

    if project.characters and params.get("skip_if_exists"):
        return "Đã có hồ sơ nhân vật — giữ nguyên"
    r = analyze(project, ctx, _client(project, params), merge=bool(params.get("merge", True)))
    project.mark("characters", "done", f"{r['characters']} nhân vật")
    return f"Hồ sơ: {r['characters']} nhân vật, {r['relations']} cặp xưng hô, {r['terms']} thuật ngữ"


@stage("translate", "Dịch")
def _translate(project, params, ctx):
    from .translate import translate

    r = translate(project, ctx, _client(project, params), only_ids=params.get("only_ids"),
                  batch_size=int(_p(project, "translate", params, "batch_size", 40)),
                  context_lines=int(_p(project, "translate", params, "context_lines", 8)),
                  overwrite=bool(params.get("overwrite", True)))
    if not params.get("only_ids"):
        project.mark("translate", "done", f"{r['translated']} câu")
    return f"Đã dịch {r['translated']} câu (rút gọn {r.get('shortened', 0)})"


@stage("shorten", "Rút gọn lời lồng tiếng")
def _shorten(project, params, ctx):
    from .translate import shorten

    n = shorten(project, ctx, _client(project, params), params.get("ids"))
    return f"Đã rút gọn {n} câu"


@stage("qa", "Soát bản dịch")
def _qa(project, params, ctx):
    from .translate import qa

    if params.get("skip"):
        return "Bỏ qua soát"
    n = qa(project, ctx, _client(project, params, "qa") if project.param("qa", "provider") else _client(project, params),
           auto_apply=bool(_p(project, "qa", params, "auto_apply", False)))
    return f"Soát xong: {n} vấn đề cần xem"


# ------------------------------------------------------------------ voices

@stage("voice_refs", "Lấy mẫu giọng nhân vật")
def _voice_refs(project, params, ctx):
    from .tts import extract_voice_refs

    if not project.characters:
        return "Chưa có nhân vật — bỏ qua"
    n = extract_voice_refs(project, ctx, overwrite=bool(params.get("overwrite", False)))
    return f"Đã lấy mẫu giọng cho {n} nhân vật"


@stage("tts", "Lồng tiếng")
def _tts(project, params, ctx):
    from .tts import synthesize, verify_clips

    if params.get("engine"):
        project.set_param("tts", "engine", params["engine"])
    r = synthesize(project, ctx, params.get("only_ids"), bool(params.get("force", False)))
    msg = f"Lồng tiếng: tạo {r['made']}, giữ {r['skipped']}, lỗi {r['failed']}"
    if _p(project, "tts", params, "verify", False):
        with ctx.sub(0.0, 1.0):
            bad = verify_clips(project, ctx)
        msg += f"; {bad} câu nghe chưa đúng"
    if not params.get("only_ids"):
        project.mark("tts", "done", msg)
    return msg


@stage("voice_preview", "Nghe thử giọng")
def _voice_preview(project, params, ctx):
    from .tts import preview_voice

    ch = project.character(params.get("character", "")) if params.get("character") else None
    out = project.path("render", "voice_preview.wav")
    preview_voice(project, ch, params.get("text") or "Xin chào, đây là giọng lồng tiếng thử.", out, params.get("engine"))
    return str(out)


# ------------------------------------------------------------------ timing

@stage("timing", "Căn nhịp")
def _timing(project, params, ctx):
    from .mixer import render_final_clips
    from .timing import apply, solve
    from .tts import duration_synth

    missing = [s.id for s in project.dub_segments() if not s.tts_file or not s.tts_ms]
    if missing:
        ctx.warn(f"{len(missing)} câu chưa có giọng lồng (chạy bước Lồng tiếng): {missing[:10]}…")
    placements, rep = solve(project.segments, project.timing, project.media.duration_ms)
    apply(project, placements, rep)
    synth = duration_synth(project) if project.timing.regen_with_duration else None
    with ctx.sub(0.05, 1.0):
        n = render_final_clips(project, ctx, synth)
    project.set_param("timing", "report", rep.text())
    project.mark("timing", "done", f"{rep.total} câu")
    return rep.text() + f"\n(Dựng lại {n} câu)"


# --------------------------------------------------------------------- mix

@stage("mix", "Trộn âm")
def _mix(project, params, ctx):
    from .export import write_project_subs
    from .mixer import render_mix

    timelines = params.get("timelines") or (["source", "output"] if project.slow_regions() else ["output"])
    for k, tl in enumerate(timelines):
        with ctx.sub(k / len(timelines), (k + 1) / len(timelines)):
            render_mix(project, ctx, tl)
        write_project_subs(project, tl)
    project.mark("mix", "done")
    return "Đã trộn âm" + (" (có đoạn giãn video: xem 'Bản xuất' để nghe/xem chính xác)" if project.slow_regions() else "")


# ------------------------------------------------------------------ export

@stage("export", "Xuất video")
def _export(project, params, ctx):
    from pathlib import Path

    from .export import export_video

    out = export_video(project, ctx, output=Path(params["output"]) if params.get("output") else None)
    project.mark("export", "done", out.name)
    return f"Đã xuất: {out}"


@stage("preview_clip", "Xem trước chính xác")
def _preview(project, params, ctx):
    from .export import export_video

    t0 = int(params.get("start_ms", 0))
    t1 = int(params.get("end_ms", t0 + 20000))
    out = export_video(project, ctx, range_ms=(t0, t1), output=project.path("render", "preview.mp4"), preview=True)
    return str(out)


@stage("proxy", "Dựng bản xem trước toàn bộ")
def _proxy(project, params, ctx):
    from .export import export_video

    out = export_video(project, ctx, output=project.path("render", "proxy.mp4"), preview=True,
                       proxy_height=int(params.get("height", 540)))
    return str(out)


# -------------------------------------------------------------------- auto

@stage("auto", "Chạy tự động")
def _auto(project, params, ctx):
    """Run every enabled step in order. NEVER exports (by design)."""
    steps = [s for s in (params.get("steps") or [k for k, _ in AUTO_STEPS]) if s != "export"]
    start_from = params.get("start_from")
    if start_from and start_from in steps:
        steps = steps[steps.index(start_from):]
    labels = dict(AUTO_STEPS)
    overrides = params.get("overrides", {})
    results = []
    for i, st in enumerate(steps):
        ctx.log(f"▶ Bước {i + 1}/{len(steps)}: {labels.get(st, st)}")
        ctx.progress(i / len(steps), f"[{i + 1}/{len(steps)}] {labels.get(st, st)}", force=True)
        p = dict(overrides.get(st, {}))
        if st == "bible":
            p.setdefault("skip_if_exists", True)
        with ctx.sub(i / len(steps), (i + 1) / len(steps)):
            msg = run_stage(st, project, p, ctx)
        project.save()
        results.append(f"✔ {labels.get(st, st)}: {msg.splitlines()[0] if msg else ''}")
        ctx.log(results[-1])
        if st == "timing" and params.get("shorten_loop", True):
            ids = [s.id for s in project.segments if "need_shorten" in s.flags]
            if ids and "translate" in steps:
                ctx.log(f"↻ {len(ids)} câu quá dài → rút gọn lời dịch, lồng lại, căn nhịp lại")
                run_stage("shorten", project, {"ids": ids, **overrides.get("translate", {})}, ctx)
                run_stage("tts", project, {"only_ids": ids, **overrides.get("tts", {})}, ctx)
                msg = run_stage("timing", project, {}, ctx)
                results.append("↻ Căn nhịp lại: " + msg.splitlines()[0])
                project.save()
    ctx.progress(1.0, "Tự động: hoàn tất (dừng trước bước Xuất)", force=True)
    return "\n".join(results) + "\n\nĐã dừng trước bước Xuất. Hãy kiểm tra ở tab Chỉnh sửa rồi xuất ở tab Xuất video."


@stage("release", "Giải phóng bộ nhớ GPU")
def _release(project, params, ctx):
    from . import transcribe as tr
    from . import tts

    tr._MODELS.clear()
    tts.release_models()
    return "Đã giải phóng mô hình"


@stage("llm_ping", "Kiểm tra kết nối mô hình dịch")
def _llm_ping(project, params, ctx):
    from .llm import LLMClient

    c = LLMClient(params.get("provider") or None, params.get("model") or None)
    return f"{c.label()} → {c.ping()}"
