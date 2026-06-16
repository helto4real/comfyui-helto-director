import asyncio
import json
import math
import wave
from pathlib import Path

import folder_paths
import pytest
from PIL import Image

from routes import media_cache as media_cache_routes
from shared.media_cache import (
    MAX_WAVEFORM_PEAKS,
    MIN_WAVEFORM_PEAKS,
    THUMBNAIL_CACHE_PURPOSE,
    WAVEFORM_CACHE_PURPOSE,
    cache_root,
    make_thumbnail,
    make_waveform,
    resolve_media_path,
)
from shared.privacy import CRYPTO_AVAILABLE, decrypt_bytes


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
def test_private_thumbnail_cache_writes_only_encrypted_webp(tmp_path):
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
def test_private_waveform_cache_writes_only_encrypted_json(tmp_path):
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


def test_media_view_route_serves_resolved_files_with_private_cache_header():
    source = (Path(__file__).resolve().parents[1] / "routes" / "media_cache.py").read_text(encoding="utf-8")

    assert '@routes.get(f"{ROUTE_PREFIX}/view")' in source
    assert 'path = resolve_media_path(' in source
    assert "web.FileResponse(" in source
    assert '"Cache-Control": "private, max-age=300"' in source
    assert "mimetypes.guess_type(path.name)[0]" in source


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
