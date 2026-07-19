import asyncio
import base64
import io
import math
import struct
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import av
import pytest
from aiohttp import web as aiohttp_web
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


def test_snapshot_model_downloaded_requires_transformers_weights(monkeypatch, tmp_path):
    spec = optimizer.OptimizerModelSpec("partial_qwen", "owner/Partial-Qwen", "qwen", "VLM")
    monkeypatch.setattr(optimizer, "_models_dir", lambda: tmp_path)
    path = optimizer.model_path_for(spec)
    path.mkdir(parents=True)
    (path / "README.md").write_text("partial download", encoding="utf-8")

    assert optimizer.model_downloaded(spec) is False

    (path / "config.json").write_text("{}", encoding="utf-8")
    assert optimizer.model_downloaded(spec) is False

    (path / "model.safetensors").write_bytes(b"weights")
    assert optimizer.model_downloaded(spec) is True


def test_snapshot_model_downloaded_requires_all_index_shards(monkeypatch, tmp_path):
    spec = optimizer.OptimizerModelSpec("sharded_qwen", "owner/Sharded-Qwen", "qwen", "VLM")
    monkeypatch.setattr(optimizer, "_models_dir", lambda: tmp_path)
    path = optimizer.model_path_for(spec)
    path.mkdir(parents=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    (path / "model.safetensors.index.json").write_text(
        (
            '{"weight_map": {'
            '"layer_a": "model-00001-of-00002.safetensors", '
            '"layer_b": "model-00002-of-00002.safetensors"'
            "}}"
        ),
        encoding="utf-8",
    )
    (path / "model-00001-of-00002.safetensors").write_bytes(b"shard")

    assert optimizer.model_downloaded(spec) is False

    (path / "model-00002-of-00002.safetensors").write_bytes(b"shard")
    assert optimizer.model_downloaded(spec) is True


def test_ensure_model_downloaded_rejects_incomplete_snapshot(monkeypatch, tmp_path):
    spec = optimizer.OptimizerModelSpec("partial_qwen", "owner/Partial-Qwen", "qwen", "VLM")
    monkeypatch.setattr(optimizer, "_models_dir", lambda: tmp_path)

    def fake_snapshot_download(**kwargs):
        local_dir = Path(kwargs["local_dir"])
        local_dir.mkdir(parents=True)
        (local_dir / "README.md").write_text("partial download", encoding="utf-8")
        return str(local_dir)

    with mock.patch("huggingface_hub.snapshot_download", side_effect=fake_snapshot_download):
        with pytest.raises(optimizer.PromptOptimizerError, match="missing config.json or model weight files"):
            optimizer.ensure_model_downloaded(spec)


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

    ollama = optimizer.save_ollama_settings(
        {
            "base_url": "http://127.0.0.1:11434/",
            "model": "llava:latest",
            "keep_alive_seconds": "0",
            "temperature": "0.15",
            "top_p": "0.8",
            "top_k": "30",
            "repeat_penalty": "1.1",
            "num_ctx": "2048",
            "num_predict": "64",
        },
        tmp_path,
    )
    assert ollama["ollamaSettings"]["base_url"] == "http://127.0.0.1:11434"
    assert ollama["ollamaSettings"]["model"] == "llava:latest"
    assert ollama["ollamaSettings"]["keep_alive_seconds"] == 0
    assert ollama["ollamaSettings"]["num_predict"] == 64


def test_timing_profile_records_average(tmp_path):
    spec = optimizer.resolve_model("fallback_text_backend")

    first = optimizer.record_prompt_timing(spec, 10.0, tmp_path)
    second = optimizer.record_prompt_timing(spec, 20.0, tmp_path)
    stored = optimizer.load_optimizer_timing(tmp_path)["profiles"][optimizer.model_timing_key(spec)]

    assert first["sample_count"] == 1
    assert second["sample_count"] == 2
    assert stored["average_seconds"] == pytest.approx(15.0)
    assert stored["last_seconds"] == pytest.approx(20.0)


def test_ollama_status_uses_single_local_model_without_pulling(monkeypatch):
    def fake_request(endpoint, payload=None, **kwargs):
        if endpoint == "/api/tags":
            assert payload is None
            return {"models": [{"name": "llava:latest"}]}
        if endpoint == "/api/show":
            assert payload == {"model": "llava:latest"}
            return {"capabilities": ["completion", "vision"]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(optimizer, "_ollama_request_json", fake_request)

    status = optimizer.ollama_connection_status({"base_url": optimizer.DEFAULT_OLLAMA_BASE_URL, "model": ""})

    assert status["status"] == "ready"
    assert status["active_model"] == "llava:latest"
    assert status["capabilities"] == ["completion", "vision"]
    assert status["supports_vision"] is True


def test_ollama_status_reports_non_vision_capability(monkeypatch):
    def fake_request(endpoint, payload=None, **kwargs):
        if endpoint == "/api/tags":
            return {"models": [{"name": "mistral:latest"}]}
        if endpoint == "/api/show":
            assert payload == {"model": "mistral:latest"}
            return {"capabilities": ["completion"]}
        raise AssertionError(endpoint)

    monkeypatch.setattr(optimizer, "_ollama_request_json", fake_request)

    status = optimizer.ollama_connection_status({"base_url": optimizer.DEFAULT_OLLAMA_BASE_URL, "model": "mistral:latest"})

    assert status["status"] == "ready"
    assert status["active_model"] == "mistral:latest"
    assert status["supports_vision"] is False
    assert status["vision_status"] == "unsupported"


def test_ollama_status_requires_choice_when_multiple_local_models(monkeypatch):
    monkeypatch.setattr(
        optimizer,
        "_ollama_request_json",
        lambda *args, **kwargs: {"models": [{"name": "llava:latest"}, {"name": "mistral:latest"}]},
    )

    status = optimizer.ollama_connection_status({"base_url": optimizer.DEFAULT_OLLAMA_BASE_URL, "model": ""})

    assert status["status"] == "choose_model"
    assert status["active_model"] == ""


def test_generate_ollama_sends_images_and_final_keep_alive(monkeypatch):
    calls = []

    def fake_request(endpoint, payload=None, **kwargs):
        if endpoint == "/api/tags":
            return {"models": [{"name": "llava:latest"}]}
        if endpoint == "/api/show":
            return {"capabilities": ["completion", "vision"]}
        if endpoint == "/api/generate":
            calls.append(payload)
            return {"response": "visual optimized prompt"}
        raise AssertionError(endpoint)

    monkeypatch.setattr(optimizer, "_ollama_request_json", fake_request)
    monkeypatch.setattr(
        optimizer,
        "configured_ollama_settings",
        lambda base_dir=None: optimizer.normalize_ollama_settings(
            {"model": "llava:latest", "keep_alive_seconds": 0, "num_predict": 33}
        ),
    )
    image = Image.new("RGB", (8, 8), color=(10, 20, 30))
    spec = optimizer.resolve_model(optimizer.OLLAMA_LOCAL_ALIAS)

    response = optimizer._generate_ollama(
        spec,
        [("Current", image)],
        "System optimizer instructions.",
        "Write the motion prompt.",
        final_request=True,
    )

    assert response == "visual optimized prompt"
    assert calls[0]["model"] == "llava:latest"
    assert calls[0]["system"] == "System optimizer instructions."
    assert calls[0]["prompt"].endswith("Write the motion prompt.")
    assert calls[0]["think"] is False
    assert calls[0]["keep_alive"] == 0
    assert calls[0]["options"]["num_predict"] == 33
    assert calls[0]["images"]
    assert "Current image is attached." in calls[0]["prompt"]


def test_generate_ollama_keeps_intermediate_zero_keep_alive_loaded(monkeypatch):
    calls = []

    def fake_request(endpoint, payload=None, **kwargs):
        if endpoint == "/api/tags":
            return {"models": [{"name": "llava:latest"}]}
        if endpoint == "/api/show":
            return {"capabilities": ["completion", "vision"]}
        if endpoint == "/api/generate":
            calls.append(payload)
            return {"response": "intermediate prompt"}
        raise AssertionError(endpoint)

    monkeypatch.setattr(optimizer, "_ollama_request_json", fake_request)
    monkeypatch.setattr(
        optimizer,
        "configured_ollama_settings",
        lambda base_dir=None: optimizer.normalize_ollama_settings(
            {"model": "llava:latest", "keep_alive_seconds": 0}
        ),
    )

    optimizer._generate_ollama(
        optimizer.resolve_model(optimizer.OLLAMA_LOCAL_ALIAS),
        [],
        "System optimizer instructions.",
        "Write the motion prompt.",
        final_request=False,
    )

    assert "keep_alive" not in calls[0]


def test_generate_ollama_rejects_image_for_model_without_vision(monkeypatch):
    calls = []

    def fake_request(endpoint, payload=None, **kwargs):
        if endpoint == "/api/tags":
            return {"models": [{"name": "mistral:latest"}]}
        if endpoint == "/api/show":
            return {"capabilities": ["completion"]}
        if endpoint == "/api/generate":
            calls.append(payload)
            return {"response": "should not generate"}
        raise AssertionError(endpoint)

    monkeypatch.setattr(optimizer, "_ollama_request_json", fake_request)
    monkeypatch.setattr(
        optimizer,
        "configured_ollama_settings",
        lambda base_dir=None: optimizer.normalize_ollama_settings({"model": "mistral:latest"}),
    )
    image = Image.new("RGB", (8, 8), color=(10, 20, 30))

    with pytest.raises(optimizer.PromptOptimizerError, match="does not advertise vision support"):
        optimizer._generate_ollama(
            optimizer.resolve_model(optimizer.OLLAMA_LOCAL_ALIAS),
            [("Current", image)],
            "System optimizer instructions.",
            "Write the motion prompt.",
        )

    assert calls == []


def test_generate_ollama_wraps_image_rejection_when_capability_is_unknown(monkeypatch):
    def fake_request(endpoint, payload=None, **kwargs):
        if endpoint == "/api/tags":
            return {"models": [{"name": "custom-vl:latest"}]}
        if endpoint == "/api/show":
            return {}
        if endpoint == "/api/generate":
            raise optimizer.PromptOptimizerError("Ollama request failed: image input is not supported")
        raise AssertionError(endpoint)

    monkeypatch.setattr(optimizer, "_ollama_request_json", fake_request)
    monkeypatch.setattr(
        optimizer,
        "configured_ollama_settings",
        lambda base_dir=None: optimizer.normalize_ollama_settings({"model": "custom-vl:latest"}),
    )
    image = Image.new("RGB", (8, 8), color=(10, 20, 30))

    with pytest.raises(optimizer.PromptOptimizerError, match="rejected image input"):
        optimizer._generate_ollama(
            optimizer.resolve_model(optimizer.OLLAMA_LOCAL_ALIAS),
            [("Current", image)],
            "System optimizer instructions.",
            "Write the motion prompt.",
        )


def test_optimize_segments_ollama_uses_timeline_images(monkeypatch):
    monkeypatch.setattr(optimizer, "ensure_model_downloaded", lambda spec, status_cb=None: None)
    monkeypatch.setattr(optimizer, "prompt_optimizer_vram_preflight", lambda status_cb=None: {"ok": True})
    monkeypatch.setattr(optimizer, "_unload_ollama_model", lambda settings=None: True)
    calls = []

    def fake_generate(spec, images, system_prompt, user_prompt, status_cb=None, final_request=False):
        calls.append(
            {
                "images": images,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "final_request": final_request,
            }
        )
        return "visual timeline prompt"

    monkeypatch.setattr(optimizer, "_generate_ollama", fake_generate)

    result = optimizer.optimize_segments(
        {
            "model": optimizer.OLLAMA_LOCAL_ALIAS,
            "mode": "sfw",
            "segments": [
                {
                    "id": "section_image",
                    "type": "Image",
                    "selected": True,
                    "direction": "The character turns toward the camera",
                    "image_data": data_url_image(),
                    "start": 0,
                    "length": 24,
                }
            ],
            "references": [],
        }
    )

    assert result["ok"] is True
    assert result["results"][0]["prompt"] == "visual timeline prompt"
    assert calls[0]["final_request"] is True
    assert calls[0]["images"][0][0] == "Current"
    assert calls[0]["images"][0][1].size == (16, 16)
    assert "User direction to preserve: The character turns toward the camera." in calls[0]["system_prompt"]
    assert "User direction: The character turns toward the camera." in calls[0]["user_prompt"]


def test_download_progress_bar_supports_thread_map():
    from tqdm.contrib.concurrent import thread_map

    events = []
    reporter = optimizer.DownloadProgressReporter(lambda *args, **kwargs: events.append((args, kwargs)))
    progress_class = reporter.tqdm_class("Qwen/Qwen3-VL-4B-Instruct")

    result = thread_map(lambda value: value + 1, [1, 2], max_workers=1, tqdm_class=progress_class)

    assert result == [2, 3]
    assert progress_class.get_lock() is not None
    assert events


def test_download_progress_bar_preserves_zero_total_for_snapshot_aggregation():
    events = []
    reporter = optimizer.DownloadProgressReporter(lambda *args, **kwargs: events.append((args, kwargs)))
    progress_class = reporter.tqdm_class("Qwen/Qwen3-VL-4B-Instruct")
    progress = progress_class(total=0, desc="Downloading (incomplete total...)")

    try:
        progress.total += 256
        progress.refresh()
    finally:
        progress.close()

    assert progress.total == 256
    assert events


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


def test_decode_image_resolves_direct_paths_through_approved_media_roots(tmp_path, monkeypatch):
    image_path = tmp_path / "guide.png"
    Image.new("RGB", (32, 16), color=(10, 20, 30)).save(image_path)
    observed = {}

    def approved_path(path, source_type):
        observed.update(path=path, source_type=source_type)
        return image_path

    monkeypatch.setattr(optimizer, "resolve_media_path", approved_path)

    preview = optimizer.decode_image({"id": "path", "type": "Image", "mediaPath": "/synthetic/guide.png"})

    assert preview.size == (32, 16)
    assert observed == {"path": "/synthetic/guide.png", "source_type": "image"}


def test_decode_image_redacts_rejected_media_paths(monkeypatch):
    def reject_path(*_args):
        raise ValueError("Security error: /private/secret.png is outside approved roots")

    monkeypatch.setattr(optimizer, "resolve_media_path", reject_path)

    with pytest.raises(
        optimizer.PromptOptimizerError,
        match="Media path is outside approved roots or unavailable",
    ) as exc_info:
        optimizer.decode_image({"id": "path", "type": "Image", "mediaPath": "/private/secret.png"})

    assert "/private/secret.png" not in str(exc_info.value)


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


def test_optimize_segments_releases_local_model_after_run(monkeypatch):
    alias = "qwen3_vl_4b_fast"
    loaded = {"model": object(), "processor": object(), "torch": None}
    monkeypatch.setattr(optimizer, "ensure_model_downloaded", lambda spec, status_cb=None: Path("/tmp/model"))
    monkeypatch.setattr(optimizer, "prompt_optimizer_vram_preflight", lambda status_cb=None: {"ok": True})
    monkeypatch.setattr(optimizer, "_load_qwen_model", lambda spec, path, status_cb=None: loaded)
    monkeypatch.setattr(optimizer, "_generate_qwen", lambda *args, **kwargs: "optimized prompt")
    unloads = []
    monkeypatch.setattr(
        optimizer,
        "unload_optimizer_model",
        lambda unload_alias=None: unloads.append(unload_alias) or {"ok": True, "unloaded": [unload_alias]},
    )
    optimizer._LOADED_MODELS[alias] = loaded
    try:
        result = optimizer.optimize_segments(
            {
                "model": alias,
                "mode": "sfw",
                "segments": [{"id": "section_001", "type": "Text", "selected": True, "direction": "walk forward"}],
                "references": [],
            }
        )
    finally:
        optimizer._LOADED_MODELS.clear()

    assert result["ok"] is True
    assert result["results"][0]["prompt"] == "optimized prompt"
    assert unloads == [alias]


def test_optimize_segments_unloads_model_when_generation_fails(monkeypatch):
    alias = "qwen3_vl_4b_fast"
    loaded = {"model": object(), "processor": object(), "torch": None}
    monkeypatch.setattr(optimizer, "ensure_model_downloaded", lambda spec, status_cb=None: Path("/tmp/model"))
    monkeypatch.setattr(optimizer, "prompt_optimizer_vram_preflight", lambda status_cb=None: {"ok": True})
    monkeypatch.setattr(optimizer, "_load_qwen_model", lambda spec, path, status_cb=None: loaded)

    def failing_generate(*args, **kwargs):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(optimizer, "_generate_qwen", failing_generate)
    unloads = []
    monkeypatch.setattr(
        optimizer,
        "unload_optimizer_model",
        lambda unload_alias=None: unloads.append(unload_alias) or {"ok": True, "unloaded": [unload_alias]},
    )
    optimizer._LOADED_MODELS[alias] = loaded
    try:
        with pytest.raises(RuntimeError, match="CUDA out of memory"):
            optimizer.optimize_segments(
                {
                    "model": alias,
                    "mode": "sfw",
                    "segments": [{"id": "section_001", "type": "Text", "selected": True, "direction": "walk forward"}],
                    "references": [],
                }
            )
    finally:
        optimizer._LOADED_MODELS.clear()

    assert unloads == [alias]


def test_unload_optimizer_model_closes_closeable_resources():
    closed = []

    class FakeCloseable:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    alias = "gemma4_e4b_uncensored_gguf_q8"
    loaded = {"model": FakeCloseable("model"), "chat_handler": FakeCloseable("chat_handler")}
    optimizer._LOADED_MODELS[alias] = loaded
    try:
        result = optimizer.unload_optimizer_model(alias)
    finally:
        optimizer._LOADED_MODELS.clear()

    assert result["unloaded"] == [alias]
    assert closed == ["model", "chat_handler"]
    assert loaded == {}


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


def test_optimizer_start_route_requires_authorization_before_reading_payload(monkeypatch):
    class RecordingRoutes:
        def __init__(self):
            self.handlers = {}

        def _record(self, method, path):
            def decorator(handler):
                self.handlers[(method, path)] = handler
                return handler

            return decorator

        def get(self, path):
            return self._record("GET", path)

        def post(self, path):
            return self._record("POST", path)

    class UnreadableRequest:
        async def json(self):
            raise AssertionError("unauthorized optimizer request must not read its payload")

    routes = RecordingRoutes()
    monkeypatch.setattr(
        prompt_optimizer_routes,
        "server",
        SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=routes))),
    )
    monkeypatch.setattr(prompt_optimizer_routes, "web", aiohttp_web)
    monkeypatch.setattr(prompt_optimizer_routes, "_ROUTES_REGISTERED", False)
    denied = prompt_optimizer_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(prompt_optimizer_routes, "check_privacy_token", lambda _request: denied)
    assert prompt_optimizer_routes.register_prompt_optimizer_routes() is True

    response = asyncio.run(
        routes.handlers[("POST", f"{prompt_optimizer_routes.ROUTE_PREFIX}/optimize/start")](
            UnreadableRequest()
        )
    )

    assert response is denied


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
