"""Scan analysis: channel spans, channel load, recommendations, security classes and the issues a survey
report lists. Pure functions over AP records (dicts), shared by the live views, the survey and the report.

An AP record has at least: bssid, ssid, channel, band ("2.4" | "5" | "6"), width (MHz), rssi (dBm);
optional: second ("above" | "below"), security (label), hidden, noise, phy.
Channel numbers are 5 MHz apart, so a 20 MHz channel spans +-2 channel numbers around its centre.
"""
from __future__ import annotations

import re

# Signal thresholds (dBm), the same bands as the FieldTab tablet map.
EXCELLENT, GOOD, FAIR, WEAK = -60, -67, -75, -82
STRONG_NEIGHBOUR = -80      # an AP this strong or stronger counts as an interferer on its channel
CONGESTED_RADIOS = 3        # this many strong radios on one primary channel = co-channel congestion
LOW_SNR = 20                # dB

CH24_PREFERRED = (1, 6, 11)
CH5_NON_DFS = (36, 40, 44, 48, 149, 153, 157, 161, 165)
CH5_ALL = (36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
           149, 153, 157, 161, 165, 169, 173, 177)
CH5_DFS = tuple(c for c in CH5_ALL if 52 <= c <= 144)
CH6_PSC = tuple(range(5, 234, 16))
CH6_ALL = tuple(range(1, 234, 4))
BANDS = ("2.4", "5", "6")


def band_of(channel, hint: str | None = None) -> str:
    """Band from an explicit hint or the channel number (6 GHz channels overlap 5 GHz numbers: hint wins)."""
    if hint in BANDS:
        return hint
    try:
        ch = int(channel)
    except (TypeError, ValueError):
        return "?"
    if 1 <= ch <= 14:
        return "2.4"
    if 32 <= ch <= 177:
        return "5"
    return "6" if ch > 177 else "?"


def freq_mhz(channel: int, band: str) -> int:
    if band == "2.4":
        return 2484 if channel == 14 else 2407 + 5 * channel
    return (5950 if band == "6" else 5000) + 5 * channel


def _block_center(ch: int, band: str, n: int) -> int:
    """Centre channel of the n x 20 MHz block holding primary channel ch (5 and 6 GHz channelisation)."""
    if band == "6":
        start = 1
    elif ch >= 149:
        start = 149
    elif ch >= 100:
        start = 100
    else:
        start = 36
    idx = (ch - start) // (4 * n)
    return start + idx * 4 * n + (n - 1) * 2


