"""Drawing on the (already masked) screenshot, for the VLM. The image stays on this side only long enough to go to the
VLM worker; nothing is saved.

- `draw_marker`: a ring and a cross where the agent wants to click, so the VLM can say whether that spot is right.
- `draw_grid` / `zoom_cell`: the grid for clicking by position. The VLM names a cell of a chessboard grid (D7), then a
  numbered sub-cell of that cell zoomed in; `GridZoom.sub_center` turns the answer into image pixels, and the screenshot's
  scale (image pixels per CSS pixel) into the CSS pixels the layer takes.
"""

from __future__ import annotations

import base64
import io

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

GRID_COLS = 16
GRID_ROWS = 9
SUB = 6
# Blue: the masking boxes are grey, the layer's ref labels red, the verify marker pink.
GRID_COLOR = (0, 110, 255)


def draw_marker(image_b64: str, x_css: float, y_css: float, scale: float) -> str:
    """The screenshot with a marker at a point given in CSS pixels; `scale` is image pixels per CSS pixel."""
    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    x, y = x_css * scale, y_css * scale
    r = max(10, int(14 * scale))
    d = ImageDraw.Draw(img)
    color = (255, 0, 160)
    width = max(2, int(3 * scale))
    d.ellipse([x - r, y - r, x + r, y + r], outline=color, width=width)
    d.line([x - 2 * r, y, x + 2 * r, y], fill=color, width=width)
    d.line([x, y - 2 * r, x, y + 2 * r], fill=color, width=width)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def _open(image_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")


def image_size(image_b64: str) -> tuple[int, int]:
    """Width and height of an image in pixels."""
    return _open(image_b64).size


def _encode(img: Image.Image) -> str:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def _label(d: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int) -> None:
    font = ImageFont.load_default(size=size)
    box = d.textbbox((x + 2, y + 1), text, font=font)
    d.rectangle([box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1], fill=(255, 255, 255))
    d.text((x + 2, y + 1), text, fill=GRID_COLOR, font=font)


def cell_name(col: int, row: int) -> str:
    """Zero-based column and row as a chessboard name: (3, 6) is D7."""
    return f"{chr(ord('A') + col)}{row + 1}"


def parse_cell(name: object, cols: int = GRID_COLS, rows: int = GRID_ROWS) -> tuple[int, int] | None:
    """A cell name such as "D7" as a zero-based (column, row); None when it is not a cell of the grid."""
    text = str(name or "").strip().upper()
    if len(text) < 2 or not text[0].isalpha() or not text[1:].isdigit():
        return None
    col, row = ord(text[0]) - ord("A"), int(text[1:]) - 1
    return (col, row) if 0 <= col < cols and 0 <= row < rows else None


def draw_grid(image_b64: str, cols: int = GRID_COLS, rows: int = GRID_ROWS) -> str:
    """The screenshot with a grid over it, each cell named in its top-left corner (A1 at the top left)."""
    img = _open(image_b64)
    w, h = img.size
    d = ImageDraw.Draw(img)
    width = max(1, round(w / 900))
    for c in range(1, cols):
        d.line([c * w / cols, 0, c * w / cols, h], fill=GRID_COLOR, width=width)
    for r in range(1, rows):
        d.line([0, r * h / rows, w, r * h / rows], fill=GRID_COLOR, width=width)
    size = max(11, round(min(w / cols, h / rows) / 5))
    for c in range(cols):
        for r in range(rows):
            _label(d, c * w / cols, r * h / rows, cell_name(c, r), size)
    return _encode(img)


@dataclass
class GridZoom:
    """One cell and its neighbours, cut out, enlarged, with a numbered sub-grid; and how to map an answer back."""

    image_b64: str
    # The crop in the screenshot's pixels.
    left: float
    top: float
    width: float
    height: float
    sub: int = SUB

    def sub_center(self, number: int) -> tuple[float, float] | None:
        """The centre of sub-cell `number` (1 at the top left, row by row) in the screenshot's pixels."""
        if not 1 <= number <= self.sub * self.sub:
            return None
        col, row = (number - 1) % self.sub, (number - 1) // self.sub
        return self.left + (col + 0.5) * self.width / self.sub, self.top + (row + 0.5) * self.height / self.sub


def zoom_cell(
    image_b64: str, col: int, row: int, cols: int = GRID_COLS, rows: int = GRID_ROWS, around: int = 1, size: int = 900
) -> GridZoom:
    """The cell with `around` neighbours on each side (fewer at the edges), enlarged so its longer side is `size`."""
    img = _open(image_b64)
    w, h = img.size
    cw, ch = w / cols, h / rows
    left, top = max(0, col - around) * cw, max(0, row - around) * ch
    right, bottom = min(cols, col + around + 1) * cw, min(rows, row + around + 1) * ch
    crop = img.crop((round(left), round(top), round(right), round(bottom)))
    factor = size / max(crop.size)
    crop = crop.resize((max(1, round(crop.width * factor)), max(1, round(crop.height * factor))), Image.LANCZOS)
    d = ImageDraw.Draw(crop)
    zw, zh = crop.size
    for i in range(1, SUB):
        d.line([i * zw / SUB, 0, i * zw / SUB, zh], fill=GRID_COLOR, width=2)
        d.line([0, i * zh / SUB, zw, i * zh / SUB], fill=GRID_COLOR, width=2)
    for n in range(SUB * SUB):
        _label(d, (n % SUB) * zw / SUB, (n // SUB) * zh / SUB, str(n + 1), max(14, round(min(zw, zh) / SUB / 5)))
    return GridZoom(_encode(crop), left, top, right - left, bottom - top)


def to_css(x_image: float, y_image: float, scale: float) -> tuple[float, float]:
    """Screenshot pixels to the CSS pixels the layer takes; `scale` is image pixels per CSS pixel."""
    s = scale or 1.0
    return round(x_image / s, 1), round(y_image / s, 1)
