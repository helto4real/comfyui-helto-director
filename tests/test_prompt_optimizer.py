import base64
import io
import math
import struct
import time
from pathlib import Path
from unittest import mock

import av
import pytest
from PIL import Image

from routes import prompt_optimizer as prompt_optimizer_routes
from shared import prompt_optimizer as optimizer


def test_resolve_model_known_alias_and_fallback_status():
    spec = optimizer.resolve_model("qwen3_vl_4b_fast")

    assert spec.repo_id == "Qwen/Qwen3-VL-4B-Instruct"
    assert spec.backend == "qwen"
    statuses = optimizer.get_model_statuses()
    fallback = next(model for model in statuses["models"] if model["alias"] == "fallback_text_backend")
    assert fallback["status"] == "ready"


def test_settings_save_clear_template_and_token(tmp_path):
    saved = optimizer.save_hf_token(" hf_test_token ", tmp_path)
    assert saved["tokenConfigured"] is True
    assert optimizer.configured_hf_token(tmp_path) == "hf_test_token"

    custom = "Custom {rating} prompt for {direction}. {continuity}"
    templated = optimizer.save_prompt_template(custom, tmp_path)
    assert templated["promptTemplateConfigured"] is True
    assert templated["promptTemplate"] == custom

    cleared = optimizer.clear_hf_token(tmp_path)
    assert cleared["tokenConfigured"] is False
    assert cleared["promptTemplateConfigured"] is True

    reset = optimizer.reset_prompt_template(tmp_path)
    assert reset["promptTemplateConfigured"] is False
    assert reset["promptTemplate"] == optimizer.DEFAULT_OPTIMIZER_PROMPT_TEMPLATE


def test_timing_profile_records_average(tmp_path):
    spec = optimizer.resolve_model("fallback_text_backend")

    first = optimizer.record_prompt_timing(spec, 10.0, tmp_path)
    second = optimizer.record_prompt_timing(spec, 20.0, tmp_path)
    stored = optimizer.load_optimizer_timing(tmp_path)["profiles"][optimizer.model_timing_key(spec)]

    assert first["sample_count"] == 1
    assert second["sample_count"] == 2
    assert stored["average_seconds"] == pytest.approx(15.0)
    assert stored["last_seconds"] == pytest.approx(20.0)


def test_decode_image_supports_data_url_and_direct_image_path(tmp_path):
    image_path = tmp_path / "guide.png"
    Image.new("RGB", (32, 16), color=(10, 20, 30)).save(image_path)

    encoded = data_url_image()
    from_data = optimizer.decode_image({"id": "data", "type": "Image", "image_data": encoded})
    from_path = optimizer.decode_image({"id": "path", "type": "Image", "mediaPath": str(image_path)})

    assert from_data.size == (16, 16)
    assert from_path.size == (32, 16)


def test_decode_image_supports_direct_video_path(tmp_path):
    video_path = tmp_path / "clip.mp4"
    write_test_video(video_path)

    preview = optimizer.decode_image({"id": "video", "type": "Video", "mediaPath": str(video_path)})

    assert preview is not None
    assert preview.size == (32, 32)


def test_fallback_optimize_segments_timeline_only():
    result = optimizer.optimize_segments(
        {
            "model": "fallback_text_backend",
            "mode": "sfw",
            "segments": [
                {
                    "id": "section_001",
                    "type": "Text",
                    "selected": True,
                    "direction": "A dancer turns toward the camera",
                    "start": 0,
                    "length": 24,
                }
            ],
            "references": [],
        }
    )

    assert result["ok"] is True
    assert result["results"][0]["kind"] == "timeline"
    assert result["results"][0]["id"] == "section_001"
    assert "dancer turns toward the camera" in result["results"][0]["prompt"]


def test_optimizer_job_completes(monkeypatch, tmp_path):
    monkeypatch.setattr(optimizer, "TIMING_FILE", tmp_path / "timing.json")
    job_id = optimizer.start_optimizer_job(
        {
            "model": "fallback_text_backend",
            "mode": "sfw",
            "segments": [{"id": "section_001", "type": "Text", "selected": True, "direction": "walk forward"}],
            "references": [],
        }
    )
    try:
        status = wait_for_job(job_id)
        assert status["state"] == "completed"
        assert status["results"][0]["id"] == "section_001"
        assert status["progress"]["phase"] == "completed"
    finally:
        with optimizer._OPTIMIZER_JOBS_LOCK:
            optimizer._OPTIMIZER_JOBS.pop(job_id, None)


def test_unload_optimizer_model_removes_loaded_alias_and_clears_cuda():
    class FakeCuda:
        def __init__(self):
            self.emptied = False
            self.collected = False

        def is_available(self):
            return True

        def empty_cache(self):
            self.emptied = True

        def ipc_collect(self):
            self.collected = True

    fake_cuda = FakeCuda()
    fake_torch = mock.Mock(cuda=fake_cuda)
    optimizer._LOADED_MODELS["qwen3_vl_4b_fast"] = {"torch": fake_torch, "model": object()}
    try:
        result = optimizer.unload_optimizer_model("qwen3_vl_4b_fast")
        assert result["unloaded"] == ["qwen3_vl_4b_fast"]
        assert "qwen3_vl_4b_fast" not in optimizer._LOADED_MODELS
        assert fake_cuda.emptied is True
        assert fake_cuda.collected is True
    finally:
        optimizer._LOADED_MODELS.clear()


def test_route_module_uses_helto_prefix():
    assert prompt_optimizer_routes.ROUTE_PREFIX == "/helto_director/prompt_optimizer"
    assert callable(prompt_optimizer_routes.register_prompt_optimizer_routes)


def data_url_image():
    image = Image.new("RGB", (16, 16), color=(255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def write_test_video(path: Path):
    try:
        with av.open(str(path), "w") as container:
            stream = container.add_stream("mpeg4", rate=1)
            stream.width = 32
            stream.height = 32
            stream.pix_fmt = "yuv420p"
            frame = av.VideoFrame.from_image(Image.new("RGB", (32, 32), color=(30, 60, 90)))
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
    except Exception as exc:
        pytest.skip(f"PyAV video encode unavailable: {exc}")


def wait_for_job(job_id):
    deadline = time.time() + 5
    while time.time() < deadline:
        status = optimizer.get_optimizer_job_status(job_id)
        if status["state"] in {"completed", "failed"}:
            return status
        time.sleep(0.05)
    raise AssertionError("optimizer job did not finish")
