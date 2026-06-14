import math
import wave

import av
import folder_paths
import pytest
from PIL import Image

from shared import media_browser
from shared.privacy import CRYPTO_AVAILABLE


def test_media_browser_folder_config_defaults_adds_removes_and_rejects_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    try:
        (tmp_path / "input").mkdir()
        custom = tmp_path / "custom"
        custom.mkdir()

        folders = media_browser.load_folders("image")
        assert folders[0].alias == "input"
        assert folders[0].path == str(tmp_path / "input")

        media_browser.add_folder("image", "custom", str(custom))
        assert [folder.alias for folder in media_browser.load_folders("image")] == ["input", "custom"]

        media_browser.remove_folder("image", "custom")
        assert [folder.alias for folder in media_browser.load_folders("image")] == ["input"]

        with pytest.raises(ValueError):
            media_browser.add_folder("image", "../bad", str(custom))
    finally:
        folder_paths.set_input_directory(original_input)


def test_media_browser_lists_only_matching_extensions(tmp_path):
    Image.new("RGB", (16, 16), color=(10, 20, 30)).save(tmp_path / "image.png")
    (tmp_path / "movie.mp4").write_bytes(b"not a real movie")
    write_test_wav(tmp_path / "tone.wav")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    assert [item["filename"] for item in media_browser.list_media("image", tmp_path)] == ["image.png"]
    assert [item["filename"] for item in media_browser.list_media("video", tmp_path)] == ["movie.mp4"]
    assert [item["filename"] for item in media_browser.list_media("audio", tmp_path)] == ["tone.wav"]


def test_media_browser_rejects_wrong_extension_and_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    try:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        Image.new("RGB", (16, 16), color=(10, 20, 30)).save(input_dir / "image.png")

        with pytest.raises(ValueError):
            media_browser.resolve_browser_media_path("video", "input", "image.png")
        with pytest.raises(ValueError):
            media_browser.resolve_browser_media_path("image", "input", "../image.png")
    finally:
        folder_paths.set_input_directory(original_input)


def test_image_and_video_browser_thumbnails_return_cached_webp(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        Image.new("RGB", (64, 32), color=(10, 20, 30)).save(input_dir / "image.png")
        write_test_video(input_dir / "clip.mp4")

        image_thumb = media_browser.make_browser_thumbnail("image", "input", "image.png")
        video_thumb = media_browser.make_browser_thumbnail("video", "input", "clip.mp4")

        assert image_thumb.suffix == ".webp"
        assert video_thumb.suffix == ".webp"
        assert image_thumb.is_file()
        assert video_thumb.is_file()
    finally:
        folder_paths.set_input_directory(original_input)
        folder_paths.set_temp_directory(original_temp)


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="cryptography package is required for encrypted preview tests")
def test_image_browser_thumbnail_privacy_returns_bytes_and_encrypted_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(media_browser, "CONFIG_DIR", tmp_path / "config")
    original_input = folder_paths.get_input_directory()
    original_temp = folder_paths.get_temp_directory()
    folder_paths.set_input_directory(str(tmp_path / "input"))
    folder_paths.set_temp_directory(str(tmp_path / "temp"))
    try:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        Image.new("RGB", (64, 32), color=(10, 20, 30)).save(input_dir / "image.png")

        thumb = media_browser.make_browser_thumbnail("image", "input", "image.png", privacy_mode=True)

        assert isinstance(thumb, bytes)
        assert thumb.startswith(b"RIFF")
        assert list((tmp_path / "temp" / "helto_timeline_director" / "thumbnails").glob("*.webp.enc"))
        assert list((tmp_path / "temp" / "helto_timeline_director" / "thumbnails").glob("*.webp")) == []
    finally:
        folder_paths.set_input_directory(original_input)
        folder_paths.set_temp_directory(original_temp)


def write_test_wav(path):
    sample_rate = 8000
    duration_seconds = 0.1
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


def write_test_video(path):
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
