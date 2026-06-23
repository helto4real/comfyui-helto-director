import assert from "node:assert/strict";
import {
  ASSET_SOURCE_GENERATED,
  ASSET_TYPE_VIDEO,
  MODEL_LORA_MODEL_LTX_2_3,
  MODEL_LORA_MODEL_WAN_2_2,
  MODEL_LORA_TARGET_HIGH_NOISE,
  MODEL_LORA_TARGET_LOW_NOISE,
  MODEL_LORA_TARGET_MAIN,
  createDefaultVideoTimeline,
} from "../../web/timeline/schema.js";
import { normalizeVideoTimeline } from "../../web/timeline/migration.js";
import {
  acceptTake,
  addAudioClip,
  addSection,
  addTakeMetadata,
  assignSectionToShot,
  attachVideoAssetAsTake,
  autoStackAudioLanes,
  canFitLastDirectorSectionToDuration,
  changeBoundaryMode,
  changeShotType,
  clearProjectModelLoraStack,
  clearShotLoraOverride,
  clearShotLoraTargetStack,
  createOrUpdateBoundaryBetweenShots,
  createShot,
  deleteTake,
  deleteTakesByAssetPath,
  deleteSelectedItem,
  duplicateSelectedSection,
  fitDirectorSectionsEvenlyToDuration,
  fitLastDirectorSectionToDuration,
  findBoundaryBetweenShots,
  findShotForSection,
  getSelectedItemIds,
  hasDirectorSectionOverflow,
  isItemSelected,
  moveAudioClip,
  moveSection,
  moveSelectedItems,
  renameShot,
  resizeAudioClip,
  resizeSection,
  selectItem,
  selectItemRange,
  setClipInstanceFromAsset,
  setProjectModelLoraStack,
  setShotLoraMergeMode,
  setShotLoraTargetStack,
  setTakeStatus,
  splitSelectedSection,
  toggleSelectItem,
} from "../../web/timeline/operations.js";
import { detectDirectorGaps, validateVideoTimeline } from "../../web/timeline/validation.js";

function addValidTextSection(timeline, startTime) {
  const section = addSection(timeline, "Text", startTime, { forceStandalone: true });
  section.prompt = "Text prompt";
  return section;
}

function errorCodes(validation) {
  return validation.errors.map((entry) => entry.code);
}

function warningCodes(validation) {
  return validation.warnings.map((entry) => entry.code);
}

function testNewTextSectionStartsEmpty() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, "Text", 0);

  assert.equal(section.prompt, "");
  assert.equal(validateVideoTimeline(timeline).is_valid, false);
}

function testSectionsCannotOverlapWhenMovedOrResized() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;
  const first = addValidTextSection(timeline, 0);
  const second = addValidTextSection(timeline, 2);

  moveSection(timeline, second.item_id, 0.5);
  assert.equal(second.start_time, first.end_time);

  resizeSection(timeline, first.item_id, "end", 3);
  assert.equal(first.end_time, second.start_time);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testAddAndDuplicateReturnNullWhenNoGapFits() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 1;
  const section = addValidTextSection(timeline, 0);
  timeline.ui_state.selected_item_id = section.item_id;

  assert.equal(addSection(timeline, "Text", 0), null);
  assert.equal(duplicateSelectedSection(timeline), null);
  assert.equal(timeline.director_track.sections.length, 1);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testGapsRemainAllowedAndDetected() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 4;
  addValidTextSection(timeline, 1);

  const gaps = detectDirectorGaps(timeline);
  assert.equal(gaps.length, 2);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testSplitAndDuplicate() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 6;
  const section = addValidTextSection(timeline, 0);
  timeline.ui_state.selected_item_id = section.item_id;

  const split = splitSelectedSection(timeline, 0.5);
  assert.ok(split);
  assert.equal(timeline.director_track.sections.length, 2);
  assert.equal(timeline.director_track.sections[0].end_time, 0.5);

  timeline.ui_state.selected_item_id = split.item_id;
  const duplicate = duplicateSelectedSection(timeline);
  assert.ok(duplicate);
  assert.equal(timeline.director_track.sections.length, 3);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testSelectionHelpersKeepPrimaryInSync() {
  const timeline = createDefaultVideoTimeline();
  const first = addValidTextSection(timeline, 0);
  const second = addValidTextSection(timeline, 1);
  const third = addValidTextSection(timeline, 2);

  selectItem(timeline, first.item_id);
  assert.deepEqual(getSelectedItemIds(timeline), [first.item_id]);
  assert.equal(timeline.ui_state.selected_item_id, first.item_id);

  toggleSelectItem(timeline, second.item_id);
  assert.deepEqual(getSelectedItemIds(timeline), [first.item_id, second.item_id]);
  assert.equal(timeline.ui_state.selected_item_id, second.item_id);
  assert.equal(isItemSelected(timeline, first.item_id), true);

  selectItemRange(timeline, third.item_id);
  assert.deepEqual(getSelectedItemIds(timeline), [second.item_id, third.item_id]);
  assert.equal(timeline.ui_state.selected_item_id, third.item_id);
}

