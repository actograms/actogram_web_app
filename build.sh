#!/bin/sh
# Assemble src/ into the single-file app index.html.
# Copyright (c) 2026 The Actogram Web Application Development Team. SPDX-License-Identifier: MIT
set -e
cd "$(dirname "$0")"
{
  printf '<!doctype html>\n<html lang="en">\n<head>\n'
  printf '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
  sed -n '1,/<\/style>/p' src/head.html
  printf '</head>\n<body>\n'
  cat src/body.html
  for f in gen core app; do printf '<script>\n'; cat "src/$f.js"; printf '</script>\n'; done
  printf '</body>\n</html>\n'
} > index.html
echo "Wrote index.html"
