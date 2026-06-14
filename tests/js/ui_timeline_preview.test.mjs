import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import {
  AUDIO_LANE_HEIGHT,
  DIRECTOR_TRACK_HEIGHT,
  RULER_HEIGHT,
  TIMELINE_VIEWPORT_BORDER_HEIGHT,
  getTimelineViewportHeight,
} from "../../web/timeline/geometry.js";
import { getTimelineWidgetHeight } from "../../web/timeline/renderer.js";

function testTimelineHeightIsTripled() {
  const timeline = createDefaultVideoTimeline();

  assert.equal(DIRECTOR_TRACK_HEIGHT, 132);
  assert.equal(
    getTimelineViewportHeight(timeline),
    RULER_HEIGHT + DIRECTOR_TRACK_HEIGHT + AUDIO_LANE_HEIGHT + TIMELINE_VIEWPORT_BORDER_HEIGHT,
  );
  assert.equal(getTimelineWidgetHeight(timeline), 302);
}

function testAudioLanesExpandViewportToContent() {
  const timeline = createDefaultVideoTimeline();
  timeline.audio_tracks.push({
    track_id: "audio_track_001",
    clips: [
      { item_id: "clip_001", lane: 0 },
      { item_id: "clip_002", lane: 2 },
    ],
  });

  assert.equal(
    getTimelineViewportHeight(timeline),
    RULER_HEIGHT + DIRECTOR_TRACK_HEIGHT + AUDIO_LANE_HEIGHT * 3 + TIMELINE_VIEWPORT_BORDER_HEIGHT,
  );
}

function testSectionPreviewUsesContainedRepeatedFrames() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");

  assert.equal(rendererSource.includes('backgroundSize = "cover"'), false);
  assert.equal(rendererSource.includes(".htd-viewport { overflow: hidden;"), true);
  assert.equal(rendererSource.includes("viewport.scrollLeft"), false);
  assert.equal(rendererSource.includes("iconButton(\"text\", \"Add Text Section\""), true);
  assert.equal(rendererSource.includes("iconMenuControl"), true);
  assert.equal(rendererSource.includes("aria-label"), true);
  assert.equal(rendererSource.includes("htd-project-end"), true);
  assert.equal(rendererSource.includes("TIMELINE_RIGHT_PADDING"), true);
  assert.equal(rendererSource.includes("renderRangeControl"), true);
  assert.equal(rendererSource.includes("htd-range-control"), true);
  assert.equal(rendererSource.includes("view_start_seconds"), true);
  assert.equal(rendererSource.includes("view_end_seconds"), true);
  assert.equal(rendererSource.includes("getTimelineViewRange(timeline)"), true);
  assert.equal(rendererSource.includes("scheduleViewportRemeasure"), true);
  assert.equal(rendererSource.includes("ResizeObserver"), true);
  assert.equal(rendererSource.includes("contentRect?.width"), true);
  assert.equal(rendererSource.includes("moveTarget?.addEventListener(\"pointermove\", this.onPointerMove)"), true);
  assert.equal(rendererSource.includes("moveTarget?.removeEventListener(\"pointermove\", this.onPointerMove)"), true);
  assert.equal(rendererSource.includes("this.drag.bar = this.container.querySelector(\".htd-range-bar\")"), true);
  assert.equal(rendererSource.includes('trackLabel("director", "Director")'), true);
  assert.equal(rendererSource.includes('trackLabel("audio", "Audio")'), true);
  assert.equal(rendererSource.includes("width: ${TIMELINE_RIGHT_PADDING}px"), true);
  assert.equal(rendererSource.includes("htd-section-preview"), true);
  assert.equal(rendererSource.includes("renderSectionPreview"), true);
  assert.equal(rendererSource.includes("object-fit: contain"), true);
}

testTimelineHeightIsTripled();
testAudioLanesExpandViewportToContent();
testSectionPreviewUsesContainedRepeatedFrames();

console.log("timeline preview UI tests passed");
