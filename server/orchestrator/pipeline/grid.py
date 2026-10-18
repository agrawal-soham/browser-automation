"""Clicking by position, through a grid: the fallback for what the page outline has no ref for (a spreadsheet cell on a
canvas, an icon, a popup button without markup) and for a ref click that keeps failing.

  1. The masked screenshot, without the layer's labels, gets a chessboard grid (A1 at the top left). The VLM names
     the cell that holds the thing described ("D7"), saying first what it sees there.
  2. That cell and its neighbours are cut out, enlarged and given a numbered 6x6 sub-grid; the VLM names the sub-cell.
  3. The sub-cell's centre, in CSS pixels, is the point. step.py then has the VLM check it on the screenshot with a marker
     (verify.py); its no blocks the click.

The layer turns the point into the element there and applies the same checks and confirmations as to a ref.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..imaging import GRID_COLS, GRID_ROWS, draw_grid, parse_cell, to_css, zoom_cell
from ..turn import Turn
from ..vlm_link import VlmLink
from .recover import parse_location


@dataclass
class GridHit:
    # CSS pixels of the screenshot.
    x: float
    y: float
    cell: str
    sub: int
    seen: str


def cell_prompt(description: str) -> str:
    return (
        f"Find this on the screenshot: {description}\n"
        f"A blue grid covers the picture: columns A to {chr(ord('A') + GRID_COLS - 1)} from left to right, rows 1 to "
        f"{GRID_ROWS} from top to bottom; each cell's name is in its top-left corner. Grey boxes hide personal data on "
        "purpose; ignore them.\n"
        "First say what you see where it is, then the cell that holds its centre.\n"
        'Answer only with JSON: {"seen": "<what is there>", "cell": "<such as D7, or null if it is not on the screen>"}'
    )


def sub_prompt(description: str) -> str:
    return (
        f"This is an enlarged part of a web page. Find this on it: {description}\n"
        "Blue lines divide it into 36 squares, numbered 1 to 36 from the top left, row by row; each number is in its "
        "square's top-left corner.\n"
        "First say what you see, then the number of the square that holds the centre of what was asked for.\n"
        'Answer only with JSON: {"seen": "<what is there>", "square": <1 to 36, or null if it is not here>}'
    )


async def locate_on_grid(description: str, turn: Turn, link: VlmLink) -> tuple[GridHit | None, str]:
    """The point of `description` on the latest screenshot, or None and why not."""
    if not turn.image_b64:
        return None, "no screenshot"
    reply = await link.run("chat", cell_prompt(description), draw_grid(turn.image_b64))
    if reply is None:
        return None, link.last_error or "no answer"
    first = parse_location(str((reply.result or {}).get("text", ""))) or {}
    cell = parse_cell(first.get("cell"))
    if cell is None:
        return None, f"not found on the grid ({first.get('seen') or first.get('cell') or 'no readable answer'})"
    zoom = zoom_cell(turn.image_b64, *cell)
    reply = await link.run("chat", sub_prompt(description), zoom.image_b64)
    if reply is None:
        return None, link.last_error or "no answer"
    second = parse_location(str((reply.result or {}).get("text", ""))) or {}
    try:
        square = int(second.get("square"))
    except (TypeError, ValueError):
        return None, f"not found in the enlarged cell ({second.get('seen') or 'no readable answer'})"
    center = zoom.sub_center(square)
    if center is None:
        return None, f"square {square} does not exist"
    x, y = to_css(*center, turn.image_scale)
    name = str(first.get("cell")).strip().upper()
    return GridHit(x, y, name, square, str(second.get("seen") or first.get("seen") or "")), ""
