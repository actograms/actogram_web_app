#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Actogram web app (Python desktop edition) | Copyright (c) 2026 The Actogram Web Application Development Team
# SPDX-License-Identifier: MIT
"""
actogram_app.py: plotting and analysing activity rhythms and lighting records.

A Python desktop version of the Actogram web app with the same input formats,
analyses and figures:

  * input: timestamped tables (CSV / TXT: timestamp, activity[, light]) with
    "#" comments and "# Recording name: foo"; text-based AWD files; UTF-8,
    UTF-16 and legacy Chinese / Japanese / Korean encodings;
  * actograms: single / double / triple plots, bars or heat maps (grey,
    viridis, magma, cividis), light/dark shading (yellow / grey-blue or
    white / light grey), onsets, offsets, regression lines, drawn guide lines
    and a right-hand panel of row totals;
  * lighting from the light column or from a schedule of LD / LL / DD
    conditions (start date and time, light or dark first, L and D lengths)
    with suggested starts at the ends of L or D of the preceding LD;
  * analysis range, zoom and pan, onset detection (step template with
    tracking) and hand editing (Place onset, Select for fit, Draw line);
  * regression fits (one per lighting condition by default) with cycle
    numbers derived from the onset times; tau, ZT / CT phase, circular
    statistics and phase differences;
  * chi-square and Lomb-Scargle periodograms (also per fit), Morlet wavelet
    with ridge and cone of influence, activity profiles (also per fit, in ZT
    or CT), daily activity, lighting record, IS / IV / RA for all files;
  * figures as PNG, SVG, PDF or EPS and tables as CSV.

Usage
  python3 actogram_app.py                      open the desktop app (Tkinter)
  python3 actogram_app.py FILE [FILE ...]      open the app with these files
  python3 actogram_app.py --batch FILE ... --out DIR [options]
                                               analyse without a window and
                                               write figures and CSV files
  python3 actogram_app.py --help               all command-line options

Requirements: Python 3.9 or later with Tkinter, numpy and matplotlib
(pip install numpy matplotlib). Everything runs locally; no data are sent
anywhere.

Methods follow the web app (see its README and Methods tab): Sokolove &
Bushell (1978), Lomb (1976) / Scargle (1982), Torrence & Compo (1998),
Van Someren et al. (1999), Zar (2010) for circular statistics.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import io
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np

APP_NAME = "Actogram app"
APP_VERSION = "1.0 (November 2026)"
AUTHORS = "The Actogram Web Application Development Team (actograms@proton.me)"
BIN_MAX_MIN = 120          # largest bin size, minutes
MAX_FITS = 6

MONTHS = {"jan": 0, "feb": 1, "mar": 2, "apr": 3, "may": 4, "jun": 5, "jul": 6, "aug": 7,
          "sep": 8, "sept": 8, "oct": 9, "nov": 10, "dec": 11}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
NAN = float("nan")


def isnan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def modpos(a: float, m: float) -> float:
    """Modulo that is always non-negative."""
    return ((a % m) + m) % m


# =============================================================================
# Parsing
# =============================================================================

def parse_date(s: str):
    """Parse "21-nov-2000", "21-Nov-00", "2000-11-21" or "11/21/2000" -> (y, m0, d)."""
    s = str(s or "").strip()
    m = re.match(r"^(\d{1,2})[-\s/.]([A-Za-z]{3,4})[A-Za-z]*[-\s/.](\d{2,4})$", s)
    if m:
        mon = MONTHS.get(m.group(2).lower())
        if mon is None:
            return None
        y = int(m.group(3))
        if y < 100:
            y += 2000 if y < 70 else 1900
        return (y, mon, int(m.group(1)))
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        return (int(m.group(1)), int(m.group(2)) - 1, int(m.group(3)))
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        return (int(m.group(3)), int(m.group(1)) - 1, int(m.group(2)))   # US month/day/year
    return None


def parse_time(s: str):
    """Parse "HH:MM" or "HH:MM:SS" (24-hour) into minutes after midnight."""
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", str(s or "").strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + (int(m.group(3)) / 60 if m.group(3) else 0)


def utc_ms(y, m0, d, hh=0, mm=0, ss=0) -> float:
    """Milliseconds since 1970-01-01 (UTC) of a plain calendar date and clock time."""
    return (datetime(y, m0 + 1, d, tzinfo=timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1000 \
        + ((hh * 60 + mm) * 60 + ss) * 1000


def ms_to_date(ms: float) -> date:
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)).date()


@dataclass
class Record:
    """A parsed recording. Times are "absolute minutes" from 00:00 of the first calendar day."""
    fileName: str
    name: str
    format: str
    header: dict
    startUTC: float            # ms of 00:00 of the first calendar day
    t0Min: float               # absolute minute of the first sample
    intervalMin: float
    counts: np.ndarray
    light: np.ndarray | None
    nMissing: int
    warnings: list = field(default_factory=list)
    encoding: str = "UTF-8"
    encodingAuto: bool = True
    raw: bytes | None = None


def parse_awd(text: str, file_name: str) -> Record:
    """Text-based .AWD: 7 header lines (name, date, time, interval code = minutes x 4,
    age, ID, sex), then one sample per line: activity[, light]; values < 0 are missing."""
    lines = re.split(r"\r\n|\r|\n", text.lstrip("﻿"))
    warnings = []
    if len(lines) < 8:
        raise ValueError("File is too short to be an AWD file (needs 7 header lines plus data).")
    header = [l.strip() for l in lines[:7]]
    d = parse_date(header[1])
    if not d:
        raise ValueError('Line 2 should be a start date like "21-nov-2000"; found "%s".' % header[1])
    t0 = parse_time(header[2])
    if t0 is None:
        raise ValueError('Line 3 should be a start time like "04:20"; found "%s".' % header[2])
    code = header[3]
    if re.match(r"^\d+(\.\d+)?$", code) and float(code) > 0:
        interval = float(code) / 4
    else:
        warnings.append('Interval code "%s" is not numeric; assuming 1-minute bins.' % code)
        interval = 1.0
    counts, light = [], []
    has_light, n_missing, n_bad = False, 0, 0
    for raw in lines[7:]:
        raw = raw.strip()
        if not raw:
            continue
        tok = [t for t in re.split(r"[\s,;]+", raw) if t]
        try:
            a = float(tok[0])
        except ValueError:
            n_bad += 1
            a = -1.0
        l = NAN
        for t in tok[1:]:
            if t.lower() == "m":
                continue
            try:
                v = float(t)
                if math.isnan(l):
                    l, has_light = v, True
            except ValueError:
                pass
        if a < 0:
            n_missing += 1
            a = NAN
        counts.append(a)
        light.append(l)
    if n_bad:
        warnings.append("%d data line(s) could not be read and were treated as missing." % n_bad)
    if not counts:
        raise ValueError("No data lines found after the 7-line header.")
    name = header[0] or re.sub(r"\.[^.]+$", "", file_name or "recording")
    return Record(fileName=file_name or header[0] or "recording", name=name, format="Text-based AWD",
                  header={"name": header[0], "date": header[1], "time": header[2], "interval": code,
                          "age": header[4], "id": header[5], "sex": header[6]},
                  startUTC=utc_ms(*d), t0Min=t0, intervalMin=interval,
                  counts=np.array(counts, float), light=np.array(light, float) if has_light else None,
                  nMissing=n_missing, warnings=warnings)


def parse_csv(text: str, file_name: str) -> Record:
    """Timestamped table: timestamp, activity[, light], separated by commas, tabs or semicolons.
    Blank lines and "#" comments are ignored; "# Recording name: foo" sets the name (first one wins).
    The sampling interval is the median spacing between rows."""
    rows = []
    rec_name = None
    for raw in re.split(r"\r\n|\r|\n", text.lstrip("﻿")):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = re.match(r"^#\s*Recording name\s*:\s*(.*\S)\s*$", line, re.I)
            if m and rec_name is None:
                rec_name = m.group(1)
            continue
        tok = [re.sub(r'^"|"$', "", t.strip()) for t in re.split(r"[,;\t]", raw)]
        if len(tok) < 2:
            continue
        m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", tok[0])
        if not m:
            continue
        try:
            t = utc_ms(int(m.group(1)), int(m.group(2)) - 1, int(m.group(3)), int(m.group(4)), int(m.group(5)),
                       int(m.group(6)) if m.group(6) else 0)
        except ValueError:
            continue
        try:
            a = float(tok[1])
        except ValueError:
            a = NAN
        try:
            l = float(tok[2]) if len(tok) > 2 and tok[2] != "" else NAN
        except ValueError:
            l = NAN
        rows.append((t, a if (not math.isnan(a) and math.isfinite(a) and a >= 0) else NAN, l))
    if len(rows) < 3:
        raise ValueError("No rows of the form timestamp,activity[,light] were found.")
    rows.sort(key=lambda r: r[0])
    diffs = sorted(rows[i][0] - rows[i - 1][0] for i in range(1, min(len(rows), 2000)))
    dt = diffs[len(diffs) // 2]
    if not dt > 0:
        raise ValueError("Could not determine the sampling interval.")
    t0 = rows[0][0]
    n = int(round((rows[-1][0] - t0) / dt)) + 1
    counts = np.full(n, NAN)
    light = np.full(n, NAN)
    has_light = False
    for t, a, l in rows:
        i = int(round((t - t0) / dt))
        counts[i] = a
        light[i] = l
        if not math.isnan(l):
            has_light = True
    d0 = ms_to_date(t0)
    start = utc_ms(d0.year, d0.month - 1, d0.day)
    return Record(fileName=file_name, name=rec_name or re.sub(r"\.[^.]+$", "", file_name), format="CSV",
                  header={"name": file_name, "date": "", "time": "", "interval": "", "age": "", "id": "", "sex": ""},
                  startUTC=start, t0Min=(t0 - start) / 60000, intervalMin=dt / 60000,
                  counts=counts, light=light if has_light else None, nMissing=int(np.isnan(counts).sum()))


def parse_any(text: str, file_name: str) -> Record:
    """Choose the parser from the content: a timestamp in the first or second line
    (ignoring blank lines and comments) means a timestamped table; otherwise AWD."""
    first = [l.strip() for l in re.split(r"\r\n|\r|\n", text.lstrip("﻿")[:20000])]
    first = [l for l in first if l and not l.startswith("#")][:2]
    stamp = re.compile(r'^"?\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d')
    if any(stamp.match(l) for l in first):
        return parse_csv(text, file_name)
    return parse_awd(text, file_name)


# --- Text encodings ----------------------------------------------------------
TEXT_ENCODINGS = [("utf-8", "UTF-8"), ("utf-16-le", "UTF-16LE"), ("utf-16-be", "UTF-16BE"), ("gb18030", "GB18030"),
                  ("big5", "Big5"), ("shift_jis", "Shift_JIS"), ("euc_jp", "EUC-JP"), ("cp949", "EUC-KR"),
                  ("cp1252", "windows-1252")]
COMMON_HANGUL = set("가각간갈감갑강개객거건걸검것게겠겨격견결경계고곡곤골공과관광괴교구국군굴궁권귀규균그극근글금급기긴길김까깨께꼬꼭꾸꿈끄끝끼나낙난날남납낭내냉너널넘넣네녀년념녕노녹논놀농높뇌누눈뉴느는늘능니님다단달담답당대댁더던덕데도독돈돌동돼되된될두둘뒤드득든들등디따때떤떻또뚜뜻라락란람랍랑래랜램략량러럼렇레렌려력련렬렵령례로록론롭료루류륙률르른를름릅릇리린릴림립링마막만많말맞매맥머먹먼멀메며면멸명몇모목몰몸못무묵문물므미민밀바박반받발밝밤방배백버번벌범법벽변별병보복본볼봉부북분불붙비빈빌빛빠뻐뿐사삭산살삼상새색생서석선설섬성세셈셔소속손솔송쇄쇠수숙순술숨쉬스슬습승시식신실심십싸쌍써쓰씨아악안알암압앙앞애액야약양어억언얼엄업없었에엔여역연열염엽영예오옥온올옷와완왕외요욕용우욱운울움웃원월위유육윤율으은을음읍응의이익인일임입있자작잔잘잠잡장재쟁저적전절점접정제져조족존졸종좌죄주죽준줄중쥐즈즉증지직진질짐집짓징짜째쪽찌차착찬찰참창채책처척천철첩청체쳐초촉촌총최추축춘출충취측층치칙친칠침카커케코쿠크큰키타탁탄탈탐탑태택터테토통퇴투특티파판팔패퍼페편평폐포폭표푸품풍프피필하학한할함합항해핵했행향허헌험혀혁현혈협형혜호혹혼홍화확환활황회획효후훈휘휴흐흑흔흘흥희흰히힘")


def decode_text(data: bytes, label: str | None = None):
    """Decode a data file to text; returns (text, encoding name). With a BOM: UTF-8/16.
    Valid UTF-8 is read as UTF-8; otherwise GB18030, Big5, Shift_JIS, EUC-JP and EUC-KR are
    scored by their characters (kana / frequent Hangul / common ideographs count for, rare and
    other non-ASCII characters against). `label` forces an encoding."""
    if label:
        for py, nm in TEXT_ENCODINGS:
            if label in (py, nm):
                return data.decode(py, errors="replace"), nm
        raise ValueError("Unknown text encoding: " + label)
    if data[:3] == codecs.BOM_UTF8:
        return data[3:].decode("utf-8", errors="replace"), "UTF-8"
    if data[:2] == codecs.BOM_UTF16_LE:
        return data[2:].decode("utf-16-le", errors="replace"), "UTF-16LE"
    if data[:2] == codecs.BOM_UTF16_BE:
        return data[2:].decode("utf-16-be", errors="replace"), "UTF-16BE"
    try:
        return data.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        pass
    ext_pairs, i = 0, 0
    while i < len(data):
        if data[i] >= 0x80:
            if data[i] < 0xA1 or i + 1 >= len(data) or data[i + 1] < 0xA1:
                ext_pairs += 1
            i += 2
        else:
            i += 1
    best = None
    for py, nm in [("gb18030", "GB18030"), ("big5", "Big5"), ("shift_jis", "Shift_JIS"), ("euc_jp", "EUC-JP"), ("cp949", "EUC-KR")]:
        try:
            t = data.decode(py)
        except UnicodeDecodeError:
            continue
        kana = hangul = rare_h = han = rare = punct = other = 0
        for ch in t:
            cp = ord(ch)
            if cp < 0x80:
                continue
            if 0x3040 <= cp <= 0x30FF:
                kana += 1
            elif 0xAC00 <= cp <= 0xD7A3:
                hangul += 1
                if ch not in COMMON_HANGUL:
                    rare_h += 1
            elif 0x4E00 <= cp <= 0x9FFF:
                han += 1
            elif 0x3400 <= cp <= 0x4DBF:
                rare += 1
            elif 0x3000 <= cp <= 0x303F or 0xFF01 <= cp <= 0xFF5E:
                punct += 1
            else:
                other += 1
        if py in ("shift_jis", "euc_jp"):
            hw = 1 if kana else 0.4
        elif py == "cp949":
            hw = 0 if hangul else 0.4
        else:
            hw = 1
        score = 3 * kana + 3 * (hangul - rare_h) + 0.5 * rare_h + hw * han + 0.5 * punct - rare - 2 * other
        if py == "cp949":
            score -= 3 * ext_pairs
        if best is None or score > best[2]:
            best = (t, nm, score)
    if best and best[2] > 0:
        return best[0], best[1]
    return data.decode("cp1252", errors="replace"), "windows-1252"


def load_file(path: str, encoding: str | None = None) -> Record:
    with open(path, "rb") as fh:
        raw = fh.read()
    text, enc = decode_text(raw, encoding)
    rec = parse_any(text, os.path.basename(path))
    rec.encoding, rec.encodingAuto, rec.raw = enc, encoding is None, raw
    return rec


# =============================================================================
# Time formatting
# =============================================================================

def fmt_clock(minutes: float) -> str:
    m = modpos(minutes, 1440)
    h = int(m // 60)
    mm = int(round(m - h * 60))
    if mm == 60:
        return "%02d:00" % ((h + 1) % 24)
    return "%02d:%02d" % (h, mm)


def abs_date(start_ms: float, abs_min: float) -> date:
    return ms_to_date(start_ms + math.floor(abs_min / 1440) * 86400000)


def fmt_date(start_ms: float, abs_min: float) -> str:
    d = abs_date(start_ms, abs_min)
    return "%02d-%s-%d" % (d.day, MONTH_NAMES[d.month - 1], d.year)


def fmt_date_short(start_ms: float, abs_min: float) -> str:
    d = abs_date(start_ms, abs_min)
    return "%02d %s" % (d.day, MONTH_NAMES[d.month - 1])


def fmt_hm(minutes: float) -> str:
    m = int(round(minutes))
    return "%d:%02d" % (m // 60, m % 60)


def parse_hm(v: str):
    v = str(v or "").strip()
    m = re.match(r"^(\d+):(\d{1,2})$", v)
    if m and int(m.group(2)) < 60:
        return int(m.group(1)) * 60 + int(m.group(2))
    if re.match(r"^\d+(\.\d+)?$", v):
        return int(round(float(v) * 60))
    return None


def f2(v, d=2):
    return "–" if v is None or (isinstance(v, float) and not math.isfinite(v)) else ("%." + str(d) + "f") % v


def safe_name(name: str) -> str:
    """Recording name made safe for file names (letters and digits of any script kept)."""
    s = unicodedata.normalize("NFC", str(name))
    s = re.sub(r"[^\w.\-]+", "_", s, flags=re.U)
    s = re.sub(r"^[._]+|[._]+$", "", s)[:100]
    return s or "recording"


# =============================================================================
# Binning
# =============================================================================

@dataclass
class Binned:
    binMin: float
    act: np.ndarray
    lux: np.ndarray | None
    n: int
    firstBin: int


def bin_record(rec: Record, bin_min: float) -> Binned:
    """Sum activity into bins of bin_min minutes (bin 0 starts at 00:00 of day 1). Bins with < 50%
    valid samples are missing; partially covered bins are scaled up to a full bin."""
    dt = rec.intervalMin
    n = len(rec.counts)
    end = rec.t0Min + n * dt
    nb = int(math.ceil(end / bin_min - 1e-9))
    idx = np.floor((rec.t0Min + np.arange(n) * dt + 1e-9) / bin_min).astype(int)
    ok = (idx >= 0) & (idx < nb)
    idx_ok = idx[ok]
    v = rec.counts[ok]
    valid = ~np.isnan(v)
    act = np.bincount(idx_ok[valid], weights=v[valid], minlength=nb).astype(float)
    cov = np.bincount(idx_ok[valid], minlength=nb).astype(float)
    expected = bin_min / dt
    act[cov < expected * 0.5] = NAN
    part = (cov >= expected * 0.5) & (cov < expected)
    act[part] = act[part] * expected / cov[part]
    lux = None
    if rec.light is not None:
        l = rec.light[ok]
        lv = ~np.isnan(l)
        s = np.bincount(idx_ok[lv], weights=l[lv], minlength=nb)
        c = np.bincount(idx_ok[lv], minlength=nb)
        with np.errstate(invalid="ignore", divide="ignore"):
            lux = np.where(c > 0, s / np.maximum(c, 1), NAN)
    return Binned(bin_min, act, lux, nb, int(math.floor(rec.t0Min / bin_min)))


# =============================================================================
# Lighting
# =============================================================================
# A schedule is a list of conditions (each lasts until the next one starts):
#   {"mode": "LD"|"LL"|"DD", "start": absolute minutes, "first": "L"|"D", "L": minutes, "D": minutes}

def normalize_schedule(sched):
    return sorted([dict(s) for s in (sched or [])], key=lambda s: s["start"])


def ld_lights_on(seg) -> float:
    return seg["start"] + (seg["D"] if seg.get("first") == "D" else 0)


def ld_boundaries(prev, kind: str, until: float, max_n: int = 1000):
    """Ends of D (kind "on", lights-on) or ends of L (kind "off", lights-off) of LD condition `prev`."""
    out = []
    if not prev or prev["mode"] != "LD":
        return out
    T = prev["L"] + prev["D"]
    if not (T > 0 and prev["L"] > 0 and prev["D"] > 0):
        return out
    t = ld_lights_on(prev) + (prev["L"] if kind == "off" else 0)
    while t - T >= prev["start"]:
        t -= T
    while t < prev["start"]:
        t += T
    while t <= until and len(out) < max_n:
        out.append(t)
        t += T
    return out


def schedule_state(sched, abs_min: float, extend_first=True) -> float:
    if not sched:
        return NAN
    seg = None
    for s in sched:
        if s["start"] <= abs_min:
            seg = s
    if seg is None:
        if extend_first is False:
            return NAN
        seg = sched[0]
    if seg["mode"] == "DD":
        return 0.0
    if seg["mode"] == "LL":
        return 1.0
    T = seg["L"] + seg["D"]
    if not T > 0:
        return NAN
    return 1.0 if modpos(abs_min - ld_lights_on(seg), T) < seg["L"] else 0.0


def light_states(rec: Record, B: Binned, source: str, threshold: float, sched, extend_first=True) -> np.ndarray:
    """Per-bin light state: 1 = light, 0 = dark, NaN = unknown."""
    if source == "recorded" and B.lux is not None:
        return np.where(np.isnan(B.lux), NAN, (B.lux >= threshold).astype(float))
    if source == "schedule":
        s = normalize_schedule(sched)
        return np.array([schedule_state(s, (b + 0.5) * B.binMin, extend_first) for b in range(B.n)], float)
    return np.full(B.n, NAN)


def detect_lighting(rec: Record, threshold: float):
    """Classify each calendar day as LD / LL / DD from the light column and merge consistent days."""
    if rec.light is None:
        return {"days": [], "segments": []}
    dt = rec.intervalMin
    n = len(rec.counts)
    n_days = int(math.ceil((rec.t0Min + n * dt) / 1440))
    days = [{"day": d + 1, "n": 0, "nLight": 0, "sumLux": 0.0, "on": [], "off": []} for d in range(n_days)]
    prev = None
    for i in range(n):
        t = rec.t0Min + i * dt
        d = int(t // 1440)
        l = rec.light[i]
        if math.isnan(l):
            prev = None
            continue
        s = 1 if l >= threshold else 0
        row = days[d]
        row["n"] += 1
        if s:
            row["nLight"] += 1
            row["sumLux"] += l
        if prev is not None and s != prev:
            (row["on"] if s else row["off"]).append(t - d * 1440)
        prev = s
    per_day = 1440 / dt
    for r in days:
        r["cover"] = r["n"] / per_day
        r["frac"] = r["nLight"] / r["n"] if r["n"] else NAN
        r["meanLux"] = r["sumLux"] / r["nLight"] if r["nLight"] else 0.0
        r["lightHours"] = r["frac"] * 24 if r["n"] else NAN
        if not r["n"]:
            r["mode"] = "?"
        elif not r["on"] and not r["off"]:
            r["mode"] = "LL" if r["frac"] > 0.5 else "DD"
        else:
            r["mode"] = "LD"
        r["onMin"] = r["on"][0] if r["on"] else None
        r["offMin"] = r["off"][-1] if r["off"] else None
        r["partial"] = r["cover"] < 0.98

    # Merge days into segments with a consistent regime. Under LD, successive lights-on (and
    # lights-off) times must be whole cycles apart (within 20 min); the cycle length T comes from
    # the first two lights-on (or lights-off) of a segment, so T-cycles such as LD 15:15 are found.
    def near(iv, T):
        k = _jround(iv / T)
        return k >= 1 and abs(iv - k * T) <= 20

    def ld_check(seg, ons, offs):
        st = {"T": seg["Tmin"], "on": seg["ons"][-1] if seg["ons"] else None, "off": seg["offs"][-1] if seg["offs"] else None}

        def step(e, key):
            last = st[key]
            st[key] = e
            if last is None:
                return True
            iv = e - last
            if st["T"] is None:
                if iv < 16 * 60 or iv > 40 * 60:
                    return False
                st["T"] = iv
                return True
            return near(iv, st["T"])
        for e in ons:
            if not step(e, "on"):
                return None
        for e in offs:
            if not step(e, "off"):
                return None
        return {"T": st["T"]}

    segs = []
    for r in days:
        if r["mode"] == "?":
            continue
        base = (r["day"] - 1) * 1440
        ons = [base + x for x in r["on"]]
        offs = [base + x for x in r["off"]]
        last = segs[-1] if segs else None
        chk = None
        if last and last["mode"] == r["mode"]:
            chk = ld_check(last, ons, offs) if r["mode"] == "LD" else {"T": None}
        if chk:
            last["endDay"] = r["day"]
            if last["onMin"] is None:
                last["onMin"] = r["onMin"]
            if last["offMin"] is None:
                last["offMin"] = r["offMin"]
            if r["mode"] != "DD":
                last["luxSum"] += r["meanLux"]
                last["luxN"] += 1
            if r["mode"] == "LD":
                last["Tmin"] = chk["T"]
                last["ons"] += ons
                last["offs"] += offs
        else:
            segs.append({"mode": r["mode"], "startDay": r["day"], "endDay": r["day"], "onMin": r["onMin"], "offMin": r["offMin"],
                         "luxSum": r["meanLux"] if r["mode"] != "DD" else 0.0, "luxN": 1 if r["mode"] != "DD" else 0,
                         "Tmin": None, "ons": ons if r["mode"] == "LD" else [], "offs": offs if r["mode"] == "LD" else []})
    i = 0
    while i < len(segs) - 1:
        if segs[i]["startDay"] == segs[i]["endDay"] and len(segs) > 1 and i > 0:
            segs[i + 1]["startDay"] = segs[i]["startDay"]
            segs.pop(i)
            i -= 1
        i += 1
    for s in segs:
        s["meanLux"] = s["luxSum"] / s["luxN"] if s["luxN"] else 0.0
        if s["mode"] == "LD":
            T = s["Tmin"] or 1440
            s["Tmin"] = T
            s["T"] = T / 60
            s["onAbs"] = s["ons"][0] if s["ons"] else None
            off = next((x for x in s["offs"] if s["onAbs"] is not None and x > s["onAbs"]), None)
            if s["onAbs"] is not None and off is not None:
                s["L"] = modpos(off - s["onAbs"], T) / 60
                s["D"] = T / 60 - s["L"]
        del s["ons"], s["offs"]
    return {"days": days, "segments": segs}


def segments_to_schedule(segs, rec: Record | None = None, threshold: float = 10):
    """Detected segments -> editable schedule; LL/DD starts refined to the actual transition."""
    out = []
    for s in segs:
        day0 = (s["startDay"] - 1) * 1440
        if s["mode"] == "LD":
            L = _jround(s["L"] * 60) if "L" in s else 720
            D = _jround(s["D"] * 60) if "D" in s else (s.get("Tmin") or 1440) - L
            start = day0 + (_jround(s["onMin"]) if s["onMin"] is not None else 420)
            if s.get("onAbs") is not None:
                # first lights-on of the segment, moved back by whole cycles to its first day
                T = s.get("Tmin") or 1440
                start = s["onAbs"]
                while start - T >= day0:
                    start -= T
                start = _jround(start)
            out.append({"mode": "LD", "start": start, "first": "L", "L": L, "D": D})
        else:
            out.append({"mode": s["mode"], "start": day0, "first": "L", "L": 720, "D": 720})
    if rec is not None and rec.light is not None:
        dt, n = rec.intervalMin, len(rec.counts)

        def state(i):
            l = rec.light[i]
            return -1 if math.isnan(l) else (1 if l >= threshold else 0)
        for k, c in enumerate(out):
            if k == 0 or c["mode"] == "LD":
                continue
            want = 1 if c["mode"] == "LL" else 0
            lo = max(0, int((out[k - 1]["start"] - rec.t0Min) // dt))
            i = min(n - 1, int((c["start"] - rec.t0Min) // dt))
            while i > lo and state(i) != 1 - want:
                i -= 1
            if state(i) == 1 - want:
                c["start"] = rec.t0Min + (i + 1) * dt
    return out


# =============================================================================
# Statistics
# =============================================================================

def norm_inv(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239]
    b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572]
    c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416]
    pl = 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p <= 1 - pl:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)


def chi2_inv(p: float, df: float) -> float:
    """Chi-square quantile (Wilson-Hilferty)."""
    z = norm_inv(p)
    h = 2 / (9 * df)
    return df * (1 - h + z * math.sqrt(h)) ** 3


def linfit(xs, ys):
    """Least squares y = a + b x -> dict(slope, intercept, se, r2, n, sd) or None."""
    x = np.asarray(xs, float)
    y = np.asarray(ys, float)
    n = len(x)
    if n < 2:
        return None
    mx, my = x.mean(), y.mean()
    sxx = ((x - mx) ** 2).sum()
    if sxx == 0:
        return None
    sxy = ((x - mx) * (y - my)).sum()
    syy = ((y - my) ** 2).sum()
    b = sxy / sxx
    a = my - b * mx
    sse = ((y - (a + b * x)) ** 2).sum()
    return {"slope": float(b), "intercept": float(a), "se": float(math.sqrt(sse / (n - 2) / sxx)) if n > 2 else NAN,
            "r2": float(1 - sse / syy) if syy > 0 else 1.0, "n": n, "sd": float(math.sqrt(sse / (n - 2))) if n > 2 else NAN}


def circular_stats(degs):
    """Mean direction, mean vector length R, circular SD and the Rayleigh test (Zar)."""
    n = len(degs)
    if not n:
        return None
    r = np.radians(np.asarray(degs, float))
    sx, sy = np.cos(r).sum(), np.sin(r).sum()
    R = math.hypot(sx, sy) / n
    mean = modpos(math.degrees(math.atan2(sy, sx)), 360)
    csd = math.degrees(math.sqrt(-2 * math.log(R))) if R > 0 else math.inf
    Rn, Z = n * R, n * R * R
    p = min(1.0, math.exp(math.sqrt(1 + 4 * n + 4 * (n * n - Rn * Rn)) - (1 + 2 * n))) if n >= 2 else NAN
    return {"n": n, "R": R, "mean": mean, "csdDeg": csd, "Z": Z, "p": p}


def wrap180(d: float) -> float:
    return modpos(d + 180, 360) - 180


def fmt_p(p) -> str:
    return "–" if p is None or not math.isfinite(p) else ("< 0.001" if p < 0.001 else "%.3f" % p)


def quantile(arr, q: float) -> float:
    a = np.asarray(arr, float)
    a = a[~np.isnan(a)]
    return float(np.quantile(a, q)) if a.size else NAN


# =============================================================================
# Periodograms
# =============================================================================

def chi_square_periodogram(x, bin_min, pmin_h, pmax_h, alpha):
    """Sokolove-Bushell chi-square periodogram with missing data:
    Qp = sum_h n_h (M_h - M)^2 / [sum (X - M)^2 / N]; critical value chi2(1 - alpha, P - 1)."""
    x = np.asarray(x, float)
    ok = ~np.isnan(x)
    N = int(ok.sum())
    out = []
    if N < 10:
        return {"points": out, "peak": None}
    M = x[ok].mean()
    ss = ((x[ok] - M) ** 2).sum()
    if ss == 0:
        return {"points": out, "peak": None}
    idx = np.nonzero(ok)[0]
    v = x[ok]
    pmin = max(2, int(round(pmin_h * 60 / bin_min)))
    pmax = int(round(pmax_h * 60 / bin_min))
    for P in range(pmin, pmax + 1):
        if P * 2 > len(x):
            break
        h = idx % P
        s = np.bincount(h, weights=v, minlength=P)
        c = np.bincount(h, minlength=P)
        nz = c > 0
        mh = s[nz] / c[nz]
        Qp = float((c[nz] * (mh - M) ** 2).sum() / (ss / N))
        crit = chi2_inv(1 - alpha, P - 1)
        out.append({"periodH": P * bin_min / 60, "value": Qp, "crit": crit, "amp": Qp - crit})
    peak = max(out, key=lambda p: p["amp"]) if out else None
    if peak:
        peak = dict(peak, significant=peak["amp"] > 0)
    return {"points": out, "peak": peak}


def lomb_scargle(x, bin_min, pmin_h, pmax_h, alpha, step_h):
    """Lomb-Scargle periodogram normalised by the variance; false-alarm threshold for alpha."""
    x = np.asarray(x, float)
    ok = ~np.isnan(x)
    t = np.nonzero(ok)[0] * bin_min / 60
    y = x[ok].copy()
    N = len(y)
    out = []
    if N < 10:
        return {"points": out, "peak": None}
    y -= y.mean()
    v2 = (y * y).sum() / (N - 1)
    if v2 == 0:
        return {"points": out, "peak": None}
    M = min(N, int(round((pmax_h - pmin_h) / step_h)) + 1)
    crit = -math.log(1 - (1 - alpha) ** (1 / max(1, M)))
    P = pmin_h
    while P <= pmax_h + 1e-9:
        w = 2 * math.pi / P
        tau = math.atan2(np.sin(2 * w * t).sum(), np.cos(2 * w * t).sum()) / (2 * w)
        a = w * (t - tau)
        c, s = np.cos(a), np.sin(a)
        pw = ((y * c).sum() ** 2 / (c * c).sum() + (y * s).sum() ** 2 / (s * s).sum()) / (2 * v2)
        out.append({"periodH": P, "value": float(pw), "crit": crit, "amp": float(pw) - crit})
        P += step_h
    peak = max(out, key=lambda p: p["value"]) if out else None
    if peak:
        peak = dict(peak, significant=peak["value"] > crit)
    return {"points": out, "peak": peak}


# =============================================================================
# Wavelet (Morlet; Torrence & Compo 1998)
# =============================================================================

def morlet_cwt(x, dt_h, periods_h, omega0=6.0):
    x = np.asarray(x, float)
    N = len(x)
    mean = x.mean()
    var = ((x - mean) ** 2).mean()
    if not var > 0:
        var = 1.0
    M = 1
    while M < N:
        M <<= 1
    X = np.fft.fft(np.concatenate([x - mean, np.zeros(M - N)]))
    k = np.arange(M)
    omega = 2 * np.pi * np.where(k <= M / 2, k, k - M) / (M * dt_h)
    ff = 4 * np.pi / (omega0 + math.sqrt(2 + omega0 ** 2))
    power = np.zeros((len(periods_h), N))
    for i, P in enumerate(periods_h):
        sc = P / ff
        norm = math.sqrt(2 * math.pi * sc / dt_h) * math.pi ** -0.25
        psi = np.where(omega > 0, norm * np.exp(-0.5 * (sc * omega - omega0) ** 2), 0.0)
        w = np.fft.ifft(X * psi)[:N]
        power[i] = (w.real ** 2 + w.imag ** 2) / var
    tt = np.arange(N)
    coi = ff / math.sqrt(2) * dt_h * np.minimum(tt + 1, N - tt)
    best = power.argmax(axis=0)
    per = np.asarray(periods_h, float)
    ridge = per[best].astype(float)
    ridge_pw = power[best, tt]
    for t in range(N):
        b = best[t]
        if 0 < b < len(per) - 1:
            y0, y1, y2 = power[b - 1, t], power[b, t], power[b + 1, t]
            den = y0 - 2 * y1 + y2
            if den < 0:
                ridge[t] = per[b] + 0.5 * (y0 - y2) / den * (per[b + 1] - per[b])
    return {"power": power, "coi": coi, "ridge": ridge, "ridgePower": ridge_pw, "periods": per, "dtH": dt_h, "N": N}


# =============================================================================
# Onsets and offsets (step template with cycle-to-cycle tracking)
# =============================================================================

class _Prefix:
    def __init__(self, x):
        v = np.nan_to_num(x, nan=0.0)
        self.s = np.concatenate([[0.0], np.cumsum(v)])
        self.c = np.concatenate([[0.0], np.cumsum(~np.isnan(x))])

    def mean(self, a, b):
        """Mean of x[a:b]; NaN if fewer than 50% of the bins are valid."""
        a = max(0, a)
        b = min(len(self.s) - 1, b)
        if b <= a:
            return NAN
        n = self.c[b] - self.c[a]
        if n < (b - a) * 0.5:
            return NAN
        return (self.s[b] - self.s[a]) / n


def detect_onsets(x, bin_min, row_start, period_bins, k_first, k_last, template_h=6, search_h=4,
                  min_contrast=20, manual=None, guides=None, rest_h=3, period_at=None):
    """One onset (and offset) per row. S(t) = mean(next W h) - mean(previous W h); the first cycle
    is searched over its row, later ones within +/- search_h of the previous onset + row period,
    or within +/- tol of a drawn guide line. Manual edits ({k: bin or None}) override."""
    x = np.asarray(x, float)
    W = max(1, int(round(template_h * 60 / bin_min)))
    H = max(1, int(round(search_h * 60 / bin_min)))
    Pb = period_bins
    ps = _Prefix(x)
    gm = np.nanmean(x) if np.any(~np.isnan(x)) else 0.0
    min_s = gm * (min_contrast / 100)
    manual = manual or {}
    guides = guides or []
    n = len(x)
    # Template score for every bin (vectorised).
    idx = np.arange(n)

    def wmean(a, b):
        a = np.clip(a, 0, n)
        b = np.clip(b, 0, n)
        cnt = ps.c[b] - ps.c[a]
        tot = ps.s[b] - ps.s[a]
        with np.errstate(invalid="ignore", divide="ignore"):
            m = np.where((b > a) & (cnt >= (b - a) * 0.5), tot / np.maximum(cnt, 1), NAN)
        return m
    S = wmean(idx, idx + W) - wmean(idx - W, idx)
    res = []
    prev = None

    def per(b):
        """Cycle length expected after an onset at bin b (T-cycles; see period_at)."""
        p = period_at(b + Pb) if period_at else None
        return p or Pb
    for k in range(k_first, k_last + 1):
        rs = row_start(k)
        g = next((g for g in guides if g["k1"] <= k <= g["k2"] and g["k2"] > g["k1"]), None)
        if not g and prev is not None and per(prev) != Pb and prev + per(prev) >= rs + Pb:
            # T-cycle: the next onset falls in a later row.
            res.append({"k": k, "onsetBin": None, "offsetBin": None, "score": NAN, "manual": False,
                        "guided": False, "cyc": k, "excluded": False, "psi": None, "phase": None})
            continue
        if g:
            pred = g["b1"] + (g["b2"] - g["b1"]) * (k - g["k1"]) / (g["k2"] - g["k1"])
            lo, hi = int(round(pred - g["tolBins"])), int(round(pred + g["tolBins"])) + 1
        elif prev is None:
            lo, hi = rs, rs + Pb
        else:
            P = per(prev)
            lo, hi = prev + P - H, prev + P + H
        clipped = hi > n - W
        lo, hi = max(lo, W), min(hi, n - W)
        best, best_s = None, -math.inf
        if hi > lo:
            seg = S[lo:hi]
            if np.any(~np.isnan(seg)):
                j = int(np.nanargmax(seg))
                best, best_s = lo + j, float(seg[j])
        if clipped and best == hi - 1:      # maximum at the cut edge at the end of the record
            best = None
        onset = best if (best is not None and best_s > min_s) else None
        if onset is not None:
            R = max(1, int(round(60 / bin_min)))
            q = max(1, int(round(30 / bin_min)))
            thr = 0.4 * ps.mean(onset, onset + W)
            for t in range(max(0, onset - R), onset + R + 1):
                m = ps.mean(t, t + q)
                if t < n and not math.isnan(m) and m >= thr and x[t] >= thr:
                    onset = t
                    break
        is_manual = False
        if k in manual:
            onset, is_manual = manual[k], True
        offset = None
        if onset is not None:
            thr = 0.15 * ps.mean(onset, onset + W)
            rest_b = max(1, int(round(rest_h * 60 / bin_min)))
            lim = min(n - rest_b, onset + Pb - int(round(60 / bin_min)))
            for t in range(onset + int(round(120 / bin_min)), lim):
                m = ps.mean(t, t + rest_b)
                if not math.isnan(m) and m < thr:
                    offset = t
                    break
        res.append({"k": k, "onsetBin": onset, "offsetBin": offset, "score": best_s, "manual": is_manual,
                    "guided": bool(g) and not is_manual, "cyc": k, "excluded": False, "psi": None, "phase": None})
        if onset is not None:
            prev = onset
        elif prev is not None:
            prev = prev + per(prev)
    return res


def fold_profile(x, start_bin, period_bins):
    """Average waveform: mean, SEM and n at each phase bin."""
    x = np.asarray(x, float)
    ph = (start_bin + np.arange(len(x))) % period_bins
    ok = ~np.isnan(x)
    s = np.bincount(ph[ok], weights=x[ok], minlength=period_bins)
    s2 = np.bincount(ph[ok], weights=x[ok] ** 2, minlength=period_bins)
    c = np.bincount(ph[ok], minlength=period_bins).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.where(c > 0, s / np.maximum(c, 1), NAN)
        sd = np.where(c > 1, np.sqrt(np.maximum(0, (s2 - c * m * m) / np.maximum(c - 1, 1))), NAN)
        sem = np.where(c > 1, sd / np.sqrt(np.maximum(c, 1)), NAN)
    return m, sem, c


def non_parametric(x, bin_min, start_bin):
    """IS, IV and RA (M10, L5) from hourly totals (Van Someren et al. 1999)."""
    x = np.asarray(x, float)
    per_h = int(round(60 / bin_min))
    if per_h < 1 or abs(per_h * bin_min - 60) > 1e-6:
        return None
    first = int(math.ceil(start_bin / per_h) * per_h - start_bin)
    hours, hod = [], []
    i = first
    while i + per_h <= len(x):
        seg = x[i:i + per_h]
        hours.append(NAN if np.isnan(seg).any() else float(seg.sum()))
        hod.append(modpos((start_bin + i) / per_h, 24))
        i += per_h
    hrs = np.array(hours)
    valid = hrs[~np.isnan(hrs)]
    N = len(valid)
    if N < 48:
        return None
    mean = valid.mean()
    tot = ((valid - mean) ** 2).sum()
    if tot == 0:
        return None
    hs, hc = np.zeros(24), np.zeros(24)
    for v, h in zip(hours, hod):
        if not math.isnan(v):
            hh = int(round(h)) % 24
            hs[hh] += v
            hc[hh] += 1
    prof = np.where(hc > 0, hs / np.maximum(hc, 1), mean)
    IS = N * ((prof - mean) ** 2).sum() / (24 * tot)
    d = np.diff(hrs)
    d = d[~np.isnan(d)]
    IV = N * (d ** 2).sum() / (len(d) * tot) if len(d) else NAN
    M10 = max((sum(prof[(h + j) % 24] for j in range(10)) / 10, h) for h in range(24))
    L5 = min((sum(prof[(h + j) % 24] for j in range(5)) / 5, h) for h in range(24))
    return {"IS": float(IS), "IV": float(IV), "M10": float(M10[0]), "M10start": M10[1], "L5": float(L5[0]), "L5start": L5[1],
            "RA": float((M10[0] - L5[0]) / (M10[0] + L5[0])), "hours": N}


# =============================================================================
# Simulated demo recordings (same seeded model as the web app)
# =============================================================================

def _mulberry32(seed):
    a = seed & 0xFFFFFFFF

    def imul(x, y):
        return ((x & 0xFFFFFFFF) * (y & 0xFFFFFFFF)) & 0xFFFFFFFF

    def rnd():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = imul(t ^ (t >> 15), t | 1)
        t ^= (t + imul(t ^ (t >> 7), t | 61)) & 0xFFFFFFFF
        t &= 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return rnd


def _jround(v):
    """Math.round of JavaScript (halves round up), so the demos match the web app exactly."""
    return math.floor(v + 0.5)


def _gauss(r):
    u = 0.0
    v = 0.0
    while u == 0:
        u = r()
    while v == 0:
        v = r()
    return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)


def _sc_lddd_light(tH):
    day = math.floor(tH / 24)
    if day >= 14:
        return 0
    h = tH - day * 24
    return 1 if 7 <= h < 19 else 0


def _sc_lddd_phase(prev, r):
    day = math.floor((prev + 24) / 24)
    if prev < 0 or day < 14:
        target = (0 if prev < 0 else day) * 24 + 19 - 0.15
        return {"onset": target + 0.12 * _gauss(r), "alpha": 11.2, "gain": 1}
    return {"onset": prev + 23.62 + 0.14 * _gauss(r), "alpha": 11.6, "gain": 1}


def _sc_ldll_light(tH):
    day = math.floor(tH / 24)
    if day >= 10:
        return 2
    h = tH - day * 24
    return 1 if 6 <= h < 18 else 0


def _sc_ldll_phase(prev, r):
    day = math.floor((prev + 24) / 24)
    if prev < 0 or day < 10:
        target = (0 if prev < 0 else day) * 24 + 18 - 0.1
        return {"onset": target + 0.12 * _gauss(r), "alpha": 11, "gain": 1}
    k = day - 10
    return {"onset": prev + 24.85 + 0.3 * _gauss(r), "alpha": max(7, 10 - 0.12 * k), "gain": max(0.45, 0.85 - 0.02 * k)}


def _sc_jet_light(tH):
    day = math.floor(tH / 24)
    h = tH - day * 24
    if day < 10:
        return 1 if 6 <= h < 18 else 0
    return 1 if h < 12 else 0


def _sc_jet_phase(prev, r):
    if prev < 0:
        return {"onset": 18 - 0.2 + 0.1 * _gauss(r), "alpha": 11, "gain": 1}
    day = math.floor((prev + 24) / 24)
    lights_off = 18 if day < 10 else 12
    target = day * 24 + lights_off - 0.2
    free = prev + 23.7
    diff = max(-0.6, min(0.6, target - free))
    return {"onset": free + diff + 0.15 * _gauss(r), "alpha": 11, "gain": 1}


def _sc_tcycle_light(tH):
    if tH < 343:
        day = math.floor(tH / 24)
        h = tH - day * 24
        return 1 if 7 <= h < 19 else 0
    if tH < 688:
        return 1 if (tH - 343) % 30 < 15 else 0
    return 0


def _sc_tcycle_phase(prev, r):
    """LD 12:12 (entrained), then LD 15:15 (T = 30 h, entrained: one onset per cycle just before
    lights-off, longer alpha), then DD (tau 23.62 h)."""
    day = math.floor((prev + 24) / 24)
    if prev < 0 or day < 14:
        target = (0 if prev < 0 else day) * 24 + 19 - 0.15
        return {"onset": target + 0.12 * _gauss(r), "alpha": 11.2, "gain": 1}
    if prev + 12 < 688:
        k = max(0, math.ceil((prev + 12 - 358) / 30))
        return {"onset": 358 + 30 * k - 0.2 + 0.15 * _gauss(r), "alpha": 13.5, "gain": 1}
    return {"onset": prev + 23.62 + 0.14 * _gauss(r), "alpha": 11.6, "gain": 1}


DEMOS = {
    "ld-dd": dict(label="Demo: LD → DD", fileName="DEMO_WT_LD-DD", seed=11, date=(2026, 2, 3), startMin=14 * 60 + 36, days=35,
                  intervalMin=6, lux=280, llLux=0, gap=None, light=_sc_lddd_light, phase=_sc_lddd_phase),
    "ld-ll": dict(label="Demo: LD → LL", fileName="DEMO_LD-LL", seed=29, date=(2026, 3, 13), startMin=10 * 60 + 5, days=30,
                  intervalMin=1, lux=240, llLux=120, gap=None, light=_sc_ldll_light, phase=_sc_ldll_phase),
    "jetlag": dict(label="Demo: 6-h advance", fileName="DEMO_JETLAG_ADV6", seed=47, date=(2026, 8, 21), startMin=16 * 60 + 48, days=28,
                   intervalMin=6, lux=310, llLux=0, gap=(16.4 * 24, 16.95 * 24), light=_sc_jet_light, phase=_sc_jet_phase),
    "t-cycle": dict(label="Demo: LD 12:12 → 15:15 → DD", fileName="DEMO_LD12-LD15-DD", seed=61, date=(2026, 4, 4), startMin=13 * 60 + 18, days=43,
                    intervalMin=6, lux=260, llLux=0, gap=None, mask=0.35, light=_sc_tcycle_light, phase=_sc_tcycle_phase),
}


def generate_demo(key: str) -> str:
    """Simulated wheel-running record as CSV text (timestamp,activity,light); seeded and reproducible."""
    s = DEMOS[key]
    r = _mulberry32(s["seed"])
    total = s["days"] * 1440
    act = np.zeros(total)
    lux = np.zeros(total)
    windows, prev = [], -1
    while True:
        w = s["phase"](prev, r)
        if w["onset"] > (total + s["startMin"]) / 60 + 30:
            break
        windows.append(w)
        prev = w["onset"]
        if len(windows) > 400:
            break

    def envelope(tH):
        e = 0.02
        for w in windows:
            xx = tH - w["onset"]
            if xx < -1 or xx > w["alpha"] + 1:
                continue
            if 0 <= xx <= w["alpha"]:
                u = xx / w["alpha"]
                v = 0.95 * math.exp(-((u - 0.18) / 0.22) ** 2) + 0.55 * math.exp(-((u - 0.85) / 0.12) ** 2) + 0.42
                e = max(e, min(1, v) * w["gain"])
        return e
    running = False
    for m in range(total):
        tH = (s["startMin"] + m) / 60
        L = s["light"](tH)
        lux_now = s["llLux"] if L == 2 else s["lux"] if L == 1 else 0
        lux[m] = max(1, _jround(lux_now + 6 * _gauss(r))) if lux_now > 0 else 0
        p = envelope(tH)
        if L == 1:
            p *= s.get("mask", 0.12)           # negative masking by light
        p_start = 0.02 + 0.22 * p
        p_stop = 0.03 + 0.25 * (1 - p)
        if running:
            if r() < p_stop:
                running = False
        elif r() < p_start * p * 1.6 + 0.0025:
            running = True
        if running:
            act[m] = max(0, _jround(22 + 30 * p + 8 * _gauss(r)))
    if s["gap"]:
        for m in range(total):
            tH = (s["startMin"] + m) / 60
            if s["gap"][0] <= tH < s["gap"][1]:
                act[m] = -1
    k = s["intervalMin"]
    y, mo, d = s["date"]
    day0 = datetime(y, mo + 1, d)
    lines = ["timestamp,activity,light"]
    for b in range(total // k):
        blk = act[b * k:(b + 1) * k]
        missing = (blk < 0).any()
        tm = day0 + timedelta(minutes=s["startMin"] + b * k)
        lines.append("%s,%s,%d" % (tm.strftime("%Y-%m-%d %H:%M"), "" if missing else int(blk.sum()), _jround(lux[b * k:(b + 1) * k].sum() / k)))
    return "\r\n".join(lines) + "\r\n"


def load_demo(key: str) -> Record:
    rec = parse_csv(generate_demo(key), DEMOS[key]["fileName"] + ".csv")
    rec.encoding = "UTF-8"
    return rec


# =============================================================================
# Session state: recordings with their own settings, display and analysis options
# =============================================================================

def default_display():
    return {"mult": 2, "T": 24.0, "offsetH": 0.0, "rowH": 14, "widthMode": "auto", "widthPx": 1200,
            "style": "bars", "gainLog": 0.0, "floor": 0.0, "shade": True, "ldCol": "colour",
            "showOn": True, "showOff": False, "rightPanel": "none", "ldLine": "mean"}


def default_analysis():
    return {"method": "chi", "pmin": 18.0, "pmax": 30.0, "alpha": 0.01, "fold": "row", "foldCustom": 24.0,
            "templateH": 6.0, "searchH": 4.0, "minContrast": 20.0, "guideTolH": 2.0,
            "phaseUnit": "ztct", "phaseRef": "ld", "fitFold": "auto", "fitFoldCustom": 24.0, "fitAxis": "phase",
            "wmin": 18.0, "wmax": 30.0, "wData": "range", "wCmap": "viridis"}


class Entry:
    """One loaded recording and everything chosen for it (bin size, lighting, range, edits, fits)."""
    _uid = 0

    def __init__(self, rec: Record, synthetic=False):
        Entry._uid += 1
        self.id = Entry._uid
        self.rec = rec
        self.synthetic = synthetic
        self.binMult = 1
        self.fits = []
        self.activeFit = 0
        self.condFits = True          # "one fit per lighting condition" until fits are edited by hand
        self.condKey = None
        has_light = rec.light is not None and quantile(rec.light, 0.99) > 0
        det = detect_lighting(rec, 10)
        self.schedule = segments_to_schedule(det["segments"], rec, 10) if det["segments"] else \
            [{"mode": "LD", "start": 420, "first": "L", "L": 720, "D": 720}]
        self.schedExtend = True
        self.lightSrc = "recorded" if has_light else "none"
        self.luxThr = 10.0
        self.d1, self.d2 = 1, 9999
        self.manual = {}               # cycle k -> absolute minutes of a hand-placed onset, or None (deleted)
        self.cache = {}


class State:
    def __init__(self):
        self.files: list[Entry] = []
        self.cur = -1
        self.disp = default_display()
        self.ana = default_analysis()
        self.tool = "zoom"

    def current(self):
        return self.files[self.cur] if 0 <= self.cur < len(self.files) else None

    def add(self, rec, synthetic=False):
        self.files.append(Entry(rec, synthetic))
        self.cur = len(self.files) - 1
        return self.files[-1]


class Derived:
    """Data of one recording under the current settings (see derive)."""
    pass


def max_bin_mult(F: Entry) -> int:
    return max(1, int(math.floor(BIN_MAX_MIN / F.rec.intervalMin + 1e-9)))


def effective_bin(F: Entry) -> float:
    F.binMult = min(max_bin_mult(F), max(1, int(round(F.binMult or 1))))
    return F.rec.intervalMin * F.binMult


def derive(S: State, F: Entry) -> Derived:
    bin_min = effective_bin(F)
    if F.cache.get("binKey") != bin_min:
        F.cache = {"binKey": bin_min, "binned": bin_record(F.rec, bin_min)}
    B = F.cache["binned"]
    D = Derived()
    D.F, D.S, D.binMin, D.B = F, S, bin_min, B
    D.Pb = max(2, int(round(S.disp["T"] * 60 / bin_min)))
    D.off = int(round(S.disp["offsetH"] * 60 / bin_min))
    D.rowStart = lambda k, off=D.off, Pb=D.Pb: off + k * Pb
    D.kStart = max(0, int(math.floor((B.firstBin - D.off) / D.Pb)))
    D.nRows = max(1, int(math.ceil((B.n - D.off) / D.Pb)))
    lk = (bin_min, F.lightSrc, F.luxThr, F.schedExtend, repr(F.schedule))
    if F.cache.get("lightKey") != lk:
        F.cache["light"] = light_states(F.rec, B, F.lightSrc, F.luxThr, F.schedule, F.schedExtend)
        F.cache["lightKey"] = lk
    D.light = F.cache["light"]
    D.k1 = min(max(F.d1 - 1, D.kStart), D.nRows - 1)
    D.k2 = min(max(F.d2 - 1, D.k1), D.nRows - 1)
    D.a = max(0, D.rowStart(D.k1))
    D.b = min(B.n, D.rowStart(D.k2 + 1))
    D.x = B.act[D.a:D.b]
    return D


# --- Lighting conditions as segments in absolute minutes ---------------------

def cond_segments(D: Derived):
    F = D.F
    key = (F.lightSrc, F.luxThr, F.schedExtend, repr(F.schedule))
    if F.cache.get("segKey") == key:
        return F.cache["segs"]
    segs = []
    if F.lightSrc == "recorded" and F.rec.light is not None:
        for s in detect_lighting(F.rec, F.luxThr)["segments"]:
            segs.append({"mode": s["mode"], "startMin": (s["startDay"] - 1) * 1440, "endMin": s["endDay"] * 1440,
                         "startDay": s["startDay"], "endDay": s["endDay"],
                         "ref0": (s["onAbs"] if s.get("onAbs") is not None else (s["startDay"] - 1) * 1440 + s["onMin"] if s["onMin"] is not None else None)
                         if s["mode"] == "LD" else None,
                         "Tmin": s.get("Tmin") or 1440, "Lmin": s["L"] * 60 if "L" in s else None})
    elif F.lightSrc == "schedule":
        sc = normalize_schedule(F.schedule)
        for i, s in enumerate(sc):
            end = sc[i + 1]["start"] if i + 1 < len(sc) else math.inf
            segs.append({"mode": s["mode"], "startMin": s["start"], "endMin": end, "startDay": int(s["start"] // 1440) + 1,
                         "endDay": end / 1440 if math.isfinite(end) else None,
                         "ref0": ld_lights_on(s) if s["mode"] == "LD" else None, "Tmin": s["L"] + s["D"], "Lmin": s["L"]})
    for s in segs:
        s["realStart"] = s["startMin"]
    if segs:
        if F.lightSrc != "schedule" or F.schedExtend:
            segs[0]["startMin"] = -math.inf
        segs[-1]["endMin"] = math.inf
    F.cache["segs"], F.cache["segKey"] = segs, key
    return segs


def t_cycle_bins(D, segs, b):
    """Cycle length in bins at bin b when an LD cycle not 24 h long (T-cycle) holds, else None."""
    i = seg_index_at(segs, b * D.binMin)
    s = segs[i] if i >= 0 else None
    return _jround(s["Tmin"] / D.binMin) if s and s["mode"] == "LD" and abs(s["Tmin"] - 1440) > 1 else None


def seg_index_at(segs, t):
    for i, s in enumerate(segs):
        if s["startMin"] <= t < s["endMin"]:
            return i
    return -1


def last_lights_on(seg, t):
    return seg["ref0"] + math.floor((t - seg["ref0"]) / seg["Tmin"]) * seg["Tmin"]


def free_run_reference(D: Derived, segs, i):
    """0-degree reference (absolute minutes) of a free-running condition."""
    ref = D.S.ana["phaseRef"]
    if ref == "row0":
        return D.off * D.binMin
    if i < 0:
        return 0
    seg = segs[i]
    if ref == "ld":
        for j in range(i - 1, -1, -1):
            if segs[j]["mode"] == "LD" and segs[j]["ref0"] is not None:
                return last_lights_on(segs[j], seg["realStart"])
    if ref == "rec":
        return 0
    return seg["realStart"] if math.isfinite(seg["realStart"]) and seg["realStart"] > 0 else D.F.rec.t0Min


def is_free_run(mode):
    return mode != "LD"


PHASE_REF_LABEL = {"ld": "projected ZT0 of the preceding LD", "start": "start of the condition",
                   "rec": "00:00 of recording day 1", "row0": "start of actogram row 1"}


# --- Regression fits ---------------------------------------------------------

def ensure_fits(F: Entry):
    if not F.fits:
        F.fits = [{"name": "Fit 1", "auto": True, "members": {}}]
        F.activeFit = 0
    if not (0 <= F.activeFit < len(F.fits)):
        F.activeFit = 0


def rebuild_condition_fits(D: Derived, lst):
    F = D.F
    segs = cond_segments(D)
    key = (repr(F.cache.get("segKey")), D.k1, D.k2, F.cache.get("onKey"))
    if F.condKey == key:
        return
    F.condKey = key
    sis = []
    if segs:
        for o in lst:
            if o["onsetBin"] is None or o["k"] < D.k1 or o["k"] > D.k2:
                continue
            si = seg_index_at(segs, o["onsetBin"] * D.binMin)
            if si >= 0 and si not in sis:
                sis.append(si)
    F.fits = [{"name": "Fit %d (%s)" % (i + 1, segs[si]["mode"]), "auto": True, "seg": si, "members": {}} for i, si in enumerate(sis[:MAX_FITS])] \
        if sis else [{"name": "Fit 1", "auto": True, "members": {}}]
    if F.activeFit >= len(F.fits):
        F.activeFit = 0


def fit_membership(D: Derived, lst):
    """Cycle k -> index of the fit it belongs to (manual fits explicit, automatic fits by condition)."""
    F = D.F
    ensure_fits(F)
    if F.condFits:
        rebuild_condition_fits(D, lst)
    mp = {}
    for i, f in enumerate(F.fits):
        if not f["auto"]:
            for k, v in f["members"].items():
                if v:
                    mp[int(k)] = i
    segs = cond_segments(D)
    for i, f in enumerate(F.fits):
        if f["auto"] and f.get("seg") is not None:
            for o in lst:
                if o["onsetBin"] is not None and D.k1 <= o["k"] <= D.k2 and o["k"] not in mp and seg_index_at(segs, o["onsetBin"] * D.binMin) == f["seg"]:
                    mp[o["k"]] = i
    for i, f in enumerate(F.fits):
        if f["auto"] and f.get("seg") is None:
            for o in lst:
                if o["onsetBin"] is not None and D.k1 <= o["k"] <= D.k2 and o["k"] not in mp:
                    mp[o["k"]] = i
    return mp


def cycle_fit(members, Ph, bin_min):
    """Regression of onset time on cycle number, with cycle numbers derived from the onset times
    (n = round((t - t0)/tau), iterated with the fitted slope, anchored to the rows of most onsets).
    Only one onset per cycle is used (the one closest to the line). Sets o["cyc"], o["excluded"]."""
    pts = sorted(({"o": o, "k": o["k"], "t": o["onsetBin"] * bin_min / 60} for o in members), key=lambda p: p["t"])
    if len(pts) < 2:
        return None, []
    by_k = sorted(pts, key=lambda p: p["k"])
    iv = [(by_k[i]["t"] - by_k[i - 1]["t"]) / (by_k[i]["k"] - by_k[i - 1]["k"]) for i in range(1, len(by_k)) if by_k[i]["k"] > by_k[i - 1]["k"]]
    tau = float(np.median(iv)) if iv else Ph
    if not (0.75 * Ph < tau < 1.25 * Ph):
        tau = Ph
    fit, used, excluded = None, pts, []
    for _ in range(6):
        n = [int(math.floor((p["t"] - pts[0]["t"]) / tau + 0.5)) for p in pts]
        off = int(math.floor(float(np.median([p["k"] - n[i] for i, p in enumerate(pts)])) + 0.5))
        for i, p in enumerate(pts):
            p["n"] = n[i] + off
        a0 = fit["intercept"] if fit else float(np.median([p["t"] - tau * p["n"] for p in pts]))
        b0 = fit["slope"] if fit else tau
        best = {}
        for p in pts:
            r = abs(p["t"] - (a0 + b0 * p["n"]))
            if p["n"] not in best or r < best[p["n"]][1]:
                best[p["n"]] = (p, r)
        used = [p for p in pts if best[p["n"]][0] is p]
        excluded = [p for p in pts if best[p["n"]][0] is not p]
        nf = linfit([p["n"] for p in used], [p["t"] for p in used])
        if not nf:
            fit = None
            break
        conv = fit is not None and abs(nf["slope"] - fit["slope"]) < 1e-9
        fit = nf
        if conv or not (0.5 * Ph < nf["slope"] < 1.5 * Ph):
            break
        tau = nf["slope"]
    for p in pts:
        p["o"]["cyc"] = p["n"]
    for p in excluded:
        p["o"]["excluded"] = True
    return fit, [p["o"] for p in excluded]


def compute_fits(D: Derived, lst):
    mp = fit_membership(D, lst)
    segs = cond_segments(D)
    fits = [{"i": i, "name": f["name"], "auto": f["auto"], "guide": f.get("guide"), "members": []} for i, f in enumerate(D.F.fits)]
    for o in lst:
        o["cyc"], o["excluded"] = o["k"], False
    for o in lst:
        if o["onsetBin"] is not None and o["k"] in mp and mp[o["k"]] < len(fits):
            fits[mp[o["k"]]]["members"].append(o)
    for f in fits:
        cnt = {}
        for o in f["members"]:
            si = seg_index_at(segs, o["onsetBin"] * D.binMin)
            cnt[si] = cnt.get(si, 0) + 1
        best = max(cnt, key=lambda s: cnt[s]) if cnt else None
        f["segIndex"] = -1 if best is None else best
        f["mode"] = segs[f["segIndex"]]["mode"] if f["segIndex"] >= 0 else ("?" if segs else "none")
        # expected cycle length: T of an LD cycle that is not 24 h long, otherwise the row period
        sg = segs[f["segIndex"]] if f["segIndex"] >= 0 else None
        Ph = sg["Tmin"] / 60 if sg and sg["mode"] == "LD" and abs(sg["Tmin"] - 1440) > 1 else D.Pb * D.binMin / 60
        f["fit"], f["excludedList"] = cycle_fit(f["members"], Ph, D.binMin)
        f["kMin"] = f["members"][0]["k"] if f["members"] else None
        f["kMax"] = f["members"][-1]["k"] if f["members"] else None
    return {"fits": fits, "map": mp, "segs": segs}


def compute_phases(D: Derived, lst, R):
    segs = R["segs"]

    def tau_for_seg(si):
        best = None
        for f in R["fits"]:
            if f["fit"] and f["segIndex"] == si and is_free_run(f["mode"]) and (best is None or len(f["members"]) > len(best["members"])):
                best = f
        return best["fit"]["slope"] if best else None
    for o in lst:
        o["phase"] = None
        if o["onsetBin"] is None:
            continue
        t = o["onsetBin"] * D.binMin
        si = seg_index_at(segs, t)
        seg = segs[si] if si >= 0 else None
        if seg and seg["mode"] == "LD":
            if seg["ref0"] is None:
                continue
            zt = (t - last_lights_on(seg, t)) / 60
            o["phase"] = {"label": "ZT", "h": zt, "deg": zt / (seg["Tmin"] / 60) * 360}
            continue
        own = R["fits"][R["map"][o["k"]]] if o["k"] in R["map"] else None
        tau = own["fit"]["slope"] if own and own["fit"] and own["segIndex"] == si and is_free_run(own["mode"]) else tau_for_seg(si)
        if not (tau and tau > 0):
            o["phase"] = {"label": "CT" if seg else "φ", "h": NAN, "deg": NAN}
            continue
        ref = free_run_reference(D, segs, si)
        ph = modpos(t - ref, tau * 60) / (tau * 60)
        o["phase"] = {"label": "CT" if seg else "φ", "h": ph * 24, "deg": ph * 360}
    for f in R["fits"]:
        f["phase"] = None
        if not f["fit"]:
            continue
        if f["mode"] == "LD":
            ds = [o["phase"]["deg"] for o in f["members"] if o["phase"] and math.isfinite(o["phase"]["deg"])]
            if ds:
                r = np.radians(ds)
                deg = modpos(math.degrees(math.atan2(np.sin(r).sum(), np.cos(r).sum())), 360)
                T = segs[f["segIndex"]]["Tmin"] / 60
                f["phase"] = {"label": "ZT", "h": deg / 360 * T, "deg": deg, "how": "circular mean of onset ZT"}
        elif f["fit"]["slope"] > 0:
            ref = free_run_reference(D, segs, f["segIndex"])
            ph = modpos(f["fit"]["intercept"] * 60 - ref, f["fit"]["slope"] * 60) / (f["fit"]["slope"] * 60)
            f["phase"] = {"label": "CT" if f["segIndex"] >= 0 else "φ", "h": ph * 24, "deg": ph * 360, "how": "fitted line, (b mod τ) / τ"}


def onsets(D: Derived):
    """Onsets of every row (with hand edits and drawn guide lines), psi, regression fits and phases."""
    S, F = D.S, D.F
    ensure_fits(F)
    guides = [{"k1": f["guide"]["k1"], "k2": f["guide"]["k2"], "b1": f["guide"]["t1"] / D.binMin, "b2": f["guide"]["t2"] / D.binMin,
               "tolBins": S.ana["guideTolH"] * 60 / D.binMin} for f in F.fits if f.get("guide") and not f["auto"]]
    segs = cond_segments(D)
    key = (D.binMin, D.Pb, D.off, S.ana["templateH"], S.ana["searchH"], S.ana["minContrast"], S.ana["guideTolH"],
           repr(sorted(F.manual.items())), repr(guides), D.nRows, repr(F.cache.get("segKey")))
    if F.cache.get("onKey") != key:
        man = {k: (None if v is None else int(round(v / D.binMin))) for k, v in F.manual.items()}
        F.cache["on"] = detect_onsets(D.B.act, D.binMin, D.rowStart, D.Pb, D.kStart, D.nRows - 1, S.ana["templateH"],
                                      S.ana["searchH"], S.ana["minContrast"], man, guides, period_at=lambda b: t_cycle_bins(D, segs, b))
        F.cache["onKey"] = key
    lst = F.cache["on"]
    win = int(round(360 / D.binMin))
    L = D.light
    for o in lst:
        o["psi"] = None
        if o["onsetBin"] is None:
            continue
        best = None
        for t in range(max(1, o["onsetBin"] - win), min(len(L) - 1, o["onsetBin"] + win) + 1):
            if L[t - 1] == 1 and L[t] == 0 and (best is None or abs(t - o["onsetBin"]) < abs(best - o["onsetBin"])):
                best = t
        if best is not None:
            o["psi"] = (o["onsetBin"] - best) * D.binMin / 60
    R = compute_fits(D, lst)
    compute_phases(D, lst, R)
    return {"list": lst, "fits": R["fits"], "map": R["map"], "segs": R["segs"], "fit": R["fits"][0]["fit"] if R["fits"] else None}


def freeze_auto_fits(D: Derived, O):
    """Turn automatic fits into explicit member lists (before a hand edit of the fits)."""
    F = D.F
    F.condFits = False
    for i, f in enumerate(F.fits):
        if f["auto"]:
            f["members"] = {k: True for k, fi in O["map"].items() if fi == i}
            f["auto"] = False


def toggle_in_active_fit(D: Derived, ks):
    F = D.F
    ensure_fits(F)
    if not ks:
        return
    O = onsets(D)
    freeze_auto_fits(D, O)
    ai = F.activeFit
    all_in = all(O["map"].get(k) == ai for k in ks)
    for k in ks:
        for f in F.fits:
            f["members"].pop(k, None)
        if not all_in:
            F.fits[ai]["members"][k] = True


def apply_guide(D: Derived, a, b):
    """Draw line: rows a.k..b.k become the active fit, onsets searched near the line (t in minutes)."""
    F = D.F
    if a["k"] == b["k"]:
        return "Draw the line across at least two rows."
    if a["k"] > b["k"]:
        a, b = b, a
    tau = (b["t"] - a["t"]) / (b["k"] - a["k"]) / 60
    T = D.S.disp["T"]
    if not (0.5 * T < tau < 1.5 * T):
        return "The line implies a period of %.1f h; draw it along the onsets (from one row to a later row)." % tau
    ensure_fits(F)
    O = onsets(D)
    freeze_auto_fits(D, O)
    fit = F.fits[F.activeFit]
    fit["members"] = {}
    for k in range(a["k"], b["k"] + 1):
        for f in F.fits:
            f["members"].pop(k, None)
        fit["members"][k] = True
    fit["guide"] = {"k1": a["k"], "k2": b["k"], "t1": a["t"], "t2": b["t"]}
    R = onsets(D)["fits"][F.activeFit]
    return "%s: rows %d–%d along the line · n %d%s" % (fit["name"], a["k"] + 1, b["k"] + 1, len(R["members"]),
                                                       " · τ %.2f h" % R["fit"]["slope"] if R["fit"] else "")


def place_onset(D: Derived, k_row, j, delete=False):
    """Place (or delete with delete=True) an onset at row k_row, bin j from the row start. The click is
    given to the cycle whose onset (detected or expected) is nearest, not simply to the row clicked."""
    F = D.F
    b = D.rowStart(k_row) + j
    O = onsets(D)
    if delete:
        best = None
        for o in O["list"]:
            if o["onsetBin"] is not None and (best is None or abs(o["onsetBin"] - b) < abs(best["onsetBin"] - b)):
                best = o
        if best:
            F.manual[best["k"]] = None
        return
    cyc = k_row + j // D.Pb
    by_k = {o["k"]: o for o in O["list"]}

    def pred(k):
        o = by_k.get(k)
        if o and o["onsetBin"] is not None:
            return o["onsetBin"]
        a, c = by_k.get(k - 1), by_k.get(k + 1)
        if a and a["onsetBin"] is not None:
            return a["onsetBin"] + D.Pb
        if c and c["onsetBin"] is not None:
            return c["onsetBin"] - D.Pb
        return None
    best, bd = cyc, math.inf
    for c in (cyc - 1, cyc, cyc + 1):
        p = pred(c)
        if p is not None and abs(p - b) < bd:
            best, bd = c, abs(p - b)
    if bd <= D.Pb / 2:
        cyc = best
    F.manual[cyc] = b * D.binMin


# --- Periodograms, profiles, wavelet, daily rows -----------------------------

def periodogram(D: Derived, method=None, x=None):
    a = D.S.ana
    method = method or a["method"]
    x = D.x if x is None else x
    if method == "ls":
        return lomb_scargle(x, D.binMin, a["pmin"], a["pmax"], a["alpha"], max(0.02, D.binMin / 60 / 2))
    return chi_square_periodogram(x, D.binMin, a["pmin"], a["pmax"], a["alpha"])


def fit_periodogram(D: Derived, f, method=None):
    if f["kMin"] is None:
        return None
    a, b = max(0, D.rowStart(f["kMin"])), min(D.B.n, D.rowStart(f["kMax"] + 1))
    if b - a < 4:
        return None
    key = ("fitpg", D.binMin, a, b, method or D.S.ana["method"], D.S.ana["pmin"], D.S.ana["pmax"], D.S.ana["alpha"])
    if key not in D.F.cache:
        D.F.cache[key] = periodogram(D, method, D.B.act[a:b])
    return {"pg": D.F.cache[key], "a": a, "b": b, "days": (b - a) * D.binMin / 1440, "kMin": f["kMin"], "kMax": f["kMax"]}


def range_profile(D: Derived):
    """Profile of the analysis range folded at the row period, the periodogram peak or a custom period."""
    a = D.S.ana
    pg = periodogram(D) if a["fold"] == "peak" else None
    PH = pg["peak"]["periodH"] if a["fold"] == "peak" and pg and pg["peak"] else a["foldCustom"] if a["fold"] == "custom" else D.S.disp["T"]
    Pb = max(2, int(round(PH * 60 / D.binMin)))
    PH = Pb * D.binMin / 60
    m, sem, n = fold_profile(D.x, 0, Pb)
    lx = D.light[D.a:D.b]
    lm, _, _ = fold_profile(lx, 0, Pb)
    ok = ~np.isnan(D.x)
    dark = float(np.nansum(np.where(lx[ok] == 0, D.x[ok], 0)))
    light = float(np.nansum(np.where(lx[ok] == 1, D.x[ok], 0)))
    return {"PH": PH, "x": np.arange(Pb) * D.binMin / 60, "mean": m, "sem": sem, "n": n, "light": lm,
            "pctDark": 100 * dark / (dark + light) if dark + light else NAN, "np": non_parametric(D.x, D.binMin, D.a)}


def fold_fit(D: Derived, O, f):
    """Profile of one fit's rows: LD fits at T in ZT, DD/LL fits at tau in CT (options in S.ana)."""
    if f["kMin"] is None:
        return None
    a, b = max(0, D.rowStart(f["kMin"])), min(D.B.n, D.rowStart(f["kMax"] + 1))
    if b - a < 4:
        return None
    seg = O["segs"][f["segIndex"]] if f["segIndex"] >= 0 else None
    is_ld = f["mode"] == "LD" and seg is not None and seg["ref0"] is not None
    an = D.S.ana

    def peak_p():
        r = fit_periodogram(D, f)
        return r["pg"]["peak"]["periodH"] * 60 if r and r["pg"]["peak"] else None
    mode = an["fitFold"]
    if mode == "custom":
        P, src = an["fitFoldCustom"] * 60, "custom"
    elif mode == "row":
        P, src = D.S.disp["T"] * 60, "row period"
    elif mode == "peak":
        P, src = peak_p(), "periodogram peak"
    elif is_ld:
        P, src = seg["Tmin"], "LD cycle length T"
    elif f["fit"] and f["fit"]["slope"] > 0:
        P, src = f["fit"]["slope"] * 60, "τ from onsets"
    else:
        P, src = peak_p(), "periodogram peak"
    if not (P and P > 0):
        P, src = D.S.disp["T"] * 60, "row period"
    ref = seg["ref0"] if is_ld else free_run_reference(D, O["segs"], f["segIndex"]) if seg else D.rowStart(f["kMin"]) * D.binMin
    nb = max(4, int(round(P / D.binMin)))
    s, s2, c, ls, lc = (np.zeros(nb) for _ in range(5))
    dark = light = 0.0
    for i in range(a, b):
        t = (i + 0.5) * D.binMin
        j = min(nb - 1, int(modpos(t - ref, P) / P * nb))
        v, st = D.B.act[i], D.light[i]
        if not math.isnan(st):
            ls[j] += st
            lc[j] += 1
        if math.isnan(v):
            continue
        s[j] += v
        s2[j] += v * v
        c[j] += 1
        if st == 0:
            dark += v
        elif st == 1:
            light += v
    axis = "hours" if an["fitAxis"] == "hours" else ("ZT" if abs(P - seg["Tmin"]) < 1e-6 else "hours") if is_ld else ("CT" if seg else "hours")
    span = 24 if axis == "CT" else P / 60
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.where(c > 0, s / np.maximum(c, 1), NAN)
        sd = np.where(c > 1, np.sqrt(np.maximum(0, (s2 - c * m * m) / np.maximum(c - 1, 1))), NAN)
        sem = np.where(c > 1, sd / np.sqrt(np.maximum(c, 1)), NAN)
        lf = np.where(lc > 0, ls / np.maximum(lc, 1), NAN)
    xs = (np.arange(nb) + 0.5) / nb * span
    pk = int(np.nanargmax(m)) if np.any(~np.isnan(m)) else None
    return {"f": f, "a": a, "b": b, "P": P / 60, "src": src, "ref": ref, "isLD": is_ld, "axis": axis, "span": span,
            "x": xs, "mean": m, "sem": sem, "n": c, "light": lf, "peak": pk, "days": (b - a) * D.binMin / 1440,
            "pctDark": 100 * dark / (dark + light) if dark + light else NAN}


