import assert from "node:assert/strict";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import { getTimelineWidth } from "../../web/timeline/geometry.js";
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
  assert.equal(getTimelineWidth(timeline, 640), 640);
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
testZoomWidgetSyncUpdatesVisibleWidget();

console.log("phase6 zoom tests passed");
