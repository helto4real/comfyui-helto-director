import assert from "node:assert/strict";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import {
  TIMELINE_RIGHT_PADDING,
  clampTimelineViewRange,
  durationToPixels,
  getTimelineViewRange,
  getTimelineWidth,
  getVisibleTimelineSeconds,
  pixelsToSeconds,
  secondsToPixels,
  timeFromClientX,
} from "../../web/timeline/geometry.js";
import { zoomToFit } from "../../web/timeline/operations.js";

function testDefaultRangeCoversWholeProject() {
  const timeline = createDefaultVideoTimeline();

  assert.deepEqual(getTimelineViewRange(timeline), { start: 0, end: 5 });
  assert.equal(getVisibleTimelineSeconds(timeline), 5);
  assert.equal(getTimelineWidth(timeline, 720), 720);
  assert.equal(secondsToPixels(5, timeline, 720), 720 - TIMELINE_RIGHT_PADDING);
}

function testVisibleRangeMapsAbsoluteTimeRelativeToStart() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 10;
  timeline.ui_state.view_start_seconds = 2;
  timeline.ui_state.view_end_seconds = 6;

  assert.equal(secondsToPixels(2, timeline, 500), 0);
  assert.equal(secondsToPixels(6, timeline, 500), 500 - TIMELINE_RIGHT_PADDING);
  assert.equal(durationToPixels(1, timeline, 500), 118);
  assert.equal(pixelsToSeconds(236, timeline, 500), 4);
}

function testRangeClampSnapsWholeSecondsAndMinimumWidth() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;

  assert.deepEqual(clampTimelineViewRange(timeline, 1.2, 1.4), { start: 1, end: 2 });
  assert.deepEqual(clampTimelineViewRange(timeline, -10, 99), { start: 0, end: 5 });
  assert.deepEqual(clampTimelineViewRange(timeline, 5, 5), { start: 4, end: 5 });
}

function testZoomToFitResetsRange() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 20;
  timeline.ui_state.view_start_seconds = 3;
  timeline.ui_state.view_end_seconds = 7;

  zoomToFit(timeline);

  assert.equal(timeline.ui_state.view_start_seconds, 0);
  assert.equal(timeline.ui_state.view_end_seconds, 20);
  assert.equal(getVisibleTimelineSeconds(timeline), 20);
  assert.equal(getTimelineWidth(timeline, 640), 640);
  assert.equal(secondsToPixels(timeline.project.duration_seconds, timeline, 640), 640 - TIMELINE_RIGHT_PADDING);
}

function testClientXUsesRenderedScaleToTimelinePixels() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 10;
  timeline.ui_state.view_start_seconds = 0;
  timeline.ui_state.view_end_seconds = 10;
  const container = {
    getBoundingClientRect: () => ({ left: 100, width: 480 }),
  };

  assert.equal(timeFromClientX(100, container, timeline, 960), 0);
  assert.equal(timeFromClientX(566, container, timeline, 960), 10);
  assert.equal(timeFromClientX(580, container, timeline, 960), 10);
}

testDefaultRangeCoversWholeProject();
testVisibleRangeMapsAbsoluteTimeRelativeToStart();
testRangeClampSnapsWholeSecondsAndMinimumWidth();
testZoomToFitResetsRange();
testClientXUsesRenderedScaleToTimelinePixels();

console.log("phase6 timeline range tests passed");
