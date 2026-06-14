import math
import wave

import folder_paths
from PIL import Image

from shared.media_cache import (
    MAX_WAVEFORM_PEAKS,
    MIN_WAVEFORM_PEAKS,
    cache_root,
    make_thumbnail,
    make_waveform,
    resolve_media_path,
)


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
