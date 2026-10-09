"""Floor plans in the FieldTab `fieldtab.plan/1` format (docs/PLAN_FORMAT.md): metres, origin top-left, x right,
y down, up to 300 wall / door / window / beam segments. Validation is shared with the FieldTab Desk so a plan made
here opens on the tablet and the other way round."""
from __future__ import annotations

import json
import math
import re

FORMAT = "fieldtab.plan/1"
KINDS = ("wall", "door", "window", "beam")
MIN_M, MAX_M = 0.5, 100.0
MAX_ITEMS = 300
MAX_NAME, MAX_LABEL = 64, 16
MAX_BYTES = 256 * 1024
TOL = 0.01

PROMPT = """\
You convert a floor plan image into JSON. Output ONLY a JSON object, no prose, in this format:
{"format": "fieldtab.plan/1", "name": "...", "width_m": W, "length_m": L, "items": [{"kind": "wall"|"door"|"window"|"beam", "x1": .., "y1": .., "x2": .., "y2": .., "label": "optional"}]}.
Units are metres. Origin is the top-left corner of the drawing's bounding box; x grows right, y grows down.
Take sizes from the dimension lines and numbers on the plan; if there are none, use the scale bar; if there
is neither, assume a standard interior door is 0.9 m wide and say nothing about it. Every wall is one straight
segment along its centre line (split walls at corners and at T-junctions). A door or window is a segment the
width of the opening on the wall line. A beam is a segment along its axis. Round to 0.05 m. At most 300 items.
width_m and length_m are the overall size of the bounding box. Keep every coordinate inside it.
"""

# Added after PROMPT when the user knows some real sizes: a vision LLM guesses the scale badly from a photo, one
# measured length (a corridor, a room) fixes it for the whole drawing.
_KNOWN_HEAD = """\
Known real sizes (use them to set the scale of the whole drawing; they win over anything you infer, and every
other size must stay consistent with them):"""

_PHOTO_NOTES = """\
The image is a photo or a scan: correct perspective and rotation first, so walls are straight and parallel.
Ignore furniture, people, text blocks, legends, evacuation arrows, fire equipment symbols and stair hatching;
draw stair and shaft outlines as walls. Outer walls must form a closed outline."""


def _clean(text, limit: int) -> str:
    """User text for the prompt: printable, one paragraph per line, bounded."""
    text = "".join(ch for ch in str(text or "") if ch == "\n" or ch.isprintable())
    lines = [ln.strip(" -*\t") for ln in text.splitlines()]
    return "\n".join(f"- {ln}" for ln in lines if ln)[:limit]


def build_prompt(known: str = "", width_m=None, length_m=None, notes: str = "", name: str = "") -> str:
    """PROMPT plus what the user knows about this plan: measured lengths, the overall size, a name, notes."""
    parts = [PROMPT.rstrip(), _PHOTO_NOTES]
    sizes = _clean(known, 1500)
    w, ln = _number(width_m), _number(length_m)
    if w and ln and MIN_M <= w <= MAX_M and MIN_M <= ln <= MAX_M:
        sizes = (sizes + "\n" if sizes else "") + f"- The whole plan is {_r(w)} m wide (x) and {_r(ln)} m long (y): use these as width_m and length_m."
    if sizes:
        parts.append(_KNOWN_HEAD + "\n" + sizes)
    name = _clean(name, MAX_NAME).lstrip("- ")
    if name:
        parts.append(f'Use "{name}" as the name.')
    extra = _clean(notes, 1000)
    if extra:
        parts.append("Notes about this plan:\n" + extra)
    return "\n\n".join(parts) + "\n"


# ---------- validation ----------

def _extract(text: str):
    """JSON as an LLM tends to answer: bare, in ```json fences, or with prose around it -> the first object."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object found in the text")


def _number(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _r(v: float) -> float:
    r = round(v, 2)
    return int(r) if r == int(r) else r


def validate_plan(obj) -> tuple[dict | None, list[str]]:
    """A plan (object or text) -> (normalized plan, []) or (None, readable errors)."""
    if isinstance(obj, (str, bytes)):
        text = obj.decode("utf-8", "replace") if isinstance(obj, bytes) else obj
        if len(text.encode()) > MAX_BYTES * 4:
            return None, ["text is too big for a plan"]
        try:
            obj = _extract(text)
        except ValueError as e:
            return None, [str(e)]
    if not isinstance(obj, dict):
        return None, ["a plan is a JSON object"]
    errs: list[str] = []
    if obj.get("format") != FORMAT:
        errs.append(f'format must be "{FORMAT}" (got {json.dumps(obj.get("format"))})')
    name = obj.get("name", "")
    if name is None:
        name = ""
    if not isinstance(name, str):
        errs.append("name must be text")
        name = ""
    elif len(name) > MAX_NAME:
        errs.append(f"name is longer than {MAX_NAME} characters")
    size = {}
    for key in ("width_m", "length_m"):
        v = _number(obj.get(key))
        if v is None:
            errs.append(f"{key} must be a number")
        elif not MIN_M <= v <= MAX_M:
            errs.append(f"{key} {v:g} is outside {MIN_M:g}..{MAX_M:g} m")
        else:
            size[key] = v
    items = obj.get("items")
    out_items = []
    if not isinstance(items, list):
        errs.append("items must be a list")
        items = []
    elif len(items) > MAX_ITEMS:
        errs.append(f"{len(items)} items, at most {MAX_ITEMS}")
        items = []
    w, ln = size.get("width_m"), size.get("length_m")
    for i, it in enumerate(items, 1):
        where = f"item {i}"
        if not isinstance(it, dict):
            errs.append(f"{where}: not an object")
            continue
        kind = it.get("kind")
        if kind not in KINDS:
            errs.append(f"{where}: kind {json.dumps(kind)} is not one of {', '.join(KINDS)}")
            continue
        where = f"item {i} ({kind})"
        pt = {}
        for key in ("x1", "y1", "x2", "y2"):
            v = _number(it.get(key))
            if v is None:
                errs.append(f"{where}: {key} must be a number")
                continue
            hi = w if key[0] == "x" else ln
            if v < -TOL or (hi is not None and v > hi + TOL):
                errs.append(f"{where}: {key}={v:g} is outside 0..{hi:g} m" if hi is not None
                            else f"{where}: {key}={v:g} is negative")
                continue
            pt[key] = _r(min(max(v, 0.0), hi if hi is not None else v))
        if len(pt) < 4:
            continue
        if pt["x1"] == pt["x2"] and pt["y1"] == pt["y2"]:
            errs.append(f"{where}: both ends are the same point")
            continue
        item = {"kind": kind, **pt}
        label = it.get("label")
        if label not in (None, ""):
            if not isinstance(label, str):
                errs.append(f"{where}: label must be text")
            elif len(label) > MAX_LABEL:
                errs.append(f"{where}: label is longer than {MAX_LABEL} characters")
            else:
                item["label"] = label
        out_items.append(item)
    if errs:
        if len(errs) > 25:
            errs = errs[:25] + [f"... and {len(errs) - 25} more"]
        return None, errs
    plan = {"format": FORMAT, "name": name, "width_m": _r(w), "length_m": _r(ln), "items": out_items}
    if len(_compact(plan)) > MAX_BYTES:
        return None, [f"the plan is bigger than {MAX_BYTES // 1024} KB"]
    return plan, []


def _compact(plan: dict) -> bytes:
    return json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def compact(plan: dict) -> bytes:
    return _compact(plan)
