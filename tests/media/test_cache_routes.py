import asyncio
import json
import math
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import folder_paths
import pytest
from PIL import Image

from routes import global_settings as global_settings_routes
from routes import media_cache as media_cache_routes
from shared import media_browser
from shared import atomic_write as atomic_write_module
from shared import media_cache as media_cache_module
from shared.media_cache import (
    MAX_WAVEFORM_PEAKS,
    MEDIA_PATH_SECURITY_ERROR,
    MIN_WAVEFORM_PEAKS,
    THUMBNAIL_CACHE_PURPOSE,
    WAVEFORM_CACHE_PURPOSE,
    cache_root,
    clear_public_media_cache,
    effective_media_privacy_mode,
    make_thumbnail,
    make_waveform,
    resolve_media_path,
)
from shared.privacy import CRYPTO_AVAILABLE, decrypt_bytes
import shared.timeline.global_settings as timeline_global_settings


@pytest.fixture(autouse=True)
def isolated_global_settings(tmp_path, monkeypatch):
    config_dir = tmp_path / "timeline_global_config"
    monkeypatch.setattr(timeline_global_settings, "CONFIG_DIR", config_dir)
    timeline_global_settings.save_global_settings({"privacy": {"mode": False}})


class _FakeRequest:
    def __init__(self, query=None, payload=None):
        self.rel_url = SimpleNamespace(query=query or {})
        self._payload = payload
        self.headers = {}
        self.cookies = {}

    async def json(self):
        return self._payload


class _RecordingRoutes:
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


def _register_media_cache_test_routes(monkeypatch):
    routes = _RecordingRoutes()
    server = SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=routes)))
    monkeypatch.setitem(sys.modules, "server", server)
    monkeypatch.setattr(media_cache_routes, "_ROUTES_REGISTERED", False)
    assert media_cache_routes.register_media_cache_routes() is True
    return routes


def test_media_request_privacy_can_upgrade_but_not_downgrade_global_setting():
    assert effective_media_privacy_mode(False) is False
    assert effective_media_privacy_mode(True) is True
    assert media_cache_routes.requested_privacy_mode(None) is False

    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})

    assert effective_media_privacy_mode(False) is True
    assert media_cache_routes.requested_privacy_mode(None) is True
    assert media_cache_routes.requested_privacy_mode("false") is True


@pytest.mark.parametrize("privacy_value", [None, "false"])
def test_thumbnail_route_uses_global_privacy_when_request_omits_or_disables_it(
    privacy_value,
    monkeypatch,
):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    routes = _register_media_cache_test_routes(monkeypatch)
    checked_requests = []
    preview_kwargs = {}

    monkeypatch.setattr(
        media_cache_routes,
        "check_privacy_token",
        lambda request: checked_requests.append(request) or None,
    )
    monkeypatch.setattr(media_cache_routes, "resolve_media_path", lambda *_args: Path("/synthetic/private.png"))

    async def fake_preview_job(_fn, *_args, **kwargs):
        preview_kwargs.update(kwargs)
        return b"RIFFsyntheticWEBP"

    monkeypatch.setattr(media_cache_routes, "_run_preview_job", fake_preview_job)
    query = {"path": "ignored.png"}
    if privacy_value is not None:
        query["privacy"] = privacy_value
    request = _FakeRequest(query=query)

    response = asyncio.run(
        routes.handlers[("GET", f"{media_cache_routes.ROUTE_PREFIX}/thumbnail")](request)
    )

    assert response.status == 200
    assert response.body == b"RIFFsyntheticWEBP"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert preview_kwargs["privacy_mode"] is True
    assert checked_requests == [request]


def test_waveform_route_uses_global_privacy_when_request_disables_it(monkeypatch):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    routes = _register_media_cache_test_routes(monkeypatch)
    checked_requests = []
    preview_kwargs = {}

    monkeypatch.setattr(
        media_cache_routes,
        "check_privacy_token",
        lambda request: checked_requests.append(request) or None,
    )
    monkeypatch.setattr(media_cache_routes, "resolve_media_path", lambda *_args: Path("/synthetic/private.wav"))

    async def fake_preview_job(_fn, *_args, **kwargs):
        preview_kwargs.update(kwargs)
        return {"sample_rate": 8_000, "peaks": [0.5], "cache_key": "private"}

    monkeypatch.setattr(media_cache_routes, "_run_preview_job", fake_preview_job)
    request = _FakeRequest(query={"path": "ignored.wav", "privacy": "false"})

    response = asyncio.run(
        routes.handlers[("GET", f"{media_cache_routes.ROUTE_PREFIX}/waveform")](request)
    )

    assert response.status == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert preview_kwargs["privacy_mode"] is True
    assert checked_requests == [request]


