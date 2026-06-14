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
  assert.equal(getTimelineWidgetHeight(timeline), 270);
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
  assert.equal(rendererSource.includes("htd-section-preview"), true);
  assert.equal(rendererSource.includes("renderSectionPreview"), true);
  assert.equal(rendererSource.includes("object-fit: contain"), true);
}

testTimelineHeightIsTripled();
testAudioLanesExpandViewportToContent();
testSectionPreviewUsesContainedRepeatedFrames();

console.log("timeline preview UI tests passed");
