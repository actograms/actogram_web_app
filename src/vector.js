/*! Actogram web app | Copyright (c) 2026 The Actogram Web Application Development Team | SPDX-License-Identifier: MIT */
/* ------------------------------------------------------------------
 * Vector export: SVG, PDF and EPS.
 *
 * The plots are drawn with the Canvas 2D API. For vector export the same
 * drawing code is run against VectorContext, a recording object that
 * implements the subset of the Canvas 2D API used by this app (rectangles,
 * paths, arcs, text, clipping, transforms, dashes, alpha and images). The
 * recorded primitives are then written out as:
 *   - SVG 1.1 (text stays editable; images are embedded as PNG data URIs);
 *   - PDF 1.4 (single page; transparency kept; base-14 fonts Helvetica and
 *     Courier, so nothing is embedded; images are Flate-compressed);
 *   - EPS (Encapsulated PostScript, EPSF-3.0, level 2; colours blended with
 *     white because PostScript has no transparency).
 *   In PDF and EPS, text with characters outside the standard fonts (e.g.
 *   Chinese, Japanese or Korean) is placed as a high-resolution picture.
 * Consecutive rectangles with identical style are merged to keep files small.
 * Exposes window.VectorExport = { Context, toSVG, toPDF, toEPS }.
 * ------------------------------------------------------------------ */
