"""Build the final Chinese subtitle lines by fusing what we *see* (OCR) with what
we *hear* (ASR + VAD).

* OCR text is usually the correct script (names, terms); its timing is frame
  accurate but reflects when the subtitle was shown, not when the voice starts.
* ASR timing per character is accurate; its text often has homophone errors.

For each OCR line we align its characters with the ASR characters heard around
it (Needleman-Wunsch).  Speech start/end come from the aligned ASR characters,
then are refined against the vocal energy envelope (~10 ms precision).
Disagreements are resolved character by character:
  - homophones (same pinyin)  -> trust OCR (typical ASR error)
  - otherwise                  -> keep OCR, flag the line for review
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from ..core.audio import envelope_db, read_audio, refine_boundaries
from ..core.jobs import JobContext
from ..core.project import Project, Segment
from ..core.subtitles import compute_display_times, read_subtitles
from ..core.textutil import ZH_PUNCT, is_homophone, is_nonverbal_zh, strip_zh_punct, to_simplified, zh_units
from ..core.timeutil import overlap
from .ocr import OCREvent, load_events


@dataclass
class Char:
    c: str
    s: int
    e: int
    spk: str = ""
    sent: int = 0
    punct: str = ""  # punctuation that follows this char in the ASR text
    emotion: str = ""
    used: bool = False


# --------------------------------------------------------------- ASR input

def load_asr(project: Project) -> dict:
    if not project.asr_json.exists():
        return {}
    return json.loads(project.asr_json.read_text("utf-8"))


def asr_chars(asr: dict) -> list[Char]:
    out: list[Char] = []
    for si, sent in enumerate(asr.get("sentences", [])):
        chars = sent.get("chars") or []
        text = sent.get("text", "")
        spk = str(sent.get("spk", "") or "")
        emo = sent.get("emotion", "")
        if not chars:
            # no char timestamps: spread units uniformly over the sentence
            units = zh_units(text)
            if not units:
                continue
            s0, e0 = int(sent["start"]), int(sent["end"])
            step = (e0 - s0) / len(units)
            chars = [[u, int(s0 + i * step), int(s0 + (i + 1) * step)] for i, u in enumerate(units)]
        # attach punctuation from the sentence text
        punct_after: dict[int, str] = {}
        k = -1
        for u in _tokenize_with_punct(text):
            if u in ZH_PUNCT or all(ch in ZH_PUNCT for ch in u):
                if k >= 0:
                    punct_after[k] = punct_after.get(k, "") + u.strip()
            else:
                k += 1
        for i, (c, s, e) in enumerate(chars):
            out.append(Char(str(c), int(s), int(e), spk, si, punct_after.get(i, ""), emo))
    out.sort(key=lambda x: x.s)
    return out


def _tokenize_with_punct(text: str) -> list[str]:
    toks: list[str] = []
    buf = ""
    for ch in text:
        if ch in ZH_PUNCT:
            if buf:
                toks.append(buf)
                buf = ""
            toks.append(ch)
        elif "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿":
            if buf:
                toks.append(buf)
                buf = ""
            toks.append(ch)
        else:
            buf += ch
    if buf:
        toks.append(buf)
    # merge latin runs like zh_units does
    return [t for t in toks if t.strip()]


def chars_to_text(chars: list[Char]) -> str:
    out = ""
    for i, ch in enumerate(chars):
        if out and ch.c.isascii() and out[-1].isascii() and out[-1].isalnum():
            out += " "
        out += ch.c
        if ch.punct and i < len(chars) - 1:
            out += ch.punct
    if chars and chars[-1].punct and chars[-1].punct in "？！?!":
        out += chars[-1].punct
    return out


# ------------------------------------------------------- sentence splitting

def split_chars(chars: list[Char], max_chars: int = 16, max_ms: int = 5500, pause_ms: int = 350,
                hard_pause_ms: int = 700) -> list[list[Char]]:
    """Split a char stream into subtitle-sized groups."""
    groups: list[list[Char]] = []
    cur: list[Char] = []

    def strength(i: int) -> int:
        a = cur[i]
        b = cur[i + 1] if i + 1 < len(cur) else None
        st = 0
        if a.punct:
            st = 3 if any(p in "。！？!?…" for p in a.punct) else 1
        if b is not None:
            g = b.s - a.e
            if g >= hard_pause_ms:
                st = max(st, 4)
            elif g >= pause_ms:
                st = max(st, 2)
        return st

    for ch in chars:
        if cur:
            prev = cur[-1]
            gap = ch.s - prev.e
            if gap >= hard_pause_ms or ch.sent != prev.sent and gap > 150 or ch.spk != prev.spk:
                groups.append(cur)
                cur = []
            elif prev.punct and any(p in "。！？!?" for p in prev.punct) and len(cur) >= 4:
                groups.append(cur)
                cur = []
        cur.append(ch)
        too_long = len(cur) > max_chars or (cur[-1].e - cur[0].s) > max_ms
        if too_long and len(cur) > 2:
            best_i, best_s = None, -1
            for i in range(1, len(cur) - 1):
                s = strength(i)
                score = s * 10 + i  # prefer strong and late
                if s > 0 and score > best_s:
                    best_i, best_s = i, score
            if best_i is None:
                best_i = len(cur) // 2
            groups.append(cur[: best_i + 1])
            cur = cur[best_i + 1:]
    if cur:
        groups.append(cur)
    return [g for g in groups if g]


# ------------------------------------------------------------ alignment

def align_units(a: list[str], b: list[str]) -> list[tuple[int | None, int | None]]:
    """Needleman-Wunsch alignment; returns pairs of indices (None = gap)."""
    n, m = len(a), len(b)
    GAP = 0.7
    cost = np.zeros((n + 1, m + 1), np.float32)
    back = np.zeros((n + 1, m + 1), np.int8)
    cost[:, 0] = np.arange(n + 1) * GAP
    cost[0, :] = np.arange(m + 1) * GAP * 0.5  # leading ASR chars outside the subtitle are cheap
    back[1:, 0] = 1
    back[0, 1:] = 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            x, y = a[i - 1], b[j - 1]
            sub = 0.0 if x == y else (0.3 if is_homophone(x, y) else 1.0)
            c0 = cost[i - 1, j - 1] + sub
            c1 = cost[i - 1, j] + GAP
            c2 = cost[i, j - 1] + (GAP * 0.5 if i == n else GAP)  # trailing ASR chars cheap
            if c0 <= c1 and c0 <= c2:
                cost[i, j], back[i, j] = c0, 0
            elif c1 <= c2:
                cost[i, j], back[i, j] = c1, 1
            else:
                cost[i, j], back[i, j] = c2, 2
    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        bk = back[i, j]
        if i > 0 and j > 0 and bk == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and (j == 0 or bk == 1):
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    return pairs


@dataclass
class FuseResult:
    start: int
    end: int
    matched: int
    equal: int
    homophone: int
    words: list
    used_idx: list
    mismatch: int = 0
    ocr_only: int = 0
    text: str = ""  # OCR text after removing obvious OCR duplications


def match_ocr_line(text: str, cand: list[Char]) -> FuseResult | None:
    units = zh_units(strip_zh_punct(text))
    if not units or not cand:
        return None
    pairs = align_units(units, [c.c for c in cand])
    eq = hp = mm = only = 0
    first_j = last_j = None
    times: list[tuple[int, int] | None] = [None] * len(units)
    drop: set[int] = set()
    for i, j in pairs:
        if i is not None and j is None:
            only += 1
            if (i > 0 and units[i] == units[i - 1]) or (i + 1 < len(units) and units[i] == units[i + 1]):
                drop.add(i)  # OCR doubled a character (common on busy backgrounds)
        if i is not None and j is not None:
            x, y = units[i], cand[j].c
            if x == y:
                eq += 1
            elif is_homophone(x, y):
                hp += 1
            else:
                mm += 1
            times[i] = (cand[j].s, cand[j].e)
            if x == y or is_homophone(x, y):
                first_j = j if first_j is None else first_j
                last_j = j
    good = eq + hp
    if good == 0 or good < max(1, int(0.25 * len(units))):
        return None
    # interpolate missing char times
    known = [k for k, t in enumerate(times) if t is not None]
    for k in range(len(units)):
        if times[k] is None:
            prev = max((p for p in known if p < k), default=None)
            nxt = min((p for p in known if p > k), default=None)
            if prev is not None and nxt is not None:
                a, b = times[prev][1], times[nxt][0]
                times[k] = (a, max(a, b))
            elif prev is not None:
                times[k] = (times[prev][1], times[prev][1] + 150)
            elif nxt is not None:
                times[k] = (max(0, times[nxt][0] - 150), times[nxt][0])
    start = cand[first_j].s
    end = cand[last_j].e
    # extend span to aligned mismatches at the edges (still part of the voice)
    used = [j for i, j in pairs if i is not None and j is not None]
    used = [j for j in used if first_j - 2 <= j <= last_j + 2]
    if used:
        start = min(start, cand[min(used)].s)
        end = max(end, cand[max(used)].e)
    words = [[u, int(t[0]), int(t[1])] for k, (u, t) in enumerate(zip(units, times)) if t and k not in drop]
    clean = text
    if drop:
        clean = "".join(u for k, u in enumerate(units) if k not in drop)
        if text and text[-1] in ZH_PUNCT:
            clean += text[-1]
    return FuseResult(int(start), int(end), eq + hp + mm, eq, hp, words, used, mm, only - len(drop), clean)


# --------------------------------------------------------------- building

def _vad_regions(asr: dict) -> list[tuple[int, int]]:
    return [(int(a), int(b)) for a, b in asr.get("vad", [])]


def _speaker_for(seg_start: int, seg_end: int, chars: list[Char], turns: list) -> str:
    votes: dict[str, int] = {}
    for c in chars:
        if c.spk:
            votes[c.spk] = votes.get(c.spk, 0) + (c.e - c.s)
    for t in turns or []:
        ov = overlap(seg_start, seg_end, int(t[0]), int(t[1]))
        if ov > 0:
            votes[str(t[2])] = votes.get(str(t[2]), 0) + ov
    return max(votes, key=votes.get) if votes else ""


def events_from_file(path: str) -> list[OCREvent]:
    cues = read_subtitles(path)
    return [OCREvent(i + 1, 0, c.start, c.end, to_simplified(c.text.replace("\n", " ")), 1.0) for i, c in enumerate(cues)]


def build_segments(project: Project, ctx: JobContext, mode: str = "auto", display_from: str = "speech",
                   subtitle_file: str = "", refine: bool = True, max_chars: int = 16) -> list[Segment]:
    asr = load_asr(project)
    chars = asr_chars(asr)
    turns = asr.get("turns", [])
    vad = _vad_regions(asr)
    if subtitle_file:
        events = events_from_file(subtitle_file)
        src_label = "import"
    else:
        events = load_events(project)
        src_label = "ocr"
    if mode == "auto":
        mode = "fused" if (events and chars) else ("ocr" if events else "asr")
    ctx.log(f"Hợp nhất: chế độ={mode}, {len(events)} dòng phụ đề hình/file, {len(chars)} chữ nghe được")
    segs: list[Segment] = []

    if mode in ("fused", "ocr"):
        for k, ev in enumerate(sorted(events, key=lambda e: e.start)):
            ctx.progress(0.1 + 0.6 * k / max(1, len(events)), "Ghép phụ đề hình với tiếng nói")
            text = ev.text.replace("\n", " ").strip()
            seg = Segment(start=ev.start, end=ev.end, zh=text, zh_ocr=text, zh_source=src_label,
                          confidence=float(ev.conf or 1.0))
            if display_from == "ocr":
                seg.disp_start, seg.disp_end = ev.start, ev.end
            matched = None
            if mode == "fused":
                lo, hi = ev.start - 700, ev.end + 400
                cand = [c for c in chars if not c.used and lo <= (c.s + c.e) / 2 <= hi]
                matched = match_ocr_line(text, cand)
                if matched:
                    for j in matched.used_idx:
                        cand[j].used = True
                    sel = [cand[j] for j in matched.used_idx]
                    seg.start, seg.end = matched.start, matched.end
                    seg.words = matched.words
                    if matched.text and matched.text != text:
                        seg.zh = matched.text
                    seg.zh_asr = chars_to_text(sel)
                    seg.zh_source = "fused"
                    units = len(zh_units(strip_zh_punct(text))) or 1
                    sim = (matched.equal + matched.homophone) / units
                    seg.confidence = round(0.5 * float(ev.conf or 1.0) + 0.5 * min(1.0, sim), 3)
                    if sim < 0.6 or ((matched.mismatch + matched.ocr_only) > 0 and float(ev.conf or 1.0) < 0.95):
                        seg.add_flag("ocr_asr_mismatch")
                    seg.speaker = _speaker_for(seg.start, seg.end, sel, turns)
                    seg.emotion = next((c.emotion for c in sel if c.emotion), "")
            if not matched:
                # no recognised words: is there voice at all under this subtitle?
                speech = [(max(a, ev.start - 300), min(b, ev.end + 200)) for a, b in vad if overlap(a, b, ev.start - 300, ev.end + 200) > 0]
                spoken = sum(max(0, b - a) for a, b in speech)
                if speech and spoken > 0.35 * max(1, ev.end - ev.start):
                    seg.start, seg.end = speech[0][0], speech[-1][1]
                    if mode == "fused":
                        seg.add_flag("no_asr_match")
                    seg.speaker = _speaker_for(seg.start, seg.end, [], turns)
                elif mode == "fused" or vad:
                    seg.kind = "onscreen"
                    seg.dub = False
                    seg.disp_start, seg.disp_end = ev.start, ev.end
            segs.append(seg)
        if mode == "fused":
            leftovers = [c for c in chars if not c.used]
            for grp in split_chars(leftovers, max_chars=max_chars):
                txt = chars_to_text(grp)
                if not strip_zh_punct(txt):
                    continue
                seg = Segment(start=grp[0].s, end=grp[-1].e, zh=txt, zh_asr=txt, zh_source="asr",
                              words=[[c.c, c.s, c.e] for c in grp], confidence=0.6,
                              speaker=_speaker_for(grp[0].s, grp[-1].e, grp, turns),
                              emotion=next((c.emotion for c in grp if c.emotion), ""))
                if is_nonverbal_zh(txt):
                    seg.kind, seg.dub, seg.keep_original = "nonverbal", False, True
                else:
                    seg.add_flag("asr_only")
                segs.append(seg)
    else:  # ASR only
        for grp in split_chars(chars, max_chars=max_chars):
            txt = chars_to_text(grp)
            if not strip_zh_punct(txt):
                continue
            seg = Segment(start=grp[0].s, end=grp[-1].e, zh=txt, zh_asr=txt, zh_source="asr",
                          words=[[c.c, c.s, c.e] for c in grp], confidence=0.85,
                          speaker=_speaker_for(grp[0].s, grp[-1].e, grp, turns),
                          emotion=next((c.emotion for c in grp if c.emotion), ""))
            if is_nonverbal_zh(txt):
                seg.kind, seg.dub, seg.keep_original = "nonverbal", False, True
            segs.append(seg)

    for s in segs:
        if s.kind == "speech" and is_nonverbal_zh(s.zh):
            s.kind, s.dub, s.keep_original = "nonverbal", False, True

    # boundary refinement on the vocal envelope
    if refine and segs:
        ctx.progress(0.75, "Tinh chỉnh điểm bắt đầu/kết thúc theo năng lượng giọng nói")
        refine_segments(project, segs)

    segs.sort(key=lambda s: s.start)
    for i, s in enumerate(segs, 1):
        s.id = i
    compute_display_times([s for s in segs if s.disp_start is None], project.media.fps or 25.0,
                          project.display, project.shots)
    ctx.progress(1.0, f"Đã tạo {len(segs)} dòng", force=True)
    return segs


def refine_segments(project: Project, segs: list[Segment]) -> None:
    src = project.asr_wav if project.asr_wav.exists() else (project.vocals_wav if project.vocals_wav.exists() else project.original_wav)
    if not src.exists():
        return
    x, sr = read_audio(src, mono=True, sr=16000)
    env = envelope_db(x, sr, hop_ms=5, win_ms=20)
    speech = sorted([s for s in segs if s.kind in ("speech", "nonverbal") and not s.locked], key=lambda s: s.start)
    for i, s in enumerate(speech):
        lo = speech[i - 1].end if i > 0 else 0
        hi = speech[i + 1].start if i + 1 < len(speech) else 10**12
        if s.zh_source in ("ocr", "import") and not s.words:
            # subtitle timing only: voice usually starts a bit after the subtitle appears
            # and ends before it disappears -> search wider, asymmetrically
            ns, ne = refine_boundaries(env, 5.0, s.start, s.end, start_window=(300, 700), end_window=(900, 300),
                                       lo_ms=lo, hi_ms=hi)
        else:
            ns, ne = refine_boundaries(env, 5.0, s.start, s.end, search_ms=150, lo_ms=lo, hi_ms=hi)
        ns = max(ns, lo)
        ne = min(ne, hi) if hi > ns + 80 else ne
        if ne - ns >= 80:
            s.start, s.end = int(ns), int(ne)