def axis_label(r):
    return "ZT (h)" if r["axis"] == "ZT" else "CT (h)" if r["axis"] == "CT" else ("Hours from lights-on" if r["isLD"] else "Hours from reference")


def wavelet_data(D: Derived):
    an, B = D.S.ana, D.B
    a, b = (B.firstBin, B.n) if an["wData"] == "all" else (D.a, D.b)
    while a < b and math.isnan(B.act[a]):
        a += 1
    while b > a and math.isnan(B.act[b - 1]):
        b -= 1
    if b - a < 8:
        return None
    key = ("w", D.binMin, a, b, an["wmin"], an["wmax"])
    if key in D.F.cache:
        return D.F.cache[key]
    g = max(1, int(math.ceil((b - a) / 4000)))
    n = (b - a) // g
    blk = B.act[a:a + n * g].reshape(n, g)
    if g > 1:
        cnt = (~np.isnan(blk)).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            y = np.where(cnt > 0, np.nansum(blk, axis=1) / np.maximum(cnt, 1), np.nan)
    else:
        y = blk[:, 0].copy()
    gap = np.isnan(y)
    if gap.any():
        idx = np.arange(n)
        y = np.interp(idx, idx[~gap], y[~gap])
    dt_h = g * D.binMin / 60
    step = max(0.05, round((an["wmax"] - an["wmin"]) / 160 * 100) / 100)
    periods = np.round(np.arange(an["wmin"], an["wmax"] + 1e-9, step), 3)
    res = morlet_cwt(y, dt_h, periods)
    res["tMin"] = (a + (np.arange(n) + 0.5) * g) * D.binMin
    res["gap"], res["a"], res["b"], res["g"] = gap, a, b, g
    res["valid"] = (res["ridge"] < res["coi"]) & ~gap
    D.F.cache[key] = res
    return res


