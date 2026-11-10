"""Context-aware Chinese -> Vietnamese translation.

1. ``analyze``   - read the whole transcript once and build the "bible":
                   characters, relationships / forms of address (xưng hô),
                   glossary, genre, scene summaries.  User reviews it.
2. ``translate`` - translate scene by scene with the bible in the (cached)
                   system prompt, previous translated lines as context, and a
                   per-line syllable budget for the dubbing version.
3. ``shorten``   - rewrite dubbing lines that are still too long.
4. ``qa``        - review pass: pronoun consistency, terms, meaning errors.
"""

from __future__ import annotations

import json
from typing import Iterable

from ..core.jobs import JobContext
from ..core.project import Character, Project, Relation, Segment, Term
from ..core.textutil import count_vi_syllables
from . import hanviet
from .llm import LLMClient, LLMError
from .timing import compute_budgets

# --------------------------------------------------------------- schemas

_LINE_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "addressee": {"type": "string"},
                    "vi_sub": {"type": "string"},
                    "vi_dub": {"type": "string"},
                },
                "required": ["id", "addressee", "vi_sub", "vi_dub"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}

_SHORTEN_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "integer"}, "vi_dub": {"type": "string"}},
        "required": ["id", "vi_dub"], "additionalProperties": False}}},
    "required": ["lines"], "additionalProperties": False,
}

_QA_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "type": {"type": "string"}, "message": {"type": "string"},
                       "fix_sub": {"type": "string"}, "fix_dub": {"type": "string"}},
        "required": ["id", "type", "message", "fix_sub", "fix_dub"], "additionalProperties": False}}},
    "required": ["issues"], "additionalProperties": False,
}

_STR = {"type": "string"}
_BIBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "genre": _STR, "era": _STR, "style_notes": _STR, "synopsis": _STR,
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "name_zh": _STR, "name_vi": _STR, "gender": _STR, "age": _STR, "role": _STR, "personality": _STR,
            "speaker_labels": {"type": "array", "items": _STR}},
            "required": ["name_zh", "name_vi", "gender", "age", "role", "personality", "speaker_labels"],
            "additionalProperties": False}},
        "relations": {"type": "array", "items": {"type": "object", "properties": {
            "from": _STR, "to": _STR, "call": _STR, "self_call": _STR, "note": _STR},
            "required": ["from", "to", "call", "self_call", "note"], "additionalProperties": False}},
        "terms": {"type": "array", "items": {"type": "object", "properties": {"zh": _STR, "vi": _STR, "note": _STR},
                                             "required": ["zh", "vi", "note"], "additionalProperties": False}},
        "scenes": {"type": "array", "items": {"type": "object", "properties": {
            "start_id": {"type": "integer"}, "end_id": {"type": "integer"}, "summary": _STR},
            "required": ["start_id", "end_id", "summary"], "additionalProperties": False}},
    },
    "required": ["genre", "era", "style_notes", "synopsis", "characters", "relations", "terms", "scenes"],
    "additionalProperties": False,
}


# --------------------------------------------------------------- helpers

def _speaker_name(project: Project, seg: Segment) -> str:
    ch = project.character_for_segment(seg)
    if ch:
        g = f", {ch.gender}" if ch.gender else ""
        return f"{ch.display_name()}{g}"
    return seg.speaker or "?"


def _char_by_name(project: Project, name: str) -> Character | None:
    n = (name or "").strip()
    if not n:
        return None
    for c in project.characters:
        if n in (c.name_zh, c.name_vi, c.id):
            return c
    return None


def bible_text(project: Project) -> str:
    b = project.bible
    lines = []
    if b.genre or b.era:
        lines.append(f"GENRE: {b.genre} | ERA/SETTING: {b.era}")
    if b.synopsis:
        lines.append(f"SYNOPSIS: {b.synopsis}")
    if project.characters:
        lines.append("CHARACTERS (always use these Vietnamese names):")
        for c in project.characters:
            meta = ", ".join(x for x in (c.gender, c.age, c.role, c.personality) if x)
            lines.append(f"- {c.name_zh or c.id} → {c.display_name()}" + (f" ({meta})" if meta else ""))
    if project.relations:
        lines.append("FORMS OF ADDRESS (xưng hô) — speaker → listener: calls listener / refers to self:")
        for r in project.relations:
            a = project.character(r.src)
            b2 = project.character(r.dst)
            if a and b2:
                note = f" — {r.note}" if r.note else ""
                lines.append(f"- {a.display_name()} → {b2.display_name()}: gọi \"{r.call}\", xưng \"{r.self_call}\"{note}")
    if project.terms:
        lines.append("GLOSSARY (use exactly):")
        for t in project.terms:
            lines.append(f"- {t.zh} = {t.vi}" + (f" ({t.note})" if t.note else ""))
    if b.style_notes:
        lines.append(f"STYLE NOTES: {b.style_notes}")
    return "\n".join(lines)