function testMigrationDerivesSelectedItemIdsFromPrimarySelection() {
  const timeline = createDefaultVideoTimeline();
  const section = addValidTextSection(timeline, 0);
  timeline.ui_state.selected_item_id = section.item_id;
  delete timeline.ui_state.selected_item_ids;

  const normalized = normalizeVideoTimeline(timeline);

  assert.deepEqual(normalized.ui_state.selected_item_ids, [section.item_id]);
  assert.equal(normalized.ui_state.selected_item_id, section.item_id);
}

function testSelectionNormalizationPreservesShotBoundaryAndTakeIds() {
  const normalized = normalizeVideoTimeline({
    type: "VIDEO_TIMELINE",
    project: {},
    sequence: {
      shots: [{
        shot_id: "shot_001",
        start_time: 0,
        end_time: 1,
        takes: [{ take_id: "take_001", status: "Candidate" }],
      }, {
        shot_id: "shot_002",
        start_time: 1,
        end_time: 2,
      }],
      boundaries: [{ boundary_id: "boundary_001", left_shot_id: "shot_001", right_shot_id: "shot_002" }],
    },
    ui_state: {
      selected_item_id: "take_001",
      selected_item_ids: ["shot_001", "boundary_001", "take_001"],
    },
  });

  assert.deepEqual(normalized.ui_state.selected_item_ids, ["shot_001", "boundary_001", "take_001"]);
  assert.equal(normalized.ui_state.selected_item_id, "take_001");
}

function testDefaultTimelineHasSequenceAndModelTargetedLoras() {
  const timeline = createDefaultVideoTimeline();

  assert.deepEqual(timeline.sequence, {
    sequence_id: "main",
    name: "Main Timeline",
    shots: [],
    boundaries: [],
  });
  assert.equal(timeline.project.model_loras.schema_version, 2);
  assert.deepEqual(timeline.project.model_loras.global[MODEL_LORA_MODEL_LTX_2_3][MODEL_LORA_TARGET_MAIN].loras, []);
  assert.deepEqual(timeline.project.model_loras.global[MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_HIGH_NOISE].loras, []);
  assert.deepEqual(timeline.project.model_loras.global[MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_LOW_NOISE].loras, []);
}

function testMigrationDropsLegacyLorasAndPreservesSections() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.model_loras = {
    lora_config_hi: { loras: [{ enabled: true, name: "hi.safetensors" }] },
    lora_config_low: { loras: [{ enabled: true, name: "low.safetensors" }] },
  };
  addValidTextSection(timeline, 0);

  const normalized = normalizeVideoTimeline(timeline);

  assert.equal("lora_config_hi" in normalized.project.model_loras, false);
  assert.equal("lora_config_low" in normalized.project.model_loras, false);
  assert.equal(normalized.project.model_loras.schema_version, 2);
  assert.equal(normalized.director_track.sections.length, 1);
  assert.deepEqual(normalized.sequence.shots.map((shot) => shot.section_ids), [[timeline.director_track.sections[0].item_id]]);
  assert.deepEqual(normalized.project.model_loras.global[MODEL_LORA_MODEL_LTX_2_3][MODEL_LORA_TARGET_MAIN].loras, []);
  assert.deepEqual(normalized.project.model_loras.global[MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_HIGH_NOISE].loras, []);
  assert.deepEqual(normalized.project.model_loras.global[MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_LOW_NOISE].loras, []);
}

function testMigrationNormalizesPartialSequenceStructures() {
  const normalized = normalizeVideoTimeline({
    type: "VIDEO_TIMELINE",
    project: {},
    sequence: {
      shots: [{
        shot_id: "shot_custom",
        section_ids: [123, null, "section_b"],
        lora_overrides: { merge_mode: "Nope", targets: [] },
        takes: [{ take_id: "take_custom", status: "Nope" }],
        clip_instance: { asset_id: 42, speed: "bad" },
      }],
      boundaries: [{ boundary_id: "boundary_custom", mode: "Nope" }],
    },
  });
  const shot = normalized.sequence.shots[0];

  assert.equal(normalized.sequence.sequence_id, "main");
  assert.equal(shot.type, "Generated");
  assert.deepEqual(shot.section_ids, ["123", "section_b"]);
  assert.deepEqual(shot.lora_overrides, {
    enabled: false,
    merge_mode: "Inherit Global",
    targets: {},
  });
  assert.equal(shot.takes[0].status, "Candidate");
  assert.equal(shot.clip_instance.asset_id, "42");
  assert.equal(shot.clip_instance.speed, 1);
  assert.equal(normalized.sequence.boundaries[0].mode, "Hard Cut");
}