def ridge_stats(W, mask):
    v = W["ridge"][mask & W["valid"]]
    if not v.size:
        return None
    return {"mean": float(v.mean()), "sd": float(v.std(ddof=1)) if v.size > 1 else NAN, "median": float(np.median(v)),
            "hours": v.size * W["dtH"]}


def row_totals(D: Derived):
    rows, mx, mx_all = {}, 0.0, 0.0
    for k in range(D.kStart, D.nRows):
        a, b = D.rowStart(k), D.rowStart(k) + D.Pb
        seg = D.B.act[max(0, a):max(0, min(D.B.n, b))]
        s = float(np.nansum(seg))
        cover = float((~np.isnan(seg)).sum()) / D.Pb
        rows[k] = {"total": s, "cover": cover, "partial": cover < 0.95}
        mx_all = max(mx_all, s)
        if cover >= 0.95:
            mx = max(mx, s)
    return {"rows": rows, "max": mx or mx_all or 1.0}


def daily_rows(D: Derived):
    out = []
    for k in range(D.kStart, D.nRows):
        a, b = D.rowStart(k), D.rowStart(k + 1)
        tot = li = da = 0.0
        miss = n = known = light_bins = 0
        for t in range(max(0, a, D.B.firstBin), min(D.B.n, b)):
            n += 1
            v, s = D.B.act[t], D.light[t]
            if not math.isnan(s):
                known += 1
                if s == 1:
                    light_bins += 1
            if math.isnan(v):
                miss += 1
                continue
            tot += v
            if s == 1:
                li += v
            elif s == 0:
                da += v
        out.append({"k": k, "date": fmt_date(D.F.rec.startUTC, a * D.binMin + 1), "total": tot, "light": li, "dark": da,
                    "missPct": 100 * miss / n if n else 100.0, "cover": n / D.Pb, "lightH": light_bins * D.binMin / 60 if known else NAN})
    return out


def phase_hours(O, f):
    sg = O["segs"][f["segIndex"]] if f["segIndex"] >= 0 else None
    return sg["Tmin"] / 60 if f["mode"] == "LD" and sg else 24.0


def phase_summary(D: Derived, O):
    out = []
    for f in O["fits"]:
        degs = [o["phase"]["deg"] for o in f["members"] if o["phase"] and math.isfinite(o["phase"]["deg"])]
        cs = circular_stats(degs)
        sg = O["segs"][f["segIndex"]] if f["segIndex"] >= 0 else None
        ref = "–"
        if f["mode"] == "LD" and sg and sg["ref0"] is not None:
            ref = "ZT0 = lights-on " + fmt_clock(sg["ref0"])
        elif f["fit"]:
            r = free_run_reference(D, O["segs"], f["segIndex"])
            ref = "%s %s (%s)" % (fmt_date(D.F.rec.startUTC, r), fmt_clock(r), PHASE_REF_LABEL[D.S.ana["phaseRef"]])
        out.append({"f": f, "cs": cs, "Th": phase_hours(O, f), "ref": ref,
                    "label": f["phase"]["label"] if f["phase"] else ("ZT" if f["mode"] == "LD" else "CT")})
    return out


