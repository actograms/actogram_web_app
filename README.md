# Actogram web app (actogram_web_app)

A single-page web app for plotting and analysing rodent locomotor activity rhythms and lighting records stored as **text-based `.AWD` data files**.

Developed by The Actogram Web Application Development Team (actograms@proton.me).

## Quick access

Use the app directly in your browser, with nothing to install:

**<https://actograms.github.io/actogram_web_app/>**

The same app is the `index.html` file in this repository. You can also download that file and open it locally, without an internet connection.

## Data handling and confidentiality

Your data stay on your computer.

- **No upload:** files you open or drop on the page are read by your browser with the File API and processed only in the browser's memory on your computer. They are never sent to the hosting server, to the developers, or to anyone else.
- **No server-side processing:** the app is a static web page, and every parsing, plotting and analysis step runs as JavaScript in your browser. The site has no back end, database or user accounts.
- **No storage, tracking or analytics:** the app does not use cookies, localStorage, IndexedDB or other browser storage, and it contains no analytics or tracking code. Loaded data, onset edits and settings are discarded when you close or reload the page.
- **Exports stay local:** CSV and PNG files are created in the browser and saved only to the location you choose.
- **Network requests:** these are the only connections the page makes, and none of them contain your data:
  - the page itself is downloaded from GitHub Pages;
  - web fonts are loaded from Google Fonts (`fonts.googleapis.com`, `fonts.gstatic.com`).

  As with any website, these services may log technical information such as your IP address and browser type under their own privacy policies.
- **Fully offline use:** for unpublished or confidential data, you can download `index.html` and open it from your own disk with the network disconnected. The app works the same, using system fonts instead of the web fonts.

You remain responsible for handling your data in line with your institution's policies and any applicable regulations.

## Features

**Input**

