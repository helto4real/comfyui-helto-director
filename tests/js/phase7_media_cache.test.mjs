import assert from "node:assert/strict";
import { thumbnailUrl, waveformUrl } from "../../web/timeline/media_cache.js";

function testThumbnailUrlUsesBackendRoute() {
  const url = thumbnailUrl({
    type: "Image",
    source_kind: "FilePath",
    path: "/mnt/media/reference image.png",
  }, 256);

  assert.ok(url.startsWith("/helto_director/media/thumbnail?"));
  assert.ok(url.includes("max_size=256"));
  assert.ok(url.includes("path=%2Fmnt%2Fmedia%2Freference+image.png"));
}

function testUploadedFileWaveformUsesInputType() {
  const url = waveformUrl({
    type: "Audio",
    source_kind: "UploadedFile",
    path: "voice.wav",
  }, 64);

  assert.ok(url.startsWith("/helto_director/media/waveform?"));
  assert.ok(url.includes("type=input"));
  assert.ok(url.includes("peaks=64"));
}

testThumbnailUrlUsesBackendRoute();
testUploadedFileWaveformUsesInputType();

console.log("phase7 media cache tests passed");
