# Overlay redesign prototypes

These pages are design prototypes for the Apollo overlay. The app implements the glass natively in `apollo_glass.py`.
Open the HTML files in Chrome or Edge. The glass effect uses `backdrop-filter: url(#filter)`, and only Chromium supports it.

## Files

- `glass.html`: Liquid Glass overlay. This is the selected direction.
  - Three materials: clear, milky light and milky dark.
  - States: idle, menu, recording with voice bars, processing, pasted, and automatic retry after an error.
  - Drag the sun to move the overlay over the moving background.
- `directions.html`: the first comparison of three directions (8-bit, ASCII and glass).
- `capture_exclusion.py`: feasibility check for real refraction of the desktop on Windows.

## How the glass works

1. A canvas computes a displacement map from a signed distance field of the shape.
2. The rim uses a convex squircle profile. It bends the most light at the edge.
3. Three `feDisplacementMap` passes with different scales give a small chromatic aberration.
4. Each droplet keeps a fixed map. Animations change only `transform` and `opacity`, so they stay smooth.
5. The menu droplets sit on a circle of radius 76 px. They do not touch the orb or each other.

## Native overlay on Windows

`capture_exclusion.py` shows that a window can read the pixels behind itself.
It uses `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)`.
A GDI grab of 160 x 160 px takes about 40 ms. That is too slow for smooth dragging.
A Qt version needs a faster capture, for example DXGI Desktop Duplication or Windows.Graphics.Capture.

Run the check with Python and Pillow:

```
python design/liquid-glass/capture_exclusion.py
```
