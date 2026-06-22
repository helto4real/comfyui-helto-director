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
  getTimelineWidgetHeight,
  isDefaultEmptyTimeline,
  measureStableTimelineViewportWidth,
  setLiveItemField,
  waveformPeakCountForWidth,
  waveformPeakRequestForClip,
  waveformPeaksForClip,
} from "../../web/timeline/renderer.js";

function testTimelineHeightIsTripled() {
  const timeline = createDefaultVideoTimeline();

  assert.equal(DIRECTOR_TRACK_HEIGHT, 132);
  assert.equal(
    getTimelineViewportHeight(timeline),
    RULER_HEIGHT + DIRECTOR_TRACK_HEIGHT + AUDIO_LANE_HEIGHT + TIMELINE_VIEWPORT_BORDER_HEIGHT,
  );
  assert.equal(getTimelineWidgetHeight(timeline), 302);
}

function testClearTimelineButtonEnablementHelper() {
  const timeline = createDefaultVideoTimeline();
  timeline.validation = { errors: [], warnings: [], info: [] };
  timeline.ui_state.state_revision = 12;

  assert.equal(isDefaultEmptyTimeline(timeline), true);

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

  assert.equal(getTimelineWidgetHeight(timeline), 528);
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
  assert.equal(rendererSource.includes(".htd-viewport { width: 100%; overflow: hidden;"), true);
  assert.equal(rendererSource.includes("viewport.scrollLeft"), false);
  assert.equal(rendererSource.includes("iconButton(\"text\", \"Add Text Section\""), true);
  assert.equal(rendererSource.includes("iconMenuControl"), true);
  assert.equal(rendererSource.includes("aria-label"), true);
  assert.equal(rendererSource.includes("renderShotBoundaryContext(timeline, shot)"), true);
  assert.equal(rendererSource.includes("renderAssemblyReadiness(timeline, shot)"), true);
  assert.equal(rendererSource.includes("renderRegisterTakeFromMetadata(timeline, shot)"), true);
  assert.equal(rendererSource.includes("Copy Shot ID For Planner Input"), true);
  assert.equal(rendererSource.includes("Attach Generated Video As Take"), true);
  assert.equal(rendererSource.includes("Register Take From Metadata"), true);
  assert.equal(rendererSource.includes("attachPickedGeneratedVideoAsTake(timeline, options.shotId, item)"), true);
  assert.equal(rendererSource.includes("addTakeMetadata(currentTimeline, liveShot.shot_id, nextTake)"), true);
  assert.equal(rendererSource.includes("entry.code === \"BOUNDARY_LORA_STACK_MISMATCH\""), true);
  assert.equal(rendererSource.includes("assetDisplayLabel(asset, privacyRevealed"), true);
  assert.equal(rendererSource.includes("assetSummaryLabel(asset, privacyRevealed)"), true);
  assert.equal(rendererSource.includes("takeSummaryLabel(timeline, take, privacyRevealed)"), true);
  assert.equal(rendererSource.includes("privacyRevealed ? take.take_id : takeStatusLabel(take)"), true);
  assert.equal(rendererSource.includes("privacyRevealed ? `seed ${take.seed}` : \"Seeded\""), true);
  assert.equal(rendererSource.includes("assemblyReadinessStatus(timeline, shot)"), true);
  assert.equal(rendererSource.includes("shotIdInput.readOnly = true"), true);
  assert.equal(rendererSource.includes("shotIdInput.setAttribute(\"aria-label\", \"Shot ID\")"), true);
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
  assert.equal(rendererSource.includes("applyViewportContainerWidth(width)"), true);
  assert.equal(rendererSource.includes("this.container.style.width = `${stableWidth}px`;"), true);
  assert.equal(rendererSource.includes("this.container.style.maxWidth = `${stableWidth}px`;"), true);
  assert.equal(rendererSource.includes("this.container.parentElement.style.width = `${stableWidth}px`;"), true);
  assert.equal(rendererSource.includes("this.container.parentElement.style.maxWidth = `${stableWidth}px`;"), true);
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
  assert.equal(rendererSource.includes("startItemDrag(event"), true);
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
  assert.equal(rendererSource.includes('resizeSection(timeline, this.drag.itemId, "start", pointerTime - this.drag.pointerEdgeTimeOffset)'), true);
  assert.equal(rendererSource.includes('resizeSection(timeline, this.drag.itemId, "end", pointerTime - this.drag.pointerEdgeTimeOffset)'), true);
  assert.equal(rendererSource.includes('resizeAudioClip(timeline, this.drag.itemId, "start", pointerTime - this.drag.pointerEdgeTimeOffset)'), true);
  assert.equal(rendererSource.includes('resizeAudioClip(timeline, this.drag.itemId, "end", pointerTime - this.drag.pointerEdgeTimeOffset)'), true);
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
  assert.equal(rendererSource.includes("Attach Generated Video As Take"), true);
  assert.equal(rendererSource.includes("Choose generated video"), true);
  assert.equal(rendererSource.includes("Clear Media"), false);

  const migrationSource = readFileSync(new URL("../../web/timeline/migration.js", import.meta.url), "utf8");
  assert.equal(migrationSource.includes('normalized.video_guidance_range ??= "Last Frames"'), true);
  assert.equal(migrationSource.includes("normalized.video_guidance_frame_count ??= 17"), true);
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
  assert.equal(rendererSource.includes("timeline.project.privacy.mode && (!this.privacyRevealActive || this.privacyExternalModalOpen)"), true);
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
  assert.equal(rendererSource.includes('settingsButton.classList.add("htd-settings-button")'), true);
  assert.equal(rendererSource.includes('toggleIconButton("native-audio", "Use Native Audio"'), true);
  assert.equal(rendererSource.includes("timeline.project.audio.use_native_audio = !timeline.project.audio.use_native_audio"), true);
  assert.equal(rendererSource.includes('"native-audio": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('references: `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('"reference-active": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('"fit-last-section": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('"fit-all-sections": `<svg viewBox="0 0 24 24">'), true);
  assert.equal(rendererSource.includes('sparkle: `<svg viewBox="0 0 24 24">'), true);
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

  assert.equal(extensionSource.includes('import { api } from "../../scripts/api.js";'), false);
  assert.equal(extensionSource.includes("installTimelineStatusBarBridge"), false);
  assert.equal(extensionSource.includes("helto-timeline-status-bar-bridge"), false);
  assert.equal(extensionSource.includes('apiRef.addEventListener?.("progress_state"'), false);
  assert.equal(extensionSource.includes("latestByNodeId: new Map()"), false);
  assert.equal(extensionSource.includes("aliasesByNodeId: new Map()"), false);
}

function testRendererUsesRealWaveformsOnly() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");

  assert.equal(rendererSource.includes("createWaveformBars"), false);
  assert.equal(rendererSource.includes("requestWaveform?.(asset, peakCount)"), true);
  assert.equal(rendererSource.includes("waveformPeakRequestForClip"), true);
  assert.equal(rendererSource.includes("waveformPeaksForClip"), true);
  assert.equal(rendererSource.includes("is-loading"), true);
  assert.equal(rendererSource.includes("if (shouldShowWaveform(timeline, this.privacyRevealActive))"), true);
  assert.equal(rendererSource.includes("item.style.height = `${AUDIO_LANE_HEIGHT - 8}px`;"), true);
  assert.equal(rendererSource.includes('if (shouldShowWaveform(timeline, this.privacyRevealActive)) item.append(renderWaveform(this.node, timeline, clip, itemWidth));\n    item.append(clipLabel);'), true);
  assert.equal(rendererSource.includes(".htd-audio-label { position: absolute; z-index: 3;"), true);
  assert.equal(rendererSource.includes(".htd-waveform { position: absolute; z-index: 1; inset: 4px 9px;"), true);
  assert.equal(rendererSource.includes('this.renderSettingCheckbox("Privacy Mode", ["project", "privacy", "mode"])'), true);
  assert.equal(rendererSource.includes("Hide Media Previews"), false);
  assert.equal(rendererSource.includes("Hide Text Prompts"), false);
  assert.equal(rendererSource.includes("Encrypt Previews"), false);
  assert.equal(rendererSource.includes("is-private"), true);
  assert.equal(rendererSource.includes("is-privacy-revealed"), true);
  assert.equal(rendererSource.includes("privacyExternalModalOpen"), true);
  assert.equal(rendererSource.includes("is-privacy-modal-open"), true);
  assert.equal(rendererSource.includes("this.privacyRevealActive && !this.privacyExternalModalOpen"), true);
  assert.equal(rendererSource.includes("onClose: () => {"), true);
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
testAudioLanesExpandViewportToContent();
testPromptEditsUpdateLiveSectionAfterStateReplacement();
testInspectorControlsUpdateLiveSectionAfterStateReplacement();
testSectionPreviewUsesContainedRepeatedFrames();
testSharedMediaPreviewSupportsVideoControls();
testDeleteContextMenuIsAvailableOnTimelineItems();
testToolbarUsesGroupedIconControls();
testWanSegmentedExecutorSplitStepWidgetSync();
testTimelineStatusBarOverlayIsNotInstalled();
testRendererUsesRealWaveformsOnly();
testWaveformHelpersAdaptAndTrimPeaks();
testViewportMeasurementIgnoresCollapsedChildWidth();

console.log("timeline preview UI tests passed");
