import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  MAX_WAVEFORM_PEAKS,
  MIN_WAVEFORM_PEAKS,
  TimelineMediaCache,
  clampWaveformPeaks,
} from "../../web/timeline/media_cache.js";

const LEASE_URL = `/helto_privacy/artifacts/hp-lease-${"L".repeat(32)}`;

function managedMedia(calls) {
  return {
    async resolveSource(input) {
      calls.push(["resolve", input]);
      return { id: `hp-ref-${"R".repeat(32)}`, kind: "media-source" };
    },
    async previewSource(source, options) {
      calls.push(["preview", source, options]);
      return { url: LEASE_URL, expiresInSeconds: 30 };
    },
    async viewSource(source) {
      calls.push(["view", source]);
      return { url: LEASE_URL, expiresInSeconds: 30 };
    },
  };
}

function visualAsset() {
  return {
    asset_id: "asset_image",
    type: "Image",
    source_kind: "FilePath",
    path: "/mnt/media/reference image.png",
  };
}

async function testManagedThumbnailAndViewLeasesNeverPutPathsInUrls() {
  const calls = [];
  const cache = new TimelineMediaCache({}, {}, managedMedia(calls));
  const asset = visualAsset();

  assert.equal(cache.requestThumbnail(asset, 256), null);
  assert.equal(await cache.acquireThumbnailUrl(asset, 256), LEASE_URL);
  assert.equal(await cache.acquireViewUrl(asset), LEASE_URL);
  assert.equal(LEASE_URL.includes(asset.path), false);
  assert.deepEqual(calls, [
    ["resolve", { assetType: "Image", path: asset.path, sourceType: "" }],
    ["preview", { id: `hp-ref-${"R".repeat(32)}`, kind: "media-source" }, { maxSize: 256 }],
    ["view", { id: `hp-ref-${"R".repeat(32)}`, kind: "media-source" }],
  ]);
}

async function testWaveformUsesManagedArtifactLease() {
  const calls = [];
  const peaks = Array.from({ length: 64 }, (_, index) => index / 64);
  const fetchCalls = [];
  const cache = new TimelineMediaCache(
    {},
    {},
    managedMedia(calls),
    async (url, options) => {
      fetchCalls.push([url, options]);
      return {
        ok: true,
        async json() {
          return { duration_seconds: 2, sample_rate: 48_000, channels: 2, peaks };
        },
      };
    },
  );
  const asset = {
    asset_id: "asset_audio",
    type: "Audio",
    path: "/mnt/media/voice.wav",
  };

  assert.equal(cache.requestWaveform(asset, 64), null);
  await cache.pendingWaveforms.get("asset_audio:64");
  assert.deepEqual(cache.getWaveform(asset.asset_id, 64)?.peaks, peaks);
  assert.deepEqual(calls.at(-1)?.[2], { peaks: 64 });
  assert.deepEqual(fetchCalls, [[LEASE_URL, { cache: "no-store" }]]);
}

function testWaveformPeakCountClamping() {
  assert.equal(clampWaveformPeaks(1), MIN_WAVEFORM_PEAKS);
  assert.equal(clampWaveformPeaks(9999), MAX_WAVEFORM_PEAKS);
}

function testRefreshDoesNotPreloadAudioWaveforms() {
  const cache = new TimelineMediaCache({}, {}, managedMedia([]));
  let loadCount = 0;
  cache.loadWaveform = () => { loadCount += 1; };

  cache.refresh({
    assets: [{ asset_id: "asset_audio", type: "Audio", path: "/mnt/media/voice.wav" }],
  }, { privacy: { mode: true }, display: { show_thumbnails: true, show_audio_waveforms: true } });

  assert.equal(loadCount, 0);
}

function testLegacyMediaTransportIsAbsent() {
  const source = readFileSync(new URL("../../web/timeline/media_cache.js", import.meta.url), "utf8");
  assert.equal(source.includes("./privacy.js"), false);
  assert.equal(source.includes("/helto_director/media"), false);
  assert.equal(source.includes("URLSearchParams"), false);
}

await testManagedThumbnailAndViewLeasesNeverPutPathsInUrls();
await testWaveformUsesManagedArtifactLease();
testWaveformPeakCountClamping();
testRefreshDoesNotPreloadAudioWaveforms();
testLegacyMediaTransportIsAbsent();

console.log("media cache tests passed");
