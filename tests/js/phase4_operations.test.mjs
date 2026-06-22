import assert from "node:assert/strict";
import {
  MODEL_LORA_MODEL_LTX_2_3,
  MODEL_LORA_MODEL_WAN_2_2,
  MODEL_LORA_TARGET_HIGH_NOISE,
  MODEL_LORA_TARGET_LOW_NOISE,
  MODEL_LORA_TARGET_MAIN,
  createDefaultVideoTimeline,
} from "../../web/timeline/schema.js";
import { normalizeVideoTimeline } from "../../web/timeline/migration.js";
import {
  addAudioClip,
  addSection,
  autoStackAudioLanes,
  canFitLastDirectorSectionToDuration,
  deleteSelectedItem,
  duplicateSelectedSection,
  fitDirectorSectionsEvenlyToDuration,
  fitLastDirectorSectionToDuration,
  getSelectedItemIds,
  hasDirectorSectionOverflow,
  isItemSelected,
  moveSelectedItems,
  moveAudioClip,
  moveSection,
  resizeAudioClip,
  resizeSection,
  selectItem,
  selectItemRange,
  splitSelectedSection,
  toggleSelectItem,
} from "../../web/timeline/operations.js";
import { detectDirectorGaps, validateVideoTimeline } from "../../web/timeline/validation.js";

function addValidTextSection(timeline, startTime) {
  const section = addSection(timeline, "Text", startTime);
  section.prompt = "Text prompt";
  return section;
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
  assert.deepEqual(normalized.sequence.shots, []);
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
testDefaultTimelineHasSequenceAndModelTargetedLoras();
testMigrationDropsLegacyLorasAndPreservesSections();
testMigrationNormalizesPartialSequenceStructures();
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