SYSTEM_RULES = """You are an expert Chinese→Vietnamese subtitle translator and dubbing-script adapter for Chinese film and TV.

RULES
1. Vietnamese pronouns (xưng hô) depend on who speaks to whom. For every line decide the listener (addressee) from context: previous/next lines, names, the scene. Follow the FORMS OF ADDRESS table exactly; for pairs that are missing, choose pronouns that fit genre, age, status and relationship, then stay consistent.
2. Historical / wuxia / xianxia genres use classical register (ta/ngươi, huynh/đệ/tỷ/muội, sư phụ/đồ nhi, bẩm/thần/bệ hạ/nô tỳ…); modern stories use contemporary pronouns (anh/em, tớ/cậu, mình/bạn, con/bố/mẹ…). Never mix registers.
3. "vi_sub" = subtitle: complete meaning, concise, easy to read quickly (about 15 characters per second of the line's duration at most), one line of text, no line breaks.
4. "vi_dub" = what the voice actor says: natural spoken Vietnamese with the same meaning and tone, and it MUST NOT exceed "max_syl" syllables (each space-separated Vietnamese word is one syllable; numbers count as spoken words). Drop filler words before dropping meaning. It may equal vi_sub when that already fits.
5. Exactly one output line per input line, same ids, never merge, split, skip or reorder lines. Interjections (啊, 嗯, 哈哈…) become short natural equivalents ("A!", "Ừm", "Ha ha").
6. Character names and terms: use the CHARACTERS / GLOSSARY lists; unlisted Chinese names and titles in Hán-Việt (e.g. 萧炎 → Tiêu Viêm, 长老 → trưởng lão).
7. No notes, explanations, brackets or quotes around the text. Keep profanity level and emotion of the original.
8. "addressee" = Vietnamese name of the listener if known, else "".
Reply with JSON only: {"lines": [{"id": <int>, "addressee": "...", "vi_sub": "...", "vi_dub": "..."}]}"""


def _system_prompt(project: Project) -> str:
    bt = bible_text(project)
    return SYSTEM_RULES + ("\n\nSERIES BIBLE\n" + bt if bt else "")


def _chunks(segs: list[Segment], size: int) -> list[list[Segment]]:
    """Split lines into chunks, preferring to cut at long pauses (scene breaks)."""
    out, cur = [], []
    for i, s in enumerate(segs):
        cur.append(s)
        if len(cur) >= size:
            # look back for the biggest gap in the last 30% of the chunk
            best_k, best_gap = len(cur) - 1, -1
            for k in range(int(len(cur) * 0.7), len(cur) - 1):
                g = cur[k + 1].start - cur[k].end
                if g > best_gap:
                    best_k, best_gap = k, g
            out.append(cur[: best_k + 1])
            cur = cur[best_k + 1:]
    if cur:
        out.append(cur)
    return out


def _scene_summary(project: Project, seg_ids: list[int]) -> str:
    out = []
    for sc in project.bible.scenes or []:
        try:
            if sc["start_id"] <= max(seg_ids) and sc["end_id"] >= min(seg_ids):
                out.append(sc["summary"])
        except (KeyError, TypeError):
            continue
    return " ".join(out)


# ---------------------------------------------------------------- analyze

