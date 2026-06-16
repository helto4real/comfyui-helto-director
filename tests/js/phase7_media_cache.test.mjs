import assert from "node:assert/strict";
import {
  MAX_WAVEFORM_PEAKS,
  MIN_WAVEFORM_PEAKS,
  TimelineMediaCache,
  clampWaveformPeaks,
  mediaViewUrl,
  thumbnailUrl,
  waveformUrl,
} from "../../web/timeline/media_cache.js";

function testThumbnailUrlUsesBackendRoute() {
  const url = thumbnailUrl({
    type: "Image",
    source_kind: "FilePath",
    path: "/mnt/media/reference image.png",
  }, 256);

  assert.ok(url.startsWith("/helto_director/media/thumbnail?"));
  assert.ok(url.includes("max_size=256"));
  assert.ok(url.includes("path=%2Fmnt%2Fmedia%2Freference+image.png"));
  assert.equal(url.includes("privacy=1"), false);

  const privateUrl = thumbnailUrl({
    type: "Image",
    source_kind: "FilePath",
    path: "/mnt/media/reference image.png",
  }, 256, true);

  assert.ok(privateUrl.includes("privacy=1"));
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

  const privateUrl = waveformUrl({
    type: "Audio",
    source_kind: "UploadedFile",
    path: "voice.wav",
  }, 64, true);

  assert.ok(privateUrl.includes("privacy=1"));
}

function testMediaViewUrlUsesBackendViewRoute() {
  const url = mediaViewUrl({
    type: "Video",
    source_kind: "FilePath",
    path: "/mnt/media/source clip.mp4",
  });

  assert.ok(url.startsWith("/helto_director/media/view?"));
  assert.ok(url.includes("path=%2Fmnt%2Fmedia%2Fsource+clip.mp4"));
  assert.ok(url.includes("type="));

  const uploadedUrl = mediaViewUrl({
    type: "Image",
    source_kind: "UploadedFile",
    path: "reference.png",
  });

  assert.ok(uploadedUrl.includes("type=input"));
  assert.equal(mediaViewUrl({ type: "Image" }), "");
}

function testWaveformUrlClampsPeakCount() {
  const asset = {
    type: "Audio",
    source_kind: "FilePath",
    path: "/mnt/media/voice.wav",
  };

  assert.equal(clampWaveformPeaks(1), MIN_WAVEFORM_PEAKS);
  assert.equal(clampWaveformPeaks(9999), MAX_WAVEFORM_PEAKS);
  assert.ok(waveformUrl(asset, 1).includes(`peaks=${MIN_WAVEFORM_PEAKS}`));
  assert.ok(waveformUrl(asset, 9999).includes(`peaks=${MAX_WAVEFORM_PEAKS}`));
}

function testWaveformCacheUsesAssetAndPeakCountKeys() {
  const cache = new TimelineMediaCache({}, {});
  const asset = {
    asset_id: "asset_audio",
    type: "Audio",
    source_kind: "FilePath",
    path: "/mnt/media/voice.wav",
  };
  const loadCalls = [];
  cache.loadWaveform = (requestedAsset, peaks) => loadCalls.push([requestedAsset.asset_id, peaks]);

  assert.equal(cache.requestWaveform(asset, 64), null);
  cache.waveforms.set("asset_audio:64:plain", { peaks: [0.2, 0.8], duration_seconds: 2 });

  assert.deepEqual(cache.requestWaveform(asset, 64).peaks, [0.2, 0.8]);
  assert.equal(cache.requestWaveform(asset, 128), null);
  assert.deepEqual(loadCalls, [["asset_audio", 64], ["asset_audio", 128]]);
}

function testRefreshDoesNotPreloadAudioWaveforms() {
  const cache = new TimelineMediaCache({}, {});
  let loadCount = 0;
  cache.loadWaveform = () => { loadCount += 1; };

  cache.refresh({
    project: {
      privacy: { mode: false },
      display: { show_thumbnails: true, show_audio_waveforms: true },
    },
    assets: [{ asset_id: "asset_audio", type: "Audio", path: "/mnt/media/voice.wav" }],
  });

  assert.equal(loadCount, 0);
}

testThumbnailUrlUsesBackendRoute();
testUploadedFileWaveformUsesInputType();
testMediaViewUrlUsesBackendViewRoute();
testWaveformUrlClampsPeakCount();
testWaveformCacheUsesAssetAndPeakCountKeys();
testRefreshDoesNotPreloadAudioWaveforms();

console.log("phase7 media cache tests passed");