def test_private_media_errors_do_not_expose_sensitive_paths():
    response = media_cache_routes._media_error_response(
        ValueError("Media file not found: /private/project/secret.mp4"),
        True,
    )

    payload = json.loads(response.body)
    assert response.status == 400
    assert payload == {"error": "PRIVATE_MEDIA_REQUEST_FAILED: Private media request failed."}
    assert "/private/project" not in response.text


def test_disabling_global_privacy_requires_authorization(monkeypatch):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    denied = media_cache_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(global_settings_routes, "check_privacy_token", lambda _request: denied)

    response = global_settings_routes.apply_global_settings_patch(
        _FakeRequest(),
        {"privacy": {"mode": False}},
    )

    assert response is denied
    assert response.status == 401
    assert timeline_global_settings.global_privacy_mode() is True


def test_authorized_global_privacy_disable_is_saved(monkeypatch):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    monkeypatch.setattr(global_settings_routes, "check_privacy_token", lambda _request: None)

    response = global_settings_routes.apply_global_settings_patch(
        _FakeRequest(),
        {"privacy": {"mode": False}},
    )

    assert response is None
    assert timeline_global_settings.global_privacy_mode() is False


def test_changing_global_asset_root_requires_authorization(tmp_path, monkeypatch):
    original_root = tmp_path / "original"
    timeline_global_settings.save_global_settings(
        {"storage": {"asset_root_directory": str(original_root)}, "privacy": {"mode": False}}
    )
    denied = media_cache_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(global_settings_routes, "check_privacy_token", lambda _request: denied)

    response = global_settings_routes.apply_global_settings_patch(
        _FakeRequest(),
        {"storage": {"asset_root_directory": str(tmp_path / "replacement")}},
    )

    assert response is denied
    assert timeline_global_settings.load_global_settings()["storage"]["asset_root_directory"] == str(original_root)


def test_unchanged_global_asset_root_does_not_require_authorization(tmp_path, monkeypatch):
    original_root = tmp_path / "original"
    timeline_global_settings.save_global_settings(
        {"storage": {"asset_root_directory": str(original_root)}, "privacy": {"mode": False}}
    )

    def unexpected_authorization(_request):
        raise AssertionError("unchanged root must not require authorization")

    monkeypatch.setattr(global_settings_routes, "check_privacy_token", unexpected_authorization)

    response = global_settings_routes.apply_global_settings_patch(
        _FakeRequest(),
        {
            "storage": {"asset_root_directory": str(original_root)},
            "display": {"show_thumbnails": False},
        },
    )

    assert response is None
    assert timeline_global_settings.load_global_settings()["display"]["show_thumbnails"] is False


def test_enabling_global_privacy_purges_only_plaintext_preview_caches(tmp_path):
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        root = cache_root()
        public_files = [
            root / "thumbnails" / "public.webp",
            root / "thumbnails" / "interrupted.webp.tmp",
            root / "thumbnails" / ".interrupted.webp.unique.tmp",
            root / "waveforms" / "public.json",
            root / "waveforms" / "interrupted.json.tmp",
            root / "waveforms" / ".interrupted.json.unique.tmp",
        ]
        encrypted_files = [
            root / "thumbnails" / "private.webp.enc",
            root / "thumbnails" / ".private.webp.enc.unique.tmp",
            root / "waveforms" / "private.json.enc",
            root / "waveforms" / ".private.json.enc.unique.tmp",
        ]
        for path in [*public_files, *encrypted_files]:
            path.write_text("cache", encoding="utf-8")

        response = global_settings_routes.apply_global_settings_patch(
            _FakeRequest(),
            {"privacy": {"mode": True}},
        )

        assert response is None
        assert timeline_global_settings.global_privacy_mode() is True
        assert all(not path.exists() for path in public_files)
        assert all(path.exists() for path in encrypted_files)
    finally:
        folder_paths.set_temp_directory(original_temp)


