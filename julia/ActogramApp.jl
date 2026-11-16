# Actogram web app (Julia desktop edition) | Copyright (c) 2026 The Actogram Web Application Development Team
# SPDX-License-Identifier: MIT
#
# ActogramApp.jl: plotting and analysing activity rhythms and lighting records (GLMakie desktop app).
#
# A Julia desktop version of the Actogram web app with the same input formats and core analyses:
#   * timestamped tables (CSV / TXT: timestamp, activity[, light]) with "#" comments and
#     "# Recording name: foo"; text-based AWD files; UTF-8, UTF-16 and legacy Chinese /
#     Japanese / Korean encodings (GB18030, Big5, Shift_JIS, EUC-JP, EUC-KR);
#   * single / double / triple actograms (bars or heat maps), light/dark shading in two colour
#     schemes, onsets, offsets, regression lines and row totals;
#   * lighting from the light column or a schedule of LD / LL / DD conditions with suggested
#     starts at the ends of L or D of the preceding LD;
#   * analysis range, onset detection (step template with tracking), Place onset, Select for fit
#     and Draw line; regression fits per lighting condition, tau, ZT / CT phase, circular statistics;
#   * chi-square and Lomb-Scargle periodograms, Morlet wavelet, activity profiles, daily activity,
#     lighting record and IS / IV / RA;
#   * figures as PNG, SVG, PDF or EPS (CairoMakie) and tables as CSV.
#
# Usage (from the julia/ folder):
#   julia --project=. -e 'using Pkg; Pkg.instantiate()'          # once
#   julia --project=. ActogramApp.jl                              # open the desktop app (GLMakie)
#   julia --project=. ActogramApp.jl FILE [FILE ...]              # open the app with these files
#   julia --project=. ActogramApp.jl --batch FILE ... --out DIR   # figures and CSV files, no window
#   julia --project=. ActogramApp.jl --help
#
# Everything runs locally; no data are sent anywhere. Methods follow the web app (see README):
# Sokolove & Bushell (1978), Lomb (1976) / Scargle (1982), Torrence & Compo (1998),
# Van Someren et al. (1999), Zar (2010).

module ActogramApp

using Dates, Printf, Statistics, Unicode
using FFTW
using StringEncodings
using Makie
import CairoMakie

export main, run_gui, run_batch, load_file, load_demo

const APP_NAME = "Actogram app (Julia)"
const APP_VERSION = "1.0 (November 2026)"
const BIN_MAX_MIN = 120
const MAX_FITS = 6
const MONTHS = Dict("jan" => 1, "feb" => 2, "mar" => 3, "apr" => 4, "may" => 5, "jun" => 6, "jul" => 7, "aug" => 8,
                    "sep" => 9, "sept" => 9, "oct" => 10, "nov" => 11, "dec" => 12)
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
const EPOCH = DateTime(1970, 1, 1)

modpos(a, m) = mod(a, m)                       # Julia's mod is already non-negative for m > 0
jround(v) = floor(v + 0.5)                     # JavaScript Math.round (halves up)
iround(v) = Int(floor(v + 0.5))
nanmean(v) = (w = filter(!isnan, v); isempty(w) ? NaN : mean(w))
nansum(v) = sum(x -> isnan(x) ? 0.0 : x, v; init = 0.0)
f2(v, d = 2) = (v === nothing || (v isa AbstractFloat && !isfinite(v))) ? "–" : Printf.format(Printf.Format("%.$(d)f"), v)
splitlines(s) = split(s, r"\r\n|\r|\n")
stripbom(s) = startswith(s, '﻿') ? s[nextind(s, 1):end] : s

# =============================================================================
# Parsing
# =============================================================================

mutable struct Recording
    fileName::String
    name::String
    format::String
    header::Dict{String,String}
    day0::Date                      # first calendar day; "absolute minutes" count from its 00:00
    t0Min::Float64                  # absolute minute of the first sample
    intervalMin::Float64
    counts::Vector{Float64}         # NaN = missing
    light::Union{Nothing,Vector{Float64}}
    nMissing::Int
    warnings::Vector{String}
    encoding::String
    encodingAuto::Bool
    raw::Union{Nothing,Vector{UInt8}}
end

function parse_date(s)
    s = strip(String(s))
    m = match(r"^(\d{1,2})[-\s/.]([A-Za-z]{3,4})[A-Za-z]*[-\s/.](\d{2,4})$", s)
    if m !== nothing
        mon = get(MONTHS, lowercase(m[2]), nothing)
        mon === nothing && return nothing
        y = parse(Int, m[3])
        y < 100 && (y += y < 70 ? 2000 : 1900)
        return Date(y, mon, parse(Int, m[1]))
    end
    m = match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    m !== nothing && return Date(parse(Int, m[1]), parse(Int, m[2]), parse(Int, m[3]))
    m = match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)          # US month/day/year
    m !== nothing && return Date(parse(Int, m[3]), parse(Int, m[1]), parse(Int, m[2]))
    return nothing
end

function parse_time(s)
    m = match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", strip(String(s)))
    m === nothing && return nothing
    return parse(Int, m[1]) * 60 + parse(Int, m[2]) + (m[3] === nothing ? 0.0 : parse(Int, m[3]) / 60)
end

minutes_since_epoch(dt::DateTime) = Dates.value(dt - EPOCH) / 60000
tryfloat(s) = (v = tryparse(Float64, strip(s)); v === nothing ? NaN : v)

function parse_awd(text::AbstractString, file_name::AbstractString)
    lines = splitlines(stripbom(text))
    warnings = String[]
    length(lines) < 8 && error("File is too short to be an AWD file (needs 7 header lines plus data).")
    header = [String(strip(l)) for l in lines[1:7]]
    d = parse_date(header[2])
    d === nothing && error("Line 2 should be a start date like \"21-nov-2000\"; found \"$(header[2])\".")
    t0 = parse_time(header[3])
    t0 === nothing && error("Line 3 should be a start time like \"04:20\"; found \"$(header[3])\".")
    code = header[4]
    v = tryparse(Float64, code)
    interval = (v !== nothing && v > 0) ? v / 4 : (push!(warnings, "Interval code \"$code\" is not numeric; assuming 1-minute bins."); 1.0)
    counts = Float64[]; light = Float64[]
    has_light = false; nmiss = 0; nbad = 0
    for raw in lines[8:end]
        raw = strip(raw)
        isempty(raw) && continue
        tok = filter(!isempty, split(raw, r"[\s,;]+"))
        a = tryparse(Float64, tok[1])
        a === nothing && (nbad += 1; a = -1.0)
        l = NaN
        for t in tok[2:end]
            lowercase(t) == "m" && continue
            x = tryparse(Float64, t)
            if x !== nothing && isnan(l)
                l = x; has_light = true
            end
        end
        a < 0 && (nmiss += 1; a = NaN)
        push!(counts, a); push!(light, l)
    end
    nbad > 0 && push!(warnings, "$nbad data line(s) could not be read and were treated as missing.")
    isempty(counts) && error("No data lines found after the 7-line header.")
    name = isempty(header[1]) ? replace(file_name, r"\.[^.]+$" => "") : header[1]
    hdr = Dict("name" => header[1], "date" => header[2], "time" => header[3], "interval" => code,
               "age" => header[5], "id" => header[6], "sex" => header[7])
    return Recording(isempty(file_name) ? name : file_name, name, "Text-based AWD", hdr, d, t0, interval, counts,
                  has_light ? light : nothing, nmiss, warnings, "UTF-8", true, nothing)
end

function parse_csv(text::AbstractString, file_name::AbstractString)
    rows = Tuple{Float64,Float64,Float64}[]
    rec_name = nothing
    for raw in splitlines(stripbom(text))
        line = strip(raw)
        isempty(line) && continue
        if startswith(line, "#")
            m = match(r"^#\s*Recording name\s*:\s*(.*\S)\s*$"i, line)
            (m !== nothing && rec_name === nothing) && (rec_name = String(m[1]))
            continue
        end
        tok = [replace(strip(t), r"^\"|\"$" => "") for t in split(raw, r"[,;\t]")]
        length(tok) < 2 && continue
        m = match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", tok[1])
        m === nothing && continue
        dt = try
            DateTime(parse(Int, m[1]), parse(Int, m[2]), parse(Int, m[3]), parse(Int, m[4]), parse(Int, m[5]),
                     m[6] === nothing ? 0 : parse(Int, m[6]))
        catch
            continue
        end
        a = tryfloat(tok[2])
        l = length(tok) > 2 && !isempty(tok[3]) ? tryfloat(tok[3]) : NaN
        push!(rows, (minutes_since_epoch(dt), (isfinite(a) && a >= 0) ? a : NaN, l))
    end
    length(rows) < 3 && error("No rows of the form timestamp,activity[,light] were found.")
    sort!(rows; by = first)
    diffs = sort([rows[i][1] - rows[i-1][1] for i in 2:min(length(rows), 2000)])
    dt = diffs[length(diffs) ÷ 2 + 1]
    dt > 0 || error("Could not determine the sampling interval.")
    t0 = rows[1][1]
    n = iround((rows[end][1] - t0) / dt) + 1
    counts = fill(NaN, n); light = fill(NaN, n)
    has_light = false
    for (t, a, l) in rows
        i = iround((t - t0) / dt) + 1
        counts[i] = a; light[i] = l
        isnan(l) || (has_light = true)
    end
    first_dt = EPOCH + Millisecond(round(Int, t0 * 60000))
    day0 = Date(first_dt)
    t0min = t0 - minutes_since_epoch(DateTime(day0))
    name = rec_name === nothing ? replace(file_name, r"\.[^.]+$" => "") : rec_name
    hdr = Dict("name" => file_name, "date" => "", "time" => "", "interval" => "", "age" => "", "id" => "", "sex" => "")
    return Recording(file_name, name, "CSV", hdr, day0, t0min, dt, counts, has_light ? light : nothing,
                  count(isnan, counts), String[], "UTF-8", true, nothing)
end

function parse_any(text::AbstractString, file_name::AbstractString)
    head = first(stripbom(text), 20000)
    ls = [strip(l) for l in splitlines(head)]
    ls = filter(l -> !isempty(l) && !startswith(l, "#"), ls)
    ls = ls[1:min(2, length(ls))]
    any(l -> occursin(r"^\"?\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d", l), ls) && return parse_csv(text, file_name)
    return parse_awd(text, file_name)
end

# --- Text encodings ------------------------------------------------------------
# (iconv name, display name)
const TEXT_ENCODINGS = [("UTF-8", "UTF-8"), ("UTF-16LE", "UTF-16LE"), ("UTF-16BE", "UTF-16BE"), ("GB18030", "GB18030"),
                        ("BIG5", "Big5"), ("CP932", "Shift_JIS"), ("EUC-JP", "EUC-JP"), ("CP949", "EUC-KR"),
                        ("CP1252", "windows-1252")]
const COMMON_HANGUL = Set(collect("가각간갈감갑강개객거건걸검것게겠겨격견결경계고곡곤골공과관광교구국군굴궁권귀규균그극근글금급기긴길김까깨께꼬꾸꿈끄끝끼나낙난날남납낭내냉너널넘넣네녀년념녕노녹논놀농높뇌누눈뉴느는늘능니님다단달담답당대댁더던덕데도독돈돌동돼되된될두둘뒤드득든들등디따때떤떻또뚜뜻라락란람랍랑래랜램략량러럼렇레렌려력련렬렵령례로록론롭료루류륙률르른를름릅릇리린릴림립링마막만많말맞매맥머먹먼멀메며면멸명몇모목몰몸못무묵문물므미민밀바박반받발밝밤방배백버번벌범법벽변별병보복본볼봉부북분불붙비빈빌빛빠뻐뿐사삭산살삼상새색생서석선설섬성세셈셔소속손솔송쇄쇠수숙순술숨쉬스슬습승시식신실심십싸쌍써쓰씨아악안알암압앙앞애액야약양어억언얼엄업없었에엔여역연열염엽영예오옥온올옷와완왕외요욕용우욱운울움웃원월위유육윤율으은을음읍응의이익인일임입있자작잔잘잠잡장재쟁저적전절점접정제져조족존졸종좌죄주죽준줄중쥐즈즉증지직진질짐집짓징짜째쪽찌차착찬찰참창채책처척천철첩청체쳐초촉촌총최추축춘출충취측층치칙친칠침카커케코쿠크큰키타탁탄탈탐탑태택터테토통퇴투특티파판팔패퍼페편평폐포폭표푸품풍프피필하학한할함합항해핵했행향허헌험혀혁현혈협형혜호혹혼홍화확환활황회획효후훈휘휴흐흑흔흘흥희흰히힘"))

function try_decode(data::Vector{UInt8}, enc::String)
    try
        return enc == "UTF-8" ? (isvalid(String, data) ? String(copy(data)) : nothing) : decode(data, enc)
    catch
        return nothing
    end
end

"Score a decoding: kana, frequent Hangul and CJK ideographs count for it; replacement, private-use, control and rare Hangul characters against."
function text_score(s::String, enc::String)
    sc = 0.0
    for c in s
        u = UInt32(c)
        if c == '�' || (0xe000 <= u <= 0xf8ff) || (u < 0x20 && !(c in ('\t', '\n', '\r')))
            sc -= 10
        elseif 0x3040 <= u <= 0x30ff
            sc += 2
        elseif 0xac00 <= u <= 0xd7a3
            sc += c in COMMON_HANGUL ? 2 : -3
        elseif 0x4e00 <= u <= 0x9fff
            sc += 1
        elseif 0x3400 <= u <= 0x4dbf || 0x20000 <= u
            sc -= 2
        elseif 0xff61 <= u <= 0xff9f                      # half-width katakana: unusual in names
            sc -= 1
        end
    end
    return sc
end

function decode_text(data::Vector{UInt8}, label::Union{Nothing,String} = nothing)
    if label !== nothing
        enc = first(e for (e, n) in TEXT_ENCODINGS if n == label || e == label)
        s = try_decode(data, enc)
        s === nothing && error("The file cannot be read as $label.")
        return stripbom(s), label
    end
    length(data) >= 3 && data[1:3] == [0xef, 0xbb, 0xbf] && return String(data[4:end]), "UTF-8"
    length(data) >= 2 && data[1:2] == [0xff, 0xfe] && return decode(data[3:end], "UTF-16LE"), "UTF-16LE"
    length(data) >= 2 && data[1:2] == [0xfe, 0xff] && return decode(data[3:end], "UTF-16BE"), "UTF-16BE"
    isvalid(String, data) && return String(copy(data)), "UTF-8"
    best = nothing; best_sc = -Inf
    for (enc, name) in TEXT_ENCODINGS[4:end]
        s = try_decode(data, enc)
        s === nothing && continue
        sc = text_score(s, enc)
        if sc > best_sc
            best, best_sc = (s, name), sc
        end
    end
    best === nothing && error("Unknown text encoding.")
    return best
end

function load_file(path::AbstractString; encoding = nothing)
    raw = read(path)
    text, enc = decode_text(raw, encoding)
    rec = parse_any(text, basename(path))
    rec.encoding, rec.encodingAuto, rec.raw = enc, encoding === nothing, raw
    return rec
end

# =============================================================================
# Formatting
# =============================================================================

function fmt_clock(minutes)
    m = modpos(minutes, 1440)
    h = Int(fld(m, 60)); mm = iround(m - h * 60)
    mm == 60 && return @sprintf("%02d:00", (h + 1) % 24)
    return @sprintf("%02d:%02d", h, mm)
end
abs_date(R::Recording, abs_min) = R.day0 + Day(Int(fld(abs_min, 1440)))
fmt_date(R::Recording, abs_min) = (d = abs_date(R, abs_min); @sprintf("%02d-%s-%d", day(d), MONTH_NAMES[month(d)], year(d)))
fmt_date_short(R::Recording, abs_min) = (d = abs_date(R, abs_min); @sprintf("%02d %s", day(d), MONTH_NAMES[month(d)]))
fmt_hm(minutes) = (m = iround(minutes); @sprintf("%d:%02d", fld(m, 60), mod(m, 60)))
function parse_hm(v)
    v = strip(String(v))
    m = match(r"^(\d+):(\d{1,2})$", v)
    (m !== nothing && parse(Int, m[2]) < 60) && return parse(Int, m[1]) * 60 + parse(Int, m[2])
    occursin(r"^\d+(\.\d+)?$", v) && return iround(parse(Float64, v) * 60)
    return nothing
end
"Recording name made safe for file names (letters and digits of any script kept)."
function safe_name(name)
    s = Unicode.normalize(String(name), :NFC)
    s = replace(s, r"[^\w.\-]+" => "_")
    s = replace(s, r"^[._]+|[._]+$" => "")
    s = first(s, 100)
    return isempty(s) ? "recording" : s
end
"Absolute minutes of a date-time text such as 2026-03-13 07:00 (relative to the recording's first day)."
function parse_abs(R::Recording, s)
    m = match(r"^\s*(\d{4}-\d{1,2}-\d{1,2})[T ]+(\d{1,2}:\d{2})\s*$", String(s))
    m === nothing && return nothing
    d = parse_date(m[1]); t = parse_time(m[2])
    (d === nothing || t === nothing) && return nothing
    return Dates.value(d - R.day0) * 1440 + t
end
fmt_abs(R::Recording, t) = string(Dates.format(abs_date(R, t), "yyyy-mm-dd"), " ", fmt_clock(t))

# =============================================================================
# Binning
# =============================================================================

struct Binned
    binMin::Float64
    act::Vector{Float64}            # index b + 1 holds bin b (bin 0 starts at 00:00 of day 1)
    lux::Union{Nothing,Vector{Float64}}
    n::Int
    firstBin::Int
end

function bin_record(R::Recording, bin_min)
    dt = R.intervalMin; n = length(R.counts)
    nb = Int(ceil((R.t0Min + n * dt) / bin_min - 1e-9))
    act = zeros(nb); cov = zeros(nb); ls = zeros(nb); lc = zeros(nb)
    for i in 1:n
        b = Int(floor((R.t0Min + (i - 1) * dt + 1e-9) / bin_min))
        (0 <= b < nb) || continue
        v = R.counts[i]
        if !isnan(v)
            act[b+1] += v; cov[b+1] += 1
        end
        if R.light !== nothing && !isnan(R.light[i])
            ls[b+1] += R.light[i]; lc[b+1] += 1
        end
    end
    expected = bin_min / dt
    for b in 1:nb
        if cov[b] < expected * 0.5
            act[b] = NaN
        elseif cov[b] < expected
            act[b] *= expected / cov[b]
        end
    end
    lux = R.light === nothing ? nothing : [lc[b] > 0 ? ls[b] / lc[b] : NaN for b in 1:nb]
    return Binned(bin_min, act, lux, nb, Int(floor(R.t0Min / bin_min)))
end

# =============================================================================
# Lighting
# =============================================================================
# Schedule: conditions (each lasts until the next one starts).

mutable struct Cond
    mode::String        # "LD", "LL" or "DD"
    start::Float64      # absolute minutes
    first::Char         # 'L' or 'D' (LD only)
    L::Float64          # minutes
    D::Float64
end
Base.copy(c::Cond) = Cond(c.mode, c.start, c.first, c.L, c.D)

normalize_schedule(s) = sort([copy(c) for c in s]; by = c -> c.start)
ld_lights_on(c::Cond) = c.start + (c.first == 'D' ? c.D : 0.0)

"Ends of D (kind :on, lights-on) or ends of L (kind :off, lights-off) of LD condition prev."
function ld_boundaries(prev, kind::Symbol, until; max_n = 1000)
    out = Float64[]
    (prev === nothing || prev.mode != "LD") && return out
    T = prev.L + prev.D
    (T > 0 && prev.L > 0 && prev.D > 0) || return out
    t = ld_lights_on(prev) + (kind == :off ? prev.L : 0.0)
    while t - T >= prev.start; t -= T; end
    while t < prev.start; t += T; end
    while t <= until && length(out) < max_n
        push!(out, t); t += T
    end
    return out
end

function schedule_state(sched, t; extend_first = true)
    isempty(sched) && return NaN
    seg = nothing
    for s in sched
        s.start <= t && (seg = s)
    end
    if seg === nothing
        extend_first || return NaN
        seg = sched[1]
    end
    seg.mode == "DD" && return 0.0
    seg.mode == "LL" && return 1.0
    T = seg.L + seg.D
    T > 0 || return NaN
    return modpos(t - ld_lights_on(seg), T) < seg.L ? 1.0 : 0.0
end

function light_states(R::Recording, B::Binned, source, threshold, sched, extend_first)
    if source == "recorded" && B.lux !== nothing
        return [isnan(v) ? NaN : (v >= threshold ? 1.0 : 0.0) for v in B.lux]
    elseif source == "schedule"
        s = normalize_schedule(sched)
        return [schedule_state(s, (b + 0.5) * B.binMin; extend_first = extend_first) for b in 0:B.n-1]
    end
    return fill(NaN, B.n)
end

