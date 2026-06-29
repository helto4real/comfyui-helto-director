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
import {
  ensureTimelineNodeFitsContent,
  findAttachedTakeForCapture,
  getTimelineNodeMinimumHeight,
  getTimelineWidgetRenderedHeight,
  getTimelineWidgetHeight,
  isDefaultEmptyTimeline,
  measureStableTimelineViewportWidth,
  setLiveItemField,
  waveformPeakCountForWidth,
  waveformPeakRequestForClip,
  waveformPeaksForClip,
} from "../../web/timeline/renderer.js";
import {
  clearNativeTakeCapturePreview,
  ensureTakeCapturePreviewNodeFits,
  installTakeCapturePreview,
  maintainTakeCapturePreview,
  repairTakeCaptureShiftedSocketlessWidgetValues,
  setTakeCapturePreviewReveal,
  stripTakeCapturePreviewMedia,
  suppressNativeTakeCapturePreview,
  syncTakeCapturePreview,
  takeCapturePreviewFromOutput,
  takeCapturePreviewRequiredNodeHeight,
  takeCapturePreviewUrl,
} from "../../web/timeline/take_capture_preview.js";

function testTimelineHeightIsTripled() {
  const timeline = createDefaultVideoTimeline();

  assert.equal(DIRECTOR_TRACK_HEIGHT, 132);
  assert.equal(
    getTimelineViewportHeight(timeline),
    RULER_HEIGHT + DIRECTOR_TRACK_HEIGHT + AUDIO_LANE_HEIGHT + TIMELINE_VIEWPORT_BORDER_HEIGHT,
  );
  assert.equal(getTimelineWidgetHeight(timeline), 308);
}

function testClearTimelineButtonEnablementHelper() {
  const timeline = createDefaultVideoTimeline();
  timeline.validation = { errors: [], warnings: [], info: [] };
  timeline.ui_state.state_revision = 12;

  assert.equal(isDefaultEmptyTimeline(timeline), true);

  timeline.project.identity.project_id = "proj_other";
  timeline.project.storage.project_directory_name = "untitled_project_proj_other";
  assert.equal(isDefaultEmptyTimeline(timeline), true);

  timeline.project.identity.name = "Real Project";
  assert.equal(isDefaultEmptyTimeline(timeline), false);
  timeline.project.identity.name = "Untitled Project";

  timeline.project.metadata.library_item_id = "timeline_123";
  assert.equal(isDefaultEmptyTimeline(timeline), false);
  delete timeline.project.metadata.library_item_id;

  timeline.director_track.sections.push({
    item_id: "section_001",
    type: "Text",
    start_time: 0,
    end_time: 1,
    prompt: "not empty",
  });
  assert.equal(isDefaultEmptyTimeline(timeline), false);
}

function testSelectedPromptUsesShotAwareInspectorHeight() {
  const timeline = createDefaultVideoTimeline();
  timeline.director_track.sections.push({
    item_id: "section_001",
    type: "Text",
    start_time: 0,
    end_time: 1,
    prompt: "hello",
  });
  timeline.ui_state.selected_item_id = "section_001";

  assert.equal(getTimelineWidgetHeight(timeline), 534);
}

