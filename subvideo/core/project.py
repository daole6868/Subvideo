"""Project data model.

A project is a folder on disk containing ``project.json`` (the single source of
truth for every step) plus working files (audio stems, OCR crops, TTS clips,
renders).  All times are stored as integer milliseconds on the *source* timeline
(the timeline of the original video).  The optional time map (local video
slow-down) converts source time to output time only when rendering.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
PROJECT_FILE = "project.json"

# Ordered list of the processing steps (one tab each in the UI).
STEPS: list[tuple[str, str]] = [
    ("source", "Dự án & tải video"),
    ("separate", "Tách giọng & nhạc nền"),
    ("transcribe", "Nghe & nhận dạng lời"),
    ("ocr", "Quét phụ đề (OCR)"),
    ("fusion", "Hợp nhất phụ đề"),
    ("characters", "Nhân vật & xưng hô"),
    ("translate", "Dịch"),
    ("tts", "Lồng tiếng"),
    ("timing", "Căn nhịp"),
    ("mix", "Trộn âm"),
    ("export", "Xuất video"),
]
STEP_IDS = [s for s, _ in STEPS]

# Steps that become stale when an earlier step is re-run.
DOWNSTREAM: dict[str, list[str]] = {
    "source": STEP_IDS[1:],
    "separate": ["transcribe", "fusion", "translate", "tts", "timing", "mix", "export"],
    "transcribe": ["fusion", "translate", "tts", "timing", "mix", "export"],
    "ocr": ["fusion", "translate", "tts", "timing", "mix", "export"],
    "fusion": ["translate", "tts", "timing", "mix", "export"],
    "characters": ["translate", "tts", "timing", "mix", "export"],
    "translate": ["tts", "timing", "mix", "export"],
    "tts": ["timing", "mix", "export"],
    "timing": ["mix", "export"],
    "mix": ["export"],
    "export": [],
}


def _from_dict(cls, data: dict | None):
    """Build a dataclass from a dict, ignoring unknown keys (forward compatible)."""
    if data is None:
        return cls()
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MediaInfo:
    path: str = ""
    duration_ms: int = 0
    width: int = 0
    height: int = 0
    fps: float = 25.0
    fps_num: int = 25
    fps_den: int = 1
    vfr: bool = False
    video_codec: str = ""
    pix_fmt: str = ""
    bit_depth: int = 8
    color_space: str = ""
    color_primaries: str = ""
    color_transfer: str = ""
    has_audio: bool = True
    audio_codec: str = ""
    audio_channels: int = 2
    audio_rate: int = 48000


@dataclass
class Segment:
    id: int = 0
    # speech timing (real voice on/off, ms, source timeline)
    start: int = 0
    end: int = 0
    # subtitle display timing (frame snapped); None = derive from speech
    disp_start: int | None = None
    disp_end: int | None = None
    kind: str = "speech"  # speech | onscreen | nonverbal | song
    speaker: str = ""  # diarization label, e.g. SPEAKER_00
    character: str = ""  # character id (from Character.id)
    addressee: str = ""  # character id or free text
    emotion: str = ""
    # Chinese source text
    zh: str = ""
    zh_source: str = ""  # asr | ocr | fused | manual | import
    zh_asr: str = ""
    zh_ocr: str = ""
    confidence: float = 1.0
    words: list = field(default_factory=list)  # [[char, start_ms, end_ms], ...]
    # Vietnamese
    vi_sub: str = ""
    vi_dub: str = ""
    max_syllables: int = 0
    # Dubbing
    dub: bool = True  # generate a dubbed line
    keep_original: bool = False  # keep the original voice during this line
    gain_db: float = 0.0
    tts_file: str = ""  # natural TTS clip (relative to project)
    tts_ms: int = 0  # natural duration (trimmed)
    tts_key: str = ""  # hash of (text, voice, engine, speed) used to detect staleness
    final_file: str = ""  # stretched / placed clip
    final_key: str = ""
    # Placement decided by the timing solver (source timeline)
    place_start: int | None = None
    place_dur: int = 0  # duration of the clip on the OUTPUT timeline
    stretch: float = 1.0  # speed factor applied to the natural clip (>1 = faster)
    slow_region: list | None = None  # [a_ms, b_ms, factor] local video slow-down
    locked: bool = False  # user locked the placement
    # Review
    flags: list = field(default_factory=list)
    qa_note: str = ""
    reviewed: bool = False
    note: str = ""

    # --- helpers -------------------------------------------------------
    @property
    def duration(self) -> int:
        return max(0, self.end - self.start)

    @property
    def display_start(self) -> int:
        return self.start if self.disp_start is None else self.disp_start

    @property
    def display_end(self) -> int:
        return self.end if self.disp_end is None else self.disp_end

    def add_flag(self, flag: str) -> None:
        if flag not in self.flags:
            self.flags.append(flag)

    def remove_flag(self, flag: str) -> None:
        if flag in self.flags:
            self.flags.remove(flag)

    def has_dub(self) -> bool:
        return bool(self.dub and self.kind == "speech" and self.vi_dub.strip())


@dataclass
class Character:
    id: str = ""
    name_zh: str = ""
    name_vi: str = ""
    gender: str = ""  # nam | nữ | khác
    age: str = ""  # trẻ em | thiếu niên | thanh niên | trung niên | già
    role: str = ""
    personality: str = ""
    speaker_labels: list = field(default_factory=list)
    # Voice
    tts_engine: str = ""  # empty = project default
    voice_preset: str = ""
    voice_ref: str = ""  # path (relative to project or absolute)
    voice_ref_text: str = ""
    voice_instruct: str = ""
    speed: float = 1.0  # TTS speed multiplier
    rate: float = 5.0  # target Vietnamese syllables / second
    gain_db: float = 0.0
    emotion_refs: dict = field(default_factory=dict)  # emotion -> ref wav

    def display_name(self) -> str:
        return self.name_vi or self.name_zh or self.id


@dataclass
class Relation:
    """Pronoun matrix entry: how ``src`` addresses ``dst``."""

    src: str = ""  # character id
    dst: str = ""
    call: str = ""  # src gọi dst là ...
    self_call: str = ""  # src tự xưng là ...
    note: str = ""


@dataclass
class Term:
    zh: str = ""
    vi: str = ""
    note: str = ""


@dataclass
class Bible:
    genre: str = ""
    era: str = ""  # cổ trang | hiện đại | tiên hiệp | ...
    style_notes: str = ""
    synopsis: str = ""
    scenes: list = field(default_factory=list)  # [{start_id, end_id, summary}]


@dataclass
class OCRSettings:
    rois: list = field(default_factory=list)  # [[x, y, w, h]] normalized 0..1
    text_color: str = "white"  # white | yellow | auto | custom
    custom_rgb: list = field(default_factory=lambda: [255, 255, 255])
    color_tol: int = 60
    luma_thr: int = 190
    need_outline: bool = True
    change_thr: float = 0.35
    min_frames: int = 3
    frame_step: int = 1
    engine: str = "rapidocr"
    to_simplified: bool = True


@dataclass
class DisplayRules:
    lead_in_ms: int = 0
    linger_ms: int = 300
    min_duration_ms: int = 700
    max_duration_ms: int = 7000
    min_gap_frames: int = 2
    snap_shot_ms: int = 250
    max_chars_line: int = 42
    max_lines: int = 2


@dataclass
class TimingSettings:
    max_lead_ms: int = 200
    max_tail_ms: int = 500
    min_gap_ms: int = 80
    max_speedup: float = 1.20
    hard_max_speedup: float = 1.35
    min_speed: float = 1.0  # < 1.0 allows slowing the voice down
    regen_with_duration: bool = True
    allow_video_slow: bool = True
    max_video_slow: float = 0.85  # minimum video speed factor (0.85 = 15% slower)
    default_rate: float = 5.0  # syllables per second


@dataclass
class MixSettings:
    mode: str = "dub"  # sub | narration | dub
    dub_db: float = 0.0
    orig_db: float = -60.0  # -60 dB = muted
    bgm_db: float = 0.0
    keep_nonverbal: bool = True
    keep_db: float = 0.0
    ducking: bool = True
    duck_db: float = 6.0
    duck_orig: bool = True
    loudness: float = -14.0  # LUFS target, 0 = off
    sample_rate: int = 48000


@dataclass
class SubtitleStyle:
    font: str = "Arial"
    size: int = 0  # 0 = auto (relative to video height)
    primary: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline: float = 2.5
    shadow: float = 1.0
    bold: bool = True
    margin_v: int = 0  # 0 = auto
    bilingual: bool = False
    zh_size_ratio: float = 0.7
    box: bool = False


@dataclass
class ExportSettings:
    container: str = "mp4"  # mp4 | mkv
    video_mode: str = "x264"  # copy | x264 | x265 | nvenc_h264 | nvenc_hevc | av1
    crf: int = 17
    preset: str = "slow"
    subs: str = "burn"  # burn | soft | both | none
    cover: str = "none"  # none | blur | box
    cover_when: str = "subtitle"  # always | subtitle
    audio_codec: str = "aac"  # aac | flac | opus
    audio_bitrate: str = "320k"
    extra_tracks: list = field(default_factory=list)  # ["original", "narration"]
    output: str = ""
    export_srt: bool = True


@dataclass
class StepState:
    status: str = "todo"  # todo | done | stale | error | running
    updated: float = 0.0
    message: str = ""


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class Project:
    """A dubbing project stored in a folder."""

    SUBDIRS = ["source", "audio", "asr", "ocr", "tts", "voices", "render", "export", "logs", "cache"]

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.name: str = self.root.name
        self.created: float = time.time()
        self.media = MediaInfo()
        self.source_url: str = ""
        self.segments: list[Segment] = []
        self.characters: list[Character] = []
        self.relations: list[Relation] = []
        self.terms: list[Term] = []
        self.bible = Bible()
        self.shots: list[int] = []  # shot-change times (ms)
        self.ocr = OCRSettings()
        self.display = DisplayRules()
        self.timing = TimingSettings()
        self.mix = MixSettings()
        self.style = SubtitleStyle()
        self.export = ExportSettings()
        self.params: dict[str, dict[str, Any]] = {}  # per-step engine parameters
        self.steps: dict[str, StepState] = {s: StepState() for s in STEP_IDS}
        self.revision: int = 0

    # ------------------------------------------------------------------ paths
    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def rel(self, p: str | os.PathLike) -> str:
        """Path relative to the project root when possible (portable projects)."""
        p = Path(p)
        try:
            return p.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(p)

    def abs(self, p: str) -> Path:
        if not p:
            return Path()
        pp = Path(p)
        return pp if pp.is_absolute() else self.root / pp

    @property
    def video_path(self) -> Path:
        return self.abs(self.media.path)

    # Standard working files
    @property
    def original_wav(self) -> Path:
        return self.path("audio", "original.wav")

    @property
    def vocals_wav(self) -> Path:
        return self.path("audio", "vocals.wav")

    @property
    def vocals_dry_wav(self) -> Path:
        return self.path("audio", "vocals_dry.wav")

    @property
    def instrumental_wav(self) -> Path:
        return self.path("audio", "instrumental.wav")

    @property
    def asr_wav(self) -> Path:
        return self.path("audio", "asr16k.wav")

    @property
    def asr_json(self) -> Path:
        return self.path("asr", "asr.json")

    @property
    def ocr_json(self) -> Path:
        return self.path("ocr", "ocr.json")

    @property
    def mix_wav(self) -> Path:
        return self.path("render", "mix.wav")

    @property
    def mix_src_wav(self) -> Path:
        return self.path("render", "mix_source.wav")

    @property
    def subs_ass(self) -> Path:
        return self.path("render", "subs_vi.ass")

    @property
    def subs_src_ass(self) -> Path:
        return self.path("render", "subs_vi_source.ass")

    def has_stems(self) -> bool:
        return self.vocals_wav.exists() and self.instrumental_wav.exists()

    # ------------------------------------------------------------- creation
    @classmethod
    def create(cls, root: str | os.PathLike, name: str | None = None) -> "Project":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        if (root / PROJECT_FILE).exists():
            raise FileExistsError(f"Thư mục đã có dự án: {root}")
        proj = cls(root)
        proj.name = name or root.name
        proj.ensure_dirs()
        proj.save()
        return proj

    def ensure_dirs(self) -> None:
        for d in self.SUBDIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def is_project(root: str | os.PathLike) -> bool:
        return (Path(root) / PROJECT_FILE).exists()

    # ----------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "name": self.name,
            "created": self.created,
            "revision": self.revision,
            "source_url": self.source_url,
            "media": asdict(self.media),
            "segments": [asdict(s) for s in self.segments],
            "characters": [asdict(c) for c in self.characters],
            "relations": [asdict(r) for r in self.relations],
            "terms": [asdict(t) for t in self.terms],
            "bible": asdict(self.bible),
            "shots": list(self.shots),
            "ocr": asdict(self.ocr),
            "display": asdict(self.display),
            "timing": asdict(self.timing),
            "mix": asdict(self.mix),
            "style": asdict(self.style),
            "export": asdict(self.export),
            "params": self.params,
            "steps": {k: asdict(v) for k, v in self.steps.items()},
        }

    def load_dict(self, d: dict) -> None:
        self.name = d.get("name", self.root.name)
        self.created = d.get("created", time.time())
        self.revision = int(d.get("revision", 0))
        self.source_url = d.get("source_url", "")
        self.media = _from_dict(MediaInfo, d.get("media"))
        self.segments = [_from_dict(Segment, s) for s in d.get("segments", [])]
        self.characters = [_from_dict(Character, c) for c in d.get("characters", [])]
        self.relations = [_from_dict(Relation, r) for r in d.get("relations", [])]
        self.terms = [_from_dict(Term, t) for t in d.get("terms", [])]
        self.bible = _from_dict(Bible, d.get("bible"))
        self.shots = [int(x) for x in d.get("shots", [])]
        self.ocr = _from_dict(OCRSettings, d.get("ocr"))
        self.display = _from_dict(DisplayRules, d.get("display"))
        self.timing = _from_dict(TimingSettings, d.get("timing"))
        self.mix = _from_dict(MixSettings, d.get("mix"))
        self.style = _from_dict(SubtitleStyle, d.get("style"))
        self.export = _from_dict(ExportSettings, d.get("export"))
        self.params = d.get("params", {}) or {}
        steps = d.get("steps", {}) or {}
        self.steps = {s: _from_dict(StepState, steps.get(s)) for s in STEP_IDS}

    @classmethod
    def load(cls, root: str | os.PathLike) -> "Project":
        root = Path(root)
        f = root / PROJECT_FILE
        if not f.exists():
            raise FileNotFoundError(f"Không tìm thấy {PROJECT_FILE} trong {root}")
        proj = cls(root)
        with open(f, "r", encoding="utf-8") as fh:
            proj.load_dict(json.load(fh))
        proj.ensure_dirs()
        return proj

    def reload(self) -> None:
        with open(self.root / PROJECT_FILE, "r", encoding="utf-8") as fh:
            self.load_dict(json.load(fh))

    def save(self, backup: bool = False) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        f = self.root / PROJECT_FILE
        if backup and f.exists():
            shutil.copy2(f, self.root / (PROJECT_FILE + ".bak"))
        self.revision += 1
        tmp = f.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=1)
        os.replace(tmp, f)  # atomic on Windows and POSIX

    # ------------------------------------------------------------- step state
    def mark(self, step: str, status: str = "done", message: str = "", cascade: bool = True) -> None:
        st = self.steps.setdefault(step, StepState())
        st.status = status
        st.updated = time.time()
        st.message = message
        if cascade and status == "done":
            for d in DOWNSTREAM.get(step, []):
                ds = self.steps.setdefault(d, StepState())
                if ds.status == "done":
                    ds.status = "stale"

    # --------------------------------------------------------------- queries
    def segment(self, seg_id: int) -> Segment | None:
        for s in self.segments:
            if s.id == seg_id:
                return s
        return None

    def sort_segments(self) -> None:
        self.segments.sort(key=lambda s: (s.start, s.end, s.id))

    def renumber(self) -> None:
        self.sort_segments()
        for i, s in enumerate(self.segments, 1):
            s.id = i

    def next_segment_id(self) -> int:
        return max((s.id for s in self.segments), default=0) + 1

    def character(self, cid: str) -> Character | None:
        for c in self.characters:
            if c.id == cid:
                return c
        return None

    def character_for_segment(self, seg: Segment) -> Character | None:
        if seg.character:
            c = self.character(seg.character)
            if c:
                return c
        if seg.speaker:
            for c in self.characters:
                if seg.speaker in c.speaker_labels:
                    return c
        return None

    def speaker_labels(self) -> list[str]:
        labels: dict[str, int] = {}
        for s in self.segments:
            if s.speaker:
                labels[s.speaker] = labels.get(s.speaker, 0) + 1
        return sorted(labels, key=lambda k: (-labels[k], k))

    def new_character_id(self) -> str:
        existing = {c.id for c in self.characters}
        i = 1
        while f"char{i:02d}" in existing:
            i += 1
        return f"char{i:02d}"

    def dub_segments(self) -> list[Segment]:
        return [s for s in sorted(self.segments, key=lambda x: x.start) if s.has_dub()]

    def param(self, step: str, key: str, default: Any = None) -> Any:
        return self.params.get(step, {}).get(key, default)

    def set_param(self, step: str, key: str, value: Any) -> None:
        self.params.setdefault(step, {})[key] = value

    def slow_regions(self) -> list[tuple[int, int, float]]:
        regs = []
        for s in self.segments:
            if s.slow_region and s.has_dub():
                a, b, v = s.slow_region
                if b > a and 0 < v < 1:
                    regs.append((int(a), int(b), float(v)))
        regs.sort()
        # merge/clip overlaps defensively
        out: list[tuple[int, int, float]] = []
        for a, b, v in regs:
            if out and a < out[-1][1]:
                a = out[-1][1]
                if b <= a:
                    continue
            out.append((a, b, v))
        return out

    # Bible export/import (share across episodes of a series)
    def bible_dict(self) -> dict:
        return {
            "characters": [asdict(c) for c in self.characters],
            "relations": [asdict(r) for r in self.relations],
            "terms": [asdict(t) for t in self.terms],
            "bible": asdict(self.bible),
        }

    def import_bible(self, d: dict, merge: bool = True) -> None:
        chars = [_from_dict(Character, c) for c in d.get("characters", [])]
        rels = [_from_dict(Relation, r) for r in d.get("relations", [])]
        terms = [_from_dict(Term, t) for t in d.get("terms", [])]
        if not merge:
            self.characters, self.relations, self.terms = chars, rels, terms
        else:
            known = {c.id for c in self.characters}
            names = {c.name_zh for c in self.characters if c.name_zh}
            for c in chars:
                if c.id in known or (c.name_zh and c.name_zh in names):
                    continue
                self.characters.append(c)
            relkeys = {(r.src, r.dst) for r in self.relations}
            self.relations += [r for r in rels if (r.src, r.dst) not in relkeys]
            termkeys = {t.zh for t in self.terms}
            self.terms += [t for t in terms if t.zh not in termkeys]
        b = d.get("bible")
        if b:
            nb = _from_dict(Bible, b)
            for k in ("genre", "era", "style_notes"):
                if not getattr(self.bible, k):
                    setattr(self.bible, k, getattr(nb, k))


def iter_projects(folder: str | os.PathLike) -> Iterable[Path]:
    folder = Path(folder)
    if Project.is_project(folder):
        yield folder
        return
    for p in sorted(folder.iterdir()) if folder.exists() else []:
        if p.is_dir() and Project.is_project(p):
            yield p