"Classify each calendar day as LD / LL / DD from the light column and merge consistent days."
function detect_lighting(R::Recording, threshold)
    R.light === nothing && return (days = Dict{Symbol,Any}[], segments = Dict{Symbol,Any}[])
    dt = R.intervalMin; n = length(R.counts)
    ndays = Int(ceil((R.t0Min + n * dt) / 1440))
    days = [Dict{Symbol,Any}(:day => d, :n => 0, :nLight => 0, :sumLux => 0.0, :on => Float64[], :off => Float64[]) for d in 1:ndays]
    prev = nothing
    for i in 1:n
        t = R.t0Min + (i - 1) * dt
        d = Int(fld(t, 1440))
        l = R.light[i]
        if isnan(l)
            prev = nothing; continue
        end
        s = l >= threshold ? 1 : 0
        row = days[d+1]
        row[:n] += 1
        if s == 1
            row[:nLight] += 1; row[:sumLux] += l
        end
        (prev !== nothing && s != prev) && push!(s == 1 ? row[:on] : row[:off], t - d * 1440)
        prev = s
    end
    for r in days
        r[:frac] = r[:n] > 0 ? r[:nLight] / r[:n] : NaN
        r[:meanLux] = r[:nLight] > 0 ? r[:sumLux] / r[:nLight] : 0.0
        r[:mode] = r[:n] == 0 ? "?" : (isempty(r[:on]) && isempty(r[:off])) ? (r[:frac] > 0.5 ? "LL" : "DD") : "LD"
        r[:onMin] = isempty(r[:on]) ? nothing : r[:on][1]
        r[:offMin] = isempty(r[:off]) ? nothing : r[:off][end]
        r[:lightHours] = r[:n] > 0 ? r[:frac] * 24 : NaN
    end
    close_(a, b) = (a === nothing || b === nothing) ? true : (d = mod(abs(a - b), 1440); min(d, 1440 - d) <= 20)
    segs = Dict{Symbol,Any}[]
    for r in days
        r[:mode] == "?" && continue
        last = isempty(segs) ? nothing : segs[end]
        if last !== nothing && last[:mode] == r[:mode] && (r[:mode] != "LD" || (close_(last[:onMin], r[:onMin]) && close_(last[:offMin], r[:offMin])))
            last[:endDay] = r[:day]
            last[:onMin] === nothing && (last[:onMin] = r[:onMin])
            last[:offMin] === nothing && (last[:offMin] = r[:offMin])
        else
            push!(segs, Dict{Symbol,Any}(:mode => r[:mode], :startDay => r[:day], :endDay => r[:day], :onMin => r[:onMin], :offMin => r[:offMin]))
        end
    end
    i = 1
    while i < length(segs)
        if segs[i][:startDay] == segs[i][:endDay] && length(segs) > 1 && i > 1
            segs[i+1][:startDay] = segs[i][:startDay]
            deleteat!(segs, i); i -= 1
        end
        i += 1
    end
    for s in segs
        if s[:mode] == "LD" && s[:onMin] !== nothing && s[:offMin] !== nothing
            s[:L] = modpos(s[:offMin] - s[:onMin], 1440) / 60
            s[:D] = 24 - s[:L]
        end
    end
    return (days = days, segments = segs)
end

"Detected segments -> editable schedule; LL/DD starts refined to the actual transition."
function segments_to_schedule(segs, R::Union{Nothing,Recording} = nothing, threshold = 10.0)
    out = Cond[]
    for s in segs
        day0 = (s[:startDay] - 1) * 1440.0
        if s[:mode] == "LD"
            L = haskey(s, :L) ? iround(s[:L] * 60) : 720
            D = haskey(s, :D) ? iround(s[:D] * 60) : 1440 - L
            push!(out, Cond("LD", day0 + (s[:onMin] === nothing ? 420 : iround(s[:onMin])), 'L', L, D))
        else
            push!(out, Cond(s[:mode], day0, 'L', 720, 720))
        end
    end
    if R !== nothing && R.light !== nothing
        dt = R.intervalMin; n = length(R.counts)
        state(i) = (l = R.light[i+1]; isnan(l) ? -1 : (l >= threshold ? 1 : 0))
        for k in 2:length(out)
            c = out[k]
            c.mode == "LD" && continue
            want = c.mode == "LL" ? 1 : 0
            lo = max(0, Int(fld(out[k-1].start - R.t0Min, dt)))
            i = min(n - 1, Int(fld(c.start - R.t0Min, dt)))
            while i > lo && state(i) != 1 - want
                i -= 1
            end
            state(i) == 1 - want && (c.start = R.t0Min + (i + 1) * dt)
        end
    end
    return out
end

# =============================================================================
# Statistics
# =============================================================================

function norm_inv(p)
    a = (-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239)
    b = (-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572)
    c = (-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783)
    d = (0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416)
    pl = 0.02425
    if p < pl
        q = sqrt(-2log(p))
        return (((((c[1]q + c[2])q + c[3])q + c[4])q + c[5])q + c[6]) / ((((d[1]q + d[2])q + d[3])q + d[4])q + 1)
    elseif p <= 1 - pl
        q = p - 0.5; r = q * q
        return (((((a[1]r + a[2])r + a[3])r + a[4])r + a[5])r + a[6]) * q / (((((b[1]r + b[2])r + b[3])r + b[4])r + b[5])r + 1)
    end
    q = sqrt(-2log(1 - p))
    return -(((((c[1]q + c[2])q + c[3])q + c[4])q + c[5])q + c[6]) / ((((d[1]q + d[2])q + d[3])q + d[4])q + 1)
end

chi2_inv(p, df) = (z = norm_inv(p); h = 2 / (9df); df * (1 - h + z * sqrt(h))^3)

function linfit(xs, ys)
    x = Float64.(xs); y = Float64.(ys); n = length(x)
    n < 2 && return nothing
    mx, my = mean(x), mean(y)
    sxx = sum((x .- mx) .^ 2)
    sxx == 0 && return nothing
    sxy = sum((x .- mx) .* (y .- my)); syy = sum((y .- my) .^ 2)
    b = sxy / sxx; a = my - b * mx
    sse = sum((y .- (a .+ b .* x)) .^ 2)
    return (slope = b, intercept = a, se = n > 2 ? sqrt(sse / (n - 2) / sxx) : NaN, r2 = syy > 0 ? 1 - sse / syy : 1.0, n = n)
end

function circular_stats(degs)
    n = length(degs)
    n == 0 && return nothing
    sx = sum(cosd, degs); sy = sum(sind, degs)
    R = hypot(sx, sy) / n
    mn = modpos(rad2deg(atan(sy, sx)), 360)
    csd = R > 0 ? rad2deg(sqrt(-2log(R))) : Inf
    Rn = n * R
    p = n >= 2 ? min(1.0, exp(sqrt(1 + 4n + 4(n^2 - Rn^2)) - (1 + 2n))) : NaN
    return (n = n, R = R, mean = mn, csdDeg = csd, Z = n * R^2, p = p)
end
wrap180(d) = modpos(d + 180, 360) - 180
fmt_p(p) = (p === nothing || !isfinite(p)) ? "–" : p < 0.001 ? "< 0.001" : @sprintf("%.3f", p)
quantile_nan(v, q) = (w = filter(!isnan, v); isempty(w) ? NaN : quantile(w, q))

# =============================================================================
# Periodograms and wavelet
# =============================================================================

"Sokolove-Bushell chi-square periodogram with missing data; critical value chi2(1 - alpha, P - 1)."
function chi_square_periodogram(x, bin_min, pmin_h, pmax_h, alpha)
    pts = NamedTuple[]
    ok = findall(!isnan, x)
    N = length(ok)
    N < 10 && return (points = pts, peak = nothing)
    v = x[ok]; M = mean(v); ss = sum((v .- M) .^ 2)
    ss == 0 && return (points = pts, peak = nothing)
    idx = ok .- 1
    pmin = max(2, iround(pmin_h * 60 / bin_min)); pmax = iround(pmax_h * 60 / bin_min)
    for P in pmin:pmax
        P * 2 > length(x) && break
        s = zeros(P); c = zeros(P)
        @inbounds for (j, i) in enumerate(idx)
            h = i % P + 1
            s[h] += v[j]; c[h] += 1
        end
        Qp = 0.0
        for h in 1:P
            c[h] > 0 && (Qp += c[h] * (s[h] / c[h] - M)^2)
        end
        Qp /= ss / N
        crit = chi2_inv(1 - alpha, P - 1)
        push!(pts, (periodH = P * bin_min / 60, value = Qp, crit = crit, amp = Qp - crit))
    end
    isempty(pts) && return (points = pts, peak = nothing)
    pk = pts[argmax([p.amp for p in pts])]
    return (points = pts, peak = merge(pk, (significant = pk.amp > 0,)))
end

"Lomb-Scargle periodogram normalised by the variance; false-alarm threshold for alpha."
function lomb_scargle(x, bin_min, pmin_h, pmax_h, alpha, step_h)
    pts = NamedTuple[]
    ok = findall(!isnan, x)
    N = length(ok)
    N < 10 && return (points = pts, peak = nothing)
    t = (ok .- 1) .* (bin_min / 60)
    y = x[ok] .- mean(x[ok])
    v2 = sum(y .^ 2) / (N - 1)
    v2 == 0 && return (points = pts, peak = nothing)
    M = min(N, iround((pmax_h - pmin_h) / step_h) + 1)
    crit = -log(1 - (1 - alpha)^(1 / max(1, M)))
    P = pmin_h
    while P <= pmax_h + 1e-9
        w = 2π / P
        tau = atan(sum(sin.(2w .* t)), sum(cos.(2w .* t))) / (2w)
        a = w .* (t .- tau); c = cos.(a); s = sin.(a)
        pw = (sum(y .* c)^2 / sum(c .^ 2) + sum(y .* s)^2 / sum(s .^ 2)) / (2v2)
        push!(pts, (periodH = P, value = pw, crit = crit, amp = pw - crit))
        P += step_h
    end
    pk = pts[argmax([p.value for p in pts])]
    return (points = pts, peak = merge(pk, (significant = pk.value > crit,)))
end

"Continuous Morlet wavelet (omega0 = 6) by FFT (Torrence & Compo 1998); power / variance, ridge and cone of influence."
function morlet_cwt(x, dt_h, periods; omega0 = 6.0)
    N = length(x); m = mean(x); var = mean((x .- m) .^ 2)
    var > 0 || (var = 1.0)
    M = nextpow(2, N)
    X = fft(vcat(x .- m, zeros(M - N)))
    k = 0:M-1
    omega = [2π * (kk <= M / 2 ? kk : kk - M) / (M * dt_h) for kk in k]
    ff = 4π / (omega0 + sqrt(2 + omega0^2))
    power = zeros(length(periods), N)
    for (i, P) in enumerate(periods)
        sc = P / ff
        nrm = sqrt(2π * sc / dt_h) * π^-0.25
        psi = [o > 0 ? nrm * exp(-0.5 * (sc * o - omega0)^2) : 0.0 for o in omega]
        w = ifft(X .* psi)[1:N]
        power[i, :] = abs2.(w) ./ var
    end
    coi = [ff / sqrt(2) * dt_h * min(t + 1, N - t) for t in 0:N-1]
    ridge = zeros(N)
    for t in 1:N
        b = argmax(view(power, :, t)); ridge[t] = periods[b]
        if 1 < b < length(periods)
            y0, y1, y2 = power[b-1, t], power[b, t], power[b+1, t]
            den = y0 - 2y1 + y2
            den < 0 && (ridge[t] = periods[b] + 0.5 * (y0 - y2) / den * (periods[b+1] - periods[b]))
        end
    end
    return (power = power, coi = coi, ridge = ridge, periods = collect(Float64, periods), dtH = dt_h, N = N)
end

# =============================================================================
# Onsets and offsets (step template with cycle-to-cycle tracking)
# =============================================================================

struct Prefix
    s::Vector{Float64}
    c::Vector{Float64}
end
Prefix(x) = Prefix(vcat(0.0, cumsum(map(v -> isnan(v) ? 0.0 : v, x))), vcat(0.0, cumsum(map(v -> isnan(v) ? 0.0 : 1.0, x))))
"Mean of bins a..b-1 (0-based); NaN with fewer than 50% valid bins."
function pmean(p::Prefix, a, b)
    a = max(0, a); b = min(length(p.s) - 1, b)
    b <= a && return NaN
    n = p.c[b+1] - p.c[a+1]
    n < (b - a) * 0.5 && return NaN
    return (p.s[b+1] - p.s[a+1]) / n
end

"""One onset (and offset) per row k = k_first..k_last (bins 0-based). S(t) = mean(next W h) - mean(previous W h).
manual: Dict(k => bin or nothing); guides: drawn lines (k1, k2, b1, b2, tolBins)."""
function detect_onsets(x, bin_min, row_start, Pb, k_first, k_last; template_h = 6.0, search_h = 4.0,
                       min_contrast = 20.0, manual = Dict{Int,Union{Nothing,Int}}(), guides = [], rest_h = 3.0)
    W = max(1, iround(template_h * 60 / bin_min)); H = max(1, iround(search_h * 60 / bin_min))
    ps = Prefix(x); n = length(x)
    gm = any(!isnan, x) ? nanmean(x) : 0.0
    min_s = gm * (min_contrast / 100)
    S = [pmean(ps, i, i + W) - pmean(ps, i - W, i) for i in 0:n-1]
    res = Dict{Symbol,Any}[]
    prev = nothing
    for k in k_first:k_last
        rs = row_start(k)
        g = nothing
        for gg in guides
            if gg.k1 <= k <= gg.k2 && gg.k2 > gg.k1
                g = gg; break
            end
        end
        if g !== nothing
            pred = g.b1 + (g.b2 - g.b1) * (k - g.k1) / (g.k2 - g.k1)
            lo, hi = iround(pred - g.tolBins), iround(pred + g.tolBins) + 1
        elseif prev === nothing
            lo, hi = rs, rs + Pb
        else
            lo, hi = prev + Pb - H, prev + Pb + H
        end
        lo, hi = max(lo, W), min(hi, n - W)
        best, best_s = nothing, -Inf
        for i in lo:hi-1
            v = S[i+1]
            if !isnan(v) && v > best_s
                best, best_s = i, v
            end
        end
        onset = (best !== nothing && best_s > min_s) ? best : nothing
        if onset !== nothing
            R = max(1, iround(60 / bin_min)); q = max(1, iround(30 / bin_min))
            thr = 0.4 * pmean(ps, onset, onset + W)
            for t in max(0, onset - R):onset+R
                m = pmean(ps, t, t + q)
                if t < n && !isnan(m) && m >= thr && x[t+1] >= thr
                    onset = t; break
                end
            end
        end
        is_manual = haskey(manual, k)
        is_manual && (onset = manual[k])
        offset = nothing
        if onset !== nothing
            thr = 0.15 * pmean(ps, onset, onset + W)
            rb = max(1, iround(rest_h * 60 / bin_min))
            lim = min(n - rb, onset + Pb - iround(60 / bin_min))
            for t in onset+iround(120 / bin_min):lim-1
                m = pmean(ps, t, t + rb)
                if !isnan(m) && m < thr
                    offset = t; break
                end
            end
        end
        push!(res, Dict{Symbol,Any}(:k => k, :onsetBin => onset, :offsetBin => offset, :score => best_s, :manual => is_manual,
                                    :guided => g !== nothing && !is_manual, :cyc => k, :excluded => false, :psi => nothing, :phase => nothing))
        if onset !== nothing
            prev = onset
        elseif prev !== nothing
            prev += Pb
        end
    end
    return res
end

"Average waveform: mean, SEM and n per phase bin (x starts at phase start_bin)."
function fold_profile(x, start_bin, Pb)
    s = zeros(Pb); s2 = zeros(Pb); c = zeros(Pb)
    for (i, v) in enumerate(x)
        isnan(v) && continue
        h = mod(start_bin + i - 1, Pb) + 1
        s[h] += v; s2[h] += v^2; c[h] += 1
    end
    m = [c[h] > 0 ? s[h] / c[h] : NaN for h in 1:Pb]
    sem = [c[h] > 1 ? sqrt(max(0, (s2[h] - c[h] * m[h]^2) / (c[h] - 1))) / sqrt(c[h]) : NaN for h in 1:Pb]
    return m, sem, c
end

"IS, IV and RA (M10, L5) from hourly totals (Van Someren et al. 1999)."
function non_parametric(x, bin_min, start_bin)
    per_h = iround(60 / bin_min)
    (per_h < 1 || abs(per_h * bin_min - 60) > 1e-6) && return nothing
    first_ = Int(ceil(start_bin / per_h) * per_h - start_bin)
    hours = Float64[]; hod = Float64[]
    i = first_
    while i + per_h <= length(x)
        seg = x[i+1:i+per_h]
        push!(hours, any(isnan, seg) ? NaN : sum(seg)); push!(hod, modpos((start_bin + i) / per_h, 24))
        i += per_h
    end
    valid = filter(!isnan, hours); N = length(valid)
    N < 48 && return nothing
    mn = mean(valid); tot = sum((valid .- mn) .^ 2)
    tot == 0 && return nothing
    hs = zeros(24); hc = zeros(24)
    for (v, h) in zip(hours, hod)
        isnan(v) && continue
        hh = mod(iround(h), 24) + 1
        hs[hh] += v; hc[hh] += 1
    end
    prof = [hc[h] > 0 ? hs[h] / hc[h] : mn for h in 1:24]
    IS = N * sum((prof .- mn) .^ 2) / (24 * tot)
    d = filter(!isnan, diff(hours))
    IV = isempty(d) ? NaN : N * sum(d .^ 2) / (length(d) * tot)
    M10 = maximum(sum(prof[mod(h + j, 24)+1] for j in 0:9) / 10 for h in 0:23)
    L5 = minimum(sum(prof[mod(h + j, 24)+1] for j in 0:4) / 5 for h in 0:23)
    return (IS = IS, IV = IV, M10 = M10, L5 = L5, RA = (M10 - L5) / (M10 + L5), hours = N)
end

# =============================================================================
# Simulated demo recordings (same seeded model as the web app)
# =============================================================================

mutable struct Mulberry32
    a::UInt32
end
function (r::Mulberry32)()
    r.a += 0x6D2B79F5
    t = r.a
    t = (t ⊻ (t >> 15)) * (t | 0x00000001)
    t ⊻= t + (t ⊻ (t >> 7)) * (t | 0x0000003d)
    return Float64(t ⊻ (t >> 14)) / 4294967296.0
end
function gauss(r)
    u = 0.0; v = 0.0
    while u == 0; u = r(); end
    while v == 0; v = r(); end
    return sqrt(-2log(u)) * cos(2π * v)
end

sc_lddd_light(tH) = (d = floor(tH / 24); d >= 14 ? 0 : (h = tH - d * 24; 7 <= h < 19 ? 1 : 0))
function sc_lddd_phase(prev, r)
    d = floor((prev + 24) / 24)
    if prev < 0 || d < 14
        target = (prev < 0 ? 0 : d) * 24 + 19 - 0.15
        return (onset = target + 0.12gauss(r), alpha = 11.2, gain = 1.0)
    end
    return (onset = prev + 23.62 + 0.14gauss(r), alpha = 11.6, gain = 1.0)
end
sc_ldll_light(tH) = (d = floor(tH / 24); d >= 10 ? 2 : (h = tH - d * 24; 6 <= h < 18 ? 1 : 0))
function sc_ldll_phase(prev, r)
    d = floor((prev + 24) / 24)
    if prev < 0 || d < 10
        target = (prev < 0 ? 0 : d) * 24 + 18 - 0.1
        return (onset = target + 0.12gauss(r), alpha = 11.0, gain = 1.0)
    end
    k = d - 10
    return (onset = prev + 24.85 + 0.3gauss(r), alpha = max(7, 10 - 0.12k), gain = max(0.45, 0.85 - 0.02k))
end
sc_jet_light(tH) = (d = floor(tH / 24); h = tH - d * 24; d < 10 ? (6 <= h < 18 ? 1 : 0) : (h < 12 ? 1 : 0))
function sc_jet_phase(prev, r)
    prev < 0 && return (onset = 18 - 0.2 + 0.1gauss(r), alpha = 11.0, gain = 1.0)
    d = floor((prev + 24) / 24)
    target = d * 24 + (d < 10 ? 18 : 12) - 0.2
    free = prev + 23.7
    return (onset = free + clamp(target - free, -0.6, 0.6) + 0.15gauss(r), alpha = 11.0, gain = 1.0)
end

const DEMOS = [
    "ld-dd" => (label = "Demo LD→DD", fileName = "DEMO_WT_LD-DD", seed = 11, date = Date(2026, 3, 3), startMin = 14 * 60 + 36, days = 35,
                intervalMin = 6, lux = 280, llLux = 0, gap = nothing, light = sc_lddd_light, phase = sc_lddd_phase),
    "ld-ll" => (label = "Demo LD→LL", fileName = "DEMO_LD-LL", seed = 29, date = Date(2026, 4, 13), startMin = 10 * 60 + 5, days = 30,
                intervalMin = 1, lux = 240, llLux = 120, gap = nothing, light = sc_ldll_light, phase = sc_ldll_phase),
    "jetlag" => (label = "Demo 6-h advance", fileName = "DEMO_JETLAG_ADV6", seed = 47, date = Date(2026, 9, 21), startMin = 16 * 60 + 48, days = 28,
                 intervalMin = 6, lux = 310, llLux = 0, gap = (16.4 * 24, 16.95 * 24), light = sc_jet_light, phase = sc_jet_phase),
]
demo_spec(key) = last(DEMOS[findfirst(p -> first(p) == key, DEMOS)])