def analyze(project: Project, ctx: JobContext, client: LLMClient | None = None, merge: bool = True) -> dict:
    client = client or LLMClient()
    segs = [s for s in sorted(project.segments, key=lambda s: s.start) if s.zh.strip()]
    if not segs:
        raise LLMError("Chưa có lời thoại tiếng Trung. Hãy chạy bước Nghe/OCR và Hợp nhất trước.")
    transcript = "\n".join(f"[{s.id}] ({s.speaker or '?'}) {s.zh}" for s in segs)
    if len(transcript) > 180_000:
        transcript = transcript[:180_000]
        ctx.warn("Lời thoại quá dài, chỉ phân tích phần đầu.")
    system = ("You analyse Chinese drama transcripts to prepare a Vietnamese translation bible. "
              "Speaker labels like SPEAKER_00 come from automatic diarization and may be imperfect. "
              "Reply with JSON only.")
    user = f"""Read this transcript (format: [line id] (speaker label) text) and return JSON with:
- genre (Vietnamese, e.g. "cổ trang cung đấu", "tiên hiệp", "hiện đại ngôn tình", "hài", "hành động")
- era: "cổ trang" | "tiên hiệp" | "hiện đại" | "dân quốc" | "khác"
- style_notes: short Vietnamese guidance on register and tone for translators
- synopsis: 2-4 sentence Vietnamese summary
- characters: every named or recurring character: name_zh, name_vi (Hán-Việt for Chinese names), gender ("nam"/"nữ"/"không rõ"), age (e.g. "thiếu niên", "thanh niên", "trung niên", "già"), role, personality (Vietnamese, short), speaker_labels (labels from the transcript that are probably this character, may be empty)
- relations: forms of address for each important pair speaking to each other: from (name_zh), to (name_zh), call (how 'from' addresses 'to' in Vietnamese, e.g. "sư huynh", "nàng", "anh"), self_call (how 'from' refers to self, e.g. "ta", "đệ", "em"), note
- terms: recurring proper nouns / titles / cultivation terms with Vietnamese renderings (zh, vi, note)
- scenes: consecutive line ranges forming scenes with a one-sentence Vietnamese summary (start_id, end_id, summary)

TRANSCRIPT:
{transcript}"""
    ctx.progress(0.1, f"Phân tích nhân vật & bối cảnh bằng {client.label()}…", force=True)
    data = client.complete_json(system, user, _BIBLE_SCHEMA, max_tokens=32000)
    apply_bible(project, data, merge=merge)
    ctx.progress(1.0, f"Đã nhận diện {len(project.characters)} nhân vật, {len(project.terms)} thuật ngữ", force=True)
    return {"characters": len(project.characters), "relations": len(project.relations), "terms": len(project.terms)}


def apply_bible(project: Project, data: dict, merge: bool = True) -> None:
    b = project.bible
    for k in ("genre", "era", "style_notes", "synopsis"):
        v = data.get(k)
        if v and (not merge or not getattr(b, k)):
            setattr(b, k, v)
    if data.get("scenes"):
        b.scenes = data["scenes"]
    if not merge:
        project.characters, project.relations, project.terms = [], [], []
    for c in data.get("characters", []):
        name_zh = (c.get("name_zh") or "").strip()
        existing = _char_by_name(project, name_zh) if name_zh else None
        hv = hanviet.convert(name_zh) if name_zh else ""
        if existing:
            for lab in c.get("speaker_labels") or []:
                if lab and lab not in existing.speaker_labels:
                    existing.speaker_labels.append(lab)
            continue
        ch = Character(id=project.new_character_id(), name_zh=name_zh, name_vi=hv or c.get("name_vi", ""),
                       gender=c.get("gender", ""), age=c.get("age", ""), role=c.get("role", ""),
                       personality=c.get("personality", ""),
                       speaker_labels=[l for l in (c.get("speaker_labels") or []) if l])
        # a diarization label belongs to one character only
        for other in project.characters:
            ch.speaker_labels = [l for l in ch.speaker_labels if l not in other.speaker_labels]
        project.characters.append(ch)
    for r in data.get("relations", []):
        a, b2 = _char_by_name(project, r.get("from", "")), _char_by_name(project, r.get("to", ""))
        if not a or not b2 or a.id == b2.id:
            continue
        if any(x.src == a.id and x.dst == b2.id for x in project.relations):
            continue
        project.relations.append(Relation(a.id, b2.id, r.get("call", ""), r.get("self_call", ""), r.get("note", "")))
    known = {t.zh for t in project.terms}
    for t in data.get("terms", []):
        if t.get("zh") and t["zh"] not in known:
            project.terms.append(Term(t["zh"], hanviet.convert(t["zh"]) or t.get("vi", ""), t.get("note", "")))
            known.add(t["zh"])