function testMigrationDerivesShotsFromFlatSections() {
  const normalized = normalizeVideoTimeline({
    type: "VIDEO_TIMELINE",
    project: {},
    assets: [
      { asset_id: "image_001", type: "Image", source_kind: "FilePath", path: "/mnt/media/reference.png" },
      { asset_id: "video_001", type: "Video", source_kind: "FilePath", path: "/mnt/media/source.mp4" },
    ],
    director_track: {
      sections: [
        { item_id: "intro", type: "Text", start_time: 0, end_time: 1, prompt: "intro prompt" },
        { item_id: "image/ref", type: "Image", start_time: 1, end_time: 2, image: { asset_id: "image_001" }, prompt: "image prompt", custom_note: "keep" },
        { item_id: "video ref", type: "Video", start_time: 3, end_time: 4, video: { asset_id: "video_001" }, prompt: "video prompt", source_in: 0.5 },
      ],
    },
    audio_tracks: [{ track_id: "music", clips: [{ item_id: "audio_1", audio: "/mnt/media/music.wav", start_time: 0, end_time: 4 }] }],
  });

  assert.deepEqual(normalized.director_track.sections.map((section) => section.prompt), [
    "intro prompt",
    "image prompt",
    "video prompt",
  ]);
  assert.equal(normalized.director_track.sections[1].custom_note, "keep");
  assert.equal(normalized.audio_tracks[0].clips[0].audio, "/mnt/media/music.wav");
  assert.deepEqual(normalized.sequence.shots.map((shot) => shot.shot_id), [
    "shot_intro",
    "shot_image_ref",
    "shot_video_ref",
  ]);
  assert.deepEqual(normalized.sequence.shots.map((shot) => shot.section_ids), [
    ["intro"],
    ["image/ref"],
    ["video ref"],
  ]);
  assert.deepEqual(normalized.sequence.shots.map((shot) => [shot.start_time, shot.end_time]), [
    [0, 1],
    [1, 2],
    [3, 4],
  ]);
  assert.deepEqual(normalized.sequence.boundaries.map((boundary) => boundary.boundary_id), [
    "boundary_shot_intro_to_shot_image_ref",
  ]);
  assert.equal("image" in normalized.sequence.shots[1], false);
  assert.equal("video" in normalized.sequence.shots[2], false);
  assert.equal(JSON.stringify(normalized.sequence).includes("thumbnail"), false);
  assert.equal(JSON.stringify(normalized.sequence).includes("waveform"), false);
}

function testMigrationIsIdempotentAndUsesDuplicateSuffixes() {
  const timeline = {
    type: "VIDEO_TIMELINE",
    project: {},
    director_track: {
      sections: [
        { item_id: "A/B", type: "Text", start_time: 0, end_time: 1, prompt: "first" },
        { item_id: "A B", type: "Text", start_time: 1.0000005, end_time: 2, prompt: "second" },
        { item_id: "gap", type: "Text", start_time: 2.25, end_time: 3, prompt: "third" },
      ],
    },
  };

  const normalized = normalizeVideoTimeline(timeline);
  const normalizedAgain = normalizeVideoTimeline(normalized);

  assert.deepEqual(normalized.sequence.shots.map((shot) => shot.shot_id), [
    "shot_A_B",
    "shot_A_B_2",
    "shot_gap",
  ]);
  assert.deepEqual(normalized.sequence.boundaries.map((boundary) => boundary.boundary_id), [
    "boundary_shot_A_B_to_shot_A_B_2",
  ]);
  assert.deepEqual(normalizedAgain.sequence, normalized.sequence);
}

function testShotOperationsCreateAssignBoundaryAndDelete() {
  const timeline = createDefaultVideoTimeline();
  const first = addValidTextSection(timeline, 0);
  const second = addValidTextSection(timeline, 1);
  const firstShot = findShotForSection(timeline, first.item_id);
  const secondShot = findShotForSection(timeline, second.item_id);

  assert.ok(firstShot);
  assert.ok(secondShot);
  assert.deepEqual(firstShot.section_ids, [first.item_id]);

  const manual = createShot(timeline, { shot_id: "shot_manual", name: "Manual", start_time: 2, end_time: 3 });
  assert.equal(timeline.ui_state.selected_item_id, "shot_manual");
  assert.equal(changeShotType(timeline, manual.shot_id, "Imported"), true);
  assert.equal(renameShot(timeline, manual.shot_id, "Imported Clip"), true);
  assert.equal(assignSectionToShot(timeline, second.item_id, manual.shot_id), true);

  assert.equal(findShotForSection(timeline, second.item_id).shot_id, manual.shot_id);
  assert.equal(timeline.sequence.shots.some((shot) => shot.shot_id === secondShot.shot_id), false);
  assert.deepEqual(manual.section_ids, [second.item_id]);
  assert.deepEqual([manual.start_time, manual.end_time], [1, 2]);

  const boundary = createOrUpdateBoundaryBetweenShots(timeline, firstShot.shot_id, manual.shot_id, { mode: "Continuous Shot" });
  assert.ok(boundary);
  assert.equal(boundary.mode, "Continuous Shot");
  assert.equal(changeBoundaryMode(timeline, boundary.boundary_id, "Transition"), true);
  assert.equal(findBoundaryBetweenShots(timeline, firstShot.shot_id, manual.shot_id).mode, "Transition");

  selectItem(timeline, manual.shot_id);
  assert.equal(deleteSelectedItem(timeline), true);
  assert.equal(timeline.director_track.sections.some((section) => section.item_id === second.item_id), false);
  assert.equal(timeline.sequence.shots.some((shot) => shot.shot_id === manual.shot_id), false);
  assert.equal(timeline.sequence.boundaries.length, 0);
}

function testAddSectionTargetsSelectedCompatibleShot() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;
  const shot = createShot(timeline, { shot_id: "shot_manual", start_time: 0, end_time: 1 });

  const first = addSection(timeline, "Text");
  first.prompt = "first";
  assert.equal(timeline.sequence.shots.length, 1);
  assert.deepEqual(shot.section_ids, [first.item_id]);
  assert.equal(findShotForSection(timeline, first.item_id).shot_id, "shot_manual");

  const second = addSection(timeline, "Image");
  assert.equal(timeline.sequence.shots.length, 1);
  assert.deepEqual(shot.section_ids, [first.item_id, second.item_id]);
  assert.deepEqual([second.start_time, second.end_time], [1, 2]);
  assert.deepEqual([shot.start_time, shot.end_time], [0, 2]);
}