def seg_detail(F: Entry, s):
    """Short description of a detected LD segment: on/off clock times, or T and the first lights-on for T-cycles."""
    if s["mode"] != "LD":
        return ""
    lengths = (" %s:%s" % (("%.1f" % s["L"]).replace(".0", ""), ("%.1f" % s["D"]).replace(".0", ""))) if "L" in s else ""
    if abs(s.get("T", 24) - 24) > 0.01:
        on = s.get("onAbs")
        return " (LD%s, T %g h, first on %s)" % (lengths, round(s["T"], 2), "%s %s" % (fmt_date(F.rec.startUTC, on), fmt_clock(on)) if on is not None else "?")
    if s["onMin"] is not None and s["offMin"] is not None:
        return " (on %s, off %s)" % (fmt_clock(s["onMin"]), fmt_clock(s["offMin"]))
    return ""


def seg_label(segs, si):
    s = segs[si] if 0 <= si < len(segs) else None
    if not s:
        return "–"
    return "%s (day %s–%s)" % (s["mode"], s["startDay"], ("%g" % s["endDay"]) if s["endDay"] else "")


def range_presets(D: Derived):
    """(label, first row, last row) for All rows and each lighting condition."""
    F = D.F
    out = [("All rows", D.kStart + 1, D.nRows)]
    segs = []
    if F.lightSrc == "recorded" and F.rec.light is not None:
        segs = [(s["mode"], (s["startDay"] - 1) * 1440, s["endDay"] * 1440) for s in detect_lighting(F.rec, F.luxThr)["segments"]]
    elif F.lightSrc == "schedule":
        sc = normalize_schedule(F.schedule)
        end = D.B.n * D.binMin
        segs = [(s["mode"], s["start"], sc[i + 1]["start"] if i + 1 < len(sc) else end) for i, s in enumerate(sc)]
    for mode, a, b in segs:
        r1 = max(D.kStart, int(math.ceil((a / D.binMin - D.off) / D.Pb))) + 1
        r2 = min(D.nRows, int(math.floor((b / D.binMin - D.off) / D.Pb)))
        if r2 >= r1:
            out.append(("%s %d–%d" % (mode, r1, r2), r1, r2))
    return out


def batch_rows(S: State):
    out = []
    for F in S.files:
        D = derive(S, F)
        chi = chi_square_periodogram(D.x, D.binMin, S.ana["pmin"], S.ana["pmax"], S.ana["alpha"])
        ls = lomb_scargle(D.x, D.binMin, S.ana["pmin"], S.ana["pmax"], S.ana["alpha"], max(0.05, D.binMin / 60))
        O = onsets(D)
        psis = [o["psi"] for o in O["list"] if D.k1 <= o["k"] <= D.k2 and o["psi"] is not None]
        npar = non_parametric(D.x, D.binMin, D.a)
        lx = D.light[D.a:D.b]
        ok = ~np.isnan(D.x)
        dark = float(np.sum(np.where((lx == 0) & ok, np.nan_to_num(D.x), 0)))
        light = float(np.sum(np.where((lx == 1) & ok, np.nan_to_num(D.x), 0)))

        def lab(f):
            return f["mode"] if f["mode"] not in ("none", "?") else f["name"]
        chi_fits = []
        for f in O["fits"]:
            r = fit_periodogram(D, f, "chi")
            if r and r["pg"]["peak"]:
                chi_fits.append("%s %.2f%s" % (lab(f), r["pg"]["peak"]["periodH"], "" if r["pg"]["peak"]["significant"] else " ns"))
        out.append({"name": F.rec.name, "file": F.rec.fileName, "rows": "%d–%d" % (D.k1 + 1, D.k2 + 1), "days": (D.b - D.a) * D.binMin / 1440,
                    "chi": chi["peak"]["periodH"] if chi["peak"] else NAN, "chiAmp": chi["peak"]["amp"] if chi["peak"] else NAN,
                    "chiSig": bool(chi["peak"] and chi["peak"]["significant"]), "ls": ls["peak"]["periodH"] if ls["peak"] else NAN,
                    "tau": O["fit"]["slope"] if O["fit"] else NAN, "tauSE": O["fit"]["se"] if O["fit"] else NAN,
                    "chiFits": "; ".join(chi_fits),
                    "tauFits": "; ".join("%s %.3f" % (lab(f), f["fit"]["slope"]) for f in O["fits"] if f["fit"]),
                    "psi": float(np.mean(psis)) if psis else NAN, "IS": npar["IS"] if npar else NAN, "IV": npar["IV"] if npar else NAN,
                    "RA": npar["RA"] if npar else NAN, "pctDark": 100 * dark / (dark + light) if dark + light else NAN})
    return out


# --- Lighting schedule helpers (editor and suggested starts) ----------------

def start_kind(c):
    return "on" if c["mode"] == "LL" or (c["mode"] == "LD" and c.get("first") != "D") else "off"


def start_candidates(F: Entry, sched, i):
    if i < 1 or sched[i - 1]["mode"] != "LD":
        return []
    until = F.rec.t0Min + len(F.rec.counts) * F.rec.intervalMin + 1440
    return ld_boundaries(sched[i - 1], start_kind(sched[i]), until, 800)


def snap_start(F: Entry, sched, i):
    c = start_candidates(F, sched, i)
    if not c:
        return False
    prev, cur = sched[i - 1], sched[i]["start"]
    best = min(c, key=lambda t: abs(t - cur))
    if best == cur or abs(best - cur) > prev["L"] + prev["D"]:
        return False
    sched[i]["start"] = best
    return True


def add_condition(F: Entry):
    sched = normalize_schedule(F.schedule)
    last = sched[-1]
    rec_end = F.rec.t0Min + len(F.rec.counts) * F.rec.intervalMin
    last_ld = next((c for c in reversed(sched) if c["mode"] == "LD"), None)
    if last["mode"] == "LD":
        sched.append({"mode": "DD", "start": min(last["start"] + 7 * 1440, rec_end), "first": "L", "L": last["L"], "D": last["D"]})
        snap_start(F, sched, len(sched) - 1)
    else:
        sched.append({"mode": "LD", "start": min(last["start"] + 7 * 1440, rec_end), "first": "L",
                      "L": last_ld["L"] if last_ld else 720, "D": last_ld["D"] if last_ld else 720})
    F.schedule = normalize_schedule(sched)


# --- CSV tables ----------------------------------------------------------------

def to_csv(rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


def fx(v, d=3):
    return "" if v is None or (isinstance(v, float) and not math.isfinite(v)) else ("%." + str(d) + "f") % v


def onsets_table(D: Derived, O):
    segs = O["segs"]
    rows = [["row", "date", "condition", "onset_clock", "onset_abs_h", "phase_label", "phase_h", "phase_deg", "offset_clock", "offset_abs_h",
             "alpha_h", "psi_h", "fit", "residual_h", "source"]]
    for o in O["list"]:
        on = None if o["onsetBin"] is None else o["onsetBin"] * D.binMin
        off = None if o["offsetBin"] is None else o["offsetBin"] * D.binMin
        fi = O["map"].get(o["k"])
        f = O["fits"][fi] if fi is not None else None
        res = on / 60 - (f["fit"]["intercept"] + f["fit"]["slope"] * o["cyc"]) if f and f["fit"] and on is not None else NAN
        si = seg_index_at(segs, on) if on is not None else -1
        p = o["phase"]
        rows.append([o["k"] + 1, fmt_date(D.F.rec.startUTC, on) if on is not None else "", segs[si]["mode"] if si >= 0 else "",
                     fmt_clock(on) if on is not None else "", fx(on / 60 if on is not None else None),
                     p["label"] if p else "", fx(p["h"] if p else None), fx(p["deg"] if p else None, 2),
                     fmt_clock(off) if off is not None else "", fx(off / 60 if off is not None else None),
                     fx((off - on) / 60 if on is not None and off is not None else None), fx(o["psi"]),
                     (f["name"] + (" (excluded: same cycle)" if o["excluded"] else "")) if f else "", fx(res),
                     ("deleted" if o["onsetBin"] is None else "manual") if o["manual"] else "guided by line" if o["guided"] else "auto"])
    return rows


def fits_table(D: Derived, O):
    rows = [["fit", "condition", "first_row", "last_row", "n", "tau_h", "tau_se_h", "r2", "intercept_h", "phase_label", "phase_h",
             "phase_deg", "phase_method", "free_run_reference"]]
    for f in O["fits"]:
        ft, ph = f["fit"], f["phase"]
        rows.append([f["name"], seg_label(O["segs"], f["segIndex"]) if f["segIndex"] >= 0 else "", f["kMin"] + 1 if f["kMin"] is not None else "",
                     f["kMax"] + 1 if f["kMax"] is not None else "", len(f["members"]), fx(ft["slope"] if ft else None, 4),
                     fx(ft["se"] if ft else None, 4), fx(ft["r2"] if ft else None, 4), fx(ft["intercept"] if ft else None, 4),
                     ph["label"] if ph else "", fx(ph["h"] if ph else None), fx(ph["deg"] if ph else None, 2), ph["how"] if ph else "",
                     PHASE_REF_LABEL[D.S.ana["phaseRef"]] if f["mode"] != "LD" else ""])
    return rows


def phase_table(D: Derived, O):
    rows = phase_summary(D, O)
    out = [["fit", "condition", "first_row", "last_row", "n", "tau_h", "tau_se_h", "r2", "phase_label", "phase_h", "phase_deg", "phase_method",
            "onset_mean_deg", "R", "circular_sd_h", "rayleigh_z", "rayleigh_p", "reference"]]
    for r in rows:
        f, cs, ft, ph = r["f"], r["cs"], r["f"]["fit"], r["f"]["phase"]
        out.append([f["name"], f["mode"], f["kMin"] + 1 if f["kMin"] is not None else "", f["kMax"] + 1 if f["kMax"] is not None else "",
                    len(f["members"]), fx(ft["slope"] if ft else None, 4), fx(ft["se"] if ft else None, 4), fx(ft["r2"] if ft else None, 4),
                    r["label"], fx(ph["h"] if ph else None), fx(ph["deg"] if ph else None, 2), ph["how"] if ph else "",
                    fx(cs["mean"] if cs else None, 2), fx(cs["R"] if cs else None, 4),
                    fx(cs["csdDeg"] / 360 * r["Th"] if cs and math.isfinite(cs["csdDeg"]) else None),
                    fx(cs["Z"] if cs else None), fx(cs["p"] if cs else None, 4), r["ref"]])
    diffs = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if a["f"]["phase"] and b["f"]["phase"]:
            diffs.append((a, b, wrap180(b["f"]["phase"]["deg"] - a["f"]["phase"]["deg"])))
    if diffs:
        out += [[], ["from", "to", "delta_phase_deg", "delta_phase_h"]]
        out += [[a["f"]["name"], b["f"]["name"], "%.2f" % d, "%.3f" % (d / 360 * 24)] for a, b, d in diffs]
    out += [[], ["row", "date", "condition", "onset_clock", "fit", "phase_label", "phase_h", "phase_deg"]]
    for o in O["list"]:
        if o["onsetBin"] is None:
            continue
        t = o["onsetBin"] * D.binMin
        si = seg_index_at(O["segs"], t)
        fi = O["map"].get(o["k"])
        p = o["phase"]
        out.append([o["k"] + 1, fmt_date(D.F.rec.startUTC, t), O["segs"][si]["mode"] if si >= 0 else "", fmt_clock(t),
                    O["fits"][fi]["name"] if fi is not None else "", p["label"] if p else "", fx(p["h"] if p else None), fx(p["deg"] if p else None, 2)])
    return out, diffs


def daily_table(D: Derived):
    out = [["row", "date", "total", "dark", "light", "pct_dark", "light_hours", "missing_pct", "coverage_pct"]]
    for r in daily_rows(D):
        kn = r["light"] + r["dark"]
        out.append([r["k"] + 1, r["date"], int(round(r["total"])), int(round(r["dark"])), int(round(r["light"])),
                    fx(100 * r["dark"] / kn if kn else None, 2), fx(r["lightH"], 2), "%.1f" % r["missPct"], "%.1f" % (100 * min(1, r["cover"]))])
    return out


def light_table(F: Entry):
    det = detect_lighting(F.rec, F.luxThr)
    out = [["day", "date", "regime", "lights_on", "lights_off", "light_hours", "mean_lux_in_light", "transitions", "coverage_pct"]]
    for r in det["days"]:
        out.append([r["day"], fmt_date(F.rec.startUTC, (r["day"] - 1) * 1440), r["mode"], fmt_clock(r["onMin"]) if r["onMin"] is not None else "",
                    fmt_clock(r["offMin"]) if r["offMin"] is not None else "", fx(r["lightHours"], 2), fx(r["meanLux"] if r["nLight"] else None, 1),
                    len(r["on"]) + len(r["off"]), "%.1f" % (100 * min(1, r["cover"]))])
    return out


def batch_table(S: State):
    out = [["name", "file", "rows", "days", "chi2_peak_h", "chi2_amplitude", "chi2_significant", "ls_peak_h", "tau_fit1_h", "tau_fit1_se_h",
            "chi2_peak_by_fit_h", "tau_by_fit_h", "psi_h", "pct_activity_dark", "IS", "IV", "RA"]]
    for r in batch_rows(S):
        out.append([r["name"], r["file"], r["rows"], fx(r["days"], 2), fx(r["chi"], 2), fx(r["chiAmp"], 1), "yes" if r["chiSig"] else "no",
                    fx(r["ls"], 2), fx(r["tau"], 3), fx(r["tauSE"], 3), r["chiFits"], r["tauFits"], fx(r["psi"], 2), fx(r["pctDark"], 1),
                    fx(r["IS"], 3), fx(r["IV"], 3), fx(r["RA"], 3)])
    return out


def periodogram_table(D: Derived, O):
    pg = periodogram(D)
    chi = D.S.ana["method"] != "ls"
    out = [["period_h", "Qp" if chi else "power", "critical", "minus_critical"]]
    out += [["%.4f" % p["periodH"], "%.4f" % p["value"], "%.4f" % p["crit"], "%.4f" % p["amp"]] for p in pg["points"]]
    fits = [(f, fit_periodogram(D, f)) for f in O["fits"]]
    if any(r for _, r in fits):
        out += [[], ["fit", "rows", "period_h", "Qp" if chi else "power", "critical", "minus_critical"]]
        for f, r in fits:
            if r:
                for p in r["pg"]["points"]:
                    out.append([f["name"], "%d-%d" % (r["kMin"] + 1, r["kMax"] + 1), "%.4f" % p["periodH"], "%.4f" % p["value"], "%.4f" % p["crit"], "%.4f" % p["amp"]])
    return out


def wavelet_table(D: Derived, O, W):
    out = [["date", "time", "abs_h", "ridge_period_h", "ridge_power", "coi_period_h", "in_cone_of_influence", "filled_gap"]]
    for i in range(W["N"]):
        t = W["tMin"][i]
        out.append([fmt_date(D.F.rec.startUTC, t), fmt_clock(t), "%.3f" % (t / 60), "%.3f" % W["ridge"][i], "%.4f" % W["ridgePower"][i],
                    "%.2f" % W["coi"][i], 1 if W["ridge"][i] >= W["coi"][i] else 0, int(W["gap"][i])])
    out += [[], ["fit", "rows", "ridge_mean_h", "ridge_sd_h", "hours_used", "tau_onsets_h", "chi2_peak_h"]]
    for f in O["fits"]:
        st = None
        if f["kMin"] is not None:
            a, b = D.rowStart(f["kMin"]) * D.binMin, D.rowStart(f["kMax"] + 1) * D.binMin
            st = ridge_stats(W, (W["tMin"] >= a) & (W["tMin"] < b))
        r = fit_periodogram(D, f, "chi")
        out.append([f["name"], "%d-%d" % (f["kMin"] + 1, f["kMax"] + 1) if f["kMin"] is not None else "", fx(st["mean"] if st else None),
                    fx(st["sd"] if st else None), fx(st["hours"] if st else None, 1), fx(f["fit"]["slope"] if f["fit"] else None, 4),
                    fx(r["pg"]["peak"]["periodH"] if r and r["pg"]["peak"] else None)])
    return out


def profile_table(D: Derived, O):
    pr = range_profile(D)
    out = [["phase_h", "mean", "sem", "n_cycles", "light_fraction"]]
    out += [["%.4f" % pr["x"][i], fx(pr["mean"][i], 4), fx(pr["sem"][i], 4), int(pr["n"][i]), fx(pr["light"][i])] for i in range(len(pr["x"]))]
    fps = [fold_fit(D, O, f) for f in O["fits"]]
    if any(fps):
        out += [[], ["fit", "period_h", "period_from", "axis", "x", "mean", "sem", "n_cycles", "light_fraction"]]
        for r in fps:
            if r:
                for i in range(len(r["x"])):
                    out.append([r["f"]["name"], "%.4f" % r["P"], r["src"], r["axis"], "%.4f" % r["x"][i], fx(r["mean"][i], 4),
                                fx(r["sem"][i], 4), int(r["n"][i]), fx(r["light"][i])])
    return out


# =============================================================================
# Figures (matplotlib). Every function draws into a given Figure so that the same
# code serves the window, the exports and the command-line mode.
# =============================================================================

import matplotlib
import matplotlib.ticker
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection, LineCollection
from matplotlib import colormaps
from matplotlib.colors import to_rgba
from matplotlib.patches import Rectangle

FIT_COLOURS = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e87ba4", "#008300"]


def palette(S: State):
    grey = S.disp["ldCol"] == "grey"
    return {"bg": "#ffffff", "ink": "#141821", "muted": "#586074", "line": "#dadee7",
            "light": "#ffffff" if grey else "#f7e7a6", "dark": "#e3e3e3" if grey else "#d3d8e3", "nodata": "#f0e3e3",
            "ldLight": "#ffffff" if grey else "#f0b429", "ldDark": "#c8c8c8" if grey else "#39404f", "ldEdge": "#a3a9b5" if grey else None,
            "bar": "#141821", "onset": "#d9481f", "offset": "#1baf7a", "accent": "#3446c9", "unused": "#9aa1af",
            "totalBar": "#3e87b8", "fits": FIT_COLOURS}


def _style_axes(ax, P):
    for s in ax.spines.values():
        s.set_color(P["line"])
    ax.tick_params(colors=P["muted"], labelsize=8)
    ax.xaxis.label.set_color(P["muted"])
    ax.yaxis.label.set_color(P["muted"])


def acto_matrix(D: Derived, span):
    """Activity of every row: M[r, j] = act[rowStart(kStart + r) + j] (NaN outside the data)."""
    rows = D.nRows - D.kStart
    starts = np.array([D.rowStart(D.kStart + r) for r in range(rows)])
    idx = starts[:, None] + np.arange(span)[None, :]
    inside = (idx >= D.B.firstBin) & (idx < D.B.n) & (idx >= 0)
    safe = np.clip(idx, 0, D.B.n - 1)
    M = np.where(inside, D.B.act[safe], np.nan)
    Lt = np.where(inside, D.light[safe], np.nan)
    return M, Lt, inside, starts


def draw_actogram(fig: Figure, D: Derived, O=None, title=None, export=False, view=None):
    """Actogram of recording D (whole record). Returns geometry used for hit testing:
    {"ax", "span", "rows", "kStart", "markers": [(x_h, row, k)], "panel_ax"}.
    x is in hours from the row start, y is the row index (0 = first plotted row) increasing downwards."""
    S = D.S
    P = palette(S)
    disp = S.disp
    span = D.Pb * disp["mult"]
    M, Lt, inside, starts = acto_matrix(D, span)
    rows = M.shape[0]
    bh = D.binMin / 60
    xmax = span * bh
    fig.clear()
    fig.patch.set_facecolor(P["bg"])
    right = disp["rightPanel"] != "none"
    if right:
        gs = fig.add_gridspec(1, 2, width_ratios=[8.5, 1], wspace=0.02)
        ax = fig.add_subplot(gs[0, 0])
        axp = fig.add_subplot(gs[0, 1], sharey=ax)
    else:
        ax = fig.add_subplot(1, 1, 1)
        axp = None
    ax.set_facecolor(P["bg"])
    vals = M[~np.isnan(M) & (M > 0)]
    scale = (np.quantile(vals, 0.95) if vals.size else 1.0) / (2 ** disp["gainLog"])
    scale = max(scale, 1e-9)
    style = disp["style"]
    if style == "bars":
        if disp["shade"]:
            img = np.zeros((rows, span, 4))
            img[(Lt == 1)] = to_rgba(P["light"])
            img[(Lt == 0)] = to_rgba(P["dark"])
            img[inside & np.isnan(M)] = to_rgba(P["nodata"])
            ax.imshow(img, aspect="auto", interpolation="nearest", extent=(0, xmax, rows, 0), zorder=0)
        else:
            img = np.zeros((rows, span, 4))
            img[inside & np.isnan(M)] = to_rgba(P["nodata"])
            ax.imshow(img, aspect="auto", interpolation="nearest", extent=(0, xmax, rows, 0), zorder=0)
        rr, jj = np.nonzero(~np.isnan(M) & (M > disp["floor"]))
        if rr.size:
            h = np.minimum(1, M[rr, jj] / scale) * 0.86
            x0 = jj * bh
            wbar = bh * (0.85 if span <= 600 else 1.0)
            y1 = rr + 0.93
            verts = np.stack([np.stack([x0, y1], 1), np.stack([x0 + wbar, y1], 1), np.stack([x0 + wbar, y1 - h], 1), np.stack([x0, y1 - h], 1)], 1)
            ax.add_collection(PolyCollection(verts, facecolors=P["bar"], edgecolors="none", zorder=2))
    else:
        # Heat map: 5 sub-rows per row, the top one is the LD bar (if shading is on).
        sub = 5
        img = np.zeros((rows * sub, span, 4))
        f = np.clip((M - disp["floor"]) / max(1e-9, scale - disp["floor"]), 0, 1)
        if style == "heat":
            rgba = np.zeros(M.shape + (4,))
            c = to_rgba(P["bar"])
            rgba[..., :3] = c[:3]
            rgba[..., 3] = 0.06 + 0.94 * np.nan_to_num(f)
        else:
            rgba = colormaps[style](np.nan_to_num(f))
        rgba[~inside] = 0
        rgba[inside & np.isnan(M)] = to_rgba(P["nodata"])
        for s in range(sub):
            img[s::sub] = rgba
        if disp["shade"]:
            bar = np.zeros((rows, span, 4))
            bar[Lt == 1] = to_rgba(P["ldLight"])
            bar[Lt == 0] = to_rgba(P["ldDark"])
            keep = inside & np.isnan(M)
            bar[keep] = to_rgba(P["nodata"])
            img[0::sub] = bar
        ax.imshow(img, aspect="auto", interpolation="nearest", extent=(0, xmax, rows, 0), zorder=1)
        if disp["shade"] and P["ldEdge"]:
            segs = []
            for r in range(rows):
                cols = np.nonzero(~np.isnan(Lt[r]))[0]
                if cols.size:
                    segs.append(((cols[0] * bh, r + 0.2), ((cols[-1] + 1) * bh, r + 0.2)))
                    segs.append(((cols[0] * bh, r + 0.005), ((cols[-1] + 1) * bh, r + 0.005)))
            ax.add_collection(LineCollection(segs, colors=P["ldEdge"], linewidths=0.4, zorder=3))
    # Grid lines every 6 h, panel boundaries and row baselines.
    for hgrid in np.arange(0, xmax + 1e-9, 6 if S.disp["T"] >= 12 else 1):
        ax.axvline(hgrid, color=P["line"], lw=0.5, zorder=1.5)
    for pnl in range(1, disp["mult"]):
        ax.axvline(pnl * D.Pb * bh, color=P["muted"], lw=0.8, zorder=3)
    ax.hlines(np.arange(1, rows + 1) - 0.02, 0, xmax, colors=P["line"], lw=0.3, zorder=1.6)
    markers = []
    if O is not None and disp["showOn"]:
        # Regression lines (dashed) and LD mean-phase lines (dotted), drawn row by row.
        for f in O["fits"]:
            col = P["fits"][f["i"] % len(P["fits"])]
            if not f["fit"] or f["kMin"] is None:
                continue
            seg = O["segs"][f["segIndex"]] if f["segIndex"] >= 0 else None
            if f["mode"] == "LD" and disp["ldLine"] == "mean" and f["phase"] and seg and seg["ref0"] is not None:
                for k in range(f["kMin"], f["kMax"] + 1):
                    r = k - D.kStart
                    rs = D.rowStart(k) * D.binMin
                    t = last_lights_on(seg, rs) + f["phase"]["h"] * 60
                    while t - seg["Tmin"] >= rs:
                        t -= seg["Tmin"]
                    while t < rs + span * D.binMin:
                        if t >= rs:
                            ax.plot([(t - rs) / 60] * 2, [r + 0.05, r + 0.95], color=col, lw=1.4, ls=(0, (1, 1.6)), zorder=4)
                        t += seg["Tmin"]
                continue
            pts = []
            for k in range(f["kMin"], f["kMax"]):
                r = k - D.kStart
                for p in range(-1, disp["mult"] + 1):
                    j0 = (f["fit"]["intercept"] + f["fit"]["slope"] * (k + p)) * 60 / D.binMin - D.rowStart(k)
                    j1 = (f["fit"]["intercept"] + f["fit"]["slope"] * (k + 1 + p)) * 60 / D.binMin - D.rowStart(k + 1)
                    if 0 <= j0 <= span and 0 <= j1 <= span:
                        pts.append(((j0 * bh, r + 0.5), (j1 * bh, r + 1.5)))
            if pts:
                ax.add_collection(LineCollection(pts, colors=col, linewidths=1.6, linestyles=(0, (5, 3)), zorder=4))
        for f in O["fits"]:
            g = f.get("guide")
            if not g or g["k2"] <= g["k1"]:
                continue
            col = P["fits"][f["i"] % len(P["fits"])]
            pts = []
            for k in range(g["k1"], g["k2"]):
                r = k - D.kStart
                for p in range(-1, disp["mult"] + 1):
                    pg = lambda c: (g["t1"] + (g["t2"] - g["t1"]) * (c - g["k1"]) / (g["k2"] - g["k1"])) / D.binMin
                    j0, j1 = pg(k + p) - D.rowStart(k), pg(k + 1 + p) - D.rowStart(k + 1)
                    if 0 <= j0 <= span and 0 <= j1 <= span:
                        pts.append(((j0 * bh, r + 0.5), (j1 * bh, r + 1.5)))
            if pts:
                ax.add_collection(LineCollection(pts, colors=col, linewidths=0.9, linestyles=(0, (1, 2.5)), zorder=4))
        # Onset markers: one per cycle, in each panel where it falls.
        xs, ys, cs, ring = [], [], [], []
        for o in O["list"]:
            if o["onsetBin"] is None:
                continue
            fi = O["map"].get(o["k"])
            col = P["unused"] if fi is None or o["excluded"] else P["fits"][fi % len(P["fits"])]
            for r in range(rows):
                j = o["onsetBin"] - starts[r]
                if 0 <= j < span:
                    xs.append((j + 0.5) * bh)
                    ys.append(r + 0.5)
                    cs.append(col)
                    ring.append(o["manual"])
                    markers.append(((j + 0.5) * bh, r + 0.5, o["k"]))
        if xs:
            xs, ys, ring = np.array(xs), np.array(ys), np.array(ring)
            cs = np.array(cs)
            sz = max(12, min(40, 600 / max(rows, 1)))
            ax.scatter(xs[~ring], ys[~ring], s=sz, c=list(cs[~ring]), edgecolors="white", linewidths=0.8, zorder=5)
            if ring.any():
                ax.scatter(xs[ring], ys[ring], s=sz, facecolors="white", edgecolors=list(cs[ring]), linewidths=1.6, zorder=5)
        if disp["showOff"]:
            xo, yo = [], []
            for o in O["list"]:
                if o["offsetBin"] is None:
                    continue
                for r in range(rows):
                    j = o["offsetBin"] - starts[r]
                    if 0 <= j < span:
                        xo.append((j + 0.5) * bh)
                        yo.append(r + 0.5)
            if xo:
                ax.scatter(xo, yo, marker="D", s=14, c=P["offset"], edgecolors="white", linewidths=0.6, zorder=5)
    # Analysis range bracket at the left edge.
    ax.plot([0, 0], [D.k1 - D.kStart, D.k2 - D.kStart + 1], color=P["accent"], lw=3, solid_capstyle="butt", zorder=6, clip_on=False)
    ax.set_xlim(0, xmax)
    ax.set_ylim(rows, 0)
    tick = 6 if disp["T"] >= 12 else 1
    ax.set_xticks(np.arange(0, xmax + 1e-9, tick))
    ax.set_xticklabels(["%02d" % int(modpos(disp["offsetH"] + h, 24)) for h in np.arange(0, xmax + 1e-9, tick)])
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    ax.set_xlabel("clock time (h)", fontsize=8, loc="left")
    step = max(1, int(math.ceil(rows / 60)))
    ax.set_yticks(np.arange(0, rows, step) + 0.5)
    labs = []
    for r in range(0, rows, step):
        k = D.kStart + r
        lab = "%d" % (k + 1)
        if disp["T"] == 24:
            lab += "  " + fmt_date_short(D.F.rec.startUTC, D.rowStart(k) * D.binMin + 1)
        labs.append(lab)
    ax.set_yticklabels(labs, fontsize=7, family="monospace")
    ax.tick_params(length=0)
    _style_axes(ax, P)
    if title:
        ax.set_title(title, fontsize=10, loc="right", color=P["ink"], fontweight="bold", pad=18)
    if axp is not None:
        rt = row_totals(D)
        tot = [rt["rows"].get(D.kStart + r, {"total": 0, "partial": True}) for r in range(rows)]
        vals = np.array([t["total"] for t in tot])
        part = np.array([t["partial"] for t in tot])
        axp.set_facecolor(P["bg"])
        if disp["rightPanel"] == "bars":
            for r in range(rows):
                axp.add_patch(Rectangle((0, r + 0.32), vals[r] / rt["max"], 0.36, color=P["totalBar"], alpha=0.4 if part[r] else 1, lw=0))
            axp.set_xlim(0, 1.05)
        elif disp["rightPanel"] == "line":
            axp.plot(vals / rt["max"], np.arange(rows) + 0.5, color=P["totalBar"], lw=1.4, marker="o", ms=2)
            axp.set_xlim(0, 1.05)
        else:
            for r in range(rows):
                axp.text(0.05, r + 0.5, "%.0f" % vals[r], va="center", fontsize=6, family="monospace", color=P["muted"] if part[r] else P["ink"])
            axp.set_xlim(0, 1)
        axp.set_xticks([])
        axp.tick_params(left=False, labelleft=False)
        axp.set_title("row total", fontsize=7, color=P["muted"], pad=18)
        _style_axes(axp, P)
    if view:
        ax.set_xlim(view["x0"], view["x1"])
        ax.set_ylim(view["y1"], view["y0"])
    return {"ax": ax, "panel_ax": axp, "span": span, "rows": rows, "kStart": D.kStart, "markers": markers, "xmax": xmax}


def actogram_figure(D: Derived, O, width_px=None, dpi=100, title=True):
    rows = D.nRows - D.kStart
    w = (width_px or (900 if D.S.disp["widthMode"] == "auto" else D.S.disp["widthPx"])) / dpi + 1.6
    h = max(3.0, rows * max(8, D.S.disp["rowH"]) / dpi + 1.0)
    fig = Figure(figsize=(w, h), dpi=dpi)
    draw_actogram(fig, D, O, title="%s · %g-min bins" % (D.F.rec.name, D.binMin) if title else None, export=True)
    fig.subplots_adjust(left=1.1 / w, right=1 - 0.3 / w, top=1 - 0.75 / h, bottom=0.15 / h)
    return fig


def _xy(ax, P, xlabel, ylabel):
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(color=P["line"], lw=0.5)
    ax.set_axisbelow(True)
    _style_axes(ax, P)


def draw_periodogram(fig: Figure, D: Derived, O):
    P = palette(D.S)
    fig.clear()
    pg = periodogram(D)
    chi = D.S.ana["method"] != "ls"
    fits = [(f, fit_periodogram(D, f)) for f in O["fits"]]
    fits = [(f, r) for f, r in fits if r and r["pg"]["points"]]
    nf = len(fits)
    gs = fig.add_gridspec(2 if nf else 1, max(1, nf), height_ratios=[1.6, 1] if nf else [1], hspace=0.45, wspace=0.25)
    ax = fig.add_subplot(gs[0, :])
    if pg["points"]:
        x = [p["periodH"] for p in pg["points"]]
        ax.plot(x, [p["value"] for p in pg["points"]], color=P["accent"], lw=1.4)
        ax.plot(x, [p["crit"] for p in pg["points"]], color=P["ink"], lw=0.8, ls="--")
        pk = pg["peak"]
        if pk:
            ax.plot([pk["periodH"]], [pk["value"]], "o", color=P["onset"], ms=5)
            ax.annotate("%.2f h" % pk["periodH"], (pk["periodH"], pk["value"]), textcoords="offset points", xytext=(4, 4), fontsize=8, fontweight="bold")
    _xy(ax, P, "Period (h)", "Qp" if chi else "Normalised power")
    ax.set_title(("χ² periodogram" if chi else "Lomb–Scargle periodogram") + " · rows %d–%d" % (D.k1 + 1, D.k2 + 1), fontsize=9, loc="left")
    for i, (f, r) in enumerate(fits):
        a2 = fig.add_subplot(gs[1, i])
        col = P["fits"][f["i"] % 6]
        x = [p["periodH"] for p in r["pg"]["points"]]
        a2.plot(x, [p["value"] for p in r["pg"]["points"]], color=col, lw=1.2)
        a2.plot(x, [p["crit"] for p in r["pg"]["points"]], color=P["ink"], lw=0.6, ls="--")
        if r["pg"]["peak"]:
            a2.plot([r["pg"]["peak"]["periodH"]], [r["pg"]["peak"]["value"]], "o", color=col, ms=4)
            a2.annotate("%.2f h" % r["pg"]["peak"]["periodH"], (r["pg"]["peak"]["periodH"], r["pg"]["peak"]["value"]), textcoords="offset points", xytext=(3, 3), fontsize=7)
        _xy(a2, P, "Period (h)", "")
        a2.set_title("%s · rows %d–%d" % (f["name"], r["kMin"] + 1, r["kMax"] + 1), fontsize=8, loc="left", color=col)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.93, bottom=0.1)
    return pg


