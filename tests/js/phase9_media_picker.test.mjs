import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import {
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
} from "../../web/timeline/schema.js";
import {
  addPickedMediaItem,
  replacePickedSectionMedia,
} from "../../web/timeline/media_actions.js";
import { addSection } from "../../web/timeline/operations.js";

function pickedItem(path, filename, extras = {}) {
  return {
    path,
    filename,
    name: filename.split("/").pop(),
    mime_type: extras.mime_type ?? "",
    size: extras.size ?? 100,
    mtime: extras.mtime ?? 1,
    folder_alias: extras.folder_alias ?? "input",
    ...extras,
  };
}

function testImagePickerSelectionCreatesSectionAndAsset() {
  const timeline = createDefaultVideoTimeline();
  const section = addPickedMediaItem(
    timeline,
    ASSET_TYPE_IMAGE,
    pickedItem("/media/ref.png", "ref.png", { width: 640, height: 480 }),
  );

  assert.equal(section.type, ASSET_TYPE_IMAGE);
  assert.equal(timeline.director_track.sections.length, 1);
  assert.equal(timeline.assets.length, 1);
  assert.deepEqual(section.image, { asset_id: timeline.assets[0].asset_id });
  assert.equal(timeline.assets[0].path, "/media/ref.png");
}

function testVideoPickerSelectionCreatesSectionAndAsset() {
  const timeline = createDefaultVideoTimeline();
  const section = addPickedMediaItem(
    timeline,
    ASSET_TYPE_VIDEO,
    pickedItem("/media/clip.mp4", "clip.mp4", { duration_seconds: 4.5 }),
  );

  assert.equal(section.type, ASSET_TYPE_VIDEO);
  assert.equal(timeline.assets[0].type, ASSET_TYPE_VIDEO);
  assert.deepEqual(section.video, { asset_id: timeline.assets[0].asset_id });
}

function testAudioPickerSelectionCreatesClipAndAsset() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 10;
  timeline.ui_state.playhead_time = 2;
  const clip = addPickedMediaItem(
    timeline,
    ASSET_TYPE_AUDIO,
    pickedItem("/media/music.wav", "music.wav", { duration_seconds: 3 }),
  );

  assert.equal(clip.start_time, 2);
  assert.equal(clip.end_time, 5);
  assert.equal(timeline.assets[0].type, ASSET_TYPE_AUDIO);
  assert.deepEqual(clip.audio, { asset_id: timeline.assets[0].asset_id });
}

function testCancelOrEmptySelectionDoesNotCreateBlankSection() {
  const timeline = createDefaultVideoTimeline();

  assert.equal(addPickedMediaItem(timeline, ASSET_TYPE_IMAGE, null), null);
  assert.equal(timeline.director_track.sections.length, 0);
  assert.equal(timeline.assets.length, 0);
}

function testReplaceModePreservesTiming() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, ASSET_TYPE_IMAGE, 0);
  section.end_time = 2.5;

  const replaced = replacePickedSectionMedia(
    timeline,
    section.item_id,
    ASSET_TYPE_IMAGE,
    pickedItem("/media/next.png", "next.png"),
  );

  assert.equal(replaced.item_id, section.item_id);
  assert.equal(section.start_time, 0);
  assert.equal(section.end_time, 2.5);
  assert.deepEqual(section.image, { asset_id: timeline.assets[0].asset_id });
}

function testInspectorNoLongerRendersPathAttachChooseClearControls() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");

  assert.equal(rendererSource.includes("renderMediaControls"), false);
  assert.equal(rendererSource.includes("Attach"), false);
  assert.equal(rendererSource.includes("Choose"), false);
  assert.equal(rendererSource.includes("Clear"), false);
  assert.equal(rendererSource.includes("htd-media-path"), false);
}

testImagePickerSelectionCreatesSectionAndAsset();
testVideoPickerSelectionCreatesSectionAndAsset();
testAudioPickerSelectionCreatesClipAndAsset();
testCancelOrEmptySelectionDoesNotCreateBlankSection();
testReplaceModePreservesTiming();
testInspectorNoLongerRendersPathAttachChooseClearControls();

console.log("phase9 media picker tests passed");