function testStandaloneSectionCreationStillCreatesWrapperShot() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;
  const shot = createShot(timeline, { shot_id: "shot_manual", start_time: 0, end_time: 1 });
  const standalone = addSection(timeline, "Text", null, { forceStandalone: true });

  assert.equal(timeline.sequence.shots.length, 2);
  assert.equal(findShotForSection(timeline, standalone.item_id).shot_id.startsWith("shot_section_"), true);
  assert.deepEqual(shot.section_ids, []);

  const importedTimeline = createDefaultVideoTimeline();
  createShot(importedTimeline, { shot_id: "shot_imported", type: "Imported", start_time: 0, end_time: 1 });
  const importedSelectedSection = addSection(importedTimeline, "Text");
  assert.equal(importedTimeline.sequence.shots.length, 2);
  assert.notEqual(findShotForSection(importedTimeline, importedSelectedSection.item_id).shot_id, "shot_imported");
}

function testTakeAndClipInstanceOperations() {
  const timeline = createDefaultVideoTimeline();
  const section = addValidTextSection(timeline, 0);
  const shot = findShotForSection(timeline, section.item_id);
  timeline.assets.push({
    asset_id: "asset_video_001",
    type: ASSET_TYPE_VIDEO,
    source_kind: ASSET_SOURCE_GENERATED,
    path: "/tmp/generated.mp4",
    name: "generated.mp4",
  });

  assert.equal(setClipInstanceFromAsset(timeline, shot.shot_id, "asset_video_001", { source_in: 0.5 }), true);
  assert.equal(shot.type, "Imported");
  assert.equal(shot.clip_instance.asset_id, "asset_video_001");
  assert.equal(shot.clip_instance.source_in, 0.5);

  const take = addTakeMetadata(timeline, shot.shot_id, {
    take_id: "take_custom",
    asset_id: "asset_video_001",
    seed: 123,
    resolved_loras: {
      model_family: "LTX",
      model_version: "2.3",
      targets: { [MODEL_LORA_TARGET_MAIN]: [] },
    },
  });
  assert.equal(take.take_id, "take_custom");
  assert.equal(acceptTake(timeline, shot.shot_id, take.take_id), true);
  assert.equal(shot.accepted_take_id, take.take_id);
  assert.equal(take.status, "Accepted");
  assert.equal(setTakeStatus(timeline, shot.shot_id, take.take_id, "Rejected"), true);
  assert.equal(shot.accepted_take_id, null);
  assert.equal(take.status, "Rejected");
}

function testDeleteTakeClearsAcceptedClipAndPrunesOnlyUnreferencedGeneratedAsset() {
  const timeline = createDefaultVideoTimeline();
  const section = addValidTextSection(timeline, 0);
  const shot = findShotForSection(timeline, section.item_id);
  timeline.assets.push({
    asset_id: "asset_generated_001",
    type: ASSET_TYPE_VIDEO,
    source_kind: ASSET_SOURCE_GENERATED,
    path: "/tmp/generated-take.mp4",
    name: "generated-take.mp4",
  }, {
    asset_id: "asset_generated_shared",
    type: ASSET_TYPE_VIDEO,
    source_kind: ASSET_SOURCE_GENERATED,
    path: "/tmp/shared-take.mp4",
    name: "shared-take.mp4",
  });
  const accepted = addTakeMetadata(timeline, shot.shot_id, {
    take_id: "take_delete",
    asset_id: "asset_generated_001",
    status: "Accepted",
  });
  const shared = addTakeMetadata(timeline, shot.shot_id, {
    take_id: "take_shared",
    asset_id: "asset_generated_shared",
  });
  shot.clip_instance = { asset_id: accepted.asset_id, source_in: 0, source_out: null, speed: 1, enabled: true };
  shot.accepted_take_id = accepted.take_id;
  const secondShot = createShot(timeline, { shot_id: "shot_second", start_time: 1, end_time: 2 });
  addTakeMetadata(timeline, secondShot.shot_id, {
    take_id: "take_still_references_shared_asset",
    asset_id: shared.asset_id,
  });

  assert.equal(deleteTake(timeline, shot.shot_id, accepted.take_id), true);
  assert.equal(shot.accepted_take_id, null);
  assert.equal(shot.clip_instance, null);
  assert.equal(shot.takes.some((take) => take.take_id === accepted.take_id), false);
  assert.equal(timeline.assets.some((asset) => asset.asset_id === accepted.asset_id), false);

  assert.equal(deleteTake(timeline, shot.shot_id, shared.take_id), true);
  assert.equal(timeline.assets.some((asset) => asset.asset_id === shared.asset_id), true);
}