def test_enabling_global_privacy_aborts_when_plaintext_cache_purge_fails(monkeypatch):
    def fail_purge():
        raise OSError("/private/cache/path")

    monkeypatch.setattr(global_settings_routes, "clear_public_media_cache", fail_purge)

    with pytest.raises(
        timeline_global_settings.GlobalSettingsError,
        match="PRIVACY_CACHE_PURGE_FAILED",
    ) as exc_info:
        global_settings_routes.apply_global_settings_patch(
            _FakeRequest(),
            {"privacy": {"mode": True}},
        )

    assert "/private/cache/path" not in str(exc_info.value)
    assert timeline_global_settings.global_privacy_mode() is False


def test_clear_public_media_cache_preserves_encrypted_cache_files(tmp_path):
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        root = cache_root()
        public_thumbnail = root / "thumbnails" / "public.webp"
        encrypted_thumbnail = root / "thumbnails" / "private.webp.enc"
        interrupted_public = root / "thumbnails" / ".public.webp.unique.tmp"
        interrupted_encrypted = root / "thumbnails" / ".private.webp.enc.unique.tmp"
        public_thumbnail.write_bytes(b"public")
        encrypted_thumbnail.write_bytes(b"encrypted")
        interrupted_public.write_bytes(b"public")
        interrupted_encrypted.write_bytes(b"encrypted")

        clear_public_media_cache()

        assert not public_thumbnail.exists()
        assert not interrupted_public.exists()
        assert encrypted_thumbnail.exists()
        assert interrupted_encrypted.exists()
    finally:
        folder_paths.set_temp_directory(original_temp)


@pytest.mark.parametrize("privacy_mode", [False, True])
def test_simultaneous_identical_thumbnail_writes_are_atomic(
    tmp_path,
    monkeypatch,
    privacy_mode,
    unlocked_privacy_keystore,
):
    if privacy_mode and not CRYPTO_AVAILABLE:
        pytest.skip("cryptography package is required for encrypted preview tests")
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        image_path = tmp_path / "reference.png"
        Image.new("RGB", (64, 32), color=(32, 96, 160)).save(image_path)
        barrier = threading.Barrier(2)

        def synchronized_thumbnail(*_args):
            barrier.wait(timeout=5)
            return Image.new("RGB", (32, 16), color=(32, 96, 160))

        monkeypatch.setattr(media_cache_module, "_load_image_thumbnail", synchronized_thumbnail)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(make_thumbnail, image_path, 128, privacy_mode)
                for _index in range(2)
            ]
            results = [future.result(timeout=5) for future in futures]

        if privacy_mode:
            assert all(isinstance(result, bytes) and result.startswith(b"RIFF") for result in results)
            cache_directory = cache_root() / "thumbnails"
            cache_files = list(cache_directory.glob("*.webp.enc"))
            assert len(cache_files) == 1
            encrypted = json.loads(cache_files[0].read_text(encoding="utf-8"))
            assert decrypt_bytes(encrypted, THUMBNAIL_CACHE_PURPOSE).startswith(b"RIFF")
        else:
            assert results[0] == results[1]
            assert results[0].is_file()
            cache_directory = cache_root() / "thumbnails"
            assert len(list(cache_directory.glob("*.webp"))) == 1
            with Image.open(results[0]) as cached_image:
                assert cached_image.size == (32, 16)
        assert not [path for path in cache_directory.iterdir() if path.name.endswith(".tmp")]
    finally:
        folder_paths.set_temp_directory(original_temp)


@pytest.mark.parametrize("privacy_mode", [False, True])
def test_simultaneous_identical_waveform_writes_are_atomic(
    tmp_path,
    monkeypatch,
    privacy_mode,
    unlocked_privacy_keystore,
):
    if privacy_mode and not CRYPTO_AVAILABLE:
        pytest.skip("cryptography package is required for encrypted preview tests")
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        audio_path = tmp_path / "tone.wav"
        audio_path.write_bytes(b"synthetic audio")
        barrier = threading.Barrier(2)

        def synchronized_waveform(*_args):
            barrier.wait(timeout=5)
            return {
                "duration_seconds": 1.0,
                "sample_rate": 8_000,
                "channels": 1,
                "peaks": [0.5] * 32,
            }

        monkeypatch.setattr(media_cache_module, "_decode_audio_waveform", synchronized_waveform)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(make_waveform, audio_path, 32, privacy_mode)
                for _index in range(2)
            ]
            results = [future.result(timeout=5) for future in futures]

        assert results[0] == results[1]
        cache_directory = cache_root() / "waveforms"
        pattern = "*.json.enc" if privacy_mode else "*.json"
        cache_files = list(cache_directory.glob(pattern))
        assert len(cache_files) == 1
        if privacy_mode:
            encrypted = json.loads(cache_files[0].read_text(encoding="utf-8"))
            decoded = decrypt_bytes(encrypted, WAVEFORM_CACHE_PURPOSE)
            assert json.loads(decoded.decode("utf-8")) == results[0]
        else:
            assert json.loads(cache_files[0].read_text(encoding="utf-8")) == results[0]
        assert not [path for path in cache_directory.iterdir() if path.name.endswith(".tmp")]
    finally:
        folder_paths.set_temp_directory(original_temp)


