/*! Actogram web app | Copyright (c) 2026 The Actogram Web Application Development Team | SPDX-License-Identifier: MIT */
/* ------------------------------------------------------------------
 * Actogram web app: user interface and drawing.
 * Depends on window.Acto (core.js) and window.DemoAWD (gen.js).
 *
 * Structure of this script (top to bottom):
 *   1. Application state (S) and per-recording entries (makeEntry).
 *   2. derive(): binned data, lighting states and the analysis range for
 *      the selected recording under the current settings (memoised in
 *      F.cache so redraws are cheap).
 *   3. Onsets, lighting segments, regression fits and phase (ZT / CT).
 *   4. Colours (read from the CSS tokens) and canvas helpers.
 *   5. Actogram: layout, drawing, zoom/pan state (Z), mouse and keyboard
 *      interaction (place onsets, select onsets for fits, zoom box, pan).
 *   6. Generic XY chart used by the periodogram, profile and light trace.
 *   7. Analysis tabs (one render function per tab) and the Methods text.
 *   8. Control rail rendering (file list, schedule, fits, range presets)
 *      and syncControls(), which mirrors state into the form controls.
 *   9. Loading files, saving CSV/PNG, toast messages.
 *  10. wire(): connects every control to its handler; then a demo
 *      recording is loaded so the page opens in a working state.
 *
 * Rendering model: any change updates the state, then calls renderAll()
 * (or a narrower render function). Nothing is drawn incrementally.
 *
 * Units: times are "absolute minutes" (or absolute bins) counted from
 * 00:00 of the first calendar day of the recording. Row k of the
 * actogram starts at absolute bin rowStart(k) = offset + k x period.
 * ------------------------------------------------------------------ */