def span(ap: dict) -> tuple[float, float, float]:
    """(lo, hi, centre) of the channel numbers an AP occupies."""
    ch = int(ap.get("channel") or 0)
    band = band_of(ch, ap.get("band"))
    width = int(ap.get("width") or 20)
    n = max(1, width // 20)
    if band == "2.4":
        if width >= 40:
            second = ap.get("second") or ("above" if ch <= 7 else "below")
            c = ch + 2 if second == "above" else ch - 2
            return c - 4, c + 4, c
        return ch - 2, ch + 2, ch
    c = _block_center(ch, band, n) if n > 1 else ch
    return c - 2 * n, c + 2 * n, c


def overlaps(a: dict, b: dict) -> bool:
    if band_of(a.get("channel"), a.get("band")) != band_of(b.get("channel"), b.get("band")):
        return False
    alo, ahi, _ = span(a)
    blo, bhi, _ = span(b)
    return min(ahi, bhi) - max(alo, blo) > 0


def radio_key(bssid: str) -> str:
    """Virtual APs of one radio share the MAC except the low bits of the last byte: first 5 bytes."""
    return ":".join(str(bssid or "").lower().split(":")[:5])


def _rssi(ap: dict) -> int:
    for k in ("rssi", "rssi_max", "rssi_last"):
        v = ap.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return -100


def channel_list(band: str, aps: list[dict]) -> list[int]:
    used = {int(a["channel"]) for a in aps if a.get("channel") and band_of(a["channel"], a.get("band")) == band}
    if band == "2.4":
        base = set(range(1, 14)) | ({14} if 14 in used else set())
    elif band == "5":
        base = set(CH5_ALL)
    else:
        base = set(CH6_PSC)
    return sorted(base | used)


def channel_stats(aps: list[dict], band: str = "2.4") -> list[dict]:
    """Per channel of a band: APs on it (primary), strongest RSSI and the overlap load. Every AP adds
    max(1, rssi + 100) to each channel inside its span (the FieldTab tablet's weighting: a 20 MHz AP on
    2.4 GHz loads +-2 channels, a 40 MHz one 4 more on its secondary side)."""
    mine = [a for a in aps if a.get("channel") and band_of(a["channel"], a.get("band")) == band]
    st = {c: {"channel": c, "band": band, "aps": 0, "max_rssi": -100, "load": 0} for c in channel_list(band, mine)}
    for a in mine:
        c, r = int(a["channel"]), _rssi(a)
        st[c]["aps"] += 1
        st[c]["max_rssi"] = max(st[c]["max_rssi"], r)
        lo, hi, _ = span(a)
        w = max(1, r + 100)
        for k, s in st.items():
            if lo <= k <= hi:
                s["load"] += w
    return [st[k] for k in sorted(st)]


def _best(stats: list[dict], pool) -> int | None:
    best = None
    for s in stats:
        if s["channel"] in pool and (best is None or (s["load"], s["aps"]) < (best["load"], best["aps"])):
            best = s
    return best["channel"] if best else None


def _best_block(stats: list[dict], band: str, n: int, pool) -> dict | None:
    """Least loaded n x 20 MHz block whose channels are all in pool: {channels, centre, load}."""
    load = {s["channel"]: s["load"] for s in stats}
    blocks: dict[int, list[int]] = {}
    for c in pool:
        blocks.setdefault(_block_center(c, band, n), []).append(c)
    best = None
    for centre, chans in sorted(blocks.items()):
        if len(chans) != n:
            continue
        total = sum(load.get(c, 0) for c in chans)
        if best is None or total < best["load"]:
            best = {"channels": sorted(chans), "centre": centre, "load": total}
    return best


def recommend(aps: list[dict]) -> dict:
    """Least loaded channels per band: 2.4 GHz among 1/6/11, 5 GHz non-DFS first (20 and 80 MHz) and with DFS,
    6 GHz among the preferred scanning channels (PSC)."""
    out: dict = {}
    s24 = channel_stats(aps, "2.4")
    out["2.4"] = {"channel": _best(s24, CH24_PREFERRED), "pool": "1/6/11"}
    s5 = channel_stats(aps, "5")
    out["5"] = {"channel": _best(s5, CH5_NON_DFS), "pool": "non-DFS",
                "channel_dfs": _best(s5, CH5_ALL),
                "block40": _best_block(s5, "5", 2, CH5_NON_DFS),
                "block80": _best_block(s5, "5", 4, CH5_NON_DFS)}
    s6 = channel_stats(aps, "6")
    out["6"] = {"channel": _best(s6, CH6_PSC), "pool": "PSC"}
    return out


# ---------- security ----------

_PART = re.compile(r"^(WPA[123]?|WEP|OPEN|OWE|WAPI)", re.IGNORECASE)


def sec_flags(label: str) -> dict:
    """Security label ("WPA2/WPA3", "WPA/WPA2-ENT", "OPEN", "OWE", ...) -> what it allows."""
    s = str(label or "").upper().strip()
    ent = "ENT" in s or "802.1X" in s or "EAP" in s
    parts = [p.strip() for p in s.replace("-ENT192", "").replace("-ENT", "").split("/") if p.strip()]
    f = {"open": False, "owe": False, "wep": False, "wpa1": False, "wpa2": False, "wpa3": False,
         "enterprise": ent, "unknown": False}
    for p in parts:
        m = _PART.match(p)
        key = m.group(1) if m else ""
        if key == "OPEN" or p in ("NONE", "OPN"):
            f["open"] = True
        elif key == "OWE":
            f["owe"] = True
        elif key == "WEP":
            f["wep"] = True
        elif key == "WPA":
            f["wpa1"] = True
        elif key in ("WPA2", "WPA3"):
            f[key.lower()] = True
    if not any(f[k] for k in ("open", "owe", "wep", "wpa1", "wpa2", "wpa3")):
        f["unknown"] = not ent
        if ent:
            f["wpa2"] = True
    return f


def sec_class(label: str) -> str:
    """One word for filters and colours: open, wep, wpa1, wpa2, wpa3, ent, owe, unknown (weakest allowed wins)."""
    f = sec_flags(label)
    if f["open"]:
        return "open"
    if f["wep"]:
        return "wep"
    if f["wpa1"]:
        return "wpa1"
    if f["enterprise"]:
        return "ent"
    if f["owe"]:
        return "owe"
    if f["wpa2"]:
        return "wpa2"
    if f["wpa3"]:
        return "wpa3"
    return "unknown"


def quality(rssi: float) -> str:
    if rssi >= EXCELLENT:
        return "excellent"
    if rssi >= GOOD:
        return "good"
    if rssi >= FAIR:
        return "fair"
    if rssi >= WEAK:
        return "weak"
    return "poor"


# ---------- interference ----------

def cochannel(aps: list[dict], min_rssi: int = STRONG_NEIGHBOUR) -> list[dict]:
    """Primary channels shared by several physical radios heard at min_rssi or better."""
    by: dict[tuple, dict] = {}
    for a in aps:
        if not a.get("channel") or _rssi(a) < min_rssi:
            continue
        band = band_of(a["channel"], a.get("band"))
        e = by.setdefault((band, int(a["channel"])), {"band": band, "channel": int(a["channel"]), "radios": set(),
                                                       "bssids": [], "ssids": set()})
        e["radios"].add(radio_key(a.get("bssid", "")))
        e["bssids"].append(a.get("bssid", ""))
        if a.get("ssid"):
            e["ssids"].add(a["ssid"])
    out = []
    for e in by.values():
        if len(e["radios"]) >= 2:
            out.append({"band": e["band"], "channel": e["channel"], "radios": len(e["radios"]),
                        "bssids": sorted(e["bssids"]), "ssids": sorted(e["ssids"])})
    return sorted(out, key=lambda e: (-e["radios"], BANDS.index(e["band"]) if e["band"] in BANDS else 9, e["channel"]))


def adjacent_overlap(aps: list[dict], min_rssi: int = STRONG_NEIGHBOUR) -> list[dict]:
    """Pairs of strong radios on different but overlapping channels (partial overlap: the worst kind on 2.4 GHz).
    Virtual APs of one radio count once (the strongest BSSID stands for it)."""
    by_radio: dict[tuple, dict] = {}
    for a in aps:
        if a.get("channel") and _rssi(a) >= min_rssi:
            k = (radio_key(a.get("bssid", "")), int(a["channel"]))
            if k not in by_radio or _rssi(a) > _rssi(by_radio[k]):
                by_radio[k] = a
    strong = list(by_radio.values())
    out, seen = [], set()
    for i, a in enumerate(strong):
        for b in strong[i + 1:]:
            if int(a["channel"]) == int(b["channel"]) or not overlaps(a, b):
                continue
            key = tuple(sorted((radio_key(a.get("bssid", "")), radio_key(b.get("bssid", "")))))
            if key in seen:
                continue
            seen.add(key)
            out.append({"band": band_of(a["channel"], a.get("band")), "a": a.get("bssid", ""), "b": b.get("bssid", ""),
                        "a_channel": int(a["channel"]), "b_channel": int(b["channel"]),
                        "a_ssid": a.get("ssid", ""), "b_ssid": b.get("ssid", "")})
    return out


# ---------- issues ----------

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _name(a: dict) -> str:
    return a.get("ssid") or "(hidden)"


def _issue(sev: str, code: str, title: str, detail: str, bssids=()) -> dict:
    return {"severity": sev, "code": code, "title": title, "detail": detail, "bssids": sorted(set(bssids))}


def _ap_list(aps: list[dict], limit: int = 6) -> str:
    names = sorted({f"{_name(a)} (ch {a.get('channel')})" for a in aps})
    more = len(names) - limit
    return ", ".join(names[:limit]) + (f" and {more} more" if more > 0 else "")


def environment_issues(aps: list[dict]) -> list[dict]:
    """Issues seen in the AP inventory alone: security, hidden SSIDs, wide 2.4 GHz channels, interference."""
    out = []
    open_ = [a for a in aps if sec_flags(a.get("security", "")).get("open")]
    if open_:
        out.append(_issue("high", "open_network", f"{len(open_)} open network BSS(s)",
                          "No encryption: traffic is readable by anyone in range. " + _ap_list(open_), [a["bssid"] for a in open_]))
    wep = [a for a in aps if sec_flags(a.get("security", "")).get("wep")]
    if wep:
        out.append(_issue("high", "wep", f"{len(wep)} WEP network BSS(s)",
                          "WEP is broken and must not be used. " + _ap_list(wep), [a["bssid"] for a in wep]))
    wpa1 = [a for a in aps if sec_flags(a.get("security", "")).get("wpa1")]
    if wpa1:
        out.append(_issue("medium", "wpa1", f"{len(wpa1)} BSS(s) allow WPA (TKIP)",
                          "WPA1/TKIP is deprecated; use WPA2 (AES) or WPA3. " + _ap_list(wpa1), [a["bssid"] for a in wpa1]))
    hidden = [a for a in aps if a.get("hidden") or not a.get("ssid")]
    if hidden:
        out.append(_issue("low", "hidden_ssid", f"{len(hidden)} hidden SSID BSS(s)",
                          "Hidden SSIDs add probe traffic and give no security benefit.", [a["bssid"] for a in hidden]))
    wide = [a for a in aps if band_of(a.get("channel"), a.get("band")) == "2.4" and int(a.get("width") or 20) >= 40]
    if wide:
        out.append(_issue("medium", "wide_24", f"{len(wide)} BSS(s) use 40 MHz or wider on 2.4 GHz",
                          "2.4 GHz has room for only three non-overlapping 20 MHz channels; 40 MHz there "
                          "overlaps most of the band. " + _ap_list(wide), [a["bssid"] for a in wide]))
    odd = [a for a in aps if band_of(a.get("channel"), a.get("band")) == "2.4" and a.get("channel")
           and int(a["channel"]) not in CH24_PREFERRED and _rssi(a) >= STRONG_NEIGHBOUR]
    if odd:
        out.append(_issue("medium", "nonstandard_24", f"{len(odd)} strong 2.4 GHz BSS(s) off channels 1/6/11",
                          "Channels between 1, 6 and 11 overlap two of them at once. " + _ap_list(odd),
                          [a["bssid"] for a in odd]))
    for e in cochannel(aps):
        if e["radios"] >= CONGESTED_RADIOS:
            out.append(_issue("medium", "cochannel", f"Co-channel congestion: {e['radios']} radios on {e['band']} GHz "
                              f"channel {e['channel']}",
                              f"Radios heard at {STRONG_NEIGHBOUR} dBm or better share airtime on this channel. SSIDs: "
                              + (", ".join(e["ssids"]) or "(hidden)"), e["bssids"]))
    adj = adjacent_overlap(aps)
    if adj:
        bss = [x["a"] for x in adj] + [x["b"] for x in adj]
        pairs = sorted({f"ch {x['a_channel']} vs {x['b_channel']}" for x in adj})
        out.append(_issue("medium", "adjacent_overlap", f"{len(adj)} partially overlapping AP pair(s)",
                          "Different channels that overlap interfere without sharing airtime fairly: "
                          + ", ".join(pairs[:8]), bss))
    return sorted(out, key=lambda i: SEVERITY_ORDER[i["severity"]])


def point_best(point: dict, ssid: str | None = None) -> int | None:
    """Strongest RSSI at a survey point (of one SSID when given); None when nothing (of it) was heard."""
    vals = [a["rssi"] for a in point.get("aps") or [] if isinstance(a.get("rssi"), (int, float))
            and (not ssid or a.get("ssid") == ssid)]
    return max(vals) if vals else None


def coverage_issues(points: list[dict], ssid: str | None = None, weak: int = FAIR) -> list[dict]:
    """Weak spots of a survey: points where the best signal (of an SSID) is below `weak`, and low SNR spots."""
    out = []
    if not points:
        return out
    bad, unheard = [], []
    for p in points:
        v = point_best(p, ssid)
        if v is None:
            unheard.append(p)
        elif v < weak:
            bad.append(p)
    who = f"SSID {ssid}" if ssid else "the strongest AP"
    where = lambda ps: ", ".join(f"({p.get('x_m', 0):g}, {p.get('y_m', 0):g}) m" for p in ps[:8]) + \
        (f" and {len(ps) - 8} more" if len(ps) > 8 else "")  # noqa: E731
    if bad:
        out.append(_issue("high" if len(bad) * 4 >= len(points) else "medium", "weak_coverage",
                          f"Weak coverage at {len(bad)} of {len(points)} points",
                          f"Signal of {who} below {weak} dBm at {where(bad)}."))
    if unheard:
        out.append(_issue("high", "no_coverage", f"{len(unheard)} point(s) without {who}",
                          f"Not heard at all at {where(unheard)}."))
    low = []
    for p in points:
        noise = p.get("noise")
        v = point_best(p, ssid)
        if v is not None and isinstance(noise, (int, float)) and noise < 0 and v - noise < LOW_SNR:
            low.append(p)
    if low:
        out.append(_issue("medium", "low_snr", f"Low SNR (< {LOW_SNR} dB) at {len(low)} point(s)",
                          f"Signal-to-noise ratio of {who} is low at {where(low)}."))
    return out