def test_atomic_write_removes_unique_temp_files_after_failures(tmp_path, monkeypatch):
    target = tmp_path / "cache" / "result.json"

    def failed_writer(temp_path):
        temp_path.write_bytes(b"partial")
        raise RuntimeError("writer failed")

    with pytest.raises(RuntimeError, match="writer failed"):
        media_cache_module._atomic_write(target, failed_writer)
    assert not target.exists()
    assert not list(target.parent.iterdir())

    def failed_replace(*_args):
        raise OSError("replace failed")

    with monkeypatch.context() as patch_context:
        patch_context.setattr(atomic_write_module.os, "replace", failed_replace)
        with pytest.raises(OSError, match="replace failed"):
            media_cache_module._atomic_write(target, lambda temp_path: temp_path.write_bytes(b"complete"))
    assert not target.exists()
    assert not list(target.parent.iterdir())


def test_preview_route_jobs_are_awaited_and_concurrency_limited(monkeypatch):
    async def run_jobs():
        monkeypatch.setattr(media_cache_routes, "_PREVIEW_JOB_SEMAPHORE", asyncio.Semaphore(2))
        active = 0
        max_active = 0

        async def fake_to_thread(fn, *args, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return fn(*args, **kwargs)

        monkeypatch.setattr(media_cache_routes.asyncio, "to_thread", fake_to_thread)

        def preview_job(value):
            return value * 2

        results = await asyncio.gather(
            *(media_cache_routes._run_preview_job(preview_job, index) for index in range(6))
        )
        return results, max_active

    results, max_active = asyncio.run(run_jobs())

    assert results == [0, 2, 4, 6, 8, 10]
    assert max_active <= media_cache_routes.PREVIEW_JOB_CONCURRENCY


def test_thumbnail_cache_writes_webp_under_comfy_temp(tmp_path):
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        image_path = tmp_path / "reference.png"
        Image.new("RGB", (640, 320), color=(32, 96, 160)).save(image_path)

        thumbnail = make_thumbnail(image_path, max_size=128)

        assert thumbnail.suffix == ".webp"
        assert thumbnail.is_file()
        assert cache_root() in thumbnail.parents
        assert cache_root().name == "helto_timeline_director"
    finally:
        folder_paths.set_temp_directory(original_temp)


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for encrypted preview tests")
def test_private_thumbnail_cache_writes_only_encrypted_webp(tmp_path, unlocked_privacy_keystore):
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        image_path = tmp_path / "reference.png"
        Image.new("RGB", (640, 320), color=(32, 96, 160)).save(image_path)

        thumbnail = make_thumbnail(image_path, max_size=128, privacy_mode=True)
        encrypted_files = list((cache_root() / "thumbnails").glob("*.webp.enc"))

        assert isinstance(thumbnail, bytes)
        assert thumbnail.startswith(b"RIFF")
        assert encrypted_files
        assert list((cache_root() / "thumbnails").glob("*.webp")) == []
        encrypted_text = encrypted_files[0].read_text(encoding="utf-8")
        assert "RIFF" not in encrypted_text
        assert "WEBP" not in encrypted_text
        assert "reference.png" not in encrypted_text
        assert decrypt_bytes(json.loads(encrypted_text), THUMBNAIL_CACHE_PURPOSE).startswith(b"RIFF")
    finally:
        folder_paths.set_temp_directory(original_temp)


def test_waveform_cache_writes_peak_json_under_comfy_temp(tmp_path):
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        audio_path = tmp_path / "tone.wav"
        write_test_wav(audio_path)

        waveform = make_waveform(audio_path, peaks=32)

        assert waveform["sample_rate"] == 8000
        assert len(waveform["peaks"]) == 32
        assert all(0.0 <= value <= 1.0 for value in waveform["peaks"])
        assert any(value > 0.0 for value in waveform["peaks"])
    finally:
        folder_paths.set_temp_directory(original_temp)


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for encrypted preview tests")
def test_private_waveform_cache_writes_only_encrypted_json(tmp_path, unlocked_privacy_keystore):
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        audio_path = tmp_path / "tone.wav"
        write_test_wav(audio_path)

        waveform = make_waveform(audio_path, peaks=32, privacy_mode=True)
        encrypted_files = list((cache_root() / "waveforms").glob("*.json.enc"))

        assert len(waveform["peaks"]) == 32
        assert encrypted_files
        assert list((cache_root() / "waveforms").glob("*.json")) == []
        encrypted_text = encrypted_files[0].read_text(encoding="utf-8")
        assert "peaks" not in encrypted_text
        decrypted = json.loads(decrypt_bytes(json.loads(encrypted_text), WAVEFORM_CACHE_PURPOSE).decode("utf-8"))
        assert decrypted["peaks"] == waveform["peaks"]
    finally:
        folder_paths.set_temp_directory(original_temp)


def test_waveform_peak_count_is_clamped_and_cache_keyed(tmp_path):
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        audio_path = tmp_path / "tone.wav"
        write_test_wav(audio_path)

        tiny = make_waveform(audio_path, peaks=1)
        huge = make_waveform(audio_path, peaks=9999)
        mid = make_waveform(audio_path, peaks=64)

        assert len(tiny["peaks"]) == MIN_WAVEFORM_PEAKS
        assert len(huge["peaks"]) == MAX_WAVEFORM_PEAKS
        assert len(mid["peaks"]) == 64
        assert tiny["cache_key"] != huge["cache_key"]
        assert tiny["cache_key"] != mid["cache_key"]
        assert all(0.0 <= value <= 1.0 for value in huge["peaks"])
        assert any(value > 0.0 for value in huge["peaks"])
    finally:
        folder_paths.set_temp_directory(original_temp)


def test_resolve_media_path_supports_comfy_input_relative_paths(tmp_path):
    original_input = folder_paths.get_input_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    try:
        media_path = tmp_path / "input" / "clip.wav"
        media_path.parent.mkdir(parents=True)
        media_path.write_bytes(b"data")

        resolved = resolve_media_path("clip.wav", "input")

        assert resolved == media_path.resolve()
    finally:
        folder_paths.set_input_directory(original_input)


def test_resolve_media_path_supports_absolute_paths_under_comfy_input(tmp_path):
    original_input = folder_paths.get_input_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    try:
        media_path = tmp_path / "input" / "clip.wav"
        media_path.parent.mkdir(parents=True)
        media_path.write_bytes(b"data")

        resolved = resolve_media_path(str(media_path))

        assert resolved == media_path.resolve()
    finally:
        folder_paths.set_input_directory(original_input)


def test_resolve_media_path_rejects_absolute_paths_outside_allowed_roots(tmp_path):
    media_path = tmp_path.parent / f"{tmp_path.name}_outside" / "clip.wav"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"data")

    with pytest.raises(ValueError, match=MEDIA_PATH_SECURITY_ERROR):
        resolve_media_path(str(media_path))


