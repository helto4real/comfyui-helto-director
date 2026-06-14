import assert from "node:assert/strict";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import {
  TIMELINE_RIGHT_PADDING,
  getTimelineWidth,
  getVisibleTimelineSeconds,
  secondsToPixels,
} from "../../web/timeline/geometry.js";
import { zoomToFit } from "../../web/timeline/operations.js";
import { setNodeZoomWidgetValue } from "../../web/timeline/renderer.js";

function testLongTimelineCanFitViewport() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 60;
  timeline.ui_state.zoom_level = 1;

  assert.equal(getTimelineWidth(timeline, 720), 720);
}

function testZoomToFitResetsZoomAndScroll() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 20;
  timeline.ui_state.zoom_level = 3;
  timeline.ui_state.scroll_x = 400;

  zoomToFit(timeline);

  assert.equal(timeline.ui_state.zoom_level, 1);
  assert.equal(timeline.ui_state.scroll_x, 0);
  assert.equal(getVisibleTimelineSeconds(timeline), 20);
  assert.equal(getTimelineWidth(timeline, 640), 640);
  assert.equal(secondsToPixels(timeline.project.duration_seconds, timeline, 640), 640 - TIMELINE_RIGHT_PADDING);
}

function testManualZoomExpandsHorizontalTimelineScale() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 10;
  timeline.ui_state.zoom_level = 2;

  assert.equal(getTimelineWidth(timeline, 500), 972);
  assert.equal(secondsToPixels(1, timeline, 500), 94.4);
}

function testManualZoomRoundsVisibleRangeUpToWholeSecond() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;
  timeline.ui_state.zoom_level = 2;

  assert.equal(getVisibleTimelineSeconds(timeline), 3);
  assert.equal(secondsToPixels(3, timeline, 500), 500 - TIMELINE_RIGHT_PADDING);
  assert.ok(Math.abs(getTimelineWidth(timeline, 500) - ((5 * (500 - TIMELINE_RIGHT_PADDING)) / 3 + TIMELINE_RIGHT_PADDING)) < 1e-9);
}

function testManualZoomClampsVisibleRange() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.duration_seconds = 5;

  timeline.ui_state.zoom_level = 20;
  assert.equal(getVisibleTimelineSeconds(timeline), 1);

  timeline.ui_state.zoom_level = 0.5;
  assert.equal(getVisibleTimelineSeconds(timeline), 5);
  assert.equal(getTimelineWidth(timeline, 500), 500);
}

function testZoomWidgetSyncUpdatesVisibleWidget() {
  let callbackValue = null;
  const node = {
    widgets: [
      {
        name: "zoom_level",
        value: 3,
        callback(value) {
          callbackValue = value;
        },
      },
    ],
  };

  assert.equal(setNodeZoomWidgetValue(node, 1), true);
  assert.equal(node.widgets[0].value, 1);
  assert.equal(callbackValue, 1);
}

testLongTimelineCanFitViewport();
testZoomToFitResetsZoomAndScroll();
testManualZoomExpandsHorizontalTimelineScale();
testManualZoomRoundsVisibleRangeUpToWholeSecond();
testManualZoomClampsVisibleRange();
testZoomWidgetSyncUpdatesVisibleWidget();

console.log("phase6 zoom tests passed");