(function () {
  "use strict";
  const A = window.Acto;
  const $ = function (id) { return document.getElementById(id); };
  // Bin size: a whole multiple of each file's sampling interval. The default is
  // 1x (the native interval); the largest allowed bin is 120 min.
  const BIN_MAX_MIN = 120;

  /* ================================================================
   * Application state
   * ================================================================ */
  const S = {
    tool: "place",    // mouse tool while editing: "place" (click places onsets, drag zooms), "select" (choose onsets for a fit), "pan"
    files: [],       // loaded recordings, see makeEntry()
    cur: -1,         // index of the selected recording
    tab: "period",
    editing: false,
    disp: { mult: 2, T: 24, offsetH: 0, rowH: 14, style: "bars", gainLog: 0, floor: 0, shade: true, showOn: true, showOff: false },
    ana: { method: "chi", pmin: 18, pmax: 30, alpha: 0.01, fold: "row", foldCustom: 24, templateH: 6, searchH: 4, minContrast: 20, phaseUnit: "ztct", phaseRef: "ld" }
  };
  let uid = 0;

  /**
   * Create the state entry for one loaded recording.
   * Holds the parsed record plus everything that is chosen per file:
   * bin multiple, regression fits, lighting source/threshold/schedule,
   * analysis range (d1..d2, 1-based rows), hand-edited onsets and a cache.
   * Recordings with a light column default to "Recorded light".
   */
  function makeEntry(rec, synthetic) {
    const hasLight = !!rec.light && A.quantile(rec.light, 0.99) > 0;
    const det = A.detectLighting(rec, 10);
    const sched = det.segments.length ? A.segmentsToSchedule(det.segments) : [{ startDay: 1, mode: "LD", onMin: 420, L: 12, D: 12 }];
    return {
      id: ++uid, rec: rec, synthetic: !!synthetic,
      binMult: 1, fits: [{ name: "Fit 1", auto: true, members: {} }], activeFit: 0,
      lightSrc: hasLight ? "recorded" : "none", luxThr: 10, schedule: sched,
      d1: 1, d2: 9999, manual: {}, cache: {}
    };
  }
  // The recording currently shown (or null when nothing is loaded).
  const curFile = function () { return S.files[S.cur] || null; };

  /* ================================================================
   * Derived data for one file under the current settings (memoised)
   * ================================================================ */
  // Largest multiple of the sampling interval that keeps the bin <= 120 min.
  function maxBinMult(F) {
    return Math.max(1, Math.floor(BIN_MAX_MIN / F.rec.intervalMin + 1e-9));
  }
  // Bin size in minutes = sampling interval x the file's chosen multiple.
  function effectiveBin(F) {
    F.binMult = Math.min(maxBinMult(F), Math.max(1, Math.round(F.binMult) || 1));
    return F.rec.intervalMin * F.binMult;
  }
  // Format minutes for labels, e.g. 6 -> "6 min", 0.25 -> "0.25 min".
  const fmtMin = function (m) { return (Math.round(m * 1000) / 1000) + " min"; };

  /**
   * Derived data for recording F under the current display settings.
   * Returns { F, binMin, B (binned data), Pb (row period in bins), off (row
   * offset in bins), rowStart(k), kStart (first row with data), nRows,
   * light (per-bin 1/0/NaN), k1..k2 (analysis rows, 0-based), a..b (bin
   * slice of the analysis range) and x (activity in that slice) }.
   * Binning and lighting states are cached in F.cache and rebuilt only when
   * their inputs change.
   */
  function derive(F) {
    const binMin = effectiveBin(F);
    const key = binMin;
    if (!F.cache.binned || F.cache.binKey !== key) { F.cache.binned = A.binRecord(F.rec, binMin); F.cache.binKey = key; }
    const B = F.cache.binned;
    const Pb = Math.max(2, Math.round(S.disp.T * 60 / binMin));
    const off = Math.round(S.disp.offsetH * 60 / binMin);
    const rowStart = function (k) { return off + k * Pb; };
    const kStart = Math.max(0, Math.floor((B.firstBin - off) / Pb));
    const nRows = Math.max(1, Math.ceil((B.n - off) / Pb));
    const lightKey = [binMin, F.lightSrc, F.luxThr, JSON.stringify(F.schedule)].join("|");
    if (F.cache.lightKey !== lightKey) { F.cache.light = A.lightStates(F.rec, B, F.lightSrc, F.luxThr, F.schedule); F.cache.lightKey = lightKey; }
    const light = F.cache.light;
    // Analysis range in rows (1-based in the UI, 0-based here).
    const k1 = Math.min(Math.max(F.d1 - 1, kStart), nRows - 1);
    const k2 = Math.min(Math.max(F.d2 - 1, k1), nRows - 1);
    const a = Math.max(0, rowStart(k1)), b = Math.min(B.n, rowStart(k2 + 1));
    const x = B.act.subarray(a, b);
    return { F: F, binMin: binMin, B: B, Pb: Pb, off: off, rowStart: rowStart, kStart: kStart, nRows: nRows, light: light, k1: k1, k2: k2, a: a, b: b, x: x };
  }

  // Chi-square or Lomb-Scargle periodogram of the analysis range (cached per settings).
  function periodogram(D) {
    const key = [D.binMin, D.a, D.b, S.ana.method, S.ana.pmin, S.ana.pmax, S.ana.alpha].join("|");
    if (D.F.cache.pgKey === key) return D.F.cache.pg;
    const pg = S.ana.method === "ls"
      ? A.lombScargle(D.x, D.binMin, S.ana.pmin, S.ana.pmax, S.ana.alpha, Math.max(0.02, D.binMin / 60 / 2))
      : A.chiSquarePeriodogram(D.x, D.binMin, S.ana.pmin, S.ana.pmax, S.ana.alpha);
    D.F.cache.pg = pg; D.F.cache.pgKey = key;
    return pg;
  }

  /**
   * Onsets for every row of the actogram, then psi, regression fits and phase.
   * Detection runs on all rows (so onsets outside the analysis range can be
   * shown and selected); hand edits in F.manual override detected onsets.
   * Returns { list, fits, map (cycle k -> fit index), fit (first fit) }.
   */
  function onsets(D) {
    const key = [D.binMin, D.Pb, D.off, S.ana.templateH, S.ana.searchH, S.ana.minContrast, JSON.stringify(D.F.manual), D.nRows].join("|");
    if (D.F.cache.onKey !== key) {
      // Manual edits are stored in absolute minutes; convert to bins here.
      const man = {};
      for (const k in D.F.manual) man[k] = D.F.manual[k] === null ? null : Math.round(D.F.manual[k] / D.binMin);
      D.F.cache.on = A.detectOnsets(D.B.act, D.binMin, {
        rowStartBin: D.rowStart, periodBins: D.Pb, kFirst: D.kStart, kLast: D.nRows - 1,
        templateH: S.ana.templateH, searchH: S.ana.searchH, minContrast: S.ana.minContrast, manual: man
      });
      D.F.cache.onKey = key;
    }
    const list = D.F.cache.on;
    // Phase angle of entrainment: onset minus the nearest lights-off within +/- 6 h.
    const win = Math.round(360 / D.binMin);
    for (const o of list) {
      o.psi = null;
      if (o.onsetBin === null) continue;
      let best = null;
      for (let t = Math.max(1, o.onsetBin - win); t <= Math.min(D.light.length - 1, o.onsetBin + win); t++) {
        if (D.light[t - 1] === 1 && D.light[t] === 0) { if (best === null || Math.abs(t - o.onsetBin) < Math.abs(best - o.onsetBin)) best = t; }
      }
      if (best !== null) o.psi = (o.onsetBin - best) * D.binMin / 60;
    }
    const R = computeFits(D, list);
    computePhases(D, list, R);
    return { list: list, fits: R.fits, map: R.map, fit: R.fits.length ? R.fits[0].fit : null };
  }

  /* ================================================================
   * Lighting conditions, regression fits and circadian phase
   * ================================================================
   * Conditions are segments in absolute minutes (LD / DD / LL), taken from
   * the recorded light column or from the schedule.
   *  - Under LD, the phase of an event is its zeitgeber time:
   *      ZT = time since the most recent lights-on (ZT0), degrees = ZT / T x 360.
   *  - Under DD or LL, event times t measured from a 0-degree reference are
   *    fitted as t = b + tau * n. The phase of an event is
   *      ((t - reference) mod tau) / tau, shown as CT (x 24 circadian hours)
   *    or in degrees; the fitted phase of a regression is (b mod tau) / tau.
   *    The reference is, by default, the projected ZT0 of the preceding LD
   *    condition (the last lights-on at or before the transfer).
   * ================================================================ */
  function condSegments(D) {
    const F = D.F;
    const key = [F.lightSrc, F.luxThr, JSON.stringify(F.schedule)].join("|");
    if (F.cache.segKey === key) return F.cache.segs;
    let segs = [];
    if (F.lightSrc === "recorded" && F.rec.light) {
      segs = A.detectLighting(F.rec, F.luxThr).segments.map(function (s) {
        return { mode: s.mode, startMin: (s.startDay - 1) * 1440, endMin: s.endDay * 1440, startDay: s.startDay, endDay: s.endDay,
          ref0: s.mode === "LD" && s.onMin !== null ? (s.startDay - 1) * 1440 + s.onMin : null, Tmin: 1440, Lmin: s.L !== undefined ? s.L * 60 : null };
      });
    } else if (F.lightSrc === "schedule") {
      const sorted = F.schedule.slice().sort(function (p, q) { return p.startDay - q.startDay; });
      segs = sorted.map(function (s, i) {
        const end = i + 1 < sorted.length ? (sorted[i + 1].startDay - 1) * 1440 : Infinity;
        return { mode: s.mode, startMin: (s.startDay - 1) * 1440, endMin: end, startDay: s.startDay, endDay: isFinite(end) ? end / 1440 : null,
          ref0: s.mode === "LD" ? (s.startDay - 1) * 1440 + s.onMin : null, Tmin: (s.L + s.D) * 60, Lmin: s.L * 60 };
      });
    }
    segs.forEach(function (s) { s.realStart = s.startMin; });
    if (segs.length) { segs[0].startMin = -Infinity; segs[segs.length - 1].endMin = Infinity; }
    F.cache.segs = segs; F.cache.segKey = key;
    return segs;
  }
  // Index of the lighting segment containing absolute minute t, or -1.
  function segIndexAt(segs, t) {
    for (let i = 0; i < segs.length; i++) if (t >= segs[i].startMin && t < segs[i].endMin) return i;
    return -1;
  }
  // Modulo that is always non-negative (JavaScript's % keeps the sign of a).
  const modPos = function (a, m) { return ((a % m) + m) % m; };
  // Most recent lights-on (ZT0) at or before absolute minute t in an LD segment.
  function lastLightsOn(seg, t) { return seg.ref0 + Math.floor((t - seg.ref0) / seg.Tmin) * seg.Tmin; }
  // 0-degree reference (absolute minutes) for a free-running segment.
  function freeRunReference(D, segs, i) {
    if (i < 0) return 0;
    const seg = segs[i];
    if (S.ana.phaseRef === "ld") {
      for (let j = i - 1; j >= 0; j--) if (segs[j].mode === "LD" && segs[j].ref0 !== null) return lastLightsOn(segs[j], seg.realStart);
    }
    if (S.ana.phaseRef === "rec") return 0;
    return isFinite(seg.realStart) && seg.realStart > 0 ? seg.realStart : D.F.rec.t0Min;
  }
  // DD and LL (and unknown conditions) are treated as free-running.
  function isFreeRun(mode) { return mode !== "LD"; }

  // Which regression fit each onset (by cycle k) belongs to.
  function ensureFits(F) {
    if (!F.fits || !F.fits.length) { F.fits = [{ name: "Fit 1", auto: true, members: {} }]; F.activeFit = 0; }
    if (!(F.activeFit >= 0 && F.activeFit < F.fits.length)) F.activeFit = 0;
  }
  /**
   * Map each onset (by cycle k) to the index of the regression fit it belongs to.
   * Manual fits list their members explicitly; automatic fits take every
   * remaining onset inside the analysis range.
   */
  function fitMembership(D, list) {
    const F = D.F; ensureFits(F);
    const map = {};
    F.fits.forEach(function (f, i) { if (!f.auto) for (const k in f.members) if (f.members[k]) map[k] = i; });
    F.fits.forEach(function (f, i) {
      if (!f.auto) return;
      for (const o of list) if (o.onsetBin !== null && o.k >= D.k1 && o.k <= D.k2 && map[o.k] === undefined) map[o.k] = i;
    });
    return map;
  }
  /**
   * Least-squares regression of onset time (h) on cycle number for each fit.
   * Also records the rows covered and the lighting condition that holds most
   * of the fit's onsets (used to decide between ZT and CT).
   */
  function computeFits(D, list) {
    const map = fitMembership(D, list);
    const segs = condSegments(D);
    const fits = D.F.fits.map(function (f, i) { return { i: i, name: f.name, auto: f.auto, members: [] }; });
    for (const o of list) if (o.onsetBin !== null && map[o.k] !== undefined && fits[map[o.k]]) fits[map[o.k]].members.push(o);
    for (const f of fits) {
      f.fit = A.linfit(f.members.map(function (o) { return o.k; }), f.members.map(function (o) { return o.onsetBin * D.binMin / 60; }));
      f.kMin = f.members.length ? f.members[0].k : null; f.kMax = f.members.length ? f.members[f.members.length - 1].k : null;
      // Lighting condition of the fit: the segment holding most of its onsets.
      const cnt = {};
      for (const o of f.members) { const si = segIndexAt(segs, o.onsetBin * D.binMin); cnt[si] = (cnt[si] || 0) + 1; }
      let best = null; for (const si in cnt) if (best === null || cnt[si] > cnt[best]) best = si;
      f.segIndex = best === null ? -1 : +best;
      f.mode = f.segIndex >= 0 ? segs[f.segIndex].mode : (segs.length ? "?" : "none");
    }
    return { fits: fits, map: map, segs: segs };
  }

  // Phase of every onset, and the fitted phase of every regression.
  function computePhases(D, list, R) {
    const segs = R.segs;
    const tauForSeg = function (si) {
      let best = null;
      for (const f of R.fits) if (f.fit && f.segIndex === si && isFreeRun(f.mode) && (!best || f.members.length > best.members.length)) best = f;
      return best ? best.fit.slope : null;
    };
    for (const o of list) {
      o.phase = null;
      if (o.onsetBin === null) continue;
      const t = o.onsetBin * D.binMin;
      const si = segIndexAt(segs, t);
      const seg = si >= 0 ? segs[si] : null;
      if (seg && seg.mode === "LD") {
        if (seg.ref0 === null) continue;
        const zt = (t - lastLightsOn(seg, t)) / 60;
        o.phase = { label: "ZT", h: zt, deg: zt / (seg.Tmin / 60) * 360 };
        continue;
      }
      const own = R.map[o.k] !== undefined ? R.fits[R.map[o.k]] : null;
      const tau = own && own.fit && own.segIndex === si && isFreeRun(own.mode) ? own.fit.slope : tauForSeg(si);
      if (!(tau > 0)) { o.phase = { label: seg ? "CT" : "φ", h: NaN, deg: NaN }; continue; }
      const ref = freeRunReference(D, segs, si);
      const ph = modPos(t - ref, tau * 60) / (tau * 60);
      o.phase = { label: seg ? "CT" : "φ", h: ph * 24, deg: ph * 360 };
    }
    for (const f of R.fits) {
      f.phase = null;
      if (!f.fit) continue;
      if (f.mode === "LD") {
        // Circular mean of the members' zeitgeber phases.
        let sx = 0, sy = 0, n = 0;
        for (const o of f.members) if (o.phase && isFinite(o.phase.deg)) { sx += Math.cos(o.phase.deg * Math.PI / 180); sy += Math.sin(o.phase.deg * Math.PI / 180); n++; }
        if (n) { const deg = modPos(Math.atan2(sy, sx) * 180 / Math.PI, 360); const T = segs[f.segIndex].Tmin / 60; f.phase = { label: "ZT", h: deg / 360 * T, deg: deg, how: "circular mean of onset ZT" }; }
      } else if (f.fit.slope > 0) {
        const ref = freeRunReference(D, segs, f.segIndex);
        const ph = modPos(f.fit.intercept * 60 - ref, f.fit.slope * 60) / (f.fit.slope * 60);
        f.phase = { label: f.segIndex >= 0 ? "CT" : "φ", h: ph * 24, deg: ph * 360, how: "fitted line, (b mod τ) / τ" };
      }
    }
  }
  // Phase as text: "ZT12.00" / "CT11.98", or degrees "179.7°" (unit from S.ana.phaseUnit).
  function fmtPhase(p, unit) {
    if (!p || !isFinite(p.deg)) return "–";
    if ((unit || S.ana.phaseUnit) === "deg") return p.deg.toFixed(1) + "°";
    return p.label + p.h.toFixed(2);
  }
  const PHASE_REF_LABEL = { ld: "projected ZT0 of the preceding LD", start: "start of the condition", rec: "00:00 of recording day 1" };

  // Put onsets (by cycle k) into the active fit, or take them out if they are all in it already.
  function toggleInActiveFit(D, ks) {
    const F = D.F; ensureFits(F);
    if (!ks.length) return;
    const O = onsets(D);
    // Freeze automatic fits so the selection becomes explicit.
    F.fits.forEach(function (f, i) {
      if (!f.auto) return;
      f.members = {};
      for (const k in O.map) if (O.map[k] === i) f.members[k] = true;
      f.auto = false;
    });
    const ai = F.activeFit;
    const allIn = ks.every(function (k) { return O.map[k] === ai; });
    for (const k of ks) {
      F.fits.forEach(function (f) { delete f.members[k]; });
      if (!allIn) F.fits[ai].members[k] = true;
    }
  }

  /* ================================================================
   * Colours (read from CSS tokens; export uses a fixed light palette)
   * ================================================================ */
  function palette(forExport) {
    if (forExport) return { bg: "#ffffff", ink: "#141821", muted: "#586074", line: "#dadee7", light: "#f7e7a6", dark: "#d3d8e3", nodata: "#f0e3e3", bar: "#141821", onset: "#d9481f", offset: "#1baf7a", fit: "#2a78d6", accent: "#3446c9", panel: "#ffffff", unused: "#9aa1af", fits: ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e87ba4", "#008300"] };
    const cs = getComputedStyle(document.documentElement);
    const g = function (n) { return cs.getPropertyValue(n).trim(); };
    return { bg: g("--panel"), ink: g("--ink"), muted: g("--muted"), line: g("--line"), light: g("--light-phase"), dark: g("--dark-phase"), nodata: g("--nodata"), bar: g("--bar"), onset: g("--onset"), offset: g("--offset"), fit: g("--fit"), accent: g("--accent"), panel: g("--panel"), unused: g("--unused"), fits: [1, 2, 3, 4, 5, 6].map(function (i) { return g("--fit" + i); }) };
  }
  const MONO = '11px "IBM Plex Mono", ui-monospace, Menlo, monospace';
  const SANS = '12px "Public Sans", system-ui, sans-serif';

  /**
   * Size a canvas for crisp drawing: the backing store is cssW x cssH times the
   * device pixel ratio (or `scale` for exports), and the context is scaled so
   * drawing code can work in CSS pixels.
   */
  function setupCanvas(cv, cssW, cssH, scale) {
    const dpr = scale || window.devicePixelRatio || 1;
    cv.width = Math.round(cssW * dpr); cv.height = Math.round(cssH * dpr);
    if (!scale) { cv.style.width = cssW + "px"; cv.style.height = cssH + "px"; }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  /* ================================================================
   * Actogram
   * ================================================================ */
  let actoGeom = null;

  /* Zoom for onset editing. zx magnifies the time axis, zy the row height;
     j0 (bins from the row start) and r0 (rows) are the top-left corner of
     the visible window. Zoom applies only while "Edit onsets" is on. */
  const Z = { zx: 1, zy: 1, j0: 0, r0: 0 };
  const ZX_MAX = 48, ZY_MAX = 8;
  // Limit v to the interval [a, b].
  const clamp = function (v, a, b) { return Math.min(b, Math.max(a, v)); };

  /**
   * Geometry of the actogram: margins, plot width, row height, visible rows
   * (r0 .. r0 + rows) and the visible time window in bins (j0 .. j1 within a
   * row of `span` bins). With full = true (PNG export) the zoom is ignored.
   * While editing, the zoom state Z is clamped here so the view stays inside
   * the record.
   */
  function actoLayout(D, width, full) {
    const left = 92, top = 36;
    const rowsAll = D.nRows - D.kStart;
    const span = S.disp.mult * D.Pb;
    const useZoom = !full && S.editing;
    const zx = useZoom ? Z.zx : 1, zy = useZoom ? Z.zy : 1;
    const zoomed = useZoom && (zx > 1 || zy > 1);
    const right = zoomed ? 20 : 10, bottom = zoomed ? 24 : 14;
    const rowH = S.disp.rowH * zy;
    // The plot keeps its unzoomed height; zooming rows shows fewer of them.
    const nVis = Math.max(1, Math.min(rowsAll, Math.floor((rowsAll * S.disp.rowH) / rowH + 1e-9)));
    const jw = span / zx;
    const j0 = clamp(useZoom ? Z.j0 : 0, 0, span - jw);
    const r0 = clamp(Math.round(useZoom ? Z.r0 : 0), 0, rowsAll - nVis);
    if (useZoom) { Z.j0 = j0; Z.r0 = r0; }
    return { left: left, right: right, top: top, bottom: bottom, W: width, H: top + nVis * rowH + bottom, plotW: width - left - right,
      rowH: rowH, rows: nVis, rowsAll: rowsAll, r0: r0, span: span, j0: j0, j1: j0 + jw, zoomed: zoomed };
  }

  // 95th percentile of non-zero bins, cached per binning (used for bar scaling).
  function activityScale(D) {
    if (D.F.cache.q95Key !== D.F.cache.binKey) {
      D.F.cache.q95 = A.quantile(D.B.act.filter(function (v) { return v > 0; }), 0.95) || 1;
      D.F.cache.q95Key = D.F.cache.binKey;
    }
    return D.F.cache.q95;
  }

  /**
   * Draw the actogram into ctx using layout G and palette P.
   * Order: light/dark shading and missing data, activity bars (or heat map),
   * row baselines, hour grid, regression lines, onset/offset markers (all
   * clipped to the plot), then hour labels, row labels, the analysis-range
   * bracket and, when zoomed, scroll indicators. Onset marker positions are
   * stored in G.markers for hit-testing by the "Select for fit" tool.
   */
  function drawActogram(ctx, D, G, P, opts) {
    opts = opts || {};
    const B = D.B, rowH = G.rowH, span = G.span;
    const xOf = function (j) { return G.left + ((j - G.j0) / (G.j1 - G.j0)) * G.plotW; };
    const rowK = function (r) { return D.kStart + G.r0 + r; };
    const plotBottom = G.top + G.rows * rowH;
    ctx.fillStyle = P.bg; ctx.fillRect(0, 0, G.W, G.H);
    const scale = activityScale(D) / Math.pow(2, S.disp.gainLog);
    const heatColor = hexToRgb(P.bar);
    const jA = Math.max(0, Math.floor(G.j0)), jB = Math.min(span, Math.ceil(G.j1));

    // Everything inside the plot is clipped, so partly visible bins stay in bounds.
    ctx.save();
    ctx.beginPath(); ctx.rect(G.left, G.top, G.plotW, G.rows * rowH); ctx.clip();
    for (let r = 0; r < G.rows; r++) {
      const k = rowK(r);
      const y = G.top + r * rowH;
      const rs = D.rowStart(k);
      // 1. Light/dark shading, drawn as runs of equal state.
      if (S.disp.shade) {
        let runStart = jA, runVal = stateAt(D, rs + jA);
        for (let j = jA + 1; j <= jB; j++) {
          const v = j < jB ? stateAt(D, rs + j) : -99;
          if (v !== runVal) {
            const c = runVal === 1 ? P.light : runVal === 0 ? P.dark : runVal === -1 ? P.nodata : null;
            if (c) { ctx.fillStyle = c; ctx.fillRect(xOf(runStart), y, xOf(j) - xOf(runStart) + 0.3, rowH); }
            runStart = j; runVal = v;
          }
        }
      } else {
        // Still mark missing data without light shading.
        for (let j = jA; j < jB; j++) { const b = rs + j; if (b >= B.firstBin && b < B.n && isNaN(B.act[b])) { ctx.fillStyle = P.nodata; ctx.fillRect(xOf(j), y, xOf(j + 1) - xOf(j) + 0.3, rowH); } }
      }
      // 2. Activity.
      for (let j = jA; j < jB; j++) {
        const b = rs + j;
        if (b < 0 || b >= B.n) continue;
        const v = B.act[b];
        if (!(v > S.disp.floor)) continue;
        const f = Math.min(1, v / scale);
        const x0 = xOf(j), bw = xOf(j + 1) - x0;
        const w = Math.max(0.6, bw > 6 ? bw - 1 : bw); // leave a hairline gap when bins are wide
        if (S.disp.style === "heat") {
          // Grey heat map: the bar colour with opacity proportional to activity.
          ctx.fillStyle = "rgba(" + heatColor + "," + (0.12 + 0.88 * f).toFixed(3) + ")";
          ctx.fillRect(x0, y + 1, w + 0.2, rowH - 2);
        } else if (COLORMAPS[S.disp.style]) {
          // Colour heat map: activity mapped through a perceptually uniform colour scale.
          ctx.fillStyle = colormapColor(S.disp.style, f);
          ctx.fillRect(x0, y + 1, w + 0.2, rowH - 2);
        } else {
          const h = f * (rowH - 2);
          ctx.fillStyle = P.bar;
          ctx.fillRect(x0, y + rowH - 1 - h, w, h);
        }
      }
      // Row baseline.
      ctx.fillStyle = P.line; ctx.fillRect(G.left, y + rowH - 0.5, G.plotW, 0.5);
    }

    // 3. Vertical grid and panel boundaries (tick step adapts to the zoom).
    const binH = D.binMin / 60;
    const h0 = G.j0 * binH, h1 = G.j1 * binH;
    const maxTicks = Math.max(4, G.plotW / 70);
    const steps = [0.25, 0.5, 1, 2, 3, 6, 12, 24, 48];
    const step = steps.find(function (st) { return (h1 - h0) / st <= maxTicks; }) || 48;
    const ticks = [];
    for (let h = Math.ceil(h0 / step - 1e-9) * step; h <= h1 + 1e-6; h += step) ticks.push(+h.toFixed(6));
    for (const h of ticks) {
      const x = xOf(h / binH);
      const isPanel = Math.abs((h / S.disp.T) - Math.round(h / S.disp.T)) < 1e-6;
      ctx.fillStyle = isPanel ? P.muted : P.line;
      ctx.globalAlpha = isPanel ? 0.6 : 0.8;
      ctx.fillRect(x - 0.25, G.top, isPanel ? 1 : 0.5, G.rows * rowH);
      ctx.globalAlpha = 1;
    }

    // 4. Onsets, offsets and regression lines. Onsets are filled with the colour
    //    of the regression fit they belong to; onsets not used in any fit are grey.
    G.markers = [];
    if (S.disp.showOn || S.disp.showOff) {
      const O = onsets(D);
      const rad = Math.max(2, Math.min(G.zoomed ? 6 : 4, rowH / 3.2));
      const mark = function (o, absBin, color, kind, manual, faded) {
        for (let r = 0; r < G.rows; r++) {
          const rs = D.rowStart(rowK(r));
          const j = absBin - rs;
          if (j < 0 || j >= span || j < G.j0 - 1 || j > G.j1 + 1) continue;
          const x = xOf(j), y = G.top + r * rowH + rowH / 2;
          if (kind === "on") G.markers.push({ k: o.k, x: x, y: y });
          ctx.globalAlpha = faded ? 0.55 : 1;
          ctx.beginPath();
          if (kind === "on") ctx.arc(x, y, rad, 0, Math.PI * 2);
          else { ctx.moveTo(x, y - rad); ctx.lineTo(x + rad, y); ctx.lineTo(x, y + rad); ctx.lineTo(x - rad, y); ctx.closePath(); }
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = P.bg;
          ctx.stroke();
          ctx.fillStyle = manual ? P.bg : color;
          ctx.fill();
          if (manual) { ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke(); }
          ctx.globalAlpha = 1;
        }
      };
      if (S.disp.showOn) {
        // Each fitted line is drawn row by row over the cycles it covers, so it follows each panel.
        ctx.lineWidth = 2; ctx.setLineDash([5, 3]);
        for (const f of O.fits) {
          if (!f.fit) continue;
          ctx.strokeStyle = P.fits[f.i % P.fits.length];
          const pb = function (c) { return (f.fit.intercept + f.fit.slope * c) * 60 / D.binMin; };
          for (let k = f.kMin; k < f.kMax; k++) {
            const r = k - D.kStart - G.r0;
            if (r < -1 || r > G.rows) continue;
            for (let p = -1; p <= S.disp.mult; p++) {
              const j0 = pb(k + p) - D.rowStart(k), j1 = pb(k + 1 + p) - D.rowStart(k + 1);
              if (j0 < 0 || j0 > span || j1 < 0 || j1 > span) continue;
              ctx.beginPath();
              ctx.moveTo(xOf(j0), G.top + r * rowH + rowH / 2);
              ctx.lineTo(xOf(j1), G.top + (r + 1) * rowH + rowH / 2);
              ctx.stroke();
            }
          }
        }
        ctx.setLineDash([]);
      }
      for (const o of O.list) {
        if (S.disp.showOn && o.onsetBin !== null) {
          const fi = O.map[o.k];
          const col = fi === undefined ? P.unused : P.fits[fi % P.fits.length];
          mark(o, o.onsetBin, col, "on", o.manual, fi === undefined);
        }
        if (S.disp.showOff && o.offsetBin !== null) mark(o, o.offsetBin, P.offset, "off", false, false);
      }
    }
    ctx.restore();

    // 5. Hour labels.
    ctx.font = MONO; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.fillStyle = P.muted;
    for (const h of ticks) {
      const x = xOf(h / binH);
      const lab = S.disp.T === 24
        ? (step < 1 ? A.fmtClock((S.disp.offsetH + h) * 60) : A.fmtClock((S.disp.offsetH + h) * 60).replace(":00", ""))
        : (h % 1 ? h.toFixed(2).replace(/0$/, "") : String(h));
      ctx.fillText(lab, clamp(x, G.left + 8, G.W - 14), G.top - 8);
    }
    ctx.textAlign = "left"; ctx.fillStyle = P.muted; ctx.font = SANS;
    ctx.fillText((S.disp.T === 24 ? "clock time (h)" : "hours from row start (row period " + S.disp.T + " h)") +
      (G.zoomed ? "   ·   zoom: time ×" + fmtZoom(G.span / (G.j1 - G.j0)) + ", rows ×" + fmtZoom(rowH / S.disp.rowH) : ""), G.left, 12);

    // 6. Row labels (row number and date).
    ctx.font = MONO; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const every = rowH >= 11 ? 1 : Math.ceil(11 / rowH) * (rowH < 6 ? 2 : 1);
    for (let r = 0; r < G.rows; r++) {
      const k = rowK(r);
      if ((k + 1) % every !== 0 && r !== 0) continue;
      const y = G.top + r * rowH + rowH / 2;
      ctx.fillStyle = P.muted;
      const startMin = D.rowStart(k) * D.binMin;
      const lab = S.disp.T === 24 ? A.fmtDateShort(D.F.rec.startUTC, startMin + 1) : "";
      ctx.fillText(String(k + 1).padStart(2, " ") + (lab ? "  " + lab : ""), G.left - 10, y);
    }

    // 7. Analysis-range bracket (visible part only).
    const ra = clamp(D.k1 - D.kStart - G.r0, 0, G.rows), rb = clamp(D.k2 - D.kStart - G.r0 + 1, 0, G.rows);
    if (rb > ra) {
      const yA = G.top + ra * rowH, yB = G.top + rb * rowH;
      ctx.fillStyle = P.accent;
      ctx.fillRect(G.left - 5, yA, 2, yB - yA);
      if (D.k1 - D.kStart - G.r0 >= 0) ctx.fillRect(G.left - 5, yA, 4, 2);
      if (D.k2 - D.kStart - G.r0 < G.rows) ctx.fillRect(G.left - 5, yB - 2, 4, 2);
    }

    // 8. Scroll indicators showing where the zoomed window sits.
    if (G.zoomed) {
      ctx.fillStyle = P.line;
      const ty = plotBottom + 12;
      ctx.fillRect(G.left, ty, G.plotW, 4);
      const tx = G.W - 12;
      ctx.fillRect(tx, G.top, 4, G.rows * rowH);
      ctx.fillStyle = P.accent;
      ctx.fillRect(G.left + (G.j0 / span) * G.plotW, ty, Math.max(6, ((G.j1 - G.j0) / span) * G.plotW), 4);
      ctx.fillRect(tx, G.top + (G.r0 / G.rowsAll) * G.rows * rowH, 4, Math.max(6, (G.rows / G.rowsAll) * G.rows * rowH));
    }
    if (opts.title) {
      ctx.fillStyle = P.ink; ctx.font = '600 13px "Archivo", Arial, sans-serif'; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
      ctx.fillText(opts.title, G.W - G.right, 12);
    }
  }
  // Zoom factor as text: whole numbers when close, else one decimal.
  const fmtZoom = function (z) { return z >= 10 || Math.abs(z - Math.round(z)) < 0.05 ? String(Math.round(z)) : z.toFixed(1); };

  // Light state of an absolute bin: 1 light, 0 dark, -1 missing activity, NaN unknown.
  function stateAt(D, b) {
    const B = D.B;
    if (b < 0 || b >= B.n) return NaN;
    if (b >= B.firstBin && isNaN(B.act[b])) return -1;
    if (b < B.firstBin) return NaN;
    const s = D.light[b];
    return isNaN(s) ? NaN : s;
  }

  // "#rrggbb" or "#rgb" -> "r,g,b" for use in rgba() strings (heat map).
  /* Colour scales for the colour heat maps. Both are perceptually uniform
     sequential scales (dark = low, bright = high), readable in grey-scale and
     by most colour-vision deficiencies. Stops are interpolated linearly in RGB
     into a 256-entry lookup table built on first use. */
  const COLORMAPS = {
    viridis: ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"],
    magma: ["#000004", "#1c1044", "#4f127b", "#812581", "#b5367a", "#e55064", "#fb8761", "#fec287", "#fcfdbf"],
    cividis: ["#00204d", "#00336f", "#39486b", "#575d6d", "#707173", "#8a8779", "#a69d75", "#c4b56c", "#e4cf5b", "#ffea46"]
  };
  const colormapLUT = {};
  function colormapColor(name, f) {
    if (!colormapLUT[name]) {
      const stops = COLORMAPS[name].map(function (h) { return [0, 2, 4].map(function (i) { return parseInt(h.substr(i + 1, 2), 16); }); });
      colormapLUT[name] = [];
      for (let i = 0; i < 256; i++) {
        const t = i / 255 * (stops.length - 1), a = Math.floor(t), b = Math.min(stops.length - 1, a + 1), u = t - a;
        const c = [0, 1, 2].map(function (q) { return Math.round(stops[a][q] + (stops[b][q] - stops[a][q]) * u); });
        colormapLUT[name].push("rgb(" + c.join(",") + ")");
      }
    }
    return colormapLUT[name][Math.max(0, Math.min(255, Math.round(f * 255)))];
  }
  // CSS gradient of a colour scale, for the legend.
  function colormapGradientCSS(name) {
    const st = COLORMAPS[name];
    return "linear-gradient(90deg, " + st.map(function (c, i) { return c + " " + Math.round(i / (st.length - 1) * 100) + "%"; }).join(", ") + ")";
  }

  function hexToRgb(c) {
    const m = c.replace("#", "");
    if (m.length === 3) return [0, 1, 2].map(function (i) { return parseInt(m[i] + m[i], 16); }).join(",");
    return [0, 2, 4].map(function (i) { return parseInt(m.substr(i, 2), 16); }).join(",");
  }

  /**
   * Redraw the on-screen actogram for the selected recording, then refresh
   * the legend and the zoom bar. actoGeom keeps the data and layout of the
   * last drawing for mouse hit-testing.
   */
  function renderActogram() {
    const F = curFile();
    const cv = $("acto");
    if (!F) { cv.width = 0; cv.height = 0; return; }
    const D = derive(F);
    const width = Math.max(320, $("actoWrap").clientWidth);
    const G = actoLayout(D, width);
    const ctx = setupCanvas(cv, G.W, G.H);
    drawActogram(ctx, D, G, palette(false));
    actoGeom = { D: D, G: G };
    renderLegend(D);
    renderZoomBar(G);
  }
  // Redraw at most once per animation frame (used while panning and zooming).
  let rafPending = false;
  function requestActoDraw() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(function () { rafPending = false; renderActogram(); });
  }

  // Show the zoom bar in edit mode and update its readouts and enabled states.
  function renderZoomBar(G) {
    const bar = $("zoomBar");
    bar.hidden = !S.editing;
    if (!S.editing) return;
    $("zoomTimeVal").textContent = "×" + fmtZoom(Z.zx);
    $("zoomRowsVal").textContent = "×" + fmtZoom(Z.zy);
    $("zoomTimeOut").disabled = Z.zx <= 1; $("zoomTimeIn").disabled = Z.zx >= ZX_MAX;
    $("zoomRowsOut").disabled = Z.zy <= 1; $("zoomRowsIn").disabled = Z.zy >= ZY_MAX;
    $("zoomReset").disabled = !(Z.zx > 1 || Z.zy > 1);
    $("zoomBack").disabled = zoomHistory.length === 0;
    ["place", "select", "pan"].forEach(function (t) {
      const b = $("tool-" + t);
      b.setAttribute("aria-pressed", S.tool === t ? "true" : "false");
      b.classList.toggle("on", S.tool === t);
    });
    $("zoomBothOut").disabled = Z.zx <= 1 && Z.zy <= 1;
    $("zoomBothIn").disabled = Z.zx >= ZX_MAX && Z.zy >= ZY_MAX;
    const G2 = actoGeom && actoGeom.G;
    const canX = G2 && G2.zoomed && Z.zx > 1, canY = G2 && G2.rows < G2.rowsAll;
    $("panLeft").disabled = !canX || G2.j0 <= 0; $("panRight").disabled = !canX || G2.j1 >= G2.span - 1e-6;
    $("panUp").disabled = !canY || G2.r0 <= 0; $("panDown").disabled = !canY || G2.r0 + G2.rows >= G2.rowsAll;
  }

  // Zoom the time axis by `factor`, keeping time position `jAnchor` under screen x `px`.
  function zoomTime(factor, px) {
    if (!actoGeom) return;
    const G = actoGeom.G;
    const fx = px === undefined ? 0.5 : clamp((px - G.left) / G.plotW, 0, 1);
    const jAnchor = G.j0 + fx * (G.j1 - G.j0);
    Z.zx = clamp(Z.zx * factor, 1, ZX_MAX);
    if (Math.abs(Z.zx - 1) < 0.02) Z.zx = 1;
    Z.j0 = jAnchor - fx * (G.span / Z.zx);
    requestActoDraw();
  }
  // Zoom the rows by `factor`, keeping the row under screen y `py` in place.
  function zoomRows(factor, py) {
    if (!actoGeom) return;
    const G = actoGeom.G;
    const fy = py === undefined ? 0.5 : clamp((py - G.top) / (G.rows * G.rowH), 0, 1);
    const rAnchor = G.r0 + fy * G.rows;
    Z.zy = clamp(Z.zy * factor, 1, ZY_MAX);
    if (Math.abs(Z.zy - 1) < 0.02) Z.zy = 1;
    const nVis = Math.max(1, Math.floor(G.rowsAll / Z.zy + 1e-9));
    Z.r0 = Math.round(rAnchor - fy * nVis);
    requestActoDraw();
  }
  // Move the zoomed view: dx in fractions of the visible width, dy in steps of a quarter of the visible rows.
  function panView(dx, dy) {
    if (!actoGeom) return;
    const G = actoGeom.G;
    Z.j0 = G.j0 + dx * (G.j1 - G.j0);
    Z.r0 = G.r0 + dy * Math.max(1, Math.round(G.rows / 4));
    requestActoDraw();
  }
  // Back to the whole record (the previous view is kept for Back).
  function resetZoom() { if (Z.zx > 1 || Z.zy > 1) pushZoom(); Z.zx = 1; Z.zy = 1; Z.j0 = 0; Z.r0 = 0; requestActoDraw(); }
  // Show the analysis range rows at full plot height.
  function zoomToRange() {
    if (!actoGeom) return;
    const D = actoGeom.D, G = actoGeom.G;
    const n = D.k2 - D.k1 + 1;
    Z.zy = clamp(G.rowsAll / n, 1, ZY_MAX);
    Z.r0 = D.k1 - D.kStart;
    requestActoDraw();
  }

  // Legend above the actogram; entries depend on what is currently drawn.
  function renderLegend(D) {
    const F = curFile();
    const items = [];
    // Colour scale for heat maps: from the floor to the saturation level (counts per bin).
    if (D && (S.disp.style === "heat" || COLORMAPS[S.disp.style])) {
      const top = activityScale(D) / Math.pow(2, S.disp.gainLog);
      const grad = COLORMAPS[S.disp.style] ? colormapGradientCSS(S.disp.style) : "linear-gradient(90deg, color-mix(in srgb, var(--bar) 12%, transparent), var(--bar))";
      items.push('<span class="cbar"><span class="tag">' + Math.round(S.disp.floor) + '</span><span class="cbar-ramp" style="background:' + grad + '"></span><span class="tag">≥ ' + Math.round(top) + " counts / " + D.binMin + " min</span></span>");
    }
    if (S.disp.shade && F && F.lightSrc !== "none") {
      items.push('<span><span class="sw" style="background:var(--light-phase)"></span>Light</span>');
      items.push('<span><span class="sw" style="background:var(--dark-phase)"></span>Dark</span>');
    }
    items.push('<span><span class="sw" style="background:var(--nodata)"></span>Missing data</span>');
    if (S.disp.showOn) items.push('<span><span class="sw" style="background:var(--fit1);border-radius:50%"></span><span class="sw" style="background:var(--fit2);border-radius:50%"></span>Onset, coloured by regression fit (dashed line)</span><span><span class="sw" style="background:var(--unused);border-radius:50%"></span>Onset not used</span><span><span class="sw" style="background:var(--panel);border:2px solid var(--fit1);border-radius:50%"></span>Edited by hand</span>');
    if (S.disp.showOff) items.push('<span><span class="sw" style="background:var(--offset);transform:rotate(45deg)"></span>Offset</span>');
    items.push('<span><span class="sw" style="background:var(--accent);width:3px"></span>Analysis range</span>');
    $("legend").innerHTML = items.join("");
  }

  // Hover and edit interactions on the actogram.
  function actoPoint(ev) {
    const rect = $("acto").getBoundingClientRect();
    return { px: ev.clientX - rect.left, py: ev.clientY - rect.top };
  }
  /**
   * Translate a mouse event into actogram coordinates:
   * { px, py (canvas pixels), k (row), j (bin within the row), b (absolute bin) },
   * or null when the pointer is outside the plot area.
   */
  function actoHit(ev) {
    if (!actoGeom) return null;
    const p = actoPoint(ev), px = p.px, py = p.py;
    const G = actoGeom.G, D = actoGeom.D;
    if (px < G.left || px > G.left + G.plotW || py < G.top || py >= G.top + G.rows * G.rowH) return null;
    const r = Math.floor((py - G.top) / G.rowH);
    const j = Math.floor(G.j0 + ((px - G.left) / G.plotW) * (G.j1 - G.j0));
    const k = D.kStart + G.r0 + r;
    return { px: px, py: py, k: k, j: j, b: D.rowStart(k) + j, D: D };
  }
  // Hover tooltip: row, date, clock time, activity, light state and lux.
  function onActoMove(ev) {
    if (drag && drag.moved) return;
    const h = actoHit(ev);
    const tip = $("actoTip");
    if (!h) { tip.hidden = true; return; }
    const D = h.D, B = D.B;
    const absMin = h.b * D.binMin;
    const v = h.b >= 0 && h.b < B.n ? B.act[h.b] : NaN;
    const st = stateAt(D, h.b);
    const lux = B.lux && h.b >= 0 && h.b < B.n ? B.lux[h.b] : NaN;
    tip.innerHTML = "Row " + (h.k + 1) + " · " + A.fmtDate(D.F.rec.startUTC, absMin) + " " + A.fmtClock(absMin) +
      "<br>Activity " + (isNaN(v) ? "missing" : Math.round(v) + " / " + D.binMin + " min") +
      (st === 1 ? " · light" : st === 0 ? " · dark" : "") + (isNaN(lux) ? "" : " · " + Math.round(lux) + " lux") +
      (!S.editing ? "" : S.tool === "select"
        ? "<br><b>Click</b> an onset or <b>drag</b> a box to add/remove onsets for " + esc(activeFitName(D.F))
        : "<br><b>Click</b> to set onset · <b>Shift-click</b> to delete");
    tip.hidden = false;
    const wrap = $("actoWrap");
    const x = Math.min(h.px + 14, wrap.clientWidth - tip.offsetWidth - 4);
    tip.style.left = Math.max(0, x) + "px";
    tip.style.top = (h.py + 14) + "px";
  }
  /**
   * Click on the actogram (edit mode only).
   * Select tool: toggle the nearest onset marker (within 10 px) in the active fit.
   * Other tools: place the onset of the clicked cycle (double/triple plots map
   * the right-hand panels to the following cycles); Shift-click deletes the
   * nearest onset. A click that ends a drag is ignored (suppressClick).
   */
  function onActoClick(ev) {
    if (!S.editing) return;
    if (suppressClick) { suppressClick = false; return; }
    const h = actoHit(ev);
    if (!h) return;
    const D = h.D, F = D.F;
    if (S.tool === "select") {
      // Toggle the onset marker nearest to the pointer (within 10 px).
      let best = null, bd = 10;
      for (const m of actoGeom.G.markers || []) { const d = Math.hypot(m.x - h.px, m.y - h.py); if (d <= bd) { bd = d; best = m; } }
      if (best) { toggleInActiveFit(D, [best.k]); renderAll(); }
      return;
    }
    const cyc = h.k + Math.floor(h.j / D.Pb);
    if (ev.shiftKey) {
      // Delete the detected onset nearest to the click.
      const O = onsets(D);
      let best = null;
      for (const o of O.list) if (o.onsetBin !== null && (best === null || Math.abs(o.onsetBin - h.b) < Math.abs(best.onsetBin - h.b))) best = o;
      if (best) F.manual[best.k] = null;
    } else {
      F.manual[cyc] = h.b * D.binMin;
    }
    renderAll();
  }

  // Wheel: Ctrl/Cmd (or trackpad pinch) zooms time, Alt zooms rows,
  // plain wheel pans when zoomed. Outside edit mode the page scrolls normally.
  function onActoWheel(ev) {
    if (!S.editing || !actoGeom) return;
    const G = actoGeom.G, p = actoPoint(ev);
    const factor = Math.exp(-clamp(ev.deltaY, -100, 100) * 0.004);
    if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); zoomTime(factor, p.px); return; }
    if (ev.altKey) { ev.preventDefault(); zoomRows(factor, p.py); return; }
    if (!G.zoomed) return;
    const horizontal = ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY);
    if (horizontal && Z.zx > 1) {
      ev.preventDefault();
      const d = ev.shiftKey && !ev.deltaX ? ev.deltaY : ev.deltaX;
      Z.j0 += (d / G.plotW) * (G.j1 - G.j0);
      requestActoDraw();
    } else if (!horizontal && G.rows < G.rowsAll) {
      const next = clamp(G.r0 + Math.sign(ev.deltaY) * Math.max(1, Math.round(G.rows / 6)), 0, G.rowsAll - G.rows);
      if (next !== G.r0) { ev.preventDefault(); Z.r0 = next; requestActoDraw(); }
    }
  }

  /* Mouse tools on the actogram while editing:
     - "Place onset" (default): click places the onset of that cycle,
       Shift-click deletes the nearest onset, and dragging a rectangle zooms
       into it (a flat box zooms time only; a narrow box zooms rows only).
     - "Select for fit": click an onset, or drag a rectangle around several,
       to add them to the active regression fit (or remove them if they are
       all in it already).
     - "Pan", or Shift + drag with any tool: move the zoomed view.
     A drag never counts as a click, and Esc cancels a rectangle in progress. */
  let drag = null, suppressClick = false;
  const zoomHistory = [];
  // Zoom history for the Back button (at most 50 entries).
  function pushZoom() { zoomHistory.push({ zx: Z.zx, zy: Z.zy, j0: Z.j0, r0: Z.r0 }); if (zoomHistory.length > 50) zoomHistory.shift(); }
  function zoomBack() { const z = zoomHistory.pop(); if (z) { Object.assign(Z, z); requestActoDraw(); } }

  // Start a drag: pan (Pan tool or Shift), zoom box (Place tool) or onset selection (Select tool).
  function onActoPointerDown(ev) {
    if (!S.editing || !actoGeom || ev.button !== 0) return;
    const G = actoGeom.G, p = actoPoint(ev);
    if (p.px < G.left || p.px > G.left + G.plotW || p.py < G.top || p.py > G.top + G.rows * G.rowH) return;
    const pan = S.tool === "pan" || ev.shiftKey;
    if (pan && !G.zoomed) return;
    drag = { kind: pan ? "pan" : S.tool === "select" ? "sel" : "box", x: ev.clientX, y: ev.clientY, px: p.px, py: p.py, j0: G.j0, r0: G.r0, moved: false, id: ev.pointerId };
  }
  function boxRect(G, p) {
    // Selection rectangle in canvas pixels, clamped to the plot area.
    const x0 = clamp(Math.min(drag.px, p.px), G.left, G.left + G.plotW), x1 = clamp(Math.max(drag.px, p.px), G.left, G.left + G.plotW);
    const y0 = clamp(Math.min(drag.py, p.py), G.top, G.top + G.rows * G.rowH), y1 = clamp(Math.max(drag.py, p.py), G.top, G.top + G.rows * G.rowH);
    return { x0: x0, x1: x1, y0: y0, y1: y1 };
  }
  // While dragging: move the view (pan) or update the rubber-band rectangle.
  function onActoPointerMove(ev) {
    if (!drag || ev.pointerId !== drag.id) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    if (!drag.moved) {
      drag.moved = true;
      $("acto").style.cursor = drag.kind === "pan" ? "grabbing" : "crosshair";
      $("actoTip").hidden = true;
      try { $("acto").setPointerCapture(drag.id); } catch (e) { /* ignore */ }
    }
    const G = actoGeom.G;
    if (drag.kind === "pan") {
      Z.j0 = drag.j0 - (dx / G.plotW) * (G.j1 - G.j0);
      Z.r0 = drag.r0 - Math.round(dy / G.rowH);
      requestActoDraw();
      return;
    }
    const r = boxRect(G, actoPoint(ev));
    const el = $("zoomSel");
    const cv = $("acto");
    // The overlay sits in the canvas wrapper, which may be scrolled sideways.
    el.style.left = (cv.offsetLeft + r.x0) + "px"; el.style.top = (cv.offsetTop + r.y0) + "px";
    el.style.width = (r.x1 - r.x0) + "px"; el.style.height = (r.y1 - r.y0) + "px";
    el.classList.toggle("select", drag.kind === "sel");
    el.hidden = false;
    drag.rect = r;
  }
  /**
   * Finish a rectangle drag. For onset selection, toggle the onsets inside it
   * in the active fit. For a zoom box, zoom so the box fills the plot: a box
   * lower than one row zooms time only, a box narrower than 8 px zooms rows only.
   */
  function endBox(apply) {
    const el = $("zoomSel"); el.hidden = true;
    if (!apply || !drag || !drag.rect || !actoGeom) return;
    const G = actoGeom.G, r = drag.rect;
    if (drag.kind === "sel") {
      // Onsets whose markers fall inside the rectangle.
      const ks = [];
      for (const m of G.markers || []) if (m.x >= r.x0 && m.x <= r.x1 && m.y >= r.y0 && m.y <= r.y1 && ks.indexOf(m.k) < 0) ks.push(m.k);
      if (ks.length) { toggleInActiveFit(actoGeom.D, ks); renderAll(); }
      return;
    }
    const timeZoom = r.x1 - r.x0 >= 8, rowZoom = r.y1 - r.y0 >= G.rowH * 0.8;
    if (!timeZoom && !rowZoom) return;
    pushZoom();
    if (timeZoom) {
      const ja = G.j0 + ((r.x0 - G.left) / G.plotW) * (G.j1 - G.j0);
      const jb = G.j0 + ((r.x1 - G.left) / G.plotW) * (G.j1 - G.j0);
      Z.zx = clamp(G.span / Math.max(1e-6, jb - ja), 1, ZX_MAX);
      Z.j0 = (ja + jb) / 2 - (G.span / Z.zx) / 2; // centre the box if the zoom limit was hit
    }
    if (rowZoom) {
      const ra = G.r0 + Math.floor((r.y0 - G.top) / G.rowH);
      const rb = G.r0 + Math.min(G.rows - 1, Math.floor((r.y1 - G.top - 1e-6) / G.rowH));
      const n = Math.max(1, rb - ra + 1);
      Z.zy = clamp(G.rowsAll / n, 1, ZY_MAX);
      const nVis = Math.max(1, Math.floor(G.rowsAll / Z.zy + 1e-9));
      Z.r0 = Math.round((ra + rb) / 2 - (nVis - 1) / 2);
    }
    requestActoDraw();
  }
  // End of a drag; apply the rectangle and swallow the click that follows.
  function onActoPointerUp(ev) {
    if (!drag || ev.pointerId !== drag.id) return;
    if (drag.moved) {
      suppressClick = true;
      if (drag.kind === "box" || drag.kind === "sel") endBox(true);
      $("acto").style.cursor = "crosshair";
    }
    drag = null;
  }
  // The browser cancelled the pointer (e.g. touch interrupted): drop the drag.
  function onActoPointerCancel() { if (drag) { endBox(false); drag = null; $("acto").style.cursor = "crosshair"; } }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && drag && (drag.kind === "box" || drag.kind === "sel") && drag.moved) { endBox(false); drag.rect = null; }
  });

  /* ================================================================
   * Generic XY chart (line + optional band, reference lines, strip)
   * ================================================================ */
  function drawXY(cv, spec) {
    const wrap = cv.parentElement;
    const W = Math.max(300, wrap.clientWidth), H = spec.height || 260;
    const ctx = setupCanvas(cv, W, H);
    const P = palette(false);
    const m = { l: 58, r: 14, t: 14, b: spec.strip ? 52 : 40 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    ctx.fillStyle = P.bg; ctx.fillRect(0, 0, W, H);
    const [x0, x1] = spec.xDomain, [y0, y1] = spec.yDomain;
    const X = function (v) { return m.l + (v - x0) / (x1 - x0) * pw; };
    const Y = function (v) { return m.t + ph - (v - y0) / (y1 - y0) * ph; };
    // Grid and axes.
    ctx.font = MONO; ctx.fillStyle = P.muted; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const yt = niceTicks(y0, y1, 5);
    for (const t of yt) { ctx.fillStyle = P.line; ctx.fillRect(m.l, Y(t), pw, 0.5); ctx.fillStyle = P.muted; ctx.fillText(fmtNum(t), m.l - 6, Y(t)); }
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const xt = spec.xTicks || niceTicks(x0, x1, 8);
    for (const t of xt) { ctx.fillStyle = P.line; ctx.fillRect(X(t), m.t + ph, 0.5, 4); ctx.fillStyle = P.muted; ctx.fillText(spec.xFmt ? spec.xFmt(t) : fmtNum(t), X(t), m.t + ph + (spec.strip ? 18 : 6)); }
    ctx.fillStyle = P.muted; ctx.font = SANS;
    ctx.fillText(spec.xLabel || "", m.l + pw / 2, H - 14);
    ctx.save(); ctx.translate(12, m.t + ph / 2); ctx.rotate(-Math.PI / 2); ctx.textBaseline = "middle"; ctx.fillText(spec.yLabel || "", 0, 0); ctx.restore();
    ctx.fillStyle = P.muted; ctx.fillRect(m.l, m.t + ph, pw, 1);
    // Light strip under the plot.
    if (spec.strip) {
      const sy = m.t + ph + 4, sh = 8;
      for (let i = 0; i < spec.strip.length; i++) {
        const v = spec.strip[i]; if (isNaN(v)) continue;
        const xa = X(spec.x[i]), xb = i + 1 < spec.x.length ? X(spec.x[i + 1]) : X(x1);
        // Blend: dark base, light colour weighted by the fraction of cycles that were light.
        ctx.fillStyle = P.dark; ctx.fillRect(xa, sy, xb - xa + 0.3, sh);
        ctx.globalAlpha = Math.max(0, Math.min(1, v)); ctx.fillStyle = P.light; ctx.fillRect(xa, sy, xb - xa + 0.3, sh); ctx.globalAlpha = 1;
      }
    }
    ctx.save(); ctx.beginPath(); ctx.rect(m.l, m.t - 2, pw, ph + 4); ctx.clip();
    for (const s of spec.series) {
      if (s.band) {
        ctx.fillStyle = s.color; ctx.globalAlpha = 0.18; ctx.beginPath();
        let started = false;
        for (let i = 0; i < spec.x.length; i++) { const v = s.band.hi[i]; if (isNaN(v)) continue; const px = X(spec.x[i]), py = Y(v); if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py); }
        for (let i = spec.x.length - 1; i >= 0; i--) { const v = s.band.lo[i]; if (isNaN(v)) continue; ctx.lineTo(X(spec.x[i]), Y(v)); }
        ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
      }
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 2; ctx.setLineDash(s.dash || []); ctx.lineJoin = "round";
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < spec.x.length; i++) {
        const v = s.y[i];
        if (isNaN(v)) { pen = false; continue; }
        const px = X(spec.x[i]), py = Y(v);
        if (!pen) { ctx.moveTo(px, py); pen = true; } else ctx.lineTo(px, py);
      }
      ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.restore();
    // Peak marker with direct label.
    if (spec.marker) {
      const mk = spec.marker, px = X(mk.x), py = Y(mk.y);
      ctx.fillStyle = P.bg; ctx.beginPath(); ctx.arc(px, py, 6, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = P.onset; ctx.beginPath(); ctx.arc(px, py, 4.5, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = P.ink; ctx.font = '600 12px "Public Sans", sans-serif'; ctx.textBaseline = "bottom";
      ctx.textAlign = px > m.l + pw - 120 ? "right" : "left";
      ctx.fillText(mk.label, px + (ctx.textAlign === "left" ? 8 : -8), py - 4);
    }
    cv._xy = { X: X, Y: Y, m: m, pw: pw, ph: ph, spec: spec, x0: x0, x1: x1 };
  }

  // Tooltip for an XY chart: snaps to the nearest x value; fmt(i) returns its HTML.
  function attachXYHover(cv, tip, fmt) {
    cv.onmousemove = function (ev) {
      const g = cv._xy; if (!g) return;
      const rect = cv.getBoundingClientRect();
      const px = ev.clientX - rect.left;
      if (px < g.m.l || px > g.m.l + g.pw) { tip.hidden = true; return; }
      const xv = g.x0 + (px - g.m.l) / g.pw * (g.x1 - g.x0);
      const xs = g.spec.x;
      let lo = 0, hi = xs.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < xv) lo = mid; else hi = mid; }
      const i = Math.abs(xs[lo] - xv) < Math.abs(xs[hi] - xv) ? lo : hi;
      tip.innerHTML = fmt(i);
      tip.hidden = false;
      tip.style.left = Math.min(px + 12, cv.parentElement.clientWidth - tip.offsetWidth - 4) + "px";
      tip.style.top = "8px";
    };
    cv.onmouseleave = function () { tip.hidden = true; };
  }

  // About n evenly spaced "nice" tick values (1, 2, 2.5, 5 x 10^k) between a and b.
  function niceTicks(a, b, n) {
    const span = b - a; if (!(span > 0)) return [a];
    const raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const st = [1, 2, 2.5, 5, 10].map(function (s) { return s * mag; }).find(function (s) { return span / s <= n; }) || mag * 10;
    const out = [];
    for (let v = Math.ceil(a / st) * st; v <= b + st * 1e-6; v += st) out.push(+v.toFixed(10));
    return out;
  }
  // Compact axis number: 12000 -> "12k", 0.25 -> "0.25", 12.5 -> "12.5".
  function fmtNum(v) {
    const a = Math.abs(v);
    if (a >= 10000) return (v / 1000).toFixed(0) + "k";
    if (a >= 100 || v === Math.round(v)) return String(Math.round(v));
    return v.toFixed(a < 1 ? 2 : 1);
  }
  const f2 = function (v, d) { return v === null || v === undefined || isNaN(v) ? "–" : v.toFixed(d === undefined ? 2 : d); };
  // Signed hours as "+1h05" / "−0h30" (used for the phase angle psi).
  const hm = function (h) { if (h === null || isNaN(h)) return "–"; const s = h < 0 ? "−" : "+"; const a = Math.abs(h); const H = Math.floor(a), M = Math.round((a - H) * 60); return s + H + "h" + String(M === 60 ? 0 : M).padStart(2, "0"); };

  /* ================================================================
   * Tabs
   * ================================================================ */
  function statBox(k, v, s) { return '<div class="stat"><div class="k">' + k + '</div><div class="v">' + v + "</div>" + (s ? '<div class="s">' + s + "</div>" : "") + "</div>"; }
  // Markup for a chart: canvas plus its tooltip, inside a positioned wrapper.
  function chartHTML(id) { return '<div class="canvas-wrap"><canvas id="' + id + '"></canvas><div class="tip" id="' + id + 'Tip" hidden></div></div>'; }
  // Escape text before inserting it into HTML (file names come from the user).
  const esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };

  // Short description of the analysis range, e.g. "rows 1–36 (35.6 d, 6-min bins)".
  function rangeText(D) {
    return "rows " + (D.k1 + 1) + "–" + (D.k2 + 1) + " (" + ((D.b - D.a) * D.binMin / 1440).toFixed(1) + " d, " + D.binMin + "-min bins)";
  }

  // Render the selected analysis tab into #tabBody (see the tab list in the markup).
  function renderTab() {
    const F = curFile();
    const body = $("tabBody");
    document.querySelectorAll(".tab").forEach(function (t) { t.setAttribute("aria-selected", t.dataset.tab === S.tab ? "true" : "false"); });
    if (S.tab === "help") { body.innerHTML = methodsHTML(); return; }
    if (!F) { body.innerHTML = '<div class="empty">Load an AWD file or a demo recording to begin.</div>'; return; }
    const D = derive(F);
    ({ period: tabPeriod, profile: tabProfile, onsets: tabOnsets, daily: tabDaily, light: tabLight, batch: tabBatch, info: tabInfo })[S.tab](D, body);
  }

  // ---- Periodogram ----
  function tabPeriod(D, body) {
    const pg = periodogram(D);
    const isChi = S.ana.method !== "ls";
    const pk = pg.peak;
    body.innerHTML =
      '<div class="toolbar"><div class="row">' +
      '<label class="check">Method <select id="pgMethod" style="width:auto"><option value="chi">χ² (Sokolove–Bushell)</option><option value="ls">Lomb–Scargle</option></select></label>' +
      '<label class="check">From <input type="number" id="pgMin" style="width:64px" step="0.5" value="' + S.ana.pmin + '"> to <input type="number" id="pgMax" style="width:64px" step="0.5" value="' + S.ana.pmax + '"> h</label>' +
      '<label class="check">α <select id="pgAlpha" style="width:auto"><option>0.05</option><option>0.01</option><option>0.001</option></select></label>' +
      '</div><button id="pgCsv" type="button">Save CSV</button></div>' +
      '<div class="stats">' +
      statBox("Peak period", pk ? pk.periodH.toFixed(2) + " h" : "–", pk ? (pk.significant ? "significant at α = " + S.ana.alpha : "not significant") : "") +
      statBox(isChi ? "Qp at peak" : "Normalised power", pk ? f2(pk.value, isChi ? 0 : 1) : "–", "threshold " + (pk ? f2(pk.crit, isChi ? 0 : 1) : "–")) +
      statBox(isChi ? "Amplitude (Qp − crit)" : "Power − threshold", pk ? f2(pk.amp, isChi ? 0 : 1) : "–", "relative rhythm strength") +
      statBox("Data analysed", (D.b - D.a) * D.binMin / 1440 >= 1 ? ((D.b - D.a) * D.binMin / 1440).toFixed(1) + " d" : "–", rangeText(D)) +
      "</div>" + chartHTML("pgCanvas") +
      '<p class="note">' + (isChi ? "Dashed line: χ² critical value for df = P − 1 at the chosen α (Wilson–Hilferty approximation). Resolution equals one bin; use smaller bins for finer period steps." : "Dashed line: false-alarm threshold for the chosen α, assuming N independent frequencies. Missing bins are skipped.") + "</p>";
    $("pgMethod").value = S.ana.method; $("pgAlpha").value = String(S.ana.alpha);
    $("pgMethod").onchange = function (e) { S.ana.method = e.target.value; renderTab(); };
    $("pgAlpha").onchange = function (e) { S.ana.alpha = +e.target.value; renderTab(); };
    $("pgMin").onchange = function (e) { S.ana.pmin = Math.max(1, +e.target.value || 18); renderTab(); };
    $("pgMax").onchange = function (e) { S.ana.pmax = Math.max(S.ana.pmin + 1, +e.target.value || 30); renderTab(); };
    $("pgCsv").onclick = function () {
      const rows = [["period_h", isChi ? "Qp" : "power", "critical", "minus_critical"]].concat(pg.points.map(function (p) { return [p.periodH.toFixed(4), p.value.toFixed(4), p.crit.toFixed(4), p.amp.toFixed(4)]; }));
      saveText(baseName(D.F) + "_periodogram_" + S.ana.method + ".csv", toCSV(rows));
    };
    if (!pg.points.length) return;
    const xs = pg.points.map(function (p) { return p.periodH; });
    const ys = pg.points.map(function (p) { return p.value; });
    const cr = pg.points.map(function (p) { return p.crit; });
    const ymax = Math.max.apply(null, ys.concat(cr)) * 1.08;
    const P = palette(false);
    drawXY($("pgCanvas"), {
      x: xs, xDomain: [xs[0], xs[xs.length - 1]], yDomain: [0, ymax],
      xLabel: "Period (h)", yLabel: isChi ? "Qp" : "Normalised power",
      series: [{ y: cr, color: P.muted, width: 1.5, dash: [4, 4] }, { y: ys, color: P.accent, width: 2 }],
      marker: pk ? { x: pk.periodH, y: pk.value, label: pk.periodH.toFixed(2) + " h" } : null
    });
    attachXYHover($("pgCanvas"), $("pgCanvasTip"), function (i) { return xs[i].toFixed(2) + " h · " + (isChi ? "Qp " : "power ") + f2(ys[i], 1) + " · crit " + f2(cr[i], 1); });
  }

  // ---- Activity profile ----
  function tabProfile(D, body) {
    const pg = S.ana.fold === "peak" ? periodogram(D) : null;
    let PH = S.ana.fold === "peak" && pg && pg.peak ? pg.peak.periodH : S.ana.fold === "custom" ? S.ana.foldCustom : S.disp.T;
    const Pb = Math.max(2, Math.round(PH * 60 / D.binMin));
    PH = Pb * D.binMin / 60;
    const prof = A.foldProfile(D.x, 0, Pb);
    const lightX = D.light.subarray(D.a, D.b);
    const lp = A.foldProfile(lightX, 0, Pb);
    // Phase 0 = start of the first analysed row.
    const x = prof.map(function (p) { return p.phase * D.binMin / 60; });
    const mean = prof.map(function (p) { return p.mean; });
    const lo = prof.map(function (p) { return p.mean - p.sem; }), hi = prof.map(function (p) { return p.mean + p.sem; });
    let tot = 0, dark = 0, light = 0;
    for (let i = 0; i < D.x.length; i++) { const v = D.x[i]; if (isNaN(v)) continue; tot += v; const s = lightX[i]; if (s === 0) dark += v; else if (s === 1) light += v; }
    let pkI = 0; mean.forEach(function (v, i) { if (v > mean[pkI]) pkI = i; });
    const np = A.nonParametric(D.x, D.binMin, D.a);
    const phaseClock = function (h) { return S.disp.T === 24 && Math.abs(PH - 24) < 1e-6 ? A.fmtClock((S.disp.offsetH + h) * 60) : h.toFixed(1) + " h"; };
    body.innerHTML =
      '<div class="toolbar"><div class="row">' +
      '<label class="check">Fold at <select id="foldSel" style="width:auto"><option value="row">Row period (' + S.disp.T + ' h)</option><option value="peak">Periodogram peak</option><option value="custom">Custom</option></select></label>' +
      '<input type="number" id="foldCustom" style="width:72px" step="0.05" value="' + S.ana.foldCustom + '"' + (S.ana.fold === "custom" ? "" : " hidden") + "></div>" +
      '<button id="profCsv" type="button">Save CSV</button></div>' +
      '<div class="stats">' +
      statBox("Folding period", PH.toFixed(2) + " h", rangeText(D)) +
      statBox("Peak phase", phaseClock(x[pkI]), "mean " + f2(mean[pkI], 0) + " counts/bin") +
      statBox("Activity in dark", tot && (dark + light) ? (100 * dark / (dark + light)).toFixed(1) + "%" : "–", (dark + light) ? "of activity with known lighting" : "no lighting information") +
      statBox("Mean daily total", tot ? Math.round(tot / ((D.b - D.a) * D.binMin / 1440)).toLocaleString() : "–", "counts per 24 h") +
      (np ? statBox("IS / IV", f2(np.IS) + " / " + f2(np.IV), "interdaily stability / intradaily variability") +
        statBox("RA", f2(np.RA), "M10 from " + A.fmtClock(np.M10start * 60) + ", L5 from " + A.fmtClock(np.L5start * 60)) : "") +
      "</div>" + chartHTML("profCanvas") +
      '<p class="note">Mean ± SEM of each bin across cycles. Phase 0 is the start of row ' + (D.k1 + 1) + '. The strip under the axis shows the lighting at each phase; its colour is weighted by the fraction of cycles that were light. IS, IV and RA use hourly totals on a 24-h clock regardless of the folding period.</p>';
    $("foldSel").value = S.ana.fold;
    $("foldSel").onchange = function (e) { S.ana.fold = e.target.value; renderTab(); };
    $("foldCustom").onchange = function (e) { S.ana.foldCustom = Math.max(1, +e.target.value || 24); renderTab(); };
    $("profCsv").onclick = function () {
      const rows = [["phase_h", "mean", "sem", "n_cycles", "light_fraction"]].concat(prof.map(function (p, i) { return [x[i].toFixed(4), f2(p.mean, 4), f2(p.sem, 4), p.n, f2(lp[i].mean, 3)]; }));
      saveText(baseName(D.F) + "_profile.csv", toCSV(rows));
    };
    const ymax = Math.max.apply(null, hi.filter(function (v) { return !isNaN(v); }).concat([1])) * 1.08;
    const strip = D.F.lightSrc !== "none" ? lp.map(function (p) { return p.mean; }) : null;
    const step = PH <= 30 ? 3 : 6;
    const ticks = []; for (let h = 0; h <= PH + 1e-6; h += step) ticks.push(h);
    drawXY($("profCanvas"), {
      x: x, xDomain: [0, PH], yDomain: [0, ymax], xTicks: ticks,
      xFmt: function (h) { return S.disp.T === 24 && Math.abs(PH - 24) < 1e-6 ? A.fmtClock((S.disp.offsetH + h) * 60).replace(":00", "") : String(h); },
      xLabel: S.disp.T === 24 && Math.abs(PH - 24) < 1e-6 ? "Clock time (h)" : "Phase (h from row start)", yLabel: "Counts per " + D.binMin + " min",
      series: [{ y: mean, color: palette(false).accent, width: 2, band: { lo: lo, hi: hi } }], strip: strip
    });
    attachXYHover($("profCanvas"), $("profCanvasTip"), function (i) { return phaseClock(x[i]) + " · " + f2(mean[i], 1) + " ± " + f2(prof[i].sem, 1) + " (n=" + prof[i].n + ")"; });
  }

  // ---- Onsets ----
  function tabOnsets(D, body) {
    const O = onsets(D);
    const unit = S.ana.phaseUnit;
    const inR = O.list.filter(function (o) { return o.k >= D.k1 && o.k <= D.k2; });
    const psis = inR.map(function (o) { return o.psi; }).filter(function (v) { return v !== null; });
    const mPsi = psis.length ? psis.reduce(function (a, b) { return a + b; }, 0) / psis.length : NaN;
    const sdPsi = psis.length > 1 ? Math.sqrt(psis.reduce(function (a, b) { return a + (b - mPsi) * (b - mPsi); }, 0) / (psis.length - 1)) : NaN;
    const alphas = inR.filter(function (o) { return o.onsetBin !== null && o.offsetBin !== null; }).map(function (o) { return (o.offsetBin - o.onsetBin) * D.binMin / 60; });
    const mAlpha = alphas.length ? alphas.reduce(function (a, b) { return a + b; }, 0) / alphas.length : NaN;
    const nEdited = Object.keys(D.F.manual).length;
    const segs = condSegments(D);
    const segLabel = function (si) { const s = segs[si]; if (!s) return "–"; return s.mode + " (day " + s.startDay + (s.endDay ? "–" + s.endDay : "–") + ")"; };
    const fitCard = function (f) {
      const sw = '<span class="sw" style="background:var(--fit' + (f.i % 6 + 1) + ');border-radius:50%"></span>';
      if (!f.fit) return '<div class="stat"><div class="k">' + sw + esc(f.name) + '</div><div class="v">–</div><div class="s">' + (f.members.length ? "needs ≥ 2 onsets" : "no onsets selected") + "</div></div>";
      const ph = f.phase ? fmtPhase(f.phase) : "–";
      const phOther = f.phase ? fmtPhase(f.phase, unit === "deg" ? "ztct" : "deg") : "";
      return '<div class="stat"><div class="k">' + sw + esc(f.name) + " · " + (f.segIndex >= 0 ? segLabel(f.segIndex) : "no lighting info") + '</div>' +
        '<div class="v">τ ' + f.fit.slope.toFixed(3) + " h</div>" +
        '<div class="s">± ' + f2(f.fit.se, 3) + " SE · r² " + f2(f.fit.r2, 3) + " · n " + f.fit.n + " · rows " + (f.kMin + 1) + "–" + (f.kMax + 1) + "</div>" +
        '<div class="s">Phase ' + ph + (phOther ? " (" + phOther + ")" : "") + (f.phase ? " · " + f.phase.how : "") + "</div></div>";
    };
    body.innerHTML =
      '<div class="toolbar"><div class="row">' +
      '<label class="check">Phase as <select id="phUnit" style="width:auto"><option value="ztct">ZT / CT (h)</option><option value="deg">Degrees (0–360°)</option></select></label>' +
      '<label class="check">Free-run 0° <select id="phRef" style="width:auto"><option value="ld">Projected ZT0 of preceding LD</option><option value="start">Start of the condition</option><option value="rec">00:00 of recording day 1</option></select></label>' +
      '</div><div class="row"><button id="fitCsv" type="button">Save fits CSV</button><button id="onCsv" type="button">Save onsets CSV</button></div></div>' +
      '<div class="stats">' + O.fits.map(fitCard).join("") + "</div>" +
      '<div class="stats">' +
      statBox("Phase angle ψ", psis.length ? hm(mPsi) : "–", psis.length ? "SD " + f2(sdPsi, 2) + " h · n " + psis.length + " · onset − lights-off" : "no lights-off within ±6 h of onsets") +
      statBox("Activity duration α", alphas.length ? f2(mAlpha, 2) + " h" : "–", "onset to offset, mean of " + alphas.length) +
      statBox("Onsets edited", String(nEdited), nEdited ? "placed or deleted by hand" : "all automatic") +
      "</div>" +
      '<div class="tbl-wrap"><table><thead><tr><th>Row</th><th>Date</th><th>Condition</th><th>Onset</th><th>Phase</th><th>Offset</th><th>α (h)</th><th>ψ (h)</th><th>Fit</th><th>Residual (h)</th><th>Source</th></tr></thead><tbody>' +
      O.list.map(function (o) {
        const on = o.onsetBin === null ? null : o.onsetBin * D.binMin;
        const off = o.offsetBin === null ? null : o.offsetBin * D.binMin;
        const fi = O.map[o.k];
        const f = fi !== undefined ? O.fits[fi] : null;
        const res = f && f.fit && on !== null ? on / 60 - (f.fit.intercept + f.fit.slope * o.k) : NaN;
        const si = on !== null ? segIndexAt(segs, on) : -1;
        const fitCell = f ? '<span class="sw" style="background:var(--fit' + (fi % 6 + 1) + ');border-radius:50%"></span>' + esc(f.name) : "–";
        return "<tr" + (f ? "" : ' style="opacity:.55"') + "><td>" + (o.k + 1) + "</td><td>" + (on !== null ? A.fmtDate(D.F.rec.startUTC, on) : "–") + "</td><td>" + (si >= 0 ? segs[si].mode : "–") + '</td><td class="mono">' + (on !== null ? A.fmtClock(on) : "–") + '</td><td class="mono">' + fmtPhase(o.phase) + '</td><td class="mono">' + (off !== null ? A.fmtClock(off) : "–") + "</td><td>" + (on !== null && off !== null ? f2((off - on) / 60) : "–") + "</td><td>" + f2(o.psi) + '</td><td style="text-align:left">' + fitCell + "</td><td>" + f2(res) + "</td><td>" + (o.manual ? (o.onsetBin === null ? "deleted" : "edited") : "auto") + "</td></tr>";
      }).join("") + "</tbody></table></div>" +
      '<p class="note">Each fit is a linear regression of onset time on cycle number; its slope is the period τ. Under LD the phase is zeitgeber time (ZT0 = lights-on). Under DD or LL the phase is circadian time computed with the free-running period of the fit for that condition and the 0° reference chosen above (CT = phase × 24 / 360). Dimmed rows are not used in any fit. ψ and α use onsets in the analysis range.</p>';
    $("phUnit").value = unit; $("phRef").value = S.ana.phaseRef;
    $("phUnit").onchange = function (e) { S.ana.phaseUnit = e.target.value; renderTab(); };
    $("phRef").onchange = function (e) { S.ana.phaseRef = e.target.value; renderAll(); };
    $("onCsv").onclick = function () {
      const rows = [["row", "date", "condition", "onset_clock", "onset_abs_h", "phase_label", "phase_h", "phase_deg", "offset_clock", "offset_abs_h", "alpha_h", "psi_h", "fit", "residual_h", "source"]];
      for (const o of O.list) {
        const on = o.onsetBin === null ? null : o.onsetBin * D.binMin, off = o.offsetBin === null ? null : o.offsetBin * D.binMin;
        const fi = O.map[o.k], f = fi !== undefined ? O.fits[fi] : null;
        const res = f && f.fit && on !== null ? on / 60 - (f.fit.intercept + f.fit.slope * o.k) : NaN;
        const si = on !== null ? segIndexAt(segs, on) : -1;
        const p = o.phase;
        rows.push([o.k + 1, on !== null ? A.fmtDate(D.F.rec.startUTC, on) : "", si >= 0 ? segs[si].mode : "", on !== null ? A.fmtClock(on) : "", on !== null ? (on / 60).toFixed(3) : "",
          p ? p.label : "", p && isFinite(p.h) ? p.h.toFixed(3) : "", p && isFinite(p.deg) ? p.deg.toFixed(2) : "",
          off !== null ? A.fmtClock(off) : "", off !== null ? (off / 60).toFixed(3) : "", on !== null && off !== null ? ((off - on) / 60).toFixed(3) : "", o.psi === null ? "" : o.psi.toFixed(3),
          f ? f.name : "", isFinite(res) ? res.toFixed(3) : "", o.manual ? "manual" : "auto"]);
      }
      saveText(baseName(D.F) + "_onsets.csv", toCSV(rows));
    };
    $("fitCsv").onclick = function () {
      const rows = [["fit", "condition", "first_row", "last_row", "n", "tau_h", "tau_se_h", "r2", "intercept_h", "phase_label", "phase_h", "phase_deg", "phase_method", "free_run_reference"]];
      for (const f of O.fits) {
        rows.push([f.name, f.segIndex >= 0 ? segLabel(f.segIndex) : "", f.kMin !== null ? f.kMin + 1 : "", f.kMax !== null ? f.kMax + 1 : "", f.members.length,
          f.fit ? f.fit.slope.toFixed(4) : "", f.fit ? f2(f.fit.se, 4) : "", f.fit ? f2(f.fit.r2, 4) : "", f.fit ? f.fit.intercept.toFixed(4) : "",
          f.phase ? f.phase.label : "", f.phase ? f.phase.h.toFixed(3) : "", f.phase ? f.phase.deg.toFixed(2) : "", f.phase ? f.phase.how : "", f.mode !== "LD" ? PHASE_REF_LABEL[S.ana.phaseRef] : ""]);
      }
      saveText(baseName(D.F) + "_fits.csv", toCSV(rows));
    };
  }

  // ---- Daily activity ----
  function dailyRows(D) {
    const out = [];
    for (let k = D.kStart; k < D.nRows; k++) {
      const a = D.rowStart(k), b = D.rowStart(k + 1);
      let tot = 0, li = 0, da = 0, miss = 0, n = 0, luxS = 0, luxN = 0, lightBins = 0, known = 0;
      for (let t = Math.max(0, a); t < Math.min(D.B.n, b); t++) {
        if (t < D.B.firstBin) continue;
        n++;
        const v = D.B.act[t];
        const s = D.light[t];
        if (!isNaN(s)) { known++; if (s === 1) lightBins++; }
        if (isNaN(v)) { miss++; continue; }
        tot += v;
        if (s === 1) li += v; else if (s === 0) da += v;
        if (D.B.lux && !isNaN(D.B.lux[t])) { luxS += D.B.lux[t]; luxN++; }
      }
      out.push({ k: k, date: A.fmtDate(D.F.rec.startUTC, a * D.binMin + 1), total: tot, light: li, dark: da, missPct: n ? 100 * miss / n : 100, cover: n / D.Pb, lightH: known ? lightBins * D.binMin / 60 : NaN, meanLux: luxN ? luxS / luxN : NaN });
    }
    return out;
  }
  // Daily activity tab: stacked bars (dark / light / unknown) and a per-row table.
  function tabDaily(D, body) {
    const rows = dailyRows(D);
    body.innerHTML =
      '<div class="toolbar"><div class="legend"><span><span class="sw" style="background:var(--accent)"></span>Activity in dark</span><span><span class="sw" style="background:#d9a400"></span>Activity in light</span><span><span class="sw" style="background:var(--muted)"></span>Lighting unknown</span></div><button id="dayCsv" type="button">Save CSV</button></div>' +
      chartHTML("dayCanvas") +
      '<div class="tbl-wrap" style="margin-top:10px"><table><thead><tr><th>Row</th><th>Date</th><th>Total counts</th><th>In dark</th><th>In light</th><th>% in dark</th><th>Light (h)</th><th>Missing</th><th>Coverage</th></tr></thead><tbody>' +
      rows.map(function (r) { const known = r.light + r.dark; return "<tr><td>" + (r.k + 1) + "</td><td>" + r.date + "</td><td>" + Math.round(r.total).toLocaleString() + "</td><td>" + Math.round(r.dark).toLocaleString() + "</td><td>" + Math.round(r.light).toLocaleString() + "</td><td>" + (known ? (100 * r.dark / known).toFixed(1) : "–") + "</td><td>" + f2(r.lightH, 1) + "</td><td>" + r.missPct.toFixed(0) + "%</td><td>" + (100 * Math.min(1, r.cover)).toFixed(0) + "%</td></tr>"; }).join("") +
      "</tbody></table></div><p class=\"note\">One row of the actogram per line. Partially recorded rows (first and last) show coverage below 100%.</p>";
    $("dayCsv").onclick = function () {
      const out = [["row", "date", "total", "dark", "light", "pct_dark", "light_hours", "missing_pct", "coverage_pct"]].concat(rows.map(function (r) { const kn = r.light + r.dark; return [r.k + 1, r.date, Math.round(r.total), Math.round(r.dark), Math.round(r.light), kn ? (100 * r.dark / kn).toFixed(2) : "", isNaN(r.lightH) ? "" : r.lightH.toFixed(2), r.missPct.toFixed(1), (100 * Math.min(1, r.cover)).toFixed(1)]; }));
      saveText(baseName(D.F) + "_daily.csv", toCSV(out));
    };
    drawDailyBars($("dayCanvas"), rows, D);
  }
  // Stacked bar chart of activity per actogram row; rows outside the analysis range are faded.
  function drawDailyBars(cv, rows, D) {
    const W = Math.max(300, cv.parentElement.clientWidth), H = 220;
    const ctx = setupCanvas(cv, W, H);
    const P = palette(false);
    const m = { l: 58, r: 10, t: 12, b: 34 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    ctx.fillStyle = P.bg; ctx.fillRect(0, 0, W, H);
    const ymax = Math.max(1, Math.max.apply(null, rows.map(function (r) { return r.total; }))) * 1.08;
    const Y = function (v) { return m.t + ph - v / ymax * ph; };
    ctx.font = MONO; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (const t of niceTicks(0, ymax, 4)) { ctx.fillStyle = P.line; ctx.fillRect(m.l, Y(t), pw, 0.5); ctx.fillStyle = P.muted; ctx.fillText(fmtNum(t), m.l - 6, Y(t)); }
    const bw = pw / rows.length;
    const amber = "#d9a400";
    rows.forEach(function (r, i) {
      const x = m.l + i * bw + Math.min(2, bw * 0.15), w = Math.max(1, bw - Math.min(4, bw * 0.3));
      const unknown = Math.max(0, r.total - r.dark - r.light);
      let y = m.t + ph;
      const seg = function (v, c) { if (v <= 0) return; const h = v / ymax * ph; ctx.fillStyle = c; ctx.fillRect(x, y - h, w, h); y -= h + (h > 3 ? 1 : 0); };
      seg(r.dark, P.accent); seg(r.light, amber); seg(unknown, P.muted);
      const inR = r.k >= D.k1 && r.k <= D.k2;
      if (!inR) { ctx.fillStyle = P.bg; ctx.globalAlpha = 0.5; ctx.fillRect(x, m.t, w, ph); ctx.globalAlpha = 1; }
    });
    ctx.fillStyle = P.muted; ctx.fillRect(m.l, m.t + ph, pw, 1);
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const every = Math.ceil(rows.length / Math.max(1, pw / 28));
    rows.forEach(function (r, i) { if ((r.k + 1) % every === 0 || i === 0) ctx.fillText(String(r.k + 1), m.l + (i + 0.5) * bw, m.t + ph + 5); });
    ctx.font = SANS; ctx.fillText("Actogram row", m.l + pw / 2, H - 14);
    const tip = $("dayCanvasTip");
    cv.onmousemove = function (ev) {
      const rect = cv.getBoundingClientRect(); const px = ev.clientX - rect.left;
      const i = Math.floor((px - m.l) / bw);
      if (i < 0 || i >= rows.length) { tip.hidden = true; return; }
      const r = rows[i];
      tip.innerHTML = "Row " + (r.k + 1) + " · " + r.date + "<br>Total " + Math.round(r.total).toLocaleString() + " · dark " + Math.round(r.dark).toLocaleString() + " · light " + Math.round(r.light).toLocaleString();
      tip.hidden = false; tip.style.left = Math.min(px + 12, W - tip.offsetWidth - 4) + "px"; tip.style.top = "8px";
    };
    cv.onmouseleave = function () { tip.hidden = true; };
  }

  // ---- Lighting record ----
  function tabLight(D, body) {
    const F = D.F;
    if (!F.rec.light) {
      body.innerHTML = '<div class="empty">This file has no light column (the optional second value on each data line).<br>Use <b>Lighting → Schedule</b> in the left panel to describe the light regime by hand.</div>';
      return;
    }
    const det = A.detectLighting(F.rec, F.luxThr);
    const segTxt = det.segments.map(function (s) {
      const lab = s.mode === "LD" ? "LD " + f2(s.L, 1).replace(".0", "") + ":" + f2(s.D, 1).replace(".0", "") + " · on " + (s.onMin !== null ? A.fmtClock(s.onMin) : "?") + ", off " + (s.offMin !== null ? A.fmtClock(s.offMin) : "?") + " · ~" + Math.round(s.meanLux) + " lux" : s.mode === "LL" ? "LL · ~" + Math.round(s.meanLux) + " lux" : "DD";
      return '<div class="stat"><div class="k">Days ' + s.startDay + "–" + s.endDay + '</div><div class="v">' + s.mode + '</div><div class="s">' + lab + "</div></div>";
    }).join("");
    body.innerHTML =
      '<div class="toolbar"><span class="note">Detected from the light column with threshold ' + F.luxThr + ' (change it under Lighting). Days are calendar days from the start date.</span><button id="ltCsv" type="button">Save CSV</button></div>' +
      '<div class="stats">' + segTxt + "</div>" + chartHTML("luxCanvas") +
      '<div class="tbl-wrap" style="margin-top:10px"><table><thead><tr><th>Day</th><th>Date</th><th>Regime</th><th>Lights on</th><th>Lights off</th><th>Light (h)</th><th>Mean lux in light</th><th>Transitions</th><th>Coverage</th></tr></thead><tbody>' +
      det.days.map(function (r) { return "<tr><td>" + r.day + "</td><td>" + A.fmtDate(F.rec.startUTC, (r.day - 1) * 1440) + "</td><td>" + r.mode + '</td><td class="mono">' + (r.onMin !== null ? A.fmtClock(r.onMin) : "–") + '</td><td class="mono">' + (r.offMin !== null ? A.fmtClock(r.offMin) : "–") + "</td><td>" + f2(r.lightHours, 1) + "</td><td>" + (r.nLight ? Math.round(r.meanLux) : "–") + "</td><td>" + (r.on.length + r.off.length) + "</td><td>" + (100 * Math.min(1, r.cover)).toFixed(0) + "%</td></tr>"; }).join("") +
      "</tbody></table></div>";
    $("ltCsv").onclick = function () {
      const rows = [["day", "date", "regime", "lights_on", "lights_off", "light_hours", "mean_lux_in_light", "transitions", "coverage_pct"]].concat(det.days.map(function (r) { return [r.day, A.fmtDate(F.rec.startUTC, (r.day - 1) * 1440), r.mode, r.onMin !== null ? A.fmtClock(r.onMin) : "", r.offMin !== null ? A.fmtClock(r.offMin) : "", isNaN(r.lightHours) ? "" : r.lightHours.toFixed(2), r.nLight ? r.meanLux.toFixed(1) : "", r.on.length + r.off.length, (100 * Math.min(1, r.cover)).toFixed(1)]; }));
      saveText(baseName(F) + "_lighting.csv", toCSV(rows));
    };
    // Light trace over the whole record (hourly means).
    const perH = Math.max(1, Math.round(60 / D.binMin));
    const xs = [], ys = [];
    for (let b = D.B.firstBin; b < D.B.n; b += perH) {
      let s = 0, n = 0;
      for (let j = 0; j < perH && b + j < D.B.n; j++) { const v = D.B.lux[b + j]; if (!isNaN(v)) { s += v; n++; } }
      xs.push(b * D.binMin / 1440); ys.push(n ? s / n : NaN);
    }
    const ymax = Math.max(1, Math.max.apply(null, ys.filter(function (v) { return !isNaN(v); }))) * 1.1;
    drawXY($("luxCanvas"), { x: xs, xDomain: [Math.floor(xs[0]), Math.ceil(xs[xs.length - 1])], yDomain: [0, ymax], xLabel: "Day of recording", yLabel: "Light (hourly mean)", height: 180, xFmt: function (d) { return String(d + 1); }, series: [{ y: ys, color: "#c48a00", width: 1.5 }] });
    attachXYHover($("luxCanvas"), $("luxCanvasTip"), function (i) { const min = xs[i] * 1440; return A.fmtDate(F.rec.startUTC, min) + " " + A.fmtClock(min) + " · " + f2(ys[i], 0); });
  }

  // ---- All files (batch) ----
  function batchRows() {
    return S.files.map(function (F) {
      const D = derive(F);
      const chi = A.chiSquarePeriodogram(D.x, D.binMin, S.ana.pmin, S.ana.pmax, S.ana.alpha);
      const ls = A.lombScargle(D.x, D.binMin, S.ana.pmin, S.ana.pmax, S.ana.alpha, Math.max(0.05, D.binMin / 60));
      const O = onsets(D);
      const psis = O.list.filter(function (o) { return o.k >= D.k1 && o.k <= D.k2 && o.psi !== null; }).map(function (o) { return o.psi; });
      const np = A.nonParametric(D.x, D.binMin, D.a);
      let dark = 0, light = 0;
      const lx = D.light.subarray(D.a, D.b);
      for (let i = 0; i < D.x.length; i++) { const v = D.x[i]; if (isNaN(v)) continue; if (lx[i] === 0) dark += v; else if (lx[i] === 1) light += v; }
      return {
        name: F.rec.name, file: F.rec.fileName, rows: (D.k1 + 1) + "–" + (D.k2 + 1), days: (D.b - D.a) * D.binMin / 1440,
        chi: chi.peak ? chi.peak.periodH : NaN, chiAmp: chi.peak ? chi.peak.amp : NaN, chiSig: chi.peak ? chi.peak.significant : false,
        ls: ls.peak ? ls.peak.periodH : NaN, tau: O.fit ? O.fit.slope : NaN, tauSE: O.fit ? O.fit.se : NaN,
        psi: psis.length ? psis.reduce(function (a, b) { return a + b; }, 0) / psis.length : NaN,
        IS: np ? np.IS : NaN, IV: np ? np.IV : NaN, RA: np ? np.RA : NaN, pctDark: dark + light ? 100 * dark / (dark + light) : NaN
      };
    });
  }
  // All files tab: one summary row per loaded recording.
  function tabBatch(D, body) {
    const rows = batchRows();
    body.innerHTML =
      '<div class="toolbar"><span class="note">Each file uses its own analysis range and lighting settings; its own bin size (multiple of its sampling interval); row period and periodogram settings are shared.</span><button id="batchCsv" type="button">Save CSV</button></div>' +
      '<div class="tbl-wrap"><table><thead><tr><th>File</th><th>Rows</th><th>Days</th><th>χ² peak (h)</th><th>χ² amp.</th><th>LS peak (h)</th><th>τ Fit 1 (h)</th><th>ψ (h)</th><th>% in dark</th><th>IS</th><th>IV</th><th>RA</th></tr></thead><tbody>' +
      rows.map(function (r, i) { return "<tr" + (i === S.cur ? ' style="font-weight:600"' : "") + '><td class="mono">' + esc(r.name) + "</td><td>" + r.rows + "</td><td>" + f2(r.days, 1) + "</td><td>" + f2(r.chi) + (r.chiSig ? "" : " ns") + "</td><td>" + f2(r.chiAmp, 0) + "</td><td>" + f2(r.ls) + "</td><td>" + f2(r.tau, 3) + "</td><td>" + f2(r.psi) + "</td><td>" + f2(r.pctDark, 1) + "</td><td>" + f2(r.IS) + "</td><td>" + f2(r.IV) + "</td><td>" + f2(r.RA) + "</td></tr>"; }).join("") +
      "</tbody></table></div><p class=\"note\">ns = χ² peak not significant at α = " + S.ana.alpha + ".</p>";
    $("batchCsv").onclick = function () {
      const out = [["name", "file", "rows", "days", "chi2_peak_h", "chi2_amplitude", "chi2_significant", "ls_peak_h", "tau_onsets_h", "tau_se_h", "psi_h", "pct_activity_dark", "IS", "IV", "RA"]].concat(rows.map(function (r) { return [r.name, r.file, r.rows, r.days.toFixed(2), f2(r.chi, 3), f2(r.chiAmp, 1), r.chiSig ? 1 : 0, f2(r.ls, 3), f2(r.tau, 4), f2(r.tauSE, 4), f2(r.psi, 3), f2(r.pctDark, 2), f2(r.IS, 4), f2(r.IV, 4), f2(r.RA, 4)].map(function (v) { return v === "–" ? "" : v; }); }));
      saveText("all_files_summary.csv", toCSV(out));
    };
  }

  // ---- File info ----
  function tabInfo(D, body) {
    const R = D.F.rec;
    const endMin = R.t0Min + R.counts.length * R.intervalMin;
    let lmin = Infinity, lmax = -Infinity;
    if (R.light) for (const v of R.light) if (!isNaN(v)) { lmin = Math.min(lmin, v); lmax = Math.max(lmax, v); }
    const kv = function (k, v) { return "<tr><td>" + k + '</td><td class="mono" style="text-align:left">' + v + "</td></tr>"; };
    body.innerHTML =
      '<div class="tbl-wrap" style="max-height:none"><table><tbody>' +
      kv("File", esc(R.fileName)) + kv("Format", R.format + (D.F.synthetic ? " (simulated demo)" : "")) +
      kv("Line 1 · Name", esc(R.header.name)) + kv("Line 2 · Start date", esc(R.header.date)) + kv("Line 3 · Start time", esc(R.header.time)) +
      kv("Line 4 · Interval code", esc(R.header.interval) + " → " + R.intervalMin + " min per sample") +
      kv("Line 5 · Age", esc(R.header.age)) + kv("Line 6 · ID", esc(R.header.id)) + kv("Line 7 · Sex", esc(R.header.sex)) +
      kv("Samples", R.counts.length.toLocaleString()) +
      kv("Recording", A.fmtDate(R.startUTC, R.t0Min) + " " + A.fmtClock(R.t0Min) + " → " + A.fmtDate(R.startUTC, endMin) + " " + A.fmtClock(endMin) + " (" + ((endMin - R.t0Min) / 1440).toFixed(2) + " d)") +
      kv("Missing samples", R.nMissing.toLocaleString() + " (" + (100 * R.nMissing / R.counts.length).toFixed(2) + "%)") +
      kv("Light column", R.light ? "yes · range " + lmin + "–" + lmax : "no") +
      kv("Event markers (M)", R.nMarker ? String(R.nMarker) : "none") +
      "</tbody></table></div>" +
      '<div class="row" style="margin-top:10px"><label class="check">Override sample interval (min) <input type="number" id="ivIn" style="width:80px" step="0.25" min="0.0625" value="' + R.intervalMin + '"></label></div>' +
      (R.warnings.length ? '<p class="warn">' + R.warnings.map(esc).join("<br>") + "</p>" : "");
    $("ivIn").onchange = function (e) { const v = +e.target.value; if (v > 0) { R.intervalMin = v; D.F.cache = {}; syncControls(); renderAll(); } };
  }

  // Text of the Methods tab: file format, every algorithm, references and the design basis.
  function methodsHTML() {
    return '<div class="note" style="max-width:85ch;color:var(--ink)">' +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">File format</h3>" +
      "<p>Text-based <code>.AWD</code> data files have a 7-line header: name, start date (<code>dd-mon-yyyy</code>), start time (<code>HH:MM</code>, 24 h), sample interval written as minutes × 4 (so <code>4</code> = 1 min, <code>24</code> = 6 min), age, ID and sex (the last three are ignored). Each following line holds one activity value, optionally followed by a comma and a light value. Values below 0 mark missing data. Times are treated as plain clock times without time-zone or daylight-saving correction.</p>" +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">Actogram</h3>" +
      "<p>Data are re-binned to the chosen bin size, which is a whole multiple of the file's sampling interval: 1× (the sampling interval itself) by default, up to a maximum of 120 min. Each file keeps its own multiple (counts summed; bins with under 50% valid samples are treated as missing). Each row spans one row period starting at the row-start offset; double and triple plots append the following cycles to the right. Bar height (or heat-map colour) is scaled to the 95th percentile of non-zero bins, multiplied by the gain; the colour heat maps use the perceptually uniform viridis, magma or cividis scales. Setting the row period to the free-running period (a τ-plot) lines up onsets vertically.</p>" +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">Lighting</h3>" +
      "<p>With <b>Recorded light</b>, a bin is light when its mean light value is at or above the threshold. The Lighting record tab classifies each calendar day as LD, LL or DD from the threshold crossings and merges consistent days into segments. With <b>Schedule</b>, lighting is computed from segments you define (LD with L and D durations allows T-cycles and skeleton-like schedules can be built from several segments).</p>" +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">Periodograms</h3>" +
      "<p><b>χ² periodogram</b> (Sokolove &amp; Bushell, 1978): for each trial period the data are folded and Qp = Σ n<sub>h</sub>(M<sub>h</sub> − M)² / [Σ(X − M)² / N]. The critical value is the χ² quantile with P − 1 degrees of freedom. The peak is the period with the greatest Qp above that line. <b>Lomb–Scargle</b> (Lomb, 1976; Scargle, 1982) handles gaps and gives a continuous period axis.</p>" +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">Onsets and offsets</h3>" +
      "<p>A step template is slid along the binned data: S(t) = mean(next W hours) − mean(previous W hours). Each cycle's onset is the maximum of S, tracked from the previous onset ± the search window; the onset is then moved to the first bin within ±1 h that is itself active and whose next 30 min reach 40% of the post-onset level. The offset is the first time, at least 2 h after onset, that is followed by 3 h below 15% of that level. Onsets whose score is below the minimum contrast (as a percentage of mean activity) are dropped. ψ is onset minus the nearest lights-off within ±6 h (negative = activity starts before lights-off). α is offset minus onset.</p>" +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">Regression fits, period and phase</h3>" +
      "<p>Each regression fit is a least-squares line of onset time against cycle number; its slope is the period τ. Fit 1 uses every onset in the analysis range until you choose onsets by hand. Several fits can be defined, for example one for LD and one for each free-running condition, with <b>One per condition</b> or by selecting onsets with the mouse.</p>" +
      "<p>Under LD, the phase of an onset is its zeitgeber time, ZT = time since the most recent lights-on (ZT0), or ZT / T × 360° in degrees. Under DD or LL, onset times t measured from a 0° reference are described by t = b + τ·n; the phase of an onset is ((t − reference) mod τ) / τ, shown as circadian time (CT = phase × 24) or in degrees, and the fitted phase of a regression is (b mod τ) / τ. The 0° reference is the projected ZT0 of the preceding LD condition (the last lights-on at or before the transfer), the start of the condition, or 00:00 of the first recording day. For an LD fit, the reported phase is the circular mean of the onsets' ZT.</p>" +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">Non-parametric variables</h3>" +
      "<p>IS, IV and RA follow Van Someren et al. (1999) on hourly totals, so they are shown only when the bin size divides 60 min evenly (for example 1, 2, 5, 6, 10, 15, 20, 30 or 60 min): IS compares the 24-h profile with overall variance, IV measures hour-to-hour fragmentation, and RA = (M10 − L5)/(M10 + L5).</p>" +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">Demo data</h3>" +
      "<p>The demo recordings are simulated with a seeded model of nocturnal wheel running (bouts within a bimodal active phase, masking by light, entrainment in LD, free-running in DD and LL). They are written in the same AWD layout and are not measurements from real animals.</p>" +
      "<h3 style=\"font-family:var(--font-display);font-size:1rem\">References</h3>" +
      "<p>Sokolove PG, Bushell WN (1978) J Theor Biol 72:131–160 · Lomb NR (1976) Astrophys Space Sci 39:447–462 · Scargle JD (1982) Astrophys J 263:835–853 · Van Someren EJW et al. (1999) Chronobiol Int 16:505–518 · Refinetti R, Cornélissen G, Halberg F (2007) Biol Rhythm Res 38:275–325.</p>" +
      "<p>The analysis design follows methods in common use in circadian biology, analyses of mouse activity rhythms published in the scientific literature, and the documented features of publicly available open-source software. Open-source tools were consulted only for their specifications and feature sets; no code was copied from them. No code, binaries or reverse-engineered material from commercial software were used.</p></div>";
  }

  /* ================================================================
   * Summary strip
   * ================================================================ */
  function renderSummary() {
    const F = curFile();
    const el = $("summary");
    if (!F) { el.innerHTML = '<span class="note">No recording loaded.</span>'; return; }
    const D = derive(F), R = F.rec;
    const endMin = R.t0Min + R.counts.length * R.intervalMin;
    const pg = A.chiSquarePeriodogram(D.x, D.binMin, S.ana.pmin, S.ana.pmax, S.ana.alpha);
    const item = function (k, v) { return '<div><span class="k">' + k + '</span><span class="v">' + v + "</span></div>"; };
    el.innerHTML =
      '<div><span class="title">' + esc(R.name) + "</span> " + (F.synthetic ? '<span class="chip synth">simulated demo</span>' : '<span class="chip">' + esc(R.format) + "</span>") + "</div>" +
      item("Start", A.fmtDate(R.startUTC, R.t0Min) + " " + A.fmtClock(R.t0Min)) +
      item("Length", ((endMin - R.t0Min) / 1440).toFixed(1) + " d") +
      item("Sample", R.intervalMin + " min") +
      item("Light data", R.light ? "recorded" : "none") +
      item("χ² peak in range", pg.peak ? pg.peak.periodH.toFixed(2) + " h" + (pg.peak.significant ? "" : " (ns)") : "–");
  }

  /* ================================================================
   * Controls
   * ================================================================ */
  function renderFileList() {
    $("fileList").innerHTML = S.files.map(function (F, i) {
      return '<div class="file' + (i === S.cur ? " sel" : "") + '" data-i="' + i + '" role="button" tabindex="0"><span class="nm" title="' + esc(F.rec.fileName) + '">' + esc(F.rec.name) + ' <span class="tag">' + F.rec.intervalMin + "m" + (F.synthetic ? " · demo" : "") + '</span></span><button class="x" data-del="' + i + '" type="button" aria-label="Remove ' + esc(F.rec.name) + '">×</button></div>';
    }).join("");
  }

  // Lighting panel: source, threshold and (for Schedule) the segment editor.
  function renderSchedule() {
    const F = curFile();
    const box = $("schedBox");
    if (!F) { box.innerHTML = ""; return; }
    $("lightSrc").value = F.lightSrc;
    $("luxThr").value = F.luxThr;
    $("luxThr").disabled = !F.rec.light;
    $("lightSrc").querySelector('option[value="recorded"]').disabled = !F.rec.light;
    const showSched = F.lightSrc === "schedule";
    box.hidden = !showSched; $("addSeg").hidden = !showSched;
    $("useDetected").hidden = !F.rec.light;
    if (!showSched) return;
    box.innerHTML = F.schedule.map(function (s, i) {
      const t = A.fmtClock(s.onMin);
      return '<div class="seg" data-i="' + i + '"><span class="tag">from day</span><input type="number" min="1" data-k="startDay" value="' + s.startDay + '" aria-label="Start day"><select data-k="mode" aria-label="Condition"><option' + (s.mode === "LD" ? " selected" : "") + ">LD</option><option" + (s.mode === "DD" ? " selected" : "") + ">DD</option><option" + (s.mode === "LL" ? " selected" : "") + ">LL</option></select>" +
        (s.mode === "LD" ? '<div class="sub"><div><label>Lights on</label><input type="time" data-k="on" value="' + t + '"></div><div><label>L (h)</label><input type="number" step="0.25" min="0" data-k="L" value="' + s.L + '"></div><div><label>D (h)</label><input type="number" step="0.25" min="0" data-k="D" value="' + s.D + '"></div><button type="button" data-rm="' + i + '" aria-label="Remove segment">×</button></div>' :
          '<div class="sub"><span class="tag" style="grid-column:1/4">' + (s.mode === "DD" ? "constant darkness" : "constant light") + '</span><button type="button" data-rm="' + i + '" aria-label="Remove segment">×</button></div>') + "</div>";
    }).join("");
  }

  // Range preset buttons: all rows plus one per lighting segment, converted to rows.
  function renderRangePresets() {
    const F = curFile();
    const box = $("rangePresets");
    if (!F) { box.innerHTML = ""; return; }
    const D = derive(F);
    const btns = ['<button type="button" data-r="' + (D.kStart + 1) + "," + D.nRows + '">All rows</button>'];
    // One preset per lighting segment (detected or scheduled), mapped to rows.
    let segs = [];
    if (F.lightSrc === "recorded" && F.rec.light) segs = A.detectLighting(F.rec, F.luxThr).segments.map(function (s) { return { mode: s.mode, a: s.startDay, b: s.endDay }; });
    else if (F.lightSrc === "schedule") {
      const sorted = F.schedule.slice().sort(function (p, q) { return p.startDay - q.startDay; });
      const lastDay = Math.ceil(D.B.n * D.binMin / 1440);
      segs = sorted.map(function (s, i) { return { mode: s.mode, a: s.startDay, b: i + 1 < sorted.length ? sorted[i + 1].startDay - 1 : lastDay }; });
    }
    for (const s of segs) {
      const r1 = Math.max(D.kStart, Math.ceil(((s.a - 1) * 1440 / D.binMin - D.off) / D.Pb)) + 1;
      const r2 = Math.min(D.nRows, Math.floor((s.b * 1440 / D.binMin - D.off) / D.Pb));
      if (r2 >= r1) btns.push('<button type="button" data-r="' + r1 + "," + r2 + '">' + s.mode + " " + r1 + "–" + r2 + "</button>");
    }
    box.innerHTML = btns.join("");
  }

  /**
   * Copy the state into the form controls (bin options, analysis range,
   * gain readout, edit button) and keep the analysis range inside the rows
   * that exist for the selected recording.
   */
  function syncControls() {
    const F = curFile();
    // Bin choices: 1x, 2x, ... of this file's sampling interval, up to 120 min.
    const sel = $("binSel");
    if (F) {
      effectiveBin(F);
      const n = maxBinMult(F), dt = F.rec.intervalMin;
      const sig = dt + "|" + n;
      if (sel.dataset.sig !== sig) {
        let html = "";
        for (let m = 1; m <= n; m++) html += '<option value="' + m + '">' + m + "× · " + fmtMin(m * dt) + "</option>";
        sel.innerHTML = html; sel.dataset.sig = sig;
      }
      sel.value = String(F.binMult);
      sel.disabled = false;
      $("binNote").textContent = "Sampling interval " + fmtMin(dt) + "; bins up to " + fmtMin(n * dt) + ".";
      $("binNote").hidden = false;
    } else { sel.innerHTML = ""; sel.dataset.sig = ""; sel.disabled = true; $("binNote").hidden = true; }
    if (F) {
      const D = derive(F);
      F.d1 = Math.min(Math.max(F.d1, D.kStart + 1), D.nRows);
      F.d2 = Math.min(Math.max(F.d2, F.d1), D.nRows);
      $("d1In").value = F.d1; $("d2In").value = F.d2;
      $("d1In").max = D.nRows; $("d2In").max = D.nRows;
    }
    $("gainOut").textContent = Math.pow(2, S.disp.gainLog).toFixed(2).replace(/0$/, "") + "×";
    $("editBtn").classList.toggle("on", S.editing);
    $("editBtn").setAttribute("aria-pressed", S.editing ? "true" : "false");
    $("editBtn").textContent = S.editing ? "Editing onsets" : "Edit onsets";
    $("acto").style.cursor = S.editing ? "crosshair" : "default";
  }

  // Name of the fit that receives onsets picked with the Select tool.
  function activeFitName(F) { ensureFits(F); return F.fits[F.activeFit].name; }

  // Regression fit list in the left panel.
  function renderFits() {
    const F = curFile();
    const box = $("fitList");
    if (!F) { box.innerHTML = ""; return; }
    const D = derive(F), O = onsets(D);
    box.innerHTML = O.fits.map(function (f) {
      const t = f.fit ? "τ " + f.fit.slope.toFixed(2) + " h" : "τ –";
      return '<div class="fitrow' + (f.i === F.activeFit ? " sel" : "") + '" data-fit="' + f.i + '" role="button" tabindex="0" aria-pressed="' + (f.i === F.activeFit) + '">' +
        '<span class="sw" style="background:var(--fit' + (f.i % 6 + 1) + ');border-radius:50%"></span>' +
        '<span class="fn">' + esc(f.name) + (f.auto ? ' <span class="tag">auto</span>' : "") + '</span>' +
        '<span class="tag">' + (f.mode && f.mode !== "none" && f.mode !== "?" ? f.mode + " · " : "") + "n " + f.members.length + " · " + t + "</span>" +
        (O.fits.length > 1 ? '<button type="button" class="x" data-delfit="' + f.i + '" aria-label="Remove ' + esc(f.name) + '">×</button>' : "<span></span>") + "</div>";
    }).join("");
    $("addFit").disabled = O.fits.length >= 6;
  }

  // Full refresh after any change of state.
  function renderAll() {
    syncControls();
    renderFileList();
    renderFits();
    renderSchedule();
    renderRangePresets();
    renderSummary();
    renderActogram();
    renderTab();
  }

  /* ================================================================
   * Loading files
   * ================================================================ */
  function addRecord(rec, synthetic) {
    const F = makeEntry(rec, synthetic);
    S.files.push(F);
    S.cur = S.files.length - 1;
    renderAll();
  }
  // Read dropped or chosen files as text and parse each one; errors are collected into one toast.
  function loadFiles(list) {
    const arr = Array.from(list);
    let errors = [];
    let pending = arr.length;
    arr.forEach(function (file) {
      const rd = new FileReader();
      rd.onload = function () {
        try { addRecord(A.parseAny(rd.result, file.name), false); }
        catch (e) { errors.push(file.name + ": " + e.message); }
        if (--pending === 0 && errors.length) toast(errors.join(" · "), 7000);
      };
      rd.onerror = function () { errors.push(file.name + ": could not be read"); if (--pending === 0) toast(errors.join(" · "), 7000); };
      rd.readAsText(file);
    });
  }
  // Generate one of the simulated demo recordings and load it like a file.
  function loadDemo(key) {
    const meta = window.DemoAWD.list.find(function (d) { return d.key === key; });
    const text = window.DemoAWD.generate(key);
    addRecord(A.parseAWD(text, meta.fileName + ".AWD"), true);
  }

  /* ================================================================
   * Saving files (a normal browser download)
   * ================================================================ */
  function saveBlob(name, blob) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
  }
  // Save a CSV text as a file; toCSV() quotes fields that contain commas, quotes or newlines.
  function saveText(name, text) { saveBlob(name, new Blob([text], { type: "text/csv" })); }
  function toCSV(rows) { return rows.map(function (r) { return r.map(function (v) { v = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }).join(","); }).join("\n") + "\n"; }
  // Recording name made safe for file names.
  function baseName(F) { return F.rec.name.replace(/[^\w.-]+/g, "_"); }

  // PNG of the whole actogram (zoom ignored) at 2x resolution with the light palette and a title.
  function exportPNG() {
    const F = curFile(); if (!F) return;
    const D = derive(F);
    const width = Math.max(900, $("actoWrap").clientWidth);
    const G = actoLayout(D, width, true); // export always shows the whole record
    const cv = document.createElement("canvas");
    const ctx = setupCanvas(cv, G.W, G.H, 2);
    drawActogram(ctx, D, G, palette(true), { title: F.rec.name + " · " + D.binMin + "-min bins" });
    cv.toBlob(function (blob) { if (blob) saveBlob(baseName(F) + "_actogram.png", blob); }, "image/png");
  }

  let toastTimer = null;
  // Show a short message for `ms` milliseconds (default 2.5 s).
  function toast(msg, ms) {
    const t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, ms || 2500);
  }

  /* ================================================================
   * Event wiring
   * ================================================================ */
  function wire() {
    const drop = $("drop"), input = $("fileInput");
    drop.onclick = function () { input.click(); };
    drop.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
    input.onchange = function () { if (input.files.length) loadFiles(input.files); input.value = ""; };
    ["dragenter", "dragover"].forEach(function (ev) { document.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("hot"); }); });
    ["dragleave", "drop"].forEach(function (ev) { document.addEventListener(ev, function (e) { e.preventDefault(); if (ev === "drop" || e.target === document.documentElement) drop.classList.remove("hot"); }); });
    document.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files.length) loadFiles(e.dataTransfer.files); });

    $("demoButtons").innerHTML = window.DemoAWD.list.map(function (d) { return '<button type="button" data-demo="' + d.key + '" title="' + esc(d.label) + '">Demo: ' + esc(d.short) + "</button>"; }).join("");
    $("demoButtons").onclick = function (e) { const b = e.target.closest("[data-demo]"); if (b) loadDemo(b.dataset.demo); };

    $("fileList").onclick = function (e) {
      const del = e.target.closest("[data-del]");
      if (del) { const i = +del.dataset.del; S.files.splice(i, 1); if (S.cur >= S.files.length) S.cur = S.files.length - 1; renderAll(); return; }
      const row = e.target.closest("[data-i]");
      if (row) { S.cur = +row.dataset.i; renderAll(); }
    };
    $("fileList").onkeydown = function (e) { const row = e.target.closest("[data-i]"); if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); S.cur = +row.dataset.i; renderAll(); } };

    const num = function (id, fn) { $(id).addEventListener("change", function (e) { const v = parseFloat(e.target.value); if (isFinite(v)) { fn(v); renderAll(); } }); };
    $("binSel").onchange = function (e) { const F = curFile(); if (F) { F.binMult = +e.target.value; renderAll(); } };
    $("plotSel").onchange = function (e) { S.disp.mult = +e.target.value; renderAll(); };
    num("periodIn", function (v) { S.disp.T = Math.min(48, Math.max(4, v)); });
    num("offsetIn", function (v) { S.disp.offsetH = Math.min(24, Math.max(-12, v)); });
    num("rowHIn", function (v) { S.disp.rowH = Math.min(40, Math.max(5, Math.round(v))); });
    $("styleSel").onchange = function (e) { S.disp.style = e.target.value; renderActogram(); };
    $("gainIn").oninput = function (e) { S.disp.gainLog = +e.target.value; syncControls(); renderActogram(); };
    num("floorIn", function (v) { S.disp.floor = Math.max(0, v); });
    $("shadeChk").onchange = function (e) { S.disp.shade = e.target.checked; renderActogram(); };
    $("onsetChk").onchange = function (e) { S.disp.showOn = e.target.checked; renderActogram(); };
    $("offsetChk").onchange = function (e) { S.disp.showOff = e.target.checked; renderActogram(); };

    $("lightSrc").onchange = function (e) { const F = curFile(); if (F) { F.lightSrc = e.target.value; renderAll(); } };
    num("luxThr", function (v) { const F = curFile(); if (F) F.luxThr = v; });
    $("addSeg").onclick = function () {
      const F = curFile(); if (!F) return;
      const last = F.schedule[F.schedule.length - 1];
      F.schedule.push({ startDay: (last ? last.startDay : 0) + 7, mode: "DD", onMin: last ? last.onMin : 420, L: 12, D: 12 });
      renderAll();
    };
    $("useDetected").onclick = function () {
      const F = curFile(); if (!F || !F.rec.light) return;
      const segs = A.detectLighting(F.rec, F.luxThr).segments;
      if (segs.length) { F.schedule = A.segmentsToSchedule(segs); F.lightSrc = "schedule"; renderAll(); toast("Schedule copied from the light column"); }
    };
    $("schedBox").addEventListener("change", function (e) {
      const F = curFile(); if (!F) return;
      const seg = e.target.closest(".seg"); if (!seg) return;
      const s = F.schedule[+seg.dataset.i], k = e.target.dataset.k;
      if (k === "startDay") s.startDay = Math.max(1, Math.round(+e.target.value) || 1);
      else if (k === "mode") s.mode = e.target.value;
      else if (k === "on") { const t = A.parseTime(e.target.value); if (t !== null) s.onMin = t; }
      else if (k === "L" || k === "D") s[k] = Math.max(0, +e.target.value || 0);
      renderAll();
    });
    $("schedBox").addEventListener("click", function (e) {
      const rm = e.target.closest("[data-rm]"); const F = curFile();
      if (rm && F && F.schedule.length > 1) { F.schedule.splice(+rm.dataset.rm, 1); renderAll(); }
    });

    num("d1In", function (v) { const F = curFile(); if (F) { F.d1 = Math.round(v); if (F.d2 < F.d1) F.d2 = F.d1; } });
    num("d2In", function (v) { const F = curFile(); if (F) { F.d2 = Math.round(v); if (F.d1 > F.d2) F.d1 = F.d2; } });
    $("rangePresets").onclick = function (e) { const b = e.target.closest("[data-r]"); const F = curFile(); if (b && F) { const p = b.dataset.r.split(","); F.d1 = +p[0]; F.d2 = +p[1]; renderAll(); } };

    num("tplIn", function (v) { S.ana.templateH = Math.max(0.5, v); });
    num("searchIn", function (v) { S.ana.searchH = Math.max(0.5, v); });
    num("minActIn", function (v) { S.ana.minContrast = Math.max(0, v); });
    $("editBtn").onclick = function () { S.editing = !S.editing; if (S.editing) S.disp.showOn = $("onsetChk").checked = true; renderAll(); };
    $("clearEdits").onclick = function () { const F = curFile(); if (F) { F.manual = {}; renderAll(); } };
    $("fitList").addEventListener("click", function (e) {
      const F = curFile(); if (!F) return;
      const del = e.target.closest("[data-delfit]");
      if (del) { F.fits.splice(+del.dataset.delfit, 1); if (F.activeFit >= F.fits.length) F.activeFit = F.fits.length - 1; renderAll(); return; }
      const row = e.target.closest("[data-fit]");
      if (row) { F.activeFit = +row.dataset.fit; renderAll(); }
    });
    $("fitList").addEventListener("keydown", function (e) { const row = e.target.closest("[data-fit]"); const F = curFile(); if (row && F && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); F.activeFit = +row.dataset.fit; renderAll(); } });
    $("addFit").onclick = function () {
      const F = curFile(); if (!F) return; ensureFits(F);
      if (F.fits.length >= 6) return;
      F.fits.push({ name: "Fit " + (F.fits.length + 1), auto: false, members: {} });
      F.activeFit = F.fits.length - 1;
      if (!S.editing) { S.editing = true; S.disp.showOn = $("onsetChk").checked = true; }
      S.tool = "select";
      renderAll();
      toast("Fit " + F.fits.length + " added. Click onsets or drag a box on the actogram to choose its onsets.", 4500);
    };
    $("splitFits").onclick = function () {
      // One fit per lighting condition inside the analysis range.
      const F = curFile(); if (!F) return;
      const D = derive(F), O = onsets(D), segs = condSegments(D);
      if (!segs.length) { toast("Set a lighting source first (recorded light or a schedule)."); return; }
      const groups = [];
      for (const o of O.list) {
        if (o.onsetBin === null || o.k < D.k1 || o.k > D.k2) continue;
        const si = segIndexAt(segs, o.onsetBin * D.binMin);
        let g = groups.find(function (x) { return x.si === si; });
        if (!g) { g = { si: si, members: {} }; groups.push(g); }
        g.members[o.k] = true;
      }
      if (!groups.length) return;
      F.fits = groups.slice(0, 6).map(function (g, i) { const sg = segs[g.si]; return { name: "Fit " + (i + 1) + (sg ? " (" + sg.mode + ")" : ""), auto: false, members: g.members }; });
      F.activeFit = 0;
      renderAll();
    };
    $("resetFits").onclick = function () { const F = curFile(); if (F) { F.fits = [{ name: "Fit 1", auto: true, members: {} }]; F.activeFit = 0; renderAll(); } };

    $("acto").addEventListener("mousemove", onActoMove);
    $("acto").addEventListener("mouseleave", function () { $("actoTip").hidden = true; });
    $("acto").addEventListener("click", onActoClick);
    $("acto").addEventListener("wheel", onActoWheel, { passive: false });
    $("acto").addEventListener("pointerdown", onActoPointerDown);
    $("acto").addEventListener("pointermove", onActoPointerMove);
    $("acto").addEventListener("pointerup", onActoPointerUp);
    $("acto").addEventListener("pointercancel", onActoPointerCancel);
    ["place", "select", "pan"].forEach(function (t) { $("tool-" + t).onclick = function () { S.tool = t; renderZoomBar(); }; });
    $("panLeft").onclick = function () { panView(-0.25, 0); };
    $("panRight").onclick = function () { panView(0.25, 0); };
    $("panUp").onclick = function () { panView(0, -1); };
    $("panDown").onclick = function () { panView(0, 1); };
    $("zoomBothIn").onclick = function () { pushZoom(); zoomTime(1.5); zoomRows(1.5); };
    $("zoomBothOut").onclick = function () { pushZoom(); zoomTime(1 / 1.5); zoomRows(1 / 1.5); };
    // Keyboard on the focused actogram: arrows move, + / - zoom.
    $("acto").addEventListener("keydown", function (e) {
      if (!S.editing) return;
      const map = { ArrowLeft: [-0.25, 0], ArrowRight: [0.25, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
      if (map[e.key]) { e.preventDefault(); panView(map[e.key][0], map[e.key][1]); }
      else if (e.key === "+" || e.key === "=") { e.preventDefault(); pushZoom(); zoomTime(1.5); zoomRows(1.5); }
      else if (e.key === "-" || e.key === "_") { e.preventDefault(); pushZoom(); zoomTime(1 / 1.5); zoomRows(1 / 1.5); }
    });
    $("zoomBack").onclick = zoomBack;
    $("zoomTimeIn").onclick = function () { pushZoom(); zoomTime(2); };
    $("zoomTimeOut").onclick = function () { pushZoom(); zoomTime(0.5); };
    $("zoomRowsIn").onclick = function () { pushZoom(); zoomRows(2); };
    $("zoomRowsOut").onclick = function () { pushZoom(); zoomRows(0.5); };
    $("zoomRange").onclick = function () { pushZoom(); zoomToRange(); };
    $("zoomReset").onclick = resetZoom;
    $("pngBtn").onclick = exportPNG;

    $("tabs").onclick = function (e) { const t = e.target.closest("[data-tab]"); if (t) { S.tab = t.dataset.tab; renderTab(); } };

    // Redraw on resize and on theme changes.
    let rz = null;
    window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(function () { renderActogram(); renderTab(); }, 120); });
    try { window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { renderActogram(); renderTab(); }); } catch (e) { /* older browsers */ }
    new MutationObserver(function () { renderActogram(); renderTab(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { renderActogram(); renderTab(); });
  }

  wire();
  loadDemo("ld-dd");
})();
