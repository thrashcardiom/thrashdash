#!/usr/bin/env python3
"""Subset and convert the bundled dashboard fonts to woff2.

    .venv/bin/python tools/make_fonts.py DOWNLOAD_DIR

DOWNLOAD_DIR must contain the original TTF files (see web/fonts/NOTICE.txt for where each comes from).
Needs `pip install fonttools brotli` (developer-only; the running app never needs them).
Jost and Barlow Condensed are subset to Latin plus the symbols dashboards use; DSEG is not (see NO_SUBSET).
"""
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parents[1] / "web" / "fonts"
# basic Latin, degree, plus/minus, multiplication, fractions (1/4 1/2 3/4), en/em dash, bullet
UNICODES = list(range(0x20, 0x7F)) + [0xB0, 0xB1, 0xD7, 0xBC, 0xBD, 0xBE, 0x2013, 0x2014, 0x2022]
# DSEG declares a Reserved Font Name ("DSEG"). Under the OFL a subsetted font counts as a modified version
# and may not keep that name, so DSEG is only converted to woff2 (glyph data untouched), never subset.
NO_SUBSET = {"DSEG7Classic-Regular.ttf", "DSEG7Classic-Bold.ttf"}
FONTS = {  # source file -> output name
    "Jost[wght].ttf": "Jost-Variable.woff2",
    "BarlowCondensed-Medium.ttf": "BarlowCondensed-Medium.woff2",
    "BarlowCondensed-SemiBold.ttf": "BarlowCondensed-SemiBold.woff2",
    "DSEG7Classic-Regular.ttf": "DSEG7Classic-Regular.woff2",
    "DSEG7Classic-Bold.ttf": "DSEG7Classic-Bold.woff2",
}


def main(src_dir):
    src = Path(src_dir)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, out_name in FONTS.items():
        font = TTFont(src / name)
        if name not in NO_SUBSET:
            options = subset.Options()
            options.layout_features = ["*"]
            options.notdef_outline = True
            sub = subset.Subsetter(options)
            sub.populate(unicodes=UNICODES)
            sub.subset(font)
        font.flavor = "woff2"
        font.save(OUT / out_name)
        print(f"{out_name}: {(OUT / out_name).stat().st_size} bytes")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