# -------------------------------------------------------------- translate

def translate(project: Project, ctx: JobContext, client: LLMClient | None = None, only_ids: Iterable[int] | None = None,
              batch_size: int = 40, context_lines: int = 8, overwrite: bool = True, save_each: bool = True) -> dict:
    client = client or LLMClient()
    compute_budgets(project)
    all_segs = [s for s in sorted(project.segments, key=lambda s: s.start) if s.zh.strip()]
    ids = set(only_ids) if only_ids is not None else None
    todo = [s for s in all_segs if (ids is None or s.id in ids) and (overwrite or not s.vi_sub)]
    if not todo:
        return {"translated": 0}
    system = _system_prompt(project)
    pos = {s.id: i for i, s in enumerate(all_segs)}
    chunks = _chunks(todo, batch_size)
    done = 0
    for ci, chunk in enumerate(chunks):
        ctx.progress(ci / len(chunks), f"Dịch phần {ci + 1}/{len(chunks)} ({client.label()})", force=True)
        first, last = pos[chunk[0].id], pos[chunk[-1].id]
        prev = [s for s in all_segs[max(0, first - context_lines):first] if s.vi_sub]
        nxt = all_segs[last + 1:last + 1 + 4]
        payload = [{"id": s.id, "speaker": _speaker_name(project, s), "zh": s.zh,
                    "dur": round(s.duration / 1000, 2), "max_syl": s.max_syllables or 12,
                    **({"note": "chữ trên màn hình, không phải lời thoại"} if s.kind == "onscreen" else {})}
                   for s in chunk]
        user_parts = []
        summary = _scene_summary(project, [s.id for s in chunk])
        if summary:
            user_parts.append("SCENE: " + summary)
        if prev:
            user_parts.append("PREVIOUS LINES (context only):\n" + "\n".join(
                f"[{s.id}] {_speaker_name(project, s)}: {s.zh} → {s.vi_sub}" for s in prev))
        user_parts.append("TRANSLATE THESE LINES:\n" + "\n".join(json.dumps(p, ensure_ascii=False) for p in payload))
        if nxt:
            user_parts.append("NEXT LINES (context only, do not translate):\n" + "\n".join(
                f"[{s.id}] {_speaker_name(project, s)}: {s.zh}" for s in nxt))
        data = client.complete_json(system, "\n\n".join(user_parts), _LINE_SCHEMA)
        got = {int(x["id"]): x for x in data.get("lines", []) if "id" in x}
        missing = [s for s in chunk if s.id not in got]
        if missing and len(missing) < len(chunk):
            ctx.warn(f"Thiếu {len(missing)} câu trong kết quả, dịch lại các câu này.")
            retry = client.complete_json(system, "TRANSLATE THESE LINES:\n" + "\n".join(
                json.dumps(p, ensure_ascii=False) for p in payload if p["id"] in {m.id for m in missing}), _LINE_SCHEMA)
            for x in retry.get("lines", []):
                got[int(x["id"])] = x
        for s in chunk:
            x = got.get(s.id)
            if not x:
                s.add_flag("untranslated")
                continue
            s.vi_sub = (x.get("vi_sub") or "").strip()
            s.vi_dub = (x.get("vi_dub") or s.vi_sub).strip()
            ad = _char_by_name(project, x.get("addressee", ""))
            s.addressee = ad.id if ad else (x.get("addressee") or "")
            s.remove_flag("untranslated")
            if s.kind == "onscreen":
                s.vi_dub = ""
            done += 1
        if save_each:
            project.save()
    # dubbing lines that are still too long
    too_long = [s for s in todo if s.vi_dub and s.max_syllables and count_vi_syllables(s.vi_dub) > s.max_syllables * 1.15]
    if too_long:
        ctx.log(f"{len(too_long)} câu lồng tiếng dài hơn ngân sách âm tiết → rút gọn")
        with ctx.sub(0.9, 1.0):
            shorten(project, ctx, client, [s.id for s in too_long])
    ctx.progress(1.0, f"Đã dịch {done} câu", force=True)
    return {"translated": done, "shortened": len(too_long)}