def test_resolve_media_path_supports_configured_director_asset_root(tmp_path):
    media_root = tmp_path.parent / f"{tmp_path.name}_director_assets"
    media_path = media_root / "project" / "takes" / "shot_001" / "take.mp4"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"video")
    timeline_global_settings.save_global_settings(
        {
            "storage": {"asset_root_directory": str(media_root)},
            "privacy": {"mode": False},
        }
    )

    resolved = resolve_media_path(str(media_path))

    assert resolved == media_path.resolve()


def test_resolve_media_path_ignores_invalid_director_asset_root(tmp_path):
    media_root = tmp_path.parent / f"{tmp_path.name}_invalid_director_assets"
    media_path = media_root / "take.mp4"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"video")
    settings_path = timeline_global_settings.settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"storage": {"asset_root_directory": "relative/assets"}, "privacy": {"mode": False}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=MEDIA_PATH_SECURITY_ERROR):
        resolve_media_path(str(media_path))


def test_resolve_media_path_rejects_relative_traversal(tmp_path):
    original_input = folder_paths.get_input_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    try:
        with pytest.raises(ValueError, match=MEDIA_PATH_SECURITY_ERROR):
            resolve_media_path("../clip.wav", "input")
    finally:
        folder_paths.set_input_directory(original_input)


