/*! Actogram web app | Copyright (c) 2026 The Actogram Web Application Development Team | SPDX-License-Identifier: MIT */
/* ------------------------------------------------------------------
 * Core: parsing, binning, lighting and rhythm analyses.
 * Pure functions with no DOM access, so they can be tested in Node.
 * ------------------------------------------------------------------ */
(function (root) {
  "use strict";

  const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, sept: 8, oct: 9, nov: 10, dec: 11 };
  const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  /* ================================================================
   * Parsing
   * ================================================================ */

  /** Parse a date such as "21-nov-2000", "21-Nov-00", "2000-11-21" or "11/21/2000". */
  function parseDate(s) {
    s = String(s || "").trim();
    let m = s.match(/^(\d{1,2})[-\s/.]([A-Za-z]{3,4})[A-Za-z]*[-\s/.](\d{2,4})$/);
    if (m) {
      const mon = MONTHS[m[2].toLowerCase()];
      if (mon === undefined) return null;
      let y = +m[3];
      if (y < 100) y += y < 70 ? 2000 : 1900;
      return { y: y, m: mon, d: +m[1] };
    }
    m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
    if (m) return { y: +m[1], m: +m[2] - 1, d: +m[3] };
    m = s.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/);
    if (m) return { y: +m[3], m: +m[1] - 1, d: +m[2] }; // assume US month/day/year
    return null;
  }

  /** Parse "HH:MM" or "HH:MM:SS" (24-hour) into minutes after midnight. */
  function parseTime(s) {
    const m = String(s || "").trim().match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
    if (!m) return null;
    return +m[1] * 60 + +m[2] + (m[3] ? +m[3] / 60 : 0);
  }

  /**
   * Parse a text-based .AWD data file.
   * Header (7 lines): name, start date (dd-mon-yyyy), start time (HH:MM),
   * interval code (= minutes x 4), age, ID, sex. Then one sample per line:
   * "activity" or "activity,light". Values < 0 are missing data.
   * A trailing "M" token (seen in some actigraphy exports) is kept as a marker.
   */
  function parseAWD(text, fileName) {
    const lines = String(text).replace(/^﻿/, "").split(/\r\n|\r|\n/);
    const warnings = [];
    if (lines.length < 8) throw new Error("File is too short to be an AWD file (needs 7 header lines plus data).");
    const header = lines.slice(0, 7).map(function (l) { return l.trim(); });
    const date = parseDate(header[1]);
    if (!date) throw new Error('Line 2 should be a start date like "21-nov-2000"; found "' + header[1] + '".');
    const t0 = parseTime(header[2]);
    if (t0 === null) throw new Error('Line 3 should be a start time like "04:20"; found "' + header[2] + '".');
    const code = header[3];
    let intervalMin = null;
    if (/^\d+(\.\d+)?$/.test(code) && +code > 0) intervalMin = +code / 4;
    else warnings.push('Interval code "' + code + '" is not numeric; assuming 1-minute bins. Change it under File info if needed.');
    if (intervalMin === null) intervalMin = 1;

    const counts = [], light = [], marker = [];
    let hasLight = false, nMissing = 0, nMarker = 0, nBad = 0;
    for (let i = 7; i < lines.length; i++) {
      const raw = lines[i].trim();
      if (raw === "") continue;
      const tok = raw.split(/[\s,;]+/).filter(Boolean);
      let a = parseFloat(tok[0]);
      if (!isFinite(a)) { nBad++; a = -1; }
      let l = NaN, mk = 0;
      for (let j = 1; j < tok.length; j++) {
        if (/^m$/i.test(tok[j])) { mk = 1; continue; }
        const v = parseFloat(tok[j]);
        if (isFinite(v) && isNaN(l)) { l = v; hasLight = true; }
      }
      if (a < 0) { nMissing++; a = NaN; }
      if (mk) nMarker++;
      counts.push(a); light.push(l); marker.push(mk);
    }
    if (nBad) warnings.push(nBad + " data line(s) could not be read and were treated as missing.");
    if (counts.length === 0) throw new Error("No data lines found after the 7-line header.");
    return {
      fileName: fileName || header[0] || "recording",
      name: header[0] || fileName,
      format: "Text-based AWD",
      header: { name: header[0], date: header[1], time: header[2], interval: code, age: header[4], id: header[5], sex: header[6] },
      date: date,
      startUTC: Date.UTC(date.y, date.m, date.d),
      t0Min: t0,
      intervalMin: intervalMin,
      counts: Float64Array.from(counts),
      light: hasLight ? Float64Array.from(light) : null,
      marker: nMarker ? Uint8Array.from(marker) : null,
      nMissing: nMissing,
      nMarker: nMarker,
      warnings: warnings
    };
  }

  /**
   * Parse a simple CSV/TSV: timestamp, activity[, light]. Timestamps like
   * "2026-03-03 14:36" or "2026-03-03T14:36:00". Header rows are skipped.
   * The sampling interval is the median spacing between rows.
   */
  function parseCSV(text, fileName) {
    const lines = String(text).replace(/^﻿/, "").split(/\r\n|\r|\n/);
    const rows = [];
    for (const raw of lines) {
      const tok = raw.split(/[,;\t]/).map(function (t) { return t.trim().replace(/^"|"$/g, ""); });
      if (tok.length < 2) continue;
      const m = tok[0].match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
      if (!m) continue;
      const t = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], m[6] ? +m[6] : 0);
      const a = parseFloat(tok[1]);
      const l = tok.length > 2 ? parseFloat(tok[2]) : NaN;
      rows.push([t, isFinite(a) && a >= 0 ? a : NaN, l]);
    }
    if (rows.length < 3) throw new Error("No rows of the form timestamp,activity[,light] were found.");
    rows.sort(function (p, q) { return p[0] - q[0]; });
    const diffs = [];
    for (let i = 1; i < Math.min(rows.length, 2000); i++) diffs.push(rows[i][0] - rows[i - 1][0]);
    diffs.sort(function (p, q) { return p - q; });
    const dtMs = diffs[Math.floor(diffs.length / 2)];
    if (!(dtMs > 0)) throw new Error("Could not determine the sampling interval.");
    const t0 = rows[0][0];
    const n = Math.round((rows[rows.length - 1][0] - t0) / dtMs) + 1;
    const counts = new Float64Array(n).fill(NaN), light = new Float64Array(n).fill(NaN);
    let hasLight = false;
    for (const r of rows) {
      const i = Math.round((r[0] - t0) / dtMs);
      counts[i] = r[1]; light[i] = r[2];
      if (isFinite(r[2])) hasLight = true;
    }
    let nMissing = 0;
    for (let i = 0; i < n; i++) if (isNaN(counts[i])) nMissing++;
    const d0 = new Date(t0);
    const startUTC = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth(), d0.getUTCDate());
    return {
      fileName: fileName, name: fileName.replace(/\.[^.]+$/, ""), format: "CSV",
      header: { name: fileName, date: "", time: "", interval: "", age: "", id: "", sex: "" },
      date: { y: d0.getUTCFullYear(), m: d0.getUTCMonth(), d: d0.getUTCDate() },
      startUTC: startUTC, t0Min: (t0 - startUTC) / 60000, intervalMin: dtMs / 60000,
      counts: counts, light: hasLight ? light : null, marker: null, nMissing: nMissing, nMarker: 0, warnings: []
    };
  }

  function parseAny(text, fileName) {
    const first = String(text).slice(0, 400).split(/\r\n|\r|\n/);
    if (/^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d/.test((first[1] || "").trim()) || /^\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d/.test((first[0] || "").trim())) {
      return parseCSV(text, fileName);
    }
    return parseAWD(text, fileName);
  }

  /* ================================================================
   * Time helpers. "Absolute minutes" count from 00:00 of the first
   * calendar day of the recording (no time zone or DST adjustment).
   * ================================================================ */
  function fmtClock(min) {
    min = ((min % 1440) + 1440) % 1440;
    const h = Math.floor(min / 60), m = Math.round(min - h * 60);
    if (m === 60) return String((h + 1) % 24).padStart(2, "0") + ":00";
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
  }
  function fmtDate(startUTC, absMin) {
    const d = new Date(startUTC + Math.floor(absMin / 1440) * 864e5);
    return String(d.getUTCDate()).padStart(2, "0") + "-" + MONTH_NAMES[d.getUTCMonth()] + "-" + d.getUTCFullYear();
  }
  function fmtDateShort(startUTC, absMin) {
    const d = new Date(startUTC + Math.floor(absMin / 1440) * 864e5);
    return String(d.getUTCDate()).padStart(2, "0") + " " + MONTH_NAMES[d.getUTCMonth()];
  }

  /* ================================================================
   * Binning. Returns arrays indexed by absolute bin (bin 0 starts at
   * 00:00 of day 1). Activity is summed; bins with < 50% coverage are
   * missing; partially covered bins are scaled up to a full bin.
   * ================================================================ */
  function binRecord(rec, binMin) {
    const dt = rec.intervalMin;
    const n = rec.counts.length;
    const endMin = rec.t0Min + n * dt;
    const nb = Math.ceil(endMin / binMin);
    const act = new Float64Array(nb), cov = new Float64Array(nb), cnt = new Float64Array(nb);
    const lux = rec.light ? new Float64Array(nb) : null, luxN = rec.light ? new Float64Array(nb) : null;
    for (let i = 0; i < n; i++) {
      const b = Math.floor((rec.t0Min + i * dt + 1e-9) / binMin);
      if (b < 0 || b >= nb) continue;
      cnt[b] += 1;
      const v = rec.counts[i];
      if (!isNaN(v)) { act[b] += v; cov[b] += 1; }
      if (lux) { const l = rec.light[i]; if (!isNaN(l)) { lux[b] += l; luxN[b] += 1; } }
    }
    const expected = binMin / dt;
    for (let b = 0; b < nb; b++) {
      if (cov[b] < expected * 0.5) act[b] = NaN;
      else if (cov[b] < expected) act[b] = act[b] * expected / cov[b];
      if (lux) lux[b] = luxN[b] > 0 ? lux[b] / luxN[b] : NaN;
    }
    const firstBin = Math.floor(rec.t0Min / binMin);
    return { binMin: binMin, act: act, lux: lux, n: nb, firstBin: firstBin };
  }

  /* ================================================================
   * Lighting
   * ================================================================ */

  /** Light state from a schedule: 1 = light, 0 = dark. */
  function scheduleState(schedule, absMin) {
    if (!schedule || !schedule.length) return NaN;
    const day = Math.floor(absMin / 1440) + 1;
    let seg = null;
    for (const s of schedule) if (s.startDay <= day && (!seg || s.startDay >= seg.startDay)) seg = s;
    if (!seg) seg = schedule.reduce(function (a, b) { return a.startDay <= b.startDay ? a : b; });
    if (seg.mode === "DD") return 0;
    if (seg.mode === "LL") return 1;
    const T = (seg.L + seg.D) * 60;
    if (!(T > 0)) return NaN;
    const ref = (seg.startDay - 1) * 1440 + seg.onMin;
    const ph = (((absMin - ref) % T) + T) % T;
    return ph < seg.L * 60 ? 1 : 0;
  }

  /** Per-bin light state (1/0/NaN) using the chosen source. */
  function lightStates(rec, binned, source, threshold, schedule) {
    const out = new Float64Array(binned.n).fill(NaN);
    if (source === "recorded" && binned.lux) {
      for (let b = 0; b < binned.n; b++) {
        const l = binned.lux[b];
        out[b] = isNaN(l) ? NaN : (l >= threshold ? 1 : 0);
      }
    } else if (source === "schedule") {
      const bm = binned.binMin;
      for (let b = 0; b < binned.n; b++) out[b] = scheduleState(schedule, (b + 0.5) * bm);
    }
    return out;
  }

  /**
   * Detect the lighting regime day by day from the recorded light column.
   * Returns per-day rows and merged segments (LD with on/off times, DD or LL).
   */
  function detectLighting(rec, threshold) {
    if (!rec.light) return { days: [], segments: [] };
    const dt = rec.intervalMin;
    const n = rec.counts.length;
    const days = [];
    const nDays = Math.ceil((rec.t0Min + n * dt) / 1440);
    for (let d = 0; d < nDays; d++) days.push({ day: d + 1, n: 0, nLight: 0, sumLux: 0, on: [], off: [], cover: 0 });
    let prev = null;
    for (let i = 0; i < n; i++) {
      const t = rec.t0Min + i * dt;
      const d = Math.floor(t / 1440);
      const l = rec.light[i];
      if (isNaN(l)) { prev = null; continue; }
      const s = l >= threshold ? 1 : 0;
      const row = days[d];
      row.n++;
      if (s) { row.nLight++; row.sumLux += l; }
      if (prev !== null && s !== prev) (s ? row.on : row.off).push(t - d * 1440);
      prev = s;
    }
    const perDay = 1440 / dt;
    for (const r of days) {
      r.cover = r.n / perDay;
      r.frac = r.n ? r.nLight / r.n : NaN;
      r.meanLux = r.nLight ? r.sumLux / r.nLight : 0;
      r.lightHours = r.n ? r.frac * 24 : NaN;
      if (!r.n) r.mode = "?";
      else if (r.on.length === 0 && r.off.length === 0) r.mode = r.frac > 0.5 ? "LL" : "DD";
      else r.mode = "LD";
      r.onMin = r.on.length ? r.on[0] : null;
      r.offMin = r.off.length ? r.off[r.off.length - 1] : null;
      r.partial = r.cover < 0.98;
    }
    // Merge days into segments with a consistent regime (edges within 20 min).
    const segs = [];
    const close = function (a, b) { if (a === null || b === null) return true; let d = Math.abs(a - b) % 1440; return Math.min(d, 1440 - d) <= 20; };
    for (const r of days) {
      if (r.mode === "?") continue;
      const last = segs[segs.length - 1];
      if (last && last.mode === r.mode && (r.mode !== "LD" || (close(last.onMin, r.onMin) && close(last.offMin, r.offMin)))) {
        last.endDay = r.day;
        if (last.onMin === null) last.onMin = r.onMin;
        if (last.offMin === null) last.offMin = r.offMin;
        if (r.mode !== "DD") last.luxSum += r.meanLux, last.luxN++;
      } else {
        segs.push({ mode: r.mode, startDay: r.day, endDay: r.day, onMin: r.onMin, offMin: r.offMin, luxSum: r.mode !== "DD" ? r.meanLux : 0, luxN: r.mode !== "DD" ? 1 : 0 });
      }
    }
    // A single-day transitional segment (e.g. the day the regime changes) is folded into the next one.
    for (let i = 0; i < segs.length - 1; i++) {
      if (segs[i].startDay === segs[i].endDay && segs.length > 1 && i > 0) {
        segs[i + 1].startDay = segs[i].startDay; segs.splice(i, 1); i--;
      }
    }
    for (const s of segs) {
      s.meanLux = s.luxN ? s.luxSum / s.luxN : 0;
      if (s.mode === "LD" && s.onMin !== null && s.offMin !== null) {
        s.L = (((s.offMin - s.onMin) % 1440) + 1440) % 1440 / 60;
        s.D = 24 - s.L;
      }
    }
    return { days: days, segments: segs };
  }

  /** Convert detected segments to an editable schedule. */
  function segmentsToSchedule(segs) {
    return segs.map(function (s) {
      if (s.mode === "LD") return { startDay: s.startDay, mode: "LD", onMin: s.onMin !== null ? Math.round(s.onMin) : 420, L: s.L !== undefined ? +s.L.toFixed(2) : 12, D: s.D !== undefined ? +s.D.toFixed(2) : 12 };
      return { startDay: s.startDay, mode: s.mode, onMin: 420, L: 12, D: 12 };
    });
  }

  /* ================================================================
   * Statistics helpers
   * ================================================================ */

  /** Inverse standard normal CDF (Acklam's rational approximation). */
  function normInv(p) {
    const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239];
    const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572];
    const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
    const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
    const pl = 0.02425;
    let q, r;
    if (p < pl) { q = Math.sqrt(-2 * Math.log(p)); return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1); }
    if (p <= 1 - pl) { q = p - 0.5; r = q * q; return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1); }
    q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }

  /** Chi-square quantile via the Wilson-Hilferty approximation (accurate for df > ~10). */
  function chi2Inv(p, df) {
    const z = normInv(p);
    const h = 2 / (9 * df);
    return df * Math.pow(1 - h + z * Math.sqrt(h), 3);
  }

  /** Ordinary least squares y = a + b x; returns slope, intercept, SE(slope), r2. */
  function linfit(xs, ys) {
    const n = xs.length;
    if (n < 2) return null;
    let sx = 0, sy = 0;
    for (let i = 0; i < n; i++) { sx += xs[i]; sy += ys[i]; }
    const mx = sx / n, my = sy / n;
    let sxx = 0, sxy = 0, syy = 0;
    for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; sxx += dx * dx; sxy += dx * dy; syy += dy * dy; }
    if (sxx === 0) return null;
    const b = sxy / sxx, a = my - b * mx;
    let sse = 0;
    for (let i = 0; i < n; i++) { const e = ys[i] - (a + b * xs[i]); sse += e * e; }
    const se = n > 2 ? Math.sqrt(sse / (n - 2) / sxx) : NaN;
    return { slope: b, intercept: a, se: se, r2: syy > 0 ? 1 - sse / syy : 1, n: n, sd: n > 2 ? Math.sqrt(sse / (n - 2)) : NaN };
  }

  /* ================================================================
   * Periodograms
   * ================================================================ */

  /**
   * Chi-square periodogram (Sokolove & Bushell 1978) with missing-data support.
   * For each trial period P (in bins) the series is folded and
   *   Qp = sum_h n_h (M_h - M)^2 / [ sum_i (X_i - M)^2 / N ]
   * which reduces to the classic formula when no data are missing.
   * Significance: Qp > chi-square quantile (1 - alpha, P - 1).
   */
  function chiSquarePeriodogram(x, binMin, pMinH, pMaxH, alpha) {
    const vals = [];
    let N = 0, sum = 0;
    for (let i = 0; i < x.length; i++) if (!isNaN(x[i])) { N++; sum += x[i]; }
    const M = sum / N;
    let ss = 0;
    for (let i = 0; i < x.length; i++) if (!isNaN(x[i])) ss += (x[i] - M) * (x[i] - M);
    const out = [];
    if (N < 10 || ss === 0) return { points: out, peak: null };
    const pMin = Math.max(2, Math.round(pMinH * 60 / binMin)), pMax = Math.round(pMaxH * 60 / binMin);
    for (let P = pMin; P <= pMax; P++) {
      if (P * 2 > x.length) break;
      const s = new Float64Array(P), c = new Float64Array(P);
      for (let i = 0; i < x.length; i++) { const v = x[i]; if (!isNaN(v)) { const h = i % P; s[h] += v; c[h]++; } }
      let num = 0;
      for (let h = 0; h < P; h++) if (c[h] > 0) { const mh = s[h] / c[h]; num += c[h] * (mh - M) * (mh - M); }
      const Qp = num / (ss / N);
      const crit = chi2Inv(1 - alpha, P - 1);
      out.push({ periodH: P * binMin / 60, value: Qp, crit: crit, amp: Qp - crit });
    }
    let peak = null;
    for (const p of out) if (!peak || p.amp > peak.amp) peak = p;
    if (peak) peak = Object.assign({ significant: peak.amp > 0 }, peak);
    return { points: out, peak: peak, vals: vals };
  }

  /**
   * Lomb-Scargle periodogram (normalised by the total variance), tolerant of gaps.
   * False-alarm threshold uses M ~ N independent frequencies:
   *   z_alpha = -ln(1 - (1 - alpha)^(1/M)).
   */
  function lombScargle(x, binMin, pMinH, pMaxH, alpha, stepH) {
    const t = [], y = [];
    for (let i = 0; i < x.length; i++) if (!isNaN(x[i])) { t.push(i * binMin / 60); y.push(x[i]); }
    const N = y.length;
    const out = [];
    if (N < 10) return { points: out, peak: null };
    let m = 0; for (const v of y) m += v; m /= N;
    let v2 = 0; for (let i = 0; i < N; i++) { y[i] -= m; v2 += y[i] * y[i]; }
    v2 /= (N - 1);
    if (v2 === 0) return { points: out, peak: null };
    const M = Math.min(N, Math.round((pMaxH - pMinH) / stepH) + 1);
    const crit = -Math.log(1 - Math.pow(1 - alpha, 1 / Math.max(1, M)));
    for (let P = pMinH; P <= pMaxH + 1e-9; P += stepH) {
      const w = 2 * Math.PI / P;
      let s2 = 0, c2 = 0;
      for (let i = 0; i < N; i++) { s2 += Math.sin(2 * w * t[i]); c2 += Math.cos(2 * w * t[i]); }
      const tau = Math.atan2(s2, c2) / (2 * w);
      let yc = 0, ys = 0, cc = 0, ssn = 0;
      for (let i = 0; i < N; i++) {
        const a = w * (t[i] - tau), c = Math.cos(a), s = Math.sin(a);
        yc += y[i] * c; ys += y[i] * s; cc += c * c; ssn += s * s;
      }
      const pw = (yc * yc / cc + ys * ys / ssn) / (2 * v2);
      out.push({ periodH: P, value: pw, crit: crit, amp: pw - crit });
    }
    let peak = null;
    for (const p of out) if (!peak || p.value > peak.value) peak = p;
    if (peak) peak = Object.assign({ significant: peak.value > crit }, peak);
    return { points: out, peak: peak };
  }

  /* ================================================================
   * Activity onsets and offsets
   * ================================================================
   * Template score at bin t (W = template width in bins):
   *   S(t) = mean(x[t .. t+W-1]) - mean(x[t-W .. t-1])
   * The onset of a cycle is the bin that maximises S (a step from rest
   * to activity); the offset maximises -S within the following active
   * period. The first cycle is searched over a whole row; later cycles
   * are tracked within +/- search hours of the previous onset + period.
   */
  function prefixSums(x) {
    const n = x.length;
    const s = new Float64Array(n + 1), c = new Float64Array(n + 1);
    for (let i = 0; i < n; i++) { const v = x[i]; s[i + 1] = s[i] + (isNaN(v) ? 0 : v); c[i + 1] = c[i] + (isNaN(v) ? 0 : 1); }
    return { s: s, c: c };
  }
  function windowMean(ps, a, b) { // mean of x[a..b-1], NaN if < 50% valid
    a = Math.max(0, a); b = Math.min(ps.s.length - 1, b);
    if (b <= a) return NaN;
    const n = ps.c[b] - ps.c[a];
    if (n < (b - a) * 0.5) return NaN;
    return (ps.s[b] - ps.s[a]) / n;
  }

  function detectOnsets(x, binMin, opt) {
    // opt: { rowStartBin(k), periodBins, kFirst, kLast, templateH, searchH, minContrast, manual: {k: absBin|null} }
    const W = Math.max(1, Math.round(opt.templateH * 60 / binMin));
    const H = Math.max(1, Math.round(opt.searchH * 60 / binMin));
    const Pb = opt.periodBins;
    const ps = prefixSums(x);
    let gm = 0, gn = 0;
    for (let i = 0; i < x.length; i++) if (!isNaN(x[i])) { gm += x[i]; gn++; }
    gm = gn ? gm / gn : 0;
    const minS = gm * (opt.minContrast / 100);
    const score = function (t) {
      const after = windowMean(ps, t, t + W), before = windowMean(ps, t - W, t);
      if (isNaN(after) || isNaN(before)) return NaN;
      return after - before;
    };
    const res = [];
    let prev = null;
    for (let k = opt.kFirst; k <= opt.kLast; k++) {
      const rs = opt.rowStartBin(k);
      let lo, hi;
      if (prev === null) { lo = rs; hi = rs + Pb; } else { lo = prev + Pb - H; hi = prev + Pb + H; }
      lo = Math.max(lo, W); hi = Math.min(hi, x.length - W);
      let best = null, bestS = -Infinity;
      for (let t = lo; t < hi; t++) { const s = score(t); if (!isNaN(s) && s > bestS) { bestS = s; best = t; } }
      let onset = best !== null && bestS > minS ? best : null;
      // Refinement: the template maximum can sit slightly before the first sustained
      // running. Move to the first bin within +/- 1 h that is itself active and whose
      // next 30 min both reach 40% of the mean level in the template window after onset.
      if (onset !== null) {
        const R = Math.max(1, Math.round(60 / binMin)), q = Math.max(1, Math.round(30 / binMin));
        const thr = 0.4 * windowMean(ps, onset, onset + W);
        for (let t = Math.max(0, onset - R); t <= onset + R; t++) {
          const m = windowMean(ps, t, t + q);
          if (!isNaN(m) && m >= thr && x[t] >= thr) { onset = t; break; }
        }
      }
      let manual = false;
      if (opt.manual && Object.prototype.hasOwnProperty.call(opt.manual, k)) { onset = opt.manual[k]; manual = true; }
      // Offset: first time after onset + 2 h that is followed by >= restH hours of
      // low activity (below 15% of the mean level in the template window after onset).
      let offset = null;
      if (onset !== null) {
        const thr = 0.15 * windowMean(ps, onset, onset + W);
        const restB = Math.max(1, Math.round((opt.restH || 3) * 60 / binMin));
        const lim = Math.min(x.length - restB, onset + Pb - Math.round(60 / binMin));
        for (let t = onset + Math.round(120 / binMin); t < lim; t++) {
          const m = windowMean(ps, t, t + restB);
          if (!isNaN(m) && m < thr) { offset = t; break; }
        }
      }
      res.push({ k: k, onsetBin: onset, offsetBin: offset, score: bestS, manual: manual });
      if (onset !== null) prev = onset;
      else if (prev !== null) prev = prev + Pb; // keep tracking through gaps
    }
    return res;
  }

  /* ================================================================
   * Folded activity profile (average waveform) with SEM.
   * ================================================================ */
  function foldProfile(x, startBin, periodBins) {
    const s = new Float64Array(periodBins), s2 = new Float64Array(periodBins), c = new Float64Array(periodBins);
    for (let i = 0; i < x.length; i++) {
      const v = x[i]; if (isNaN(v)) continue;
      const h = (((startBin + i) % periodBins) + periodBins) % periodBins;
      s[h] += v; s2[h] += v * v; c[h]++;
    }
    const out = [];
    for (let h = 0; h < periodBins; h++) {
      const n = c[h], m = n ? s[h] / n : NaN;
      const sd = n > 1 ? Math.sqrt(Math.max(0, (s2[h] - n * m * m) / (n - 1))) : NaN;
      out.push({ phase: h, mean: m, sem: n > 1 ? sd / Math.sqrt(n) : NaN, n: n });
    }
    return out;
  }

  /* ================================================================
   * Non-parametric rhythm variables (Van Someren et al. 1999), computed
   * on hourly totals: IS (interdaily stability), IV (intradaily
   * variability), and RA (relative amplitude from M10 and L5).
   * ================================================================ */
  function nonParametric(x, binMin, startBin) {
    const perH = Math.round(60 / binMin);
    if (perH < 1 || Math.abs(perH * binMin - 60) > 1e-6) return null;
    const firstHourBin = Math.ceil(startBin / perH) * perH - startBin;
    const hours = [], hourOfDay = [];
    for (let i = firstHourBin; i + perH <= x.length; i += perH) {
      let s = 0, ok = true;
      for (let j = 0; j < perH; j++) { const v = x[i + j]; if (isNaN(v)) { ok = false; break; } s += v; }
      hours.push(ok ? s : NaN);
      hourOfDay.push((((startBin + i) / perH) % 24 + 24) % 24);
    }
    const valid = hours.filter(function (v) { return !isNaN(v); });
    const N = valid.length;
    if (N < 48) return null;
    const mean = valid.reduce(function (a, b) { return a + b; }, 0) / N;
    let tot = 0; for (const v of valid) tot += (v - mean) * (v - mean);
    if (tot === 0) return null;
    const hs = new Float64Array(24), hc = new Float64Array(24);
    for (let i = 0; i < hours.length; i++) if (!isNaN(hours[i])) { const h = Math.round(hourOfDay[i]) % 24; hs[h] += hours[i]; hc[h]++; }
    let between = 0;
    const prof = [];
    for (let h = 0; h < 24; h++) { const m = hc[h] ? hs[h] / hc[h] : mean; prof.push(m); between += (m - mean) * (m - mean); }
    const IS = (N * between) / (24 * tot);
    let diff = 0, nd = 0;
    for (let i = 1; i < hours.length; i++) if (!isNaN(hours[i]) && !isNaN(hours[i - 1])) { diff += Math.pow(hours[i] - hours[i - 1], 2); nd++; }
    const IV = nd ? (N * diff) / (nd * tot) : NaN;
    let M10 = -Infinity, M10s = 0, L5 = Infinity, L5s = 0;
    for (let h = 0; h < 24; h++) {
      let a = 0, b = 0;
      for (let j = 0; j < 10; j++) a += prof[(h + j) % 24];
      for (let j = 0; j < 5; j++) b += prof[(h + j) % 24];
      if (a / 10 > M10) { M10 = a / 10; M10s = h; }
      if (b / 5 < L5) { L5 = b / 5; L5s = h; }
    }
    return { IS: IS, IV: IV, M10: M10, M10start: M10s, L5: L5, L5start: L5s, RA: (M10 - L5) / (M10 + L5), hours: N };
  }

  function quantile(arr, q) {
    const a = Array.from(arr).filter(function (v) { return !isNaN(v); }).sort(function (p, r) { return p - r; });
    if (!a.length) return NaN;
    const i = (a.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
    return a[lo] + (a[hi] - a[lo]) * (i - lo);
  }

  const api = {
    parseAWD: parseAWD, parseCSV: parseCSV, parseAny: parseAny, parseDate: parseDate, parseTime: parseTime,
    fmtClock: fmtClock, fmtDate: fmtDate, fmtDateShort: fmtDateShort,
    binRecord: binRecord, scheduleState: scheduleState, lightStates: lightStates,
    detectLighting: detectLighting, segmentsToSchedule: segmentsToSchedule,
    chi2Inv: chi2Inv, normInv: normInv, linfit: linfit,
    chiSquarePeriodogram: chiSquarePeriodogram, lombScargle: lombScargle,
    detectOnsets: detectOnsets, foldProfile: foldProfile, nonParametric: nonParametric, quantile: quantile
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Acto = api;
})(typeof window !== "undefined" ? window : globalThis);
