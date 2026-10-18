"""Pointing at a described element on a screenshot (the `locate` job), for grounding models such as UI-TARS-1.5.

A grounding model answers with a point, but in its own coordinate space: UI-TARS and Qwen2.5-VL count pixels of the
image as they resized it (sides rounded to multiples of 28, the area capped), other models use 0-1000. Ollama resizes
the image itself and does not say how, so the worker calibrates once at start-up: it shows the model a picture with one
button at a known place, and keeps the convention that puts the answer on that button (`calibrate`).
"""

from __future__ import annotations

import base64
import io
import math
import re

from PIL import Image, ImageDraw, ImageFont

# The coordinate conventions tried, most likely first. "resized:<n>": pixels of the image resized with at most n pixels
# (Ollama's qwen2.5vl engine caps at 28*28*1280; Hugging Face's default is 16384*28*28, about the original size).
CONVENTIONS = ("resized:1003520", "pixels", "norm1000", "norm1")


def family(model: str) -> str:
    name = model.lower()
    if "ui-tars" in name or "uitars" in name:
        return "ui-tars"
    if "qwen2.5vl" in name or "qwen2.5-vl" in name or "qwen3-vl" in name or "qwen3vl" in name:
        return "qwen-vl"
    return "other"


def locate_prompt(model: str, description: str) -> str:
    if family(model) == "ui-tars":
        # UI-TARS's own grounding prompt (its README): the answer is one point, "(x,y)".
        return (
            "Output only the coordinate of one point in your response. "
            f"What element matches the following task: {description}"
        )
    return (
        f"Find this on the screenshot: {description}\n"
        "Grey boxes hide personal data on purpose; ignore them.\n"
        'Answer only with JSON: {"point_2d": [x, y]} for the centre of it.'
    )


NUMBER = r"-?\d+(?:\.\d+)?"
GROUP = re.compile(rf"[\(\[]\s*({NUMBER})\s*,\s*({NUMBER})(?:\s*,\s*({NUMBER})\s*,\s*({NUMBER}))?\s*[\)\]]")


def parse_point(text: str) -> tuple[float, float] | None:
    """The first point in the answer: "(x,y)", "[x, y]", start_box='(x,y)', or the centre of a box (x1,y1,x2,y2)."""
    m = GROUP.search(text or "")
    if not m:
        return None
    x1, y1 = float(m.group(1)), float(m.group(2))
    if m.group(3) is not None:
        return (x1 + float(m.group(3))) / 2, (y1 + float(m.group(4))) / 2
    return x1, y1


def smart_resize(height: int, width: int, max_pixels: int, factor: int = 28, min_pixels: int = 56 * 56) -> tuple[int, int]:
    """Qwen2.5-VL's resize: sides rounded to multiples of `factor`, the area kept between the limits."""
    h = max(factor, round(height / factor) * factor)
    w = max(factor, round(width / factor) * factor)
    if h * w > max_pixels:
        beta = math.sqrt(height * width / max_pixels)
        h = max(factor, math.floor(height / beta / factor) * factor)
        w = max(factor, math.floor(width / beta / factor) * factor)
    elif h * w < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h = math.ceil(height * beta / factor) * factor
        w = math.ceil(width * beta / factor) * factor
    return h, w


def to_fraction(point: tuple[float, float], convention: str, width: int, height: int) -> tuple[float, float]:
    """The model's point as fractions of the image (0 to 1) under one convention."""
    x, y = point
    if convention == "norm1000":
        return x / 1000, y / 1000
    if convention == "norm1":
        return x, y
    if convention == "pixels":
        return x / width, y / height
    rh, rw = smart_resize(height, width, int(convention.split(":", 1)[1]))
    return x / rw, y / rh


def image_size(image_b64: str) -> tuple[int, int]:
    return Image.open(io.BytesIO(base64.b64decode(image_b64))).size


# The calibration picture: wide enough (1600 x 1000) that a capped resize differs from the original size, and the
# target away from the centre, so each convention lands somewhere else.
CALIBRATION_SIZE = (1600, 1000)
CALIBRATION_TARGET = (1180, 760, 1380, 830)
CALIBRATION_DESCRIPTION = 'the green "Submit" button'


def calibration_image() -> str:
    img = Image.new("RGB", CALIBRATION_SIZE, "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=32)
    except TypeError:
        font = ImageFont.load_default()
    d.rectangle([0, 0, 1600, 90], fill=(235, 238, 245))
    d.text((40, 25), "Contact form", fill="black", font=font)
    for i, label in enumerate(("Name", "Email", "Message")):
        top = 180 + i * 150
        d.text((200, top), label, fill="black", font=font)
        d.rectangle([200, top + 45, 1400, top + 105], outline=(150, 150, 150), width=3)
    d.rounded_rectangle([200, 760, 400, 830], radius=12, fill=(40, 90, 200))
    d.text((240, 775), "Cancel", fill="white", font=font)
    d.rounded_rectangle(CALIBRATION_TARGET, radius=12, fill=(30, 150, 60))
    d.text((CALIBRATION_TARGET[0] + 45, CALIBRATION_TARGET[1] + 15), "Submit", fill="white", font=font)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def pick_convention(point: tuple[float, float]) -> str | None:
    """The first convention that puts the calibration answer on the target (with a small margin), or None."""
    w, h = CALIBRATION_SIZE
    left, top, right, bottom = CALIBRATION_TARGET
    margin = 15
    for convention in CONVENTIONS:
        fx, fy = to_fraction(point, convention, w, h)
        x, y = fx * w, fy * h
        if left - margin <= x <= right + margin and top - margin <= y <= bottom + margin:
            return convention
    return None