function testDeleteTakesByAssetPathPrefersPathBeforeFallbackTakeId() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, "Text", 0);
  const shot = timeline.sequence.shots.find((candidate) => candidate.section_ids.includes(section.item_id));
  timeline.assets.push({
    asset_id: "asset_capture_001",
    type: ASSET_TYPE_VIDEO,
    source_kind: ASSET_SOURCE_GENERATED,
    path: "/tmp/capture_001.mp4",
    name: "capture_001.mp4",
  }, {
    asset_id: "asset_capture_002",
    type: ASSET_TYPE_VIDEO,
    source_kind: ASSET_SOURCE_GENERATED,
    path: "/tmp/capture_002.mp4",
    name: "capture_002.mp4",
  });
  const first = addTakeMetadata(timeline, shot.shot_id, {
    take_id: "take_capture_001",
    asset_id: "asset_capture_001",
    status: "Accepted",
  });
  const second = addTakeMetadata(timeline, shot.shot_id, {
    take_id: "take_capture_002",
    asset_id: "asset_capture_002",
  });
  acceptTake(timeline, shot.shot_id, first.take_id);

  assert.equal(deleteTakesByAssetPath(timeline, shot.shot_id, "/tmp/capture_001.mp4", second.take_id), true);
  assert.equal(shot.takes.some((take) => take.take_id === first.take_id), false);
  assert.equal(shot.takes.some((take) => take.take_id === second.take_id), true);
  assert.equal(shot.accepted_take_id, null);
  assert.equal(shot.clip_instance, null);
  assert.equal(timeline.assets.some((asset) => asset.asset_id === "asset_capture_001"), false);
  assert.equal(timeline.assets.some((asset) => asset.asset_id === "asset_capture_002"), true);
}

function testAttachGeneratedAssetAsTakePreservesGeneratedShotType() {
  const timeline = createDefaultVideoTimeline();
  const section = addValidTextSection(timeline, 0);
  const shot = findShotForSection(timeline, section.item_id);
  timeline.assets.push({
    asset_id: "asset_generated_001",
    type: ASSET_TYPE_VIDEO,
    source_kind: ASSET_SOURCE_GENERATED,
    path: "/tmp/generated-take.mp4",
    name: "generated-take.mp4",
  });

  const take = attachVideoAssetAsTake(timeline, shot.shot_id, "asset_generated_001", {
    take_id: "take_generated",
    seed: 456,
    model_family: "WAN",
    model_version: "2.2",
  });

  assert.equal(take.take_id, "take_generated");
  assert.equal(take.asset_id, "asset_generated_001");
  assert.equal(take.status, "Candidate");
  assert.equal(shot.type, "Generated");
  assert.equal(acceptTake(timeline, shot.shot_id, take.take_id), true);
  assert.equal(shot.type, "Generated");
  assert.equal(shot.accepted_take_id, take.take_id);
  assert.equal(shot.clip_instance.asset_id, "asset_generated_001");

  assert.equal(setTakeStatus(timeline, shot.shot_id, take.take_id, "Rejected"), true);
  assert.equal(shot.accepted_take_id, null);
  assert.equal(shot.clip_instance, null);
  assert.equal(setTakeStatus(timeline, shot.shot_id, take.take_id, "Candidate"), true);
  assert.equal(take.status, "Candidate");
  assert.equal(shot.clip_instance, null);
}

function testProjectAndShotLoraOperations() {
  const timeline = createDefaultVideoTimeline();
  const section = addValidTextSection(timeline, 0);
  const shot = findShotForSection(timeline, section.item_id);
  const stack = {
    version: 1,
    loras: [{ enabled: true, name: "style.safetensors", strength_model: 0.75, strength_clip: 0.5 }],
    ui: { show_strengths: "dual", match: "style" },
  };

  assert.equal(setProjectModelLoraStack(timeline, MODEL_LORA_MODEL_LTX_2_3, MODEL_LORA_TARGET_MAIN, stack), true);
  assert.deepEqual(timeline.project.model_loras.global[MODEL_LORA_MODEL_LTX_2_3][MODEL_LORA_TARGET_MAIN].loras, stack.loras);
  assert.equal(setProjectModelLoraStack(timeline, MODEL_LORA_MODEL_LTX_2_3, MODEL_LORA_TARGET_HIGH_NOISE, stack), false);
  assert.equal(clearProjectModelLoraStack(timeline, MODEL_LORA_MODEL_LTX_2_3, MODEL_LORA_TARGET_MAIN), true);
  assert.deepEqual(timeline.project.model_loras.global[MODEL_LORA_MODEL_LTX_2_3][MODEL_LORA_TARGET_MAIN].loras, []);

  assert.equal(setShotLoraMergeMode(timeline, shot.shot_id, "Replace Global"), true);
  assert.equal(shot.lora_overrides.enabled, true);
  assert.equal(shot.lora_overrides.merge_mode, "Replace Global");
  assert.equal(setShotLoraTargetStack(timeline, shot.shot_id, MODEL_LORA_MODEL_WAN_2_2, MODEL_LORA_TARGET_HIGH_NOISE, stack), true);
  assert.equal(shot.lora_overrides.targets[MODEL_LORA_MODEL_WAN_2_2][MODEL_LORA_TARGET_HIGH_NOISE].loras[0].name, "style.safetensors");
  assert.equal(setShotLoraTargetStack(timeline, shot.shot_id, MODEL_LORA_MODEL_WAN_2_2, MODEL_LORA_TARGET_MAIN, stack), false);
  assert.equal(clearShotLoraTargetStack(timeline, shot.shot_id, MODEL_LORA_MODEL_WAN_2_2, MODEL_LORA_TARGET_HIGH_NOISE), true);
  assert.equal(shot.lora_overrides.merge_mode, "Replace Global");
  assert.equal(shot.lora_overrides.targets[MODEL_LORA_MODEL_WAN_2_2], undefined);
  assert.equal(setShotLoraMergeMode(timeline, shot.shot_id, "Disable LoRAs"), true);
  assert.equal(setShotLoraTargetStack(timeline, shot.shot_id, MODEL_LORA_MODEL_WAN_2_2, MODEL_LORA_TARGET_HIGH_NOISE, stack), true);
  assert.equal(shot.lora_overrides.merge_mode, "Add To Global");
  assert.equal(clearShotLoraOverride(timeline, shot.shot_id), true);
  assert.deepEqual(shot.lora_overrides, {
    enabled: false,
    merge_mode: "Inherit Global",
    targets: {},
  });
}