def draw_wavelet(fig: Figure, D: Derived, O, W):
    P = palette(D.S)
    fig.clear()
    gs = fig.add_gridspec(2, 2, height_ratios=[2, 1], width_ratios=[40, 1], hspace=0.35, wspace=0.03)
    ax = fig.add_subplot(gs[0, 0])
    cax = fig.add_subplot(gs[0, 1])
    t = W["tMin"] / 1440
    per = W["periods"]
    valid_pw = W["power"][:, :][np.broadcast_to(per[:, None] < W["coi"][None, :], W["power"].shape)]
    vmax = valid_pw.max() if valid_pw.size else W["power"].max()
    im = ax.imshow(W["power"], aspect="auto", origin="lower", cmap=D.S.ana["wCmap"], vmin=0, vmax=vmax,
                   extent=(t[0], t[-1], per[0], per[-1]), interpolation="bilinear")
    ax.fill_between(t, np.minimum(W["coi"], per[-1] + 1), per[-1] + 1, color="white", alpha=0.55, lw=0)
    rv = np.where(W["valid"], W["ridge"], np.nan)
    ax.plot(t, rv, color="black", lw=2.6, alpha=0.5)
    ax.plot(t, rv, color="white", lw=1.2)
    for sg in O["segs"]:
        if math.isfinite(sg["realStart"]) and t[0] < sg["realStart"] / 1440 < t[-1]:
            ax.axvline(sg["realStart"] / 1440, color=P["ink"], lw=0.8, ls="--")
    ax.set_ylim(per[0], per[-1])
    # Lighting strip under the plot.
    li = D.light[np.minimum(len(D.light) - 1, (W["tMin"] / D.binMin).astype(int))]
    cols = np.zeros((1, len(li), 4))
    cols[0, li == 1] = to_rgba(P["ldLight"])
    cols[0, li == 0] = to_rgba(P["ldDark"])
    ax.imshow(cols, aspect="auto", extent=(t[0], t[-1], per[0] - (per[-1] - per[0]) * 0.05, per[0] - (per[-1] - per[0]) * 0.015), zorder=3, clip_on=False)
    ax.set_ylim(per[0], per[-1])
    _style_axes(ax, P)
    dayfmt = matplotlib.ticker.FuncFormatter(lambda v, _: "%g" % (math.floor(v) + 1) if abs(v - round(v)) < 1e-9 else "")
    ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.xaxis.set_major_formatter(dayfmt)
    ax.set_xlabel("Day of recording", fontsize=8)
    ax.set_ylabel("Period (h)")
    fig.colorbar(im, cax=cax, label="power")
    a2 = fig.add_subplot(gs[1, 0])
    a2.plot(t, rv, color=P["accent"], lw=1.4)
    for f in O["fits"]:
        if f["fit"] and f["kMin"] is not None:
            a, b = D.rowStart(f["kMin"]) * D.binMin / 1440, D.rowStart(f["kMax"] + 1) * D.binMin / 1440
            a2.plot([a, b], [f["fit"]["slope"]] * 2, color=P["fits"][f["i"] % 6], lw=2, ls="--")
    a2.set_ylim(per[0], per[-1])
    a2.set_xlim(t[0], t[-1])
    a2.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    a2.xaxis.set_major_formatter(dayfmt)
    _xy(a2, P, "Day of recording", "Ridge period (h)")


def draw_profile(fig: Figure, D: Derived, O):
    P = palette(D.S)
    fig.clear()
    pr = range_profile(D)
    fps = [r for r in (fold_fit(D, O, f) for f in O["fits"]) if r]
    nf = len(fps)
    gs = fig.add_gridspec(2 if nf else 1, max(1, nf), height_ratios=[1.4, 1] if nf else [1], hspace=0.5, wspace=0.25)
    ax = fig.add_subplot(gs[0, :])

    def shade(a, x, lf, width):
        # Light/dark at each phase as one background strip (light where >= half of the cycles were light).
        if not len(x):
            return
        img = np.zeros((1, len(lf), 4))
        lf = np.asarray(lf, float)
        img[0, lf >= 0.5] = to_rgba(P["light"])
        img[0, lf < 0.5] = to_rgba(P["dark"])
        a.imshow(img, aspect="auto", interpolation="nearest", extent=(x[0], x[-1] + width, 0, 1),
                 transform=a.get_xaxis_transform(), zorder=0)
    shade(ax, pr["x"], pr["light"], D.binMin / 60)
    ax.fill_between(pr["x"], pr["mean"] - pr["sem"], pr["mean"] + pr["sem"], color=P["accent"], alpha=0.2, lw=0)
    ax.plot(pr["x"], pr["mean"], color=P["accent"], lw=1.4)
    ax.set_xlim(0, pr["PH"])
    _xy(ax, P, "Hours from the start of the row (period %.2f h)" % pr["PH"], "Mean counts / bin")
    ax.set_title("Activity profile · rows %d–%d" % (D.k1 + 1, D.k2 + 1), fontsize=9, loc="left")
    for i, r in enumerate(fps):
        a2 = fig.add_subplot(gs[1, i])
        col = P["fits"][r["f"]["i"] % 6]
        shade(a2, r["x"] - r["span"] / len(r["x"]) / 2, r["light"], r["span"] / len(r["x"]))
        a2.fill_between(r["x"], r["mean"] - r["sem"], r["mean"] + r["sem"], color=col, alpha=0.2, lw=0)
        a2.plot(r["x"], r["mean"], color=col, lw=1.2)
        a2.set_xlim(0, r["span"])
        _xy(a2, P, axis_label(r), "")
        a2.set_title("%s · %.2f h (%s)" % (r["f"]["name"], r["P"], r["src"]), fontsize=8, loc="left", color=col)
    return pr, fps


def draw_phase(fig: Figure, D: Derived, O):
    P = palette(D.S)
    fig.clear()
    rows = phase_summary(D, O)
    unit = D.S.ana["phaseUnit"]
    ax = fig.add_subplot(1, 2, 1, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_yticks([])
    nring = max(1, len(rows))
    for i, r in enumerate(rows):
        f = r["f"]
        rr = 1 - i * 0.6 / nring
        col = P["fits"][f["i"] % 6]
        sg = O["segs"][f["segIndex"]] if f["segIndex"] >= 0 else None
        th = np.linspace(0, 2 * np.pi, 200)
        if f["mode"] == "LD" and sg and sg["Lmin"]:
            lf = sg["Lmin"] / sg["Tmin"] * 2 * np.pi
            ax.plot(th[th <= lf], [rr] * int((th <= lf).sum()), color=P["ldLight"], lw=4)
            ax.plot(th[th >= lf], [rr] * int((th >= lf).sum()), color=P["ldDark"], lw=4)
        else:
            ax.plot(th, [rr] * len(th), color=P["line"], lw=4)
        degs = [o["phase"]["deg"] for o in f["members"] if o["phase"] and math.isfinite(o["phase"]["deg"])]
        if degs:
            ax.scatter(np.radians(degs), [rr] * len(degs), s=16, color=col, edgecolors="white", linewidths=0.5, zorder=5)
        if r["cs"]:
            ax.annotate("", xy=(math.radians(r["cs"]["mean"]), r["cs"]["R"] * rr), xytext=(0, 0),
                        arrowprops=dict(arrowstyle="->", color=col, lw=1.6))
    ax.set_ylim(0, 1.08)
    ax.set_xticks(np.radians([0, 90, 180, 270]))
    ax.set_xticklabels(["0°", "90°", "180°", "270°"] if unit == "deg" else ["0", "6", "12", "18"], fontsize=8)
    ax.set_title("Phase (%s), 0 at top" % ("°" if unit == "deg" else "ZT / CT h"), fontsize=9)
    a2 = fig.add_subplot(1, 2, 2)
    for f in O["fits"]:
        col = P["fits"][f["i"] % 6]
        ks = [o["k"] + 1 for o in f["members"] if o["phase"] and math.isfinite(o["phase"]["deg"])]
        ds = [o["phase"]["deg"] for o in f["members"] if o["phase"] and math.isfinite(o["phase"]["deg"])]
        if unit != "deg":
            ds = [d / 15 for d in ds]
        a2.scatter(ks, ds, s=12, color=col, zorder=3)
        if f["phase"] and f["kMin"] is not None:
            v = f["phase"]["deg"] if unit == "deg" else f["phase"]["deg"] / 15
            a2.plot([f["kMin"] + 1, f["kMax"] + 1], [v, v], color=col, ls="--", lw=1.4)
    a2.set_ylim(0, 360 if unit == "deg" else 24)
    a2.set_yticks(np.arange(0, 361, 45) if unit == "deg" else np.arange(0, 25, 3))
    _xy(a2, P, "Actogram row", "Phase (°)" if unit == "deg" else "Phase (ZT / CT h)")
    fig.subplots_adjust(left=0.04, right=0.98, top=0.86, bottom=0.14, wspace=0.25)
    return rows


def draw_daily(fig: Figure, D: Derived):
    P = palette(D.S)
    fig.clear()
    rows = daily_rows(D)
    ax = fig.add_subplot(1, 1, 1)
    k = [r["k"] + 1 for r in rows]
    dark = [r["dark"] for r in rows]
    light = [r["light"] for r in rows]
    unk = [max(0, r["total"] - r["dark"] - r["light"]) for r in rows]
    ax.bar(k, dark, color=P["accent"], label="dark")
    ax.bar(k, light, bottom=dark, color="#c48a00", label="light")
    ax.bar(k, unk, bottom=np.array(dark) + np.array(light), color=P["muted"], label="unknown")
    ax.legend(fontsize=8, frameon=False)
    _xy(ax, P, "Actogram row", "Total counts")
    return rows


def draw_light(fig: Figure, D: Derived):
    P = palette(D.S)
    fig.clear()
    F = D.F
    ax = fig.add_subplot(1, 1, 1)
    if F.rec.light is None:
        ax.text(0.5, 0.5, "This file has no light column.", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return
    per_h = max(1, int(round(60 / F.rec.intervalMin)))
    n = len(F.rec.light) // per_h
    v = np.nanmean(F.rec.light[:n * per_h].reshape(n, per_h), axis=1) if n else np.array([])
    t = (F.rec.t0Min + (np.arange(n) + 0.5) * 60) / 1440
    ax.plot(t, v, color="#c48a00", lw=1.2)
    ax.axhline(F.luxThr, color=P["ink"], ls="--", lw=0.8)
    ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: "%g" % (math.floor(v) + 1)))
    _xy(ax, P, "Day of recording", "Light (hourly mean)")


# =============================================================================
# Desktop window (Tkinter + Matplotlib)
# =============================================================================

METHODS_TEXT = """METHODS (summary; the README and the user manual give full details)

File format
  Timestamped tables (CSV or TXT; commas, tabs or semicolons): timestamp, activity[, light].
  Timestamps YYYY-MM-DD HH:MM[:SS]. Lines starting with # are comments; "# Recording name: foo"
  sets the recording name to foo (first such line; not case-sensitive). The sampling interval is
  the median spacing between rows; missing rows and empty or negative values are missing data.
  Text-based AWD files (7-line header, line 1 = name) are also read. Text encodings: UTF-8,
  UTF-16 with a byte-order mark, and GB18030, Big5, Shift_JIS, EUC-JP and EUC-KR (detected;
  can be chosen in File info).

Binning
  The bin is a multiple of the sampling interval (default 1x, up to 120 min). Counts are summed;
  bins with < 50% valid samples are missing; partially covered bins are scaled to a full bin.

Lighting
  Recorded light: a bin is light when its mean light value >= the lights-on threshold. Days are
  classified as LD, LL or DD and grouped into conditions; under LD, lights-on (and lights-off) times
  must be whole cycles apart, with T taken from the data, so T-cycles (e.g. LD 15:15) are recognised. Schedule: conditions LD / LL / DD, each
  starting at a date and time and lasting until the next; LD has L and D lengths (T = L + D) and
  begins with light or dark. Within LD a time t is light when (t - lights-on) mod T < L.

Periodograms
  Chi-square (Sokolove & Bushell 1978): Qp = sum n_h (M_h - M)^2 / [sum (X - M)^2 / N] with
  missing data; critical value chi2(1 - alpha, P - 1) (Wilson-Hilferty). Lomb-Scargle (Lomb 1976;
  Scargle 1982) normalised by the variance, with a false-alarm threshold for alpha.

Wavelet
  Continuous Morlet wavelet (omega0 = 6) by FFT (Torrence & Compo 1998), power normalised by the
  variance; ridge = period of maximum power, refined by parabolic interpolation; points inside the
  cone of influence (e-folding time) and filled gaps are excluded from summaries.

Onsets and offsets
  Step template S(t) = mean(next W h) - mean(previous W h). The first cycle is searched over its
  row, later cycles within +/- the search window of the previous onset + row period (+ T under an LD
  cycle that is not 24 h long; rows the predicted onset does not reach stay empty) (or within the
  line tolerance of a drawn line). Onsets below the minimum contrast are dropped. The onset is
  refined to the first bin within +/- 1 h that is active and followed by 30 min at >= 40% of the
  post-onset level. Offset: first time >= 2 h after onset followed by 3 h below 15% of that level.
  psi = onset - nearest lights-off within +/- 6 h; alpha = offset - onset.

Regression fits, period and phase
  Least-squares line of onset time on cycle number; slope = tau (DD/LL) or T (LD). Cycle numbers
  come from the onset times (n = round((t - t0)/tau), iterated, anchored to the rows of most
  onsets); two onsets in one cycle: only the one closer to the line is used. One automatic fit
  per lighting condition by default. Phase: ZT under LD (ZT0 = lights-on; LD fit = circular mean
  of onset ZT); under DD/LL, t = b + tau n from the 0-degree reference, phase = ((t - ref) mod tau)
  / tau, CT = phase x 24. Circular statistics: R, circular SD = sqrt(-2 ln R), Rayleigh test (Zar).

Activity profile and non-parametric variables
  Mean +/- SEM of the analysis range folded at the row period, periodogram peak or a custom
  period; per fit at T (ZT) under LD or tau (CT) under DD/LL. IS, IV and RA (from M10 and L5) are
  computed on hourly totals (Van Someren et al. 1999).

Demo data
  The demo recordings are simulated with a seeded model (identical to the web app).
"""


