import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const modulePath = path.join(repoRoot, "shared/timeline/managed_media_artifacts.py");
const source = fs.readFileSync(modulePath, "utf8");

function testArtifactSliceIsNotLiveImportedOrRouteRegistered() {
  const liveFiles = [
    "__init__.py",
    "shared/timeline/__init__.py",
    "routes/__init__.py",
  ];
  for (const relative of liveFiles) {
    const liveSource = fs.readFileSync(path.join(repoRoot, relative), "utf8");
    assert.equal(liveSource.includes("managed_media_artifacts"), false, relative);
  }
  assert.equal(source.includes("PromptServer"), false);
  assert.equal(source.includes("register_media_cache_routes"), false);
  assert.equal(source.includes("/helto_director/media/thumbnail"), false);
  assert.equal(source.includes("/helto_director/media/waveform"), false);
}

function testStaticDeclarationAndLegacyInventoryAreExact() {
  for (const expected of [
    'MEDIA_ARTIFACT_RESOURCE_ID = "timeline-media-cache"',
    'THUMBNAIL_ARTIFACT_ADAPTER_ID = "director-thumbnail-artifact"',
    'WAVEFORM_ARTIFACT_ADAPTER_ID = "director-waveform-artifact"',
    'THUMBNAIL_ARTIFACT_PURPOSE = "timeline-thumbnail-cache"',
    'WAVEFORM_ARTIFACT_PURPOSE = "timeline-waveform-cache"',
    'media_type="image/webp"',
    'media_type="application/json"',
    '"thumbnails/*.webp"',
    '"thumbnails/*.webp.tmp"',
    '"thumbnails/.*.webp.*.tmp"',
    '"thumbnails/*.webp.enc"',
    '"thumbnails/*.webp.enc.tmp"',
    '"thumbnails/.*.webp.enc.*.tmp"',
    '"waveforms/*.json"',
    '"waveforms/*.json.tmp"',
    '"waveforms/.*.json.*.tmp"',
    '"waveforms/*.json.enc"',
    '"waveforms/*.json.enc.tmp"',
    '"waveforms/.*.json.enc.*.tmp"',
  ]) {
    assert.ok(source.includes(expected), expected);
  }
}

function testModeIsNotAManagedCacheKeyOrCompositorInput() {
  const keyStart = source.indexOf("def normalized_media_parameter_key(");
  const keyEnd = source.indexOf("\ndef _require_bytes", keyStart);
  const compositorStart = source.indexOf("class DirectorManagedMediaArtifacts:");
  const compositorEnd = source.indexOf("\ndef normalized_media_parameter_key", compositorStart);
  assert.ok(keyStart >= 0 && keyEnd > keyStart);
  assert.ok(compositorStart >= 0 && compositorEnd > compositorStart);
  assert.equal(source.slice(keyStart, keyEnd).includes("privacy_mode"), false);
  assert.equal(source.slice(compositorStart, compositorEnd).includes("privacy_mode"), false);
  assert.equal(source.includes('waveform["cache_key"]'), false);
  assert.ok(source.includes("authoritative storage mode"));
  assert.ok(source.includes("shared artifact"));
}

testArtifactSliceIsNotLiveImportedOrRouteRegistered();
testStaticDeclarationAndLegacyInventoryAreExact();
testModeIsNotAManagedCacheKeyOrCompositorInput();

console.log("managed media artifact static contract tests passed");
