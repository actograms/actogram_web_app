/*! Actogram web app | Copyright (c) 2026 The Actogram Web Application Development Team | SPDX-License-Identifier: MIT */
/* ------------------------------------------------------------------
 * Synthetic demo recordings
 * ------------------------------------------------------------------
 * Generates simulated mouse wheel-running records in the text-based
 * text-based .AWD data-file layout. The model is deliberately simple and fully
 * documented so the demo data are reproducible and clearly synthetic:
 *   - Each circadian cycle has an activity onset; activity occupies an
 *     "alpha" window after onset with a bimodal intensity envelope
 *     (strong early bout, mid-night lull, late bout).
 *   - Inside alpha, running is organised in bouts (two-state Markov
 *     switching, minute resolution); counts per running minute are
 *     drawn around a mean wheel speed.
 *   - Under LD the onset is locked to lights-off plus a small phase
 *     angle; under DD/LL the onset drifts with the free-running period.
 *   - Light suppresses activity (negative masking) in nocturnal mice.
 * The generator is seeded, so the same demo always produces the same file.
 * ------------------------------------------------------------------ */
(function (root) {
  "use strict";

  // Small, fast, seeded PRNG (mulberry32).
  function rng(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Standard normal deviate (Box-Muller).
  function gauss(r) {
    let u = 0, v = 0;
    while (u === 0) u = r();
    while (v === 0) v = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

  /**
   * Scenario definitions. Times are in hours from midnight of the
   * recording's first calendar day ("absolute hours").
   *  light(tH)        -> lux at absolute hour tH
   *  onsetRule(...)   -> next activity onset given the previous one
   */
  const SCENARIOS = {
    "ld-dd": {
      label: "Wild-type: LD 12:12 then DD",
      short: "LD → DD",
      fileName: "DEMO_WT_LD-DD",
      id: "M01",
      sex: "M",
      seed: 11,
      date: [2026, 2, 3],        // 3 Mar 2026 (month is 0-based)
      startMin: 14 * 60 + 36,    // recording starts 14:36
      days: 35,
      intervalMin: 6,            // interval code 24 (minutes x 4)
      lux: 280,
      // LD 12:12, lights on 07:00, for days 1-14; constant darkness afterwards.
      light: function (tH) {
        const day = Math.floor(tH / 24);
        if (day >= 14) return 0;
        const h = tH - day * 24;
        return h >= 7 && h < 19 ? 1 : 0;
      },
      phaseFor: function (cycleStartH, prevOnset, r) {
        // Entrained: onset ~ lights-off (19:00) minus a small phase angle.
        const day = Math.floor((prevOnset + 24) / 24);
        if (prevOnset < 0 || day < 14) {
          const target = (prevOnset < 0 ? 0 : day) * 24 + 19 - 0.15;
          return { onset: target + 0.12 * gauss(r), alpha: 11.2, gain: 1 };
        }
        // Free-running in DD with tau = 23.62 h.
        return { onset: prevOnset + 23.62 + 0.14 * gauss(r), alpha: 11.6, gain: 1 };
      }
    },
    "ld-ll": {
      label: "LD 12:12 then constant light (1-min bins)",
      short: "LD → LL",
      fileName: "DEMO_LD-LL",
      id: "M07",
      sex: "F",
      seed: 29,
      date: [2026, 3, 13],
      startMin: 10 * 60 + 5,
      days: 30,
      intervalMin: 1,            // interval code 4 (minutes x 4)
      lux: 240,
      llLux: 120,
      light: function (tH) {
        const day = Math.floor(tH / 24);
        if (day >= 10) return 2; // 2 = LL level
        const h = tH - day * 24;
        return h >= 6 && h < 18 ? 1 : 0;
      },
      phaseFor: function (cycleStartH, prevOnset, r) {
        const day = Math.floor((prevOnset + 24) / 24);
        if (prevOnset < 0 || day < 10) {
          const target = (prevOnset < 0 ? 0 : day) * 24 + 18 - 0.1;
          return { onset: target + 0.12 * gauss(r), alpha: 11, gain: 1 };
        }
        // LL lengthens tau (Aschoff's rule) and compresses alpha.
        const k = day - 10;
        return {
          onset: prevOnset + 24.85 + 0.3 * gauss(r),
          alpha: Math.max(7, 10 - 0.12 * k),
          gain: Math.max(0.45, 0.85 - 0.02 * k)
        };
      }
    },
    "jetlag": {
      label: "6-h phase advance (jet lag) with a data gap",
      short: "6-h advance",
      fileName: "DEMO_JETLAG_ADV6",
      id: "M12",
      sex: "M",
      seed: 47,
      date: [2026, 8, 21],
      startMin: 16 * 60 + 48,
      days: 28,
      intervalMin: 6,
      lux: 310,
      gap: [16.4 * 24, 16.95 * 24], // equipment failure: missing data (written as -1)
      light: function (tH) {
        const day = Math.floor(tH / 24);
        const h = tH - day * 24;
        if (day < 10) return h >= 6 && h < 18 ? 1 : 0;
        // From day 11 the LD cycle is advanced by 6 h: lights on 00:00, off 12:00.
        return h < 12 ? 1 : 0;
      },
      phaseFor: function (cycleStartH, prevOnset, r) {
        if (prevOnset < 0) return { onset: 18 - 0.2 + 0.1 * gauss(r), alpha: 11, gain: 1 };
        const day = Math.floor((prevOnset + 24) / 24);
        const lightsOff = day < 10 ? 18 : 12;
        const target = day * 24 + lightsOff - 0.2;
        // Natural free-run step, then a limited daily correction toward the target.
        const free = prevOnset + 23.7;
        let diff = target - free;
        diff = Math.max(-0.6, Math.min(0.6, diff));
        return { onset: free + diff + 0.15 * gauss(r), alpha: 11, gain: 1 };
      }
    }
  };

  /** Generate minute-level activity and light, then bin and format as AWD text. */
  function generate(key) {
    const s = SCENARIOS[key];
    if (!s) throw new Error("Unknown demo: " + key);
    const r = rng(s.seed);
    const totalMin = s.days * 1440;
    const act = new Float64Array(totalMin);
    const lux = new Float64Array(totalMin);

    // 1. Build the list of activity windows (onset, alpha, gain).
    const windows = [];
    let prev = -1;
    while (true) {
      const w = s.phaseFor(0, prev, r);
      if (w.onset > (totalMin + s.startMin) / 60 + 30) break;
      windows.push(w);
      prev = w.onset;
      if (windows.length > 400) break;
    }

    // 2. Intensity envelope at absolute hour tH.
    function envelope(tH) {
      let e = 0.02; // sporadic rest-phase activity
      for (let i = 0; i < windows.length; i++) {
        const w = windows[i];
        const x = tH - w.onset;
        if (x < -1 || x > w.alpha + 1) continue;
        if (x >= 0 && x <= w.alpha) {
          const u = x / w.alpha;
          // Bimodal envelope: early peak, lull near 60% of alpha, late bout.
          const v = 0.95 * Math.exp(-Math.pow((u - 0.18) / 0.22, 2)) +
                    0.55 * Math.exp(-Math.pow((u - 0.85) / 0.12, 2)) + 0.42;
          e = Math.max(e, Math.min(1, v) * w.gain);
        }
      }
      return e;
    }

    // 3. Minute-by-minute bout simulation.
    let running = false;
    for (let m = 0; m < totalMin; m++) {
      const tH = (s.startMin + m) / 60;
      const L = s.light(tH);
      const luxNow = L === 2 ? s.llLux : L === 1 ? s.lux : 0;
      lux[m] = luxNow > 0 ? Math.max(1, Math.round(luxNow + 6 * gauss(r))) : 0;
      let p = envelope(tH);
      if (L === 1) p *= 0.12;      // negative masking by light
      const pStart = 0.02 + 0.22 * p; // chance a bout starts this minute
      const pStop = 0.03 + 0.25 * (1 - p); // chance a bout ends this minute
      if (running) { if (r() < pStop) running = false; }
      else if (r() < pStart * p * 1.6 + 0.0025) running = true; // includes sporadic rest-phase bouts
      if (running) {
        const speed = 22 + 30 * p + 8 * gauss(r);
        act[m] = Math.max(0, Math.round(speed));
      }
    }

    // 4. Missing-data gap (if any).
    if (s.gap) {
      for (let m = 0; m < totalMin; m++) {
        const tH = (s.startMin + m) / 60;
        if (tH >= s.gap[0] && tH < s.gap[1]) act[m] = -1;
      }
    }

    // 5. Bin to the requested interval and format as AWD text.
    const k = s.intervalMin;
    const nBins = Math.floor(totalMin / k);
    const lines = [];
    const d = s.date;
    lines.push(s.fileName);
    lines.push(String(d[2]).padStart(2, "0") + "-" + MONTHS[d[1]] + "-" + d[0]);
    lines.push(String(Math.floor(s.startMin / 60)).padStart(2, "0") + ":" + String(s.startMin % 60).padStart(2, "0"));
    lines.push(String(k * 4));   // INTERVAL = minutes x 4
    lines.push("0");             // AGE (not used)
    lines.push(s.id);            // ID
    lines.push(s.sex);           // SEX
    for (let b = 0; b < nBins; b++) {
      let sum = 0, missing = false, l = 0;
      for (let j = 0; j < k; j++) {
        const v = act[b * k + j];
        if (v < 0) missing = true; else sum += v;
        l += lux[b * k + j];
      }
      lines.push((missing ? -1 : sum) + "," + Math.round(l / k));
    }
    return lines.join("\r\n") + "\r\n";
  }

  const api = {
    list: Object.keys(SCENARIOS).map(function (k) { return { key: k, label: SCENARIOS[k].label, short: SCENARIOS[k].short, fileName: SCENARIOS[k].fileName }; }),
    generate: generate
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DemoAWD = api;
})(typeof window !== "undefined" ? window : globalThis);
