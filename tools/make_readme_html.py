#!/usr/bin/env python3
# Copyright (c) 2026 The Actogram Web Application Development Team. SPDX-License-Identifier: MIT
"""Build readme.html (README as a web page with a contents sidebar) from README.md.

Usage, from the repository folder:  python3 tools/make_readme_html.py
Requires the Python "markdown" package (pip install markdown).
"""
import os, re, markdown
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..') + os.sep
src=open(root+'README.md',encoding='utf-8').read()

def gh_slug(value, separator='-'):
    # GitHub-style anchors: lower case, drop punctuation (keep letters/digits/_/-/space), spaces -> "-"
    v=re.sub(r'<[^>]+>','',value).strip().lower()
    v=re.sub(r'[^\w\- ]','',v,flags=re.U)
    return v.replace(' ','-')

md=markdown.Markdown(extensions=['tables','fenced_code','toc','sane_lists'],
                     extension_configs={'toc':{'slugify':gh_slug,'toc_depth':'2-3'}})
body=md.convert(src)
toc=md.toc

# Title and description come from the first lines of the README.
title='Actogram web app'
page='''<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Actogram web app – README</title>
<meta name="description" content="README of the Actogram web app: plotting and analysing activity rhythms and lighting records in the browser.">
<!-- Generated from README.md (same content). Figures are in docs/images/. -->
<style>
:root { --bg: #ffffff; --ink: #1c2230; --muted: #5b6375; --line: #dde1ea; --soft: #f3f5f9; --accent: #3446c9; --code: #eef1f6; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg: #0f1218; --ink: #e6e9ef; --muted: #9ba3b4; --line: #2a303c; --soft: #171b23; --accent: #8a97ff; --code: #1d222c; }
}
:root[data-theme="dark"] { --bg: #0f1218; --ink: #e6e9ef; --muted: #9ba3b4; --line: #2a303c; --soft: #171b23; --accent: #8a97ff; --code: #1d222c; }
* { box-sizing: border-box; }
html { scroll-padding-top: 16px; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 "Public Sans", "Inter", "Segoe UI", system-ui, -apple-system, sans-serif; }
.layout { display: grid; grid-template-columns: 270px minmax(0, 1fr); max-width: 1280px; margin: 0 auto; gap: 40px; padding: 0 24px; }
.layout > * { min-width: 0; }
main p, main li, main td { overflow-wrap: anywhere; }
nav.toc { position: sticky; top: 0; align-self: start; max-height: 100vh; overflow-y: auto; padding: 28px 0 40px; font-size: 14px; }
nav.toc .ttl { font-weight: 700; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin-bottom: 8px; }
nav.toc ul { list-style: none; margin: 0; padding: 0; }
nav.toc ul ul { padding-left: 14px; }
nav.toc li { margin: 2px 0; }
nav.toc a { color: var(--muted); text-decoration: none; display: block; padding: 2px 6px; border-radius: 4px; }
nav.toc a:hover { color: var(--ink); background: var(--soft); }
nav.toc > ul > li > a { color: var(--ink); font-weight: 600; margin-top: 6px; }
main { min-width: 0; padding: 28px 0 80px; }
h1, h2, h3, h4 { line-height: 1.25; font-family: "Archivo", "Inter Display", "Inter", system-ui, sans-serif; }
h1 { font-size: 2.1rem; margin: 0 0 .4em; }
h2 { font-size: 1.5rem; margin: 2.2em 0 .6em; padding-bottom: .3em; border-bottom: 1px solid var(--line); }
h3 { font-size: 1.2rem; margin: 1.8em 0 .5em; }
h4 { font-size: 1.02rem; margin: 1.4em 0 .4em; }
a { color: var(--accent); }
p, ul, ol, table, pre { margin: 0 0 1em; }
li { margin: .2em 0; }
code { font-family: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace; font-size: .88em; background: var(--code); padding: .1em .35em; border-radius: 4px; }
pre { background: var(--code); padding: 12px 16px; border-radius: 6px; overflow-x: auto; line-height: 1.45; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; display: block; overflow-x: auto; max-width: 100%; }
th, td { border: 1px solid var(--line); padding: 6px 12px; vertical-align: top; text-align: left; }
th { background: var(--soft); }
img { max-width: 100%; height: auto; border-radius: 6px; }
p[align="center"] img { border: 1px solid var(--line); }
img[align="right"] { margin: 0 0 12px 20px; border: 1px solid var(--line); max-width: 42%; }
td img { border: 1px solid var(--line); }
td em { color: var(--muted); font-size: .9em; }
p > em:only-child { display: block; color: var(--muted); font-size: .92em; margin-top: -.4em; }
blockquote { margin: 0 0 1em; padding: .2em 1em; border-left: 4px solid var(--line); color: var(--muted); }
hr { border: 0; border-top: 1px solid var(--line); margin: 2em 0; }
.topbar { display: flex; justify-content: flex-end; gap: 10px; font-size: 13px; margin-bottom: 8px; }
.topbar a { text-decoration: none; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; gap: 0; padding: 0 16px; }
  nav.toc { position: static; max-height: none; padding: 16px 0 0; border-bottom: 1px solid var(--line); }
  img[align="right"] { float: none !important; display: block; margin: 0 auto 12px; max-width: 100%; }
}
@media print { nav.toc { display: none; } .layout { display: block; } }
</style>
</head>
<body>
<div class="layout">
<nav class="toc" aria-label="Contents"><div class="ttl">Contents</div>
''' + toc + '''
</nav>
<main>
<div class="topbar"><a href="https://actograms.github.io/actogram_web_app/">Open the app</a> · <a href="docs/actogram_web_app_manual.pdf">User manual (PDF)</a> · <a href="README.md">README.md</a></div>
''' + body + '''
</main>
</div>
</body>
</html>
'''
open(root+'readme.html','w',encoding='utf-8').write(page)
print('Wrote readme.html')
