import assert from "node:assert/strict";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import { addAudioClip, addSection } from "../../web/timeline/operations.js";
import {
  attachMediaAsset,
  createFilePathAsset,
  createWaveformBars,
  mediaLabel,
} from "../../web/timeline/media.js";
import { validateVideoTimeline } from "../../web/timeline/validation.js";

function testMediaAttachmentCreatesAssetReference() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, "Image", 0);
  const asset = createFilePathAsset("Image", "/mnt/media/reference.png");

  attachMediaAsset(timeline, section.item_id, asset);
  const validation = validateVideoTimeline(timeline);

  assert.equal(timeline.assets.length, 1);
  assert.deepEqual(section.image, { asset_id: asset.asset_id });
  assert.equal(mediaLabel(timeline, section.image), "reference.png");
  assert.equal(validation.errors.length, 0);
}

function testAudioAttachmentAndWaveformSeed() {
  const timeline = createDefaultVideoTimeline();
  const clip = addAudioClip(timeline, 0, 1);
  const asset = createFilePathAsset("Audio", "/mnt/media/music.wav");

  attachMediaAsset(timeline, clip.item_id, asset);
  const bars = createWaveformBars(asset.asset_id, 8);
  const validation = validateVideoTimeline(timeline);

  assert.equal(clip.audio.asset_id, asset.asset_id);
  assert.equal(bars.length, 8);
  assert.ok(bars.every((value) => value > 0 && value <= 1));
  assert.equal(validation.errors.length, 0);
}

function testMissingAssetReferenceIsInvalid() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, "Video", 0);
  section.video = { asset_id: "missing_asset" };

  const validation = validateVideoTimeline(timeline);

  assert.equal(validation.is_valid, false);
  assert.equal(validation.errors[0].code, "VIDEO_SECTION_MEDIA_ASSET_NOT_FOUND");
}

function testNewVideoSectionUsesTailGuidanceDefaults() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, "Video", 0);

  assert.equal(section.prompt, "");
  assert.equal(section.video_guidance_range, "Last Frames");
  assert.equal(section.video_guidance_frame_count, 17);
}

function testEmbeddedMediaIsRejected() {
  const timeline = createDefaultVideoTimeline();
  timeline.assets.push({
    asset_id: "asset_001",
    type: "Image",
    source_kind: "FilePath",
    path: "/mnt/media/reference.png",
    thumbnail: "data:image/png;base64,AAAA",
  });

  const validation = validateVideoTimeline(timeline);

  assert.equal(validation.is_valid, false);
  assert.equal(validation.errors[0].code, "ASSET_EMBEDDED_MEDIA_NOT_ALLOWED");
}

testMediaAttachmentCreatesAssetReference();
testAudioAttachmentAndWaveformSeed();
testMissingAssetReferenceIsInvalid();
testNewVideoSectionUsesTailGuidanceDefaults();
testEmbeddedMediaIsRejected();

console.log("phase5 media tests passed");
