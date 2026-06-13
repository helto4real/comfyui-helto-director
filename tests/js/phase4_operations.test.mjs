import assert from "node:assert/strict";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import {
  addAudioClip,
  addSection,
  autoStackAudioLanes,
  duplicateSelectedSection,
  moveSection,
  resizeSection,
  splitSelectedSection,
} from "../../web/timeline/operations.js";
import { detectDirectorGaps, validateVideoTimeline } from "../../web/timeline/validation.js";

function testSectionsCannotOverlapWhenMovedOrResized() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;
  const first = addSection(timeline, "Text", 0);
  const second = addSection(timeline, "Text", 2);

  moveSection(timeline, second.item_id, 0.5);
  assert.equal(second.start_time, first.end_time);

  resizeSection(timeline, first.item_id, "end", 3);
  assert.equal(first.end_time, second.start_time);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testAddAndDuplicateReturnNullWhenNoGapFits() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 1;
  const section = addSection(timeline, "Text", 0);
  timeline.ui_state.selected_item_id = section.item_id;

  assert.equal(addSection(timeline, "Text", 0), null);
  assert.equal(duplicateSelectedSection(timeline), null);
  assert.equal(timeline.director_track.sections.length, 1);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testGapsRemainAllowedAndDetected() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 4;
  addSection(timeline, "Text", 1);

  const gaps = detectDirectorGaps(timeline);
  assert.equal(gaps.length, 2);
  assert.equal(validateVideoTimeline(timeline).is_valid, true);
}

function testSplitAndDuplicate() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 6;
  const section = addSection(timeline, "Text", 0);
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

function testAudioAutoLanes() {
  const timeline = createDefaultVideoTimeline();
  addAudioClip(timeline, 0, 2);
  addAudioClip(timeline, 1, 2);
  addAudioClip(timeline, 2.5, 1);
  autoStackAudioLanes(timeline);
  const lanes = timeline.audio_tracks[0].clips.map((clip) => clip.lane);

  assert.deepEqual(lanes, [0, 0, 1]);
}

testSectionsCannotOverlapWhenMovedOrResized();
testAddAndDuplicateReturnNullWhenNoGapFits();
testGapsRemainAllowedAndDetected();
testSplitAndDuplicate();
testAudioAutoLanes();

console.log("phase4 operation tests passed");