- Text-based `.AWD` data files, including the optional light column (`activity,light`) and missing data (values `< 0`)
- Simple CSV/TSV files with columns `timestamp, activity[, light]`
- Several files at once (drag and drop), with per-file lighting settings and analysis ranges
- Built-in simulated demo recordings (see [Demo data](#demo-data))

**Actogram**

- Single, double or triple plot
- Bin size chosen as a multiple of each file's sampling interval: 1× (the native interval) by default, up to 120 min
- Adjustable row period (τ-plot), row start time and row height
- Bar or heat-map style: grey heat map, or colour heat maps with the perceptually uniform viridis, magma or cividis scales (a colour bar with the count range is shown in the legend); gain and a noise floor apply to all styles
- Light/dark shading from the recorded light column or from a user-defined schedule (LD, LL, DD and T-cycles, in multiple segments)
- Missing data highlighted
- Onsets, offsets and regression lines drawn on the plot, with onsets coloured by the fit they belong to
- Onsets can be corrected by clicking on the actogram, with zoom, pan and move buttons while editing (see [Editing onsets](#editing-onsets))
- Onsets used for each regression can be chosen with the mouse, and several regressions can be run on one record (see [Regression fits and phase](#regression-fits-and-phase))
- Export to PNG

**Analyses** (over a range of rows chosen with the row inputs, the presets, or by dragging over rows on the actogram)

| Tab | Content |
|---|---|
| Periodogram | χ² periodogram (Sokolove & Bushell 1978) with a critical-value line, or Lomb–Scargle periodogram with a false-alarm threshold |
| Activity profile | Mean waveform ± SEM folded at the row period, the periodogram peak or a custom period; % activity in dark; IS, IV and RA (Van Someren et al. 1999) |
| Onsets & phase | Automatic onset/offset detection; one or more linear regressions of onsets (period τ, SE, r²); onset phase as ZT (LD) or CT (DD/LL) in hours or degrees; phase angle of entrainment ψ; activity duration α |
| Daily activity | Per-cycle totals split into light-phase and dark-phase activity, missing data and coverage |
| Lighting record | Day-by-day lights-on/off times, photoperiod and light level, with automatic grouping into LD / LL / DD segments |
| All files | Summary table across all loaded recordings |
| File info | Parsed header fields, recording span, missing samples, interval override |

Every table can be saved as CSV.

## Usage

1. Open `index.html` in a recent browser (Chrome, Edge, Firefox or Safari). You can also serve the repository with GitHub Pages. No installation or server is needed.
2. Drop one or more `.AWD` files onto the page, or click **Demo** to load a simulated recording.
3. Set the bin size (a multiple of the sampling interval), plot type and lighting source in the left panel, then choose the analysis range used by the periodogram, activity profile and the other analyses:
   - type the first and last row, or use a preset generated from the lighting segments;
   - or select it with the mouse: press **Select on actogram** (in the Analysis range panel, or **Select range on actogram** in the Periodogram and Activity profile tabs) and drag over rows on the actogram. You can also drag over the row labels at the left edge at any time. Esc cancels.
4. Read the results in the tabs below the actogram, and save PNG or CSV files.

### Editing onsets

1. Click **Edit onsets** in the left panel. A zoom bar appears above the actogram.
2. Magnify the part you want to check:
   - With the **Place onset** tool (the default), **drag a box** over the actogram to zoom into that area. A flat box zooms the time axis only, and a narrow box zooms the rows only. Press Esc while dragging to cancel.
   - **Zoom** − / + changes the magnification of both axes. **Time** − / + zooms only the time axis (up to ×48), and **Rows** − / + only enlarges the rows (up to ×8).
   - **Back** returns to the previous zoom. **Fit analysis range** fills the plot with the rows of the analysis range, and **Reset** returns to the whole record.
   - With the mouse: Ctrl/⌘ + wheel (or a trackpad pinch) zooms time around the pointer, and Alt + wheel zooms rows.
3. Move around the zoomed actogram:
   - with the **Move** ← ↑ ↓ → buttons,
   - with the arrow keys (and + / − to zoom) after clicking the actogram,
   - by dragging with the **Pan** tool, or with Shift + drag in any tool,
   - with the wheel (rows) and Shift + wheel (sideways).

   Scroll indicators at the bottom and right edges show where you are.
4. Click a row to place that cycle's onset, or Shift-click to delete the nearest onset. Edited onsets are drawn as rings.

### Regression fits and phase

- **Fit 1 (auto)** uses every onset in the analysis range until you change it.
- **Choosing onsets with the mouse:** in edit mode, switch to the **Select for fit** tool. Click an onset, or drag a box around several, to add them to the active fit. Onsets that are all in the active fit already are removed from it. Onsets not used in any fit are drawn in grey and dimmed in the table.
- **Several regressions:** in the **Regression fits** panel, **Add fit** creates a new fit and makes it active, and clicking a fit makes it the active one. **One per condition** creates one fit for each lighting condition in the analysis range, for example one for LD and one for the following DD. This lets you, for example, fit the early and late parts of a free run separately, or exclude transients after a transfer.
- **Results:** the **Onsets & phase** tab lists, for each fit, τ ± SE, r², the number of onsets and the fitted phase. Fits and onsets can be saved as CSV.
- **Phase:**
  - Under LD, phase is zeitgeber time, where ZT0 is lights-on.
  - Under DD or LL, phase is circadian time computed from the free-running period of the fit for that condition. The 0° reference can be the projected ZT0 of the preceding LD condition (the default; the last lights-on at or before the transfer), the start of the condition, or 00:00 of the first recording day.
  - Phase can be shown in hours (ZT/CT) or in degrees (0–360°).

Zoom applies only while editing. The normal view and the PNG export always show the whole record.

### AWD format summary

A text-based `.AWD` data file is plain text with a 7-line header followed by one sample per line:

```
DEMO_WT_LD-DD   <- line 1: name
03-mar-2026     <- line 2: start date (dd-mon-yyyy)
14:36           <- line 3: start time (HH:MM, 24 h)
24              <- line 4: sample interval in minutes x 4 (4 = 1 min, 24 = 6 min)
0               <- line 5: age (not used)
M01             <- line 6: ID (not used)
M               <- line 7: sex (not used)
0,279           <- data: activity[,light], one sample per line; values < 0 = missing
```

Times are treated as clock times without time-zone or daylight-saving correction. If a file uses a non-numeric interval code, set the interval by hand in the **File info** tab.

## Methods

The **Methods** tab in the app documents every algorithm. In brief:

- **Binning:** the bin is a whole multiple of the sampling interval (default 1×, maximum 120 min), set separately for each file. Counts are summed. Bins with less than 50% valid samples are treated as missing, and partially covered bins are scaled up to a full bin.
- **χ² periodogram:** Qp = Σ nₕ(Mₕ − M)² / [Σ(X − M)² / N], with missing data allowed. The critical value is the χ² quantile with P − 1 degrees of freedom (Wilson–Hilferty approximation).
- **Onsets:** each onset is the maximum of a step template, mean(next W h) − mean(previous W h). It is tracked from cycle to cycle within a search window, then refined to the first sustained activity within ±1 h.
- **Offsets:** each offset is the first time, at least 2 h after onset, that is followed by 3 h of low activity.
- **Regression and period:** each fit is a least-squares line of onset time against cycle number, and its slope is the period τ.
- **Phase under LD:** zeitgeber time, ZT = time since the most recent lights-on, or ZT / T × 360° in degrees. The phase of an LD fit is the circular mean of its onsets' ZT.
- **Phase under DD/LL:** onset times t measured from the 0° reference are described by t = b + τ·n. The phase of an onset is ((t − reference) mod τ) / τ, shown as CT (× 24) or in degrees. The fitted phase of a regression is (b mod τ) / τ.
- **ψ and α:** ψ is the onset minus the nearest lights-off. α is the offset minus the onset.

The automatic onset detection is a convenience. Check the detected onsets on the actogram and correct them where needed before you report τ or ψ.

## Demo data

`demo_data/` and the **Demo** buttons provide **simulated** recordings generated by a seeded model of nocturnal wheel running (`src/gen.js`). **They are not measurements from real animals.**

| File | Scenario |
|---|---|
| `DEMO_WT_LD-DD.AWD` | 14 d LD 12:12, then 21 d DD (τ = 23.62 h), 6-min bins, with light column |
| `DEMO_LD-LL.AWD` | 10 d LD 12:12, then 20 d LL (τ ≈ 24.85 h), 1-min bins, with light column |
| `DEMO_JETLAG_ADV6.AWD` | 6-h advance of the LD cycle with re-entrainment transients and a missing-data gap |

## Repository layout

```
index.html        the app (generated; open this)
src/head.html     page title and styles
src/body.html     page markup
src/gen.js        demo-data generator
src/core.js       parsing, binning, lighting and analyses (no DOM; testable in Node)
src/app.js        user interface and drawing
build.sh          concatenates src/ into index.html
demo_data/        simulated example .AWD files
LICENSE           MIT License
```

After editing files in `src/`, run `sh build.sh` to regenerate `index.html`. The analysis functions can be used from Node, for example:

```js
const Acto = require("./src/core.js");
const rec = Acto.parseAWD(require("fs").readFileSync("demo_data/DEMO_WT_LD-DD.AWD", "utf8"));
```

## License

Copyright (c) 2026 The Actogram Web Application Development Team

Released under the [MIT License](LICENSE).

### Third-party components

- **Fonts:** the page loads [Archivo](https://fonts.google.com/specimen/Archivo), [Public Sans](https://fonts.google.com/specimen/Public+Sans) and [IBM Plex Mono](https://fonts.google.com/specimen/IBM+Plex+Mono) from Google Fonts at run time. All three are licensed under the SIL Open Font License 1.1. They are not redistributed in this repository, and the page falls back to system fonts when offline.
- **Other libraries:** none. The app uses only standard browser APIs.

### Basis of the design

The specification of this app was designed with reference to:

- analysis methods in common use in the field of circadian biology,
- analyses of mouse activity rhythms published as peer-reviewed scientific papers (see [References](#references)),
- the publicly documented text-based `.AWD` data-file format, and
- the specifications and features of various publicly available open-source software for activity-rhythm analysis.

Open-source software was consulted only for its specifications and for the kinds of features it implements. No source code was copied from it, and all code in this repository was written independently for this project. To the best of the developers' knowledge, nothing was done in developing this app that would raise a copyright problem.

### Development with AI assistance

This app was developed by The Actogram Web Application Development Team with the assistance of AI tools. AI assistance was used for writing code, documentation and the simulated demo data. The development team directed the work, reviewed the results, and is responsible for the contents of this repository.

### Intellectual property statement

During development, neither the development team nor the AI tools it used copied, decompiled or reverse-engineered source code, binaries or any other material from any commercial or proprietary software. Nothing was done that would infringe the rights of the owners of such software. No code was copied from open-source software either. The implementation is based only on:

- the publicly documented text-based `.AWD` data-file format, and
- the published scientific methods cited in the [References](#references).

To the best of the developers' knowledge, this repository does not infringe the intellectual property rights of others. If you believe any part of it does, please open an issue so it can be reviewed and corrected promptly.

## References

- Sokolove PG, Bushell WN (1978). The chi square periodogram: its utility for analysis of circadian rhythms. *J Theor Biol* 72:131–160.
- Lomb NR (1976). Least-squares frequency analysis of unequally spaced data. *Astrophys Space Sci* 39:447–462.
- Scargle JD (1982). Studies in astronomical time series analysis. II. *Astrophys J* 263:835–853.
- Van Someren EJW et al. (1999). Bright light therapy: improved sensitivity to its effects on rest-activity rhythms in Alzheimer patients by application of nonparametric methods. *Chronobiol Int* 16:505–518.
- Refinetti R, Cornélissen G, Halberg F (2007). Procedures for numerical analysis of circadian rhythms. *Biol Rhythm Res* 38:275–325.
- Pittendrigh CS, Daan S (1976). A functional analysis of circadian pacemakers in nocturnal rodents. I. The stability and lability of spontaneous frequency. *J Comp Physiol* 106:223–252.
- Jud C, Schmutz I, Hampp G, Oster H, Albrecht U (2005). A guideline for analyzing circadian wheel-running behavior in rodents under different lighting conditions. *Biol Proced Online* 7:101–116.

## Disclaimer

This software is provided "as is", without warranty of any kind. Verify the results before you use them in publications.
