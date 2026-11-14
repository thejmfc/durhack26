#!/usr/bin/env bash
# Downloads the free NASA source data used by Skyline (~250 MB total) into data/raw/.
# File names verified against the source pages on 2026-11-14:
#   https://svs.gsfc.nasa.gov/4720            (CGI Moon Kit - visuals only, not science-grade)
#   https://pgda.gsfc.nasa.gov/products/78    (LOLA 5 m/px south-pole site DEMs)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw
SVS=https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720
PGDA=https://pgda.gsfc.nasa.gov/data/LOLA_5mpp
fetch() { # url
  local out="data/raw/$(basename "$1")"
  if [ -s "$out" ]; then echo "have $out"; else echo "get  $1"; curl -fsSL --retry 3 -o "$out.part" "$1" && mv "$out.part" "$out"; fi
}
fetch "$SVS/lroc_color_poles_4k.tif"                      # global colour map (4k, poles filled)
fetch "$SVS/ldem_16_uint.tif"                             # global displacement, 16 px/deg, uint16 half-metres
# 5 m DEMs, 16 x 16 km each: Connecting ridge, Shackleton rim, Peak near Shackleton, de Gerlache rim, Leibnitz beta plateau
for S in Site01 Site04 Site07 Site11 Site20; do fetch "$PGDA/$S/${S}_final_adj_5mpp_surf.tif"; done
fetch "$PGDA/README"
ls -lh data/raw
