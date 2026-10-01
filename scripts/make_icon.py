# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Draws the Lily app icon (same family as the Tulip and Rose icons) and
writes it to every place the app looks for one.

    python3 scripts/make_icon.py

Needs rsvg-convert (brew install librsvg) and Pillow.
"""
import math
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
S = 1024
INSET = 0.098 * S  # macOS keeps this margin for its own shadow


def squircle(inset, n=5, steps=720):
    r = S / 2 - inset
    c0 = S / 2
    pts = []
    for i in range(steps):
        t = i / steps * 2 * math.pi
        c, s = math.cos(t), math.sin(t)
        x = c0 + math.copysign(abs(c) ** (2 / n), c) * r
        y = c0 + math.copysign(abs(s) ** (2 / n), s) * r
        pts.append(f"{x:.1f},{y:.1f}")
    return "M" + " L".join(pts) + " Z"


CX, CY = 512, 420   # centre of the bloom
L = 235             # petal length

# One petal pointing along +x; rotated into place six times. A lily petal is
# long and pointed, widest a third of the way out, with the tip flicked back.
PETAL = (f"M0,0 C {L*.25},-58 {L*.62},-64 {L*.86},-30 "
         f"C {L*.95},-16 {L},-4 {L*1.02},6 "
         f"C {L*.9},30 {L*.6},62 {L*.28},52 "
         f"C {L*.12},40 {L*.04},20 0,0 Z")
RIB = f"M{L*.08},2 C {L*.35},4 {L*.65},0 {L*.94},2"


def petal(angle, fill, rib):
    return (f'<g transform="translate({CX},{CY}) rotate({angle})">'
            f'<path d="{PETAL}" fill="{fill}"/>'
            f'<path d="{RIB}" fill="none" stroke="{rib}" stroke-width="7" '
            f'stroke-linecap="round"/></g>')


def speckles():
    out = []
    for a in range(-90, 270, 60):
        for d, off in ((62, -9), (80, 10), (98, -4), (72, 20), (88, -22)):
            t = math.radians(a + off * 0.45)
            out.append(f'<circle cx="{CX + d*math.cos(t):.1f}" '
                       f'cy="{CY + d*math.sin(t):.1f}" r="5" fill="#9E2F55"/>')
    return "".join(out)


def stamens():
    out = []
    for i, a in enumerate(range(-120, 240, 60)):
        t = math.radians(a + 15)
        r = 118 if i % 2 else 104
        x, y = CX + r * math.cos(t), CY + r * math.sin(t)
        mx, my = CX + r * .55 * math.cos(t - .25), CY + r * .55 * math.sin(t - .25)
        out.append(f'<path d="M{CX},{CY} Q{mx:.1f},{my:.1f} {x:.1f},{y:.1f}" '
                   f'fill="none" stroke="#E9D9A6" stroke-width="6" stroke-linecap="round"/>')
        out.append(f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="16" ry="8" fill="#C8662C" '
                   f'transform="rotate({a + 105} {x:.1f} {y:.1f})"/>')
    return "".join(out)


svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 {S} {S}">
  <defs>
    <linearGradient id="paper" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FDFCFA"/><stop offset="1" stop-color="#EFE8DE"/>
    </linearGradient>
    <radialGradient id="throat">
      <stop offset="0" stop-color="#F3EFC4"/><stop offset="1" stop-color="#F3EFC4" stop-opacity="0"/>
    </radialGradient>
    <clipPath id="tile"><path d="{squircle(INSET)}"/></clipPath>
  </defs>
  <g clip-path="url(#tile)">
    <rect width="{S}" height="{S}" fill="url(#paper)"/>

    <!-- stem and two narrow lily leaves, drawn first so the bloom sits over them -->
    <path d="M{CX},{CY} Q{CX+8},{CY+230} {CX},{CY+385}" fill="none" stroke="#4F6B4B"
          stroke-width="27" stroke-linecap="round"/>
    <path d="M{CX+3},{CY+330} C{CX-60},{CY+320} {CX-140},{CY+280} {CX-190},{CY+205}
             C{CX-110},{CY+225} {CX-40},{CY+265} {CX+3},{CY+330} Z" fill="#4F6B4B"/>
    <path d="M{CX+4},{CY+290} C{CX+60},{CY+280} {CX+130},{CY+245} {CX+175},{CY+180}
             C{CX+100},{CY+195} {CX+40},{CY+230} {CX+4},{CY+290} Z" fill="#5E7D59"/>

    <!-- back row of petals, a shade deeper -->
    {petal(-90, "#D9708F", "#C25478")}
    {petal(30, "#D9708F", "#C25478")}
    {petal(150, "#D9708F", "#C25478")}
    <!-- front row -->
    {petal(-30, "#EC9AB1", "#D9708F")}
    {petal(90, "#EC9AB1", "#D9708F")}
    {petal(210, "#EC9AB1", "#D9708F")}

    <circle cx="{CX}" cy="{CY}" r="70" fill="url(#throat)"/>
    {speckles()}
    {stamens()}
    <circle cx="{CX}" cy="{CY}" r="11" fill="#7E9C58"/>
  </g>
  <path d="{squircle(INSET)}" fill="none" stroke="#000" stroke-opacity=".1" stroke-width="3.6"/>
</svg>
'''

static = ROOT / "cps" / "static"
(static / "icon.svg").write_text(svg)
png = static / "icon.png"
subprocess.run(["rsvg-convert", "-w", "1024", "-h", "1024", "-o", str(png), str(static / "icon.svg")], check=True)

img = Image.open(png).convert("RGBA")
img.resize((800, 800), Image.LANCZOS).save(png)
img.resize((180, 180), Image.LANCZOS).save(static / "img" / "apple-touch-icon.png")
img.save(static / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
for name in ("cwa-logo-round-light.png", "cwa-logo-round-dark.png"):
    img.resize((300, 300), Image.LANCZOS).save(ROOT / "README_images" / name)
print("wrote icon.svg, icon.png, favicon.ico, apple-touch-icon.png, README logos")