function testMalformedOrMissingSequenceMigratesFromSections() {
  const timeline = {
    type: "VIDEO_TIMELINE",
    project: {},
    sequence: "not-a-sequence",
    director_track: {
      sections: [{ item_id: "section_001", type: "Text", start_time: 0, end_time: 1, prompt: "text" }],
    },
  };
  const { sequence: _sequence, ...missingSequence } = timeline;

  const normalizedMalformed = normalizeVideoTimeline(timeline);
  const normalizedMissing = normalizeVideoTimeline(missingSequence);

  assert.equal(normalizedMalformed.sequence.shots[0].shot_id, "shot_section_001");
  assert.deepEqual(normalizedMissing.sequence, normalizedMalformed.sequence);
}

function testExistingSequenceShotsArePreserved() {
  const normalized = normalizeVideoTimeline({
    type: "VIDEO_TIMELINE",
    project: {},
    director_track: {
      sections: [{ item_id: "section_001", type: "Text", start_time: 0, end_time: 1, prompt: "text" }],
    },
    sequence: {
      shots: [{ shot_id: "shot_authored", start_time: 10, end_time: 11, section_ids: ["custom_section"] }],
      boundaries: [{ boundary_id: "boundary_authored", left_shot_id: "shot_previous", right_shot_id: "shot_authored", mode: "Hard Cut" }],
    },
  });

  assert.deepEqual(normalized.sequence.shots.map((shot) => shot.shot_id), ["shot_authored"]);
  assert.equal(normalized.sequence.shots[0].start_time, 10);
  assert.deepEqual(normalized.sequence.shots[0].section_ids, ["custom_section"]);
  assert.deepEqual(normalized.sequence.boundaries.map((boundary) => boundary.boundary_id), ["boundary_authored"]);
}

function testValidationReportsGenericShotStructureIssues() {
  const timeline = createDefaultVideoTimeline();
  timeline.sequence.shots = [
    { shot_id: "shot_bad", type: "Generated", start_time: 1, end_time: 1, section_ids: ["missing_section"] },
    { shot_id: "shot_a", type: "Generated", start_time: 0, end_time: 2 },
    {
      shot_id: "shot_b",
      type: "Generated",
      start_time: 1.5,
      end_time: 3,
      takes: [{ take_id: "take_001", status: "Candidate", asset_id: "missing_asset" }],
      accepted_take_id: "stale_take",
    },
  ];
  timeline.sequence.boundaries = [
    { boundary_id: "boundary_001", left_shot_id: "shot_a", right_shot_id: "missing_shot", mode: "Jump Cut" },
  ];

  const codes = errorCodes(validateVideoTimeline(timeline));

  assert.equal(codes.includes("SHOT_INVALID_TIME_RANGE"), true);
  assert.equal(codes.includes("SHOT_OVERLAP"), true);
  assert.equal(codes.includes("SHOT_SECTION_NOT_FOUND"), true);
  assert.equal(codes.includes("BOUNDARY_RIGHT_SHOT_NOT_FOUND"), true);
  assert.equal(codes.includes("BOUNDARY_MODE_INVALID"), true);
  assert.equal(codes.includes("TAKE_ASSET_NOT_FOUND"), true);
  assert.equal(codes.includes("SHOT_ACCEPTED_TAKE_NOT_FOUND"), true);
}

function testValidationChecksGenericLoraStructureAndLegacyRemoval() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.model_loras = {
    lora_config_hi: { loras: [{ enabled: true, name: "hi.safetensors" }] },
    global: {
      [MODEL_LORA_MODEL_LTX_2_3]: {
        [MODEL_LORA_TARGET_HIGH_NOISE]: {
          version: 1,
          loras: [],
          ui: { show_strengths: "single", match: "" },
        },
      },
    },
  };
  timeline.sequence.shots = [
    {
      shot_id: "shot_bad_lora",
      type: "Generated",
      start_time: 0,
      end_time: 1,
      lora_overrides: { enabled: true, merge_mode: "Sideways" },
    },
  ];

  const validation = validateVideoTimeline(timeline);
  const normalized = normalizeVideoTimeline(timeline);
  const codes = errorCodes(validation);

  assert.equal(codes.includes("MODEL_LORA_TARGET_INVALID"), true);
  assert.equal(codes.includes("SHOT_LORA_MERGE_MODE_INVALID"), true);
  assert.equal("lora_config_hi" in normalized.project.model_loras, false);
}