"Simulated wheel-running record as CSV text (timestamp,activity,light); seeded and reproducible."
function generate_demo(key)
    s = demo_spec(key)
    r = Mulberry32(UInt32(s.seed))
    total = s.days * 1440
    act = zeros(total); lux = zeros(total)
    windows = NamedTuple[]; prev = -1.0
    while true
        w = s.phase(prev, r)
        w.onset > (total + s.startMin) / 60 + 30 && break
        push!(windows, w); prev = w.onset
        length(windows) > 400 && break
    end
    function envelope(tH)
        e = 0.02
        for w in windows
            xx = tH - w.onset
            (xx < -1 || xx > w.alpha + 1) && continue
            if 0 <= xx <= w.alpha
                u = xx / w.alpha
                v = 0.95exp(-((u - 0.18) / 0.22)^2) + 0.55exp(-((u - 0.85) / 0.12)^2) + 0.42
                e = max(e, min(1, v) * w.gain)
            end
        end
        return e
    end
    running = false
    for m in 0:total-1
        tH = (s.startMin + m) / 60
        L = s.light(tH)
        lux_now = L == 2 ? s.llLux : L == 1 ? s.lux : 0
        lux[m+1] = lux_now > 0 ? max(1, jround(lux_now + 6gauss(r))) : 0
        p = envelope(tH)
        L == 1 && (p *= 0.12)
        p_start = 0.02 + 0.22p; p_stop = 0.03 + 0.25(1 - p)
        if running
            r() < p_stop && (running = false)
        elseif r() < p_start * p * 1.6 + 0.0025
            running = true
        end
        running && (act[m+1] = max(0, jround(22 + 30p + 8gauss(r))))
    end
    if s.gap !== nothing
        for m in 0:total-1
            tH = (s.startMin + m) / 60
            s.gap[1] <= tH < s.gap[2] && (act[m+1] = -1)
        end
    end
    k = s.intervalMin
    io = IOBuffer()
    print(io, "timestamp,activity,light\r\n")
    for b in 0:(total ÷ k)-1
        blk = act[b*k+1:(b+1)*k]
        tm = DateTime(s.date) + Minute(s.startMin + b * k)
        a = any(<(0), blk) ? "" : string(Int(sum(blk)))
        print(io, Dates.format(tm, "yyyy-mm-dd HH:MM"), ",", a, ",", Int(jround(sum(lux[b*k+1:(b+1)*k]) / k)), "\r\n")
    end
    return String(take!(io))
end

load_demo(key) = parse_csv(generate_demo(key), demo_spec(key).fileName * ".csv")

# =============================================================================
# Model: recordings, settings and derived results
# =============================================================================

default_display() = Dict{Symbol,Any}(:mult => 2, :T => 24.0, :offsetH => 0.0, :rowH => 14, :widthPx => 900,
    :style => "bars", :gainLog => 0.0, :floor => 0.0, :shade => true, :ldCol => "colour",
    :showOn => true, :showOff => false, :rightPanel => "none", :ldLine => "mean")
default_analysis() = Dict{Symbol,Any}(:method => "chi", :pmin => 18.0, :pmax => 30.0, :alpha => 0.01, :fold => "row", :foldCustom => 24.0,
    :templateH => 6.0, :searchH => 4.0, :minContrast => 20.0, :guideTolH => 2.0, :phaseUnit => "ztct", :phaseRef => "ld",
    :fitFold => "auto", :fitAxis => "phase", :wmin => 18.0, :wmax => 30.0, :wData => "range")

"One loaded recording and everything chosen for it (bin size, lighting, range, edits, fits)."
mutable struct Entry
    rec::Recording
    synthetic::Bool
    binMult::Int
    fits::Vector{Dict{Symbol,Any}}       # :name, :auto, :seg, :members (Set of k), :guide
    activeFit::Int                       # 1-based
    condFits::Bool
    condKey::Any
    schedule::Vector{Cond}
    schedExtend::Bool
    lightSrc::String
    luxThr::Float64
    d1::Int
    d2::Int
    manual::Dict{Int,Union{Nothing,Float64}}   # cycle k -> absolute minutes of a hand-placed onset, or nothing (deleted)
    cache::Dict{Any,Any}
end
function Entry(rec::Recording, synthetic = false)
    has_light = rec.light !== nothing && quantile_nan(rec.light, 0.99) > 0
    det = detect_lighting(rec, 10.0)
    sched = isempty(det.segments) ? [Cond("LD", 420, 'L', 720, 720)] : segments_to_schedule(det.segments, rec, 10.0)
    Entry(rec, synthetic, 1, Dict{Symbol,Any}[], 1, true, nothing, sched, true, has_light ? "recorded" : "none", 10.0, 1, 9999,
          Dict{Int,Union{Nothing,Float64}}(), Dict{Any,Any}())
end

mutable struct Session
    files::Vector{Entry}
    cur::Int                             # 1-based; 0 = none
    disp::Dict{Symbol,Any}
    ana::Dict{Symbol,Any}
    tool::String                         # "zoom", "place", "select", "line", "range"
end
Session() = Session(Entry[], 0, default_display(), default_analysis(), "zoom")
current(S::Session) = 1 <= S.cur <= length(S.files) ? S.files[S.cur] : nothing
function add!(S::Session, rec::Recording, synthetic = false)
    push!(S.files, Entry(rec, synthetic)); S.cur = length(S.files)
    return S.files[end]
end

"Data of one recording under the current settings. Bins and rows (k) are 0-based numbers."
struct Derived
    F::Entry
    S::Session
    binMin::Float64
    B::Binned
    Pb::Int
    off::Int
    kStart::Int
    nRows::Int
    light::Vector{Float64}
    k1::Int
    k2::Int
    a::Int
    b::Int
    x::Vector{Float64}
end
rowstart(D::Derived, k) = D.off + k * D.Pb
act(D::Derived, b) = D.B.act[b+1]
lightat(D::Derived, b) = D.light[b+1]

max_bin_mult(F::Entry) = max(1, Int(floor(BIN_MAX_MIN / F.rec.intervalMin + 1e-9)))
effective_bin(F::Entry) = (F.binMult = clamp(F.binMult, 1, max_bin_mult(F)); F.rec.intervalMin * F.binMult)

function derive(S::Session, F::Entry)
    bin_min = effective_bin(F)
    if get(F.cache, :binKey, nothing) != bin_min
        empty!(F.cache); F.cache[:binKey] = bin_min; F.cache[:binned] = bin_record(F.rec, bin_min)
    end
    B = F.cache[:binned]
    Pb = max(2, iround(S.disp[:T] * 60 / bin_min))
    off = iround(S.disp[:offsetH] * 60 / bin_min)
    kStart = max(0, Int(floor((B.firstBin - off) / Pb)))
    nRows = max(1, Int(ceil((B.n - off) / Pb)))
    lk = (bin_min, F.lightSrc, F.luxThr, F.schedExtend, sched_key(F.schedule))
    if get(F.cache, :lightKey, nothing) != lk
        F.cache[:light] = light_states(F.rec, B, F.lightSrc, F.luxThr, F.schedule, F.schedExtend); F.cache[:lightKey] = lk
    end
    k1 = min(max(F.d1 - 1, kStart), nRows - 1)
    k2 = min(max(F.d2 - 1, k1), nRows - 1)
    a = max(0, off + k1 * Pb); b = min(B.n, off + (k2 + 1) * Pb)
    return Derived(F, S, bin_min, B, Pb, off, kStart, nRows, F.cache[:light], k1, k2, a, b, B.act[a+1:b])
end
sched_key(s) = [(c.mode, c.start, c.first, c.L, c.D) for c in s]

# --- Lighting conditions as segments in absolute minutes ---------------------

function cond_segments(D::Derived)
    F = D.F
    key = (F.lightSrc, F.luxThr, F.schedExtend, sched_key(F.schedule))
    get(F.cache, :segKey, nothing) == key && return F.cache[:segs]
    segs = Dict{Symbol,Any}[]
    if F.lightSrc == "recorded" && F.rec.light !== nothing
        for s in detect_lighting(F.rec, F.luxThr).segments
            push!(segs, Dict{Symbol,Any}(:mode => s[:mode], :startMin => (s[:startDay] - 1) * 1440.0, :endMin => s[:endDay] * 1440.0,
                :startDay => s[:startDay], :endDay => s[:endDay],
                :ref0 => (s[:mode] == "LD" && s[:onMin] !== nothing) ? (s[:startDay] - 1) * 1440.0 + s[:onMin] : nothing, :Tmin => 1440.0))
        end
    elseif F.lightSrc == "schedule"
        sc = normalize_schedule(F.schedule)
        for (i, s) in enumerate(sc)
            e = i < length(sc) ? sc[i+1].start : Inf
            push!(segs, Dict{Symbol,Any}(:mode => s.mode, :startMin => s.start, :endMin => e, :startDay => Int(fld(s.start, 1440)) + 1,
                :endDay => isfinite(e) ? e / 1440 : nothing, :ref0 => s.mode == "LD" ? ld_lights_on(s) : nothing, :Tmin => s.L + s.D))
        end
    end
    for s in segs
        s[:realStart] = s[:startMin]
    end
    if !isempty(segs)
        (F.lightSrc != "schedule" || F.schedExtend) && (segs[1][:startMin] = -Inf)
        segs[end][:endMin] = Inf
    end
    F.cache[:segs] = segs; F.cache[:segKey] = key
    return segs
end

seg_index_at(segs, t) = (i = findfirst(s -> s[:startMin] <= t < s[:endMin], segs); i === nothing ? 0 : i)
last_lights_on(seg, t) = seg[:ref0] + floor((t - seg[:ref0]) / seg[:Tmin]) * seg[:Tmin]
is_free_run(mode) = mode != "LD"
const PHASE_REF_LABEL = Dict("ld" => "projected ZT0 of the preceding LD", "start" => "start of the condition",
                             "rec" => "00:00 of recording day 1", "row0" => "start of actogram row 1")

"0-degree reference (absolute minutes) of a free-running condition i (1-based; 0 = none)."
function free_run_reference(D::Derived, segs, i)
    ref = D.S.ana[:phaseRef]
    ref == "row0" && return D.off * D.binMin
    i == 0 && return 0.0
    seg = segs[i]
    if ref == "ld"
        for j in i-1:-1:1
            (segs[j][:mode] == "LD" && segs[j][:ref0] !== nothing) && return last_lights_on(segs[j], seg[:realStart])
        end
    end
    ref == "rec" && return 0.0
    return (isfinite(seg[:realStart]) && seg[:realStart] > 0) ? seg[:realStart] : D.F.rec.t0Min
end

# --- Regression fits ---------------------------------------------------------

newfit(name; auto = true, seg = nothing) = Dict{Symbol,Any}(:name => name, :auto => auto, :seg => seg, :members => Set{Int}(), :guide => nothing)

function ensure_fits!(F::Entry)
    isempty(F.fits) && (F.fits = [newfit("Fit 1")]; F.activeFit = 1)
    (1 <= F.activeFit <= length(F.fits)) || (F.activeFit = 1)
end

function rebuild_condition_fits!(D::Derived, lst)
    F = D.F
    segs = cond_segments(D)
    key = (get(F.cache, :segKey, nothing), D.k1, D.k2, get(F.cache, :onKey, nothing))
    F.condKey == key && return
    F.condKey = key
    sis = Int[]
    for o in lst
        (o[:onsetBin] === nothing || o[:k] < D.k1 || o[:k] > D.k2) && continue
        si = seg_index_at(segs, o[:onsetBin] * D.binMin)
        (si > 0 && !(si in sis)) && push!(sis, si)
    end
    F.fits = isempty(sis) ? [newfit("Fit 1")] : [newfit("Fit $i ($(segs[si][:mode]))"; seg = si) for (i, si) in enumerate(sis[1:min(end, MAX_FITS)])]
    F.activeFit > length(F.fits) && (F.activeFit = 1)
end

"Cycle k -> index (1-based) of the fit it belongs to."
function fit_membership(D::Derived, lst)
    F = D.F
    ensure_fits!(F)
    F.condFits && rebuild_condition_fits!(D, lst)
    mp = Dict{Int,Int}()
    for (i, f) in enumerate(F.fits)
        f[:auto] || foreach(k -> (mp[k] = i), f[:members])
    end
    segs = cond_segments(D)
    inrange(o) = o[:onsetBin] !== nothing && D.k1 <= o[:k] <= D.k2 && !haskey(mp, o[:k])
    for (i, f) in enumerate(F.fits)
        (f[:auto] && f[:seg] !== nothing) || continue
        for o in lst
            (inrange(o) && seg_index_at(segs, o[:onsetBin] * D.binMin) == f[:seg]) && (mp[o[:k]] = i)
        end
    end
    for (i, f) in enumerate(F.fits)
        (f[:auto] && f[:seg] === nothing) || continue
        for o in lst
            inrange(o) && (mp[o[:k]] = i)
        end
    end
    return mp
end

"""Regression of onset time on cycle number with cycle numbers derived from the onset times
(n = round((t - t0)/tau), iterated, anchored to the rows of most onsets); one onset per cycle (closest to the line)."""
function cycle_fit(members, Ph, bin_min)
    pts = sort([Dict{Symbol,Any}(:o => o, :k => o[:k], :t => o[:onsetBin] * bin_min / 60) for o in members]; by = p -> p[:t])
    length(pts) < 2 && return nothing, Dict{Symbol,Any}[]
    byk = sort(pts; by = p -> p[:k])
    iv = [(byk[i][:t] - byk[i-1][:t]) / (byk[i][:k] - byk[i-1][:k]) for i in 2:length(byk) if byk[i][:k] > byk[i-1][:k]]
    tau = isempty(iv) ? Ph : median(iv)
    (0.75Ph < tau < 1.25Ph) || (tau = Ph)
    fit = nothing; excluded = Dict{Symbol,Any}[]
    for _ in 1:6
        n = [Int(floor((p[:t] - pts[1][:t]) / tau + 0.5)) for p in pts]
        off = Int(floor(median([p[:k] - n[i] for (i, p) in enumerate(pts)]) + 0.5))
        for (i, p) in enumerate(pts)
            p[:n] = n[i] + off
        end
        a0 = fit !== nothing ? fit.intercept : median([p[:t] - tau * p[:n] for p in pts])
        b0 = fit !== nothing ? fit.slope : tau
        best = Dict{Int,Any}()
        for p in pts
            r = abs(p[:t] - (a0 + b0 * p[:n]))
            (!haskey(best, p[:n]) || r < best[p[:n]][2]) && (best[p[:n]] = (p, r))
        end
        used = [p for p in pts if best[p[:n]][1] === p]
        excluded = [p for p in pts if best[p[:n]][1] !== p]
        nf = linfit([p[:n] for p in used], [p[:t] for p in used])
        nf === nothing && (fit = nothing; break)
        conv = fit !== nothing && abs(nf.slope - fit.slope) < 1e-9
        fit = nf
        (conv || !(0.5Ph < nf.slope < 1.5Ph)) && break
        tau = nf.slope
    end
    for p in pts
        p[:o][:cyc] = p[:n]
    end
    for p in excluded
        p[:o][:excluded] = true
    end
    return fit, [p[:o] for p in excluded]
end

function compute_fits(D::Derived, lst)
    mp = fit_membership(D, lst)
    segs = cond_segments(D)
    fits = [Dict{Symbol,Any}(:i => i, :name => f[:name], :auto => f[:auto], :guide => f[:guide], :members => Dict{Symbol,Any}[]) for (i, f) in enumerate(D.F.fits)]
    for o in lst
        o[:cyc] = o[:k]; o[:excluded] = false
    end
    for o in lst
        (o[:onsetBin] !== nothing && haskey(mp, o[:k])) && push!(fits[mp[o[:k]]][:members], o)
    end
    for f in fits
        f[:fit], f[:excludedList] = cycle_fit(f[:members], D.Pb * D.binMin / 60, D.binMin)
        f[:kMin] = isempty(f[:members]) ? nothing : f[:members][1][:k]
        f[:kMax] = isempty(f[:members]) ? nothing : f[:members][end][:k]
        cnt = Dict{Int,Int}()
        for o in f[:members]
            si = seg_index_at(segs, o[:onsetBin] * D.binMin); cnt[si] = get(cnt, si, 0) + 1
        end
        f[:segIndex] = isempty(cnt) ? 0 : first(sort(collect(cnt); by = p -> -p[2]))[1]
        f[:mode] = f[:segIndex] > 0 ? segs[f[:segIndex]][:mode] : (isempty(segs) ? "none" : "?")
    end
    return (fits = fits, map = mp, segs = segs)
end

function compute_phases!(D::Derived, lst, R)
    segs = R.segs
    function tau_for_seg(si)
        best = nothing
        for f in R.fits
            (f[:fit] !== nothing && f[:segIndex] == si && is_free_run(f[:mode]) && (best === nothing || length(f[:members]) > length(best[:members]))) && (best = f)
        end
        return best === nothing ? nothing : best[:fit].slope
    end
    for o in lst
        o[:phase] = nothing
        o[:onsetBin] === nothing && continue
        t = o[:onsetBin] * D.binMin
        si = seg_index_at(segs, t)
        seg = si > 0 ? segs[si] : nothing
        if seg !== nothing && seg[:mode] == "LD"
            seg[:ref0] === nothing && continue
            zt = (t - last_lights_on(seg, t)) / 60
            o[:phase] = (label = "ZT", h = zt, deg = zt / (seg[:Tmin] / 60) * 360)
            continue
        end
        own = haskey(R.map, o[:k]) ? R.fits[R.map[o[:k]]] : nothing
        tau = (own !== nothing && own[:fit] !== nothing && own[:segIndex] == si && is_free_run(own[:mode])) ? own[:fit].slope : tau_for_seg(si)
        lab = seg === nothing ? "φ" : "CT"
        if tau === nothing || tau <= 0
            o[:phase] = (label = lab, h = NaN, deg = NaN); continue
        end
        ph = modpos(t - free_run_reference(D, segs, si), tau * 60) / (tau * 60)
        o[:phase] = (label = lab, h = ph * 24, deg = ph * 360)
    end
    for f in R.fits
        f[:phase] = nothing
        f[:fit] === nothing && continue
        if f[:mode] == "LD"
            ds = [o[:phase].deg for o in f[:members] if o[:phase] !== nothing && isfinite(o[:phase].deg)]
            if !isempty(ds)
                deg = modpos(rad2deg(atan(sum(sind, ds), sum(cosd, ds))), 360)
                T = segs[f[:segIndex]][:Tmin] / 60
                f[:phase] = (label = "ZT", h = deg / 360 * T, deg = deg)
            end
        elseif f[:fit].slope > 0
            ref = free_run_reference(D, segs, f[:segIndex])
            ph = modpos(f[:fit].intercept * 60 - ref, f[:fit].slope * 60) / (f[:fit].slope * 60)
            f[:phase] = (label = f[:segIndex] > 0 ? "CT" : "φ", h = ph * 24, deg = ph * 360)
        end
    end
end

"Onsets of every row (with hand edits and drawn lines), psi, regression fits and phases."
function onsets(D::Derived)
    S, F = D.S, D.F
    ensure_fits!(F)
    guides = [(k1 = f[:guide].k1, k2 = f[:guide].k2, b1 = f[:guide].t1 / D.binMin, b2 = f[:guide].t2 / D.binMin, tolBins = S.ana[:guideTolH] * 60 / D.binMin)
              for f in F.fits if f[:guide] !== nothing && !f[:auto]]
    key = (D.binMin, D.Pb, D.off, S.ana[:templateH], S.ana[:searchH], S.ana[:minContrast], S.ana[:guideTolH],
           sort(collect(F.manual); by = first), guides, D.nRows)
    if get(F.cache, :onKey, nothing) != key
        man = Dict{Int,Union{Nothing,Int}}(k => (v === nothing ? nothing : iround(v / D.binMin)) for (k, v) in F.manual)
        F.cache[:on] = detect_onsets(D.B.act, D.binMin, k -> rowstart(D, k), D.Pb, D.kStart, D.nRows - 1;
            template_h = S.ana[:templateH], search_h = S.ana[:searchH], min_contrast = S.ana[:minContrast], manual = man, guides = guides)
        F.cache[:onKey] = key
    end
    lst = F.cache[:on]
    win = iround(360 / D.binMin); L = D.light
    for o in lst
        o[:psi] = nothing
        o[:onsetBin] === nothing && continue
        best = nothing; ob = o[:onsetBin]
        for t in max(1, ob - win):min(length(L) - 1, ob + win)
            (L[t] == 1 && L[t+1] == 0 && (best === nothing || abs(t - ob) < abs(best - ob))) && (best = t)
        end
        best !== nothing && (o[:psi] = (ob - best) * D.binMin / 60)
    end
    R = compute_fits(D, lst)
    compute_phases!(D, lst, R)
    return (list = lst, fits = R.fits, map = R.map, segs = R.segs, fit = isempty(R.fits) ? nothing : R.fits[1][:fit])