def run_gui(paths=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    class DateTimePicker(tk.Toplevel):
        """Date and time picker for the start of a schedule condition (calendar, 24-h time and the
        suggested starts: ends of L or D of the preceding LD; double-click a suggestion = use and close)."""

        def __init__(self, app, F, i):
            super().__init__(app)
            self.app, self.F, self.i = app, F, i
            self.result = None
            sched = F.schedule
            c = sched[i]
            self.t = c["start"]
            self.cands = start_candidates(F, sched, i)
            self.title("Date and time")
            self.transient(app)
            self.resizable(False, False)
            d = abs_date(F.rec.startUTC, self.t)
            self.y, self.m = d.year, d.month
            head = ttk.Frame(self, padding=(10, 8, 10, 0))
            head.pack(fill="x")
            ttk.Label(head, text="Date and time", font=("TkDefaultFont", 11, "bold")).pack(side="left")
            ttk.Label(head, text="  Start of condition %d (%s%s)" % (i + 1, c["mode"], ", %s first" % ("dark" if c.get("first") == "D" else "light") if c["mode"] == "LD" else ""),
                      foreground="#586074").pack(side="left")
            body = ttk.Frame(self, padding=10)
            body.pack(fill="both")
            cal = ttk.Frame(body)
            cal.grid(row=0, column=0, sticky="n")
            nav = ttk.Frame(cal)
            nav.pack(fill="x")
            ttk.Button(nav, text="◀", width=3, command=lambda: self.shift(-1)).pack(side="left")
            self.mlab = ttk.Label(nav, width=16, anchor="center")
            self.mlab.pack(side="left", padx=4)
            ttk.Button(nav, text="▶", width=3, command=lambda: self.shift(1)).pack(side="left")
            ttk.Button(nav, text="Recording start", command=self.goto_start).pack(side="left", padx=(6, 0))
            self.grid_f = ttk.Frame(cal)
            self.grid_f.pack(pady=(6, 0))
            side = ttk.Frame(body, padding=(14, 0, 0, 0))
            side.grid(row=0, column=1, sticky="n")
            ttk.Label(side, text="Time (24-hour clock)").pack(anchor="w")
            tf = ttk.Frame(side)
            tf.pack(anchor="w", pady=(2, 8))
            tm = modpos(self.t, 1440)
            self.hv = tk.StringVar(value="%02d" % (tm // 60))
            self.mv = tk.StringVar(value="%02d" % (tm % 60))
            self.hs = tk.Spinbox(tf, from_=0, to=23, width=3, wrap=True, textvariable=self.hv, format="%02.0f", command=self.from_fields)
            self.hs.pack(side="left")
            ttk.Label(tf, text=":").pack(side="left")
            self.ms = tk.Spinbox(tf, from_=0, to=59, width=3, wrap=True, textvariable=self.mv, format="%02.0f", command=self.from_fields)
            self.ms.pack(side="left")
            for w in (self.hs, self.ms):
                w.bind("<KeyRelease>", lambda e: self.from_fields())
            kind = "ends of D (lights on)" if start_kind(c) == "on" else "ends of L (lights off)"
            ttk.Label(side, text=("Suggested: %s of condition %d (LD)" % (kind, i)) if self.cands else
                      ("No suggestions: condition %d is %s, not LD." % (i, sched[i - 1]["mode"]) if i > 0 else "No suggestions for the first condition."),
                      font=("TkDefaultFont", 9, "bold"), wraplength=260).pack(anchor="w")
            if self.cands:
                ttk.Label(side, text="Click one to use it (double-click: use it and close).", foreground="#586074").pack(anchor="w")
                lf = ttk.Frame(side)
                lf.pack(fill="both", pady=(4, 0))
                self.lb = tk.Listbox(lf, height=10, width=30, exportselection=False, font=("TkFixedFont", 9))
                sb = ttk.Scrollbar(lf, command=self.lb.yview)
                self.lb.configure(yscrollcommand=sb.set)
                self.lb.pack(side="left", fill="both")
                sb.pack(side="right", fill="y")
                for t in self.cands:
                    self.lb.insert("end", "%s %s   day %d" % (fmt_date(F.rec.startUTC, t), fmt_clock(t), int(t // 1440) + 1))
                self.lb.bind("<<ListboxSelect>>", self.on_pick)
                self.lb.bind("<Double-Button-1>", lambda e: (self.on_pick(), self.ok()))
                if self.t in self.cands:
                    j = self.cands.index(self.t)
                    self.lb.selection_set(j)
                    self.lb.see(j)
            foot = ttk.Frame(self, padding=10)
            foot.pack(fill="x")
            self.sel = ttk.Label(foot, font=("TkFixedFont", 9))
            self.sel.pack(side="left")
            ttk.Button(foot, text="OK", command=self.ok).pack(side="right")
            ttk.Button(foot, text="Cancel", command=self.destroy).pack(side="right", padx=6)
            self.bind("<Escape>", lambda e: self.destroy())
            self.bind("<Return>", lambda e: (self.from_fields(), self.ok()))
            self.render()
            self.grab_set()
            self.hs.focus_set()

        def shift(self, dm):
            self.m += dm
            if self.m < 1:
                self.m, self.y = 12, self.y - 1
            if self.m > 12:
                self.m, self.y = 1, self.y + 1
            self.render()

        def goto_start(self):
            d = abs_date(self.F.rec.startUTC, self.F.rec.t0Min)
            self.y, self.m = d.year, d.month
            self.render()

        def from_fields(self):
            try:
                h, m = int(self.hv.get()), int(self.mv.get())
            except ValueError:
                return
            if 0 <= h <= 23 and 0 <= m <= 59:
                self.t = math.floor(self.t / 1440) * 1440 + h * 60 + m
                self.render(False)

        def on_pick(self, e=None):
            s = self.lb.curselection()
            if s:
                self.t = self.cands[s[0]]
                d = abs_date(self.F.rec.startUTC, self.t)
                self.y, self.m = d.year, d.month
                tm = modpos(self.t, 1440)
                self.hv.set("%02d" % (tm // 60))
                self.mv.set("%02d" % (tm % 60))
                self.render(False)

        def pick_day(self, absday):
            self.t = absday * 1440 + modpos(self.t, 1440)
            self.render()

        def render(self, cal=True):
            F = self.F
            self.mlab.configure(text="%s %d" % (["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                                  "October", "November", "December"][self.m - 1], self.y))
            if cal:
                for w in self.grid_f.winfo_children():
                    w.destroy()
                for c, d in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
                    ttk.Label(self.grid_f, text=d, foreground="#586074").grid(row=0, column=c)
                first = date(self.y, self.m, 1)
                nd = (date(self.y + (self.m == 12), self.m % 12 + 1, 1) - first).days
                rf = int(F.rec.t0Min // 1440)
                rl = int((F.rec.t0Min + len(F.rec.counts) * F.rec.intervalMin - 1) // 1440)
                cand_days = {int(t // 1440) for t in self.cands}
                sel = int(self.t // 1440)
                start_day = ms_to_date(F.rec.startUTC)
                for dd in range(1, nd + 1):
                    cur = date(self.y, self.m, dd)
                    absday = (cur - start_day).days
                    pos = first.weekday() + dd - 1
                    txt = "%d" % dd + ("\n%d" % (absday + 1) if rf <= absday <= rl else "\n ") + (" •" if absday in cand_days else "")
                    b = tk.Button(self.grid_f, text=txt, width=4, font=("TkDefaultFont", 8), relief="flat",
                                  bg="#3446c9" if absday == sel else ("#eef0f5" if rf <= absday <= rl else self.cget("background")),
                                  fg="white" if absday == sel else "#141821", command=lambda a=absday: self.pick_day(a))
                    b.grid(row=1 + pos // 7, column=pos % 7, padx=1, pady=1)
            tag = ""
            if self.t in self.cands:
                tag = " · end of D" if start_kind(F.schedule[self.i]) == "on" else " · end of L"
            self.sel.configure(text="%s %s (day %d)%s" % (fmt_date(F.rec.startUTC, self.t), fmt_clock(self.t), int(self.t // 1440) + 1, tag))
            if getattr(self, "lb", None) is not None:
                self.lb.selection_clear(0, "end")
                if self.t in self.cands:
                    self.lb.selection_set(self.cands.index(self.t))

        def ok(self):
            self.result = self.t
            self.destroy()

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(APP_NAME)
            self.geometry("1440x940")
            self.minsize(1100, 700)
            self.S = State()
            self.view = None              # actogram view (zoom): {"x0","x1","y0","y1"} in hours / rows
            self.history = []
            self.geom = None
            self.drag = None
            self.range_mode = False
            self._busy = False
            self._pending = None
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("Tool.TButton", padding=(6, 2), width=-1)
            style.configure("On.TButton", padding=(6, 2), width=-1, background="#3446c9", foreground="white")
            style.map("On.TButton", background=[("active", "#2a38a8")])
            style.configure("Head.TLabel", font=("TkDefaultFont", 10, "bold"))
            self.build()
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.after(50, self.initial_load, paths)

        # ---------- layout ----------
        def build(self):
            pw = ttk.Panedwindow(self, orient="horizontal")
            pw.pack(fill="both", expand=True)
            left_outer = ttk.Frame(pw, width=350)
            pw.add(left_outer, weight=0)
            cv = tk.Canvas(left_outer, highlightthickness=0, width=340)
            sb = ttk.Scrollbar(left_outer, orient="vertical", command=cv.yview)
            self.left = ttk.Frame(cv, padding=8)
            self.left.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
            win = cv.create_window((0, 0), window=self.left, anchor="nw")
            cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
            cv.configure(yscrollcommand=sb.set)
            cv.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self._left_canvas = cv
            cv._scrollable = True
            cv.bind_all("<MouseWheel>", self._left_wheel, add="+")
            cv.bind_all("<Button-4>", lambda e: self._left_wheel(e, -1), add="+")
            cv.bind_all("<Button-5>", lambda e: self._left_wheel(e, 1), add="+")
            right = ttk.Panedwindow(pw, orient="vertical")
            pw.add(right, weight=1)
            top = ttk.Frame(right, padding=(8, 6, 8, 0))
            right.add(top, weight=3)
            bottom = ttk.Frame(right, padding=(8, 0, 8, 6))
            right.add(bottom, weight=2)
            self._vpane = right
            self.after(300, lambda: right.sashpos(0, int(right.winfo_height() * 0.56)) if right.winfo_height() > 100 else None)
            self.build_left()
            self.build_top(top)
            self.build_tabs(bottom)
            self.status = ttk.Label(self, anchor="w", padding=(8, 2), foreground="#586074")
            self.status.pack(fill="x", side="bottom")

        def _left_wheel(self, e, direction=None):
            """Mouse wheel: scroll the left panel or the analysis area under the pointer
            (tables and text boxes scroll themselves)."""
            try:
                w = self.winfo_containing(e.x_root, e.y_root)
            except (KeyError, tk.TclError):
                return
            if w is None or w.winfo_class() in ("Treeview", "Listbox", "Text", "TCombobox", "Spinbox"):
                return
            while w is not None and not getattr(w, "_scrollable", False):
                w = w.master
            if w is not None:
                d = direction if direction is not None else (-1 if e.delta > 0 else 1)
                w.yview_scroll(d * (1 if w is self._left_canvas else 3), "units")

        def scroll_area(self, parent):
            """Vertically scrolling frame; returns the inner frame (as wide as the visible area)."""
            outer = ttk.Frame(parent)
            outer.pack(fill="both", expand=True)
            cv = tk.Canvas(outer, highlightthickness=0, background="white", yscrollincrement=20)
            sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
            inner = ttk.Frame(cv, padding=(6, 6, 10, 6))
            win = cv.create_window((0, 0), window=inner, anchor="nw")
            inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
            cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
            cv.configure(yscrollcommand=sb.set)
            cv._scrollable = True
            sb.pack(side="right", fill="y")
            cv.pack(side="left", fill="both", expand=True)
            return inner

        def toggle_big(self):
            """Enlarge the analysis area (actogram smaller) or go back to the normal split."""
            self.big = not self.big
            h = self._vpane.winfo_height()
            self._vpane.sashpos(0, int(h * (0.25 if self.big else 0.56)))
            self.big_b.configure(text="Larger actogram" if self.big else "Larger analysis area")

        def open_window(self):
            """Open the current analysis tab in a window of its own (follows the main window)."""
            key = self.tab_key()
            label = self.nb.tab(self.nb.select(), "text")
            top = tk.Toplevel(self)
            top.title("%s – %s" % (label, APP_NAME))
            top.geometry("1150x850")
            holder = ttk.Frame(top)
            holder.pack(fill="both", expand=True)
            win = {"top": top, "key": key, "holder": holder}
            self.windows.append(win)
            top.protocol("WM_DELETE_WINDOW", lambda: (self.windows.remove(win) if win in self.windows else None, top.destroy()))
            self.render_into(win["holder"], key, scale=1.35)
            self.msg("%s opened in a new window; it follows the recording and settings of the main window." % label)

        def section(self, title):
            f = ttk.LabelFrame(self.left, text=title, padding=6)
            f.pack(fill="x", pady=(0, 8))
            return f

        def build_left(self):
            S = self.S
            # Recordings
            f = self.section("Recordings")
            ttk.Button(f, text="Open files…  (CSV / TXT / AWD)", command=self.open_files).pack(fill="x")
            dm = ttk.Frame(f)
            dm.pack(fill="x", pady=4)
            for n, key in enumerate(DEMOS):
                ttk.Button(dm, text=DEMOS[key]["label"].replace("Demo: ", "Demo "), style="Tool.TButton",
                           command=lambda k=key: self.add_demo(k)).grid(row=n // 2, column=n % 2, sticky="ew", padx=(0, 3), pady=(0, 2))
            dm.columnconfigure(0, weight=1)
            dm.columnconfigure(1, weight=1)
            ttk.Label(f, text="Demo records are simulated (seeded model), not real animals.", foreground="#586074", wraplength=290).pack(anchor="w")
            lf = ttk.Frame(f)
            lf.pack(fill="x", pady=(4, 0))
            self.file_lb = tk.Listbox(lf, height=5, exportselection=False, font=("TkFixedFont", 9))
            self.file_lb.pack(side="left", fill="x", expand=True)
            self.file_lb.bind("<<ListboxSelect>>", self.on_file_select)
            ttk.Button(lf, text="×", width=2, command=self.remove_file).pack(side="left", padx=(4, 0))
            # Actogram
            f = self.section("Actogram")
            self.v = {}

            def row(parent, label, widget):
                r = ttk.Frame(parent)
                r.pack(fill="x", pady=1)
                ttk.Label(r, text=label, width=17).pack(side="left")
                widget(r).pack(side="left", fill="x", expand=True)

            def combo(key, values, store, conv=str, labels=None):
                var = tk.StringVar()
                self.v[key] = var

                def make(r):
                    cb = ttk.Combobox(r, textvariable=var, values=labels or values, state="readonly", width=18)
                    cb.bind("<<ComboboxSelected>>", lambda e: (store(conv(values[(labels or values).index(var.get())])), self.refresh()))
                    return cb
                return make

            def entry(key, store, width=8):
                var = tk.StringVar()
                self.v[key] = var

                def make(r):
                    e = ttk.Entry(r, textvariable=var, width=width)
                    commit = lambda ev=None: self._commit(var, store)
                    e.bind("<Return>", commit)
                    e.bind("<FocusOut>", commit)
                    return e
                return make
            self.bin_var = tk.StringVar()
            r = ttk.Frame(f)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text="Bin size", width=17).pack(side="left")
            self.bin_cb = ttk.Combobox(r, textvariable=self.bin_var, state="readonly", width=18)
            self.bin_cb.pack(side="left", fill="x", expand=True)
            self.bin_cb.bind("<<ComboboxSelected>>", self.on_bin)
            row(f, "Plot", combo("mult", [1, 2, 3], lambda v: S.disp.__setitem__("mult", v), int, ["Single", "Double", "Triple"]))
            row(f, "Row period (h)", entry("T", lambda v: S.disp.__setitem__("T", min(48, max(1, float(v))))))
            row(f, "Row start (h)", entry("offsetH", lambda v: S.disp.__setitem__("offsetH", float(v) % 24)))
            row(f, "Row height (px)", entry("rowH", lambda v: S.disp.__setitem__("rowH", int(min(40, max(4, float(v)))))))
            row(f, "Export width", combo("widthMode", ["auto", "custom"], lambda v: S.disp.__setitem__("widthMode", v), str, ["Auto (900 px)", "Custom"]))
            row(f, "Custom width (px)", entry("widthPx", lambda v: S.disp.__setitem__("widthPx", int(min(2880, max(720, float(v)))))))
            styles = ["bars", "heat", "viridis", "magma", "cividis"]
            row(f, "Style", combo("style", styles, lambda v: S.disp.__setitem__("style", v), str,
                                  ["Bars", "Heat map (grey)", "Heat map (viridis)", "Heat map (magma)", "Heat map (cividis)"]))
            r = ttk.Frame(f)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text="Gain", width=17).pack(side="left")
            self.gain = tk.DoubleVar(value=0)
            sc = ttk.Scale(r, from_=-2, to=2, variable=self.gain, command=lambda v: self._gain(float(v)))
            sc.pack(side="left", fill="x", expand=True)
            self.gain_lab = ttk.Label(r, text="1.0×", width=5)
            self.gain_lab.pack(side="left")
            row(f, "Hide below (counts)", entry("floor", lambda v: S.disp.__setitem__("floor", max(0, float(v)))))
            self.chk = {}
            for key, label in [("shade", "Shade light / dark"), ("showOn", "Show onsets and fit"), ("showOff", "Show offsets")]:
                var = tk.BooleanVar(value=S.disp[key])
                self.chk[key] = var
                ttk.Checkbutton(f, text=label, variable=var, command=lambda k=key, vv=var: (S.disp.__setitem__(k, vv.get()), self.refresh())).pack(anchor="w")
            row(f, "Light / dark colours", combo("ldCol", ["colour", "grey"], lambda v: S.disp.__setitem__("ldCol", v), str, ["Yellow / grey-blue", "White / light grey"]))
            row(f, "Right panel", combo("rightPanel", ["none", "bars", "line", "numbers"], lambda v: S.disp.__setitem__("rightPanel", v), str,
                                        ["None", "Daily total (bars)", "Daily total (line)", "Daily total (numbers)"]))
            row(f, "LD fit line", combo("ldLine", ["mean", "regression"], lambda v: S.disp.__setitem__("ldLine", v), str, ["Mean phase (ZT)", "Regression line"]))
            # Lighting
            f = self.section("Lighting")
            r = ttk.Frame(f)
            r.pack(fill="x")
            ttk.Label(r, text="Source", width=17).pack(side="left")
            self.src_var = tk.StringVar()
            self.src_cb = ttk.Combobox(r, textvariable=self.src_var, state="readonly", values=["Recorded light", "Schedule", "None"], width=18)
            self.src_cb.pack(side="left", fill="x", expand=True)
            self.src_cb.bind("<<ComboboxSelected>>", self.on_source)
            r = ttk.Frame(f)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text="Lights-on threshold", width=17).pack(side="left")
            self.thr_var = tk.StringVar()
            self.thr_e = ttk.Entry(r, textvariable=self.thr_var, width=8)
            self.thr_e.pack(side="left")
            self.thr_e.bind("<Return>", lambda e: self._commit(self.thr_var, lambda v: setattr(self.cur(), "luxThr", float(v))))
            self.thr_e.bind("<FocusOut>", lambda e: self._commit(self.thr_var, lambda v: setattr(self.cur(), "luxThr", float(v))))
            self.sched_f = ttk.Frame(f)
            self.sched_f.pack(fill="x", pady=(4, 0))
            bf = ttk.Frame(f)
            bf.pack(fill="x", pady=(4, 0))
            self.add_cond_b = ttk.Button(bf, text="Add condition", command=self.add_cond)
            self.add_cond_b.pack(side="left")
            self.use_det_b = ttk.Button(bf, text="Use detected", command=self.use_detected)
            self.use_det_b.pack(side="left", padx=4)
            # Analysis range
            f = self.section("Analysis range")
            r = ttk.Frame(f)
            r.pack(fill="x")
            ttk.Label(r, text="From row").pack(side="left")
            self.d1 = tk.StringVar()
            self.d2 = tk.StringVar()
            e1 = ttk.Entry(r, textvariable=self.d1, width=6)
            e1.pack(side="left", padx=4)
            ttk.Label(r, text="to row").pack(side="left")
            e2 = ttk.Entry(r, textvariable=self.d2, width=6)
            e2.pack(side="left", padx=4)
            for e in (e1, e2):
                e.bind("<Return>", self.on_range)
                e.bind("<FocusOut>", self.on_range)
            self.range_b = ttk.Button(f, text="Select on actogram", command=self.toggle_range_mode)
            self.range_b.pack(anchor="w", pady=(4, 0))
            self.presets = ttk.Frame(f)
            self.presets.pack(fill="x", pady=(4, 0))
            ttk.Label(f, text="Or drag over the row labels at the left of the actogram.", foreground="#586074", wraplength=290).pack(anchor="w")
            # Onset detection
            f = self.section("Onset detection")
            for key, label in [("templateH", "Template (h)"), ("searchH", "Search ± (h)"), ("minContrast", "Min. contrast (%)"), ("guideTolH", "Line tolerance ± (h)")]:
                r = ttk.Frame(f)
                r.pack(fill="x", pady=1)
                ttk.Label(r, text=label, width=17).pack(side="left")
                var = tk.StringVar(value=str(S.ana[key]))
                e = ttk.Entry(r, textvariable=var, width=8)
                e.pack(side="left")
                lim = {"templateH": (0.5, 12), "searchH": (0.5, 12), "minContrast": (0, 100), "guideTolH": (0.25, 8)}[key]
                st = lambda v, k=key, lo=lim[0], hi=lim[1]: S.ana.__setitem__(k, min(hi, max(lo, float(v))))
                e.bind("<Return>", lambda ev, vv=var, s2=st: self._commit(vv, s2))
                e.bind("<FocusOut>", lambda ev, vv=var, s2=st: self._commit(vv, s2))
            bf = ttk.Frame(f)
            bf.pack(fill="x", pady=(4, 0))
            ttk.Button(bf, text="Edit onsets", command=lambda: self.set_tool("place" if S.tool != "place" else "zoom")).pack(side="left")
            ttk.Button(bf, text="Clear edits", command=self.clear_edits).pack(side="left", padx=4)
            # Regression fits
            f = self.section("Regression fits")
            self.fit_lb = tk.Listbox(f, height=6, exportselection=False, font=("TkFixedFont", 9))
            self.fit_lb.pack(fill="x")
            self.fit_lb.bind("<<ListboxSelect>>", self.on_fit_select)
            bf = ttk.Frame(f)
            bf.pack(fill="x", pady=(4, 0))
            ttk.Button(bf, text="Add fit", command=self.add_fit).pack(side="left")
            ttk.Button(bf, text="One per condition", command=self.split_fits).pack(side="left", padx=3)
            ttk.Button(bf, text="Reset", command=self.reset_fits).pack(side="left")
            bf = ttk.Frame(f)
            bf.pack(fill="x", pady=(3, 0))
            ttk.Button(bf, text="Remove fit", command=self.remove_fit).pack(side="left")
            ttk.Button(bf, text="Clear line", command=self.clear_line).pack(side="left", padx=3)

        def build_top(self, top):
            S = self.S
            self.summary = ttk.Label(top, font=("TkDefaultFont", 10), anchor="w")
            self.summary.pack(fill="x")
            bar = ttk.Frame(top)
            bar.pack(fill="x", pady=(4, 2))
            for fmt in ("png", "svg", "pdf", "eps"):
                ttk.Button(bar, text="Save " + fmt.upper(), style="Tool.TButton", command=lambda f=fmt: self.save_actogram(f)).pack(side="left", padx=(0, 3))
            ttk.Label(bar, text="   Mouse:").pack(side="left")
            self.tool_b = {}
            for t, lab in [("zoom", "Zoom"), ("place", "Place onset"), ("select", "Select for fit"), ("line", "Draw line"), ("pan", "Pan")]:
                b = ttk.Button(bar, text=lab, style="Tool.TButton", command=lambda tt=t: self.set_tool(tt))
                b.pack(side="left", padx=1)
                self.tool_b[t] = b
            bar2 = ttk.Frame(top)
            bar2.pack(fill="x", pady=(0, 2))
            ttk.Label(bar2, text="View:").pack(side="left")
            for lab, cmd in [("Zoom −", lambda: self.zoom(1.5, 1.5)), ("Zoom +", lambda: self.zoom(1 / 1.5, 1 / 1.5)),
                             ("Time −", lambda: self.zoom(1.5, 1)), ("Time +", lambda: self.zoom(1 / 1.5, 1)),
                             ("Rows −", lambda: self.zoom(1, 1.5)), ("Rows +", lambda: self.zoom(1, 1 / 1.5)),
                             ("Left", lambda: self.pan(-0.25, 0)), ("Up", lambda: self.pan(0, -0.25)), ("Down", lambda: self.pan(0, 0.25)), ("Right", lambda: self.pan(0.25, 0)),
                             ("Fit analysis range", self.zoom_range), ("Back", self.zoom_back), ("Reset", self.zoom_reset)]:
                ttk.Button(bar2, text=lab, style="Tool.TButton", command=cmd).pack(side="left", padx=1)
            ttk.Label(top, text="Drag a box to zoom (Zoom, Place onset) or to pick onsets (Select for fit) · Draw line: drag along the onsets to set the active fit · "
                                "Shift + drag or Pan: move · Ctrl + wheel: zoom time · Alt + wheel: zoom rows · drag over the row labels: analysis range · Esc cancels",
                      foreground="#586074", wraplength=1050).pack(fill="x")
            holder = ttk.Frame(top)
            holder.pack(fill="both", expand=True)
            self.afig = Figure(figsize=(9, 5), dpi=100)
            self.acanvas = FigureCanvasTkAgg(self.afig, master=holder)
            self.acanvas.get_tk_widget().pack(fill="both", expand=True)
            self.acanvas.mpl_connect("button_press_event", self.on_press)
            self.acanvas.mpl_connect("motion_notify_event", self.on_motion)
            self.acanvas.mpl_connect("button_release_event", self.on_release)
            self.acanvas.mpl_connect("scroll_event", self.on_scroll)
            self.acanvas.get_tk_widget().bind("<Configure>", lambda e: self.schedule_draw())
            self.bind("<Escape>", self.on_escape)

        def build_tabs(self, bottom):
            bar = ttk.Frame(bottom)
            bar.pack(fill="x", pady=(2, 2))
            ttk.Label(bar, text="Analysis", style="Head.TLabel").pack(side="left")
            ttk.Label(bar, text="  scroll with the mouse wheel; the panel can be enlarged or opened in its own window",
                      foreground="#586074").pack(side="left")
            ttk.Button(bar, text="Open in new window", style="Tool.TButton", command=self.open_window).pack(side="right")
            self.big_b = ttk.Button(bar, text="Larger analysis area", style="Tool.TButton", command=self.toggle_big)
            self.big_b.pack(side="right", padx=4)
            self.big = False
            self.windows = []          # analysis views opened in their own windows
            self.scale = 1.0           # figure height scale (larger in separate windows)
            self.nb = ttk.Notebook(bottom)
            self.nb.pack(fill="both", expand=True)
            self.tabs = {}
            for key, lab in [("period", "Periodogram"), ("wavelet", "Wavelet"), ("profile", "Activity profile"), ("onsets", "Onsets & fits"),
                             ("phase", "Phase"), ("daily", "Daily activity"), ("light", "Lighting record"), ("batch", "All files"),
                             ("info", "File info"), ("help", "Methods")]:
                fr = ttk.Frame(self.nb, padding=6)
                self.nb.add(fr, text=lab)
                self.tabs[key] = fr
            self.nb.bind("<<NotebookTabChanged>>", lambda e: self.render_tab())

        # ---------- helpers ----------
        def cur(self):
            return self.S.current()

        def msg(self, text):
            self.status.configure(text=text)

        def _commit(self, var, store):
            if self.cur() is None:
                return
            try:
                store(var.get())
            except (ValueError, TypeError):
                self.msg("Please enter a number.")
                return
            self.refresh()

        def _gain(self, v):
            self.S.disp["gainLog"] = round(v * 10) / 10
            self.gain_lab.configure(text="%.1f×" % (2 ** self.S.disp["gainLog"]))
            self.schedule_draw()

        def initial_load(self, paths):
            if paths:
                self.load_paths(paths)
            if not self.S.files:
                self.add_demo("ld-dd")

        def add_demo(self, key):
            self.S.add(load_demo(key), True)
            self.view = None
            self.refresh()

        def open_files(self):
            paths = filedialog.askopenfilenames(title="Open data files", filetypes=[("Data files", "*.csv *.txt *.tsv *.awd *.AWD"), ("All files", "*.*")])
            if paths:
                self.load_paths(paths)

        def load_paths(self, paths):
            notes = []
            for p in paths:
                try:
                    rec = load_file(p)
                    self.S.add(rec)
                    if not rec.encoding.startswith("UTF"):
                        notes.append("%s: read as %s (not UTF-8)" % (os.path.basename(p), rec.encoding))
                except Exception as e:      # noqa: BLE001 - shown to the user
                    notes.append("%s: %s" % (os.path.basename(p), e))
            self.view = None
            self.refresh()
            if notes:
                self.msg(" · ".join(notes))

        def on_file_select(self, e=None):
            s = self.file_lb.curselection()
            if s and s[0] != self.S.cur:
                self.S.cur = s[0]
                self.view = None
                self.refresh()

        def remove_file(self):
            s = self.file_lb.curselection()
            if not s:
                return
            self.S.files.pop(s[0])
            self.S.cur = min(self.S.cur, len(self.S.files) - 1)
            self.view = None
            self.refresh()

        def on_bin(self, e=None):
            F = self.cur()
            if F:
                F.binMult = self.bin_cb.current() + 1
                self.refresh()

        def on_source(self, e=None):
            F = self.cur()
            if F:
                F.lightSrc = ["recorded", "schedule", "none"][self.src_cb.current()]
                self.refresh()

        def on_range(self, e=None):
            F = self.cur()
            if not F:
                return
            try:
                F.d1 = max(1, int(float(self.d1.get())))
                F.d2 = max(F.d1, int(float(self.d2.get())))
            except ValueError:
                return
            self.refresh()

        def toggle_range_mode(self):
            self.range_mode = not self.range_mode
            self.range_b.configure(text="Drag over rows… (Esc cancels)" if self.range_mode else "Select on actogram")
            self.msg("Drag over rows on the actogram to set the analysis range." if self.range_mode else "")

        def clear_edits(self):
            F = self.cur()
            if F:
                F.manual = {}
                self.refresh()

        def set_tool(self, t):
            self.S.tool = t
            if t in ("place", "select", "line") and not self.S.disp["showOn"]:
                self.S.disp["showOn"] = True
                self.chk["showOn"].set(True)
            self.refresh()

        # ---------- fits ----------
        def on_fit_select(self, e=None):
            F = self.cur()
            s = self.fit_lb.curselection()
            if F and s:
                F.activeFit = s[0]
                self.refresh(tabs=False)

        def add_fit(self):
            F = self.cur()
            if not F:
                return
            D = derive(self.S, F)
            O = onsets(D)
            if len(F.fits) >= MAX_FITS:
                self.msg("Up to %d fits." % MAX_FITS)
                return
            freeze_auto_fits(D, O)
            F.fits.append({"name": "Fit %d" % (len(F.fits) + 1), "auto": False, "members": {}})
            F.activeFit = len(F.fits) - 1
            self.set_tool("select")

        def split_fits(self):
            F = self.cur()
            if F:
                F.condFits, F.condKey = True, None
                self.refresh()

        def reset_fits(self):
            F = self.cur()
            if F:
                F.condFits, F.condKey = False, None
                F.fits = [{"name": "Fit 1", "auto": True, "members": {}}]
                F.activeFit = 0
                self.refresh()

        def remove_fit(self):
            F = self.cur()
            if not F or len(F.fits) < 2:
                return
            D = derive(self.S, F)
            freeze_auto_fits(D, onsets(D))
            F.fits.pop(F.activeFit)
            for i, f in enumerate(F.fits):
                if re.match(r"^Fit \d+", f["name"]):
                    f["name"] = re.sub(r"^Fit \d+", "Fit %d" % (i + 1), f["name"])
            F.activeFit = 0
            self.refresh()

        def clear_line(self):
            F = self.cur()
            if F and F.fits and F.fits[F.activeFit].get("guide"):
                F.fits[F.activeFit].pop("guide")
                self.msg("Line removed; the fit keeps its rows.")
                self.refresh()

        # ---------- lighting schedule ----------
        def render_schedule(self):
            for w in self.sched_f.winfo_children():
                w.destroy()
            F = self.cur()
            show = F is not None and F.lightSrc == "schedule"
            for b in (self.add_cond_b,):
                b.configure(state="normal" if show else "disabled")
            self.use_det_b.configure(state="normal" if F is not None and F.rec.light is not None else "disabled")
            if not show:
                return
            F.schedule = normalize_schedule(F.schedule)
            sched = F.schedule
            ev = tk.BooleanVar(value=F.schedExtend)
            ttk.Checkbutton(self.sched_f, text="Extend the first condition before its start", variable=ev,
                            command=lambda: (setattr(F, "schedExtend", ev.get()), self.refresh())).pack(anchor="w")
            for i, c in enumerate(sched):
                box = ttk.Frame(self.sched_f, relief="groove", padding=4)
                box.pack(fill="x", pady=2)
                r1 = ttk.Frame(box)
                r1.pack(fill="x")
                ttk.Label(r1, text="%d" % (i + 1), width=2).pack(side="left")
                mv = tk.StringVar(value=c["mode"])
                cb = ttk.Combobox(r1, textvariable=mv, values=["LD", "LL", "DD"], state="readonly", width=4)
                cb.pack(side="left")
                cb.bind("<<ComboboxSelected>>", lambda e, j=i, v=mv: self.edit_cond(j, "mode", v.get()))
                summ = ("LD %s / %s (T %s), %s first" % (fmt_hm(c["L"]), fmt_hm(c["D"]), fmt_hm(c["L"] + c["D"]), "dark" if c.get("first") == "D" else "light")) \
                    if c["mode"] == "LD" else ("constant darkness" if c["mode"] == "DD" else "constant light")
                ttk.Label(r1, text=" " + summ, foreground="#586074", font=("TkDefaultFont", 8)).pack(side="left")
                ttk.Button(r1, text="×", width=2, command=lambda j=i: self.remove_cond(j), state="normal" if len(sched) > 1 else "disabled").pack(side="right")
                r2 = ttk.Frame(box)
                r2.pack(fill="x", pady=(2, 0))
                cands = start_candidates(F, sched, i)
                tag = (" · end of D" if start_kind(c) == "on" else " · end of L") if c["start"] in cands else ""
                ttk.Label(r2, text="Starts at").pack(side="left")
                ttk.Button(r2, text="%s %s · day %d%s ▾" % (fmt_date(F.rec.startUTC, c["start"]), fmt_clock(c["start"]), int(c["start"] // 1440) + 1, tag),
                           command=lambda j=i: self.pick_start(j)).pack(side="left", padx=4, fill="x", expand=True)
                if c["mode"] == "LD":
                    r3 = ttk.Frame(box)
                    r3.pack(fill="x", pady=(2, 0))
                    fv = tk.StringVar(value="Dark first" if c.get("first") == "D" else "Light first")
                    fcb = ttk.Combobox(r3, textvariable=fv, values=["Light first", "Dark first"], state="readonly", width=10)
                    fcb.pack(side="left")
                    fcb.bind("<<ComboboxSelected>>", lambda e, j=i, v=fv: self.edit_cond(j, "first", "D" if v.get() == "Dark first" else "L"))
                    for key in ("L", "D"):
                        ttk.Label(r3, text="  " + key).pack(side="left")
                        hv = tk.StringVar(value=fmt_hm(c[key]))
                        en = ttk.Entry(r3, textvariable=hv, width=6)
                        en.pack(side="left")
                        en.bind("<Return>", lambda e, j=i, k=key, v=hv: self.edit_cond(j, k, v.get()))
                        en.bind("<FocusOut>", lambda e, j=i, k=key, v=hv: self.edit_cond(j, k, v.get()) if v.get() != fmt_hm(F.schedule[j][k]) else None)
                        en.bind("<MouseWheel>", lambda e, j=i, k=key: self.wheel_hm(j, k, e))
                        en.bind("<Button-4>", lambda e, j=i, k=key: self.wheel_hm(j, k, e, 1))
                        en.bind("<Button-5>", lambda e, j=i, k=key: self.wheel_hm(j, k, e, -1))
            ttk.Label(self.sched_f, text="Each condition lasts until the next one starts. L and D in h:mm (T = L + D). Click a start to choose the date and time; "
                                         "after an LD condition the picker suggests its ends of D (for LL or LD with light first) or of L (for DD or LD with dark first). "
                                         "Mouse wheel on L or D: ±15 min (Shift: 1 min).", foreground="#586074", wraplength=290, font=("TkDefaultFont", 8)).pack(anchor="w", pady=(2, 0))

        def wheel_hm(self, j, key, e, direction=None):
            F = self.cur()
            d = direction if direction is not None else (1 if e.delta > 0 else -1)
            step = 1 if (e.state & 0x1) else 15
            F.schedule[j][key] = max(0, F.schedule[j][key] + d * step)
            self.refresh()
            return "break"

        def edit_cond(self, j, k, v):
            F = self.cur()
            sched = F.schedule
            c = sched[j]
            if k in ("mode", "first"):
                c[k] = v
                if c["mode"] == "LD" and not (c["L"] + c["D"] > 0):
                    c["L"], c["D"] = 720, 720
                if snap_start(F, sched, j):
                    self.msg("Start moved to the nearest %s: %s %s" % ("end of D (lights on)" if start_kind(c) == "on" else "end of L (lights off)",
                                                                       fmt_date(F.rec.startUTC, c["start"]), fmt_clock(c["start"])))
            elif k in ("L", "D"):
                m = parse_hm(v)
                if m is None:
                    self.msg("Use h:mm, e.g. 12:00")
                    return
                c[k] = max(0, m)
            F.schedule = normalize_schedule(sched)
            self.refresh()

        def remove_cond(self, j):
            F = self.cur()
            if len(F.schedule) > 1:
                F.schedule.pop(j)
                self.refresh()

        def pick_start(self, j):
            F = self.cur()
            dlg = DateTimePicker(self, F, j)
            self.wait_window(dlg)
            if dlg.result is not None:
                F.schedule[j]["start"] = dlg.result
                F.schedule = normalize_schedule(F.schedule)
                self.refresh()

        def add_cond(self):
            F = self.cur()
            if F:
                add_condition(F)
                self.refresh()

        def use_detected(self):
            F = self.cur()
            if not F or F.rec.light is None:
                return
            segs = detect_lighting(F.rec, F.luxThr)["segments"]
            if segs:
                F.schedule = segments_to_schedule(segs, F.rec, F.luxThr)
                F.lightSrc = "schedule"
                self.msg("Schedule copied from the light column")
                self.refresh()

        # ---------- refresh ----------
        def refresh(self, tabs=True):
            F = self.cur()
            S = self.S
            self.file_lb.delete(0, "end")
            for E in S.files:
                self.file_lb.insert("end", "%s  %gm%s" % (E.rec.name, E.rec.intervalMin, " · demo" if E.synthetic else ""))
            if F is None:
                self.afig.clear()
                self.acanvas.draw_idle()
                self.summary.configure(text="Load a data file (CSV or TXT) or a demo recording to begin.")
                return
            self.file_lb.selection_clear(0, "end")
            self.file_lb.selection_set(S.cur)
            # Control values
            n = max_bin_mult(F)
            self.bin_cb.configure(values=["%d× · %g min" % (m, m * F.rec.intervalMin) for m in range(1, n + 1)])
            effective_bin(F)
            self.bin_cb.current(F.binMult - 1)
            self.v["mult"].set(["Single", "Double", "Triple"][S.disp["mult"] - 1])
            for key in ("T", "offsetH", "rowH", "floor", "widthPx"):
                self.v[key].set("%g" % S.disp[key])
            labels = {"widthMode": {"auto": "Auto (900 px)", "custom": "Custom"},
                      "style": dict(zip(["bars", "heat", "viridis", "magma", "cividis"], ["Bars", "Heat map (grey)", "Heat map (viridis)", "Heat map (magma)", "Heat map (cividis)"])),
                      "ldCol": {"colour": "Yellow / grey-blue", "grey": "White / light grey"},
                      "rightPanel": {"none": "None", "bars": "Daily total (bars)", "line": "Daily total (line)", "numbers": "Daily total (numbers)"},
                      "ldLine": {"mean": "Mean phase (ZT)", "regression": "Regression line"}}
            for key, mp in labels.items():
                self.v[key].set(mp[S.disp[key]])
            self.src_cb.current(["recorded", "schedule", "none"].index(F.lightSrc))
            self.thr_var.set("%g" % F.luxThr)
            for t, b in self.tool_b.items():
                b.configure(style="On.TButton" if S.tool == t else "Tool.TButton")
            D = derive(S, F)
            F.d1, F.d2 = D.k1 + 1, D.k2 + 1
            self.d1.set(str(F.d1))
            self.d2.set(str(F.d2))
            for w in self.presets.winfo_children():
                w.destroy()
            for lab, a, b in range_presets(D):
                ttk.Button(self.presets, text=lab, style="Tool.TButton", command=lambda a=a, b=b: self.set_range(a, b)).pack(side="left", padx=(0, 3))
            O = onsets(D)
            self.fit_lb.delete(0, "end")
            for f in O["fits"]:
                self.fit_lb.insert("end", "%s%s%s · %sn %d · τ %s" % (f["name"], " (auto)" if f["auto"] else "", " ⟋" if f["guide"] else "",
                                                                     (f["mode"] + " · ") if f["mode"] not in ("none", "?") else "", len(f["members"]),
                                                                     "%.2f h" % f["fit"]["slope"] if f["fit"] else "–"))
            self.fit_lb.selection_set(F.activeFit)
            self.render_schedule()
            pg = periodogram(D)
            r = F.rec
            self.summary.configure(text="%s%s    start %s %s · length %.1f d · sample %g min · light: %s · χ² peak in range %s" % (
                r.name, "  (simulated demo)" if F.synthetic else "", fmt_date(r.startUTC, r.t0Min), fmt_clock(r.t0Min),
                len(r.counts) * r.intervalMin / 1440, r.intervalMin, {"recorded": "recorded", "schedule": "schedule", "none": "none"}[F.lightSrc],
                "%.2f h" % pg["peak"]["periodH"] if pg["peak"] else "–"))
            self.draw_actogram()
            if tabs:
                self.render_tab()

        def set_range(self, a, b):
            F = self.cur()
            F.d1, F.d2 = a, b
            self.refresh()

        def schedule_draw(self):
            if self._pending:
                self.after_cancel(self._pending)
            self._pending = self.after(60, self.draw_actogram)

        def draw_actogram(self):
            self._pending = None
            F = self.cur()
            if F is None:
                return
            D = derive(self.S, F)
            O = onsets(D)
            w = max(400, self.acanvas.get_tk_widget().winfo_width())
            h = max(300, self.acanvas.get_tk_widget().winfo_height())
            self.geom = draw_actogram(self.afig, D, O, view=self.view)
            self.geom["D"] = D
            self.afig.subplots_adjust(left=min(0.2, 115 / w), right=1 - 12 / w, top=1 - 46 / h, bottom=10 / h)
            self.acanvas.draw_idle()

        # ---------- actogram mouse ----------
        def data_xy(self, ev):
            ax = self.geom["ax"]
            x, y = ax.transData.inverted().transform((ev.x, ev.y))
            return x, y

        def row_of(self, y):
            return int(math.floor(y))

        def on_press(self, ev):
            if self.geom is None or ev.button != 1:
                return
            ax = self.geom["ax"]
            x, y = self.data_xy(ev)
            rows = self.geom["rows"]
            if not (0 <= y < rows):
                return
            in_labels = x < ax.get_xlim()[0]
            if self.range_mode or in_labels:
                self.drag = {"kind": "range", "r0": self.row_of(y), "r1": self.row_of(y)}
                return
            if ev.inaxes is not ax:
                return
            pan = self.S.tool == "pan" or (ev.key and "shift" in ev.key and self.S.tool != "place")
            kind = "pan" if pan else {"select": "sel", "line": "line"}.get(self.S.tool, "box")
            self.drag = {"kind": kind, "x": x, "y": y, "px": ev.x, "py": ev.y, "moved": False, "artist": None,
                         "lim": (ax.get_xlim(), ax.get_ylim()), "shift": bool(ev.key and "shift" in ev.key)}

        def on_motion(self, ev):
            if self.geom is None:
                return
            if self.drag is None:
                self.hover(ev)
                return
            ax = self.geom["ax"]
            x, y = self.data_xy(ev)
            d = self.drag
            if d["kind"] == "range":
                d["r1"] = min(self.geom["rows"] - 1, max(0, self.row_of(y)))
                a, b = sorted((d["r0"], d["r1"]))
                if d.get("artist"):
                    d["artist"].remove()
                d["artist"] = ax.axhspan(a, b + 1, color="#3446c9", alpha=0.12, zorder=7)
                self.acanvas.draw_idle()
                return
            if not d["moved"] and math.hypot(ev.x - d["px"], ev.y - d["py"]) < 5:
                return
            d["moved"] = True
            if d["kind"] == "pan":
                (x0, x1), (y1, y0) = d["lim"]
                dx = (ev.x - d["px"]) / ax.bbox.width * (x1 - x0)
                dy = (ev.y - d["py"]) / ax.bbox.height * (y1 - y0)
                self.view = {"x0": x0 - dx, "x1": x1 - dx, "y0": y0 + dy, "y1": y1 + dy}
                ax.set_xlim(self.view["x0"], self.view["x1"])
                ax.set_ylim(self.view["y1"], self.view["y0"])
                self.acanvas.draw_idle()
                return
            if d.get("artist"):
                d["artist"].remove()
            if d["kind"] == "line":
                d["artist"], = ax.plot([d["x"], x], [d["y"], y], color="#3446c9", lw=2, ls="--", zorder=8)
            else:
                d["artist"] = ax.add_patch(Rectangle((min(d["x"], x), min(d["y"], y)), abs(x - d["x"]), abs(y - d["y"]), fill=True,
                                                     facecolor="#3446c9" if d["kind"] == "box" else "#eb6834", alpha=0.12,
                                                     edgecolor="#3446c9" if d["kind"] == "box" else "#eb6834", lw=1.2, ls="-" if d["kind"] == "box" else "--", zorder=8))
            d["end"] = (x, y)
            self.acanvas.draw_idle()

        def on_release(self, ev):
            d = self.drag
            self.drag = None
            if d is None or self.geom is None:
                return
            G = self.geom
            D = G["D"]
            F = D.F
            if d["kind"] == "range":
                a, b = sorted((d["r0"], d["r1"]))
                F.d1, F.d2 = D.kStart + a + 1, D.kStart + b + 1
                self.range_mode = False
                self.range_b.configure(text="Select on actogram")
                self.msg("Analysis range: rows %d–%d" % (F.d1, F.d2))
                self.refresh()
                return
            x, y = self.data_xy(ev)
            bh = D.binMin / 60
            if not d["moved"]:
                if d["kind"] == "sel":
                    best, bd = None, 10
                    for mx, my, k in G["markers"]:
                        px, py = G["ax"].transData.transform((mx, my))
                        dd = math.hypot(px - ev.x, py - ev.y)
                        if dd <= bd:
                            best, bd = k, dd
                    if best is not None:
                        toggle_in_active_fit(D, [best])
                        self.refresh()
                elif self.S.tool == "place":
                    r = self.row_of(y)
                    if 0 <= r < G["rows"] and x >= 0:
                        place_onset(D, D.kStart + r, int(x / bh), delete=d["shift"])
                        self.refresh()
                return
            if d["kind"] == "pan":
                return
            if d.get("artist"):
                d["artist"].remove()
            if d["kind"] == "line":
                ra, rb = self.row_of(d["y"]), self.row_of(y)
                ra, rb = min(G["rows"] - 1, max(0, ra)), min(G["rows"] - 1, max(0, rb))
                ka, kb = D.kStart + ra, D.kStart + rb
                msg = apply_guide(D, {"k": ka, "t": (D.rowStart(ka) + d["x"] / bh) * D.binMin}, {"k": kb, "t": (D.rowStart(kb) + x / bh) * D.binMin})
                self.msg(msg)
                self.refresh()
                return
            x0, x1 = sorted((d["x"], x))
            y0, y1 = sorted((d["y"], y))
            if d["kind"] == "sel":
                ks = sorted({k for mx, my, k in G["markers"] if x0 <= mx <= x1 and y0 <= my <= y1})
                if ks:
                    toggle_in_active_fit(D, ks)
                    self.refresh()
                else:
                    self.acanvas.draw_idle()
                return
            ax = G["ax"]
            cx, cy = ax.get_xlim(), ax.get_ylim()
            px0, px1 = ax.transData.transform((x0, y0)), ax.transData.transform((x1, y1))
            tz, rz = abs(px1[0] - px0[0]) >= 8, abs(y1 - y0) >= 0.8
            if not tz and not rz:
                self.acanvas.draw_idle()
                return
            self.history.append(self.view)
            v = {"x0": cx[0], "x1": cx[1], "y0": cy[1], "y1": cy[0]}
            if tz:
                v["x0"], v["x1"] = x0, x1
            if rz:
                v["y0"], v["y1"] = math.floor(y0), math.ceil(y1)
            self.view = v
            self.draw_actogram()

        def on_scroll(self, ev):
            if self.geom is None:
                return
            key = ev.key or ""
            if "control" in key:
                self.zoom(1.25 if ev.button == "down" else 0.8, 1)
            elif "alt" in key:
                self.zoom(1, 1.25 if ev.button == "down" else 0.8)
            elif self.view:
                self.pan(0, 0.15 if ev.button == "down" else -0.15)

        def on_escape(self, e=None):
            if self.drag and self.drag.get("artist"):
                self.drag["artist"].remove()
                self.acanvas.draw_idle()
            self.drag = None
            if self.range_mode:
                self.toggle_range_mode()

        def hover(self, ev):
            G = self.geom
            if ev.inaxes is not G["ax"] or ev.xdata is None:
                return
            D = G["D"]
            r = self.row_of(ev.ydata)
            if not (0 <= r < G["rows"]):
                return
            k = D.kStart + r
            j = int(ev.xdata / (D.binMin / 60))
            b = D.rowStart(k) + j
            t = b * D.binMin
            v = D.B.act[b] if 0 <= b < D.B.n else NAN
            st = D.light[b] if 0 <= b < D.B.n else NAN
            lux = D.B.lux[b] if D.B.lux is not None and 0 <= b < D.B.n else NAN
            tip = {"zoom": "drag a box to zoom", "place": "click: place onset · Shift-click: delete",
                   "select": "click an onset or drag a box: add/remove in %s" % (D.F.fits[D.F.activeFit]["name"] if D.F.fits else "the active fit"),
                   "line": "drag a line along the onsets", "pan": "drag to move the view"}[self.S.tool]
            self.msg("Row %d · %s %s · activity %s · %s%s    —  %s" % (k + 1, fmt_date(D.F.rec.startUTC, t), fmt_clock(t),
                     "missing" if math.isnan(v) else "%d / %g min" % (v, D.binMin), {1.0: "light", 0.0: "dark"}.get(st, "light unknown"),
                     "" if math.isnan(lux) else " · %.0f lux" % lux, tip))

        # ---------- view ----------
        def current_lim(self):
            ax = self.geom["ax"]
            (x0, x1), (y1, y0) = ax.get_xlim(), ax.get_ylim()
            return x0, x1, y0, y1

        def zoom(self, fx_, fy_):
            if self.geom is None:
                return
            x0, x1, y0, y1 = self.current_lim()
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            wx, wy = (x1 - x0) * fx_, (y1 - y0) * fy_
            xmax, rows = self.geom["xmax"], self.geom["rows"]
            wx, wy = min(xmax, max(xmax / 48, wx)), min(rows, max(2, wy))
            self.history.append(self.view)
            self.view = self.clamp_view({"x0": cx - wx / 2, "x1": cx + wx / 2, "y0": cy - wy / 2, "y1": cy + wy / 2})
            self.draw_actogram()

        def clamp_view(self, v):
            xmax, rows = self.geom["xmax"], self.geom["rows"]
            wx, wy = v["x1"] - v["x0"], v["y1"] - v["y0"]
            x0 = min(max(0, v["x0"]), max(0, xmax - wx))
            y0 = min(max(0, v["y0"]), max(0, rows - wy))
            if wx >= xmax - 1e-9 and wy >= rows - 1e-9:
                return None
            return {"x0": x0, "x1": x0 + wx, "y0": y0, "y1": y0 + wy}

        def pan(self, dx, dy):
            if self.geom is None:
                return
            x0, x1, y0, y1 = self.current_lim()
            v = {"x0": x0 + dx * (x1 - x0), "x1": x1 + dx * (x1 - x0), "y0": y0 + dy * (y1 - y0), "y1": y1 + dy * (y1 - y0)}
            self.view = self.clamp_view(v)
            self.draw_actogram()

        def zoom_range(self):
            F = self.cur()
            if not F or self.geom is None:
                return
            D = derive(self.S, F)
            self.history.append(self.view)
            self.view = {"x0": 0, "x1": self.geom["xmax"], "y0": D.k1 - D.kStart, "y1": D.k2 - D.kStart + 1}
            self.draw_actogram()

        def zoom_back(self):
            if self.history:
                self.view = self.history.pop()
                self.draw_actogram()

        def zoom_reset(self):
            self.history.append(self.view)
            self.view = None
            self.draw_actogram()

        # ---------- saving ----------
        def ask_save(self, default, exts):
            types = [{"png": ("PNG image", "*.png"), "svg": ("SVG (vector)", "*.svg"), "pdf": ("PDF (vector)", "*.pdf"),
                      "eps": ("EPS (vector)", "*.eps"), "csv": ("CSV table", "*.csv")}[e] for e in exts]
            return filedialog.asksaveasfilename(title="Save", initialfile=default, defaultextension="." + exts[0], filetypes=types)

        def save_actogram(self, fmt):
            F = self.cur()
            if not F:
                return
            D = derive(self.S, F)
            path = self.ask_save(safe_name(F.rec.name) + "_actogram." + fmt, [fmt])
            if path:
                fig = actogram_figure(D, onsets(D), width_px=self.S.disp["widthPx"] if self.S.disp["widthMode"] == "custom" else None)
                fig.savefig(path, format=fmt, dpi=200 if fmt == "png" else 100, facecolor="white")
                self.msg("Saved " + path)

        def save_figure(self, fig, stem):
            F = self.cur()
            path = self.ask_save(safe_name(F.rec.name) + "_" + stem + ".png", ["png", "svg", "pdf", "eps"])
            if path:
                ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
                fig.savefig(path, format=ext, dpi=200 if ext == "png" else 100, facecolor="white")
                self.msg("Saved " + path)

        def save_csv(self, rows, stem):
            F = self.cur()
            path = self.ask_save(safe_name(F.rec.name if F else "all") + "_" + stem + ".csv", ["csv"])
            if path:
                with open(path, "w", newline="", encoding="utf-8") as fh:
                    fh.write(to_csv(rows))
                self.msg("Saved " + path)

        # ---------- tabs ----------
        def tab_key(self):
            try:
                idx = self.nb.index("current")
            except tk.TclError:
                return "period"
            return list(self.tabs)[idx]

        def clear(self, fr):
            for w in fr.winfo_children():
                w.destroy()

        def fig_in(self, fr, h=3.6, w=9):
            """Figure of fixed height (h inches at 100 dpi, larger in separate windows) and full width."""
            h *= self.scale
            fig = Figure(figsize=(w, h), dpi=100)
            cv = FigureCanvasTkAgg(fig, master=fr)
            cv.get_tk_widget().configure(height=int(h * 100))
            cv.get_tk_widget().pack(fill="x")
            return fig, cv

        def table_in(self, fr, header, rows, height=8):
            height = max(3, min(len(rows), max(height, 30 if self.scale > 1 else 20)))
            tf = ttk.Frame(fr)
            tf.pack(fill="x", pady=(6, 0))
            tv = ttk.Treeview(tf, columns=list(range(len(header))), show="headings", height=height)
            for i, h in enumerate(header):
                tv.heading(i, text=h)
                tv.column(i, width=max(60, min(220, 9 * len(str(h)) + 20)), anchor="e" if i else "w", stretch=True)
            for r in rows:
                tv.insert("", "end", values=["" if v is None else v for v in r])
            sb = ttk.Scrollbar(tf, orient="vertical", command=tv.yview)
            sx = ttk.Scrollbar(tf, orient="horizontal", command=tv.xview)
            tv.configure(yscrollcommand=sb.set, xscrollcommand=sx.set)
            tv.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            sx.grid(row=1, column=0, sticky="ew")
            tf.rowconfigure(0, weight=1)
            tf.columnconfigure(0, weight=1)
            return tv

        def controls(self, fr):
            c = ttk.Frame(fr)
            c.pack(fill="x", pady=(0, 4))
            return c

        def opt(self, parent, label, key, values, labels, conv=str, store=None):
            ttk.Label(parent, text=label).pack(side="left", padx=(6, 2))
            var = tk.StringVar(value=labels[values.index(self.S.ana[key])] if self.S.ana[key] in values else labels[0])
            cb = ttk.Combobox(parent, textvariable=var, values=labels, state="readonly", width=min(20, max(6, max(len(l) for l in labels))))
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: (self.S.ana.__setitem__(key, conv(values[labels.index(var.get())])), self.refresh()))

        def num(self, parent, label, key, width=6, lo=None, hi=None):
            ttk.Label(parent, text=label).pack(side="left", padx=(6, 2))
            var = tk.StringVar(value="%g" % self.S.ana[key])
            e = ttk.Entry(parent, textvariable=var, width=width)
            e.pack(side="left")

            def store(v):
                x = float(v)
                if lo is not None:
                    x = max(lo, x)
                if hi is not None:
                    x = min(hi, x)
                self.S.ana[key] = x
            e.bind("<Return>", lambda ev: self._commit(var, store))
            e.bind("<FocusOut>", lambda ev: self._commit(var, store) if var.get() != "%g" % self.S.ana[key] else None)

        def render_tab(self):
            key = self.tab_key()
            y = self._scroll_pos(self.tabs[key])
            self.render_into(self.tabs[key], key)
            self._scroll_to(self.tabs[key], y)
            for win in list(self.windows):
                if not win["top"].winfo_exists():
                    self.windows.remove(win)
                    continue
                y = self._scroll_pos(win["holder"])
                self.render_into(win["holder"], win["key"], scale=1.35)
                self._scroll_to(win["holder"], y)

        def _canvas_of(self, holder):
            for w in holder.winfo_children():
                for c in w.winfo_children():
                    if getattr(c, "_scrollable", False):
                        return c
            return None

        def _scroll_pos(self, holder):
            c = self._canvas_of(holder)
            return c.yview()[0] if c is not None else 0.0

        def _scroll_to(self, holder, y):
            c = self._canvas_of(holder)
            if c is not None and y:
                c.update_idletasks()
                c.configure(scrollregion=c.bbox("all"))
                c.yview_moveto(y)

        def render_into(self, holder, key, scale=1.0):
            """Render analysis tab `key` into `holder` as a scrolling page."""
            self.clear(holder)
            fr = self.scroll_area(holder)
            self.scale = scale
            F = self.cur()
            try:
                if key == "help":
                    t = tk.Text(fr, wrap="word", font=("TkFixedFont", 9), height=60)
                    t.insert("1.0", METHODS_TEXT)
                    t.configure(state="disabled")
                    t.pack(fill="both", expand=True)
                    return
                if F is None:
                    ttk.Label(fr, text="Load a data file (CSV or TXT) or a demo recording to begin.").pack()
                    return
                D = derive(self.S, F)
                O = onsets(D)
                getattr(self, "tab_" + key)(fr, D, O)
            finally:
                self.scale = 1.0

        def tab_period(self, fr, D, O):
            c = self.controls(fr)
            self.opt(c, "Method", "method", ["chi", "ls"], ["χ² (Sokolove–Bushell)", "Lomb–Scargle"])
            self.num(c, "From", "pmin", lo=1, hi=100)
            self.num(c, "to", "pmax", lo=2, hi=200)
            self.opt(c, "α", "alpha", [0.05, 0.01, 0.001], ["0.05", "0.01", "0.001"], float)
            fig, cv = self.fig_in(fr, 5.4 if O["fits"] else 4.2)
            ttk.Button(c, text="Save CSV", command=lambda: self.save_csv(periodogram_table(D, O), "periodogram")).pack(side="right")
            ttk.Button(c, text="Save figure…", command=lambda: self.save_figure(fig, "periodogram")).pack(side="right", padx=4)
            ttk.Button(c, text="Select range on actogram", command=self.toggle_range_mode).pack(side="right")
            pg = draw_periodogram(fig, D, O)
            cv.draw_idle()
            rows = []
            for f in O["fits"]:
                r = fit_periodogram(D, f)
                pk = r["pg"]["peak"] if r else None
                rows.append([f["name"], f["mode"], "%d–%d" % (r["kMin"] + 1, r["kMax"] + 1) if r else "–", f2(r["days"], 1) if r else "–",
                             f2(pk["periodH"]) if pk else "–", f2(pk["value"], 0) if pk else "–", f2(pk["amp"], 0) if pk else "–",
                             ("yes" if pk["significant"] else "no") if pk else "–", f2(f["fit"]["slope"], 3) if f["fit"] else "–",
                             ("%+.2f" % (pk["periodH"] - f["fit"]["slope"])) if pk and f["fit"] else "–"])
            self.table_in(fr, ["Fit", "Condition", "Rows", "Days", "Peak period (h)", "Qp / power", "− crit", "Significant", "τ from onsets (h)", "Peak − τ (h)"], rows, 4)
            pk = pg["peak"]
            self.msg("Peak period %s · data analysed rows %d–%d (%.1f d, %g-min bins)" % ("%.2f h" % pk["periodH"] if pk else "–", D.k1 + 1, D.k2 + 1,
                                                                                         (D.b - D.a) * D.binMin / 1440, D.binMin))

        def tab_wavelet(self, fr, D, O):
            c = self.controls(fr)
            self.num(c, "Periods", "wmin", lo=1, hi=100)
            self.num(c, "to", "wmax", lo=2, hi=200)
            self.opt(c, "Data", "wData", ["range", "all"], ["Analysis range", "Whole record"])
            self.opt(c, "Colours", "wCmap", ["viridis", "magma", "cividis"], ["viridis", "magma", "cividis"])
            W = wavelet_data(D)
            if W is None:
                ttk.Label(fr, text="Not enough data for the wavelet.").pack()
                return
            fig, cv = self.fig_in(fr, 5.0)
            ttk.Button(c, text="Save CSV", command=lambda: self.save_csv(wavelet_table(D, O, W), "wavelet")).pack(side="right")
            ttk.Button(c, text="Save figure…", command=lambda: self.save_figure(fig, "wavelet")).pack(side="right", padx=4)
            draw_wavelet(fig, D, O, W)
            cv.draw_idle()
            rows = []
            for f in O["fits"]:
                st = None
                if f["kMin"] is not None:
                    st = ridge_stats(W, (W["tMin"] >= D.rowStart(f["kMin"]) * D.binMin) & (W["tMin"] < D.rowStart(f["kMax"] + 1) * D.binMin))
                r = fit_periodogram(D, f, "chi")
                rows.append([f["name"], f["mode"], "%d–%d" % (f["kMin"] + 1, f["kMax"] + 1) if f["kMin"] is not None else "–",
                             f2(st["mean"]) if st else "–", f2(st["sd"]) if st else "–", f2(st["hours"], 0) if st else "–",
                             f2(f["fit"]["slope"], 3) if f["fit"] else "–", f2(r["pg"]["peak"]["periodH"]) if r and r["pg"]["peak"] else "–"])
            self.table_in(fr, ["Fit", "Condition", "Rows", "Ridge period (h)", "SD (h)", "Hours used", "τ from onsets (h)", "χ² peak (h)"], rows, 3)
            all_st = ridge_stats(W, np.ones(W["tMin"].size, bool))
            self.msg("Mean ridge period %s · usable ridge %.0f%%" % (f2(all_st["mean"]) + " h" if all_st else "–", 100 * W["valid"].mean()))

        def tab_profile(self, fr, D, O):
            c = self.controls(fr)
            self.opt(c, "Fold at", "fold", ["row", "peak", "custom"], ["Row period", "Periodogram peak", "Custom"])
            self.num(c, "Custom (h)", "foldCustom", lo=1, hi=100)
            self.opt(c, "Fit period", "fitFold", ["auto", "peak", "row", "custom"], ["Auto (LD: T · DD/LL: τ)", "Periodogram peak of the fit", "Row period", "Custom"])
            self.num(c, "Custom (h)", "fitFoldCustom", lo=1, hi=100)
            c2 = self.controls(fr)
            fig, cv = self.fig_in(fr, 2.9 + 2.3 * len(O["fits"]))
            ttk.Button(c2, text="Save CSV", command=lambda: self.save_csv(profile_table(D, O), "profile")).pack(side="right")
            ttk.Button(c2, text="Save figure…", command=lambda: self.save_figure(fig, "profile")).pack(side="right", padx=4)
            self.opt(c2, "X axis", "fitAxis", ["phase", "hours"], ["ZT (LD) / CT (DD, LL)", "Hours from reference"])
            pr, fps = draw_profile(fig, D, O)
            cv.draw_idle()
            rows = []
            for r in fps:
                pk = r["peak"]
                rows.append([r["f"]["name"], r["f"]["mode"], "%d–%d" % (r["f"]["kMin"] + 1, r["f"]["kMax"] + 1), f2(r["days"], 1), f2(r["P"], 3), r["src"],
                             axis_label(r), ("%s%.1f" % (r["axis"], r["x"][pk]) if r["axis"] != "hours" else "%.1f h" % r["x"][pk]) if pk is not None else "–",
                             f2(r["mean"][pk], 1) if pk is not None else "–", f2(r["pctDark"], 1)])
            self.table_in(fr, ["Fit", "Condition", "Rows", "Days", "Period (h)", "Period from", "X axis", "Peak phase", "Peak (counts/bin)", "% in dark"], rows, 3)
            npv = pr["np"]
            self.msg("Folding period %.2f h · %% activity in dark %s · IS %s · IV %s · RA %s" % (pr["PH"], f2(pr["pctDark"], 1), f2(npv["IS"], 3) if npv else "–",
                                                                                              f2(npv["IV"], 3) if npv else "–", f2(npv["RA"], 3) if npv else "–"))

        def tab_onsets(self, fr, D, O):
            c = self.controls(fr)
            self.opt(c, "Phase as", "phaseUnit", ["ztct", "deg"], ["ZT / CT (h)", "Degrees (0–360°)"])
            self.opt(c, "Free-run 0°", "phaseRef", ["ld", "start", "rec", "row0"], ["Projected ZT0 of preceding LD", "Start of the condition", "00:00 of recording day 1", "Start of actogram row 1"])
            ttk.Button(c, text="Save onsets CSV", command=lambda: self.save_csv(onsets_table(D, O), "onsets")).pack(side="right")
            ttk.Button(c, text="Save fits CSV", command=lambda: self.save_csv(fits_table(D, O), "fits")).pack(side="right", padx=4)
            rows = []
            for f in O["fits"]:
                ft, ph = f["fit"], f["phase"]
                rows.append([f["name"], seg_label(O["segs"], f["segIndex"]) if f["segIndex"] >= 0 else "no lighting info",
                             "%.3f ± %.3f" % (ft["slope"], ft["se"]) if ft and math.isfinite(ft["se"]) else (f2(ft["slope"], 3) if ft else "needs ≥ 2 onsets"),
                             f2(ft["r2"], 3) if ft else "–", len(f["members"]), "%d–%d" % (f["kMin"] + 1, f["kMax"] + 1) if f["kMin"] is not None else "–",
                             ("%s%.2f (%.1f°)" % (ph["label"], ph["h"], ph["deg"])) if ph else "–", len(f["excludedList"] or [])])
            self.table_in(fr, ["Fit", "Condition", "τ ± SE (h)", "r²", "n", "Rows", "Phase", "Excluded"], rows, 3)
            psis = [o["psi"] for o in O["list"] if D.k1 <= o["k"] <= D.k2 and o["psi"] is not None]
            al = [(o["offsetBin"] - o["onsetBin"]) * D.binMin / 60 for o in O["list"] if D.k1 <= o["k"] <= D.k2 and o["onsetBin"] is not None and o["offsetBin"] is not None]
            ttk.Label(fr, text="Phase angle ψ %s · activity duration α %s · onsets edited %d" % (
                ("%+.2f h (SD %.2f, n %d)" % (np.mean(psis), np.std(psis, ddof=1) if len(psis) > 1 else 0, len(psis))) if psis else "–",
                ("%.2f h (n %d)" % (np.mean(al), len(al))) if al else "–", sum(1 for o in O["list"] if o["manual"])), foreground="#586074").pack(anchor="w", pady=4)
            t = onsets_table(D, O)
            self.table_in(fr, t[0], t[1:], 12)

        def tab_phase(self, fr, D, O):
            c = self.controls(fr)
            self.opt(c, "Phase as", "phaseUnit", ["ztct", "deg"], ["ZT / CT (h)", "Degrees (0–360°)"])
            self.opt(c, "Free-run 0°", "phaseRef", ["ld", "start", "rec", "row0"], ["Projected ZT0 of preceding LD", "Start of the condition", "00:00 of recording day 1", "Start of actogram row 1"])
            table, diffs = phase_table(D, O)
            ttk.Button(c, text="Save CSV", command=lambda: self.save_csv(table, "phase")).pack(side="right")
            ttk.Button(c, text="Copy as tab-separated text", command=lambda: self.copy_tsv(table)).pack(side="right", padx=4)
            fig, cv = self.fig_in(fr, 4.2)
            ttk.Button(c, text="Save figure…", command=lambda: self.save_figure(fig, "phase")).pack(side="right")
            rows = draw_phase(fig, D, O)
            cv.draw_idle()
            out = []
            for r in rows:
                f, cs = r["f"], r["cs"]
                out.append([f["name"], f["mode"], "%d–%d" % (f["kMin"] + 1, f["kMax"] + 1) if f["kMin"] is not None else "–", len(f["members"]),
                            ("%.3f ± %s" % (f["fit"]["slope"], f2(f["fit"]["se"], 3))) if f["fit"] else "–",
                            ("%s%.2f" % (r["label"], f["phase"]["h"])) if f["phase"] else "–", f2(f["phase"]["deg"], 1) if f["phase"] else "–",
                            f2(cs["R"], 3) if cs else "–", f2(cs["csdDeg"] / 360 * r["Th"]) if cs and math.isfinite(cs["csdDeg"]) else "–",
                            fmt_p(cs["p"]) if cs else "–", r["ref"]])
            self.table_in(fr, ["Fit", "Condition", "Rows", "n", "τ ± SE (h)", "Phase (h)", "Phase (°)", "R", "Circ. SD (h)", "Rayleigh p", "0° reference"], out, 3)
            if diffs:
                self.table_in(fr, ["From", "To", "Δ phase (°)", "Δ phase (h)", "Direction"],
                              [[a["f"]["name"], b["f"]["name"], "%+.1f" % d, "%+.2f" % (d / 15), "later (delay)" if d > 0 else "earlier (advance)" if d < 0 else "same"] for a, b, d in diffs], 2)

        def copy_tsv(self, rows):
            self.clipboard_clear()
            self.clipboard_append("\n".join("\t".join(str(v) for v in r) for r in rows))
            self.msg("Copied as tab-separated text")

        def tab_daily(self, fr, D, O):
            c = self.controls(fr)
            t = daily_table(D)
            fig, cv = self.fig_in(fr, 3.4)
            ttk.Button(c, text="Save CSV", command=lambda: self.save_csv(t, "daily")).pack(side="right")
            ttk.Button(c, text="Save figure…", command=lambda: self.save_figure(fig, "daily")).pack(side="right", padx=4)
            draw_daily(fig, D)
            fig.tight_layout()
            cv.draw_idle()
            self.table_in(fr, t[0], t[1:], 8)

        def tab_light(self, fr, D, O):
            F = D.F
            c = self.controls(fr)
            if F.rec.light is None:
                ttk.Label(fr, text="This file has no light column. Use Lighting → Source → Schedule to describe the light regime by hand.").pack(anchor="w")
                return
            t = light_table(F)
            fig, cv = self.fig_in(fr, 3.0)
            ttk.Button(c, text="Save CSV", command=lambda: self.save_csv(t, "lighting")).pack(side="right")
            ttk.Button(c, text="Save figure…", command=lambda: self.save_figure(fig, "lighting")).pack(side="right", padx=4)
            segs = detect_lighting(F.rec, F.luxThr)["segments"]
            ttk.Label(c, text="Detected: " + "  ·  ".join("%s days %d–%d%s" % (s["mode"], s["startDay"], s["endDay"], seg_detail(F, s)) for s in segs)).pack(side="left")
            draw_light(fig, D)
            fig.tight_layout()
            cv.draw_idle()
            self.table_in(fr, t[0], t[1:], 8)

        def tab_batch(self, fr, D, O):
            c = self.controls(fr)
            t = batch_table(self.S)
            ttk.Button(c, text="Save CSV", command=lambda: self.save_csv(t, "all_files")).pack(side="right")
            ttk.Label(c, text="Each recording uses its own settings (bin size, lighting, analysis range, fits).", foreground="#586074").pack(side="left")
            self.table_in(fr, t[0], t[1:], 10)

        def tab_info(self, fr, D, O):
            F = D.F
            R = F.rec
            end = R.t0Min + len(R.counts) * R.intervalMin
            rows = [("File", R.fileName), ("Recording name", R.name), ("Format", "Timestamped table (CSV/TXT)" if R.format == "CSV" else R.format)]
            if R.format == "CSV":
                rows += [("Columns", "timestamp, activity" + (", light" if R.light is not None else "")), ("Sampling interval", "%g min (median spacing between rows)" % R.intervalMin)]
            else:
                h = R.header
                rows += [("Line 1 · Name", h["name"]), ("Line 2 · Start date", h["date"]), ("Line 3 · Start time", h["time"]),
                         ("Line 4 · Interval code", "%s → %g min per sample" % (h["interval"], R.intervalMin)), ("Line 5 · Age", h["age"]), ("Line 6 · ID", h["id"]), ("Line 7 · Sex", h["sex"])]
            rows += [("Samples", "{:,}".format(len(R.counts))), ("Recording", "%s %s → %s %s (%.2f d)" % (fmt_date(R.startUTC, R.t0Min), fmt_clock(R.t0Min), fmt_date(R.startUTC, end), fmt_clock(end), (end - R.t0Min) / 1440)),
                     ("Missing samples", "{:,} ({:.2f}%)".format(R.nMissing, 100 * R.nMissing / len(R.counts)))]
            if R.light is not None:
                rows.append(("Light values", "%g – %g" % (np.nanmin(R.light), np.nanmax(R.light))))
            for w in R.warnings:
                rows.append(("Note", w))
            g = ttk.Frame(fr)
            g.pack(fill="x")
            for i, (k, v) in enumerate(rows):
                ttk.Label(g, text=k, foreground="#586074").grid(row=i, column=0, sticky="w", padx=(0, 12))
                ttk.Label(g, text=v, font=("TkFixedFont", 9)).grid(row=i, column=1, sticky="w")
            c = ttk.Frame(fr)
            c.pack(fill="x", pady=8)
            if R.raw is not None:
                ttk.Label(c, text="Text encoding").pack(side="left")
                names = [n for _, n in TEXT_ENCODINGS]
                var = tk.StringVar(value=R.encoding)
                cb = ttk.Combobox(c, textvariable=var, values=names, state="readonly", width=14)
                cb.pack(side="left", padx=4)
                ttk.Label(c, text="(detected)" if R.encodingAuto else "", foreground="#586074").pack(side="left")
                cb.bind("<<ComboboxSelected>>", lambda e: self.reencode(var.get()))
                ttk.Label(c, text="   change if the recording name or comments look garbled", foreground="#586074").pack(side="left")
            c2 = ttk.Frame(fr)
            c2.pack(fill="x")
            ttk.Label(c2, text="Override sample interval (min)").pack(side="left")
            iv = tk.StringVar(value="%g" % R.intervalMin)
            e = ttk.Entry(c2, textvariable=iv, width=8)
            e.pack(side="left", padx=4)

            def set_iv(ev=None):
                try:
                    v = float(iv.get())
                except ValueError:
                    return
                if v > 0 and v != R.intervalMin:
                    R.intervalMin = v
                    F.cache = {}
                    self.refresh()
            e.bind("<Return>", set_iv)

        def reencode(self, name):
            F = self.cur()
            R = F.rec
            try:
                text, enc = decode_text(R.raw, name)
                rec = parse_any(text, R.fileName)
                if len(rec.counts) != len(R.counts) or rec.t0Min != R.t0Min:
                    raise ValueError("the data changed; check the encoding")
                rec.encoding, rec.encodingAuto, rec.raw, rec.intervalMin = enc, False, R.raw, R.intervalMin
                F.rec, F.cache = rec, {}
                self.msg("Read as %s: %s" % (enc, rec.name))
            except Exception as e:          # noqa: BLE001 - shown to the user
                self.msg("Could not read the file as %s: %s" % (name, e))
            self.refresh()

    app = App()
    app.mainloop()


# =============================================================================
# Command line (batch mode) and entry point
# =============================================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="actogram_app.py",
        description="Plot and analyse activity rhythms and lighting records from timestamped activity tables (CSV or TXT). "
                    "Without --batch the desktop app opens (with FILEs loaded, if given).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("files", nargs="*", metavar="FILE", help="data files (CSV / TXT / AWD)")
    p.add_argument("--batch", action="store_true", help="analyse without a window and write figures and CSV files to --out")
    p.add_argument("--demo", action="append", choices=list(DEMOS), help="add a simulated demo recording (repeatable)")
    p.add_argument("--out", default="actogram_output", help="output folder for --batch")
    p.add_argument("--formats", default="png", help="figure formats, comma-separated: png, svg, pdf, eps")
    p.add_argument("--figures", default="actogram,periodogram,wavelet,profile,phase,daily,light",
                   help="figures to write (comma-separated; 'none' for tables only)")
    p.add_argument("--encoding", choices=[n for _, n in TEXT_ENCODINGS], help="text encoding of the files (default: detect)")
    g = p.add_argument_group("actogram")
    g.add_argument("--bin", type=int, default=1, help="bin size as a multiple of the sampling interval")
    g.add_argument("--plot", choices=["single", "double", "triple"], default="double")
    g.add_argument("--period", type=float, default=24.0, help="row period (h)")
    g.add_argument("--offset", type=float, default=0.0, help="row start (h)")
    g.add_argument("--style", choices=["bars", "heat", "viridis", "magma", "cividis"], default="bars")
    g.add_argument("--gain", type=float, default=1.0, help="display gain (0.25 to 4)")
    g.add_argument("--floor", type=float, default=0.0, help="hide counts below this value")
    g.add_argument("--ld-colours", choices=["colour", "grey"], default="colour", help="light/dark shading: yellow / grey-blue or white / light grey")
    g.add_argument("--no-shade", action="store_true", help="do not shade light and dark")
    g.add_argument("--offsets", action="store_true", help="show offsets")
    g.add_argument("--right-panel", choices=["none", "bars", "line", "numbers"], default="none")
    g.add_argument("--ld-line", choices=["mean", "regression"], default="mean")
    g.add_argument("--width", type=int, help="actogram export width in pixels (720 to 2880; default 900)")
    g.add_argument("--row-height", type=int, default=14)
    g = p.add_argument_group("lighting")
    g.add_argument("--light", choices=["recorded", "schedule", "none"], help="lighting source (default: recorded if the file has a light column)")
    g.add_argument("--threshold", type=float, default=10.0, help="lights-on threshold")
    g.add_argument("--schedule", action="append", metavar="COND",
                   help="schedule condition, repeatable, e.g. 'LD@2026-03-01T07:00,L=12:00,D=12:00,first=L' or 'DD@2026-03-15T19:00'; implies --light schedule")
    g = p.add_argument_group("analysis")
    g.add_argument("--rows", metavar="A-B", help="analysis range in actogram rows, e.g. 3-14 (default: all)")
    g.add_argument("--method", choices=["chi", "ls"], default="chi", help="periodogram")
    g.add_argument("--pmin", type=float, default=18.0)
    g.add_argument("--pmax", type=float, default=30.0)
    g.add_argument("--alpha", type=float, choices=[0.05, 0.01, 0.001], default=0.01)
    g.add_argument("--fold", choices=["row", "peak", "custom"], default="row")
    g.add_argument("--fold-period", type=float, default=24.0)
    g.add_argument("--template", type=float, default=6.0, help="onset template (h)")
    g.add_argument("--search", type=float, default=4.0, help="onset search window (± h)")
    g.add_argument("--min-contrast", type=float, default=20.0, help="minimum onset contrast (%%)")
    g.add_argument("--phase-ref", choices=["ld", "start", "rec", "row0"], default="ld", help="0-degree reference under DD/LL")
    g.add_argument("--single-fit", action="store_true", help="one fit over all onsets instead of one per lighting condition")
    return p


def parse_schedule_arg(text, rec):
    m = re.match(r"^\s*(LD|LL|DD)\s*@\s*(\d{4}-\d{1,2}-\d{1,2})[T ](\d{1,2}:\d{2})\s*(.*)$", text, re.I)
    if not m:
        raise ValueError("cannot read schedule condition %r" % text)
    mode = m.group(1).upper()
    dt = parse_date(m.group(2))
    tm = parse_time(m.group(3))
    if dt is None or tm is None:
        raise ValueError("cannot read the date or time in %r" % text)
    t = (utc_ms(*dt) + tm * 60000 - rec.startUTC) / 60000
    c = {"mode": mode, "start": int(round(t)), "first": "L", "L": 720, "D": 720}
    for part in filter(None, (s.strip() for s in m.group(4).split(","))):
        k, _, v = part.partition("=")
        k = k.strip().upper()
        if k in ("L", "D"):
            mm = parse_hm(v.strip())
            if mm is None:
                raise ValueError("cannot read %s=%s (use h:mm)" % (k, v))
            c[k] = mm
        elif k == "FIRST":
            c["first"] = "D" if v.strip().upper().startswith("D") else "L"
    return c


def apply_args(S: State, args):
    d, a = S.disp, S.ana
    d.update({"mult": {"single": 1, "double": 2, "triple": 3}[args.plot], "T": min(48, max(1, args.period)), "offsetH": args.offset % 24,
              "style": args.style, "gainLog": math.log2(min(4, max(0.25, args.gain))), "floor": max(0, args.floor), "ldCol": args.ld_colours,
              "shade": not args.no_shade, "showOff": args.offsets, "rightPanel": args.right_panel, "ldLine": args.ld_line,
              "rowH": int(min(40, max(4, args.row_height)))})
    if args.width:
        d["widthMode"], d["widthPx"] = "custom", int(min(2880, max(720, args.width)))
    a.update({"method": args.method, "pmin": args.pmin, "pmax": args.pmax, "alpha": args.alpha, "fold": args.fold, "foldCustom": args.fold_period,
              "templateH": args.template, "searchH": args.search, "minContrast": args.min_contrast, "phaseRef": args.phase_ref})
    for F in S.files:
        F.binMult = args.bin
        F.luxThr = args.threshold
        if args.schedule:
            F.schedule = normalize_schedule([parse_schedule_arg(s, F.rec) for s in args.schedule])
            F.lightSrc = "schedule"
        if args.light:
            F.lightSrc = args.light
        if args.rows:
            m = re.match(r"^\s*(\d+)\s*[-–:]\s*(\d+)\s*$", args.rows)
            if not m:
                raise ValueError("--rows: use A-B, e.g. 3-14")
            F.d1, F.d2 = int(m.group(1)), int(m.group(2))
        if args.single_fit:
            F.condFits = False


def run_batch(S: State, args):
    os.makedirs(args.out, exist_ok=True)
    fmts = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    bad = [f for f in fmts if f not in ("png", "svg", "pdf", "eps")]
    if bad:
        raise SystemExit("Unknown figure format: " + ", ".join(bad))
    figs = [f.strip() for f in args.figures.split(",") if f.strip() and f.strip() != "none"]
    written = []

    def save(fig, stem):
        for fmt in fmts:
            path = os.path.join(args.out, stem + "." + fmt)
            fig.savefig(path, format=fmt, dpi=200 if fmt == "png" else 100, facecolor="white")
            written.append(path)

    def save_csv(rows, stem):
        path = os.path.join(args.out, stem + ".csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write(to_csv(rows))
        written.append(path)

    used = set()
    for F in S.files:
        base = safe_name(F.rec.name)
        stem, i = base, 2
        while stem in used:
            stem, i = "%s_%d" % (base, i), i + 1
        used.add(stem)
        D = derive(S, F)
        O = onsets(D)
        if "actogram" in figs:
            save(actogram_figure(D, O), stem + "_actogram")
        for name, h, fn in [("periodogram", 5, lambda f: draw_periodogram(f, D, O)), ("profile", 5, lambda f: draw_profile(f, D, O)),
                            ("phase", 4, lambda f: draw_phase(f, D, O)), ("daily", 3, lambda f: draw_daily(f, D))]:
            if name in figs:
                fig = Figure(figsize=(10, h), dpi=100)
                fn(fig)
                if name == "daily":
                    fig.tight_layout()
                save(fig, stem + "_" + name)
        W = wavelet_data(D)
        if "wavelet" in figs and W is not None:
            fig = Figure(figsize=(10, 5), dpi=100)
            draw_wavelet(fig, D, O, W)
            save(fig, stem + "_wavelet")
        if "light" in figs and F.rec.light is not None:
            fig = Figure(figsize=(10, 3), dpi=100)
            draw_light(fig, D)
            fig.tight_layout()
            save(fig, stem + "_lighting")
        save_csv(onsets_table(D, O), stem + "_onsets")
        save_csv(fits_table(D, O), stem + "_fits")
        save_csv(phase_table(D, O)[0], stem + "_phase")
        save_csv(daily_table(D), stem + "_daily")
        save_csv(periodogram_table(D, O), stem + "_periodogram")
        save_csv(profile_table(D, O), stem + "_profile")
        if W is not None:
            save_csv(wavelet_table(D, O, W), stem + "_wavelet")
        if F.rec.light is not None:
            save_csv(light_table(F), stem + "_lighting")
        pg = periodogram(D)
        fits = "; ".join("%s τ %.3f h (n %d)" % (f["name"] if f["mode"] in ("none", "?") else f["name"] + " " + f["mode"], f["fit"]["slope"], len(f["members"]))
                         for f in O["fits"] if f["fit"])
        print("%s: rows %d–%d · %s peak %s · %s" % (F.rec.name, D.k1 + 1, D.k2 + 1, "χ²" if args.method == "chi" else "Lomb–Scargle",
                                                  "%.2f h" % pg["peak"]["periodH"] if pg["peak"] else "–", fits or "no fit"))
    save_csv(batch_table(S), "all_files")
    print("Wrote %d files to %s" % (len(written), os.path.abspath(args.out)))


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.batch:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            raise SystemExit("Tkinter is not available in this Python. Install it (e.g. the python3-tk package) or use --batch.")
        run_gui(args.files)
        return
    S = State()
    for key in args.demo or []:
        S.add(load_demo(key), True)
    for p in args.files:
        try:
            S.add(load_file(p, args.encoding))
        except Exception as e:      # noqa: BLE001 - reported to the user
            print("%s: %s" % (p, e), file=sys.stderr)
    if not S.files:
        raise SystemExit("No recordings: give data files or --demo (see --help).")
    apply_args(S, args)
    run_batch(S, args)


if __name__ == "__main__":
    main()