function testValidationChecksTakeResolvedLorasAndBoundaryContinuity() {
  const timeline = createDefaultVideoTimeline();
  timeline.sequence.shots = [
    {
      shot_id: "shot_a",
      type: "Generated",
      start_time: 0,
      end_time: 1,
      takes: [{
        take_id: "take_001",
        status: "Candidate",
        model_family: "LTX",
        model_version: "2.3",
        resolved_loras: {
          model_family: "LTX",
          model_version: "2.3",
          targets: {
            [MODEL_LORA_TARGET_HIGH_NOISE]: [{ name: "wrong.safetensors", thumbnail: "data:image/png;base64,AAAA" }],
          },
        },
      }],
    },
    {
      shot_id: "shot_b",
      type: "Generated",
      start_time: 1,
      end_time: 2,
      lora_overrides: {
        enabled: true,
        merge_mode: "Replace Global",
        targets: {
          [MODEL_LORA_MODEL_LTX_2_3]: {
            [MODEL_LORA_TARGET_MAIN]: {
              version: 1,
              loras: [],
              ui: { show_strengths: "single", match: "different" },
            },
          },
        },
      },
    },
  ];
  timeline.sequence.boundaries = [
    { boundary_id: "boundary_001", left_shot_id: "shot_a", right_shot_id: "shot_b", mode: "Continuous Shot" },
  ];
  const hardCut = JSON.parse(JSON.stringify(timeline));
  hardCut.sequence.boundaries[0].mode = "Hard Cut";

  const validation = validateVideoTimeline(timeline);
  const hardCutValidation = validateVideoTimeline(hardCut);

  assert.equal(errorCodes(validation).includes("TAKE_RESOLVED_LORAS_TARGET_INVALID"), true);
  assert.equal(errorCodes(validation).includes("TAKE_RESOLVED_LORAS_EMBEDDED_MEDIA_NOT_ALLOWED"), true);
  assert.equal(warningCodes(validation).includes("BOUNDARY_LORA_STACK_MISMATCH"), true);
  assert.equal(warningCodes(hardCutValidation).includes("BOUNDARY_LORA_STACK_MISMATCH"), false);
}

function testGroupDeleteRemovesMixedSelection() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;
  const section = addValidTextSection(timeline, 0);
  const keep = addValidTextSection(timeline, 2);
  const clip = addAudioClip(timeline, 0, 1);
  clip.audio = "/tmp/audio.wav";
  selectItem(timeline, section.item_id);
  toggleSelectItem(timeline, clip.item_id);

  assert.equal(getSelectedItemIds(timeline).length, 2);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);

  assert.equal(deleteSelectedItem(timeline), true);
  assert.deepEqual(timeline.director_track.sections.map((item) => item.item_id), [keep.item_id]);
  assert.equal(timeline.audio_tracks.length, 0);
  assert.deepEqual(getSelectedItemIds(timeline), []);
}

function testGroupMoveClampsSectionsAndMovesUnlockedAudio() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 6;
  const first = addValidTextSection(timeline, 0);
  const selectedSection = addValidTextSection(timeline, 2);
  const blockedByNext = addValidTextSection(timeline, 4);
  const clip = addAudioClip(timeline, 1, 1);
  const locked = addAudioClip(timeline, 2, 1);
  clip.audio = "/tmp/audio.wav";
  locked.audio = "/tmp/locked.wav";
  locked.locked = true;
  selectItem(timeline, selectedSection.item_id);
  toggleSelectItem(timeline, clip.item_id);
  toggleSelectItem(timeline, locked.item_id);

  moveSelectedItems(timeline, selectedSection.item_id, 3.5);

  assert.equal(selectedSection.start_time, 3);
  assert.equal(selectedSection.end_time, blockedByNext.start_time);
  assert.equal(clip.start_time, 2);
  assert.equal(clip.end_time, 3);
  assert.equal(locked.start_time, 2);
  assert.equal(locked.end_time, 3);
  assert.equal(first.start_time, 0);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testGroupDuplicatePreservesOffsetsAndSelectsCopies() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 8;
  const section = addValidTextSection(timeline, 0);
  const clip = addAudioClip(timeline, 0.25, 0.5);
  selectItem(timeline, section.item_id);
  toggleSelectItem(timeline, clip.item_id);

  const ids = duplicateSelectedSection(timeline);

  assert.equal(Array.isArray(ids), true);
  assert.equal(ids.length, 2);
  assert.deepEqual(getSelectedItemIds(timeline), ids);
  const copiedSection = timeline.director_track.sections.find((item) => item.item_id === ids[0]);
  const copiedClip = timeline.audio_tracks.flatMap((track) => track.clips).find((item) => item.item_id === ids[1]);
  assert.equal(copiedSection.start_time, 1);
  assert.equal(copiedSection.end_time, 2);
  assert.equal(copiedClip.start_time, 1.25);
  assert.equal(copiedClip.end_time, 1.75);
}

function testAudioAutoLanes() {
  const timeline = createDefaultVideoTimeline();
  addAudioClip(timeline, 0, 2);
  addAudioClip(timeline, 1, 2);
  addAudioClip(timeline, 2.5, 1);
  autoStackAudioLanes(timeline);
  const lanes = timeline.audio_tracks[0].clips.map((clip) => clip.lane);

  assert.deepEqual(lanes, [0, 0, 1]);
}