end

"Turn automatic fits into explicit member sets (before a hand edit of the fits)."
function freeze_auto_fits!(D::Derived, O)
    F = D.F
    F.condFits = false
    for (i, f) in enumerate(F.fits)
        if f[:auto]
            f[:members] = Set(k for (k, fi) in O.map if fi == i); f[:auto] = false
        end
    end
end

function toggle_in_active_fit!(D::Derived, ks)
    F = D.F; ensure_fits!(F)
    isempty(ks) && return
    O = onsets(D)
    freeze_auto_fits!(D, O)
    ai = F.activeFit
    all_in = all(k -> get(O.map, k, 0) == ai, ks)
    for k in ks
        foreach(f -> delete!(f[:members], k), F.fits)
        all_in || push!(F.fits[ai][:members], k)
    end
end

"Draw line: rows a.k..b.k become the active fit, onsets searched near the line (t in absolute minutes)."
function apply_guide!(D::Derived, a, b)
    F = D.F
    a.k == b.k && return "Draw the line across at least two rows."
    a.k > b.k && ((a, b) = (b, a))
    tau = (b.t - a.t) / (b.k - a.k) / 60
    T = D.S.disp[:T]
    (0.5T < tau < 1.5T) || return @sprintf("The line implies a period of %.1f h; draw it along the onsets (from one row to a later row).", tau)
    ensure_fits!(F)
    freeze_auto_fits!(D, onsets(D))
    fit = F.fits[F.activeFit]
    for k in a.k:b.k
        foreach(f -> delete!(f[:members], k), F.fits)
    end
    fit[:members] = Set(a.k:b.k)
    fit[:guide] = (k1 = a.k, k2 = b.k, t1 = a.t, t2 = b.t)
    R = onsets(derive(D.S, F)).fits[F.activeFit]
    return @sprintf("%s: rows %d–%d along the line · n %d%s", fit[:name], a.k + 1, b.k + 1, length(R[:members]),
                    R[:fit] === nothing ? "" : @sprintf(" · τ %.2f h", R[:fit].slope))
end

"Place (or delete) an onset at row k, bin j from the row start; the click goes to the cycle whose onset is nearest."
function place_onset!(D::Derived, k_row, j; delete = false)
    F = D.F
    b = rowstart(D, k_row) + j
    O = onsets(D)
    if delete
        best = nothing
        for o in O.list
            (o[:onsetBin] !== nothing && (best === nothing || abs(o[:onsetBin] - b) < abs(best[:onsetBin] - b))) && (best = o)
        end
        best !== nothing && (F.manual[best[:k]] = nothing)
        return
    end
    cyc = k_row + fld(j, D.Pb)
    byk = Dict(o[:k] => o for o in O.list)
    function pred(k)
        o = get(byk, k, nothing)
        (o !== nothing && o[:onsetBin] !== nothing) && return o[:onsetBin]
        a = get(byk, k - 1, nothing); c = get(byk, k + 1, nothing)
        (a !== nothing && a[:onsetBin] !== nothing) && return a[:onsetBin] + D.Pb
        (c !== nothing && c[:onsetBin] !== nothing) && return c[:onsetBin] - D.Pb
        return nothing
    end
    best, bd = cyc, Inf
    for c in (cyc - 1, cyc, cyc + 1)
        p = pred(c)
        (p !== nothing && abs(p - b) < bd) && ((best, bd) = (c, abs(p - b)))
    end
    bd <= D.Pb / 2 && (cyc = best)
    F.manual[cyc] = b * D.binMin
end

# --- Periodograms, profiles, wavelet, daily rows -----------------------------

function periodogram(D::Derived; method = nothing, x = nothing)
    a = D.S.ana; method = something(method, a[:method]); x = something(x, D.x)
    method == "ls" && return lomb_scargle(x, D.binMin, a[:pmin], a[:pmax], a[:alpha], max(0.02, D.binMin / 120))
    return chi_square_periodogram(x, D.binMin, a[:pmin], a[:pmax], a[:alpha])
end

function fit_periodogram(D::Derived, f; method = nothing)
    f[:kMin] === nothing && return nothing
    a = max(0, rowstart(D, f[:kMin])); b = min(D.B.n, rowstart(D, f[:kMax] + 1))
    b - a < 4 && return nothing
    key = (:fitpg, D.binMin, a, b, something(method, D.S.ana[:method]), D.S.ana[:pmin], D.S.ana[:pmax], D.S.ana[:alpha])
    haskey(D.F.cache, key) || (D.F.cache[key] = periodogram(D; method = method, x = D.B.act[a+1:b]))
    return (pg = D.F.cache[key], a = a, b = b, days = (b - a) * D.binMin / 1440)
end

function dark_light_sums(D::Derived, a, b)
    dark = 0.0; light = 0.0
    for i in a:b-1
        v = act(D, i); st = lightat(D, i)
        isnan(v) && continue
        st == 0 && (dark += v); st == 1 && (light += v)
    end
    return dark, light
end
pct_dark(dark, light) = dark + light > 0 ? 100dark / (dark + light) : NaN

"Profile of the analysis range folded at the row period, the periodogram peak or a custom period."
function range_profile(D::Derived)
    a = D.S.ana
    pg = a[:fold] == "peak" ? periodogram(D) : nothing
    PH = (a[:fold] == "peak" && pg.peak !== nothing) ? pg.peak.periodH : a[:fold] == "custom" ? a[:foldCustom] : D.S.disp[:T]
    Pb = max(2, iround(PH * 60 / D.binMin)); PH = Pb * D.binMin / 60
    m, sem, n = fold_profile(D.x, 0, Pb)
    lm, _, _ = fold_profile(D.light[D.a+1:D.b], 0, Pb)
    dk, lt = dark_light_sums(D, D.a, D.b)
    return (PH = PH, x = collect(0:Pb-1) .* (D.binMin / 60), mean = m, sem = sem, n = n, light = lm,
            pctDark = pct_dark(dk, lt), np = non_parametric(D.x, D.binMin, D.a))
end

"Profile of one fit's rows: LD fits at T in ZT, DD/LL fits at tau in CT."
function fold_fit(D::Derived, O, f)
    f[:kMin] === nothing && return nothing
    a = max(0, rowstart(D, f[:kMin])); b = min(D.B.n, rowstart(D, f[:kMax] + 1))
    b - a < 4 && return nothing
    seg = f[:segIndex] > 0 ? O.segs[f[:segIndex]] : nothing
    is_ld = f[:mode] == "LD" && seg !== nothing && seg[:ref0] !== nothing
    an = D.S.ana
    peak_p() = (r = fit_periodogram(D, f); (r === nothing || r.pg.peak === nothing) ? nothing : r.pg.peak.periodH * 60)
    if an[:fitFold] == "row"
        P, src = D.S.disp[:T] * 60, "row period"
    elseif an[:fitFold] == "peak"
        P, src = peak_p(), "periodogram peak"
    elseif is_ld
        P, src = seg[:Tmin], "LD cycle length T"
    elseif f[:fit] !== nothing && f[:fit].slope > 0
        P, src = f[:fit].slope * 60, "τ from onsets"
    else
        P, src = peak_p(), "periodogram peak"
    end
    (P === nothing || P <= 0) && ((P, src) = (D.S.disp[:T] * 60, "row period"))
    ref = is_ld ? seg[:ref0] : seg !== nothing ? free_run_reference(D, O.segs, f[:segIndex]) : rowstart(D, f[:kMin]) * D.binMin
    nb = max(4, iround(P / D.binMin))
    s = zeros(nb); s2 = zeros(nb); c = zeros(nb); ls = zeros(nb); lc = zeros(nb)
    for i in a:b-1
        t = (i + 0.5) * D.binMin
        j = min(nb - 1, Int(floor(modpos(t - ref, P) / P * nb))) + 1
        v = act(D, i); st = lightat(D, i)
        isnan(st) || (ls[j] += st; lc[j] += 1)
        isnan(v) && continue
        s[j] += v; s2[j] += v^2; c[j] += 1
    end
    dk, lt = dark_light_sums(D, a, b)
    axis = an[:fitAxis] == "hours" ? "hours" : is_ld ? (abs(P - seg[:Tmin]) < 1e-6 ? "ZT" : "hours") : (seg !== nothing ? "CT" : "hours")
    span = axis == "CT" ? 24.0 : P / 60
    m = [c[h] > 0 ? s[h] / c[h] : NaN for h in 1:nb]
    sem = [c[h] > 1 ? sqrt(max(0, (s2[h] - c[h] * m[h]^2) / (c[h] - 1))) / sqrt(c[h]) : NaN for h in 1:nb]
    lf = [lc[h] > 0 ? ls[h] / lc[h] : NaN for h in 1:nb]
    xs = [(h - 0.5) / nb * span for h in 1:nb]
    pk = any(!isnan, m) ? argmax(map(v -> isnan(v) ? -Inf : v, m)) : nothing
    return (f = f, P = P / 60, src = src, isLD = is_ld, axis = axis, span = span, x = xs, mean = m, sem = sem, light = lf, peak = pk,
            days = (b - a) * D.binMin / 1440, pctDark = pct_dark(dk, lt))
end
axis_label(r) = r.axis == "ZT" ? "ZT (h)" : r.axis == "CT" ? "CT (h)" : (r.isLD ? "Hours from lights-on" : "Hours from reference")

function wavelet_data(D::Derived)
    an = D.S.ana; B = D.B
    a, b = an[:wData] == "all" ? (B.firstBin, B.n) : (D.a, D.b)
    while a < b && isnan(B.act[a+1]); a += 1; end
    while b > a && isnan(B.act[b]); b -= 1; end
    b - a < 8 && return nothing
    key = (:w, D.binMin, a, b, an[:wmin], an[:wmax])
    haskey(D.F.cache, key) && return D.F.cache[key]
    g = max(1, Int(ceil((b - a) / 4000))); n = (b - a) ÷ g
    y = [nanmean(B.act[a+i*g+1:a+(i+1)*g]) for i in 0:n-1]
    gap = isnan.(y)
    if any(gap)
        idx = findall(!, gap)
        for i in findall(gap)               # linear interpolation over gaps
            j = searchsortedfirst(idx, i)
            y[i] = j == 1 ? y[idx[1]] : j > length(idx) ? y[idx[end]] :
                   y[idx[j-1]] + (y[idx[j]] - y[idx[j-1]]) * (i - idx[j-1]) / (idx[j] - idx[j-1])
        end
    end
    dt_h = g * D.binMin / 60
    step = max(0.05, round((an[:wmax] - an[:wmin]) / 160 * 100) / 100)
    periods = round.(collect(an[:wmin]:step:an[:wmax] + 1e-9); digits = 3)
    res = morlet_cwt(y, dt_h, periods)
    tMin = [(a + (i + 0.5) * g) * D.binMin for i in 0:n-1]
    valid = (res.ridge .< res.coi) .& .!gap
    W = merge(res, (tMin = tMin, gap = gap, valid = valid))
    D.F.cache[key] = W
    return W
end
function ridge_stats(W, mask)
    v = W.ridge[mask .& W.valid]
    isempty(v) && return nothing
    return (mean = mean(v), sd = length(v) > 1 ? std(v) : NaN, hours = length(v) * W.dtH)
end

function row_totals(D::Derived)
    rows = Dict{Int,Any}(); mx = 0.0; mxall = 0.0
    for k in D.kStart:D.nRows-1
        a = rowstart(D, k); b = a + D.Pb
        seg = D.B.act[max(0, a)+1:max(0, min(D.B.n, b))]
        s = nansum(seg); cover = count(!isnan, seg) / D.Pb
        rows[k] = (total = s, partial = cover < 0.95)
        mxall = max(mxall, s); cover >= 0.95 && (mx = max(mx, s))
    end
    return (rows = rows, max = mx > 0 ? mx : mxall > 0 ? mxall : 1.0)
end

function daily_rows(D::Derived)
    out = NamedTuple[]
    for k in D.kStart:D.nRows-1
        a = rowstart(D, k); b = rowstart(D, k + 1)
        tot = li = da = 0.0; miss = n = known = lb = 0
        for t in max(0, a, D.B.firstBin):min(D.B.n, b)-1
            n += 1
            v = act(D, t); s = lightat(D, t)
            isnan(s) || (known += 1; s == 1 && (lb += 1))
            isnan(v) && (miss += 1; continue)
            tot += v; s == 1 && (li += v); s == 0 && (da += v)
        end
        push!(out, (k = k, date = fmt_date(D.F.rec, a * D.binMin + 1), total = tot, light = li, dark = da,
                    missPct = n > 0 ? 100miss / n : 100.0, lightH = known > 0 ? lb * D.binMin / 60 : NaN))
    end
    return out
end

phase_hours(O, f) = (f[:mode] == "LD" && f[:segIndex] > 0) ? O.segs[f[:segIndex]][:Tmin] / 60 : 24.0

function phase_summary(D::Derived, O)
    out = NamedTuple[]
    for f in O.fits
        degs = [o[:phase].deg for o in f[:members] if o[:phase] !== nothing && isfinite(o[:phase].deg)]
        sg = f[:segIndex] > 0 ? O.segs[f[:segIndex]] : nothing
        ref = "–"
        if f[:mode] == "LD" && sg !== nothing && sg[:ref0] !== nothing
            ref = "ZT0 = lights-on " * fmt_clock(sg[:ref0])
        elseif f[:fit] !== nothing
            r = free_run_reference(D, O.segs, f[:segIndex])
            ref = string(fmt_date(D.F.rec, r), " ", fmt_clock(r), " (", PHASE_REF_LABEL[D.S.ana[:phaseRef]], ")")
        end
        push!(out, (f = f, cs = circular_stats(degs), Th = phase_hours(O, f), ref = ref,
                    label = f[:phase] !== nothing ? f[:phase].label : (f[:mode] == "LD" ? "ZT" : "CT")))
    end
    return out
end

function seg_label(segs, si)
    (1 <= si <= length(segs)) || return "–"
    s = segs[si]
    return string(s[:mode], " (day ", s[:startDay], "–", s[:endDay] === nothing ? "" : @sprintf("%g", s[:endDay]), ")")
end

"(label, first row, last row) for All rows and each lighting condition (rows 1-based)."
function range_presets(D::Derived)
    F = D.F
    out = [("All rows", D.kStart + 1, D.nRows)]
    segs = Tuple{String,Float64,Float64}[]
    if F.lightSrc == "recorded" && F.rec.light !== nothing
        segs = [(s[:mode], (s[:startDay] - 1) * 1440.0, s[:endDay] * 1440.0) for s in detect_lighting(F.rec, F.luxThr).segments]
    elseif F.lightSrc == "schedule"
        sc = normalize_schedule(F.schedule); e = D.B.n * D.binMin
        segs = [(s.mode, s.start, i < length(sc) ? sc[i+1].start : e) for (i, s) in enumerate(sc)]
    end
    for (mode, a, b) in segs
        r1 = max(D.kStart, Int(ceil((a / D.binMin - D.off) / D.Pb))) + 1
        r2 = min(D.nRows, Int(floor((b / D.binMin - D.off) / D.Pb)))
        r2 >= r1 && push!(out, ("$mode $r1–$r2", r1, r2))
    end
    return out
end

# --- Schedule helpers ---------------------------------------------------------

start_kind(c::Cond) = (c.mode == "LL" || (c.mode == "LD" && c.first != 'D')) ? :on : :off
"Suggested starts of condition i (1-based): ends of D (:on) or of L (:off) of the preceding LD condition."
function start_candidates(F::Entry, sched, i)
    (i <= 1 || sched[i-1].mode != "LD") && return Float64[]
    until = F.rec.t0Min + length(F.rec.counts) * F.rec.intervalMin + 1440
    return ld_boundaries(sched[i-1], start_kind(sched[i]), until; max_n = 800)
end
function snap_start!(F::Entry, sched, i)
    c = start_candidates(F, sched, i)
    isempty(c) && return false
    prev = sched[i-1]; cur = sched[i].start
    best = c[argmin(abs.(c .- cur))]
    (best == cur || abs(best - cur) > prev.L + prev.D) && return false
    sched[i].start = best
    return true
end
function add_condition!(F::Entry)
    sched = normalize_schedule(F.schedule)
    last_ = sched[end]
    rec_end = F.rec.t0Min + length(F.rec.counts) * F.rec.intervalMin
    li = findlast(c -> c.mode == "LD", sched)
    if last_.mode == "LD"
        push!(sched, Cond("DD", min(last_.start + 7 * 1440, rec_end), 'L', last_.L, last_.D))
        snap_start!(F, sched, length(sched))
    else
        push!(sched, Cond("LD", min(last_.start + 7 * 1440, rec_end), 'L', li === nothing ? 720 : sched[li].L, li === nothing ? 720 : sched[li].D))
    end
    F.schedule = normalize_schedule(sched)
end

# --- Tables (CSV) ---------------------------------------------------------------

fx(v, d = 3) = (v === nothing || (v isa AbstractFloat && !isfinite(v))) ? "" : Printf.format(Printf.Format("%.$(d)f"), v)
csvcell(v) = (s = string(v); occursin(r"[\",\n]", s) ? "\"" * replace(s, "\"" => "\"\"") * "\"" : s)
to_csv(rows) = join([join(csvcell.(r), ",") for r in rows], "\r\n") * "\r\n"

function onsets_table(D::Derived, O)
    R = D.F.rec
    out = Any[["row", "date", "onset_clock", "onset_abs_h", "offset_clock", "alpha_h", "psi_h", "phase_label", "phase_h", "phase_deg",
               "fit", "cycle", "excluded", "manual"]]
    for o in O.list
        o[:onsetBin] === nothing && continue
        on = o[:onsetBin] * D.binMin
        off = o[:offsetBin] === nothing ? nothing : o[:offsetBin] * D.binMin
        fi = get(O.map, o[:k], 0)
        push!(out, [o[:k] + 1, fmt_date(R, on), fmt_clock(on), fx(on / 60), off === nothing ? "" : fmt_clock(off),
                    off === nothing ? "" : fx((off - on) / 60, 2), fx(o[:psi], 2), o[:phase] === nothing ? "" : o[:phase].label,
                    o[:phase] === nothing ? "" : fx(o[:phase].h, 2), o[:phase] === nothing ? "" : fx(o[:phase].deg, 1),
                    fi > 0 ? O.fits[fi][:name] : "", o[:cyc] + 1, o[:excluded] ? "yes" : "", o[:manual] ? "yes" : ""])
    end
    return out
end

function fits_table(D::Derived, O)
    out = Any[["fit", "condition", "first_row", "last_row", "n", "tau_h", "tau_se_h", "r2", "phase_label", "phase_h", "phase_deg", "excluded"]]
    for f in O.fits
        ft = f[:fit]; ph = f[:phase]
        push!(out, [f[:name], f[:segIndex] > 0 ? seg_label(O.segs, f[:segIndex]) : "", f[:kMin] === nothing ? "" : f[:kMin] + 1,
                    f[:kMax] === nothing ? "" : f[:kMax] + 1, length(f[:members]), ft === nothing ? "" : fx(ft.slope), ft === nothing ? "" : fx(ft.se),
                    ft === nothing ? "" : fx(ft.r2), ph === nothing ? "" : ph.label, ph === nothing ? "" : fx(ph.h, 2),
                    ph === nothing ? "" : fx(ph.deg, 1), length(f[:excludedList])])
    end
    return out
end

function phase_table(D::Derived, O)
    rows = phase_summary(D, O)
    out = Any[["fit", "condition", "n", "tau_h", "phase_label", "phase_h", "phase_deg", "R", "circular_sd_h", "rayleigh_p", "reference"]]
    for r in rows
        f = r.f; cs = r.cs
        push!(out, [f[:name], f[:mode], length(f[:members]), f[:fit] === nothing ? "" : fx(f[:fit].slope), r.label,
                    f[:phase] === nothing ? "" : fx(f[:phase].h, 2), f[:phase] === nothing ? "" : fx(f[:phase].deg, 1),
                    cs === nothing ? "" : fx(cs.R), (cs === nothing || !isfinite(cs.csdDeg)) ? "" : fx(cs.csdDeg / 360 * r.Th, 2),
                    cs === nothing ? "" : fmt_p(cs.p), r.ref])
    end
    diffs = Any[["from", "to", "delta_phase_deg", "delta_phase_h"]]
    for i in 2:length(rows)
        (rows[i-1].f[:phase] === nothing || rows[i].f[:phase] === nothing) && continue
        d = wrap180(rows[i].f[:phase].deg - rows[i-1].f[:phase].deg)
        push!(diffs, [rows[i-1].f[:name], rows[i].f[:name], fx(d, 1), fx(d / 15, 2)])
    end
    return out, diffs