function testTimelineWidgetUsesNodeHeightAndGrowsWhenTooSmall() {
  const timeline = createDefaultVideoTimeline();
  timeline.director_track.sections.push({
    item_id: "section_001",
    type: "Text",
    start_time: 0,
    end_time: 1,
    prompt: "hello",
  });
  timeline.ui_state.selected_item_id = "section_001";

  const widget = { y: 180 };
  const contentHeight = getTimelineWidgetHeight(timeline);
  assert.equal(contentHeight, 534);
  assert.equal(getTimelineWidgetRenderedHeight({ size: [820, 900] }, widget, timeline), 700);
  assert.equal(getTimelineNodeMinimumHeight({ size: [820, 600] }, widget, timeline), 734);

  const setSizes = [];
  const node = {
    size: [820, 600],
    setSize(nextSize) {
      this.size = nextSize;
      setSizes.push(nextSize);
    },
  };
  assert.equal(ensureTimelineNodeFitsContent(node, widget, timeline), true);
  assert.deepEqual(setSizes, [[820, 734]]);
  assert.equal(getTimelineWidgetRenderedHeight(node, widget, timeline), contentHeight);

  const tallNode = { size: [820, 900] };
  assert.equal(ensureTimelineNodeFitsContent(tallNode, widget, timeline), false);
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

function testBoundarySelectionUsesInspectorHeightAndLiveFieldUpdates() {
  const timeline = createDefaultVideoTimeline();
  const liveBoundary = {
    boundary_id: "boundary_001",
    left_shot_id: "shot_left",
    right_shot_id: "shot_right",
    mode: "Continuous Shot",
    tail_frames: 5,
    blend_frames: 3,
    transition_prompt: "",
    reuse_character_refs: true,
    reuse_style: true,
    metadata: {},
  };
  timeline.sequence.boundaries.push(liveBoundary);
  timeline.ui_state.selected_item_id = "boundary_001";

  assert.equal(getTimelineWidgetHeight(timeline), 534);

  const staleBoundaryReference = { ...liveBoundary };
  const updated = setLiveItemField(timeline, staleBoundaryReference, "transition_prompt", "match cut through smoke");

  assert.equal(updated, liveBoundary);
  assert.equal(timeline.sequence.boundaries[0].transition_prompt, "match cut through smoke");
  assert.equal(staleBoundaryReference.transition_prompt, "");
}

function testSectionPreviewUsesContainedRepeatedFrames() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");

  assert.equal(rendererSource.includes('backgroundSize = "cover"'), false);
  assert.equal(rendererSource.includes(".htd-viewport { width: 100%; overflow: hidden;"), true);
  assert.equal(rendererSource.includes("viewport.scrollLeft"), false);
  assert.equal(rendererSource.includes("iconButton(\"text\", \"Add Text Section\""), true);
  assert.equal(rendererSource.includes("iconMenuControl"), true);
  assert.equal(rendererSource.includes("aria-label"), true);
  assert.equal(rendererSource.includes("renderShotBoundaryContext(timeline, shot)"), true);
  assert.equal(rendererSource.includes("renderBoundaryInspector(timeline, selectedBoundary)"), true);
  assert.equal(rendererSource.includes("renderBoundaryModeField(boundary)"), true);
  assert.equal(rendererSource.includes('this.renderNumberField(boundary, "tail_frames"'), true);
  assert.equal(rendererSource.includes('this.renderNumberField(boundary, "blend_frames"'), true);
  assert.equal(rendererSource.includes('this.renderTextField(boundary, "transition_prompt"'), true);
  assert.equal(rendererSource.includes('this.renderCheckboxField(boundary, "reuse_character_refs"'), true);
  assert.equal(rendererSource.includes('this.renderCheckboxField(boundary, "reuse_style"'), true);
  assert.equal(rendererSource.includes(".htd-boundary-prompt"), true);
  assert.equal(rendererSource.includes("renderAssemblyReadinessPill(timeline, shot)"), true);
  assert.equal(rendererSource.includes("renderLatestCapture(timeline, shot)"), true);
  assert.equal(rendererSource.includes("renderCaptureManagementModal(timeline, modalShot)"), true);
  assert.equal(rendererSource.includes("renderAvailableCaptures(timeline, shot)"), false);
  assert.equal(rendererSource.includes("Copy Shot ID For Planner Input"), false);
  assert.equal(rendererSource.includes("shotDisplayLabel(timeline, shot)"), true);
  assert.equal(rendererSource.includes("return `Shot ${index >= 0 ? index + 1 : 1}`;"), true);
  assert.equal(rendererSource.includes("shot.name || shot.shot_id"), false);
  assert.equal(rendererSource.includes("const advanced = this.renderAdvancedTakeAttachment(timeline, shot)"), true);
  assert.equal(rendererSource.includes("Manual Take"), true);
  assert.equal(rendererSource.includes("Choose generated asset"), true);
  assert.equal(rendererSource.includes("Attach Existing Generated Asset As Candidate Take"), true);
  assert.equal(rendererSource.includes("Pick Existing Generated Asset As Candidate Take"), true);
  assert.equal(rendererSource.includes("Attach Generated Video As Take"), false);
  assert.equal(rendererSource.includes("Latest Capture"), true);
  assert.equal(rendererSource.includes("Available Captures"), false);
  assert.equal(rendererSource.includes("Project Captures"), true);
  assert.equal(rendererSource.includes("Attached Takes"), true);
  assert.equal(rendererSource.includes("Open Captures"), true);
  assert.equal(rendererSource.includes("Register Take From Metadata"), false);
  assert.equal(rendererSource.includes("attachPickedGeneratedVideoAsTake(timeline, options.shotId, item)"), true);
  assert.equal(rendererSource.includes("fetchProjectTakeCaptures(timeline, shot.shot_id"), true);
  assert.equal(rendererSource.includes("Attach Project Capture As Take"), true);
  assert.equal(rendererSource.includes("Attach And Accept Project Capture"), true);
  assert.equal(rendererSource.includes("Project Name"), false);
  assert.equal(rendererSource.includes("Project ID"), false);
  assert.equal(rendererSource.includes("Asset Root Directory"), true);
  assert.equal(rendererSource.includes("Project Folder"), true);
  assert.equal(rendererSource.includes("projectFolderDisplay(timeline"), true);
  assert.equal(rendererSource.includes("entry.code === \"BOUNDARY_LORA_STACK_MISMATCH\""), true);
  assert.equal(rendererSource.includes("showTimelineLoraStackEditor"), true);
  assert.equal(rendererSource.includes("loraEditorProfileForTarget"), true);
  assert.equal(rendererSource.includes("renderLoraTargetActions"), true);
  assert.equal(rendererSource.includes("LoRA Targets"), true);
  assert.equal(rendererSource.includes("htd-shot-lora-targets-row"), true);
  assert.equal(rendererSource.includes("htd-lora-target-separator"), true);
  assert.equal(rendererSource.includes("openProjectLoraStackEditor"), true);
  assert.equal(rendererSource.includes("openShotLoraStackEditor"), true);
  assert.equal(rendererSource.includes("Reveal privacy before editing LoRAs"), true);
  assert.equal(rendererSource.includes("Stack note"), false);
  assert.equal(rendererSource.includes("assetDisplayLabel(asset, privacyRevealed"), true);
  assert.equal(rendererSource.includes("assetSummaryLabel(asset, privacyRevealed)"), true);
  assert.equal(rendererSource.includes("takeSummaryLabel(timeline, take, privacyRevealed)"), true);
  assert.equal(rendererSource.includes("privacyRevealed ? take.take_id : takeStatusLabel(take)"), true);
  assert.equal(rendererSource.includes("privacyRevealed ? `seed ${take.seed}` : \"Seeded\""), true);
  assert.equal(rendererSource.includes("deleteProjectTakeCapture,"), true);
  assert.equal(rendererSource.includes("deleteTake,"), false);
  assert.equal(rendererSource.includes("deleteTakesByAssetPath,"), true);
  assert.equal(rendererSource.includes("captureOrderLabel(index, total)"), true);
  assert.equal(rendererSource.includes("return `Capture ${String(number).padStart(3, \"0\")}`;"), true);
  assert.equal(rendererSource.includes("return `Take ${String(number).padStart(3, \"0\")}`;"), true);
  assert.equal(rendererSource.includes("const previewData = this.captureVideoPreviewData(timeline, item, privacyRevealed)"), true);
  assert.equal(rendererSource.includes("const previewData = this.takeVideoPreviewData(timeline, take, asset, privacyRevealed)"), true);
  assert.equal((rendererSource.match(/iconButton\("preview-video", previewData \? "Preview Take Video" : "No preview available"/g) ?? []).length, 2);
  assert.equal((rendererSource.match(/iconButton\("delete", "Delete Take Files"/g) ?? []).length, 2);
  assert.equal(rendererSource.includes('iconButton("insert", existing ? "Capture already attached"'), true);
  assert.equal(rendererSource.includes('iconButton("accept", existing?.status === "Accepted"'), true);
  assert.equal(rendererSource.includes('iconButton("reject", !existing ? "Attach capture before rejecting"'), true);
  assert.equal(rendererSource.includes('iconButton("restore", !existing ? "Attach capture before restoring"'), true);
  assert.equal(rendererSource.includes("remove.classList.add(\"is-danger\")"), true);
  assert.equal(rendererSource.includes("actions.append(remove, attach, accept, reject, restore)"), true);
  assert.equal(rendererSource.includes("row.append(label, assetSummary, status, actions)"), true);
  assert.equal(rendererSource.includes("row.append(label, summary, status, actions)"), true);
  assert.equal(rendererSource.includes("if (previewData) actions.append(iconButton(\"preview-video\", \"Preview Take Video\""), false);
  assert.equal(rendererSource.includes("deleteProjectTakeCaptureFromItem(shot, item"), true);
  assert.equal(rendererSource.includes("deleteProjectTakeFromTimelineTake(shot, take, asset"), true);
  assert.equal(rendererSource.includes("const capturePath = captureMediaPath(item)"), true);
  assert.equal(rendererSource.includes("const existing = findAttachedTakeForCapture(timeline, shot, item)"), true);
  assert.equal(rendererSource.includes("const liveExisting = findAttachedTakeForCapture(currentTimeline, liveShot, item)"), true);
  assert.equal(rendererSource.includes("async deleteProjectTakePath(shotId, path, options = {})"), true);
  assert.equal(rendererSource.includes("if (!path && !options.takeId)"), true);
  assert.equal(rendererSource.includes("confirmFn?.(`Remove ${label} from the timeline and delete any remaining project take files?`)"), true);
  assert.equal(rendererSource.includes("if (path) {"), true);
  assert.equal(rendererSource.includes("await deleteProjectTakeCapture(timeline, shotId, path"), true);
  assert.equal(rendererSource.includes("deleteTakesByAssetPath(currentTimeline, shotId, path, options.takeId)"), true);
  assert.equal(rendererSource.includes("export function findAttachedTakeForCapture(timeline, shot, item)"), true);
  assert.equal(rendererSource.includes("function captureMediaPath(item)"), true);
  assert.equal(rendererSource.includes("if (capturePath) {"), true);
  assert.equal(rendererSource.includes("return takes.find((take) => assetMediaPath(assetForId(timeline, take.asset_id)) === capturePath) ?? null;"), true);
  assert.equal(rendererSource.includes("&& !assetMediaPath(assetForId(timeline, take.asset_id))"), true);
  assert.equal(rendererSource.includes("const existing = takeId ? (shot.takes ?? []).find((take) => take.take_id === takeId) : null"), false);
  assert.equal(rendererSource.includes("alertFn?.(error?.message || \"Could not delete project take files.\")"), true);
  assert.equal(rendererSource.includes("takeVideoPreviewData(timeline, take, asset = null"), true);
  assert.equal(rendererSource.includes("captureVideoPreviewData(timeline, item"), true);
  assert.equal(rendererSource.includes("caption: assetDisplayLabel(resolvedAsset, privacyRevealed, \"Video Take\")"), true);
  assert.equal(rendererSource.includes("caption: captureSummaryLabel(item, privacyRevealed)"), true);
  assert.equal(rendererSource.includes("privacyMode: this.isGlobalPrivacyMode()"), true);
  assert.equal(rendererSource.includes("openTakeVideoPreviewData(previewData)"), true);
  assert.equal(rendererSource.includes('"preview-video": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('accept: `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('reject: `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('restore: `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('refresh: `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes(".htd-capture-row { grid-template-columns:"), true);
  assert.equal(rendererSource.includes(".htd-captures-modal .htd-take-row, .htd-captures-modal .htd-capture-row { grid-template-columns:"), true);
  assert.equal(rendererSource.includes(".htd-captures-modal .htd-take-actions { width: 164px; display: grid; grid-template-columns: repeat(6, 24px);"), true);
  assert.equal(rendererSource.includes(".htd-take-status-placeholder { visibility: hidden; pointer-events: none; }"), true);
  assert.equal(rendererSource.includes(".htd-captures-overlay { position: absolute;"), true);
  assert.equal(rendererSource.includes(".htd-captures-modal { width: min(860px, 100%);"), true);
  assert.equal(rendererSource.includes("event.target === overlay"), true);
  assert.equal(rendererSource.includes("assemblyReadinessStatus(timeline, shot)"), true);
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("Status:"'), false);
  assert.equal(rendererSource.includes("renderShotStatusField"), false);
  assert.equal(rendererSource.includes("assemblyReadinessPillTone(fullStatus)"), true);
  assert.equal(rendererSource.includes(".htd-readiness-pill.is-ready"), true);
  assert.equal(rendererSource.includes(".htd-readiness-pill.is-needs-take"), true);
  assert.equal(rendererSource.includes(".htd-readiness-pill.is-needs-generation"), true);
  assert.equal(rendererSource.includes(".htd-readiness-pill.is-blocked"), true);
  assert.equal(rendererSource.includes("shotIdInput"), false);
  assert.equal(rendererSource.includes("htd-shot-id"), false);
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("ID:"'), false);
  assert.equal(rendererSource.includes('const nameField = this.renderInspectorCompactField("Name:", nameInput, "is-shot-name")'), true);
  assert.equal(rendererSource.includes(".htd-shot-name { width: min(100%, 520px); min-width: 220px; max-width: none; }"), true);
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
  assert.equal(rendererSource.includes("measureStableTimelineViewportWidth(this.node, this.container)"), true);
  assert.equal(rendererSource.includes("this.applyViewportContainerWidth(this.viewportWidth)"), true);
  assert.equal(rendererSource.includes("handleNodeResize()"), true);
  assert.equal(rendererSource.includes("getTimelineWidgetRenderedHeight(node, widget"), true);
  assert.equal(rendererSource.includes("ensureTimelineNodeFitsContent(this.node, this.widget"), true);
  assert.equal(rendererSource.includes("getRenderedInspectorHeight(timeline, renderedHeight)"), true);
  assert.equal(rendererSource.includes("measureIntrinsicTimelineContentHeight"), true);
  assert.equal(rendererSource.includes("applyViewportContainerWidth(width)"), true);
  assert.equal(rendererSource.includes("applyWidgetContainerHeight(renderedHeight"), true);
  assert.equal(rendererSource.includes("this.container.style.width = `${stableWidth}px`;"), true);
  assert.equal(rendererSource.includes("this.container.style.maxWidth = `${stableWidth}px`;"), true);
  assert.equal(rendererSource.includes("this.container.parentElement.style.width = `${stableWidth}px`;"), true);
  assert.equal(rendererSource.includes("this.container.parentElement.style.maxWidth = `${stableWidth}px`;"), true);
  assert.equal(rendererSource.includes("this.container.parentElement.style.height = `${stableHeight}px`;"), true);
  assert.equal(rendererSource.includes("root.style.width = `${this.viewportWidth}px`;"), true);
  assert.equal(rendererSource.includes("nodeBodyWidth(node)"), true);
  assert.equal(rendererSource.includes("if (nodeWidth > 0) return Math.max(1, nodeWidth);"), true);
  assert.equal(rendererSource.includes("viewportWidth >= stableWidth ? viewportWidth : stableWidth"), true);
  assert.equal(rendererSource.includes(".helto-timeline-director { width: 100%; box-sizing: border-box;"), true);
  assert.equal(rendererSource.includes(".htd-root { position: relative; width: 100%; height: 100%; box-sizing: border-box;"), true);
  assert.equal(rendererSource.includes(".htd-range-control { width: 100%;"), true);
  assert.equal(rendererSource.includes(".htd-viewport { width: 100%; overflow: hidden; box-sizing: border-box;"), true);
  assert.equal(rendererSource.includes(".htd-inspector { width: 100%;"), true);
  assert.equal(rendererSource.includes("this.controller.setTimelineKeyboardScope?.(this.container)"), true);
  assert.equal(rendererSource.includes("this.controller.setTimelineKeyboardScope?.(null)"), true);
  assert.equal(rendererSource.includes("item.tabIndex = -1"), true);
  assert.equal(rendererSource.includes("item.dataset.itemId = section.item_id"), true);
  assert.equal(rendererSource.includes("item.dataset.itemId = clip.item_id"), true);
  assert.equal(rendererSource.includes("item.dataset.itemId = shot.shot_id"), true);
  assert.equal(rendererSource.includes("const movableShot = canMoveBareShot(timeline, shot.shot_id)"), true);
  assert.equal(rendererSource.includes("item.classList.toggle(\"is-bare-shot\", movableShot)"), true);
  assert.equal(rendererSource.includes("this.startShotDrag(event, shot)"), true);
  assert.equal(rendererSource.includes("startShotDrag(event, shot)"), true);
  assert.equal(rendererSource.includes('mode: "shot-move"'), true);
  assert.equal(rendererSource.includes("startItemDrag(event"), true);
  assert.equal(rendererSource.includes('dragState.mode === "move" || dragState.mode === "audio-move" || dragState.mode === "shot-move"'), true);
  assert.equal(rendererSource.includes("handlePointerSelection(event, dragState.itemId, target)"), true);
  assert.equal(rendererSource.includes("selectItem(timeline, dragState.itemId)"), true);
  assert.equal(rendererSource.includes("toggleSelectItem(timeline, itemId)"), true);
  assert.equal(rendererSource.includes("selectItemRange(timeline, itemId)"), true);
  assert.equal(rendererSource.includes("this.focusTimelineItem(dragState.itemId, target)"), true);
  assert.equal(rendererSource.includes("focusTimelineItem(itemId"), true);
  assert.equal(rendererSource.includes("target?.focus?.({ preventScroll: true })"), true);
  assert.equal(rendererSource.includes("rerender: false"), true);
  assert.equal(rendererSource.includes("timeFromClientX(event.clientX, timeContainer, this.controller.timeline, this.viewportWidth)"), true);
  assert.equal(rendererSource.includes("pointerTimeOffset: pointerTime - Number(dragState.startStart)"), true);
  assert.equal(rendererSource.includes("pointerEdgeTimeOffset: pointerTime - pointerEdgeTime"), true);
  assert.equal(rendererSource.includes("const pointerTime = this.dragTimeFromClientX(event.clientX)"), true);
  assert.equal(rendererSource.includes("moveSelectedItems(timeline, this.drag.itemId, pointerTime - this.drag.pointerTimeOffset)"), true);
  assert.equal(rendererSource.includes('this.drag.mode === "shot-move"'), true);
  assert.equal(rendererSource.includes('resizeSection(timeline, this.drag.itemId, "start", pointerTime - this.drag.pointerEdgeTimeOffset, { globalSettings: this.globalSettings() })'), true);
  assert.equal(rendererSource.includes('resizeSection(timeline, this.drag.itemId, "end", pointerTime - this.drag.pointerEdgeTimeOffset, { globalSettings: this.globalSettings() })'), true);
  assert.equal(rendererSource.includes('resizeAudioClip(timeline, this.drag.itemId, "start", pointerTime - this.drag.pointerEdgeTimeOffset, { globalSettings: this.globalSettings() })'), true);
  assert.equal(rendererSource.includes('resizeAudioClip(timeline, this.drag.itemId, "end", pointerTime - this.drag.pointerEdgeTimeOffset, { globalSettings: this.globalSettings() })'), true);
  assert.equal(rendererSource.includes("this.drag.timeContainer = this.findDragTimeContainer(this.drag.itemId)"), true);
  assert.equal(rendererSource.includes("Number(event.clientX) - Number(this.drag.startX)"), false);
  assert.equal(rendererSource.includes("deltaSeconds"), false);
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
  assert.equal(rendererSource.includes("item.classList.add(\"is-selected\")"), true);
  assert.equal(rendererSource.includes("item.classList.add(\"is-primary-selected\")"), true);
  assert.equal(rendererSource.includes(".htd-item.is-primary-selected"), true);
  assert.equal(rendererSource.includes("showMediaPreview(this.container.ownerDocument ?? globalThis.document"), true);
  assert.equal(rendererSource.includes("thumb.addEventListener(\"click\""), true);
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
  assert.equal(rendererSource.includes('scheduleDebouncedCommit("reference description", { delayMs: 150, rerender: false })'), true);
  assert.equal(rendererSource.includes('flushDebouncedCommit("reference description", { rerender: false })'), true);
  assert.equal(rendererSource.includes('scheduleDebouncedCommit("reference strength", { delayMs: 80, rerender: false })'), true);
  assert.equal(rendererSource.includes('this.renderInspectorRow("Prompt", control, "is-prompt")'), false);
  assert.equal(rendererSource.includes('el("div", "htd-inspector-row is-prompt")'), true);
  assert.equal(rendererSource.includes("htd-inspector.has-selection"), true);
  assert.equal(rendererSource.includes("INSPECTOR_EDITOR_HEIGHT"), true);
  assert.equal(rendererSource.includes("resize: none"), true);
  assert.equal(rendererSource.includes('this.renderSectionInspectorHeader(timeline, selected, activeShot, "Image Section")'), true);
  assert.equal(rendererSource.includes('this.renderSectionInspectorHeader(timeline, selected, activeShot, "Video Section")'), true);
  assert.equal(rendererSource.includes('this.renderSectionInspectorHeader(timeline, selected, activeShot, "Text Section")'), true);
  assert.equal(rendererSource.includes("renderSectionShotSummary(timeline, activeShot)"), true);
  assert.equal(rendererSource.includes("Shot: ${shotLabel}"), true);
  assert.equal(rendererSource.includes("activeShot ? this.renderShotInspector(timeline, activeShot)"), false);
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
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("Guidance Range:", this.renderIconSelectField(selected, "video_guidance_range", "Guidance Range", VIDEO_GUIDANCE_RANGES, "guide-range"))'), true);
  assert.equal(rendererSource.includes('selected.video_guidance_range === "Last Frames"'), true);
  assert.equal(rendererSource.includes('this.renderInspectorCompactField("Guide Frames:", this.renderIconSelectField(selected, "video_guidance_frame_count", "Guide Frames", VIDEO_GUIDANCE_FRAME_COUNTS, "guide-frames"))'), true);
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
  assert.equal(rendererSource.includes("Attach Existing Generated Asset As Candidate Take"), true);
  assert.equal(rendererSource.includes("Choose generated asset"), true);
  assert.equal(rendererSource.includes("Choose generated video"), false);
  assert.equal(rendererSource.includes("Clear Media"), false);

  const migrationSource = readFileSync(new URL("../../web/timeline/migration.js", import.meta.url), "utf8");
  assert.equal(migrationSource.includes('normalized.video_guidance_range ??= "Last Frames"'), true);
  assert.equal(migrationSource.includes("normalized.video_guidance_frame_count ??= 17"), true);
}

function testProjectCaptureAttachmentMatchingPrefersPath() {
  const timeline = {
    assets: [
      { asset_id: "asset_attached", type: "Video", path: "/project/takes/shot_001/old.mp4" },
      { asset_id: "asset_other", type: "Video", path: "/project/takes/shot_001/other.mp4" },
    ],
  };
  const attachedTake = { take_id: "take_001", asset_id: "asset_attached", status: "Candidate" };
  const shot = { shot_id: "shot_001", takes: [attachedTake] };

  assert.equal(findAttachedTakeForCapture(timeline, shot, {
    path: "/project/takes/shot_001/old.mp4",
    take_capture: { registration: { take: { take_id: "take_999" } } },
  }), attachedTake);

  assert.equal(findAttachedTakeForCapture(timeline, shot, {
    path: "/project/takes/shot_001/new.mp4",
    take_capture: { registration: { take: { take_id: "take_001" } } },
  }), null);

  assert.equal(findAttachedTakeForCapture(timeline, {
    shot_id: "shot_002",
    takes: [{ take_id: "take_001", asset_id: "asset_other", status: "Candidate" }],
  }, {
    take_capture: { registration: { take: { take_id: "take_001" } } },
  }), null);

  const pathlessTake = { take_id: "take_002", asset_id: "asset_missing", status: "Candidate" };
  assert.equal(findAttachedTakeForCapture(timeline, {
    shot_id: "shot_003",
    takes: [pathlessTake],
  }, {
    take_capture: { registration: { take: { take_id: "take_002" } } },
  }), pathlessTake);
}

function testSharedMediaPreviewSupportsVideoControls() {
  const previewSource = readFileSync(new URL("../../web/timeline/media_preview.js", import.meta.url), "utf8");

  assert.equal(previewSource.includes("export function showMediaPreview"), true);
  assert.equal(previewSource.includes("export function closeMediaPreview"), true);
  assert.equal(previewSource.includes('video.preload = "metadata"'), true);
  assert.equal(previewSource.includes("video.playsInline = true"), true);
  assert.equal(previewSource.includes("video.muted = true"), true);
  assert.equal(previewSource.includes("video.currentTime = 0"), true);
  assert.equal(previewSource.includes("Muted"), true);
  assert.equal(previewSource.includes("Audio On"), true);
  assert.equal(previewSource.includes("pr-image-large-preview-close"), true);
  assert.equal(previewSource.includes("event.target === overlay"), true);
  assert.equal(previewSource.includes("options.privacyMode ? \" privacy-mode\" : \"\""), true);
  assert.equal(previewSource.includes(".pr-image-large-preview.privacy-mode .pr-image-large-preview-panel video"), true);
  assert.equal(previewSource.includes(".pr-image-large-preview.privacy-mode .pr-image-large-preview-panel:hover video"), true);
}

function testTakeCapturePreviewHelpersUseTaggedOutput() {
  const source = { filename: "shot take 001.mp4", subfolder: "takes/shot_001", type: "output" };
  const apiRef = { apiURL: (path) => `/api${path}` };
  const output = {
    images: [source],
    animated: [true],
    helto_take_capture_preview: [true],
    helto_privacy_mode: [true],
  };

  assert.equal(
    takeCapturePreviewUrl(source, apiRef),
    "/api/view?filename=shot+take+001.mp4&type=output&subfolder=takes%2Fshot_001",
  );
  assert.deepEqual(takeCapturePreviewFromOutput(output, apiRef), {
    privacyMode: true,
    source,
    url: "/api/view?filename=shot+take+001.mp4&type=output&subfolder=takes%2Fshot_001",
  });
  assert.deepEqual(takeCapturePreviewFromOutput({ ...output, helto_privacy_mode: [false] }, apiRef), {
    privacyMode: false,
    source,
    url: "/api/view?filename=shot+take+001.mp4&type=output&subfolder=takes%2Fshot_001",
  });
  assert.equal(takeCapturePreviewFromOutput({ ...output, helto_take_capture_preview: [false] }, apiRef), null);
  assert.equal(takeCapturePreviewFromOutput({ ...output, images: [] }, apiRef), null);
  assert.deepEqual(stripTakeCapturePreviewMedia(output), {
    helto_take_capture_preview: [true],
    helto_privacy_mode: [true],
  });
}

function testTakeCapturePreviewClearsNativeAndRevealsOnHover() {
  const previousDocument = globalThis.document;
  globalThis.document = createFakeDocument();
  try {
    const node = createFakeTakeCaptureNode();
    const appRef = { canvas: { dirty: [], setDirty(...args) { this.dirty.push(args); } } };
    const output = {
      images: [{ filename: "private.mp4", subfolder: "takes", type: "output" }],
      animated: [true],
      helto_take_capture_preview: [true],
      helto_privacy_mode: [true],
    };

    assert.equal(syncTakeCapturePreview(node, output, { apiRef: { apiURL: (path) => path }, appRef }), true);
    assert.equal(node.imgs.length, 0);
    assert.equal(node.videoContainer, undefined);
    assert.equal(node.images, output.images);
    assert.equal(node.animatedImages, false);
    assert.equal(node.previewMediaType, undefined);
    assert.equal(node.widgets.some((widget) => widget.name === "$$canvas-image-preview"), false);
    assert.equal(node.widgets.some((widget) => widget.name === "$$comfy_animation_preview"), false);
    assert.equal(node.widgets.some((widget) => widget.name === "video-preview"), false);
    assert.equal(node.removedNativeWidgets, 3);
    assert.equal(node.hideOutputImages, true);
    assert.equal(node._heltoTakeCapturePreview.url, "/view?filename=private.mp4&type=output&subfolder=takes");
    assert.equal(node._heltoTakeCapturePreview.container.hidden, false);
    assert.equal(node._heltoTakeCapturePreview.container.classList.contains("is-revealed"), false);
    assert.equal(node._heltoTakeCapturePreview.container.classList.contains("privacy-mode"), true);
    assert.equal(node._heltoTakeCapturePreview.video.attributes["aria-label"], "Private take capture preview");
    assert.equal(node._heltoTakeCapturePreview.widget.hidden, false);
    assert.equal(node._heltoTakeCapturePreview.widget.options.getHeight(), 200);
    assert.equal("computeSize" in node._heltoTakeCapturePreview.widget, false);
    assert.deepEqual(node.setSizes, [[320, 696]]);
    assert.deepEqual(node.graph.dirty, [[true, true]]);
    assert.deepEqual(appRef.canvas.dirty, [[true, true]]);

    assert.equal(setTakeCapturePreviewReveal(node, true), true);
    assert.equal(node._heltoTakeCapturePreview.container.classList.contains("is-revealed"), true);
    assert.equal(node._heltoTakeCapturePreview.video.playCount, 1);

    assert.equal(setTakeCapturePreviewReveal(node, false), true);
    assert.equal(node._heltoTakeCapturePreview.container.classList.contains("is-revealed"), false);
    assert.equal(node._heltoTakeCapturePreview.video.pauseCount, 2);
    assert.equal(node._heltoTakeCapturePreview.video.currentTime, 0);
  } finally {
    globalThis.document = previousDocument;
  }
}

function testTakeCapturePublicPreviewUsesHoverWidgetAndRestoresNativeVisibility() {
  const previousDocument = globalThis.document;
  globalThis.document = createFakeDocument();
  try {
    const node = createFakeTakeCaptureNode({ hideOutputImages: false });
    const output = {
      images: [{ filename: "public.mp4", subfolder: "takes", type: "output" }],
      animated: [true],
      helto_take_capture_preview: [true],
      helto_privacy_mode: [false],
    };

    assert.equal(syncTakeCapturePreview(node, output, { apiRef: { apiURL: (path) => path } }), true);
    assert.equal(node.hideOutputImages, true);
    assert.equal(node._heltoTakeCapturePreview.container.hidden, false);
    assert.equal(node._heltoTakeCapturePreview.container.classList.contains("privacy-mode"), false);
    assert.equal(node._heltoTakeCapturePreview.video.attributes["aria-label"], "Take capture preview");

    assert.equal(syncTakeCapturePreview(node, { images: [{ filename: "other.mp4" }] }), false);
    assert.equal(node.hideOutputImages, false);
    assert.equal(node._heltoTakeCapturePreview.container.hidden, true);
    assert.equal(node._heltoTakeCapturePreview.widget.hidden, true);
    assert.equal(node._heltoTakeCapturePreview.url, "");
    assert.equal(node._heltoTakeCapturePreview.video.attributes.src, undefined);
  } finally {
    globalThis.document = previousDocument;
  }
}

function testTakeCapturePreviewInstallerStripsNativeMediaBeforeOriginalOnExecuted() {
  const previousDocument = globalThis.document;
  globalThis.document = createFakeDocument();
  try {
    let forwardedOutput = null;
    function FakeNode() {
      Object.assign(this, createFakeTakeCaptureNode());
    }
    FakeNode.prototype.onExecuted = function (output) {
      forwardedOutput = output;
      return "native-result";
    };

    installTakeCapturePreview(FakeNode, { canvas: { setDirty() {} } }, { apiURL: (path) => path });
    const node = new FakeNode();
    node.onNodeCreated();
    const result = node.onExecuted({
      images: [{ filename: "public.mp4", type: "output" }],
      animated: [true],
      helto_take_capture_preview: [true],
      helto_privacy_mode: [false],
    });

    assert.equal(result, "native-result");
    assert.equal("images" in forwardedOutput, false);
    assert.equal("animated" in forwardedOutput, false);
    assert.deepEqual(forwardedOutput, {
      helto_take_capture_preview: [true],
      helto_privacy_mode: [false],
    });
    assert.equal(node.hideOutputImages, true);
    assert.equal(node._heltoTakeCapturePreview.url, "/view?filename=public.mp4&type=output");
  } finally {
    globalThis.document = previousDocument;
  }
}

function testTakeCapturePreviewConsumesNativeStoredOutput() {
  const output = {
    images: [{ filename: "stored.mp4", type: "output" }],
    animated: [true],
    helto_take_capture_preview: [true],
  };
  const node = createFakeTakeCaptureNode({
    images: [{ filename: "previous.mp4", type: "output" }],
    animatedImages: true,
    previewMediaType: "video",
  });

  assert.equal(suppressNativeTakeCapturePreview(node, output), true);
  assert.equal(node.images, output.images);
  assert.equal(node.animatedImages, false);
  assert.equal(node.previewMediaType, undefined);
  assert.equal(node.imgs.length, 0);
  assert.equal(node.videoContainer, undefined);
  assert.equal(node.widgets.some((widget) => widget.name === "video-preview"), false);
}

function testTakeCapturePreviewNodeGrowthUsesStableFitTarget() {
  const node = createFakeTakeCaptureNode();
  node._heltoTakeCapturePreview = {
    url: "/view?filename=active.mp4&type=output",
    widget: {
      y: 500,
      computedHeight: 200,
      options: { margin: 10 },
    },
  };

  assert.equal(takeCapturePreviewRequiredNodeHeight(node), 696);
  assert.equal(ensureTakeCapturePreviewNodeFits(node), true);
  assert.deepEqual(node.size, [320, 696]);
  assert.deepEqual(node.setSizes, [[320, 696]]);

  node.setSizes = [];
  assert.equal(ensureTakeCapturePreviewNodeFits(node), false);
  assert.deepEqual(node.setSizes, []);

  node.computeSize = () => {
    throw new Error("computeSize should not be used for active preview fitting");
  };
  node._heltoTakeCapturePreview.widget.y = 900;
  node._heltoTakeCapturePreview.widget.computedHeight = 500;
  assert.equal(takeCapturePreviewRequiredNodeHeight(node), 696);
  assert.equal(ensureTakeCapturePreviewNodeFits(node), false);
  assert.deepEqual(node.setSizes, []);
}

function testTakeCapturePreviewPreservesSocketlessWidgetOrder() {
  const previousDocument = globalThis.document;
  globalThis.document = createFakeDocument();
  try {
    const node = createFakeTakeCaptureNode({
      widgets: [
        { name: "$$canvas-image-preview", onRemove() { node.removedNativeWidgets += 1; } },
        { name: "frame_rate" },
        { name: "take_registration_json" },
        { name: "generated_asset_path" },
        { name: "shot_id_override" },
        { name: "filename_prefix" },
      ],
    });
    const inputWidgetOrder = [
      "frame_rate",
      "take_registration_json",
      "generated_asset_path",
      "shot_id_override",
      "filename_prefix",
    ];
    const output = {
      images: [{ filename: "ordered.mp4", type: "output" }],
      helto_take_capture_preview: [true],
    };

    assert.equal(syncTakeCapturePreview(node, output, { apiRef: { apiURL: (path) => path } }), true);
    const previewIndex = node.widgets.findIndex((widget) => widget.name === "helto_take_capture_preview");
    const remainingInputOrder = node.widgets
      .map((widget) => widget.name)
      .filter((name) => inputWidgetOrder.includes(name));
    assert.equal(previewIndex >= 0, true);
    assert.deepEqual(remainingInputOrder, inputWidgetOrder);
    assert.equal(previewIndex > node.widgets.findIndex((widget) => widget.name === "filename_prefix"), true);
    assert.deepEqual(node.setSizes, [[320, 696]]);
  } finally {
    globalThis.document = previousDocument;
  }
}

function testTakeCapturePreviewRepairsPreviouslyShiftedSocketlessValues() {
  const node = createFakeTakeCaptureNode({
    widgets: [
      { name: "frame_rate", value: 24 },
      { name: "take_registration_json", value: "" },
      { name: "generated_asset_path", value: "" },
      { name: "shot_id_override", value: "%shot_id%_%take_id%" },
      { name: "filename_prefix", value: false },
      { name: "accept", value: true },
      { name: "update_clip_instance", value: true },
    ],
  });

  assert.equal(repairTakeCaptureShiftedSocketlessWidgetValues(node), true);
  assert.equal(node.widgets.find((widget) => widget.name === "shot_id_override").value, "");
  assert.equal(node.widgets.find((widget) => widget.name === "filename_prefix").value, "%shot_id%_%take_id%");
  assert.equal(node.widgets.find((widget) => widget.name === "accept").value, false);
  assert.equal(node.widgets.find((widget) => widget.name === "update_clip_instance").value, true);

  assert.equal(repairTakeCaptureShiftedSocketlessWidgetValues(node), false);
}

function testTakeCapturePreviewMaintainsNodeGrowthDuringDraw() {
  const node = createFakeTakeCaptureNode();
  node._heltoTakeCapturePreview = {
    url: "/view?filename=active.mp4&type=output",
    widget: {
      y: 500,
      computedHeight: 200,
      options: { margin: 10 },
    },
  };
  const appRef = { canvas: { dirty: [], setDirty(...args) { this.dirty.push(args); } } };

  assert.equal(maintainTakeCapturePreview(node, { appRef }), true);
  assert.deepEqual(node.size, [320, 696]);
  assert.deepEqual(node.setSizes, [[320, 696]]);
  assert.deepEqual(node.graph.dirty, [[true, true]]);
  assert.deepEqual(appRef.canvas.dirty, [[true, true]]);

  node.setSizes = [];
  node.graph.dirty = [];
  appRef.canvas.dirty = [];
  assert.equal(maintainTakeCapturePreview(node, { appRef }), false);
  assert.deepEqual(node.setSizes, []);
  assert.deepEqual(node.graph.dirty, []);
}

function testTakeCapturePreviewExtensionIsInstalled() {
  const extensionSource = readFileSync(new URL("../../web/video_timeline_director.js", import.meta.url), "utf8");

  assert.equal(extensionSource.includes('import { api } from "../../scripts/api.js";'), true);
  assert.equal(extensionSource.includes('import { installTakeCapturePreview } from "./timeline/take_capture_preview.js";'), true);
  assert.equal(extensionSource.includes('nodeData?.name === "HeltoTimelineTakeCapture"'), true);
  assert.equal(extensionSource.includes("installTakeCapturePreview(nodeType, app, api)"), true);

  const previewSource = readFileSync(new URL("../../web/timeline/take_capture_preview.js", import.meta.url), "utf8");
  assert.equal(previewSource.includes("helto_take_capture_preview"), true);
  assert.equal(previewSource.includes("helto_privacy_mode"), true);
  assert.equal(previewSource.includes("\"$$canvas-image-preview\""), true);
  assert.equal(previewSource.includes("\"$$comfy_animation_preview\""), true);
  assert.equal(previewSource.includes("\"video-preview\""), true);
  assert.equal(previewSource.includes("hideOutputImages = true"), true);
  assert.equal(previewSource.includes("getMinHeight: widgetHeight"), true);
  assert.equal(previewSource.includes("getMaxHeight: widgetHeight"), true);
  assert.equal(previewSource.includes("getHeight: widgetHeight"), true);
  assert.equal(previewSource.includes("onMouseEnter"), true);
  assert.equal(previewSource.includes("onMouseLeave"), true);
}

function testDeleteContextMenuIsAvailableOnTimelineItems() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");
  const contextMenuRegistrations = rendererSource.match(/addEventListener\("contextmenu"/g) ?? [];

  assert.equal(contextMenuRegistrations.length >= 2, true);
  assert.equal(rendererSource.includes('this.showItemContextMenu(event, section.item_id, section.type)'), true);
  assert.equal(rendererSource.includes('this.showItemContextMenu(event, clip.item_id, "Audio Clip")'), true);
  assert.equal(rendererSource.includes('Image: "Delete Image"'), true);
  assert.equal(rendererSource.includes('Video: "Delete Video"'), true);
  assert.equal(rendererSource.includes('Image: "Replace image"'), true);
  assert.equal(rendererSource.includes('Video: "Replace video"'), true);
  assert.equal(rendererSource.includes('Text: "Delete Text"'), true);
  assert.equal(rendererSource.includes('"Audio Clip": "Delete Audio Clip"'), true);
  assert.equal(rendererSource.includes('Image: "Duplicate Image"'), true);
  assert.equal(rendererSource.includes('"Audio Clip": "Duplicate Audio Clip"'), true);
  assert.equal(rendererSource.includes('"Duplicate Selection"'), true);
  assert.equal(rendererSource.includes('"Delete Selection"'), true);
  assert.equal(rendererSource.includes('"Preview image"'), true);
  assert.equal(rendererSource.includes("deleteLabelForItemType"), true);
  assert.equal(rendererSource.includes("duplicateLabelForItemType"), true);
  assert.equal(rendererSource.includes("replaceLabelForItemType"), true);
  assert.equal(rendererSource.includes("if (!isItemSelected(this.controller.timeline, itemId))"), true);
  assert.equal(rendererSource.includes("const selectedSection = selectedCount === 1 && this.controller.timeline.ui_state.selected_item_id === itemId"), true);
  assert.equal(rendererSource.includes("selectedSection?.type === ASSET_TYPE_IMAGE"), true);
  assert.equal(rendererSource.includes("this.sectionImagePreviewData(selectedSection)"), true);
  assert.equal(rendererSource.includes("this.openSectionMediaPreviewData(previewData)"), true);
  assert.equal(rendererSource.includes('this.openMediaPicker(itemType, { mode: "replace", itemId })'), true);
  assert.equal(rendererSource.includes("if (replaceLabel && selectedCount === 1)"), true);
  assert.equal(rendererSource.includes("sectionImagePreviewData(section)"), true);
  assert.equal(rendererSource.includes("section?.type !== ASSET_TYPE_IMAGE"), true);
  assert.equal(rendererSource.includes("this.isGlobalPrivacyMode() && (!this.privacyRevealActive || this.privacyExternalModalOpen)"), true);
  assert.equal(rendererSource.includes("const url = mediaViewUrl(asset);"), true);
  assert.equal(rendererSource.includes("openSectionMediaPreviewData(previewData)"), true);
  assert.equal(rendererSource.includes("if (!previewData?.url) return false;"), true);
  assert.equal(rendererSource.includes("showMediaPreview(this.container.ownerDocument ?? globalThis.document, previewData)"), true);
  assert.equal(rendererSource.includes("deleteSelectedItem(timeline)"), true);
  assert.equal(rendererSource.includes("if (!this.contextMenuElement) this.setPrivacyRevealActive(false);"), true);
  assert.equal(rendererSource.includes("this.setPrivacyRevealActive(true);"), true);
  assert.equal(rendererSource.includes("htd-context-menu"), true);
  assert.equal(rendererSource.includes("htd-context-menu-item"), true);
  assert.equal(rendererSource.includes('(documentRef?.body ?? this.container).append(menu)'), true);
  assert.equal(rendererSource.includes("viewport?.innerWidth"), true);
  assert.equal(rendererSource.includes("viewport?.innerHeight"), true);
  assert.equal(rendererSource.includes("root.getBoundingClientRect"), false);
  assert.equal(rendererSource.includes(".htd-context-menu { position: fixed;"), true);
  assert.equal(rendererSource.includes("width: 150px;"), true);
  assert.equal(rendererSource.includes("max-width: calc(100vw - 8px);"), true);
  assert.equal(rendererSource.includes("background: linear-gradient(180deg, var(--htd-surface-2, #1b2333), var(--htd-surface, #151c2a));"), true);
  assert.equal(rendererSource.includes("border: 1px solid var(--htd-border-strong, #3a465c);"), true);
  assert.equal(rendererSource.includes("color: var(--htd-text, #e7ebf3);"), true);
  assert.equal(rendererSource.includes("text-overflow: ellipsis;"), true);
  assert.equal(rendererSource.includes(".htd-context-menu-item:hover { background: var(--htd-surface-hover, #2c3850);"), true);
}

function testToolbarUsesGroupedIconControls() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");
  const toolbarSpacers = rendererSource.match(/toolbarSpacer\(\)/g) ?? [];

  assert.equal(toolbarSpacers.length >= 4, true);
  assert.equal(rendererSource.includes("const hasOverflow = hasDirectorSectionOverflow(this.controller.timeline)"), true);
  assert.equal(rendererSource.includes("const selectedIds = getSelectedItemIds(this.controller.timeline)"), true);
  assert.equal(rendererSource.includes("const selectedSection = selectedIds.some((itemId) => findSection(this.controller.timeline, itemId))"), true);
  assert.equal(rendererSource.includes('const deleteButton = iconButton("delete", "Delete", () => this.commitMutation((timeline) => deleteSelectedItem(timeline), "delete"))'), true);
  assert.equal(rendererSource.includes('deleteButton.classList.toggle("is-danger", Boolean(selectedSection))'), true);
  assert.equal(rendererSource.includes("const repairButtons = hasOverflow"), true);
  assert.equal(rendererSource.includes('iconButton("shot", "Add Shot", () => this.commitMutation((timeline) => insertShotAfterCurrent(timeline, { globalSettings: this.globalSettings() }), "add shot"))'), true);
  assert.equal(rendererSource.includes("const start = Number(timeline.ui_state.playhead_time ?? 0)"), false);
  assert.equal(rendererSource.includes('"Fit Last Section"'), true);
  assert.equal(rendererSource.includes('"Fit All Sections Evenly"'), true);
  assert.equal(rendererSource.includes("fitLastDirectorSectionToDuration(timeline)"), true);
  assert.equal(rendererSource.includes("fitDirectorSectionsEvenlyToDuration(timeline)"), true);
  assert.equal(rendererSource.includes("canFitLastDirectorSectionToDuration(this.controller.timeline)"), true);
  assert.equal(rendererSource.includes("...repairButtons"), true);
  assert.equal(rendererSource.includes("control.disabled = Boolean(options.disabled)"), true);
  assert.equal(rendererSource.includes('const clearTimelineButton = iconButton("timeline-clear", "Clear Current Timeline", () => this.clearCurrentTimeline(), {'), true);
  assert.equal(rendererSource.includes("disabled: isDefaultEmptyTimeline(this.controller.timeline)"), true);
  assert.equal(rendererSource.includes('clearTimelineButton.classList.add("htd-clear-timeline-button", "is-danger")'), true);
  assert.equal(rendererSource.includes("if (isDefaultEmptyTimeline(this.controller.timeline)) return false;"), true);
  assert.equal(rendererSource.includes("CLEAR_TIMELINE_CONFIRMATION"), true);
  assert.equal(rendererSource.includes("Saved library items and media files will not be deleted."), true);
  assert.equal(rendererSource.includes("this.controller.replaceTimeline(createDefaultVideoTimeline(), \"clear current timeline\""), true);
  assert.equal(rendererSource.includes('"timeline-clear": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes(".htd-button.is-danger {"), true);
  assert.equal(rendererSource.includes('const promptOptimizerButton = iconButton("sparkle", "Prompt Optimizer"'), true);
  assert.equal(rendererSource.includes('const referenceManagerButton = iconButton("references", "Manage Character References"'), true);
  assert.equal(rendererSource.includes('const referencePresentButton = iconButton("reference-active"'), true);
  assert.equal(rendererSource.includes("const referencesEnabled = areCharacterReferencesEnabled(this.controller.timeline)"), true);
  assert.equal(rendererSource.includes("() => this.toggleCharacterReferences()"), true);
  assert.equal(rendererSource.includes("{ disabled: referenceCount === 0 }"), true);
  assert.equal(rendererSource.includes("referencePresentButton.classList.toggle(\"is-active\", referenceCount > 0 && referencesEnabled)"), true);
  assert.equal(rendererSource.includes("timeline.project.metadata.character_references_enabled = !areCharacterReferencesEnabled(timeline)"), true);
  assert.equal(rendererSource.includes("this.renderReferenceManager(timeline)"), true);
  assert.equal(rendererSource.includes('mode: "reference"'), true);
  assert.equal(rendererSource.includes("input.dataset.referenceTrigger = PROMPT_REFERENCE_TRIGGER"), true);
  assert.equal(rendererSource.includes("return this.wrapPromptReferenceIntellisense(input, item);"), true);
  assert.equal(rendererSource.includes("attachPromptReferenceIntellisense(input, popup, item)"), true);
  assert.equal(rendererSource.includes("applyReferencePromptCompletion("), true);
  assert.equal(rendererSource.includes("filterReferencePromptCompletions(completions, context.query)"), true);
  assert.equal(rendererSource.includes('event.key === "ArrowDown" || event.key === "ArrowUp"'), true);
  assert.equal(rendererSource.includes('event.key === "Enter"'), true);
  assert.equal(rendererSource.includes('event.key === "Escape"'), true);
  assert.equal(rendererSource.includes("/^[1-9]$/.test(event.key)"), true);
  assert.equal(rendererSource.includes('this.controller.scheduleDebouncedCommit("prompt typing", { rerender: false });'), true);
  assert.equal(rendererSource.includes(".htd-prompt-wrap { position: relative;"), true);
  assert.equal(rendererSource.includes(".htd-reference-completions { position: absolute;"), true);
  assert.equal(rendererSource.includes("showPromptOptimizer({"), true);
  assert.equal(rendererSource.includes("promptOptimizerButton,"), true);
  assert.equal(rendererSource.indexOf("promptOptimizerButton,") < rendererSource.indexOf("settingsButton,"), true);
  assert.equal(rendererSource.includes('promptOptimizerButton.classList.add("htd-prompt-optimizer-button")'), true);
  assert.equal(rendererSource.includes('const projectSettingsButton = iconButton("project-settings", "Project Settings", () => this.openProjectSettings())'), true);
  assert.equal(rendererSource.includes('settingsButton.classList.add("htd-settings-button")'), true);
  assert.equal(rendererSource.includes('projectSettingsButton.classList.add("htd-project-settings-button")'), true);
  assert.equal(rendererSource.indexOf("projectLibraryButton,") < rendererSource.indexOf("clearTimelineButton,"), true);
  assert.equal(rendererSource.indexOf("clearTimelineButton,") < rendererSource.indexOf("projectSettingsButton,"), true);
  assert.equal(rendererSource.indexOf("projectSettingsButton,") < rendererSource.indexOf("referenceManagerButton,"), true);
  assert.equal(rendererSource.includes('toggleIconButton("native-audio", "Use Native Audio"'), true);
  assert.equal(rendererSource.includes("timeline.project.audio.use_native_audio = !timeline.project.audio.use_native_audio"), true);
  assert.equal(rendererSource.includes('"native-audio": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('references: `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('"reference-active": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('"fit-last-section": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('"fit-all-sections": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('sparkle: `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('"project-settings": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('this.renderSettingsActions("Project Settings", () => this.saveProjectSettings(), () => this.cancelProjectSettings())'), true);
  assert.equal(rendererSource.includes('this.renderSettingsActions("Global Settings", (control) => this.saveGlobalSettings(control), () => this.cancelGlobalSettings())'), true);
  assert.equal(rendererSource.includes(".htd-toolbar-spacer {"), true);
  assert.equal(rendererSource.includes(".htd-button:disabled {"), true);
  assert.equal(rendererSource.includes(".htd-prompt-optimizer-button { margin-left: auto; }"), true);
  assert.equal(rendererSource.includes('settings: `<svg viewBox="0 0 24 24"><path d="M12 15.5a3.5'), true);
}

function testWanSegmentedExecutorSplitStepWidgetSync() {
  const extensionSource = readFileSync(new URL("../../web/video_timeline_director.js", import.meta.url), "utf8");

  assert.equal(extensionSource.includes('nodeData?.name === "HeltoWAN22TimelineSegmentedExecutor"'), true);
  assert.equal(extensionSource.includes("installWanSegmentedExecutorSplitStepSync(nodeType)"), true);
  assert.equal(extensionSource.includes('findWidget(node, "steps")'), true);
  assert.equal(extensionSource.includes('findWidget(node, "phase_split_step")'), true);
  assert.equal(extensionSource.includes("Math.floor(Number.isFinite(steps) ? steps / 2 : 10)"), true);
  assert.equal(extensionSource.includes("stepsWidget.callback = function ()"), true);
  assert.equal(extensionSource.includes("syncWanPhaseSplitStep(node, { markCanvas: true })"), true);
  assert.equal(extensionSource.includes("app.graph?.setDirtyCanvas?.(true, true)"), true);
}

function testTimelineStatusBarOverlayIsNotInstalled() {
  const extensionSource = readFileSync(new URL("../../web/video_timeline_director.js", import.meta.url), "utf8");

  assert.equal(extensionSource.includes("installTimelineStatusBarBridge"), false);
  assert.equal(extensionSource.includes("helto-timeline-status-bar-bridge"), false);
  assert.equal(extensionSource.includes('apiRef.addEventListener?.("progress_state"'), false);
  assert.equal(extensionSource.includes("latestByNodeId: new Map()"), false);
  assert.equal(extensionSource.includes("aliasesByNodeId: new Map()"), false);
}

function testRendererUsesRealWaveformsOnly() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");
  const stateSource = readFileSync(new URL("../../web/timeline/state.js", import.meta.url), "utf8");

  assert.equal(rendererSource.includes("createWaveformBars"), false);
  assert.equal(rendererSource.includes("requestWaveform?.(asset, peakCount)"), true);
  assert.equal(rendererSource.includes("waveformPeakRequestForClip"), true);
  assert.equal(rendererSource.includes("waveformPeaksForClip"), true);
  assert.equal(rendererSource.includes("is-loading"), true);
  assert.equal(rendererSource.includes("if (shouldShowWaveform(timeline, this.privacyRevealActive, this.globalSettings()))"), true);
  assert.equal(rendererSource.includes("item.style.height = `${AUDIO_LANE_HEIGHT - 8}px`;"), true);
  assert.equal(rendererSource.includes('if (shouldShowWaveform(timeline, this.privacyRevealActive, this.globalSettings())) item.append(renderWaveform(this.node, timeline, clip, itemWidth));\n    item.append(clipLabel);'), true);
  assert.equal(rendererSource.includes(".htd-audio-label { position: absolute; z-index: 3;"), true);
  assert.equal(rendererSource.includes(".htd-waveform { position: absolute; z-index: 1; inset: 4px 9px;"), true);
  assert.equal(rendererSource.includes('this.renderGlobalSettingCheckbox("Privacy Mode", draft, ["privacy", "mode"])'), true);
  assert.equal(rendererSource.includes('this.renderSettingCheckbox("Privacy Mode", ["project", "privacy", "mode"])'), false);
  assert.equal(rendererSource.includes("renderGlobalSettings(timeline)"), true);
  assert.equal(rendererSource.includes("renderProjectSettings(timeline)"), true);
  assert.equal(rendererSource.includes("Hide Media Previews"), false);
  assert.equal(rendererSource.includes("Hide Text Prompts"), false);
  assert.equal(rendererSource.includes("Encrypt Previews"), false);
  assert.equal(rendererSource.includes("is-private"), true);
  assert.equal(rendererSource.includes("is-privacy-revealed"), true);
  assert.equal(rendererSource.includes("privacyExternalModalOpen"), true);
  assert.equal(rendererSource.includes("is-privacy-modal-open"), true);
  assert.equal(rendererSource.includes("this.privacyRevealActive && !this.privacyExternalModalOpen"), true);
  assert.equal(rendererSource.includes("onClose: () => {"), true);
  assert.equal(stateSource.includes(".htd-lora-editor-dialog"), true);
  assert.equal(stateSource.includes(".htd-lora-info-dialog"), true);
}

function testWaveformHelpersAdaptAndTrimPeaks() {
  const payload = {
    duration_seconds: 10,
    peaks: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
  };

  assert.equal(waveformPeakCountForWidth(8), 16);
  assert.equal(waveformPeakCountForWidth(2000), 512);
  assert.equal(waveformPeakRequestForClip(100, {
    start_time: 0,
    end_time: 2,
    source_in: 2,
    source_out: 4,
  }, 10), 250);
  assert.deepEqual(waveformPeaksForClip(payload, {
    start_time: 0,
    end_time: 2,
    source_in: 2,
    source_out: 5,
  }), [0.2, 0.3, 0.4]);
  assert.deepEqual(waveformPeaksForClip(payload, {
    start_time: 0,
    end_time: 2,
    source_in: 3,
    source_out: null,
  }), [0.3, 0.4]);
  assert.deepEqual(waveformPeaksForClip(payload, {
    start_time: 0,
    end_time: 2,
    source_in: 2,
    source_out: 5,
  }, 6), [0.2, 0.2, 0.3, 0.3, 0.4, 0.4]);
  assert.deepEqual(waveformPeaksForClip(payload, {
    start_time: 0,
    end_time: 2,
    source_in: 2,
    source_out: 5,
    volume: 50,
  }), [0.1, 0.15, 0.2]);
  assert.deepEqual(waveformPeaksForClip(payload, {
    start_time: 0,
    end_time: 2,
    source_in: 5,
    source_out: 8,
    volume: 200,
  }), [1, 1, 1]);
}

function testViewportMeasurementIgnoresCollapsedChildWidth() {
  const container = {
    clientWidth: 460,
    offsetWidth: 460,
    parentElement: {
      clientWidth: 768,
      offsetWidth: 768,
      getBoundingClientRect: () => ({ width: 768 }),
    },
    getBoundingClientRect: () => ({ width: 460 }),
    querySelector: () => ({ clientWidth: 460 }),
  };

  assert.equal(measureStableTimelineViewportWidth({ size: [820, 400] }, container), 800);
  assert.equal(measureStableTimelineViewportWidth({}, container), 768);
  assert.equal(measureStableTimelineViewportWidth({ size: [820, 400] }, {
    ...container,
    querySelector: () => ({ clientWidth: 900 }),
  }), 800);
  assert.equal(measureStableTimelineViewportWidth({ size: [540, 400] }, {
    ...container,
    querySelector: () => ({ clientWidth: 900 }),
  }), 520);
}

testTimelineHeightIsTripled();
testClearTimelineButtonEnablementHelper();
testSelectedPromptUsesShotAwareInspectorHeight();
testTimelineWidgetUsesNodeHeightAndGrowsWhenTooSmall();
testAudioLanesExpandViewportToContent();
testPromptEditsUpdateLiveSectionAfterStateReplacement();
testInspectorControlsUpdateLiveSectionAfterStateReplacement();
testBoundarySelectionUsesInspectorHeightAndLiveFieldUpdates();
testSectionPreviewUsesContainedRepeatedFrames();
testProjectCaptureAttachmentMatchingPrefersPath();
testSharedMediaPreviewSupportsVideoControls();
testTakeCapturePreviewHelpersUseTaggedOutput();
testTakeCapturePreviewClearsNativeAndRevealsOnHover();
testTakeCapturePublicPreviewUsesHoverWidgetAndRestoresNativeVisibility();
testTakeCapturePreviewInstallerStripsNativeMediaBeforeOriginalOnExecuted();
testTakeCapturePreviewConsumesNativeStoredOutput();
testTakeCapturePreviewNodeGrowthUsesStableFitTarget();
testTakeCapturePreviewPreservesSocketlessWidgetOrder();
testTakeCapturePreviewRepairsPreviouslyShiftedSocketlessValues();
testTakeCapturePreviewMaintainsNodeGrowthDuringDraw();
testTakeCapturePreviewExtensionIsInstalled();
testDeleteContextMenuIsAvailableOnTimelineItems();
testToolbarUsesGroupedIconControls();
testWanSegmentedExecutorSplitStepWidgetSync();
testTimelineStatusBarOverlayIsNotInstalled();
testRendererUsesRealWaveformsOnly();
testWaveformHelpersAdaptAndTrimPeaks();
testViewportMeasurementIgnoresCollapsedChildWidth();

console.log("timeline preview UI tests passed");

function createFakeTakeCaptureNode(overrides = {}) {
  const node = {
    imgs: [{ src: "native" }],
    videoContainer: { source: "native-video" },
    animatedImages: true,
    previewMediaType: "video",
    widgets: [
      { name: "$$canvas-image-preview", onRemove() { node.removedNativeWidgets += 1; } },
      { name: "$$comfy_animation_preview", onRemove() { node.removedNativeWidgets += 1; } },
      { name: "video-preview", onRemove() { node.removedNativeWidgets += 1; } },
      { name: "other" },
    ],
    removedNativeWidgets: 0,
    size: [320, 480],
    setSizes: [],
    computeSize() {
      return [360, 620];
    },
    setSize(nextSize) {
      this.size = nextSize;
      this.setSizes.push(nextSize);
    },
    graph: {
      dirty: [],
      setDirtyCanvas(...args) {
        this.dirty.push(args);
      },
    },
    addDOMWidget(name, label, element, options) {
      const widget = { name, label, element, options, margin: options?.margin };
      this.widgets.push(widget);
      return widget;
    },
  };
  return Object.assign(node, overrides);
}

function createFakeDocument() {
  const elementsById = new Map();
  const head = createFakeElement("head");
  return {
    head,
    createElement(tagName) {
      const element = createFakeElement(tagName);
      if (tagName === "style") {
        Object.defineProperty(element, "id", {
          get() {
            return this._id ?? "";
          },
          set(value) {
            this._id = value;
            elementsById.set(value, this);
          },
        });
      }
      return element;
    },
    getElementById(id) {
      return elementsById.get(id) ?? null;
    },
  };
}

function createFakeElement(tagName) {
  const classes = new Set();
  return {
    tagName,
    children: [],
    hidden: false,
    currentTime: 0,
    pauseCount: 0,
    playCount: 0,
    className: "",
    attributes: {},
    listeners: {},
    classList: {
      toggle(name, force) {
        if (force) classes.add(name);
        else classes.delete(name);
      },
      remove(name) {
        classes.delete(name);
      },
      contains(name) {
        return classes.has(name);
      },
    },
    append(child) {
      this.children.push(child);
    },
    appendChild(child) {
      this.children.push(child);
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    removeAttribute(name) {
      delete this.attributes[name];
    },
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    },
    play() {
      this.playCount += 1;
      return Promise.resolve();
    },
    pause() {
      this.pauseCount += 1;
    },
    load() {},
  };
}