function testRippleResizeMovesFollowingSections() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;
  timeline.ui_state.section_edit_mode = "Ripple Edit";
  const first = addValidTextSection(timeline, 0);
  const second = addValidTextSection(timeline, 1);
  const third = addValidTextSection(timeline, 2.5);

  resizeSection(timeline, first.item_id, "end", 1.5);

  assert.equal(first.end_time, 1.5);
  assert.equal(second.start_time, 1.5);
  assert.equal(second.end_time, 2.5);
  assert.equal(third.start_time, 3);
  assert.equal(third.end_time, 4);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testAudioMoveAndResizeKeepSourceTrim() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 6;
  timeline.ui_state.snap_mode = "None";
  const clip = addAudioClip(timeline, 0, 2);
  clip.audio = "/tmp/audio.wav";

  moveAudioClip(timeline, clip.item_id, 1);
  assert.equal(clip.start_time, 1);
  assert.equal(clip.end_time, 3);
  assert.equal(clip.source_in, 0);
  assert.equal(clip.source_out, null);

  resizeAudioClip(timeline, clip.item_id, "start", 1.5);
  assert.equal(clip.start_time, 1.5);
  assert.equal(clip.source_in, 0.5);

  resizeAudioClip(timeline, clip.item_id, "end", 4);
  assert.equal(clip.end_time, 4);
  assert.equal(clip.source_out, 3);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testDirectorSectionOverflowDetection() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;
  addValidTextSection(timeline, 0);

  assert.equal(hasDirectorSectionOverflow(timeline), false);

  timeline.director_track.sections[0].end_time = 5.5;

  assert.equal(hasDirectorSectionOverflow(timeline), true);
}

function testFitLastDirectorSectionTrimsOnlyFinalSection() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 6;
  const first = addValidTextSection(timeline, 0);
  const second = addValidTextSection(timeline, 2);
  const last = addValidTextSection(timeline, 4);
  timeline.project.duration_seconds = 4.5;

  assert.equal(canFitLastDirectorSectionToDuration(timeline), true);
  assert.equal(fitLastDirectorSectionToDuration(timeline), true);

  assert.equal(first.end_time, 1);
  assert.equal(second.end_time, 3);
  assert.equal(last.start_time, 4);
  assert.equal(last.end_time, 4.5);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testFitLastDirectorSectionNoopsWhenFinalWouldBeInvalid() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 7;
  const last = addValidTextSection(timeline, 5);
  timeline.project.duration_seconds = 4.5;

  assert.equal(canFitLastDirectorSectionToDuration(timeline), false);
  assert.equal(fitLastDirectorSectionToDuration(timeline), false);
  assert.equal(last.start_time, 5);
  assert.equal(last.end_time, 6);
}

function testFitDirectorSectionsEvenlyPreservesRelativeGaps() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 10;
  const first = addValidTextSection(timeline, 0);
  const second = addValidTextSection(timeline, 4);
  first.end_time = 2;
  second.end_time = 6;
  timeline.project.duration_seconds = 3;

  assert.equal(fitDirectorSectionsEvenlyToDuration(timeline), true);

  assert.equal(first.start_time, 0);
  assert.equal(first.end_time, 1);
  assert.equal(second.start_time, 2);
  assert.equal(second.end_time, 3);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

testNewTextSectionStartsEmpty();
testSectionsCannotOverlapWhenMovedOrResized();
testAddAndDuplicateReturnNullWhenNoGapFits();
testGapsRemainAllowedAndDetected();
testSplitAndDuplicate();
testSelectionHelpersKeepPrimaryInSync();
testMigrationDerivesSelectedItemIdsFromPrimarySelection();
testSelectionNormalizationPreservesShotBoundaryAndTakeIds();
testDefaultTimelineHasSequenceAndModelTargetedLoras();
testMigrationDropsLegacyLorasAndPreservesSections();
testMigrationNormalizesPartialSequenceStructures();
testMigrationDerivesShotsFromFlatSections();
testMigrationIsIdempotentAndUsesDuplicateSuffixes();
testMalformedOrMissingSequenceMigratesFromSections();
testExistingSequenceShotsArePreserved();
testShotOperationsCreateAssignBoundaryAndDelete();
testAddSectionTargetsSelectedCompatibleShot();
testStandaloneSectionCreationStillCreatesWrapperShot();
testTakeAndClipInstanceOperations();
testDeleteTakeClearsAcceptedClipAndPrunesOnlyUnreferencedGeneratedAsset();
testDeleteTakesByAssetPathPrefersPathBeforeFallbackTakeId();
testAttachGeneratedAssetAsTakePreservesGeneratedShotType();
testProjectAndShotLoraOperations();
testValidationReportsGenericShotStructureIssues();
testValidationChecksGenericLoraStructureAndLegacyRemoval();
testValidationChecksTakeResolvedLorasAndBoundaryContinuity();
testGroupDeleteRemovesMixedSelection();
testGroupMoveClampsSectionsAndMovesUnlockedAudio();
testGroupDuplicatePreservesOffsetsAndSelectsCopies();
testAudioAutoLanes();
testRippleResizeMovesFollowingSections();
testAudioMoveAndResizeKeepSourceTrim();
testDirectorSectionOverflowDetection();
testFitLastDirectorSectionTrimsOnlyFinalSection();
testFitLastDirectorSectionNoopsWhenFinalWouldBeInvalid();
testFitDirectorSectionsEvenlyPreservesRelativeGaps();

console.log("phase4 operation tests passed");
