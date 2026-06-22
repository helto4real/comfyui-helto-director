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
testDefaultTimelineHasSequenceAndModelTargetedLoras();
testMigrationDropsLegacyLorasAndPreservesSections();
testMigrationNormalizesPartialSequenceStructures();
testMigrationDerivesShotsFromFlatSections();
testMigrationIsIdempotentAndUsesDuplicateSuffixes();
testMalformedOrMissingSequenceMigratesFromSections();
testExistingSequenceShotsArePreserved();
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
