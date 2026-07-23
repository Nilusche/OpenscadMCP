"""Tile multiple rendered views into a single labeled contact-sheet PNG.

Keeping Pillow isolated here means the OpenSCAD wrapper stays dependency-free.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

_BG = (250, 250, 240)      # match OpenSCAD's default background
_LABEL_BG = (40, 40, 40)
_LABEL_FG = (255, 255, 255)
_LABEL_H = 22


def tile_images(
    labeled_paths: list[tuple[str, Path]],
    out_path: Path,
    *,
    columns: int | None = None,
) -> Path:
    """Compose *labeled_paths* [(label, png_path), ...] into one grid image.

    Each cell shows the view's render with a caption bar. The grid is as square
    as possible unless *columns* is given. Returns *out_path*.
    """
    if not labeled_paths:
        raise ValueError("No images to tile.")

    tiles = [(label, Image.open(p).convert("RGB")) for label, p in labeled_paths]
    cell_w = max(im.width for _, im in tiles)
    cell_h = max(im.height for _, im in tiles) + _LABEL_H

    n = len(tiles)
    cols = columns or math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)

    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), _BG)
    draw = ImageDraw.Draw(sheet)

    for idx, (label, im) in enumerate(tiles):
        r, c = divmod(idx, cols)
        x0 = c * cell_w
        y0 = r * cell_h
        # caption bar
        draw.rectangle([x0, y0, x0 + cell_w, y0 + _LABEL_H], fill=_LABEL_BG)
        draw.text((x0 + 6, y0 + 5), label, fill=_LABEL_FG)
        # center the render under the caption
        ox = x0 + (cell_w - im.width) // 2
        oy = y0 + _LABEL_H + (cell_h - _LABEL_H - im.height) // 2
        sheet.paste(im, (ox, oy))
        im.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path
