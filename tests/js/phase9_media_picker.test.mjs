import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import {
  ASSET_SOURCE_GENERATED,
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  MODEL_LORA_TARGET_MAIN,
} from "../../web/timeline/schema.js";
import {
  addPickedMediaItem,
  attachPickedGeneratedVideoAsTake,
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

function testGeneratedVideoPickerSidecarCreatesMetadataRichTake() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, "Text", 0);
  const shot = timeline.sequence.shots.find((candidate) => candidate.section_ids.includes(section.item_id));
  const result = attachPickedGeneratedVideoAsTake(
    timeline,
    shot.shot_id,
    pickedItem("/outputs/shot_001_take_001.mp4", "shot_001_take_001.mp4", {
      duration_seconds: 2,
      take_capture: {
        schema_version: 1,
        type: "HELTO_GENERATED_TAKE_CAPTURE",
        media: {
          type: ASSET_TYPE_VIDEO,
          filename: "shot_001_take_001.mp4",
          frame_rate: 24,
          frame_count: 49,
          width: 768,
          height: 432,
        },
        registration: {
          schema_version: 1,
          type: "TAKE_REGISTRATION_ENVELOPE",
          shot_id: "shot_001",
          asset: {
            asset_id: "asset_sidecar_001",
            type: ASSET_TYPE_VIDEO,
            name: "shot_001_take_001.mp4",
            metadata: {
              model_family: "LTX",
              model_version: "2.3",
            },
          },
          take: {
            take_id: "take_sidecar_001",
            seed: 789,
            model_family: "LTX",
            model_version: "2.3",
            resolved_loras: {
              model_family: "LTX",
              model_version: "2.3",
              targets: {
                [MODEL_LORA_TARGET_MAIN]: [
                  { name: "style.safetensors", strength_model: 0.8 },
                ],
              },
            },
          },
        },
        privacy: { privacy_mode: false, redacted_fields: [] },
      },
    }),
  );

  assert.ok(result);
  assert.equal(result.asset.asset_id, "asset_sidecar_001");
  assert.equal(result.asset.source_kind, ASSET_SOURCE_GENERATED);
  assert.equal(result.asset.metadata.shot_id, shot.shot_id);
  assert.equal(result.asset.metadata.frame_count, 49);
  assert.equal(result.asset.metadata.model_family, "LTX");
  assert.equal(result.take.take_id, "take_sidecar_001");
  assert.equal(result.take.seed, 789);
  assert.equal(result.take.asset_id, "asset_sidecar_001");
  assert.equal(result.take.resolved_loras.targets[MODEL_LORA_TARGET_MAIN][0].name, "style.safetensors");
  assert.equal("shot_id" in result.take, false);
}

function testGeneratedVideoPickerFallbackStillCreatesCandidateTake() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, "Text", 0);
  const shot = timeline.sequence.shots.find((candidate) => candidate.section_ids.includes(section.item_id));
  const result = attachPickedGeneratedVideoAsTake(
    timeline,
    shot.shot_id,
    pickedItem("/outputs/plain.mp4", "plain.mp4", { duration_seconds: 3 }),
  );

  assert.ok(result);
  assert.equal(result.asset.source_kind, ASSET_SOURCE_GENERATED);
  assert.equal(result.take.status, "Candidate");
  assert.equal(result.take.asset_id, result.asset.asset_id);
}

function testInspectorNoLongerRendersPathEntryClearControls() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");

  assert.equal(rendererSource.includes("renderMediaControls"), false);
  assert.equal(rendererSource.includes("Attach Generated Video As Take"), true);
  assert.equal(rendererSource.includes("Choose generated video"), true);
  assert.equal(rendererSource.includes("Clear Media"), false);
  assert.equal(rendererSource.includes("htd-media-path"), false);
}

function testMediaPickerPrivacyUsesSingleDirectorMode() {
  const pickerSource = readFileSync(new URL("../../web/timeline/media_picker.js", import.meta.url), "utf8");

  assert.equal(pickerSource.includes("hover-hide"), false);
  assert.equal(pickerSource.includes("privacyMode = false"), true);
  assert.equal(pickerSource.includes("pr-image-browser-dialog.privacy-mode"), true);
  assert.equal(pickerSource.includes("pr-audio-browser-dialog.privacy-mode"), true);
  assert.equal(pickerSource.includes("showMediaPreview(documentRef"), true);
  assert.equal(pickerSource.includes("closeMediaPreview(documentRef)"), true);
  assert.equal(pickerSource.includes("showLargePreview"), false);
  assert.equal(pickerSource.includes("if (!privacyMode || overlay.querySelector(\".pr-image-browser-panel\")?.matches(\":hover\"))"), true);
  assert.equal(pickerSource.includes("selectedItem = { ...item, folder_alias: folderSelect.value };"), true);
  assert.equal(pickerSource.includes("promptInDocument"), false);
  assert.equal(pickerSource.includes("function showFolderManager"), true);
  assert.equal(pickerSource.includes("folder.alias === \"input\""), true);
  assert.equal(pickerSource.includes("folder-alias"), false);
  assert.equal(pickerSource.includes("ADD FOLDER PATH"), true);
  assert.equal(pickerSource.includes("ACTIVE FOLDERS"), true);
  assert.equal(pickerSource.includes("body: JSON.stringify({ path })"), true);
  assert.equal(pickerSource.includes("folderDisplayName(folder)"), true);
  assert.equal(pickerSource.includes("pr-folder-item-path"), true);
  assert.equal(pickerSource.includes("folderCountLabel"), false);
  assert.equal(pickerSource.includes("<strong title="), false);
  assert.equal((pickerSource.match(/showFolderManager\(/g) ?? []).length, 3);
  assert.equal((pickerSource.match(/folder-manage/g) ?? []).length >= 4, true);
}

testImagePickerSelectionCreatesSectionAndAsset();
testVideoPickerSelectionCreatesSectionAndAsset();
testAudioPickerSelectionCreatesClipAndAsset();
testCancelOrEmptySelectionDoesNotCreateBlankSection();
testReplaceModePreservesTiming();
testGeneratedVideoPickerSidecarCreatesMetadataRichTake();
testGeneratedVideoPickerFallbackStillCreatesCandidateTake();
testInspectorNoLongerRendersPathEntryClearControls();
testMediaPickerPrivacyUsesSingleDirectorMode();

console.log("phase9 media picker tests passed");
