from vlm_worker import grounding


def test_gemma_is_its_own_family_with_a_scale_stated_prompt():
    assert grounding.family("gemma4:latest") == "gemma"
    prompt = grounding.locate_prompt("gemma4:latest", 'the green "Submit" button')
    assert "0 to 1000" in prompt and "point_2d" in prompt and "Submit" in prompt


def test_gemma_calibration_accepts_only_norm1000():
    w, h = grounding.CALIBRATION_SIZE
    left, top, right, bottom = grounding.CALIBRATION_TARGET
    centre = ((left + right) / 2 / w * 1000, (top + bottom) / 2 / h * 1000)
    assert grounding.pick_convention(centre, "gemma4:latest") == "norm1000"
    # The same answer read as pixels would be far from the button; gemma is never tried with that convention.
    assert grounding.conventions("gemma4:latest") == ("norm1000",)
    # Measured gemma4 answer for the Submit button: off in x by 71 px past the edge, so locate stays off.
    assert grounding.pick_convention((694, 793), "gemma4:latest") is None


def test_other_models_keep_all_conventions():
    assert grounding.conventions("hf.co/x/UI-TARS-1.5-7B-GGUF") == grounding.CONVENTIONS