end

function daily_table(D::Derived)
    out = Any[["row", "date", "total", "in_light", "in_dark", "pct_missing", "light_h"]]
    for r in daily_rows(D)
        push!(out, [r.k + 1, r.date, fx(r.total, 0), fx(r.light, 0), fx(r.dark, 0), fx(r.missPct, 1), fx(r.lightH, 2)])
    end
    return out
end

function light_table(F::Entry)
    out = Any[["day", "date", "mode", "light_h", "lights_on", "lights_off", "mean_lux_in_light"]]
    for r in detect_lighting(F.rec, F.luxThr).days
        r[:n] == 0 && continue
        push!(out, [r[:day], fmt_date(F.rec, (r[:day] - 1) * 1440), r[:mode], fx(r[:lightHours], 2),
                    r[:onMin] === nothing ? "" : fmt_clock(r[:onMin]), r[:offMin] === nothing ? "" : fmt_clock(r[:offMin]), fx(r[:meanLux], 1)])
    end
    return out
end

function periodogram_table(D::Derived, O)
    pg = periodogram(D)
    out = Any[["period_h", "value", "critical", "fit"]]
    for p in pg.points
        push!(out, [fx(p.periodH), fx(p.value), fx(p.crit), "analysis range"])
    end
    for f in O.fits
        r = fit_periodogram(D, f)
        r === nothing && continue
        for p in r.pg.points
            push!(out, [fx(p.periodH), fx(p.value), fx(p.crit), f[:name]])
        end
    end
    return out
end

function profile_table(D::Derived, O)
    pr = range_profile(D)
    out = Any[["profile", "x", "mean", "sem", "light_fraction"]]
    for i in eachindex(pr.x)
        push!(out, ["analysis range", fx(pr.x[i]), fx(pr.mean[i]), fx(pr.sem[i]), fx(pr.light[i])])
    end
    for f in O.fits
        r = fold_fit(D, O, f)
        r === nothing && continue
        for i in eachindex(r.x)
            push!(out, [f[:name] * " (" * axis_label(r) * ")", fx(r.x[i]), fx(r.mean[i]), fx(r.sem[i]), fx(r.light[i])])
        end
    end
    return out
end

function wavelet_table(D::Derived, W)
    out = Any[["abs_h", "date", "clock", "ridge_period_h", "inside_cone"]]
    for i in eachindex(W.tMin)
        t = W.tMin[i]
        push!(out, [fx(t / 60), fmt_date(D.F.rec, t), fmt_clock(t), fx(W.ridge[i]), W.valid[i] ? "yes" : "no"])
    end
    return out
end

function batch_table(S::Session)
    out = Any[["name", "file", "rows", "days", "chi2_peak_h", "chi2_significant", "ls_peak_h", "tau_by_fit_h", "psi_h", "pct_activity_dark", "IS", "IV", "RA"]]
    for F in S.files
        D = derive(S, F); O = onsets(D)
        chi = periodogram(D; method = "chi"); ls = periodogram(D; method = "ls")
        psis = [o[:psi] for o in O.list if D.k1 <= o[:k] <= D.k2 && o[:psi] !== nothing]
        npv = non_parametric(D.x, D.binMin, D.a)
        dk, lt = dark_light_sums(D, D.a, D.b)
        lab(f) = f[:mode] in ("none", "?") ? f[:name] : f[:mode]
        push!(out, [F.rec.name, F.rec.fileName, "$(D.k1 + 1)–$(D.k2 + 1)", fx((D.b - D.a) * D.binMin / 1440, 2),
                    chi.peak === nothing ? "" : fx(chi.peak.periodH, 2), (chi.peak !== nothing && chi.peak.significant) ? "yes" : "no",
                    ls.peak === nothing ? "" : fx(ls.peak.periodH, 2),
                    join([@sprintf("%s %.3f", lab(f), f[:fit].slope) for f in O.fits if f[:fit] !== nothing], "; "),
                    isempty(psis) ? "" : fx(mean(psis), 2), fx(pct_dark(dk, lt), 1),
                    npv === nothing ? "" : fx(npv.IS), npv === nothing ? "" : fx(npv.IV), npv === nothing ? "" : fx(npv.RA)])
    end
    return out
end

# =============================================================================
# Figures (Makie). The same functions draw in the window (GLMakie) and in exported files (CairoMakie).
# =============================================================================

const FIT_COLOURS = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e87ba4", "#008300"]
col(s) = Makie.to_color(s)
fitcol(i) = col(FIT_COLOURS[mod(i - 1, length(FIT_COLOURS))+1])

function palette(S::Session)
    g = S.disp[:ldCol] == "grey"
    return (bg = col("#ffffff"), ink = col("#141821"), muted = col("#586074"), line = col("#dadee7"),
            light = col(g ? "#ffffff" : "#f7e7a6"), dark = col(g ? "#e3e3e3" : "#d3d8e3"), nodata = col("#f0e3e3"),
            ldLight = col(g ? "#ffffff" : "#f0b429"), ldDark = col(g ? "#c8c8c8" : "#39404f"), ldEdge = g ? col("#a3a9b5") : nothing,
            bar = col("#141821"), onset = col("#d9481f"), offset = col("#1baf7a"), accent = col("#3446c9"), unused = col("#9aa1af"),
            totalBar = col("#3e87b8"))
end
const CLEAR = RGBAf(0, 0, 0, 0)

"Activity and light state of every plotted row: M[j, r] (j = bin in the row, r = plotted row), NaN outside the data."
function acto_matrix(D::Derived, span)
    rows = D.nRows - D.kStart
    M = fill(NaN, span, rows); Lt = fill(NaN, span, rows); inside = falses(span, rows)
    for r in 1:rows, j in 1:span
        b = rowstart(D, D.kStart + r - 1) + j - 1
        if b >= D.B.firstBin && 0 <= b < D.B.n
            inside[j, r] = true; M[j, r] = act(D, b); Lt[j, r] = lightat(D, b)
        end
    end
    return M, Lt, inside
end

"""Draw the actogram of D into axis ax (and the row totals into axp, if given).
Returns the onset markers [(x_h, y, k)] for hit testing; x in hours from the row start, y = plotted row (0 = top)."""
function draw_actogram!(ax, D::Derived, O; axp = nothing)
    S = D.S; disp = S.disp; P = palette(S)
    span = D.Pb * disp[:mult]
    M, Lt, inside = acto_matrix(D, span)
    rows = size(M, 2); bh = D.binMin / 60; xmax = span * bh
    empty!(ax)
    vals = filter(v -> !isnan(v) && v > 0, M)
    scale = max((isempty(vals) ? 1.0 : quantile(vals, 0.95)) / 2.0^disp[:gainLog], 1e-9)
    fl = disp[:floor]
    if disp[:style] == "bars"
        img = fill(CLEAR, span, rows)
        for i in eachindex(M)
            if inside[i] && isnan(M[i])
                img[i] = P.nodata
            elseif disp[:shade]
                Lt[i] == 1 && (img[i] = P.light); Lt[i] == 0 && (img[i] = P.dark)
            end
        end
        image!(ax, 0 .. xmax, 0 .. rows, img; interpolate = false)
        w = bh * (span <= 600 ? 0.85 : 1.0)
        rects = Rect2f[]
        for r in 1:rows, j in 1:span
            v = M[j, r]
            (isnan(v) || v <= fl) && continue
            h = min(1, v / scale) * 0.86
            push!(rects, Rect2f((j - 1) * bh, r - 1 + 0.93 - h, w, h))
        end
        isempty(rects) || poly!(ax, rects; color = P.bar, strokewidth = 0)
    else
        sub = 5
        img = fill(CLEAR, span, rows * sub)       # y grows downwards: sub-row 1 of row r is at the top
        cmap = disp[:style] == "heat" ? nothing : Makie.to_colormap(Symbol(disp[:style]))
        for r in 1:rows, j in 1:span
            inside[j, r] || continue
            v = M[j, r]
            c = if isnan(v)
                P.nodata
            else
                f = clamp((v - fl) / max(1e-9, scale - fl), 0, 1)
                cmap === nothing ? RGBAf(P.bar.r, P.bar.g, P.bar.b, 0.06 + 0.94f) : cmap[iround(f * (length(cmap) - 1)) + 1]
            end
            for s in 1:sub
                img[j, (r-1)*sub+s] = c
            end
            if disp[:shade] && !isnan(v)
                Lt[j, r] == 1 && (img[j, (r-1)*sub+1] = P.ldLight); Lt[j, r] == 0 && (img[j, (r-1)*sub+1] = P.ldDark)
            end
        end
        image!(ax, 0 .. xmax, 0 .. rows, img; interpolate = false)
        if disp[:shade] && P.ldEdge !== nothing
            xs = Float64[]; ys = Float64[]
            for r in 1:rows
                cols = findall(!isnan, view(Lt, :, r))
                isempty(cols) && continue
                for y in (r - 1 + 0.2, r - 1 + 0.005)
                    append!(xs, [(cols[1] - 1) * bh, cols[end] * bh, NaN]); append!(ys, [y, y, NaN])
                end
            end
            lines!(ax, xs, ys; color = P.ldEdge, linewidth = 0.5)
        end
    end
    tick = disp[:T] >= 12 ? 6 : 1
    vlines!(ax, collect(0:tick:xmax); color = P.line, linewidth = 0.5)
    disp[:mult] > 1 && vlines!(ax, [p * D.Pb * bh for p in 1:disp[:mult]-1]; color = P.muted, linewidth = 0.8)
    hlines!(ax, collect(1:rows) .- 0.02; color = P.line, linewidth = 0.3)
    markers = Tuple{Float64,Float64,Int}[]
    if O !== nothing && disp[:showOn]
        for f in O.fits
            (f[:fit] === nothing || f[:kMin] === nothing) && continue
            c = fitcol(f[:i])
            seg = f[:segIndex] > 0 ? O.segs[f[:segIndex]] : nothing
            xs = Float64[]; ys = Float64[]
            if f[:mode] == "LD" && disp[:ldLine] == "mean" && f[:phase] !== nothing && seg !== nothing && seg[:ref0] !== nothing
                for k in f[:kMin]:f[:kMax]
                    r = k - D.kStart; rs = rowstart(D, k) * D.binMin
                    t = last_lights_on(seg, rs) + f[:phase].h * 60
                    while t - seg[:Tmin] >= rs; t -= seg[:Tmin]; end
                    while t < rs + span * D.binMin
                        t >= rs && (append!(xs, [(t - rs) / 60, (t - rs) / 60, NaN]); append!(ys, [r + 0.05, r + 0.95, NaN]))
                        t += seg[:Tmin]
                    end
                end
                lines!(ax, xs, ys; color = c, linewidth = 1.6, linestyle = :dot)
                continue
            end
            ft = f[:fit]
            for k in f[:kMin]:f[:kMax]-1, p in -1:disp[:mult]
                r = k - D.kStart
                j0 = (ft.intercept + ft.slope * (k + p)) * 60 / D.binMin - rowstart(D, k)
                j1 = (ft.intercept + ft.slope * (k + 1 + p)) * 60 / D.binMin - rowstart(D, k + 1)
                if 0 <= j0 <= span && 0 <= j1 <= span
                    append!(xs, [j0 * bh, j1 * bh, NaN]); append!(ys, [r + 0.5, r + 1.5, NaN])
                end
            end
            isempty(xs) || lines!(ax, xs, ys; color = c, linewidth = 1.8, linestyle = :dash)
        end
        for f in O.fits
            g = f[:guide]
            (g === nothing || g.k2 <= g.k1) && continue
            pg(c) = (g.t1 + (g.t2 - g.t1) * (c - g.k1) / (g.k2 - g.k1)) / D.binMin
            xs = Float64[]; ys = Float64[]
            for k in g.k1:g.k2-1, p in -1:disp[:mult]
                r = k - D.kStart
                j0 = pg(k + p) - rowstart(D, k); j1 = pg(k + 1 + p) - rowstart(D, k + 1)
                if 0 <= j0 <= span && 0 <= j1 <= span
                    append!(xs, [j0 * bh, j1 * bh, NaN]); append!(ys, [r + 0.5, r + 1.5, NaN])
                end
            end
            isempty(xs) || lines!(ax, xs, ys; color = fitcol(f[:i]), linewidth = 1.0, linestyle = :dot)
        end
        mx = Float64[]; my = Float64[]; mc = RGBAf[]; ring = Bool[]
        for o in O.list
            o[:onsetBin] === nothing && continue
            fi = get(O.map, o[:k], 0)
            c = (fi == 0 || o[:excluded]) ? P.unused : fitcol(fi)
            for r in 1:rows
                j = o[:onsetBin] - rowstart(D, D.kStart + r - 1)
                if 0 <= j < span
                    push!(mx, (j + 0.5) * bh); push!(my, r - 0.5); push!(mc, c); push!(ring, o[:manual])
                    push!(markers, ((j + 0.5) * bh, r - 0.5, o[:k]))
                end
            end
        end
        sz = clamp(260 / max(rows, 1), 6, 11)
        if !isempty(mx)
            nr = .!ring
            any(nr) && scatter!(ax, mx[nr], my[nr]; color = mc[nr], markersize = sz, strokecolor = :white, strokewidth = 0.8)
            any(ring) && scatter!(ax, mx[ring], my[ring]; color = :white, markersize = sz, strokecolor = mc[ring], strokewidth = 1.8)
        end
        if disp[:showOff]
            ox = Float64[]; oy = Float64[]
            for o in O.list, r in 1:rows
                o[:offsetBin] === nothing && continue
                j = o[:offsetBin] - rowstart(D, D.kStart + r - 1)
                0 <= j < span && (push!(ox, (j + 0.5) * bh); push!(oy, r - 0.5))
            end
            isempty(ox) || scatter!(ax, ox, oy; marker = :diamond, color = P.offset, markersize = sz * 0.8, strokecolor = :white, strokewidth = 0.6)
        end
    end
    lines!(ax, [0.0, 0.0], [D.k1 - D.kStart, D.k2 - D.kStart + 1.0]; color = P.accent, linewidth = 4)
    ax.xticks = (collect(0:tick:xmax), [@sprintf("%02d", Int(floor(modpos(disp[:offsetH] + h, 24)))) for h in 0:tick:xmax])
    step = max(1, Int(ceil(rows / 60)))
    labs = String[]
    for r in 0:step:rows-1
        k = D.kStart + r
        push!(labs, string(k + 1, disp[:T] == 24 ? "  " * fmt_date_short(D.F.rec, rowstart(D, k) * D.binMin + 1) : ""))
    end
    ax.yticks = (collect(0:step:rows-1) .+ 0.5, labs)
    if axp !== nothing
        empty!(axp)
        rt = row_totals(D)
        vals = [get(rt.rows, D.kStart + r, (total = 0.0, partial = true)) for r in 0:rows-1]
        tot = [v.total / rt.max for v in vals]
        if disp[:rightPanel] == "bars"
            barplot!(axp, collect(1:rows) .- 0.5, tot; direction = :x, width = 0.36,
                     color = [v.partial ? RGBAf(P.totalBar.r, P.totalBar.g, P.totalBar.b, 0.4) : P.totalBar for v in vals])
        elseif disp[:rightPanel] == "line"
            scatterlines!(axp, tot, collect(1:rows) .- 0.5; color = P.totalBar, markersize = 4)
        else
            for (r, v) in enumerate(vals)
                text!(axp, 0.05, r - 0.5; text = @sprintf("%.0f", v.total), align = (:left, :center), fontsize = 9, color = v.partial ? P.muted : P.ink)
            end
        end
        xlims!(axp, 0, 1.05)
        ylims!(axp, 0, rows)
    end
    return (markers = markers, rows = rows, xmax = xmax, span = span)
end

function new_actogram_axis(gp, D::Derived; title = "")
    ax = Axis(gp; yreversed = true, xaxisposition = :top, xlabel = "clock time (h)", xlabelsize = 11, title = title,
              titlealign = :right, titlesize = 12, yticklabelsize = 9, xticklabelsize = 10, xgridvisible = false, ygridvisible = false,
              ytickwidth = 0, xtickwidth = 0)
    return ax
end
new_panel_axis(gp) = Axis(gp; yreversed = true, xaxisposition = :top, title = "row total", titlesize = 9, xticksvisible = false,
                          xticklabelsvisible = false, yticksvisible = false, yticklabelsvisible = false, xgridvisible = false, ygridvisible = false)

"Stand-alone actogram figure (for saving)."
function actogram_figure(D::Derived, O; width_px = nothing)
    rows = D.nRows - D.kStart
    w = something(width_px, D.S.disp[:widthPx]) + 160
    h = max(300, rows * max(8, D.S.disp[:rowH]) + 110)
    fig = Figure(; size = (w, h), backgroundcolor = :white)
    ax = new_actogram_axis(fig[1, 1], D; title = @sprintf("%s · %g-min bins", D.F.rec.name, D.binMin))
    axp = nothing
    if D.S.disp[:rightPanel] != "none"
        axp = new_panel_axis(fig[1, 2]); colsize!(fig.layout, 2, Relative(0.1)); linkyaxes!(ax, axp)
    end
    g = draw_actogram!(ax, D, O; axp = axp)
    limits!(ax, 0, g.xmax, 0, g.rows)
    return fig
end

function draw_periodogram!(gp::GridLayout, D::Derived, O)
    pg = periodogram(D); chi = D.S.ana[:method] != "ls"; P = palette(D.S)
    ax = Axis(gp[1, 1:max(1, length(O.fits))]; xlabel = "Period (h)", ylabel = chi ? "Qp" : "Normalised power",
              title = (chi ? "χ² periodogram" : "Lomb–Scargle periodogram") * " · rows $(D.k1 + 1)–$(D.k2 + 1)", titlealign = :left, titlesize = 12)
    if !isempty(pg.points)
        x = [p.periodH for p in pg.points]
        lines!(ax, x, [p.value for p in pg.points]; color = P.accent, linewidth = 1.5)
        lines!(ax, x, [p.crit for p in pg.points]; color = P.ink, linewidth = 0.8, linestyle = :dash)
        if pg.peak !== nothing
            scatter!(ax, [pg.peak.periodH], [pg.peak.value]; color = P.onset, markersize = 9)
            text!(ax, pg.peak.periodH, pg.peak.value; text = @sprintf(" %.2f h", pg.peak.periodH), align = (:left, :bottom), fontsize = 11)
        end
    end
    for (i, f) in enumerate(O.fits)
        r = fit_periodogram(D, f)
        (r === nothing || isempty(r.pg.points)) && continue
        a2 = Axis(gp[2, i]; xlabel = "Period (h)", title = @sprintf("%s · rows %d–%d", f[:name], f[:kMin] + 1, f[:kMax] + 1),
                  titlesize = 10, titlecolor = fitcol(f[:i]), titlealign = :left)
        x = [p.periodH for p in r.pg.points]
        lines!(a2, x, [p.value for p in r.pg.points]; color = fitcol(f[:i]), linewidth = 1.3)
        lines!(a2, x, [p.crit for p in r.pg.points]; color = P.ink, linewidth = 0.7, linestyle = :dash)
        pk = r.pg.peak
        pk !== nothing && text!(a2, pk.periodH, pk.value; text = @sprintf(" %.2f h", pk.periodH), align = (:left, :bottom), fontsize = 10)
    end
    return pg
end

function draw_wavelet!(gp::GridLayout, D::Derived, O, W)
    P = palette(D.S)
    td = W.tMin ./ 1440
    ax = Axis(gp[1, 1]; xlabel = "Recording day", ylabel = "Period (h)", title = "Morlet wavelet power (ridge = black, outside the cone shaded)",
              titlealign = :left, titlesize = 12)
    hm = heatmap!(ax, td, W.periods, permutedims(W.power); colormap = :viridis)
    Colorbar(gp[1, 2], hm; label = "power / variance", width = 10)
    rid = [W.valid[i] ? W.ridge[i] : NaN for i in eachindex(W.ridge)]
    lines!(ax, td, rid; color = :black, linewidth = 1.4)
    pmax = W.periods[end]
    band!(ax, td, min.(W.coi, pmax), fill(pmax, length(td)); color = (:white, 0.55))
    for f in O.fits
        (f[:fit] === nothing || f[:kMin] === nothing) && continue
        a = rowstart(D, f[:kMin]) * D.binMin / 1440; b = rowstart(D, f[:kMax] + 1) * D.binMin / 1440
        lines!(ax, [a, b], [f[:fit].slope, f[:fit].slope]; color = fitcol(f[:i]), linewidth = 2, linestyle = :dash)
    end
    ax.xtickformat = v -> [string(Int(round(x)) + 1) for x in v]
    ylims!(ax, W.periods[1], pmax)
end

