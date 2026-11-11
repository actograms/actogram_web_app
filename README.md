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
- Bar or heat-map style: grey heat map, or colour heat maps with the perceptually uniform viridis, magma or cividis scales (a colour bar with the count range is shown in the legend). In heat maps every recorded bin is coloured, including bins with zero counts, and light and dark are shown by a thin bar along the top of each row (amber = light, dark grey = dark). Gain and the noise floor ("Hide below", the low end of the heat-map scale) apply to all styles
- Light/dark shading from the recorded light column or from a user-defined schedule (LD, LL, DD and T-cycles, in multiple segments)
- Missing data highlighted
- Optional panel to the right of the actogram with the total activity of each row (daily total when the row period is 24 h), drawn as thin steel-blue bars, a line or numbers and aligned with the rows; partial rows are faded, and the panel is included in the PNG export
- Onsets, offsets and regression lines drawn on the plot, with onsets coloured by the fit they belong to
- Zoom, move and edit tools in a bar above the actogram, including regression-fit controls (see [Zooming, moving and editing onsets](#zooming-moving-and-editing-onsets))
- Onsets used for each regression can be chosen with the mouse, and several regressions can be run on one record (see [Regression fits and phase](#regression-fits-and-phase))
- Export to PNG

**Analyses** (over a range of rows chosen with the row inputs, the presets, or by dragging over rows on the actogram)

| Tab | Content |
|---|---|
| Periodogram | χ² periodogram (Sokolove & Bushell 1978) with a critical-value line, or Lomb–Scargle periodogram with a false-alarm threshold, for the analysis range and separately for the rows of each regression fit (table of peak periods vs τ from onsets, plus one small periodogram per fit) |
| Wavelet | Continuous Morlet wavelet transform: a time × period power map with the cone of influence, the ridge (period of maximum power at each time) and lighting; ridge period over time compared with each fit's τ; mean ridge period for the whole series, each regression fit and each lighting condition |
| Activity profile | Mean waveform ± SEM folded at the row period, the periodogram peak or a custom period; % activity in dark; IS, IV and RA (Van Someren et al. 1999). Also one profile per regression fit: by default folded at the LD cycle length with ZT on the x axis under LD, and at the fit's τ with CT on the x axis under DD/LL (options: periodogram peak, row period or a custom period; hours from the reference instead of ZT/CT) |
| Onsets & fits | Automatic onset/offset detection; one or more linear regressions of onsets (period τ, SE, r²); onset table with condition, phase, fit and residual; phase angle of entrainment ψ; activity duration α |
| Phase | Phase of each fit as ZT (LD) or CT (DD/LL) in hours and degrees; circular statistics of the onset phases (mean direction, mean vector length R, circular SD, Rayleigh test); the 0° reference used; phase differences between consecutive fits; a circular clock plot and a phase-versus-row plot; copy as tab-separated text or save as CSV |
| Daily activity | Per-cycle totals split into light-phase and dark-phase activity, missing data and coverage |
| Lighting record | Day-by-day lights-on/off times, photoperiod and light level, with automatic grouping into LD / LL / DD segments |
| All files | Summary table across all loaded recordings, including the χ² peak period and τ of each regression fit |
| File info | Parsed header fields, recording span, missing samples, interval override |

Every table can be saved as CSV.

## Usage

1. Open `index.html` in a recent browser (Chrome, Edge, Firefox or Safari). You can also serve the repository with GitHub Pages. No installation or server is needed.
2. Drop one or more `.AWD` files onto the page, or click **Demo** to load a simulated recording.
3. Set the bin size (a multiple of the sampling interval), plot type and lighting source in the left panel, then choose the analysis range used by the periodogram, activity profile and the other analyses:
   - type the first and last row, or use a preset generated from the lighting segments;
   - or select it with the mouse: press **Select on actogram** (in the Analysis range panel, or **Select range on actogram** in the Periodogram and Activity profile tabs) and drag over rows on the actogram. You can also drag over the row labels at the left edge at any time. Esc cancels.
4. Read the results in the tabs below the actogram, and save PNG or CSV files.

### Zooming, moving and editing onsets

A bar above the actogram is always shown. It has three rows.

1. **Mouse** selects what the mouse does on the actogram:
   - **Zoom** (the default) is view only: drag a box to zoom into that area, and clicks do not change anything.
   - **Place onset**: click a row to place that cycle's onset, or Shift-click to delete the nearest onset. Dragging a box still zooms. The **Edit onsets** button in the left panel switches between this tool and Zoom.
   - **Select for fit**: click onsets, or drag a box around them, to add them to the active regression fit or remove them.
   - **Pan**: drag to move the zoomed view. Shift + drag pans with any tool.

   A flat box zooms the time axis only, and a narrow box zooms the rows only. Esc cancels a box.
2. **View** changes the magnification and moves the view:
   - **Zoom** − / + changes the magnification of both axes. **Time** − / + zooms only the time axis (up to ×48), and **Rows** − / + only the rows (up to ×8).
   - **Move** ← ↑ ↓ → moves the view.
   - **Fit analysis range** shows exactly the analysis-range rows. **Back** returns to the previous zoom, and **Reset** returns to the whole record.
   - Keyboard and wheel: after clicking the actogram, the arrow keys move and + / − zoom. Ctrl/⌘ + wheel (or a pinch) zooms time, Alt + wheel zooms rows, and the wheel scrolls when zoomed.

   Scroll indicators at the bottom and right edges show where you are.
3. **Fits** shows one chip per regression fit.
   - Click a chip to make that fit active, or × to remove it.
   - **+ Add fit**, **One per condition** and **Reset fits** work as in the Regression fits panel.

Edited onsets are drawn as rings. The PNG export always shows the whole record, whatever the zoom.

### Regression fits and phase

- **Default:** when a file is opened, the fits are set to **One per condition**, with one automatic fit for each lighting condition (for example LD and DD) that has onsets in the analysis range. These fits follow changes of the analysis range and the lighting settings until you edit the fits by hand. **Reset** gives a single fit over the whole range. Without lighting information there is a single fit.
- **Choosing onsets with the mouse:** choose the **Select for fit** tool in the bar above the actogram. Click an onset, or drag a box around several, to add them to the active fit. Onsets that are all in the active fit already are removed from it. Onsets not used in any fit are drawn in grey and dimmed in the table.
- **Several regressions:** in the **Regression fits** panel or the **Fits** row of the bar above the actogram, **Add fit** creates a new fit and makes it active, and clicking a fit makes it the active one. **One per condition** creates one fit for each lighting condition in the analysis range, for example one for LD and one for the following DD. This lets you, for example, fit the early and late parts of a free run separately, or exclude transients after a transfer.
- **Results:** the **Onsets & fits** tab lists, for each fit, τ ± SE, r², the number of onsets and the fitted phase, plus a table of all onsets. Fits and onsets can be saved as CSV.
- **Wavelet analysis:** the **Wavelet** tab shows how the period changes over time, for example the transition from LD to DD or re-entrainment after a phase shift.
  - **Power map:** a Morlet wavelet power map with days on the x axis and period on the y axis, with lighting shown under the plot.
  - **Ridge:** the white line marks the period with the highest power at each moment.
  - **Edge effects:** the pale cone of influence near the record edges is excluded from the summaries.
  - **Summaries:** the mean ridge period is given for the whole series, for each regression fit (next to τ from onsets and the χ² peak) and for each lighting condition.
  - **Options:** the period range, the data used (analysis range or whole record) and the colour scale can be changed. Results can be saved as CSV.
- **Activity profile per fit:** the **Activity profile** tab also folds the rows of each regression fit.
  - **Default period and x axis:**
    - under LD, the fit is folded at the LD cycle length T, with ZT on the x axis (ZT0 = lights-on);
    - under DD or LL, it is folded at the fit's free-running period τ, with circadian time on the x axis (one cycle = 24 CT hours, CT0 = the free-run 0° reference of the Phase tab).
  - **Changing the period:** **Period** can be set instead to the fit's periodogram peak, the row period or a custom value.
  - **Changing the x axis:** **X axis → Hours from reference** shows plain hours.
  - **Results:** a table gives each fit's period, its source, its peak phase and % activity in dark, and each fit gets its own profile chart.
- **Periodogram per fit:** the **Periodogram** tab also analyses the rows covered by each regression fit, using the same method, period range and α. A table compares each fit's peak period with its τ from onsets, and a small periodogram is drawn for each fit in its colour. A fit needs at least twice the longest tested period of data.
- **Phase results:** the **Phase** tab is dedicated to phase.
  - **Cards and summary table:** one card per fit with its phase in ZT/CT hours and degrees, τ, and the circular statistics of its onset phases. The summary table also gives:
    - the mean vector length R (1 = identical phases, 0 = scattered);
    - the circular SD in hours, √(−2 ln R);
    - the Rayleigh test p-value;
    - the 0° reference that was used, as a date and time.
  - **Phase differences:** consecutive fits (for example LD → DD) are compared by their phase difference, wrapped to ±180° and shown in degrees and circadian hours.
  - **Clock plot:** each fit's onset phases sit on their own ring, with 0° at the top and phase running clockwise. An arrow marks the mean vector, and LD rings show the light and dark parts of the cycle.
  - **Phase-versus-row plot:** the phase of every onset across the record, with each fit's fitted phase as a dashed line. Transients after a lighting change show up clearly.
  - **Export:** results can be copied as tab-separated text, to paste into a spreadsheet, or saved as CSV.
- **LD fits on the actogram:** by default, fits under LD are drawn as a dotted line at their mean phase (ZT). Choose **LD fit line → Regression line** in the Actogram panel to draw them like DD/LL fits.
- **Phase:**
  - Under LD, phase is zeitgeber time, where ZT0 is lights-on.
  - Under DD or LL, phase is circadian time computed from the free-running period of the fit for that condition. The 0° reference can be the projected ZT0 of the preceding LD condition (the default; the last lights-on at or before the transfer), the start of the condition, 00:00 of the first recording day, or the start of actogram row 1.
  - Phase can be shown in hours (ZT/CT) or in degrees (0–360°).

Zoom applies only while editing. The normal view and the PNG export always show the whole record.

### AWD format summary

A text-based `.AWD` data file is plain text with a 7-line header followed by one sample per line (format as described in Brown et al. 2020, Support Protocol, Materials, item 10; see also the example data of the MRC Harwell sleep-tracking repository, listed in [References](#references)):

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
- **Circular statistics:** for the onset phases θ of a fit, R = |mean of (cos θ, sin θ)|, circular SD = √(−2 ln R), and the Rayleigh test uses Z = nR² with Zar's p-value approximation. Phase differences between fits are wrapped to ±180° (360° = 24 circadian hours).
- **Wavelet:** continuous wavelet transform with a Morlet wavelet (ω0 = 6) computed by FFT (Torrence & Compo 1998). Power is normalised by the variance of the series. The ridge is refined between grid periods by parabolic interpolation. Points inside the cone of influence (e-folding time √2·s from either end) and filled gaps are excluded from the summaries.
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
- Torrence C, Compo GP (1998). A practical guide to wavelet analysis. *Bull Am Meteorol Soc* 79:61–78.
- Leise TL (2013). Wavelet analysis of circadian and ultradian behavioral rhythms. *J Circadian Rhythms* 11:5.
- Zar JH (2010). *Biostatistical Analysis*, 5th ed. Pearson. (Circular statistics, Rayleigh test.)
- Batschelet E (1981). *Circular Statistics in Biology*. Academic Press.
- Van Someren EJW et al. (1999). Bright light therapy: improved sensitivity to its effects on rest-activity rhythms in Alzheimer patients by application of nonparametric methods. *Chronobiol Int* 16:505–518.
- Refinetti R, Cornélissen G, Halberg F (2007). Procedures for numerical analysis of circadian rhythms. *Biol Rhythm Res* 38:275–325.
- Pittendrigh CS, Daan S (1976). A functional analysis of circadian pacemakers in nocturnal rodents. I. The stability and lability of spontaneous frequency. *J Comp Physiol* 106:223–252.
- Jud C, Schmutz I, Hampp G, Oster H, Albrecht U (2005). A guideline for analyzing circadian wheel-running behavior in rodents under different lighting conditions. *Biol Proced Online* 7:101–116.
- Brown LA, Banks GT, Horner N, Wilcox SL, Nolan PM, Peirson SN (2020). Simultaneous assessment of circadian rhythms and sleep in mice using passive infrared sensors: a user's guide. *Curr Protoc Mouse Biol* 10(3):e81. (Support Protocol, Materials, item 10: text-based `.AWD` data-file format.)
- MRC Harwell. sleep-tracking (GitHub repository), example data. https://github.com/MRC-Harwell/sleep-tracking/tree/master/data
- Taufique SKT, Brown AJ, Mahadevan S, Ehichioya DE, Farah S, Shen M, Yamazaki S (2025). A novel circadian behavior rhythm in arrhythmic Period triple knockout mice revealed by constant light exposure. *iScience* 28(9):113292.

## Disclaimer

This software is provided "as is", without warranty of any kind. Verify the results before you use them in publications.
