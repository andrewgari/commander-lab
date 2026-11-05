#!/bin/sh
# Re-shoot the GN-D mockup states into docs/screenshots/genre/ (headless Chromium).
cd "$(dirname "$0")/../../.." || exit 1
mkdir -p docs/screenshots/genre
for s in empty set full search kindred browse; do
  for w in 1440 390; do
    h=1250; [ "$w" = 390 ] && h=1900
    chromium --headless=new --no-sandbox --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
      --virtual-time-budget=4000 --window-size=$w,$h \
      --screenshot="$PWD/docs/screenshots/genre/${s}_$w.png" \
      "file://$PWD/docs/design/genre/index.html?state=$s" >/dev/null 2>&1
  done
done