function draw_profile!(gp::GridLayout, D::Derived, O)
    P = palette(D.S)
    pr = range_profile(D)
    ax = Axis(gp[1, 1]; xlabel = @sprintf("Hours from the start of the row (period %.2f h)", pr.PH), ylabel = "Mean counts / bin",
              title = "Activity profile · rows $(D.k1 + 1)–$(D.k2 + 1)", titlealign = :left, titlesize = 12)
    plot_profile!(ax, pr.x, pr.mean, pr.sem, pr.light, D.binMin / 60, P.accent, P)
    fps = filter(!isnothing, [fold_fit(D, O, f) for f in O.fits])
    for (i, r) in enumerate(fps)
        a = Axis(gp[1+i, 1]; xlabel = axis_label(r), title = @sprintf("%s · %.2f h (%s)", r.f[:name], r.P, r.src), titlecolor = fitcol(r.f[:i]),
                 titlealign = :left, titlesize = 10)
        plot_profile!(a, r.x .- r.span / length(r.x) / 2, r.mean, r.sem, r.light, r.span / length(r.x), fitcol(r.f[:i]), P)
    end
    return pr, fps
end
function plot_profile!(ax, x, m, sem, lf, width, c, P)
    hi = maximum(map(v -> isnan(v) ? 0.0 : v, m .+ map(v -> isnan(v) ? 0.0 : v, sem))) * 1.08 + 1e-9
    sh = [i for i in eachindex(x) if !isnan(lf[i])]
    isempty(sh) || poly!(ax, [Rect2f(x[i], 0, width, hi) for i in sh]; color = [lf[i] >= 0.5 ? P.light : P.dark for i in sh], strokewidth = 0)
    xm = x .+ width / 2
    ok = .!isnan.(m) .& .!isnan.(sem)
    any(ok) && band!(ax, xm[ok], (m .- sem)[ok], (m .+ sem)[ok]; color = (c, 0.25))
    lines!(ax, xm, m; color = c, linewidth = 1.3)
    limits!(ax, x[1], x[end] + width, 0, hi)
end

function draw_phase!(gp::GridLayout, D::Derived, O)
    P = palette(D.S); unit = D.S.ana[:phaseUnit]
    rows = phase_summary(D, O)
    ax = Axis(gp[1, 1]; aspect = DataAspect(), title = "Phase (clock: 0 at the top, clockwise)", titlesize = 11, titlealign = :left)
    hidedecorations!(ax); hidespines!(ax)
    θ = range(0, 2π; length = 200)
    lines!(ax, cos.(θ), sin.(θ); color = P.line)
    for (lab, a) in (("0", 0), ("6", 90), ("12", 180), ("18", 270))
        text!(ax, 1.15sind(a), 1.15cosd(a); text = unit == "deg" ? string(parse(Int, lab) * 15, "°") : lab, align = (:center, :center), fontsize = 10, color = P.muted)
    end
    n = max(1, length(rows))
    for (i, r) in enumerate(rows)
        f = r.f; c = fitcol(f[:i]); rr = 1 - (i - 1) * 0.6 / n
        ds = [o[:phase].deg for o in f[:members] if o[:phase] !== nothing && isfinite(o[:phase].deg)]
        isempty(ds) || scatter!(ax, rr .* sind.(ds), rr .* cosd.(ds); color = c, markersize = 6)
        if r.cs !== nothing
            lines!(ax, [0, r.cs.R * sind(r.cs.mean)], [0, r.cs.R * cosd(r.cs.mean)]; color = c, linewidth = 2.5)
        end
    end
    limits!(ax, -1.3, 1.3, -1.3, 1.3)
    a2 = Axis(gp[1, 2]; xlabel = "Actogram row", ylabel = unit == "deg" ? "Phase (°)" : "Phase (ZT / CT h)")
    for f in O.fits
        ks = [o[:k] + 1 for o in f[:members] if o[:phase] !== nothing && isfinite(o[:phase].deg)]
        ds = [o[:phase].deg for o in f[:members] if o[:phase] !== nothing && isfinite(o[:phase].deg)]
        unit == "deg" || (ds = ds ./ 15)
        isempty(ks) || scatter!(a2, ks, ds; color = fitcol(f[:i]), markersize = 7)
        if f[:phase] !== nothing && f[:kMin] !== nothing
            v = unit == "deg" ? f[:phase].deg : f[:phase].deg / 15
            lines!(a2, [f[:kMin] + 1, f[:kMax] + 1], [v, v]; color = fitcol(f[:i]), linestyle = :dash)
        end
    end
    ylims!(a2, 0, unit == "deg" ? 360 : 24)
    a2.yticks = unit == "deg" ? (0:45:360) : (0:3:24)
    colsize!(gp, 1, Relative(0.35))
    return rows
end

function draw_daily!(gp::GridLayout, D::Derived)
    P = palette(D.S); rs = daily_rows(D)
    ax = Axis(gp[1, 1]; xlabel = "Actogram row", ylabel = "Counts per row", title = "Daily activity (light / dark)", titlealign = :left, titlesize = 12)
    x = [r.k + 1 for r in rs]
    barplot!(ax, x, [r.dark + r.light for r in rs]; color = P.ldLight, strokecolor = P.muted, strokewidth = 0.3, label = "in light")
    barplot!(ax, x, [r.dark for r in rs]; color = P.ldDark, label = "in dark")
    axislegend(ax; position = :rt, framevisible = false, labelsize = 10)
end

function draw_light!(gp::GridLayout, D::Derived)
    F = D.F; P = palette(D.S)
    ax = Axis(gp[1, 1]; xlabel = "Recording day", ylabel = "Light", title = @sprintf("Light record (threshold %g)", F.luxThr), titlealign = :left, titlesize = 12)
    D.B.lux === nothing && return
    t = [(b + 0.5) * D.binMin / 1440 for b in 0:D.B.n-1]
    lines!(ax, t, D.B.lux; color = P.ldLight, linewidth = 1)
    hlines!(ax, [F.luxThr]; color = P.onset, linestyle = :dash)
    ax.xtickformat = v -> [string(Int(round(x)) + 1) for x in v]
end

# =============================================================================
# Desktop window (GLMakie)
# =============================================================================

mutable struct App
    S::Session
    fig::Any
    ax::Any
    axp::Any
    anagl::Any
    geom::Any
    status::Observable{String}
    summary::Observable{String}
    info::Observable{String}
    sched_text::Observable{String}
    prev::Observable{Vector{Point2f}}
    w::Dict{Symbol,Any}
    busy::Bool
    drag::Any
    resetview::Bool
    selcond::Int
    tab::String
    right::Any                       # right-hand layout (actogram above, analysis below)
    bigana::Bool                     # analysis area enlarged
    detached::Vector{Dict{Symbol,Any}}   # analysis views opened in their own windows
end

const TABS = ["Periodogram", "Wavelet", "Activity profile", "Onsets & fits", "Phase", "Daily activity", "Lighting record", "All files", "File info"]

opts(labels, values) = collect(zip(labels, values))
optval(o) = o isa Tuple ? o[2] : o
function setmenu!(m, v)
    vals = [optval(o) for o in m.options[]]
    i = findfirst(==(v), vals)
    i === nothing || m.i_selected[] == i || (m.i_selected[] = i)
end
function settb!(tb, s)
    s = string(s)
    tb.stored_string[] == s || (tb.displayed_string[] = s; tb.stored_string[] = s)
end
gstr(v) = (v == round(v) && abs(v) < 1e6) ? string(Int(round(v))) : @sprintf("%g", v)

function clear_layout!(gl)
    for c in copy(contents(gl))
        delete!(c)
    end
    trim!(gl)
    nr, nc = gl.nrows, gl.ncols
    for i in 1:nc; colsize!(gl, i, Auto()); end
    for i in 1:nr; rowsize!(gl, i, Auto()); end
end

msg!(app, s) = (app.status[] = s)

"Open the desktop window; paths are data files (or folders) to load."
function run_gui(paths = String[])
    if !isdefined(@__MODULE__, :GLMakie)
        @eval import GLMakie                      # loaded only for the window (batch mode needs no OpenGL)
        return Base.invokelatest(run_gui, paths)
    end
    GLMakie.activate!(; title = APP_NAME)
    fig = Figure(; size = (1640, 1000), fontsize = 11, backgroundcolor = :white)
    app = App(Session(), fig, nothing, nothing, nothing, nothing, Observable(""), Observable(""), Observable(""), Observable(""),
              Observable(Point2f[]), Dict{Symbol,Any}(), false, nothing, true, 1, "Periodogram", nothing, false, Dict{Symbol,Any}[])
    build_gui!(app)
    for p in paths
        load_path!(app, p)
    end
    isempty(app.S.files) && add!(app.S, load_demo("ld-dd"), true)
    refresh!(app)
    screen = display(fig)
    wait(screen)
    return app
end

function load_path!(app, p)
    p = expanduser(strip(p))
    files = isdir(p) ? sort(filter(f -> occursin(r"\.(csv|txt|tsv|awd)$"i, f), readdir(p; join = true))) : [p]
    notes = String[]
    for f in files
        try
            rec = load_file(f)
            add!(app.S, rec)
            startswith(rec.encoding, "UTF") || push!(notes, "$(basename(f)): read as $(rec.encoding) (not UTF-8)")
        catch e
            push!(notes, "$(basename(f)): " * sprint(showerror, e))
        end
    end
    app.resetview = true
    isempty(notes) || msg!(app, join(notes, " · "))
end

function build_gui!(app::App)
    fig = app.fig; S = app.S; w = app.w
    left = fig[1, 1] = GridLayout(; valign = :top, tellheight = false)
    right = fig[1, 2] = GridLayout()
    colsize!(fig.layout, 1, Fixed(430))
    H = 22
    r = Ref(0)
    nextrow() = (r[] += 1)
    head(t) = (left[nextrow(), 1:4] = Label(fig, t; font = :bold, halign = :left, fontsize = 13, padding = (0, 0, 0, 6)))
    lab(t) = Label(fig, t; halign = :left, fontsize = 11)
    menu(o; kw...) = Menu(fig; options = o, height = H, fontsize = 11, kw...)
    tbox(s; width = 90, placeholder = "") = isempty(s) ? Textbox(fig; placeholder = placeholder, height = H, width = width, fontsize = 11, halign = :left) :
        Textbox(fig; stored_string = s, displayed_string = s, height = H, width = width, fontsize = 11, halign = :left)
    btn(t) = Button(fig; label = t, height = H, fontsize = 11, padding = (6, 6, 2, 2))
    function pair!(l1, w1, l2 = nothing, w2 = nothing)
        i = nextrow()
        left[i, 1] = lab(l1); left[i, 2] = w1
        l2 === nothing || (left[i, 3] = lab(l2); left[i, 4] = w2)
    end
    function wide!(l1, w1)
        i = nextrow(); left[i, 1] = lab(l1); left[i, 2:4] = w1
    end
    function buttons!(names)
        i = nextrow(); g = left[i, 1:4] = GridLayout(; halign = :left)
        bs = [g[1, j] = btn(n) for (j, n) in enumerate(names)]
        return bs
    end

    left[nextrow(), 1:4] = Label(fig, APP_NAME; font = :bold, fontsize = 16, halign = :left)
    head("Recordings")
    w[:path] = tbox(""; width = 300, placeholder = "file or folder path (CSV / TXT / AWD)")
    i = nextrow(); left[i, 1:3] = w[:path]; left[i, 4] = (w[:open] = btn("Open"))
    bs = buttons!([last(p).label for p in DEMOS])
    for (b, (key, _)) in zip(bs, DEMOS)
        on(b.clicks) do _
            add!(S, load_demo(key), true); app.resetview = true; refresh!(app)
        end
    end
    w[:files] = menu(opts(["(none)"], [0]))
    i = nextrow(); left[i, 1:3] = w[:files]; left[i, 4] = (w[:remove] = btn("Remove"))
    left[nextrow(), 1:4] = Label(fig, "Demo records are simulated (seeded model), not real animals."; halign = :left, fontsize = 10, color = :gray40)

    head("Actogram")
    w[:bin] = menu(opts(["1×"], [1])); w[:mult] = menu(opts(["Single", "Double", "Triple"], [1, 2, 3]))
    pair!("Bin size", w[:bin], "Plot", w[:mult])
    w[:T] = tbox("24"); w[:offsetH] = tbox("0")
    pair!("Row period (h)", w[:T], "Row start (h)", w[:offsetH])
    w[:style] = menu(opts(["Bars", "Heat map (grey)", "Heat map (viridis)", "Heat map (magma)", "Heat map (cividis)"], ["bars", "heat", "viridis", "magma", "cividis"]))
    w[:ldCol] = menu(opts(["Yellow / grey-blue", "White / light grey"], ["colour", "grey"]))
    pair!("Style", w[:style], "Light / dark", w[:ldCol])
    w[:gain] = Slider(fig; range = -2:0.1:2, startvalue = 0.0, height = H)
    wide!("Gain", w[:gain])
    w[:shade] = Toggle(fig; active = true, halign = :left); w[:showOn] = Toggle(fig; active = true, halign = :left)
    pair!("Shade light / dark", w[:shade], "Onsets and fits", w[:showOn])
    w[:showOff] = Toggle(fig; active = false, halign = :left)
    w[:rightPanel] = menu(opts(["None", "Daily total (bars)", "Daily total (line)", "Daily total (numbers)"], ["none", "bars", "line", "numbers"]))
    pair!("Offsets", w[:showOff], "Right panel", w[:rightPanel])
    w[:ldLine] = menu(opts(["Mean phase (ZT)", "Regression line"], ["mean", "regression"]))
    w[:floor] = tbox("0")
    pair!("LD fit line", w[:ldLine], "Hide below", w[:floor])

    head("Lighting")
    w[:src] = menu(opts(["Recorded light", "Schedule", "None"], ["recorded", "schedule", "none"]))
    w[:thr] = tbox("10")
    pair!("Source", w[:src], "Lights-on thr.", w[:thr])
    w[:cond] = menu(opts(["1"], [1])); w[:extend] = Toggle(fig; active = true, halign = :left)
    pair!("Condition", w[:cond], "Extend first", w[:extend])
    w[:cmode] = menu(opts(["LD", "LL", "DD"], ["LD", "LL", "DD"])); w[:cfirst] = menu(opts(["Light first", "Dark first"], ['L', 'D']))
    pair!("Mode", w[:cmode], "Begins with", w[:cfirst])
    w[:cL] = tbox("12:00"); w[:cD] = tbox("12:00")
    pair!("L (h:mm)", w[:cL], "D (h:mm)", w[:cD])
    w[:cstart] = tbox(""; width = 200, placeholder = "YYYY-MM-DD HH:MM")
    wide!("Starts at", w[:cstart])
    w[:cands] = menu(opts(["(no suggestions)"], [NaN]))
    wide!("Suggested", w[:cands])
    b_add, b_rem, b_det = buttons!(["Add condition", "Remove condition", "Use detected"])
    left[nextrow(), 1:4] = Label(fig, app.sched_text; halign = :left, fontsize = 10, color = :gray30, justification = :left)

    head("Analysis range and onsets")
    w[:d1] = tbox("1"); w[:d2] = tbox("9999")
    pair!("From row", w[:d1], "To row", w[:d2])
    w[:presets] = menu(opts(["All rows"], [(1, 9999)]))
    wide!("Presets", w[:presets])
    w[:templateH] = tbox("6"); w[:searchH] = tbox("4")
    pair!("Template (h)", w[:templateH], "Search ± (h)", w[:searchH])
    w[:minContrast] = tbox("20"); w[:guideTolH] = tbox("2")
    pair!("Min. contrast (%)", w[:minContrast], "Line tol. ± (h)", w[:guideTolH])

    head("Regression fits")
    w[:fit] = menu(opts(["Fit 1"], [1]))
    wide!("Active fit", w[:fit])
    b_addfit, b_split, b_reset = buttons!(["Add fit", "One per condition", "Reset fits"])
    b_rmfit, b_clrline, b_clredit = buttons!(["Remove fit", "Clear line", "Clear onset edits"])

    head("Mouse and output")
    w[:tool] = menu(opts(["Zoom (drag a box; right-drag pans; Ctrl-click resets)", "Place onset (click; Shift-click deletes)",
                          "Select for fit (click or drag a box)", "Draw line (drag along the onsets)", "Analysis range (drag over rows)"],
                         ["zoom", "place", "select", "line", "range"]))
    wide!("Mouse", w[:tool])
    w[:outdir] = tbox(pwd(); width = 300)
    wide!("Save to folder", w[:outdir])
    save_bs = buttons!(["Save PNG", "Save SVG", "Save PDF", "Save EPS", "Save all CSV"])
    rowgap!(left, 4); colgap!(left, 6)

    # ----- right side -----
    right[1, 1] = Label(fig, app.summary; halign = :left, fontsize = 12, font = :bold, tellwidth = false)
    vb = right[2, 1] = GridLayout(; halign = :left)
    vb[1, 1] = (b_rv = btn("Reset view")); vb[1, 2] = (b_fr = btn("Fit analysis range"))
    vb[1, 3] = Label(fig, "Tip: scroll = zoom · right-drag = pan · Ctrl + click = reset (Zoom tool)"; fontsize = 10, color = :gray40)
    ag = right[3, 1] = GridLayout()
    app.ax = Axis(ag[1, 1]; yreversed = true, xaxisposition = :top, xlabel = "clock time (h)", xlabelsize = 11, yticklabelsize = 9,
                  xticklabelsize = 10, xgridvisible = false, ygridvisible = false)
    app.axp = Axis(ag[1, 2]; yreversed = true, title = "row total", titlesize = 9, xticksvisible = false, xticklabelsvisible = false,
                   yticksvisible = false, yticklabelsvisible = false, xgridvisible = false, ygridvisible = false, width = 110)
    linkyaxes!(app.ax, app.axp)
    ob = right[4, 1] = GridLayout(; halign = :left)
    w[:tab] = Menu(fig; options = TABS, default = 1, height = H, width = 150, fontsize = 11)
    w[:method] = Menu(fig; options = opts(["χ² (Sokolove–Bushell)", "Lomb–Scargle"], ["chi", "ls"]), height = H, width = 160, fontsize = 11)
    w[:pmin] = tbox("18"; width = 50); w[:pmax] = tbox("30"; width = 50)
    w[:alpha] = Menu(fig; options = opts(["α 0.05", "α 0.01", "α 0.001"], [0.05, 0.01, 0.001]), height = H, width = 80, fontsize = 11)
    w[:fold] = Menu(fig; options = opts(["Fold: row period", "Fold: periodogram peak", "Fold: custom"], ["row", "peak", "custom"]), height = H, width = 170, fontsize = 11)
    w[:foldCustom] = tbox("24"; width = 50)
    w[:phaseUnit] = Menu(fig; options = opts(["ZT / CT (h)", "Degrees"], ["ztct", "deg"]), height = H, width = 100, fontsize = 11)
    w[:phaseRef] = Menu(fig; options = opts(["0°: projected ZT0 of preceding LD", "0°: start of the condition", "0°: 00:00 of day 1", "0°: start of row 1"],
                                             ["ld", "start", "rec", "row0"]), height = H, width = 230, fontsize = 11)
    w[:wData] = Menu(fig; options = opts(["Wavelet: analysis range", "Wavelet: whole record"], ["range", "all"]), height = H, width = 170, fontsize = 11)
    w[:encoding] = Menu(fig; options = [n for (_, n) in TEXT_ENCODINGS], height = H, width = 120, fontsize = 11)
    ob[1, 1] = w[:tab]; ob[1, 2] = w[:method]; ob[1, 3] = Label(fig, "periods"); ob[1, 4] = w[:pmin]; ob[1, 5] = Label(fig, "to"); ob[1, 6] = w[:pmax]
    ob[1, 7] = w[:alpha]; ob[1, 8] = w[:fold]; ob[1, 9] = w[:foldCustom]
    ob[2, 2] = w[:phaseUnit]; ob[2, 3:6] = w[:phaseRef]; ob[2, 7:8] = w[:wData]
    ob[2, 9] = Label(fig, "Encoding"); ob[2, 10] = w[:encoding]
    ob[1, 10] = (b_savefig = btn("Save figure (PNG)")); ob[1, 11] = (b_savecsv = btn("Save table (CSV)"))
    ob[2, 11] = (b_window = btn("Open in new window")); ob[2, 12] = (b_big = btn("Larger analysis area"))
    app.right = right
    app.anagl = right[5, 1] = GridLayout()
    right[6, 1] = Label(fig, app.info; halign = :left, justification = :left, fontsize = 10, tellwidth = false)
    right[7, 1] = Label(fig, app.status; halign = :left, fontsize = 10, color = :gray30, tellwidth = false)
    rowsize!(right, 3, Relative(0.47)); rowsize!(right, 5, Relative(0.30))

    # ----- callbacks -----
    guard(f) = (args...) -> (app.busy || f(args...); nothing)
    F() = current(S)
    numcb(key, store) = on(guard(s -> begin
        v = tryparse(Float64, strip(s)); F() === nothing && return
        v === nothing ? msg!(app, "Please enter a number.") : (store(v); refresh!(app))
    end), w[key].stored_string)

    on(guard(_ -> (load_path!(app, something(w[:path].stored_string[], "")); refresh!(app))), w[:open].clicks)
    on(guard(v -> (1 <= v <= length(S.files) && v != S.cur) && (S.cur = v; app.resetview = true; refresh!(app))), w[:files].selection)
    on(guard(_ -> begin
        F() === nothing && return
        deleteat!(S.files, S.cur); S.cur = min(S.cur, length(S.files)); app.resetview = true; refresh!(app)
    end), w[:remove].clicks)
    on(guard(v -> (F() === nothing || (F().binMult = v; app.resetview = true; refresh!(app)))), w[:bin].selection)
    for key in (:mult, :style, :ldCol, :rightPanel, :ldLine)
        on(guard(v -> (S.disp[key] = v; key == :mult && (app.resetview = true); refresh!(app))), w[key].selection)
    end
    numcb(:T, v -> (S.disp[:T] = clamp(v, 1, 48); app.resetview = true))
    numcb(:offsetH, v -> (S.disp[:offsetH] = modpos(v, 24); app.resetview = true))
    numcb(:floor, v -> S.disp[:floor] = max(0, v))
    on(guard(v -> (S.disp[:gainLog] = v; refresh!(app; tabs = false))), w[:gain].value)
    for key in (:shade, :showOn, :showOff)
        on(guard(v -> (S.disp[key] = v; refresh!(app; tabs = false))), w[key].active)
    end
    on(guard(v -> (F() === nothing || (F().lightSrc = v; refresh!(app)))), w[:src].selection)
    numcb(:thr, v -> F().luxThr = v)
    on(guard(v -> (F() === nothing || (F().schedExtend = v; refresh!(app)))), w[:extend].active)
    on(guard(v -> (app.selcond = v; refresh!(app; tabs = false))), w[:cond].selection)
    function edit_cond!(f)
        E = F(); (E === nothing || E.lightSrc != "schedule") && return
        sched = E.schedule; i = clamp(app.selcond, 1, length(sched))
        f(sched, i)
        E.schedule = normalize_schedule(sched)
        refresh!(app)
    end
    on(guard(v -> edit_cond!((s, i) -> begin
        s[i].mode = v
        (v == "LD" && s[i].L + s[i].D <= 0) && (s[i].L = 720; s[i].D = 720)
        snap_start!(F(), s, i) && msg!(app, "Start moved to the nearest " * (start_kind(s[i]) == :on ? "end of D (lights on)" : "end of L (lights off)"))
    end)), w[:cmode].selection)
    on(guard(v -> edit_cond!((s, i) -> (s[i].first = v; snap_start!(F(), s, i)))), w[:cfirst].selection)
    for (key, fld_) in ((:cL, :L), (:cD, :D))
        on(guard(str -> edit_cond!((s, i) -> begin
            m = parse_hm(str)
            m === nothing ? msg!(app, "Use h:mm, e.g. 12:00") : setfield!(s[i], fld_, Float64(m))
        end)), w[key].stored_string)
    end
    on(guard(str -> edit_cond!((s, i) -> begin
        t = parse_abs(F().rec, str)
        t === nothing ? msg!(app, "Use YYYY-MM-DD HH:MM, e.g. 2026-03-15 19:00") : (s[i].start = t)
    end)), w[:cstart].stored_string)
    on(guard(v -> (v isa Number && isfinite(v)) && edit_cond!((s, i) -> (s[i].start = v))), w[:cands].selection)
    on(guard(_ -> (F() === nothing || F().lightSrc != "schedule") || (add_condition!(F()); app.selcond = length(F().schedule); refresh!(app))), b_add.clicks)
    on(guard(_ -> edit_cond!((s, i) -> length(s) > 1 && (deleteat!(s, i); app.selcond = max(1, i - 1)))), b_rem.clicks)
    on(guard(_ -> begin
        E = F(); (E === nothing || E.rec.light === nothing) && return
        segs = detect_lighting(E.rec, E.luxThr).segments
        isempty(segs) && return
        E.schedule = segments_to_schedule(segs, E.rec, E.luxThr); E.lightSrc = "schedule"
        msg!(app, "Schedule copied from the light column"); refresh!(app)
    end), b_det.clicks)
    numcb(:d1, v -> (F().d1 = max(1, Int(round(v)))))
    numcb(:d2, v -> (F().d2 = max(1, Int(round(v)))))
    on(guard(v -> (E = F(); E === nothing || v === nothing || (E.d1 = v[1]; E.d2 = v[2]; refresh!(app)))), w[:presets].selection)
    for (key, lo, hi) in ((:templateH, 0.5, 12), (:searchH, 0.5, 12), (:minContrast, 0, 100), (:guideTolH, 0.25, 8))
        numcb(key, v -> (S.ana[key] = clamp(v, lo, hi)))
    end
    on(guard(v -> (F() === nothing || (F().activeFit = v; refresh!(app; tabs = false)))), w[:fit].selection)
    on(guard(_ -> begin
        E = F(); E === nothing && return
        length(E.fits) >= MAX_FITS && return msg!(app, "Up to $MAX_FITS fits.")
        D = derive(S, E); freeze_auto_fits!(D, onsets(D))
        push!(E.fits, newfit("Fit $(length(E.fits) + 1)"; auto = false)); E.activeFit = length(E.fits)
        S.tool = "select"; setmenu!(w[:tool], "select")
        msg!(app, "Click or drag over onsets to add them to the new fit, or draw a line along them."); refresh!(app)
    end), b_addfit.clicks)
    on(guard(_ -> (F() === nothing || (F().condFits = true; F().condKey = nothing; refresh!(app)))), b_split.clicks)
    on(guard(_ -> begin
        E = F(); E === nothing && return
        E.condFits = false; E.condKey = nothing; E.fits = [newfit("Fit 1")]; E.activeFit = 1; refresh!(app)
    end), b_reset.clicks)
    on(guard(_ -> begin
        E = F(); (E === nothing || length(E.fits) < 2) && return
        D = derive(S, E); freeze_auto_fits!(D, onsets(D))
        deleteat!(E.fits, E.activeFit)
        for (i, f) in enumerate(E.fits)
            f[:name] = replace(f[:name], r"^Fit \d+" => "Fit $i")
        end
        E.activeFit = 1; refresh!(app)
    end), b_rmfit.clicks)
    on(guard(_ -> begin
        E = F(); E === nothing && return
        E.fits[E.activeFit][:guide] = nothing; msg!(app, "Line removed; the fit keeps its rows."); refresh!(app)
    end), b_clrline.clicks)
    on(guard(_ -> (F() === nothing || (empty!(F().manual); refresh!(app)))), b_clredit.clicks)
    on(guard(v -> begin
        S.tool = v
        v == "zoom" ? activate_interaction!(app.ax, :rectanglezoom) : deactivate_interaction!(app.ax, :rectanglezoom)
        (v in ("place", "select", "line") && !S.disp[:showOn]) && (S.disp[:showOn] = true; refresh!(app; tabs = false))
    end), w[:tool].selection)
    for (b, fmt) in zip(save_bs[1:4], ("png", "svg", "pdf", "eps"))
        on(guard(_ -> save_actogram(app, fmt)), b.clicks)
    end
    on(guard(_ -> save_all_csv(app)), save_bs[5].clicks)
    on(guard(_ -> (app.resetview = true; refresh!(app; tabs = false))), b_rv.clicks)
    on(guard(_ -> begin
        E = F(); (E === nothing || app.geom === nothing) && return
        D = derive(S, E)
        limits!(app.ax, 0, app.geom.xmax, D.k1 - D.kStart, D.k2 - D.kStart + 1)
    end), b_fr.clicks)
    on(guard(v -> (app.tab = v; render_tab!(app))), w[:tab].selection)
    for key in (:method, :alpha, :fold, :phaseUnit, :phaseRef, :wData)
        on(guard(v -> (S.ana[key] = v; refresh!(app))), w[key].selection)
    end
    numcb(:pmin, v -> S.ana[:pmin] = clamp(v, 1, 100))
    numcb(:pmax, v -> S.ana[:pmax] = clamp(v, 2, 200))
    numcb(:foldCustom, v -> S.ana[:foldCustom] = clamp(v, 1, 100))
    on(guard(v -> reencode!(app, v)), w[:encoding].selection)
    on(guard(_ -> save_tab_figure(app)), b_savefig.clicks)
    on(guard(_ -> open_window!(app)), b_window.clicks)
    on(guard(_ -> begin
        app.bigana = !app.bigana
        set_analysis_size!(app)
        b_big.label = app.bigana ? "Larger actogram" : "Larger analysis area"
    end), b_big.clicks)
    on(guard(_ -> save_tab_csv(app)), b_savecsv.clicks)
    setup_mouse!(app)