def test_resolve_media_path_supports_registered_folder_paths(tmp_path):
    original_registry = folder_paths.folder_names_and_paths.copy()
    try:
        media_root = tmp_path / "registered"
        media_path = media_root / "clip.wav"
        media_root.mkdir()
        media_path.write_bytes(b"data")
        folder_paths.add_model_folder_path("helto_test_media", str(media_root))

        resolved = resolve_media_path(str(media_path))

        assert resolved == media_path.resolve()
    finally:
        folder_paths.folder_names_and_paths = original_registry


def test_resolve_media_path_supports_enabled_media_browser_folder_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    media_root = tmp_path.parent / f"{tmp_path.name}_browser_media"
    media_path = media_root / "image.png"
    media_root.mkdir()
    Image.new("RGB", (16, 16), color=(32, 96, 160)).save(media_path)
    media_browser.add_folder("image", "custom", str(media_root))

    resolved = resolve_media_path(str(media_path))

    assert resolved == media_path.resolve()


def test_private_media_view_requires_authorization_before_path_resolution(monkeypatch):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    routes = _register_media_cache_test_routes(monkeypatch)
    denied = media_cache_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(media_cache_routes, "check_privacy_token", lambda _request: denied)

    def unexpected_resolution(*_args):
        raise AssertionError("unauthorized view must not resolve a path")

    monkeypatch.setattr(media_cache_routes, "resolve_media_path", unexpected_resolution)
    request = _FakeRequest(query={"path": "/synthetic/private.png", "privacy": "false"})

    response = asyncio.run(
        routes.handlers[("GET", f"{media_cache_routes.ROUTE_PREFIX}/view")](request)
    )

    assert response is denied


def test_authorized_private_media_view_disables_browser_cache(monkeypatch):
    timeline_global_settings.save_global_settings({"privacy": {"mode": True}})
    routes = _register_media_cache_test_routes(monkeypatch)
    monkeypatch.setattr(media_cache_routes, "check_privacy_token", lambda _request: None)
    monkeypatch.setattr(
        media_cache_routes,
        "resolve_media_path",
        lambda *_args: Path("/synthetic/private.png"),
    )

    response = asyncio.run(
        routes.handlers[("GET", f"{media_cache_routes.ROUTE_PREFIX}/view")](
            _FakeRequest(query={"path": "ignored.png"})
        )
    )

    assert response.status == 200
    assert response.headers["Cache-Control"] == "private, no-store"


def test_public_media_view_preserves_short_browser_cache(monkeypatch):
    routes = _register_media_cache_test_routes(monkeypatch)

    def unexpected_authorization(_request):
        raise AssertionError("public view must not require privacy authorization")

    monkeypatch.setattr(media_cache_routes, "check_privacy_token", unexpected_authorization)
    monkeypatch.setattr(
        media_cache_routes,
        "resolve_media_path",
        lambda *_args: Path("/synthetic/public.png"),
    )

    response = asyncio.run(
        routes.handlers[("GET", f"{media_cache_routes.ROUTE_PREFIX}/view")](
            _FakeRequest(query={"path": "ignored.png"})
        )
    )

    assert response.status == 200
    assert response.headers["Cache-Control"] == "private, max-age=300"


def test_media_cache_clear_requires_authorization(monkeypatch):
    routes = _register_media_cache_test_routes(monkeypatch)
    denied = media_cache_routes.web.json_response(
        {"ok": False, "error": "PRIVACY_TOKEN_REQUIRED"},
        status=401,
    )
    monkeypatch.setattr(media_cache_routes, "check_privacy_token", lambda _request: denied)

    def unexpected_clear():
        raise AssertionError("unauthorized request must not clear caches")

    monkeypatch.setattr(media_cache_routes, "clear_media_cache", unexpected_clear)

    response = asyncio.run(
        routes.handlers[("POST", f"{media_cache_routes.ROUTE_PREFIX}/cache/clear")](
            _FakeRequest()
        )
    )

    assert response is denied


def write_test_wav(path):
    sample_rate = 8000
    duration_seconds = 0.25
    sample_count = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(sample_count):
            value = int(math.sin(index / sample_rate * math.tau * 440.0) * 16000)
            frames.extend(value.to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(frames))
