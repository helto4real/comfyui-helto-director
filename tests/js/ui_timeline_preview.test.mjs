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
import { getTimelineWidgetHeight, setLiveItemField } from "../../web/timeline/renderer.js";

function testTimelineHeightIsTripled() {
  const timeline = createDefaultVideoTimeline();

  assert.equal(DIRECTOR_TRACK_HEIGHT, 132);
  assert.equal(
    getTimelineViewportHeight(timeline),
    RULER_HEIGHT + DIRECTOR_TRACK_HEIGHT + AUDIO_LANE_HEIGHT + TIMELINE_VIEWPORT_BORDER_HEIGHT,
  );
  assert.equal(getTimelineWidgetHeight(timeline), 302);
}

function testSelectedPromptUsesFiveRowInspector() {
  const timeline = createDefaultVideoTimeline();
  timeline.director_track.sections.push({
    item_id: "section_001",
    type: "Text",
    start_time: 0,
    end_time: 1,
    prompt: "hello",
  });
  timeline.ui_state.selected_item_id = "section_001";

  assert.equal(getTimelineWidgetHeight(timeline), 456);
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

function testPromptEditsUpdateLiveSectionAfterStateReplacement() {
  const timeline = createDefaultVideoTimeline();
  const liveSection = {
    item_id: "section_001",
    type: "Text",
    start_time: 0,
    end_time: 1,
    prompt: "first debounce chunk",
  };
  const staleSectionReference = { ...liveSection };
  timeline.director_track.sections.push(liveSection);

  const updated = setLiveItemField(timeline, staleSectionReference, "prompt", "first debounce chunk plus the rest of the prompt");

  assert.equal(updated, liveSection);
  assert.equal(timeline.director_track.sections[0].prompt, "first debounce chunk plus the rest of the prompt");
  assert.equal(staleSectionReference.prompt, "first debounce chunk");
}

function testInspectorControlsUpdateLiveSectionAfterStateReplacement() {
  const timeline = createDefaultVideoTimeline();
  const liveSection = {
    item_id: "section_001",
    type: "Image",
    start_time: 0,
    end_time: 1,
    prompt: "",
    crop_mode: "Project Default",
  };
  const staleSectionReference = { ...liveSection };
  timeline.director_track.sections.push(liveSection);

  const updated = setLiveItemField(timeline, staleSectionReference, "crop_mode", "Crop");

  assert.equal(updated, liveSection);
  assert.equal(timeline.director_track.sections[0].crop_mode, "Crop");
  assert.equal(staleSectionReference.crop_mode, "Project Default");
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
  assert.equal(rendererSource.includes("startItemDrag(event"), true);
  assert.equal(rendererSource.includes("selectItem(timeline, dragState.itemId)"), true);
  assert.equal(rendererSource.includes("rerender: false"), true);
  assert.equal(rendererSource.includes("getPixelsPerSecond(timeline, this.viewportWidth)"), true);
  assert.equal(rendererSource.includes("moveTarget?.addEventListener(\"pointermove\", this.onPointerMove)"), true);
  assert.equal(rendererSource.includes("moveTarget?.removeEventListener(\"pointermove\", this.onPointerMove)"), true);
  assert.equal(rendererSource.includes("captureTarget?.releasePointerCapture"), true);
  assert.equal(rendererSource.includes("event.currentTarget.parentElement"), false);
  assert.equal(rendererSource.includes("this.drag.bar = this.container.querySelector(\".htd-range-bar\")"), true);
  assert.equal(rendererSource.includes('trackLabel("director", "Director")'), true);
  assert.equal(rendererSource.includes('trackLabel("audio", "Audio")'), true);
  assert.equal(rendererSource.includes("width: ${TIMELINE_RIGHT_PADDING}px"), true);
  assert.equal(rendererSource.includes("htd-section-preview"), true);
  assert.equal(rendererSource.includes("renderSectionPreview"), true);
  assert.equal(rendererSource.includes("object-fit: contain"), true);
  assert.equal(rendererSource.includes("touch-action: none"), true);
  assert.equal(rendererSource.includes("user-select: none"), true);
  assert.equal(rendererSource.includes('el("textarea", options.className ?? "htd-field")'), true);
  assert.equal(rendererSource.includes('placeholder: "Write your prompt here..."'), true);
  assert.equal(rendererSource.includes("input.placeholder = options.placeholder ?? title"), true);
  assert.equal(rendererSource.includes("rows: 5"), true);
  assert.equal(rendererSource.includes('scheduleDebouncedCommit("prompt typing", { rerender: false })'), true);
  assert.equal(rendererSource.includes("setLiveItemField(this.controller.timeline, item, field, input.value)"), true);
  assert.equal(rendererSource.includes('flushDebouncedCommit("prompt typing", { rerender: false })'), true);
  assert.equal(rendererSource.includes('this.renderInspectorRow("Prompt", control, "is-prompt")'), false);
  assert.equal(rendererSource.includes('el("div", "htd-inspector-row is-prompt")'), true);
  assert.equal(rendererSource.includes("htd-inspector.has-selection"), true);
  assert.equal(rendererSource.includes("INSPECTOR_EDITOR_HEIGHT"), true);
  assert.equal(rendererSource.includes("resize: none"), true);
  assert.equal(rendererSource.includes('inspectorTitle("Image Section")'), true);
  assert.equal(rendererSource.includes('inspectorTitle("Video Section")'), true);
  assert.equal(rendererSource.includes('inspectorTitle("Audio Clip")'), true);
  assert.equal(rendererSource.includes("renderInspectorControlRow"), true);
  assert.equal(rendererSource.includes("renderInspectorCompactField"), true);
  assert.equal(rendererSource.includes("renderIconSelectField"), true);
  assert.equal(rendererSource.includes('placement: "above-end"'), true);
  assert.equal(rendererSource.includes("showValue: true"), true);
  assert.equal(rendererSource.includes(".htd-menu.opens-above .htd-menu-list"), true);
  assert.equal(rendererSource.includes(".htd-menu.align-end .htd-menu-list"), true);
  assert.equal(rendererSource.includes("htd-menu-value"), true);
  assert.equal(rendererSource.includes("margin-right: 6px"), true);
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("Guide Strength:", this.renderGuideStrengthField(selected), "is-strength")'), true);
  assert.equal(rendererSource.includes('slider.type = "range"'), true);
  assert.equal(rendererSource.includes("clampNumber(value, 0, 1, 1)"), true);
  assert.equal(rendererSource.includes("setLiveItemField(timeline, item, field, nextValue)"), true);
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("Crop Mode:", this.renderIconSelectField(selected, "crop_mode", "Crop Mode", CROP_MODES, "crop"))'), true);
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("Timing Mode:", this.renderIconSelectField(selected, "timing_mode", "Timing Mode", VIDEO_TIMING_MODES, "timing"))'), true);
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("Source In:"'), true);
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("Source Out:"'), true);
  assert.equal(rendererSource.includes("is-section-inspector"), true);
  assert.equal(rendererSource.includes("is-audio-inspector"), true);
  assert.equal(rendererSource.includes('this.renderInspectorRow("Volume"'), true);
  assert.equal(rendererSource.includes('this.renderInspectorRow("Fade In"'), true);
  assert.equal(rendererSource.includes('this.renderInspectorRow("Fade Out"'), true);
  assert.equal(rendererSource.includes('this.renderInspectorRow("Enabled"'), true);
  assert.equal(rendererSource.includes('this.renderInspectorRow("Locked"'), true);
  assert.equal(rendererSource.includes('this.renderMediaSummary(timeline, selected.image, "Image")'), false);
  assert.equal(rendererSource.includes('this.renderMediaSummary(timeline, selected.video, "Video")'), false);
  assert.equal(rendererSource.includes('this.renderMediaSummary(timeline, selectedAudio.audio, "Audio")'), true);
  assert.equal(rendererSource.includes("Attach"), false);
  assert.equal(rendererSource.includes("Choose"), false);
  assert.equal(rendererSource.includes("Clear"), false);
}

testTimelineHeightIsTripled();
testSelectedPromptUsesFiveRowInspector();
testAudioLanesExpandViewportToContent();
testPromptEditsUpdateLiveSectionAfterStateReplacement();
testInspectorControlsUpdateLiveSectionAfterStateReplacement();
testSectionPreviewUsesContainedRepeatedFrames();

console.log("timeline preview UI tests passed");