end

# ---------- refresh ----------

function refresh!(app::App; tabs = true)
    S = app.S; w = app.w; F = current(S)
    app.busy = true
    try
        if F === nothing
            w[:files].options[] = opts(["(none)"], [0])
            app.summary[] = "Load a data file (CSV or TXT) or a demo recording to begin."
            empty!(app.ax); empty!(app.axp)
            return
        end
        w[:files].options[] = opts(["$(i). $(E.rec.name)  ($(gstr(E.rec.intervalMin)) min$(E.synthetic ? ", demo" : ""))" for (i, E) in enumerate(S.files)], collect(1:length(S.files)))
        setmenu!(w[:files], S.cur)
        n = max_bin_mult(F); effective_bin(F)
        w[:bin].options[] = opts(["$(m)× · $(gstr(m * F.rec.intervalMin)) min" for m in 1:n], collect(1:n))
        setmenu!(w[:bin], F.binMult)
        for key in (:mult, :style, :ldCol, :rightPanel, :ldLine)
            setmenu!(w[key], S.disp[key])
        end
        settb!(w[:T], gstr(S.disp[:T])); settb!(w[:offsetH], gstr(S.disp[:offsetH])); settb!(w[:floor], gstr(S.disp[:floor]))
        setmenu!(w[:src], F.lightSrc); settb!(w[:thr], gstr(F.luxThr)); w[:extend].active[] = F.schedExtend
        for key in (:method, :alpha, :fold, :phaseUnit, :phaseRef, :wData)
            setmenu!(w[key], S.ana[key])
        end
        for key in (:pmin, :pmax, :foldCustom, :templateH, :searchH, :minContrast, :guideTolH)
            settb!(w[key], gstr(S.ana[key]))
        end
        render_schedule!(app, F)
        D = derive(S, F)
        F.d1, F.d2 = D.k1 + 1, D.k2 + 1
        settb!(w[:d1], F.d1); settb!(w[:d2], F.d2)
        w[:presets].options[] = opts([p[1] for p in range_presets(D)], [(p[2], p[3]) for p in range_presets(D)])
        O = onsets(D)
        w[:fit].options[] = opts([@sprintf("%s%s%s · n %d · τ %s", f[:name], f[:auto] ? " (auto)" : "", f[:guide] === nothing ? "" : " (line)",
                                           length(f[:members]), f[:fit] === nothing ? "–" : @sprintf("%.2f h", f[:fit].slope)) for f in O.fits], collect(1:length(O.fits)))
        setmenu!(w[:fit], F.activeFit)
        F.rec.raw === nothing || setmenu!(w[:encoding], F.rec.encoding)
        pg = periodogram(D); R = F.rec
        app.summary[] = @sprintf("%s%s    start %s %s · %.1f d · sample %s min · light: %s · peak in range %s", R.name, F.synthetic ? " (simulated demo)" : "",
                                 fmt_date(R, R.t0Min), fmt_clock(R.t0Min), length(R.counts) * R.intervalMin / 1440, gstr(R.intervalMin), F.lightSrc,
                                 pg.peak === nothing ? "–" : @sprintf("%.2f h", pg.peak.periodH))
        draw_main!(app, D, O)
        tabs && render_tab!(app, D, O)
    catch e
        msg!(app, "Error: " * sprint(showerror, e))
        @error "refresh failed" exception = (e, catch_backtrace())
    finally
        app.busy = false
    end
end

function render_schedule!(app::App, F::Entry)
    w = app.w
    F.schedule = normalize_schedule(F.schedule)
    sched = F.schedule
    app.selcond = clamp(app.selcond, 1, length(sched))
    w[:cond].options[] = opts(["$i: $(c.mode) from $(fmt_abs(F.rec, c.start))" for (i, c) in enumerate(sched)], collect(1:length(sched)))
    setmenu!(w[:cond], app.selcond)
    c = sched[app.selcond]
    setmenu!(w[:cmode], c.mode); setmenu!(w[:cfirst], c.first)
    settb!(w[:cL], fmt_hm(c.L)); settb!(w[:cD], fmt_hm(c.D)); settb!(w[:cstart], fmt_abs(F.rec, c.start))
    cands = start_candidates(F, sched, app.selcond)
    kind = start_kind(c) == :on ? "end of D" : "end of L"
    w[:cands].options[] = isempty(cands) ? opts(["(no suggestions: the preceding condition is not LD)"], [NaN]) :
                          opts(["$(fmt_abs(F.rec, t)) · day $(Int(fld(t, 1440)) + 1) · $kind" for t in cands], cands)
    i = findfirst(==(c.start), cands)
    i === nothing || (w[:cands].i_selected[] = i)
    lines = String[]
    for (j, s) in enumerate(sched)
        push!(lines, s.mode == "LD" ? @sprintf("%d. LD %s / %s (T %s), %s first, from %s", j, fmt_hm(s.L), fmt_hm(s.D), fmt_hm(s.L + s.D),
                                               s.first == 'D' ? "dark" : "light", fmt_abs(F.rec, s.start)) :
                                      @sprintf("%d. %s from %s", j, s.mode == "DD" ? "DD (constant darkness)" : "LL (constant light)", fmt_abs(F.rec, s.start)))
    end
    app.sched_text[] = F.lightSrc == "schedule" ? join(lines, "\n") * "\nEach condition lasts until the next one starts." :
                       "Choose Source → Schedule to describe the light regime by hand."
end

function draw_main!(app::App, D::Derived, O)
    ax = app.ax
    lims = ax.finallimits[]
    show_panel = D.S.disp[:rightPanel] != "none"
    app.geom = draw_actogram!(ax, D, O; axp = show_panel ? app.axp : nothing)
    show_panel || empty!(app.axp)
    app.axp.width = show_panel ? 110 : 1
    app.axp.blockscene.visible[] = show_panel
    lines!(ax, app.prev; color = palette(D.S).accent, linewidth = 1.5, linestyle = :dash)
    if app.resetview
        limits!(ax, 0, app.geom.xmax, 0, app.geom.rows); app.resetview = false
    else
        o = lims.origin; s = lims.widths
        limits!(ax, o[1], o[1] + s[1], o[2], o[2] + s[2])
    end
end

# ---------- analysis tabs ----------

render_tab!(app::App) = (F = current(app.S); F === nothing || (D = derive(app.S, F); render_tab!(app, D, onsets(D))))

"Draw the current analysis view in the main window and refresh the views opened in their own windows."
function render_tab!(app::App, D::Derived, O)
    clear_layout!(app.anagl)
    app.info[] = join(draw_tab!(app, app.anagl, D, O, app.tab), "\n")
    refresh_detached!(app, D, O)
end

"Actogram above, analysis below: normal split or enlarged analysis area."
function set_analysis_size!(app::App)
    rowsize!(app.right, 3, Relative(app.bigana ? 0.24 : 0.47))
    rowsize!(app.right, 5, Relative(app.bigana ? 0.56 : 0.30))
end

"Open the current analysis view in a window of its own (large, resizable; follows the main window)."
function open_window!(app::App)
    F = current(app.S); F === nothing && return
    fig = Figure(; size = (1250, 900), fontsize = 13, backgroundcolor = :white)
    title = "$(app.tab) – $APP_NAME"
    screen = try
        GLMakie.Screen(; title = title)
    catch
        GLMakie.Screen()
    end
    d = Dict{Symbol,Any}(:fig => fig, :tab => app.tab, :screen => screen, :info => Observable(""))
    push!(app.detached, d)
    D = derive(app.S, F)
    draw_detached!(app, d, D, onsets(D))
    display(screen, fig)
    msg!(app, "$(app.tab) opened in a new window; it is updated when you change the recording or the settings.")
end

function draw_detached!(app::App, d, D::Derived, O)
    fig = d[:fig]
    empty!(fig)
    Label(fig[1, 1], @sprintf("%s · %s", d[:tab], D.F.rec.name); halign = :left, font = :bold, fontsize = 15, tellwidth = false)
    gl = fig[2, 1] = GridLayout()
    d[:info][] = join(draw_tab!(app, gl, D, O, d[:tab]), "\n")
    Label(fig[3, 1], d[:info]; halign = :left, justification = :left, fontsize = 12, tellwidth = false)
end

function refresh_detached!(app::App, D::Derived, O)
    filter!(d -> (try isopen(d[:screen]) catch; false end), app.detached)
    for d in app.detached
        try
            draw_detached!(app, d, D, O)
        catch e
            @error "window update failed" exception = (e, catch_backtrace())
        end
    end
end

"Draw analysis view t into layout gl; returns the summary lines shown below it."
function draw_tab!(app::App, gl, D::Derived, O, t)
    info = String[]
    try
        if t == "Periodogram"
            pg = draw_periodogram!(gl, D, O)
            for f in O.fits
                r = fit_periodogram(D, f); pk = r === nothing ? nothing : r.pg.peak
                push!(info, @sprintf("%-18s %-4s rows %-8s peak %s  τ from onsets %s", f[:name], f[:mode], f[:kMin] === nothing ? "–" : "$(f[:kMin] + 1)–$(f[:kMax] + 1)",
                                     pk === nothing ? "–" : @sprintf("%.2f h%s", pk.periodH, pk.significant ? "" : " (ns)"), f[:fit] === nothing ? "–" : @sprintf("%.3f h", f[:fit].slope)))
            end
        elseif t == "Wavelet"
            W = wavelet_data(D)
            if W === nothing
                Label(gl[1, 1], "Not enough data for the wavelet.")
            else
                draw_wavelet!(gl, D, O, W)
                for f in O.fits
                    f[:kMin] === nothing && continue
                    st = ridge_stats(W, (W.tMin .>= rowstart(D, f[:kMin]) * D.binMin) .& (W.tMin .< rowstart(D, f[:kMax] + 1) * D.binMin))
                    push!(info, @sprintf("%-18s ridge period %s (SD %s, %s h used)", f[:name], st === nothing ? "–" : f2(st.mean), st === nothing ? "–" : f2(st.sd), st === nothing ? "–" : f2(st.hours, 0)))
                end
            end
        elseif t == "Activity profile"
            pr, fps = draw_profile!(gl, D, O)
            npv = pr.np
            push!(info, @sprintf("Folding period %.2f h · %% activity in dark %s · IS %s · IV %s · RA %s", pr.PH, f2(pr.pctDark, 1),
                                 npv === nothing ? "–" : f2(npv.IS, 3), npv === nothing ? "–" : f2(npv.IV, 3), npv === nothing ? "–" : f2(npv.RA, 3)))
            for r in fps
                pk = r.peak
                push!(info, @sprintf("%-18s %.3f h (%s) · peak at %s · %% in dark %s", r.f[:name], r.P, r.src,
                                     pk === nothing ? "–" : @sprintf("%s %.1f", r.axis == "hours" ? "h" : r.axis, r.x[pk]), f2(r.pctDark, 1)))
            end
        elseif t == "Onsets & fits"
            draw_phase!(gl, D, O)
            for f in O.fits
                ft = f[:fit]; ph = f[:phase]
                push!(info, @sprintf("%-18s %-22s τ %s · r² %s · n %d · phase %s · excluded %d", f[:name], seg_label(O.segs, f[:segIndex]),
                                     ft === nothing ? "needs ≥ 2 onsets" : @sprintf("%.3f ± %s h", ft.slope, f2(ft.se, 3)), ft === nothing ? "–" : f2(ft.r2, 3),
                                     length(f[:members]), ph === nothing ? "–" : @sprintf("%s%.2f (%.1f°)", ph.label, ph.h, ph.deg), length(f[:excludedList])))
            end
            psis = [o[:psi] for o in O.list if D.k1 <= o[:k] <= D.k2 && o[:psi] !== nothing]
            push!(info, @sprintf("Phase angle ψ %s · onsets edited %d · the onset table is saved with Save table (CSV)",
                                 isempty(psis) ? "–" : @sprintf("%+.2f h (n %d)", mean(psis), length(psis)), count(o -> o[:manual], O.list)))
        elseif t == "Phase"
            rows = draw_phase!(gl, D, O)
            for r in rows
                cs = r.cs; f = r.f
                push!(info, @sprintf("%-18s %-4s phase %s · R %s · circ. SD %s h · Rayleigh p %s · 0°: %s", f[:name], f[:mode],
                                     f[:phase] === nothing ? "–" : @sprintf("%s%.2f", r.label, f[:phase].h), cs === nothing ? "–" : f2(cs.R, 3),
                                     (cs === nothing || !isfinite(cs.csdDeg)) ? "–" : f2(cs.csdDeg / 360 * r.Th), cs === nothing ? "–" : fmt_p(cs.p), r.ref))
            end
            _, diffs = phase_table(D, O)
            for d in diffs[2:end]
                push!(info, "Δ phase $(d[1]) → $(d[2]): $(d[3])° ($(d[4]) h)")
            end
        elseif t == "Daily activity"
            draw_daily!(gl, D)
        elseif t == "Lighting record"
            if D.F.rec.light === nothing
                Label(gl[1, 1], "This file has no light column. Use Lighting → Source → Schedule to describe the light regime by hand.")
            else
                draw_light!(gl, D)
                segs = detect_lighting(D.F.rec, D.F.luxThr).segments
                push!(info, "Detected: " * join([string(s[:mode], " days ", s[:startDay], "–", s[:endDay],
                    (s[:mode] == "LD" && s[:onMin] !== nothing && s[:offMin] !== nothing) ? " (on $(fmt_clock(s[:onMin])), off $(fmt_clock(s[:offMin])))" : "") for s in segs], "  ·  "))
            end
        elseif t == "All files"
            tb = batch_table(app.S)
            Label(gl[1, 1], join([join(string.(r[[1, 3, 5, 7, 8, 9, 11, 12, 13]]), "  |  ") for r in tb], "\n"); halign = :left, justification = :left, fontsize = 10)
        elseif t == "File info"
            R = D.F.rec
            e = R.t0Min + length(R.counts) * R.intervalMin
            lines = ["File: $(R.fileName)", "Recording name: $(R.name)", "Format: $(R.format == "CSV" ? "Timestamped table (CSV/TXT)" : R.format)",
                     "Text encoding: $(R.encoding)$(R.encodingAuto ? " (detected)" : "") – change it in the Encoding menu if the name looks garbled",
                     "Sampling interval: $(gstr(R.intervalMin)) min · samples $(length(R.counts)) · missing $(R.nMissing)",
                     "Recording: $(fmt_date(R, R.t0Min)) $(fmt_clock(R.t0Min)) → $(fmt_date(R, e)) $(fmt_clock(e)) ($(f2((e - R.t0Min) / 1440)) d)"]
            R.format != "CSV" && append!(lines, ["AWD header: " * join(["$k = $(R.header[k])" for k in ("name", "date", "time", "interval", "age", "id", "sex")], ", ")])
            append!(lines, ["Note: " * x for x in R.warnings])
            Label(gl[1, 1], join(lines, "\n"); halign = :left, justification = :left, fontsize = 11, tellwidth = false)
        end
    catch e
        push!(info, "Error: " * sprint(showerror, e))
        @error "tab failed" exception = (e, catch_backtrace())
    end
    return info