def shorten(project: Project, ctx: JobContext, client: LLMClient | None = None, ids: Iterable[int] | None = None,
            factor: float = 1.0) -> int:
    client = client or LLMClient()
    ids = set(ids) if ids is not None else {s.id for s in project.segments if "need_shorten" in s.flags}
    segs = [s for s in project.segments if s.id in ids and s.vi_dub]
    if not segs:
        return 0
    system = _system_prompt(project)
    n = 0
    for chunk in _chunks(segs, 40):
        payload = []
        for s in chunk:
            cur = count_vi_syllables(s.vi_dub)
            target = max(2, int(min(s.max_syllables or cur, cur) * factor))
            if "need_shorten" in s.flags:
                target = max(2, min(target, int(cur * 0.8)))
            payload.append({"id": s.id, "speaker": _speaker_name(project, s), "zh": s.zh, "vi_dub": s.vi_dub,
                            "syllables": cur, "max_syl": target})
        user = ("Rewrite each vi_dub so it has at most max_syl syllables, keeping the meaning, tone and the same "
                "forms of address. Natural spoken Vietnamese. Reply JSON: {\"lines\": [{\"id\": int, \"vi_dub\": str}]}\n"
                + "\n".join(json.dumps(p, ensure_ascii=False) for p in payload))
        data = client.complete_json(system, user, _SHORTEN_SCHEMA)
        for x in data.get("lines", []):
            s = project.segment(int(x.get("id", -1)))
            if s and x.get("vi_dub"):
                s.vi_dub = x["vi_dub"].strip()
                n += 1
        ctx.progress(1.0, f"Đã rút gọn {n} câu")
    return n


def qa(project: Project, ctx: JobContext, client: LLMClient | None = None, auto_apply: bool = False) -> int:
    client = client or LLMClient()
    segs = [s for s in sorted(project.segments, key=lambda s: s.start) if s.vi_sub]
    system = ("You are a senior Vietnamese subtitle editor reviewing a Chinese→Vietnamese translation.\n"
              + bible_text(project) +
              "\nReport only real problems: wrong forms of address (xưng hô) for the speaker/listener pair, "
              "inconsistent names or terms, mistranslations, missing meaning, unnatural Vietnamese, register mixing. "
              "type ∈ xung_ho | ten_thuat_ngu | sai_nghia | thieu_y | van_phong. fix_sub / fix_dub = corrected text "
              "(empty if not applicable). Write message in Vietnamese. Reply JSON: {\"issues\": [...]}")
    issues = 0
    for s in segs:
        s.remove_flag("qa")
        s.qa_note = ""
    chunks = _chunks(segs, 80)
    for ci, chunk in enumerate(chunks):
        ctx.progress(ci / len(chunks), f"Soát bản dịch {ci + 1}/{len(chunks)}", force=True)
        lines = "\n".join(json.dumps({"id": s.id, "speaker": _speaker_name(project, s),
                                      "listener": (project.character(s.addressee).display_name() if project.character(s.addressee) else s.addressee),
                                      "zh": s.zh, "vi_sub": s.vi_sub, "vi_dub": s.vi_dub}, ensure_ascii=False) for s in chunk)
        data = client.complete_json(system, lines, _QA_SCHEMA)
        for it in data.get("issues", []):
            s = project.segment(int(it.get("id", -1)))
            if not s:
                continue
            s.add_flag("qa")
            s.qa_note = f"[{it.get('type', '')}] {it.get('message', '')}"
            if auto_apply:
                if it.get("fix_sub"):
                    s.vi_sub = it["fix_sub"]
                if it.get("fix_dub"):
                    s.vi_dub = it["fix_dub"]
            else:
                fixes = []
                if it.get("fix_sub"):
                    fixes.append(f"Gợi ý phụ đề: {it['fix_sub']}")
                if it.get("fix_dub"):
                    fixes.append(f"Gợi ý lồng tiếng: {it['fix_dub']}")
                if fixes:
                    s.qa_note += "\n" + "\n".join(fixes)
            issues += 1
    ctx.progress(1.0, f"Soát xong: {issues} vấn đề", force=True)
    return issues