(function (root) {
  "use strict";

  /* ---------- colours ---------- */
  const NAMED = { black: [0, 0, 0], white: [255, 255, 255], transparent: [0, 0, 0, 0] };
  /** Parse "#rgb", "#rrggbb", "rgb(...)", "rgba(...)" into [r, g, b, a]. */
  function parseColor(c) {
    c = String(c || "").trim().toLowerCase();
    if (NAMED[c]) { const n = NAMED[c]; return [n[0], n[1], n[2], n.length > 3 ? n[3] : 1]; }
    let m = c.match(/^#([0-9a-f]{3})$/);
    if (m) return [parseInt(m[1][0] + m[1][0], 16), parseInt(m[1][1] + m[1][1], 16), parseInt(m[1][2] + m[1][2], 16), 1];
    m = c.match(/^#([0-9a-f]{6})([0-9a-f]{2})?$/);
    if (m) return [parseInt(m[1].substr(0, 2), 16), parseInt(m[1].substr(2, 2), 16), parseInt(m[1].substr(4, 2), 16), m[2] ? parseInt(m[2], 16) / 255 : 1];
    m = c.match(/^rgba?\(([^)]+)\)$/);
    if (m) { const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat); return [p[0] || 0, p[1] || 0, p[2] || 0, p.length > 3 ? p[3] : 1]; }
    return [0, 0, 0, 1];
  }
  const hex2 = function (v) { return Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0"); };

  /** Parse a CSS font shorthand such as '600 13px "Archivo", Arial, sans-serif'. */
  function parseFont(f) {
    const m = String(f || "").match(/^\s*(?:(italic|oblique)\s+)?(?:(normal|bold|[1-9]00)\s+)?([\d.]+)px\s+(.+)$/i);
    if (!m) return { size: 12, weight: 400, family: "sans-serif", mono: false };
    const w = m[2] ? (m[2] === "bold" ? 700 : m[2] === "normal" ? 400 : +m[2]) : 400;
    const family = m[4].trim();
    return { size: +m[3], weight: w, family: family, mono: /mono|consolas|menlo|courier/i.test(family) };
  }

  /* ---------- recording context ---------- */
  function VectorContext(width, height) {
    this.W = width; this.H = height;
    this.ops = [];
    this._st = { fillStyle: "#000", strokeStyle: "#000", lineWidth: 1, font: "10px sans-serif", textAlign: "start", textBaseline: "alphabetic",
      globalAlpha: 1, lineJoin: "miter", dash: [], m: [1, 0, 0, 1, 0, 0], clip: null };
    this._stack = [];
    this._path = [];
    this.imageSmoothingEnabled = true;
  }
  const VC = VectorContext.prototype;
  // State properties map onto the internal state object (so save/restore work).
  ["fillStyle", "strokeStyle", "lineWidth", "font", "textAlign", "textBaseline", "globalAlpha", "lineJoin"].forEach(function (k) {
    Object.defineProperty(VC, k, { get: function () { return this._st[k]; }, set: function (v) { this._st[k] = v; } });
  });
  VC.save = function () { const s = this._st; this._stack.push(Object.assign({}, s, { dash: s.dash.slice(), m: s.m.slice(), clip: s.clip ? s.clip.slice() : null })); };
  VC.restore = function () { if (this._stack.length) this._st = this._stack.pop(); };
  VC.setTransform = function (a, b, c, d, e, f) { this._st.m = [a, b, c, d, e, f]; };
  VC.translate = function (x, y) { const m = this._st.m; m[4] += m[0] * x + m[2] * y; m[5] += m[1] * x + m[3] * y; };
  VC.rotate = function (r) {
    const m = this._st.m, c = Math.cos(r), s = Math.sin(r);
    this._st.m = [m[0] * c + m[2] * s, m[1] * c + m[3] * s, m[2] * c - m[0] * s, m[3] * c - m[1] * s, m[4], m[5]];
  };
  VC.scale = function (x, y) { const m = this._st.m; m[0] *= x; m[1] *= x; m[2] *= y; m[3] *= y; };
  VC._tp = function (x, y) { const m = this._st.m; return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]]; };
  VC._scale = function () { const m = this._st.m; return Math.sqrt(Math.abs(m[0] * m[3] - m[1] * m[2])) || 1; };
  VC.setLineDash = function (d) { this._st.dash = (d || []).slice(); };
  VC.getLineDash = function () { return this._st.dash.slice(); };
  VC.beginPath = function () { this._path = []; };
  VC.moveTo = function (x, y) { this._path.push({ pts: [this._tp(x, y)], closed: false }); };
  VC.lineTo = function (x, y) { if (!this._path.length) this.moveTo(x, y); else this._path[this._path.length - 1].pts.push(this._tp(x, y)); };
  VC.closePath = function () { if (this._path.length) this._path[this._path.length - 1].closed = true; };
  VC.rect = function (x, y, w, h) { this._path.push({ pts: [this._tp(x, y), this._tp(x + w, y), this._tp(x + w, y + h), this._tp(x, y + h)], closed: true }); };
  VC.arc = function (cx, cy, r, a0, a1, ccw) {
    let span = a1 - a0;
    if (ccw) { while (span > 0) span -= 2 * Math.PI; } else { while (span < 0) span += 2 * Math.PI; }
    if (Math.abs(a1 - a0) >= 2 * Math.PI - 1e-9) span = ccw ? -2 * Math.PI : 2 * Math.PI;
    const n = Math.max(8, Math.ceil(Math.abs(span) / (Math.PI / 90)));
    const pts = [];
    for (let i = 0; i <= n; i++) { const a = a0 + span * i / n; pts.push(this._tp(cx + r * Math.cos(a), cy + r * Math.sin(a))); }
    if (this._path.length && !this._path[this._path.length - 1].closed) Array.prototype.push.apply(this._path[this._path.length - 1].pts, pts);
    else this._path.push({ pts: pts, closed: false });
  };
  VC._style = function (kind) {
    const s = this._st;
    return { color: kind === "fill" ? s.fillStyle : s.strokeStyle, alpha: s.globalAlpha, lw: s.lineWidth * this._scale(), dash: s.dash.map(function (v) { return v; }), join: s.lineJoin, clip: s.clip };
  };
  VC.fill = function () { if (this._path.length) this.ops.push({ t: "path", fill: true, sub: this._path.map(function (p) { return { pts: p.pts.slice(), closed: true }; }), st: this._style("fill") }); };
  VC.stroke = function () { if (this._path.length) this.ops.push({ t: "path", fill: false, sub: this._path.map(function (p) { return { pts: p.pts.slice(), closed: p.closed }; }), st: this._style("stroke") }); };
  VC.fillRect = function (x, y, w, h) {
    if (!(w > 0 || w < 0) || !(h > 0 || h < 0)) return;
    const m = this._st.m;
    if (m[1] === 0 && m[2] === 0) {                       // axis-aligned: keep as a rectangle
      const p = this._tp(x, y), q = this._tp(x + w, y + h);
      this.ops.push({ t: "rect", x: Math.min(p[0], q[0]), y: Math.min(p[1], q[1]), w: Math.abs(q[0] - p[0]), h: Math.abs(q[1] - p[1]), st: this._style("fill") });
    } else { const save = this._path; this._path = []; this.rect(x, y, w, h); this.fill(); this._path = save; }
  };
  VC.strokeRect = function (x, y, w, h) { const save = this._path; this._path = []; this.rect(x, y, w, h); this.stroke(); this._path = save; };
  VC.clip = function () {
    // Only rectangular clips are used by the app: clip to the bounding box of the current path.
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of this._path) for (const q of p.pts) { x0 = Math.min(x0, q[0]); y0 = Math.min(y0, q[1]); x1 = Math.max(x1, q[0]); y1 = Math.max(y1, q[1]); }
    if (!isFinite(x0)) return;
    const c = this._st.clip;
    this._st.clip = c ? [Math.max(c[0], x0), Math.max(c[1], y0), Math.min(c[2], x1), Math.min(c[3], y1)] : [x0, y0, x1, y1];
  };
  VC.fillText = function (text, x, y) {
    const s = this._st, p = this._tp(x, y), m = s.m;
    const sc = this._scale(), f = parseFont(s.font);
    this.ops.push({ t: "text", text: String(text), x: p[0], y: p[1], angle: Math.atan2(m[1], m[0]) * 180 / Math.PI,
      font: { size: f.size * sc, weight: f.weight, family: f.family, mono: f.mono }, align: s.textAlign, baseline: s.textBaseline,
      color: s.fillStyle, alpha: s.globalAlpha, clip: s.clip });
  };
  VC.measureText = function (t) { const f = parseFont(this._st.font); return { width: String(t).length * f.size * (f.mono ? 0.6 : 0.55) }; };
  VC.drawImage = function (img, dx, dy, dw, dh) {
    const p = this._tp(dx, dy), q = this._tp(dx + (dw === undefined ? img.width : dw), dy + (dh === undefined ? img.height : dh));
    this.ops.push({ t: "image", img: img, x: Math.min(p[0], q[0]), y: Math.min(p[1], q[1]), w: Math.abs(q[0] - p[0]), h: Math.abs(q[1] - p[1]), alpha: this._st.globalAlpha, clip: this._st.clip, smooth: this.imageSmoothingEnabled });
  };

  /* ---------- helpers shared by the writers ---------- */
  const r2 = function (v) { return Math.round(v * 100) / 100; };
  const sameClip = function (a, b) { return a === b || (a && b && a[0] === b[0] && a[1] === b[1] && a[2] === b[2] && a[3] === b[3]); };
  const sameFill = function (a, b) { return a.color === b.color && a.alpha === b.alpha && sameClip(a.clip, b.clip); };
  /** Group consecutive rectangles of identical fill so they can be written as one shape. */
  function groupOps(ops) {
    const out = [];
    for (const op of ops) {
      const last = out[out.length - 1];
      if (op.t === "rect" && last && last.t === "rects" && sameFill(last.st, op.st)) last.list.push(op);
      else if (op.t === "rect") out.push({ t: "rects", st: op.st, list: [op] });
      else out.push(op);
    }
    return out;
  }
  function imagePixels(img) {
    const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
    const x = c.getContext("2d"); x.drawImage(img, 0, 0);
    return { w: c.width, h: c.height, data: x.getImageData(0, 0, c.width, c.height).data, url: c.toDataURL("image/png") };
  }

  /* ---------- SVG writer ---------- */
  const escXml = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  function toSVG(ctx, title) {
    const W = ctx.W, H = ctx.H, defs = [], body = [], clips = [];
    const clipId = function (c) {
      if (!c) return "";
      let i = clips.findIndex(function (k) { return sameClip(k, c); });
      if (i < 0) { clips.push(c); i = clips.length - 1; defs.push('<clipPath id="c' + i + '"><rect x="' + r2(c[0]) + '" y="' + r2(c[1]) + '" width="' + r2(c[2] - c[0]) + '" height="' + r2(c[3] - c[1]) + '"/></clipPath>'); }
      return ' clip-path="url(#c' + i + ')"';
    };
    const paint = function (color, alpha, kind) {
      const c = parseColor(color), a = (c[3] === undefined ? 1 : c[3]) * (alpha === undefined ? 1 : alpha);
      return " " + kind + '="#' + hex2(c[0]) + hex2(c[1]) + hex2(c[2]) + '"' + (a < 0.999 ? " " + kind + '-opacity="' + r2(a) + '"' : "");
    };
    for (const op of groupOps(ctx.ops)) {
      if (op.t === "rects") {
        if (op.list.length === 1) { const q = op.list[0]; body.push('<rect x="' + r2(q.x) + '" y="' + r2(q.y) + '" width="' + r2(q.w) + '" height="' + r2(q.h) + '"' + paint(op.st.color, op.st.alpha, "fill") + clipId(op.st.clip) + "/>"); }
        else body.push('<path d="' + op.list.map(function (q) { return "M" + r2(q.x) + " " + r2(q.y) + "h" + r2(q.w) + "v" + r2(q.h) + "h" + r2(-q.w) + "z"; }).join("") + '"' + paint(op.st.color, op.st.alpha, "fill") + clipId(op.st.clip) + "/>");
      } else if (op.t === "path") {
        const d = op.sub.map(function (sp) { return "M" + sp.pts.map(function (q) { return r2(q[0]) + " " + r2(q[1]); }).join("L") + (sp.closed ? "Z" : ""); }).join("");
        if (op.fill) body.push('<path d="' + d + '"' + paint(op.st.color, op.st.alpha, "fill") + clipId(op.st.clip) + "/>");
        else body.push('<path d="' + d + '" fill="none"' + paint(op.st.color, op.st.alpha, "stroke") + ' stroke-width="' + r2(op.st.lw) + '"' + (op.st.dash.length ? ' stroke-dasharray="' + op.st.dash.join(" ") + '"' : "") + ' stroke-linejoin="' + (op.st.join || "miter") + '"' + clipId(op.st.clip) + "/>");
      } else if (op.t === "text") {
        const anchor = { left: "start", start: "start", center: "middle", right: "end", end: "end" }[op.align] || "start";
        const base = { top: "hanging", hanging: "hanging", middle: "central", bottom: "text-after-edge", ideographic: "text-after-edge" }[op.baseline];
        const rot = Math.abs(op.angle) > 0.01 ? ' transform="rotate(' + r2(op.angle) + " " + r2(op.x) + " " + r2(op.y) + ')"' : "";
        body.push('<text x="' + r2(op.x) + '" y="' + r2(op.y) + '" font-family="' + escXml(op.font.family.replace(/"/g, "'")) + '" font-size="' + r2(op.font.size) + '"' + (op.font.weight !== 400 ? ' font-weight="' + op.font.weight + '"' : "") + ' text-anchor="' + anchor + '"' + (base ? ' dominant-baseline="' + base + '"' : "") + paint(op.color, op.alpha, "fill") + rot + clipId(op.clip) + ">" + escXml(op.text) + "</text>");
      } else if (op.t === "image") {
        const px = imagePixels(op.img);
        body.push('<image x="' + r2(op.x) + '" y="' + r2(op.y) + '" width="' + r2(op.w) + '" height="' + r2(op.h) + '" preserveAspectRatio="none"' + (op.smooth ? "" : ' style="image-rendering:pixelated"') + (op.alpha < 0.999 ? ' opacity="' + r2(op.alpha) + '"' : "") + ' href="' + px.url + '" xlink:href="' + px.url + '"' + clipId(op.clip) + "/>");
      }
    }
    return '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '">\n' +
      (title ? "<title>" + escXml(title) + "</title>\n" : "") +
      (defs.length ? "<defs>" + defs.join("") + "</defs>\n" : "") + body.join("\n") + "\n</svg>\n";
  }

  /* ---------- shared by the PDF and EPS writers ---------- */
  // Both use the standard PostScript/PDF base fonts (Helvetica, Courier), which every
  // viewer and printer provides, so no font is embedded. Those fonts have a Latin-1 /
  // WinAnsi character set: characters outside it are replaced by readable ASCII.
  const PT = 0.75;                                                    // CSS px (96 dpi) -> points (72 dpi)
  const SUBST = { "τ": "tau", "ψ": "psi", "α": "alpha", "χ": "chi", "ρ": "rho", "σ": "sigma", "μ": "µ", "Δ": "delta", "θ": "theta", "ω": "omega",
    "−": "-", "≥": ">=", "≤": "<=", "→": "->", "←": "<-", "…": "...", "≈": "~", "‘": "'", "’": "'", "“": '"', "”": '"', "√": "sqrt", "≠": "!=", "∞": "inf" };
  /** Encode text as single-byte codes. pdf = true: WinAnsiEncoding; false: ISO Latin-1. */
  function encode1(text, pdf) {
    const out = [];
    for (const ch of String(text)) {
      if (ch === "–" || ch === "—") { if (pdf) out.push(ch === "–" ? 0x96 : 0x97); else out.push(45); continue; }
      if (ch === "•") { out.push(pdf ? 0x95 : 0xb7); continue; }
      const sub = SUBST[ch];
      if (sub !== undefined) { for (const c of sub) out.push(c.charCodeAt(0)); continue; }
      const c = ch.codePointAt(0);
      out.push(c >= 32 && c < 127 ? c : c >= 160 && c < 256 ? c : 63);   // "?" for anything else
    }
    return out;
  }
  /**
   * Text that the standard fonts cannot show (e.g. Chinese, Japanese or Korean
   * characters in a recording name) is placed as a high-resolution picture of
   * the text instead (4x, drawn with the browser's fonts), so it looks right in
   * PDF and EPS; it is then not editable as text. Greek letters and the other
   * characters in SUBST are still written out in ASCII.
   */
  function needsRaster(text) {
    for (const ch of String(text)) {
      const c = ch.codePointAt(0);
      if (c > 255 && SUBST[ch] === undefined && "–—•".indexOf(ch) < 0) return true;
    }
    return false;
  }
  const RASTER = 4;
  // Picture of one text op. lx, ly, lw, lh: its rectangle in text-local px (x along the baseline, y down, origin at the anchor).
  function rasterText(op) {
    const f = op.font, font = (f.weight !== 400 ? f.weight + " " : "") + f.size + "px " + f.family;
    const m = document.createElement("canvas").getContext("2d");
    m.font = font; m.textBaseline = op.baseline; m.textAlign = "left";
    const tm = m.measureText(op.text), w = tm.width;
    const asc = tm.actualBoundingBoxAscent || f.size, desc = tm.actualBoundingBoxDescent || f.size * 0.3, pad = 1;
    const lx = -alignK(op.align) * w - pad, ly = -asc - pad, lw = w + 2 * pad, lh = asc + desc + 2 * pad;
    const cv = document.createElement("canvas");
    cv.width = Math.max(1, Math.ceil(lw * RASTER)); cv.height = Math.max(1, Math.ceil(lh * RASTER));
    const x = cv.getContext("2d");
    x.scale(RASTER, RASTER); x.translate(-lx, -ly);
    x.font = font; x.textBaseline = op.baseline; x.textAlign = "left";
    x.fillStyle = op.color; x.globalAlpha = op.alpha === undefined ? 1 : op.alpha;
    x.fillText(op.text, -alignK(op.align) * w, 0);
    return { canvas: cv, lx: lx, ly: ly, lw: cv.width / RASTER, lh: cv.height / RASTER };
  }
  const hexStr = function (bytes) { return "<" + bytes.map(function (b) { return b.toString(16).padStart(2, "0"); }).join("") + ">"; };
  const n3 = function (v) { const r = Math.round(v * 1000) / 1000; return String(Object.is(r, -0) ? 0 : r); };
  const baseFont = function (f) { return (f.mono ? "Courier" : "Helvetica") + (f.weight >= 600 ? "-Bold" : ""); };
  // Canvas baseline -> offset (px, downwards) from the anchor to the alphabetic baseline.
  const baseShift = function (baseline, size) {
    if (baseline === "top" || baseline === "hanging") return size * 0.76;
    if (baseline === "middle") return size * 0.35;
    if (baseline === "bottom" || baseline === "ideographic") return -size * 0.22;
    return 0;
  };
  const alignK = function (a) { return a === "center" ? 0.5 : a === "right" || a === "end" ? 1 : 0; };
  // Width of a string in the substitute font (Arial has Helvetica's metrics; Courier is 0.6 em).
  let measureCtx = null;
  function textWidth(bytes, f) {
    if (f.mono) return bytes.length * 0.6 * f.size;
    if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d");
    measureCtx.font = (f.weight >= 600 ? "bold " : "") + "100px Helvetica, Arial, \"Liberation Sans\", sans-serif";
    let s = "";
    for (const b of bytes) s += b === 0x96 ? "–" : b === 0x97 ? "—" : b === 0x95 ? "•" : String.fromCharCode(b);
    return measureCtx.measureText(s).width / 100 * f.size;
  }
  const strokeParams = function (st) {
    return { join: st.join === "round" ? 1 : st.join === "bevel" ? 2 : 0, dash: st.dash.length ? "[" + st.dash.map(n3).join(" ") + "] 0" : "[] 0" };
  };
  const pathOps = function (sub, close, mv, ln, cp) {
    const out = [];
    for (const sp of sub) {
      if (!sp.pts.length) continue;
      out.push(n3(sp.pts[0][0]) + " " + n3(sp.pts[0][1]) + " " + mv);
      for (let i = 1; i < sp.pts.length; i++) out.push(n3(sp.pts[i][0]) + " " + n3(sp.pts[i][1]) + " " + ln);
      if (close || sp.closed) out.push(cp);
    }
    return out.join("\n");
  };

  /* ---------- PDF writer ---------- */
  // Deflate with the browser's CompressionStream when available (zlib format = FlateDecode).
  async function deflate(bytes) {
    if (typeof CompressionStream === "undefined") return null;
    try {
      const cs = new CompressionStream("deflate");
      const out = new Response(new Blob([bytes]).stream().pipeThrough(cs)).arrayBuffer();
      return new Uint8Array(await out);
    } catch (e) { return null; }
  }
  const latin1 = function (s) { const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i) & 255; return u; };
  // PDF text string for the document information (title): ASCII as a literal string,
  // anything else (e.g. a recording name in Chinese, Japanese, and Korean) as UTF-16BE with a byte-order mark.
  const pdfText = function (s) {
    s = String(s);
    if (/^[\x20-\x7e]*$/.test(s)) return "(" + s.replace(/[\\()]/g, "\\$&") + ")";
    let h = "<FEFF";
    for (let i = 0; i < s.length; i++) h += s.charCodeAt(i).toString(16).toUpperCase().padStart(4, "0");
    return h + ">";
  };

  /** Single-page PDF 1.4 (vector; transparency kept; base-14 fonts). Returns a Promise of a Blob. */
  async function toPDF(ctx, title) {
    const W = ctx.W, H = ctx.H;
    const c = [];                                                     // content stream lines
    const fonts = {}, gstates = {}, images = [];
    const fontRef = function (name) { if (!fonts[name]) fonts[name] = "F" + Object.keys(fonts).length; return fonts[name]; };
    const gsRef = function (a) { a = Math.round(a * 1000) / 1000; if (!gstates[a]) gstates[a] = "G" + Object.keys(gstates).length; return gstates[a]; };
    const rgb = function (color) { const p = parseColor(color); return [p[0] / 255, p[1] / 255, p[2] / 255].map(n3).join(" "); };
    const alphaOf = function (color, alpha) { const p = parseColor(color); return (p[3] === undefined ? 1 : p[3]) * (alpha === undefined ? 1 : alpha); };
    // Page space: 1 unit = 1 CSS px, origin at the top left, y downwards (like the canvas).
    c.push(n3(PT) + " 0 0 " + n3(-PT) + " 0 " + n3(H * PT) + " cm");
    c.push("1 1 1 rg 0 0 " + W + " " + H + " re f");                 // white background
    let curClip = null;
    const setClip = function (k) {
      if (sameClip(k, curClip)) return;
      if (curClip) c.push("Q");
      if (k) c.push("q " + n3(k[0]) + " " + n3(k[1]) + " " + n3(k[2] - k[0]) + " " + n3(k[3] - k[1]) + " re W n");
      curClip = k;
    };
    const withAlpha = function (a, body) { if (a < 0.999) c.push("q /" + gsRef(a) + " gs"); c.push(body); if (a < 0.999) c.push("Q"); };
    for (const op of groupOps(ctx.ops)) {
      setClip(op.t === "rects" || op.t === "path" ? op.st.clip : op.clip);
      if (op.t === "rects") {
        const a = alphaOf(op.st.color, op.st.alpha); if (a <= 0) continue;
        withAlpha(a, rgb(op.st.color) + " rg\n" + op.list.map(function (q) { return n3(q.x) + " " + n3(q.y) + " " + n3(q.w) + " " + n3(q.h) + " re"; }).join("\n") + "\nf");
      } else if (op.t === "path") {
        const a = alphaOf(op.st.color, op.st.alpha); if (a <= 0) continue;
        if (op.fill) withAlpha(a, rgb(op.st.color) + " rg\n" + pathOps(op.sub, true, "m", "l", "h") + "\nf");
        else {
          const sp = strokeParams(op.st);
          withAlpha(a, rgb(op.st.color) + " RG " + n3(op.st.lw) + " w " + sp.join + " j 0 J " + sp.dash + " d\n" + pathOps(op.sub, false, "m", "l", "h") + "\nS");
        }
      } else if (op.t === "text") {
        const a = alphaOf(op.color, op.alpha); if (a <= 0 || !op.text) continue;
        if (needsRaster(op.text)) {                                     // CJK and other scripts: picture of the text
          const r = rasterText(op), px = imagePixels(r.canvas), raw = new Uint8Array(px.w * px.h * 3), al = new Uint8Array(px.w * px.h);
          for (let k = 0; k < px.w * px.h; k++) { raw[k * 3] = px.data[k * 4]; raw[k * 3 + 1] = px.data[k * 4 + 1]; raw[k * 3 + 2] = px.data[k * 4 + 2]; al[k] = px.data[k * 4 + 3]; }
          const name = "Im" + images.length, th = op.angle * Math.PI / 180, cs = Math.cos(th), sn = Math.sin(th);
          images.push({ name: name, w: px.w, h: px.h, raw: raw, alpha: al, smooth: true });
          // Unit square -> rotated text rectangle (image row 0 at the top).
          const cx = op.x + cs * r.lx - sn * (r.ly + r.lh), cy = op.y + sn * r.lx + cs * (r.ly + r.lh);
          c.push("q " + [r.lw * cs, r.lw * sn, r.lh * sn, -r.lh * cs, cx, cy].map(n3).join(" ") + " cm /" + name + " Do Q");
          continue;
        }
        const f = op.font, bytes = encode1(op.text, true), wpx = textWidth(bytes, f);
        const th = op.angle * Math.PI / 180, cs = Math.cos(th), sn = Math.sin(th);
        const dx = -alignK(op.align) * wpx, dy = baseShift(op.baseline, f.size);
        const x = op.x + cs * dx - sn * dy, y = op.y + sn * dx + cs * dy;
        withAlpha(a, "BT /" + fontRef(baseFont(f)) + " " + n3(f.size) + " Tf " + rgb(op.color) + " rg " + [cs, sn, sn, -cs, x, y].map(n3).join(" ") + " Tm " + hexStr(bytes) + " Tj ET");
      } else if (op.t === "image") {
        const px = imagePixels(op.img), raw = new Uint8Array(px.w * px.h * 3), al = new Uint8Array(px.w * px.h);
        let hasAlpha = false;
        for (let i = 0; i < px.w * px.h; i++) { raw[i * 3] = px.data[i * 4]; raw[i * 3 + 1] = px.data[i * 4 + 1]; raw[i * 3 + 2] = px.data[i * 4 + 2]; al[i] = px.data[i * 4 + 3]; if (al[i] < 255) hasAlpha = true; }
        const name = "Im" + images.length;
        images.push({ name: name, w: px.w, h: px.h, raw: raw, alpha: hasAlpha ? al : null, smooth: op.smooth });
        withAlpha(op.alpha, "q " + [op.w, 0, 0, -op.h, op.x, op.y + op.h].map(n3).join(" ") + " cm /" + name + " Do Q");
      }
    }
    setClip(null);

    // Assemble the file: objects are numbered in the order they are added.
    const objs = [];                                                  // each: array of Uint8Array/strings
    const add = function () { objs.push(null); return objs.length; };
    const set = function (n, parts) { objs[n - 1] = parts; };
    const stream = async function (dict, bytes) {
      const z = await deflate(bytes);
      const body = z && z.length < bytes.length ? z : bytes;
      return ["<< " + dict + (body === z ? " /Filter /FlateDecode" : "") + " /Length " + body.length + " >>\nstream\n", body, "\nendstream"];
    };
    const catalog = add(), pages = add(), page = add(), content = add(), info = add();
    const fontObjs = {};
    for (const name in fonts) { const n = add(); fontObjs[fonts[name]] = n; set(n, ["<< /Type /Font /Subtype /Type1 /BaseFont /" + name + (name.indexOf("Courier") === 0 || name.indexOf("Helvetica") === 0 ? " /Encoding /WinAnsiEncoding" : "") + " >>"]); }
    const imgObjs = {};
    for (const im of images) {
      let smask = "";
      if (im.alpha) { const n = add(); set(n, await stream("/Type /XObject /Subtype /Image /Width " + im.w + " /Height " + im.h + " /ColorSpace /DeviceGray /BitsPerComponent 8", im.alpha)); smask = " /SMask " + n + " 0 R"; }
      const n = add(); imgObjs[im.name] = n;
      set(n, await stream("/Type /XObject /Subtype /Image /Width " + im.w + " /Height " + im.h + " /ColorSpace /DeviceRGB /BitsPerComponent 8" + (im.smooth ? " /Interpolate true" : "") + smask, im.raw));
    }
    const res = "<< /ProcSet [/PDF /Text /ImageC /ImageB]" +
      " /Font << " + Object.keys(fontObjs).map(function (k) { return "/" + k + " " + fontObjs[k] + " 0 R"; }).join(" ") + " >>" +
      " /ExtGState << " + Object.keys(gstates).map(function (a) { return "/" + gstates[a] + " << /ca " + a + " /CA " + a + " >>"; }).join(" ") + " >>" +
      " /XObject << " + Object.keys(imgObjs).map(function (k) { return "/" + k + " " + imgObjs[k] + " 0 R"; }).join(" ") + " >> >>";
    set(catalog, ["<< /Type /Catalog /Pages " + pages + " 0 R >>"]);
    set(pages, ["<< /Type /Pages /Kids [" + page + " 0 R] /Count 1 >>"]);
    set(page, ["<< /Type /Page /Parent " + pages + " 0 R /MediaBox [0 0 " + n3(W * PT) + " " + n3(H * PT) + "] /Resources " + res + " /Contents " + content + " 0 R >>"]);
    set(content, await stream("", latin1(c.join("\n"))));
    const now = new Date(), p2 = function (v) { return String(v).padStart(2, "0"); };
    set(info, ["<< /Producer (Actogram web app)" + (title ? " /Title " + pdfText(title) : "") + " /CreationDate (D:" + now.getUTCFullYear() + p2(now.getUTCMonth() + 1) + p2(now.getUTCDate()) + p2(now.getUTCHours()) + p2(now.getUTCMinutes()) + p2(now.getUTCSeconds()) + "Z) >>"]);
    const parts = ["%PDF-1.4\n%âãÏÓ\n"], offsets = [];
    let pos = 15;
    const push = function (p) { const b = typeof p === "string" ? latin1(p) : p; parts.push(b); pos += b.length; };
    parts[0] = latin1(parts[0]);
    objs.forEach(function (o, i) { offsets.push(pos); push((i + 1) + " 0 obj\n"); o.forEach(push); push("\nendobj\n"); });
    const xref = pos;
    push("xref\n0 " + (objs.length + 1) + "\n0000000000 65535 f \n" + offsets.map(function (o) { return String(o).padStart(10, "0") + " 00000 n \n"; }).join(""));
    push("trailer\n<< /Size " + (objs.length + 1) + " /Root " + catalog + " 0 R /Info " + info + " 0 R >>\nstartxref\n" + xref + "\n%%EOF\n");
    return new Blob(parts, { type: "application/pdf" });
  }

  /* ---------- EPS writer ---------- */
  /** Encapsulated PostScript (EPSF-3.0, PostScript level 2). No transparency: colours are blended with white. */
  function toEPS(ctx, title) {
    const W = ctx.W, H = ctx.H, Wpt = W * PT, Hpt = H * PT;
    const o = [];
    const col = function (color, alpha) { const p = parseColor(color), a = (p[3] === undefined ? 1 : p[3]) * (alpha === undefined ? 1 : alpha); return [p[0], p[1], p[2]].map(function (v) { return n3((v * a + 255 * (1 - a)) / 255); }).join(" ") + " setrgbcolor"; };
    const used = {};
    let curClip = null;
    const setClip = function (k) {
      if (sameClip(k, curClip)) return;
      if (curClip) o.push("grestore");
      if (k) o.push("gsave newpath " + n3(k[0]) + " " + n3(k[1]) + " " + n3(k[2] - k[0]) + " " + n3(k[3] - k[1]) + " rectclip");
      curClip = k;
    };
    for (const op of groupOps(ctx.ops)) {
      setClip(op.t === "rects" || op.t === "path" ? op.st.clip : op.clip);
      if (op.t === "rects") {
        o.push(col(op.st.color, op.st.alpha));
        for (let i = 0; i < op.list.length; i += 2000)                 // stay well below the 65535-element array limit
          o.push("[" + op.list.slice(i, i + 2000).map(function (q) { return n3(q.x) + " " + n3(q.y) + " " + n3(q.w) + " " + n3(q.h); }).join("\n") + "] rectfill");
      } else if (op.t === "path") {
        if (op.fill) o.push(col(op.st.color, op.st.alpha) + " newpath\n" + pathOps(op.sub, true, "M", "L", "C") + "\nfill");
        else { const sp = strokeParams(op.st); o.push(col(op.st.color, op.st.alpha) + " " + n3(op.st.lw) + " setlinewidth " + sp.join + " setlinejoin 0 setlinecap " + sp.dash + " setdash newpath\n" + pathOps(op.sub, false, "M", "L", "C") + "\nstroke"); }
      } else if (op.t === "text") {
        if (!op.text) continue;
        if (needsRaster(op.text)) {                                     // CJK and other scripts: picture of the text, blended with white
          const r = rasterText(op), px = imagePixels(r.canvas), lines = [];
          let line = "";
          for (let k = 0; k < px.w * px.h; k++) {
            const a = px.data[k * 4 + 3] / 255;
            for (let q = 0; q < 3; q++) line += Math.round(px.data[k * 4 + q] * a + 255 * (1 - a)).toString(16).padStart(2, "0");
            if (line.length >= 78) { lines.push(line); line = ""; }
          }
          if (line) lines.push(line);
          o.push("gsave " + n3(op.x) + " " + n3(op.y) + " translate " + n3(op.angle) + " rotate " + n3(r.lx) + " " + n3(r.ly) + " translate " + n3(r.lw) + " " + n3(r.lh) + " scale /DeviceRGB setcolorspace\n" +
            "<< /ImageType 1 /Width " + px.w + " /Height " + px.h + " /BitsPerComponent 8 /Decode [0 1 0 1 0 1] /ImageMatrix [" + px.w + " 0 0 " + px.h + " 0 0] /Interpolate true /DataSource currentfile /ASCIIHexDecode filter >> image\n" +
            lines.join("\n") + ">\ngrestore");
          continue;
        }
        const f = op.font, fn = baseFont(f) + "-L1"; used[baseFont(f)] = true;
        o.push("gsave " + col(op.color, op.alpha) + " " + n3(op.x) + " " + n3(op.y) + " translate " + n3(op.angle) + " rotate 1 -1 scale /" + fn + " " + n3(f.size) + " selectfont " +
          hexStr(encode1(op.text, false)) + " dup stringwidth pop " + n3(-alignK(op.align)) + " mul " + n3(-baseShift(op.baseline, f.size)) + " moveto show grestore");
      } else if (op.t === "image") {
        const px = imagePixels(op.img), lines = [];
        let line = "";
        for (let i = 0; i < px.w * px.h; i++) {
          const a = px.data[i * 4 + 3] / 255 * op.alpha;
          for (let k = 0; k < 3; k++) line += Math.round(px.data[i * 4 + k] * a + 255 * (1 - a)).toString(16).padStart(2, "0");
          if (line.length >= 78) { lines.push(line); line = ""; }
        }
        if (line) lines.push(line);
        o.push("gsave " + n3(op.x) + " " + n3(op.y) + " translate " + n3(op.w) + " " + n3(op.h) + " scale /DeviceRGB setcolorspace\n" +
          "<< /ImageType 1 /Width " + px.w + " /Height " + px.h + " /BitsPerComponent 8 /Decode [0 1 0 1 0 1] /ImageMatrix [" + px.w + " 0 0 " + px.h + " 0 0] /Interpolate " + (op.smooth ? "true" : "false") + " /DataSource currentfile /ASCIIHexDecode filter >> image\n" +
          lines.join("\n") + ">\ngrestore");
      }
    }
    setClip(null);
    const reencode = Object.keys(used).map(function (f) {
      return "/" + f + " findfont dup length dict begin {1 index /FID ne {def} {pop pop} ifelse} forall /Encoding ISOLatin1Encoding def currentdict end /" + f + "-L1 exch definefont pop";
    });
    const head = [
      "%!PS-Adobe-3.0 EPSF-3.0",
      "%%BoundingBox: 0 0 " + Math.ceil(Wpt) + " " + Math.ceil(Hpt),
      "%%HiResBoundingBox: 0 0 " + n3(Wpt) + " " + n3(Hpt),
      "%%Creator: Actogram web app",
      "%%Title: " + String(title || "plot").replace(/·/g, "-").replace(/[^\x20-\x7e]/g, "?"),
      "%%CreationDate: " + new Date().toISOString(),
      "%%LanguageLevel: 2",
      Object.keys(used).length ? "%%DocumentNeededResources: font " + Object.keys(used).join(" ") : "",
      "%%Pages: 1",
      "%%EndComments",
      "%%BeginProlog",
      "/M {moveto} bind def /L {lineto} bind def /C {closepath} bind def",
      "%%EndProlog",
      "%%Page: 1 1",
      "save",
      reencode.join("\n"),
      // Page space: 1 unit = 1 CSS px, origin at the top left, y downwards (like the canvas).
      "0 " + n3(Hpt) + " translate " + n3(PT) + " " + n3(-PT) + " scale",
      "1 1 1 setrgbcolor 0 0 " + W + " " + H + " rectfill"
    ].filter(Boolean);
    const text = head.concat(o, ["restore", "showpage", "%%Trailer", "%%EOF", ""]).join("\n");
    return new Blob([latin1(text)], { type: "application/postscript" });
  }

  const api = { Context: VectorContext, toSVG: toSVG, toPDF: toPDF, toEPS: toEPS, parseColor: parseColor };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VectorExport = api;
})(typeof window !== "undefined" ? window : globalThis);