end

function tab_figure(app::App, D::Derived, O)
    fig = Figure(; size = (1100, 650), backgroundcolor = :white)
    gl = fig[1, 1] = GridLayout()
    t = app.tab
    t == "Periodogram" ? draw_periodogram!(gl, D, O) :
    t == "Wavelet" ? ((W = wavelet_data(D)) === nothing ? nothing : draw_wavelet!(gl, D, O, W)) :
    t == "Activity profile" ? draw_profile!(gl, D, O) :
    t in ("Phase", "Onsets & fits") ? draw_phase!(gl, D, O) :
    t == "Daily activity" ? draw_daily!(gl, D) :
    t == "Lighting record" ? draw_light!(gl, D) : return nothing
    return fig
end

function tab_csv(app::App, D::Derived, O)
    t = app.tab
    if t == "Periodogram"
        return periodogram_table(D, O), "periodogram"
    elseif t == "Wavelet"
        W = wavelet_data(D)
        return W === nothing ? nothing : wavelet_table(D, W), "wavelet"
    elseif t == "Activity profile"
        return profile_table(D, O), "profile"
    elseif t == "Onsets & fits"
        return onsets_table(D, O), "onsets"
    elseif t == "Phase"
        return phase_table(D, O)[1], "phase"
    elseif t == "Daily activity"
        return daily_table(D), "daily"
    elseif t == "Lighting record"
        return D.F.rec.light === nothing ? nothing : light_table(D.F), "lighting"
    elseif t == "All files"
        return batch_table(app.S), "all_files"
    end
    return nothing, ""
end

# ---------- saving ----------

outdir(app) = (d = expanduser(strip(something(app.w[:outdir].stored_string[], ""))); isempty(d) ? pwd() : d)
function unique_path(dir, stem, ext)
    p = joinpath(dir, "$stem.$ext"); i = 2
    while isfile(p)
        p = joinpath(dir, "$(stem)_$i.$ext"); i += 1
    end
    return p
end
function savefig(path, fig)
    ext = lowercase(splitext(path)[2])
    ext == ".png" ? save(path, fig; backend = CairoMakie, px_per_unit = 2) : save(path, fig; backend = CairoMakie)
end

function save_actogram(app::App, fmt)
    F = current(app.S); F === nothing && return
    D = derive(app.S, F)
    try
        mkpath(outdir(app))
        p = unique_path(outdir(app), safe_name(F.rec.name) * "_actogram", fmt)
        savefig(p, actogram_figure(D, onsets(D)))
        msg!(app, "Saved " * p)
    catch e
        msg!(app, "Could not save: " * sprint(showerror, e))
    end
end
function save_tab_figure(app::App)
    F = current(app.S); F === nothing && return
    D = derive(app.S, F)
    fig = tab_figure(app, D, onsets(D))
    fig === nothing && return msg!(app, "This tab has no figure.")
    mkpath(outdir(app))
    p = unique_path(outdir(app), safe_name(F.rec.name) * "_" * replace(lowercase(app.tab), r"[^a-z]+" => "_"), "png")
    savefig(p, fig); msg!(app, "Saved " * p)
end
function save_tab_csv(app::App)
    F = current(app.S); F === nothing && return
    D = derive(app.S, F)
    rows, stem = tab_csv(app, D, onsets(D))
    rows === nothing && return msg!(app, "This tab has no table.")
    mkpath(outdir(app))
    p = unique_path(outdir(app), (stem == "all_files" ? "all_files" : safe_name(F.rec.name) * "_" * stem), "csv")
    write(p, to_csv(rows)); msg!(app, "Saved " * p)
end
function save_all_csv(app::App)
    F = current(app.S); F === nothing && return
    D = derive(app.S, F); O = onsets(D); dir = outdir(app); mkpath(dir); st = safe_name(F.rec.name)
    n = write_tables(dir, st, D, O)
    msg!(app, "Saved $n CSV files to $dir")
end

function reencode!(app::App, name)
    F = current(app.S); (F === nothing || F.rec.raw === nothing || name == F.rec.encoding) && return
    R = F.rec
    try
        text, enc = decode_text(R.raw, name)
        rec = parse_any(text, R.fileName)
        (length(rec.counts) != length(R.counts) || rec.t0Min != R.t0Min) && error("the data changed; check the encoding")
        rec.encoding, rec.encodingAuto, rec.raw = enc, false, R.raw
        F.rec = rec; empty!(F.cache)
        msg!(app, "Read as $enc: $(rec.name)")
    catch e
        msg!(app, "Could not read the file as $name: " * sprint(showerror, e))
    end
    refresh!(app)
end

# ---------- mouse on the actogram ----------

function setup_mouse!(app::App)
    fig = app.fig; ax = app.ax
    on(events(fig).mousebutton; priority = 2) do ev
        (ev.button == Mouse.left && app.geom !== nothing && current(app.S) !== nothing) || return Consume(false)
        if ev.action == Mouse.press
            (is_mouseinside(ax.scene) && app.S.tool != "zoom") || return Consume(false)
            p = mouseposition(ax)
            app.drag = (x = Float64(p[1]), y = Float64(p[2]), shift = ispressed(fig, Keyboard.left_shift) || ispressed(fig, Keyboard.right_shift))
            return Consume(true)
        elseif ev.action == Mouse.release && app.drag !== nothing
            p = mouseposition(ax); d = app.drag
            app.drag = nothing; app.prev[] = Point2f[]
            try
                on_release!(app, d, Float64(p[1]), Float64(p[2]))
            catch e
                msg!(app, "Error: " * sprint(showerror, e))
            end
            return Consume(true)
        end
        return Consume(false)
    end
    on(events(fig).mouseposition; priority = 2) do _
        (app.geom === nothing || current(app.S) === nothing) && return Consume(false)
        p = mouseposition(ax); x, y = Float64(p[1]), Float64(p[2])
        d = app.drag
        if d !== nothing
            tool = app.S.tool
            app.prev[] = tool == "line" ? [Point2f(d.x, d.y), Point2f(x, y)] :
                         tool == "range" ? Point2f[(0, d.y), (app.geom.xmax, d.y), (app.geom.xmax, y), (0, y), (0, d.y)] :
                         Point2f[(d.x, d.y), (x, d.y), (x, y), (d.x, y), (d.x, d.y)]
            return Consume(true)
        end
        is_mouseinside(ax.scene) && hover!(app, x, y)
        return Consume(false)
    end
end

function hover!(app::App, x, y)
    F = current(app.S); D = derive(app.S, F)
    r = Int(floor(y)); (0 <= r < app.geom.rows && x >= 0) || return
    k = D.kStart + r; b = rowstart(D, k) + Int(floor(x / (D.binMin / 60))); t = b * D.binMin
    v = 0 <= b < D.B.n ? act(D, b) : NaN; st = 0 <= b < D.B.n ? lightat(D, b) : NaN
    msg!(app, @sprintf("Row %d · %s %s · activity %s · %s", k + 1, fmt_date(F.rec, t), fmt_clock(t),
                       isnan(v) ? "missing" : @sprintf("%.0f / %s min", v, gstr(D.binMin)), st == 1 ? "light" : st == 0 ? "dark" : "light unknown"))
end

function on_release!(app::App, d, x, y)
    S = app.S; F = current(S); D = derive(S, F); G = app.geom
    bh = D.binMin / 60
    lims = app.ax.finallimits[]
    moved = abs(x - d.x) > lims.widths[1] / 150 || abs(y - d.y) > 0.3
    row(v) = clamp(Int(floor(v)), 0, G.rows - 1)
    tool = S.tool
    if tool == "range"
        a, b = minmax(row(d.y), row(y))
        F.d1, F.d2 = D.kStart + a + 1, D.kStart + b + 1
        msg!(app, "Analysis range: rows $(F.d1)–$(F.d2)")
    elseif tool == "place"
        moved && return
        r = Int(floor(y)); (0 <= r < G.rows && x >= 0) || return
        place_onset!(D, D.kStart + r, Int(floor(x / bh)); delete = d.shift)
    elseif tool == "select"
        if !moved
            tolx = lims.widths[1] / 80
            best = nothing; bd = Inf
            for (mx, my, k) in G.markers
                dd = abs(mx - x) / tolx + abs(my - y) / 0.5
                (abs(mx - x) <= tolx && abs(my - y) <= 0.5 && dd < bd) && ((best, bd) = (k, dd))
            end
            best === nothing && return
            toggle_in_active_fit!(D, [best])
        else
            x0, x1 = minmax(d.x, x); y0, y1 = minmax(d.y, y)
            ks = sort(unique([k for (mx, my, k) in G.markers if x0 <= mx <= x1 && y0 <= my <= y1]))
            isempty(ks) && return
            toggle_in_active_fit!(D, ks)
        end
    elseif tool == "line"
        moved || return
        ka, kb = D.kStart + row(d.y), D.kStart + row(y)
        msg!(app, apply_guide!(D, (k = ka, t = (rowstart(D, ka) + d.x / bh) * D.binMin), (k = kb, t = (rowstart(D, kb) + x / bh) * D.binMin)))
    end
    refresh!(app)
end

# =============================================================================
# Command line (batch mode) and entry point
# =============================================================================

const HELP = """
ActogramApp.jl: plot and analyse activity rhythms and lighting records from timestamped activity tables (CSV or TXT).

  julia --project=. ActogramApp.jl [FILE|FOLDER ...]              open the desktop app (GLMakie)
  julia --project=. ActogramApp.jl --batch FILE ... --out DIR [options]

Batch options (defaults in brackets):
  --demo KEY            add a simulated demo: ld-dd, ld-ll, jetlag (repeatable)
  --out DIR             output folder [actogram_output]
  --formats LIST        figure formats: png,svg,pdf,eps [png]
  --encoding NAME       UTF-8, UTF-16LE, UTF-16BE, GB18030, Big5, Shift_JIS, EUC-JP, EUC-KR [detect]
  --bin N               bin size as a multiple of the sampling interval [1]
  --plot single|double|triple [double]      --period H row period [24]      --offset H row start [0]
  --style bars|heat|viridis|magma|cividis [bars]          --ld-colours colour|grey [colour]
  --gain X [1]          --no-shade          --offsets          --right-panel none|bars|line|numbers [none]
  --light recorded|schedule|none            --threshold X lights-on threshold [10]
  --schedule COND       e.g. 'LD@2026-03-01T07:00,L=12:00,D=12:00,first=L' or 'DD@2026-03-15T19:00' (repeatable)
  --rows A-B            analysis range in actogram rows [all]
  --method chi|ls [chi] --pmin H [18]  --pmax H [30]  --alpha 0.05|0.01|0.001 [0.01]
  --template H [6]      --search H [4]  --min-contrast PCT [20]  --phase-ref ld|start|rec|row0 [ld]
  --single-fit          one fit over all onsets instead of one per lighting condition
"""

function parse_args(args)
    o = Dict{String,Any}("files" => String[], "demo" => String[], "schedule" => String[], "batch" => false, "out" => "actogram_output",
                         "formats" => "png")
    flags = Set(["--batch", "--no-shade", "--offsets", "--single-fit", "--help", "-h"])
    i = 1
    while i <= length(args)
        a = args[i]
        if a in flags
            o[lstrip(a, '-')] = true
        elseif startswith(a, "--")
            i == length(args) && error("$a needs a value")
            key = a[3:end]; val = args[i+1]; i += 1
            key in ("demo", "schedule") ? push!(o[key], val) : (o[key] = val)
        else
            push!(o["files"], a)
        end
        i += 1
    end
    return o
end

function parse_schedule_arg(text, R::Recording)
    m = match(r"^\s*(LD|LL|DD)\s*@\s*(\d{4}-\d{1,2}-\d{1,2})[T ](\d{1,2}:\d{2})\s*(.*)$"i, text)
    m === nothing && error("cannot read schedule condition '$text'")
    t = parse_abs(R, "$(m[2]) $(m[3])")
    t === nothing && error("cannot read the date or time in '$text'")
    c = Cond(uppercase(m[1]), t, 'L', 720, 720)
    for part in filter(!isempty, strip.(split(m[4], ",")))
        k, v = strip.(split(part * "=", "=")[1:2])
        k = uppercase(k)
        if k in ("L", "D")
            mm = parse_hm(v); mm === nothing && error("cannot read $k=$v (use h:mm)")
            setfield!(c, Symbol(k), Float64(mm))
        elseif k == "FIRST"
            c.first = startswith(uppercase(v), "D") ? 'D' : 'L'
        end
    end
    return c
end

function apply_args!(S::Session, o)
    num(k, d) = haskey(o, k) ? parse(Float64, o[k]) : d
    d = S.disp; a = S.ana
    d[:mult] = Dict("single" => 1, "double" => 2, "triple" => 3)[get(o, "plot", "double")]
    d[:T] = clamp(num("period", 24.0), 1, 48); d[:offsetH] = modpos(num("offset", 0.0), 24)
    d[:style] = get(o, "style", "bars"); d[:ldCol] = get(o, "ld-colours", "colour")
    d[:gainLog] = log2(clamp(num("gain", 1.0), 0.25, 4)); d[:shade] = !get(o, "no-shade", false)
    d[:showOff] = get(o, "offsets", false); d[:rightPanel] = get(o, "right-panel", "none")
    haskey(o, "width") && (d[:widthPx] = Int(clamp(num("width", 900), 720, 2880)))
    a[:method] = get(o, "method", "chi"); a[:pmin] = num("pmin", 18.0); a[:pmax] = num("pmax", 30.0); a[:alpha] = num("alpha", 0.01)
    a[:templateH] = num("template", 6.0); a[:searchH] = num("search", 4.0); a[:minContrast] = num("min-contrast", 20.0)
    a[:phaseRef] = get(o, "phase-ref", "ld")
    for F in S.files
        F.binMult = Int(num("bin", 1.0)); F.luxThr = num("threshold", 10.0)
        if !isempty(o["schedule"])
            F.schedule = normalize_schedule([parse_schedule_arg(s, F.rec) for s in o["schedule"]]); F.lightSrc = "schedule"
        end
        haskey(o, "light") && (F.lightSrc = o["light"])
        if haskey(o, "rows")
            m = match(r"^\s*(\d+)\s*[-–:]\s*(\d+)\s*$", o["rows"])
            m === nothing && error("--rows: use A-B, e.g. 3-14")
            F.d1, F.d2 = parse(Int, m[1]), parse(Int, m[2])
        end
        get(o, "single-fit", false) && (F.condFits = false)
    end
end

"Write the CSV tables of one recording; returns the number of files."
function write_tables(dir, stem, D::Derived, O)
    n = 0
    wr(rows, name) = (write(joinpath(dir, "$(stem)_$name.csv"), to_csv(rows)); n += 1)
    wr(onsets_table(D, O), "onsets"); wr(fits_table(D, O), "fits"); wr(phase_table(D, O)[1], "phase")
    wr(daily_table(D), "daily"); wr(periodogram_table(D, O), "periodogram"); wr(profile_table(D, O), "profile")
    W = wavelet_data(D); W === nothing || wr(wavelet_table(D, W), "wavelet")
    D.F.rec.light === nothing || wr(light_table(D.F), "lighting")
    return n
end

function run_batch(S::Session, o)
    CairoMakie.activate!()
    out = o["out"]; mkpath(out)
    fmts = lowercase.(strip.(split(o["formats"], ",")))
    bad = setdiff(fmts, ["png", "svg", "pdf", "eps"])
    isempty(bad) || error("Unknown figure format: " * join(bad, ", "))
    nfiles = 0
    used = Set{String}()
    for F in S.files
        stem = safe_name(F.rec.name); base = stem; i = 2
        while stem in used
            stem = "$(base)_$i"; i += 1
        end
        push!(used, stem)
        D = derive(S, F); O = onsets(D)
        figs = Pair{String,Any}["actogram" => actogram_figure(D, O)]
        for (name, f) in (("periodogram", gl -> draw_periodogram!(gl, D, O)), ("profile", gl -> draw_profile!(gl, D, O)),
                          ("phase", gl -> draw_phase!(gl, D, O)), ("daily", gl -> draw_daily!(gl, D)))
            fig = Figure(; size = (1100, name == "profile" ? 700 : 520), backgroundcolor = :white)
            gl = fig[1, 1] = GridLayout(); f(gl); push!(figs, name => fig)
        end
        W = wavelet_data(D)
        if W !== nothing
            fig = Figure(; size = (1100, 520), backgroundcolor = :white); gl = fig[1, 1] = GridLayout(); draw_wavelet!(gl, D, O, W); push!(figs, "wavelet" => fig)
        end
        if F.rec.light !== nothing
            fig = Figure(; size = (1100, 320), backgroundcolor = :white); gl = fig[1, 1] = GridLayout(); draw_light!(gl, D); push!(figs, "lighting" => fig)
        end
        for (name, fig) in figs, fmt in fmts
            p = joinpath(out, "$(stem)_$name.$fmt")
            fmt == "png" ? save(p, fig; px_per_unit = 2) : save(p, fig)
            nfiles += 1
        end
        nfiles += write_tables(out, stem, D, O)
        pg = periodogram(D)
        fits = join([@sprintf("%s τ %.3f h (n %d)", f[:name], f[:fit].slope, length(f[:members])) for f in O.fits if f[:fit] !== nothing], "; ")
        println(@sprintf("%s: rows %d–%d · peak %s · %s", F.rec.name, D.k1 + 1, D.k2 + 1,
                         pg.peak === nothing ? "–" : @sprintf("%.2f h", pg.peak.periodH), isempty(fits) ? "no fit" : fits))
    end
    write(joinpath(out, "all_files.csv"), to_csv(batch_table(S))); nfiles += 1
    println("Wrote $nfiles files to $(abspath(out))")
end

function main(args = ARGS)
    o = parse_args(args)
    if get(o, "help", false) || get(o, "h", false)
        print(HELP); return
    end
    if !o["batch"]
        run_gui(o["files"]); return
    end
    S = Session()
    for k in o["demo"]
        add!(S, load_demo(k), true)
    end
    for p in o["files"]
        try
            add!(S, load_file(p; encoding = get(o, "encoding", nothing)))
        catch e
            println(stderr, p, ": ", sprint(showerror, e))
        end
    end
    isempty(S.files) && (println(stderr, "No recordings: give data files or --demo (see --help)."); exit(1))
    apply_args!(S, o)
    run_batch(S, o)
end

end # module

if abspath(PROGRAM_FILE) == @__FILE__
    ActogramApp.main(ARGS)
end
