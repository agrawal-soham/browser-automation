# server/vlm_worker/grounding.py

**Responsibility.** The `locate` job's prompt (UI-TARS's own grounding prompt; a JSON point for Qwen-VL), parsing of
the answer (`(x,y)`, `start_box`, a box's centre), the coordinate conventions (Qwen2.5-VL resize, pixels, 0-1000,
0-1) and the start-up calibration picture that picks the convention.

**Added 2026-10-18.**
