import {
  AUDIO_NORMALIZATION_MODES,
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  BOUNDARY_MODES,
  CROP_MODES,
  GLOBAL_PROMPT_POSITIONS,
  LORA_MERGE_MODES,
  SECTION_EDIT_MODES,
  SNAP_MODES,
  SHOT_TYPES,
  TAKE_STATUSES,
  TIMELINE_DISPLAY_MODES,
  VIDEO_GUIDANCE_FRAME_COUNTS,
  VIDEO_GUIDANCE_RANGES,
  VIDEO_TIMING_MODES,
  createDefaultVideoTimeline,
  deepClone,
  modelLoraTargetDescriptors,
} from "./schema.js";
import {
  mediaLabel,
  resolveMediaReference,
} from "./media.js";
import { MAX_WAVEFORM_PEAKS, MIN_WAVEFORM_PEAKS } from "./media_cache.js";
import {
  deleteProjectTakeCapture,
  fetchProjectTakeCaptures,
} from "./media_actions.js";
import {
  cloneProjectForDirectorLibrary,
  confirmProjectUpdate,
  showDirectorLibrary,
} from "./library.js";
import { showMediaPicker } from "./media_picker.js";
import { showMediaPreview } from "./media_preview.js";
import { showPromptOptimizer } from "./prompt_optimizer.js";
import { htdScrollbarBlock, htdTokenBlock } from "./design_tokens.js";
import {
  PROMPT_REFERENCE_TRIGGER,
  addCharacterReference,
  applyReferencePromptCompletion,
  areCharacterReferencesEnabled,
  ensureCharacterReferences,
  filterReferencePromptCompletions,
  formatCharacterReferenceTag,
  getCharacterReferences,
  getReferencePromptCompletions,
  referencePromptCompletionContext,
  removeCharacterReference,
} from "./references.js";
import {
  AUDIO_LANE_HEIGHT,
  DIRECTOR_TRACK_HEIGHT,
  HANDLE_WIDTH,
  RANGE_CONTROL_HEIGHT,
  RULER_HEIGHT,
  TIMELINE_RIGHT_PADDING,
  TIMELINE_WIDTH,
  clampTimelineViewRange,
  durationToPixels,
  getProjectWholeSeconds,
  getTimelineViewportHeight,
  getTimelineViewRange,
  getTimelineWidth,
  secondsToPixels,
  timeFromClientX,
} from "./geometry.js";
import {
  globalAssetRootLabel,
  isGlobalPrivacyMode,
  normalizeGlobalSettings,
} from "./global_settings.js";
import {
  acceptTake,
  addAudioClip,
  attachVideoAssetAsTake,
  addSection,
  adjacentShotPairs,
  canFitLastDirectorSectionToDuration,
  changeShotType,
  clearProjectModelLoraStack,
  clearShotLoraOverride,
  clearShotLoraTargetStack,
  createOrUpdateBoundaryBetweenShots,
  deleteTakesByAssetPath,
  deleteSelectedItem,
  duplicateSelectedSection,
  fitDirectorSectionsEvenlyToDuration,
  fitLastDirectorSectionToDuration,
  findBoundary,
  findBoundaryBetweenShots,
  findSection,
  findShot,
  findShotForSection,
  getSelectedItemIds,
  hasDirectorSectionOverflow,
  insertShotAfterCurrent,
  isItemSelected,
  canMoveBareShot,
  moveSelectedItems,
  renameShot,
  resizeAudioClip,
  resizeSection,
  selectItem,
  selectItemRange,
  setClipInstanceFromAsset,
  setProjectModelLoraStack,
  setShotLoraMergeMode,
  setShotLoraTargetStack,
  setTakeStatus,
  splitSelectedSection,
  toggleSelectItem,
  zoomToFit,
} from "./operations.js";
import {
  loraEditorProfileForTarget,
  showTimelineLoraStackEditor,
} from "./lora_editor.js";

const TOOLBAR_HEIGHT = 34;
const INSPECTOR_HEIGHT = 34;
const INSPECTOR_EDITOR_HEIGHT = 260;
const ROOT_GAP = 6;
const NODE_BODY_HORIZONTAL_PADDING = 20;
const NODE_BODY_BOTTOM_PADDING = NODE_BODY_HORIZONTAL_PADDING;
const CLEAR_TIMELINE_CONFIRMATION = "Clear current timeline? This will replace the current timeline with a new blank timeline and remove its Director Library link. Saved library items and media files will not be deleted.";
const DELETE_MENU_LABELS = {
  Image: "Delete Image",
  Video: "Delete Video",
  Text: "Delete Text",
  "Audio Clip": "Delete Audio Clip",
};
const DUPLICATE_MENU_LABELS = {
  Image: "Duplicate Image",
  Video: "Duplicate Video",
  Text: "Duplicate Text",
  "Audio Clip": "Duplicate Audio Clip",
};
const REPLACE_MENU_LABELS = {
  Image: "Replace image",
  Video: "Replace video",
};

export function getTimelineWidgetHeight(timeline) {
  return TOOLBAR_HEIGHT + RANGE_CONTROL_HEIGHT + getTimelineViewportHeight(timeline) + getInspectorHeight(timeline) + ROOT_GAP * 3;
}

export function getTimelineWidgetRenderedHeight(node, widget, timeline, contentHeight = getTimelineWidgetHeight(timeline)) {
  return Math.max(contentHeight, timelineWidgetAvailableHeight(node, widget));
}

export function getTimelineNodeMinimumHeight(node, widget, timeline, contentHeight = getTimelineWidgetHeight(timeline)) {
  const widgetY = timelineWidgetY(widget);
  if (!widgetY) return contentHeight;
  return widgetY + contentHeight + NODE_BODY_BOTTOM_PADDING;
}

export function ensureTimelineNodeFitsContent(node, widget, timeline, contentHeight = getTimelineWidgetHeight(timeline), appRef = null) {
  const currentHeight = positiveNumber(node?.size?.[1]);
  const minimumHeight = getTimelineNodeMinimumHeight(node, widget, timeline, contentHeight);
  if (!currentHeight || currentHeight + 1 >= minimumHeight) return false;

  const currentWidth = positiveNumber(node?.size?.[0]) || TIMELINE_WIDTH + NODE_BODY_HORIZONTAL_PADDING;
  const nextSize = [currentWidth, Math.ceil(minimumHeight)];
  if (typeof node?.setSize === "function") {
    node.setSize(nextSize);
  } else if (Array.isArray(node?.size)) {
    node.size[0] = nextSize[0];
    node.size[1] = nextSize[1];
  }
  appRef?.graph?.setDirtyCanvas?.(true, true);
  node?.graph?.setDirtyCanvas?.(true, true);
  return true;
}

export function measureStableTimelineViewportWidth(node, container) {
  const viewport = container?.querySelector?.(".htd-viewport");
  const viewportWidth = positiveNumber(viewport?.clientWidth);
  const nodeWidth = nodeBodyWidth(node);
  if (nodeWidth > 0) return Math.max(1, nodeWidth);
  const stableWidth = nodeWidth || Math.max(
    elementLayoutWidth(container?.parentElement),
    elementLayoutWidth(container),
  );
  if (stableWidth > 0) {
    return Math.max(1, viewportWidth >= stableWidth ? viewportWidth : stableWidth);
  }
  return Math.max(1, viewportWidth || TIMELINE_WIDTH);
}

export function setLiveItemField(timeline, item, field, value) {
  const liveItem = resolveLiveTimelineItem(timeline, item) ?? item;
  liveItem[field] = value;
  return liveItem;
}

export function isDefaultEmptyTimeline(timeline) {
  return timelineComparisonPayload(timeline) === timelineComparisonPayload(createDefaultVideoTimeline());
}

export class TimelineRenderer {
  constructor(node, app, controller, container, widget = null) {
    this.node = node;
    this.app = app;
    this.controller = controller;
    this.container = container;
    this.widget = widget;
    this.drag = null;
    this.globalSettingsOpen = false;
    this.projectSettingsOpen = false;
    this.globalSettingsDraft = null;
    this.projectSettingsDraft = null;
    this.referencesOpen = false;
    this.openMenu = null;
    this.contextMenuElement = null;
    this.contextMenuDocument = null;
    this.remeasureHandle = null;
    this.resizeObserver = null;
    this.observedWidth = null;
    this.viewportWidth = TIMELINE_WIDTH;
    this.renderedHeight = 0;
    this.contentHeight = getTimelineWidgetHeight(controller.timeline);
    this.privacyRevealActive = false;
    this.privacyExternalModalOpen = false;
    this.captureModalShotId = "";
    this.shotDetailsOpen = {};
    this.availableCaptures = {
      key: "",
      loading: false,
      error: "",
      items: [],
    };
    this.onContextMenuPointerDown = (event) => this.handleContextMenuPointerDown(event);
    this.onContextMenuKeyDown = (event) => this.handleContextMenuKeyDown(event);
    this.onPrivacyPointerEnter = () => this.setPrivacyRevealActive(true);
    this.onPrivacyPointerLeave = () => {
      if (!this.contextMenuElement) this.setPrivacyRevealActive(false);
    };
    this.container.className = "helto-timeline-director";
    installStyles(container.ownerDocument ?? globalThis.document);
    this.container.addEventListener?.("pointerenter", this.onPrivacyPointerEnter);
    this.container.addEventListener?.("pointerleave", this.onPrivacyPointerLeave);
    this.controller.setTimelineKeyboardScope?.(this.container);
    this.render(controller.timeline);
    this.startResizeObserver();
  }

  destroy() {
    this.controller.setTimelineKeyboardScope?.(null);
    this.closeContextMenu({ rerender: false });
    this.cancelViewportRemeasure();
    this.stopResizeObserver();
    this.container.removeEventListener?.("pointerenter", this.onPrivacyPointerEnter);
    this.container.removeEventListener?.("pointerleave", this.onPrivacyPointerLeave);
    this.container.replaceChildren();
  }

  render(timeline = this.controller.timeline) {
    if (!timeline) return this.renderLocked();
    this.closeContextMenu({ rerender: false });
    this.contentHeight = getTimelineWidgetHeight(timeline);
    this.ensureNodeFitsContent(timeline);
    this.viewportWidth = this.measureViewportWidth();
    this.applyViewportContainerWidth(this.viewportWidth);
    const renderedHeight = this.getRenderedHeight(timeline);
    this.applyWidgetContainerHeight(renderedHeight, this.contentHeight);
    this.container.replaceChildren();
    const root = el("div", "htd-root");
    root.style.width = `${this.viewportWidth}px`;
    const privacyMode = this.isGlobalPrivacyMode();
    const privacyRevealed = !privacyMode || (this.privacyRevealActive && !this.privacyExternalModalOpen);
    root.classList.toggle("is-private", privacyMode);
    root.classList.toggle("is-privacy-modal-open", this.privacyExternalModalOpen);
    root.classList.toggle("is-privacy-revealed", privacyRevealed);
    root.append(
      this.renderToolbar(),
      this.renderRangeControl(timeline),
      this.renderTimeline(timeline),
      this.renderInspector(timeline, getRenderedInspectorHeight(timeline, renderedHeight)),
    );
    if (this.controller.privacyError) {
      const status = el("div", "htd-privacy-status");
      status.textContent = this.controller.privacyError;
      root.append(status);
    }
    if (this.captureModalShotId) {
      const modalShot = findShot(timeline, this.captureModalShotId);
      if (modalShot) {
        root.append(this.renderCaptureManagementModal(timeline, modalShot));
      } else {
        this.captureModalShotId = "";
      }
    }
    if (this.globalSettingsOpen) root.append(this.renderGlobalSettings(timeline));
    if (this.projectSettingsOpen) root.append(this.renderProjectSettings(timeline));
    if (this.referencesOpen) root.append(this.renderReferenceManager(timeline));
    this.container.append(root);
    this.updateMeasuredContentHeight(timeline);
    this.scheduleViewportRemeasure();
  }

  renderLocked() {
    this.closeContextMenu({ rerender: false });
    this.cancelViewportRemeasure();
    this.contentHeight = TOOLBAR_HEIGHT + ROOT_GAP * 2;
    this.viewportWidth = this.measureViewportWidth();
    this.applyViewportContainerWidth(this.viewportWidth);
    this.applyWidgetContainerHeight(this.contentHeight, this.contentHeight);
    this.container.replaceChildren();
    const root = el("div", "htd-root is-private");
    root.style.width = `${this.viewportWidth}px`;
    const status = el("div", "htd-privacy-status");
    status.textContent = this.controller.privacyError || "Private timeline locked";
    status.setAttribute("role", "status");
    root.append(status);
    this.container.append(root);
  }

  setPrivacyRevealActive(active) {
    const next = Boolean(active);
    if (next === this.privacyRevealActive) return;
    this.privacyRevealActive = next;
    if (this.isGlobalPrivacyMode()) this.render();
  }

  isPrivacyRevealed(timeline = this.controller.timeline) {
    return !this.isGlobalPrivacyMode() || (this.privacyRevealActive && !this.privacyExternalModalOpen);
  }

  isGlobalPrivacyMode() {
    return isGlobalPrivacyMode(this.controller.globalSettings);
  }

  globalSettings() {
    return normalizeGlobalSettings(this.controller.globalSettings);
  }

  openGlobalSettings() {
    this.globalSettingsDraft = deepClone(this.globalSettings());
    this.globalSettingsOpen = true;
    this.projectSettingsOpen = false;
    this.render();
  }

  openProjectSettings() {
    this.projectSettingsDraft = deepClone(this.controller.timeline);
    this.projectSettingsOpen = true;
    this.globalSettingsOpen = false;
    this.render();
  }

  cancelGlobalSettings() {
    this.globalSettingsDraft = null;
    this.globalSettingsOpen = false;
    this.render();
  }

  cancelProjectSettings() {
    this.projectSettingsDraft = null;
    this.projectSettingsOpen = false;
    this.render();
  }

  saveProjectSettings() {
    const draftProject = deepClone((this.projectSettingsDraft ?? this.controller.timeline).project);
    this.projectSettingsDraft = null;
    this.projectSettingsOpen = false;
    this.commitMutation((timeline) => {
      timeline.project = draftProject;
    }, "project settings change");
  }

  async saveGlobalSettings(control = null) {
    const draft = deepClone(this.globalSettingsDraft ?? this.globalSettings());
    await withDisabledControl(control, async () => {
      try {
        await this.controller.updateGlobalSettings((settings) => replaceObject(settings, draft));
        this.globalSettingsDraft = null;
        this.globalSettingsOpen = false;
        this.render();
      } catch (error) {
        this.controller.globalSettingsError = error.message;
        this.render();
      }
    });
  }

  renderToolbar() {
    const toolbar = el("div", "htd-toolbar");
    const hasOverflow = hasDirectorSectionOverflow(this.controller.timeline);
    const referenceCount = getCharacterReferences(this.controller.timeline).length;
    const referencesEnabled = areCharacterReferencesEnabled(this.controller.timeline);
    const settingsButton = iconButton("settings", "Global Settings", () => this.openGlobalSettings());
    const projectSettingsButton = iconButton("project-settings", "Project Settings", () => this.openProjectSettings());
    const referenceManagerButton = iconButton("references", "Manage Character References", () => this.openReferenceManager());
    const referenceToggleTitle = referenceCount
      ? referencesEnabled
        ? `${referenceCount} Character References Enabled`
        : `${referenceCount} Character References Disabled`
      : "No Character References";
    const referencePresentButton = iconButton("reference-active", referenceToggleTitle, () => this.toggleCharacterReferences(), { disabled: referenceCount === 0 });
    const promptOptimizerButton = iconButton("sparkle", "Prompt Optimizer", () => this.openPromptOptimizer());
    const clearTimelineButton = iconButton("timeline-clear", "Clear Current Timeline", () => this.clearCurrentTimeline(), {
      disabled: isDefaultEmptyTimeline(this.controller.timeline),
    });
    const selectedIds = getSelectedItemIds(this.controller.timeline);
    const selectedSection = selectedIds.some((itemId) => findSection(this.controller.timeline, itemId));
    const deleteButton = iconButton("delete", "Delete", () => this.commitMutation((timeline) => deleteSelectedItem(timeline), "delete"));
    const projectLibraryItemId = projectLibraryItemIdFor(this.controller.timeline);
    const projectLibraryButton = iconButton(
      projectLibraryItemId ? "library-update" : "library-add",
      projectLibraryItemId ? "Update Project" : "Save Project",
      async () => this.saveCurrentProjectToLibrary(projectLibraryButton),
    );
    const repairButtons = hasOverflow
      ? [
          iconButton("fit-last-section", "Fit Last Section", () => {
            this.commitMutation((timeline) => fitLastDirectorSectionToDuration(timeline), "fit last section");
          }, { disabled: !canFitLastDirectorSectionToDuration(this.controller.timeline) }),
          iconButton("fit-all-sections", "Fit All Sections Evenly", () => {
            this.commitMutation((timeline) => fitDirectorSectionsEvenlyToDuration(timeline), "fit all sections evenly");
          }),
        ]
      : [];
    promptOptimizerButton.classList.add("htd-prompt-optimizer-button");
    clearTimelineButton.classList.add("htd-clear-timeline-button", "is-danger");
    deleteButton.classList.toggle("is-danger", Boolean(selectedSection));
    projectLibraryButton.classList.add("htd-project-library-save-button");
    projectLibraryButton.classList.toggle("is-active", Boolean(projectLibraryItemId));
    referenceManagerButton.classList.add("htd-reference-manager-button");
    referencePresentButton.classList.add("htd-reference-present-button");
    referencePresentButton.classList.toggle("is-active", referenceCount > 0 && referencesEnabled);
    referencePresentButton.setAttribute("aria-pressed", referenceCount > 0 && referencesEnabled ? "true" : "false");
    settingsButton.classList.add("htd-settings-button");
    projectSettingsButton.classList.add("htd-project-settings-button");
    toolbar.append(
      iconButton("text", "Add Text Section", () => this.commitMutation((timeline) => addSection(timeline, "Text"), "add")),
      iconButton("image", "Add Image Section", () => this.openMediaPicker(ASSET_TYPE_IMAGE)),
      iconButton("video", "Add Video Section", () => this.openMediaPicker(ASSET_TYPE_VIDEO)),
      iconButton("audio", "Add Audio Clip", () => this.openMediaPicker(ASSET_TYPE_AUDIO)),
      iconButton("shot", "Add Shot", () => this.commitMutation((timeline) => insertShotAfterCurrent(timeline, { globalSettings: this.globalSettings() }), "add shot")),
      toolbarSpacer(),
      this.renderToolbarMenu("display", "Display Mode", "layers", this.controller.timeline.ui_state.timeline_display_mode, TIMELINE_DISPLAY_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.timeline_display_mode = value; }, "settings change");
      }),
      this.renderToolbarMenu("edit", "Edit Mode", "trim", this.controller.timeline.ui_state.section_edit_mode, SECTION_EDIT_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.section_edit_mode = value; }, "settings change");
      }),
      this.renderToolbarMenu("snap", "Snap Mode", "magnet", this.controller.timeline.ui_state.snap_mode, SNAP_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.snap_mode = value; }, "settings change");
      }),
      toolbarSpacer(),
      toggleIconButton("global", "Use Global Prompt", this.controller.timeline.project.global_prompt.enabled, () => {
        this.commitMutation((timeline) => {
          timeline.project.global_prompt.enabled = !timeline.project.global_prompt.enabled;
        }, "settings change");
      }),
      toggleIconButton("native-audio", "Use Native Audio", this.controller.timeline.project.audio.use_native_audio, () => {
        this.commitMutation((timeline) => {
          timeline.project.audio.use_native_audio = !timeline.project.audio.use_native_audio;
        }, "settings change");
      }),
      toolbarSpacer(),
      iconButton("library", "Director Library", () => this.openDirectorLibrary()),
      projectLibraryButton,
      clearTimelineButton,
      toolbarSpacer(),
      projectSettingsButton,
      toolbarSpacer(),
      referenceManagerButton,
      referencePresentButton,
      toolbarSpacer(),
      iconButton("split", "Split", () => this.commitMutation((timeline) => splitSelectedSection(timeline), "split")),
      iconButton("duplicate", "Duplicate", () => this.commitMutation((timeline) => duplicateSelectedSection(timeline), "duplicate")),
      deleteButton,
      ...repairButtons,
      toolbarSpacer(),
      iconButton("fit", "Zoom to Fit", () => this.handleZoomToFit()),
      promptOptimizerButton,
      settingsButton,
    );
    return toolbar;
  }

  clearCurrentTimeline() {
    if (isDefaultEmptyTimeline(this.controller.timeline)) return false;
    const confirmFn = this.container.ownerDocument?.defaultView?.confirm ?? globalThis.confirm;
    if (confirmFn && !confirmFn(CLEAR_TIMELINE_CONFIRMATION)) return false;
    this.controller.replaceTimeline(createDefaultVideoTimeline(), "clear current timeline", {
      flushReason: "clear timeline flush",
    });
    return true;
  }

  renderToolbarMenu(id, title, iconName, value, options, onChange) {
    return iconMenuControl({
      id,
      title,
      iconName,
      value,
      options,
      open: this.openMenu === id,
      onToggle: () => {
        this.openMenu = this.openMenu === id ? null : id;
        this.render();
      },
      onChange: (nextValue) => {
        this.openMenu = null;
        onChange(nextValue);
      },
    });
  }

  renderTimeline(timeline) {
    const viewport = el("div", "htd-viewport");
    viewport.style.height = `${getTimelineViewportHeight(timeline)}px`;

    const width = getTimelineWidth(timeline, this.viewportWidth);
    const stage = el("div", "htd-stage");
    stage.style.width = `${width}px`;
    stage.append(this.renderRuler(timeline, width), this.renderDirectorTrack(timeline), this.renderAudioTracks(timeline));
    viewport.append(stage);
    return viewport;
  }

  renderRangeControl(timeline) {
    const range = getTimelineViewRange(timeline);
    const projectSeconds = getProjectWholeSeconds(timeline);
    const row = el("div", "htd-range-control");
    row.title = `Visible range ${range.start}s to ${range.end}s`;
    const leftGutter = el("div", "htd-range-gutter");
    const bar = el("div", "htd-range-bar");
    bar.setAttribute("aria-label", "Timeline visible range");
    bar.setAttribute("role", "slider");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", String(projectSeconds));
    bar.setAttribute("aria-valuetext", `${range.start}s to ${range.end}s`);
    const active = el("div", "htd-range-active");
    active.style.left = `${(range.start / projectSeconds) * 100}%`;
    active.style.width = `${((range.end - range.start) / projectSeconds) * 100}%`;
    const startHandle = el("div", "htd-range-handle htd-range-start");
    startHandle.title = "Visible Start";
    const endHandle = el("div", "htd-range-handle htd-range-end");
    endHandle.title = "Visible End";
    startHandle.addEventListener("pointerdown", (event) => this.startRangeDrag(event, "range-start", bar));
    endHandle.addEventListener("pointerdown", (event) => this.startRangeDrag(event, "range-end", bar));
    bar.addEventListener("pointerdown", (event) => {
      if (event.target !== bar && event.target !== active) return;
      const second = rangeSecondFromClientX(event.clientX, bar, timeline);
      const startDistance = Math.abs(second - range.start);
      const endDistance = Math.abs(second - range.end);
      this.startRangeDrag(event, startDistance <= endDistance ? "range-start" : "range-end", bar);
      setTimelineRangeBoundary(timeline, this.drag.mode, second);
      this.render(timeline);
      this.drag.bar = this.container.querySelector(".htd-range-bar") ?? this.drag.bar;
    });
    active.append(startHandle, endHandle);
    bar.append(active);
    row.append(leftGutter, bar);
    return row;
  }

  renderRuler(timeline, width) {
    const ruler = el("div", "htd-ruler");
    ruler.style.height = `${RULER_HEIGHT}px`;
    const range = getTimelineViewRange(timeline);
    for (let second = range.start; second <= range.end; second += 1) {
      const tick = el("div", second === range.end ? "htd-tick htd-end-tick" : "htd-tick");
      tick.style.left = `${secondsToPixels(second, timeline, this.viewportWidth)}px`;
      tick.textContent = `${second}s`;
      ruler.append(tick);
    }
    const visibleEnd = el("div", "htd-project-end");
    visibleEnd.style.left = `${secondsToPixels(range.end, timeline, this.viewportWidth)}px`;
    visibleEnd.style.width = `${TIMELINE_RIGHT_PADDING}px`;
    ruler.append(visibleEnd);
    const playhead = el("div", "htd-playhead");
    playhead.style.left = `${secondsToPixels(timeline.ui_state.playhead_time ?? 0, timeline, this.viewportWidth)}px`;
    ruler.append(playhead);
    ruler.addEventListener("pointerdown", (event) => {
      timeline.ui_state.playhead_time = timeFromClientX(event.clientX, ruler, timeline, this.viewportWidth);
      this.controller.commitTimelineChange("playhead", { pushUndo: false });
    });
    ruler.style.width = `${width}px`;
    return ruler;
  }

  renderDirectorTrack(timeline) {
    const track = el("div", "htd-track htd-director-track");
    track.style.height = `${DIRECTOR_TRACK_HEIGHT}px`;
    track.append(trackLabel("director", "Director"));

    for (const gap of computeGaps(timeline)) {
      const item = el("div", "htd-gap");
      item.style.left = `${secondsToPixels(gap.start_time, timeline, this.viewportWidth)}px`;
      item.style.width = `${durationToPixels(gap.end_time - gap.start_time, timeline, this.viewportWidth)}px`;
      item.title = "No Guidance";
      track.append(item);
    }

    for (const shot of timeline.sequence?.shots ?? []) {
      track.append(this.renderShotBand(timeline, shot));
    }
    for (const [leftShot, rightShot] of adjacentShotPairs(timeline)) {
      track.append(this.renderBoundaryControl(timeline, leftShot, rightShot));
    }
    for (const section of timeline.director_track.sections) {
      track.append(this.renderSection(timeline, section));
    }
    return track;
  }

  renderShotBand(timeline, shot) {
    const item = el("div", "htd-item htd-shot-band");
    item.tabIndex = -1;
    item.dataset.itemId = shot.shot_id;
    item.style.left = `${secondsToPixels(shot.start_time, timeline, this.viewportWidth)}px`;
    item.style.width = `${Math.max(18, durationToPixels(shot.end_time - shot.start_time, timeline, this.viewportWidth))}px`;
    const label = shotDisplayLabel(timeline, shot);
    item.textContent = label;
    const takes = shot.takes ?? [];
    const latestTake = latestAttachedTake(shot);
    let title = `${label} (${shot.type})`;
    if (latestTake) {
      const badge = el("span", "htd-shot-take-badge");
      badge.classList.toggle("is-accepted", Boolean(shot.accepted_take_id));
      badge.textContent = `T${takes.length}`;
      badge.setAttribute("aria-hidden", "true");
      item.append(badge);
      const latestStatus = latestTake.status ?? "Candidate";
      title += ` - ${takes.length} ${takes.length === 1 ? "take" : "takes"}, latest ${latestStatus}`;
    }
    item.title = title;
    item.setAttribute("aria-label", item.title);
    const movableShot = canMoveBareShot(timeline, shot.shot_id);
    item.classList.toggle("is-bare-shot", movableShot);
    item.classList.toggle("is-selected", isItemSelected(timeline, shot.shot_id));
    item.classList.toggle("is-primary-selected", timeline.ui_state.selected_item_id === shot.shot_id);
    if (movableShot) {
      item.addEventListener("pointerdown", (event) => this.startShotDrag(event, shot));
    } else {
      item.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.commitMutation((currentTimeline) => selectItem(currentTimeline, shot.shot_id), "select", { pushUndo: false });
        this.focusTimelineItem(shot.shot_id, item);
      });
    }
    return item;
  }

  renderBoundaryControl(timeline, leftShot, rightShot) {
    const boundary = findBoundaryBetweenShots(timeline, leftShot.shot_id, rightShot.shot_id);
    const id = boundary?.boundary_id ?? `boundary-${leftShot.shot_id}-to-${rightShot.shot_id}`;
    const wrapper = iconMenuControl({
      id,
      title: "Boundary Mode",
      iconName: "boundary",
      value: boundary?.mode ?? "Hard Cut",
      options: BOUNDARY_MODES,
      open: this.openMenu === id,
      onToggle: () => {
        this.openMenu = this.openMenu === id ? null : id;
        if (boundary) {
          this.commitMutation((currentTimeline) => selectItem(currentTimeline, boundary.boundary_id), "select", { pushUndo: false });
        } else {
          this.render();
        }
      },
      onChange: (nextValue) => {
        this.openMenu = null;
        this.commitMutation((currentTimeline) => {
          const liveBoundary = createOrUpdateBoundaryBetweenShots(currentTimeline, leftShot.shot_id, rightShot.shot_id, { mode: nextValue });
          if (liveBoundary) selectItem(currentTimeline, liveBoundary.boundary_id);
        }, "boundary change");
      },
    });
    wrapper.classList.add("htd-boundary-control");
    wrapper.dataset.itemId = boundary?.boundary_id ?? id;
    wrapper.classList.toggle("is-selected", Boolean(boundary && isItemSelected(timeline, boundary.boundary_id)));
    wrapper.classList.toggle("is-primary-selected", Boolean(boundary && timeline.ui_state.selected_item_id === boundary.boundary_id));
    wrapper.style.left = `${secondsToPixels(rightShot.start_time, timeline, this.viewportWidth)}px`;
    wrapper.title = `${shotDisplayLabel(timeline, leftShot)} to ${shotDisplayLabel(timeline, rightShot)}`;
    return wrapper;
  }

  renderSection(timeline, section) {
    const item = el("div", `htd-item htd-section htd-${section.type.toLowerCase()}`);
    item.tabIndex = -1;
    item.dataset.itemId = section.item_id;
    if (isItemSelected(timeline, section.item_id)) item.classList.add("is-selected");
    if (timeline.ui_state.selected_item_id === section.item_id) item.classList.add("is-primary-selected");
    item.style.left = `${secondsToPixels(section.start_time, timeline, this.viewportWidth)}px`;
    const itemWidth = Math.max(12, durationToPixels(section.end_time - section.start_time, timeline, this.viewportWidth));
    item.style.width = `${itemWidth}px`;
    const thumbnail = sectionThumbnailUrl(this.node, timeline, section, this.privacyRevealActive, this.globalSettings());
    if (thumbnail) {
      item.classList.add("has-preview");
      item.append(renderSectionPreview(timeline, thumbnail, itemWidth));
    }
    const labelText = sectionLabel(timeline, section, this.globalSettings());
    const labelElement = el("span", "htd-section-label");
    labelElement.textContent = labelText;
    item.append(labelElement);
    item.title = labelText;
    item.setAttribute("aria-label", labelText || `${section.type} section`);
    item.addEventListener("pointerdown", (event) => this.startSectionDrag(event, section, "move"));
    item.addEventListener("contextmenu", (event) => this.showItemContextMenu(event, section.item_id, section.type));
    if (section.type === ASSET_TYPE_IMAGE || section.type === ASSET_TYPE_VIDEO) {
      item.addEventListener("dblclick", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.openMediaPicker(section.type, { mode: "replace", itemId: section.item_id });
      });
    }

    const leftHandle = el("div", "htd-handle htd-left");
    leftHandle.style.width = `${HANDLE_WIDTH}px`;
    leftHandle.addEventListener("pointerdown", (event) => this.startSectionDrag(event, section, "start"));
    const rightHandle = el("div", "htd-handle htd-right");
    rightHandle.style.width = `${HANDLE_WIDTH}px`;
    rightHandle.addEventListener("pointerdown", (event) => this.startSectionDrag(event, section, "end"));
    item.append(leftHandle, rightHandle);
    return item;
  }

  renderAudioTracks(timeline) {
    const wrapper = el("div", "htd-audio");
    const tracks = timeline.audio_tracks.length ? timeline.audio_tracks : [{ track_id: "audio_track_001", clips: [] }];
    for (const trackData of tracks) {
      const maxLane = Math.max(0, ...trackData.clips.map((clip) => Number(clip.lane ?? 0)));
      const track = el("div", "htd-track htd-audio-track");
      track.style.height = `${(maxLane + 1) * AUDIO_LANE_HEIGHT}px`;
      track.append(trackLabel("audio", "Audio"));
      for (const clip of trackData.clips) track.append(this.renderAudioClip(timeline, clip));
      wrapper.append(track);
    }
    return wrapper;
  }

  renderAudioClip(timeline, clip) {
    const item = el("div", "htd-item htd-audio-clip");
    item.tabIndex = -1;
    item.dataset.itemId = clip.item_id;
    if (isItemSelected(timeline, clip.item_id)) item.classList.add("is-selected");
    if (timeline.ui_state.selected_item_id === clip.item_id) item.classList.add("is-primary-selected");
    item.style.left = `${secondsToPixels(clip.start_time, timeline, this.viewportWidth)}px`;
    item.style.top = `${Number(clip.lane ?? 0) * AUDIO_LANE_HEIGHT + 4}px`;
    item.style.height = `${AUDIO_LANE_HEIGHT - 8}px`;
    const itemWidth = Math.max(12, durationToPixels(clip.end_time - clip.start_time, timeline, this.viewportWidth));
    item.style.width = `${itemWidth}px`;
    const clipLabel = el("div", "htd-audio-label");
    clipLabel.textContent = clip.name || mediaLabel(timeline, clip.audio, "Audio");
    if (shouldShowWaveform(timeline, this.privacyRevealActive, this.globalSettings())) item.append(renderWaveform(this.node, timeline, clip, itemWidth));
    item.append(clipLabel);
    item.title = "Audio";
    item.setAttribute("aria-label", clip.name || mediaLabel(timeline, clip.audio, "Audio"));
    item.addEventListener("pointerdown", (event) => this.startAudioDrag(event, clip, "audio-move"));
    item.addEventListener("contextmenu", (event) => this.showItemContextMenu(event, clip.item_id, "Audio Clip"));

    const leftHandle = el("div", "htd-handle htd-left");
    leftHandle.style.width = `${HANDLE_WIDTH}px`;
    leftHandle.addEventListener("pointerdown", (event) => this.startAudioDrag(event, clip, "audio-start"));
    const rightHandle = el("div", "htd-handle htd-right");
    rightHandle.style.width = `${HANDLE_WIDTH}px`;
    rightHandle.addEventListener("pointerdown", (event) => this.startAudioDrag(event, clip, "audio-end"));
    item.append(leftHandle, rightHandle);
    return item;
  }

  renderInspector(timeline, renderedHeight = getInspectorHeight(timeline)) {
    const inspector = el("div", "htd-inspector");
    inspector.style.height = `${Math.max(getInspectorHeight(timeline), renderedHeight)}px`;
    const selected = timeline.director_track.sections.find((section) => section.item_id === timeline.ui_state.selected_item_id);
    const selectedAudio = findAudioClip(timeline, timeline.ui_state.selected_item_id);
    const selectedShot = findShot(timeline, timeline.ui_state.selected_item_id);
    const selectedBoundary = findBoundary(timeline, timeline.ui_state.selected_item_id);
    const activeShot = selectedShot ?? (selected ? findShotForSection(timeline, selected.item_id) : null);
    inspector.classList.toggle("has-selection", Boolean(selected || selectedAudio || selectedShot || selectedBoundary));
    if (!selected && !selectedAudio && !selectedShot && !selectedBoundary) return inspector;

    const panel = el("div", "htd-inspector-panel");
    if (selected?.type === ASSET_TYPE_IMAGE) {
      panel.classList.add("is-section-inspector");
      panel.append(
        this.renderSectionInspectorHeader(timeline, selected, activeShot, "Image Section"),
        this.renderInspectorControlRow(
          this.renderInspectorCompactField("Guide Strength:", this.renderGuideStrengthField(selected), "is-strength"),
          this.renderInspectorCompactField("Crop Mode:", this.renderIconSelectField(selected, "crop_mode", "Crop Mode", CROP_MODES, "crop")),
        ),
        this.renderPromptRow(selected),
      );
    } else if (selected?.type === ASSET_TYPE_VIDEO) {
      panel.classList.add("is-section-inspector");
      panel.append(
        this.renderSectionInspectorHeader(timeline, selected, activeShot, "Video Section"),
        this.renderInspectorControlRow(
          this.renderInspectorCompactField("Guide Strength:", this.renderGuideStrengthField(selected), "is-strength"),
          this.renderInspectorCompactField("Crop Mode:", this.renderIconSelectField(selected, "crop_mode", "Crop Mode", CROP_MODES, "crop")),
          this.renderInspectorCompactField("Timing Mode:", this.renderIconSelectField(selected, "timing_mode", "Timing Mode", VIDEO_TIMING_MODES, "timing")),
          this.renderInspectorCompactField("Guidance Range:", this.renderIconSelectField(selected, "video_guidance_range", "Guidance Range", VIDEO_GUIDANCE_RANGES, "guide-range")),
        ),
        this.renderInspectorControlRow(
          this.renderInspectorCompactField("Source In:", this.renderNumberField(selected, "source_in", "Source In", { min: 0, step: 0.05 })),
          this.renderInspectorCompactField("Source Out:", this.renderNumberField(selected, "source_out", "Source Out", { min: 0, step: 0.05, allowNull: true })),
          selected.video_guidance_range === "Last Frames"
            ? this.renderInspectorCompactField("Guide Frames:", this.renderIconSelectField(selected, "video_guidance_frame_count", "Guide Frames", VIDEO_GUIDANCE_FRAME_COUNTS, "guide-frames"))
            : null,
        ),
        this.renderPromptRow(selected),
      );
    } else if (selected?.type === "Text") {
      panel.classList.add("is-section-inspector");
      panel.append(
        this.renderSectionInspectorHeader(timeline, selected, activeShot, "Text Section"),
        this.renderPromptRow(selected),
      );
    } else if (selectedAudio) {
      panel.classList.add("is-audio-inspector");
      panel.append(
        inspectorTitle("Audio Clip"),
        this.renderInspectorRow("Name", this.renderTextField(selectedAudio, "name", "Name")),
        this.renderInspectorRow("Volume", this.renderNumberField(selectedAudio, "volume", "Volume", { min: 0, max: 400, step: 1 })),
        this.renderInspectorRow("Source In", this.renderNumberField(selectedAudio, "source_in", "Source In", { min: 0, step: 0.05 })),
        this.renderInspectorRow("Source Out", this.renderNumberField(selectedAudio, "source_out", "Source Out", { min: 0, step: 0.05, allowNull: true })),
        this.renderInspectorRow("Fade In", this.renderNumberField(selectedAudio, "fade_in", "Fade In", { min: 0, step: 0.05 })),
        this.renderInspectorRow("Fade Out", this.renderNumberField(selectedAudio, "fade_out", "Fade Out", { min: 0, step: 0.05 })),
        this.renderInspectorRow("Enabled", this.renderCheckboxField(selectedAudio, "enabled", "Enabled")),
        this.renderInspectorRow("Locked", this.renderCheckboxField(selectedAudio, "locked", "Locked")),
        this.renderMediaSummary(timeline, selectedAudio.audio, "Audio"),
      );
    } else if (selectedShot) {
      panel.classList.add("is-shot-inspector");
      panel.append(this.renderShotInspector(timeline, selectedShot, { standalone: true }));
    } else if (selectedBoundary) {
      panel.classList.add("is-boundary-inspector");
      panel.append(this.renderBoundaryInspector(timeline, selectedBoundary));
    }
    inspector.append(panel);
    return inspector;
  }

  renderBoundaryInspector(timeline, boundary) {
    const leftShot = findShot(timeline, boundary.left_shot_id);
    const rightShot = findShot(timeline, boundary.right_shot_id);
    const title = [leftShot, rightShot].every(Boolean)
      ? `${shotDisplayLabel(timeline, leftShot)} to ${shotDisplayLabel(timeline, rightShot)}`
      : "Boundary";
    const wrapper = el("div", "htd-boundary-inspector");
    wrapper.append(
      inspectorTitle(title),
      this.renderInspectorControlRow(
        this.renderInspectorCompactField("Mode:", this.renderBoundaryModeField(boundary), "is-boundary-mode"),
        this.renderInspectorCompactField("Tail:", this.renderNumberField(boundary, "tail_frames", "Tail Frames", { min: 0, step: 1 })),
        this.renderInspectorCompactField("Blend:", this.renderNumberField(boundary, "blend_frames", "Blend Frames", { min: 0, step: 1 })),
      ),
      this.renderInspectorControlRow(
        this.renderInspectorCompactField("Character Refs:", this.renderCheckboxField(boundary, "reuse_character_refs", "Reuse Character References"), "is-boundary-toggle"),
        this.renderInspectorCompactField("Style:", this.renderCheckboxField(boundary, "reuse_style", "Reuse Style"), "is-boundary-toggle"),
      ),
      this.renderInspectorRow(
        "Transition",
        this.renderTextField(boundary, "transition_prompt", "Transition Prompt", {
          multiline: true,
          rows: 3,
          className: "htd-field htd-boundary-prompt",
        }),
        "is-transition-prompt",
      ),
    );
    return wrapper;
  }

  renderBoundaryModeField(boundary) {
    const id = `boundary-inspector-mode-${boundary.boundary_id}`;
    return iconMenuControl({
      id,
      title: "Boundary Mode",
      iconName: "boundary",
      value: boundary.mode,
      options: BOUNDARY_MODES,
      placement: "above-end",
      showValue: true,
      open: this.openMenu === id,
      onToggle: () => {
        this.openMenu = this.openMenu === id ? null : id;
        this.render();
      },
      onChange: (nextValue) => {
        this.openMenu = null;
        this.commitMutation((timeline) => {
          const liveBoundary = findBoundary(timeline, boundary.boundary_id);
          if (liveBoundary) liveBoundary.mode = nextValue;
        }, "boundary change");
      },
    });
  }

  renderShotInspector(timeline, shot, options = {}) {
    this.requestAvailableCaptures(timeline, shot);
    const wrapper = el("div", `htd-shot-inspector${options.standalone ? " is-standalone" : ""}`);
    wrapper.append(
      this.renderShotCardHeader(timeline, shot, options),
      this.renderCurrentTakeCard(timeline, shot),
      this.renderShotDetails(timeline, shot),
    );
    return wrapper;
  }

  renderShotCardHeader(timeline, shot, options = {}) {
    const shotLabel = shotDisplayLabel(timeline, shot);
    const header = el("div", "htd-shot-card-header");
    const title = inspectorTitle(options.standalone ? shotLabel : `${shotLabel} Details`);
    const tools = el("div", "htd-shot-header-tools");
    tools.append(
      this.renderAssemblyReadinessPill(timeline, shot),
      this.renderShotTypeField(shot),
      iconButton("take", "Open Captures", () => this.openCaptureManagementModal(shot.shot_id)),
    );
    header.append(title, tools);
    return header;
  }

  renderShotDetails(timeline, shot) {
    const details = el("details", "htd-shot-advanced htd-shot-details");
    details.open = Boolean(this.shotDetailsOpen[shot.shot_id]);
    details.addEventListener("toggle", () => {
      const open = Boolean(details.open);
      if (Boolean(this.shotDetailsOpen[shot.shot_id]) === open) return;
      this.shotDetailsOpen[shot.shot_id] = open;
      this.render();
    });
    const summary = el("summary", "htd-shot-advanced-summary");
    summary.textContent = "Details";
    summary.title = "Shot Settings";

    const shotLabel = shotDisplayLabel(timeline, shot);
    const nameInput = el("input", "htd-field htd-shot-name");
    nameInput.value = shot.name ?? "";
    nameInput.placeholder = shotLabel;
    nameInput.title = "Shot Name";
    nameInput.addEventListener("change", () => {
      this.commitMutation((currentTimeline) => renameShot(currentTimeline, shot.shot_id, nameInput.value), "shot change");
    });

    const settingsRow = el("div", "htd-shot-details-row");
    settingsRow.append(
      this.renderInspectorCompactField("Name:", nameInput, "is-shot-name"),
      this.renderInspectorCompactField("LoRAs:", this.renderShotLoraModeField(shot), "is-shot-lora-mode"),
      this.renderInspectorCompactField("Clip:", this.renderShotClipControl(timeline, shot), "is-shot-clip"),
    );

    const body = el("div", "htd-shot-details-body");
    body.append(
      settingsRow,
      this.renderShotBoundaryContext(timeline, shot),
      this.renderShotLoraTargets(timeline, shot),
    );
    details.append(summary, body);
    return details;
  }

  renderSectionInspectorHeader(timeline, section, activeShot, titleText) {
    const header = el("div", "htd-section-inspector-header");
    header.append(inspectorTitle(titleText));
    const actions = el("div", "htd-section-header-actions");
    if (activeShot) actions.append(this.renderSectionShotSummary(timeline, activeShot));
    header.append(actions);
    return header;
  }

  renderSectionShotSummary(timeline, shot) {
    const summary = el("div", "htd-section-shot-summary");
    const shotLabel = shotDisplayLabel(timeline, shot);
    const name = el("span", "htd-section-shot-name");
    name.textContent = `Shot: ${shotLabel}`;
    name.title = `${shotLabel} (${shot.type})`;
    const readiness = this.renderAssemblyReadinessPill(timeline, shot);
    const type = el("span", "htd-boundary-pill htd-section-shot-type");
    type.textContent = shot.type;
    type.title = `Shot Type: ${shot.type}`;
    const openShot = iconButton("shot", "Open Shot Details", () => {
      this.commitMutation((currentTimeline) => selectItem(currentTimeline, shot.shot_id), "select", { pushUndo: false });
    });
    const captures = iconButton("take", "Open Captures", () => this.openCaptureManagementModal(shot.shot_id));
    summary.append(name, readiness, type, openShot, captures);
    return summary;
  }

  renderShotTypeField(shot) {
    return iconMenuControl({
      id: `shot-type-${shot.shot_id}`,
      title: "Shot Type",
      iconName: "shot",
      value: shot.type,
      options: SHOT_TYPES,
      placement: "above-end",
      showValue: true,
      open: this.openMenu === `shot-type-${shot.shot_id}`,
      onToggle: () => {
        const id = `shot-type-${shot.shot_id}`;
        this.openMenu = this.openMenu === id ? null : id;
        this.render();
      },
      onChange: (nextValue) => {
        this.openMenu = null;
        this.commitMutation((timeline) => changeShotType(timeline, shot.shot_id, nextValue), "shot change");
      },
    });
  }

  renderShotLoraModeField(shot) {
    const value = shot.lora_overrides?.merge_mode ?? "Inherit Global";
    return iconMenuControl({
      id: `shot-lora-mode-${shot.shot_id}`,
      title: "Shot LoRA Mode",
      iconName: "lora",
      value,
      options: LORA_MERGE_MODES,
      placement: "above-end",
      showValue: true,
      open: this.openMenu === `shot-lora-mode-${shot.shot_id}`,
      onToggle: () => {
        const id = `shot-lora-mode-${shot.shot_id}`;
        this.openMenu = this.openMenu === id ? null : id;
        this.render();
      },
      onChange: (nextValue) => {
        this.openMenu = null;
        this.commitMutation((timeline) => setShotLoraMergeMode(timeline, shot.shot_id, nextValue), "shot lora change");
      },
    });
  }

  renderShotClipControl(timeline, shot) {
    const videos = (timeline.assets ?? []).filter((asset) => asset.type === ASSET_TYPE_VIDEO);
    const privacyRevealed = this.isPrivacyRevealed(timeline);
    const select = el("select", "htd-select htd-shot-clip-select");
    const none = el("option");
    none.value = "";
    none.textContent = "No video asset";
    select.append(none);
    for (const asset of videos) {
      const option = el("option");
      option.value = asset.asset_id;
      option.textContent = assetDisplayLabel(asset, privacyRevealed, "Video Asset");
      select.append(option);
    }
    select.value = shot.clip_instance?.asset_id ?? "";
    select.title = "Imported Clip Asset";
    select.addEventListener("change", () => {
      this.commitMutation((currentTimeline) => {
        if (select.value) {
          setClipInstanceFromAsset(currentTimeline, shot.shot_id, select.value);
        } else {
          const liveShot = findShot(currentTimeline, shot.shot_id);
          if (liveShot) liveShot.clip_instance = null;
      }
    }, "shot clip change");
    });
    return select;
  }

  renderAdvancedTakeAttachment(timeline, shot) {
    const details = el("details", "htd-shot-advanced");
    const summary = el("summary", "htd-shot-advanced-summary");
    summary.textContent = "Advanced";
    summary.title = "Advanced Shot Actions";
    details.append(summary, this.renderManualTakeAttachmentField(timeline, shot));
    return details;
  }

  renderManualTakeAttachmentField(timeline, shot) {
    const row = el("div", "htd-shot-row htd-shot-attach-take");
    const label = el("span", "htd-shot-row-label");
    label.textContent = "Manual Take";
    const privacyRevealed = this.isPrivacyRevealed(timeline);
    const generatedVideos = (timeline.assets ?? []).filter((asset) => asset.type === ASSET_TYPE_VIDEO && asset.source_kind === "Generated");
    const select = el("select", "htd-select htd-generated-take-select");
    const none = el("option");
    none.value = "";
    none.textContent = generatedVideos.length ? "Choose generated asset" : "No generated assets";
    select.title = "Existing Generated Asset";
    select.append(none);
    for (const asset of generatedVideos) {
      const option = el("option");
      option.value = asset.asset_id;
      option.textContent = assetDisplayLabel(asset, privacyRevealed, "Generated Video");
      select.append(option);
    }
    const attach = button("Attach", "Attach Existing Generated Asset As Candidate Take", () => {
      if (!select.value) return;
      this.commitMutation((currentTimeline) => attachVideoAssetAsTake(currentTimeline, shot.shot_id, select.value), "attach take");
    });
    attach.disabled = !generatedVideos.length;
    const pick = button("Pick", "Pick Existing Generated Asset As Candidate Take", () => {
      this.openMediaPicker(ASSET_TYPE_VIDEO, { mode: "attach-generated-take", shotId: shot.shot_id });
    });
    row.append(label, select, attach, pick);
    return row;
  }

  openCaptureManagementModal(shotId) {
    this.captureModalShotId = shotId;
    const shot = findShot(this.controller.timeline, shotId);
    if (shot) this.requestAvailableCaptures(this.controller.timeline, shot, { force: true });
    this.render();
  }

  closeCaptureManagementModal() {
    this.captureModalShotId = "";
    this.render();
  }

  requestAvailableCaptures(timeline, shot, options = {}) {
    if (!shot?.shot_id) return;
    const key = availableCapturesKey(timeline, shot, this.isPrivacyRevealed(timeline), this.globalSettings());
    if (!options.force && this.availableCaptures.key === key) return;
    this.availableCaptures = {
      key,
      loading: true,
      error: "",
      items: [],
    };
    if (options.rerender) this.render();
    fetchProjectTakeCaptures(
      timeline,
      shot.shot_id,
      this.controller.managedPrivacy?.media,
    )
      .then((payload) => {
        if (this.availableCaptures.key !== key) return;
        this.availableCaptures = {
          key,
          loading: false,
          error: "",
          items: Array.isArray(payload?.captures) ? payload.captures : [],
        };
        this.render();
      })
      .catch((error) => {
        if (this.availableCaptures.key !== key) return;
        this.availableCaptures = {
          key,
          loading: false,
          error: error?.message || "Failed to load captures.",
          items: [],
        };
        this.render();
      });
  }

  availableCaptureState(timeline, shot) {
    const key = availableCapturesKey(timeline, shot, this.isPrivacyRevealed(timeline), this.globalSettings());
    return this.availableCaptures.key === key ? this.availableCaptures : { loading: true, error: "", items: [] };
  }

  renderCurrentTakeCard(timeline, shot) {
    const card = el("div", "htd-current-take-card");
    const header = el("div", "htd-shot-subheader");
    const title = el("span");
    title.textContent = "Current Take";
    const takes = shot.takes ?? [];
    const count = button(
      `${takes.length} ${takes.length === 1 ? "take" : "takes"}`,
      "Open Captures",
      () => this.openCaptureManagementModal(shot.shot_id),
    );
    header.append(title, count);
    card.append(header);

    const latestTake = latestAttachedTake(shot);
    if (!latestTake) {
      card.append(this.renderCurrentTakeEmptyState(timeline, shot));
      return card;
    }

    const privacyRevealed = this.isPrivacyRevealed(timeline);
    const asset = latestTake.asset_id ? timeline.assets?.find((candidate) => candidate.asset_id === latestTake.asset_id) : null;
    const previewData = this.takeVideoPreviewData(timeline, latestTake, asset, privacyRevealed);

    const body = el("div", "htd-current-take-body");
    body.append(this.renderCurrentTakeThumbnail(previewData));

    const info = el("div", "htd-current-take-info");
    const titleRow = el("div", "htd-current-take-title-row");
    const takeIndex = takes.findIndex((candidate) => candidate.take_id === latestTake.take_id);
    const label = el("span", "htd-take-label");
    label.textContent = takeOrderLabel(takeIndex, takes.length);
    label.title = privacyRevealed ? takeSummaryLabel(timeline, latestTake, true) : "Private take";
    const status = el("span", "htd-take-status-pill");
    status.textContent = latestTake.status ?? "Candidate";
    status.title = `Take Status: ${latestTake.status ?? "Candidate"}`;
    titleRow.append(label, status);
    const summary = el("div", "htd-take-asset-summary");
    summary.textContent = assetSummaryLabel(asset, privacyRevealed);
    summary.title = privacyRevealed ? (asset?.path || asset?.name || asset?.asset_id || summary.textContent) : summary.textContent;
    info.append(titleRow, summary, this.renderTakeActions(timeline, shot, latestTake, asset, previewData, { menuIdPrefix: "current-take-status" }));
    body.append(info);
    card.append(body, this.renderTakeHistoryDots(shot, takes, latestTake));

    if (shot.accepted_take_id && shot.accepted_take_id !== latestTake.take_id) {
      const acceptedIndex = takes.findIndex((candidate) => candidate.take_id === shot.accepted_take_id);
      if (acceptedIndex >= 0) {
        const note = el("div", "htd-current-take-note");
        note.textContent = `Accepted: ${takeOrderLabel(acceptedIndex, takes.length)}`;
        note.title = "An earlier take is accepted for assembly. Open Captures to review.";
        card.append(note);
      }
    }
    return card;
  }

  renderCurrentTakeThumbnail(previewData) {
    const thumb = el("div", "htd-current-take-thumb");
    if (!previewData?.url) {
      thumb.classList.add("is-empty");
      const empty = el("span", "htd-shot-empty");
      empty.textContent = "No preview";
      thumb.append(empty);
      return thumb;
    }
    const video = el("video", "htd-current-take-video");
    video.preload = "metadata";
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    video.controls = false;
    video.src = previewData.url;
    video.setAttribute("aria-label", previewData.privacyMode ? "Private current take preview" : "Current take preview");
    thumb.append(video);
    thumb.title = "Hover to play, click to enlarge";
    thumb.addEventListener("mouseenter", () => {
      video.muted = true;
      const playResult = video.play?.();
      playResult?.catch?.(() => {});
    });
    thumb.addEventListener("mouseleave", () => {
      video.pause?.();
      video.currentTime = 0;
    });
    thumb.addEventListener("click", () => this.openTakeVideoPreviewData(previewData));
    return thumb;
  }

  renderTakeHistoryDots(shot, takes, latestTake) {
    const history = el("div", "htd-take-history");
    if (takes.length < 2) return history;
    const visibleTakes = takes.slice(-12);
    visibleTakes.forEach((take, index) => {
      const dot = el("span", "htd-take-history-dot");
      const takeIndex = takes.length - visibleTakes.length + index;
      dot.classList.toggle("is-current", take.take_id === latestTake.take_id);
      dot.classList.toggle("is-accepted", take.take_id === shot.accepted_take_id);
      dot.classList.toggle("is-rejected", take.status === "Rejected");
      dot.title = `${takeOrderLabel(takeIndex, takes.length)} - ${take.status ?? "Candidate"}`;
      history.append(dot);
    });
    return history;
  }

  renderCurrentTakeEmptyState(timeline, shot) {
    const state = this.availableCaptureState(timeline, shot);
    if (state.loading) {
      const loading = el("div", "htd-shot-empty");
      loading.textContent = "Loading captures...";
      return loading;
    }
    if (state.error) {
      const error = el("div", "htd-shot-empty htd-capture-error");
      error.textContent = state.error;
      return error;
    }
    if (state.items.length) {
      return this.renderProjectCaptureRow(timeline, shot, state.items[0], 0, state.items.length, { compact: true });
    }
    const empty = el("div", "htd-shot-empty");
    empty.textContent = "No takes yet. Run a generation with Take Capture to see it here.";
    return empty;
  }

  renderCaptureManagementModal(timeline, shot) {
    this.requestAvailableCaptures(timeline, shot);
    const overlay = el("div", "htd-captures-overlay");
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) this.closeCaptureManagementModal();
    });
    const modal = el("div", "htd-captures-modal");
    const header = el("div", "htd-captures-header");
    const title = el("div", "htd-captures-title");
    title.textContent = `Captures - ${shotDisplayLabel(timeline, shot)}`;
    const refresh = iconButton("refresh", "Refresh Project Take Captures", () => {
      this.requestAvailableCaptures(this.controller.timeline, shot, { force: true, rerender: true });
    });
    const close = button("X", "Close Captures", () => this.closeCaptureManagementModal());
    const headerActions = el("div", "htd-captures-header-actions");
    headerActions.append(refresh, close);
    header.append(title, headerActions);

    const body = el("div", "htd-captures-body");
    const state = this.availableCaptureState(timeline, shot);
    const projectBlock = el("div", "htd-captures-section");
    const projectTitle = el("div", "htd-captures-section-title");
    projectTitle.textContent = "Project Captures";
    projectBlock.append(projectTitle);
    if (state.loading) {
      const loading = el("div", "htd-shot-empty");
      loading.textContent = "Loading captures...";
      projectBlock.append(loading);
    } else if (state.error) {
      const error = el("div", "htd-shot-empty htd-capture-error");
      error.textContent = state.error;
      projectBlock.append(error);
    } else if (!state.items.length) {
      const empty = el("div", "htd-shot-empty");
      empty.textContent = "No captured takes found.";
      projectBlock.append(empty);
    } else {
      state.items.forEach((item, index) => {
        projectBlock.append(this.renderProjectCaptureRow(timeline, shot, item, index, state.items.length));
      });
    }

    const takesBlock = el("div", "htd-captures-section");
    const takesTitle = el("div", "htd-captures-section-title");
    takesTitle.textContent = "Attached Takes";
    takesBlock.append(takesTitle);
    const takes = shot.takes ?? [];
    if (!takes.length) {
      const empty = el("div", "htd-shot-empty");
      empty.textContent = "No takes.";
      takesBlock.append(empty);
    } else {
      [...takes].reverse().forEach((take, reverseIndex) => {
        const index = takes.length - 1 - reverseIndex;
        takesBlock.append(this.renderAttachedTakeRow(timeline, shot, take, index, takes.length));
      });
    }

    const advanced = this.renderAdvancedTakeAttachment(timeline, shot);
    body.append(projectBlock, takesBlock, advanced);
    modal.append(header, body);
    overlay.append(modal);
    return overlay;
  }

  renderProjectCaptureRow(timeline, shot, item, index, total, options = {}) {
    const privacyRevealed = this.isPrivacyRevealed(timeline);
    const row = el("div", `htd-capture-row${options.compact ? " is-compact" : ""}`);
    const label = el("span", "htd-take-label");
    label.textContent = captureOrderLabel(index, total);
    label.title = privacyRevealed ? captureSummaryLabel(item, true) : "Captured video";
    const summary = el("span", "htd-take-asset-summary");
    summary.textContent = captureShortSummaryLabel(item, privacyRevealed);
    summary.title = privacyRevealed ? (item.path || item.filename || summary.textContent) : summary.textContent;
    const actions = el("span", "htd-take-actions");
    const takeId = captureTakeId(item);
    const capturePath = captureMediaPath(item);
    const existing = findAttachedTakeForCapture(timeline, shot, item);
    const previewData = this.captureVideoPreviewData(timeline, item, privacyRevealed);
    const status = el("span", `htd-take-status-pill${existing ? "" : " htd-take-status-placeholder"}`);
    status.textContent = existing?.status ?? "Candidate";
    status.title = existing ? `Attached Take Status: ${existing.status ?? "Candidate"}` : "Capture is not attached";
    if (!existing) status.setAttribute("aria-hidden", "true");
    actions.append(iconButton("preview-video", previewData ? "Preview Take Video" : "No preview available", () => {
      if (previewData) this.openTakeVideoPreviewData(previewData);
    }, { disabled: !previewData }));
    const remove = iconButton("delete", "Delete Take Files", () => {
      this.deleteProjectTakeCaptureFromItem(shot, item, { takeId, path: capturePath, label: label.textContent });
    });
    remove.classList.add("is-danger");
    const attach = iconButton("insert", existing ? "Capture already attached" : "Attach Project Capture As Take", () => {
      this.attachManagedProjectCapture(shot, item, false);
    }, { disabled: Boolean(existing) });
    const accept = iconButton("accept", existing?.status === "Accepted" ? "Capture already accepted" : existing ? "Accept attached capture" : "Attach And Accept Project Capture", () => {
      if (existing) {
        this.commitMutation((currentTimeline) => {
          acceptTake(currentTimeline, shot.shot_id, existing.take_id);
        }, "accept project capture");
      } else {
        this.attachManagedProjectCapture(shot, item, true);
      }
    }, { disabled: existing?.status === "Accepted" });
    const reject = iconButton("reject", !existing ? "Attach capture before rejecting" : existing.status === "Rejected" ? "Capture already rejected" : "Mark Take Rejected", () => {
      if (!existing) return;
      this.commitMutation((currentTimeline) => setTakeStatus(currentTimeline, shot.shot_id, existing.take_id, "Rejected"), "take change");
    }, { disabled: !existing || existing.status === "Rejected" });
    const restore = iconButton("restore", !existing ? "Attach capture before restoring" : existing.status === "Candidate" ? "Capture already candidate" : "Restore Candidate Take", () => {
      if (!existing) return;
      this.commitMutation((currentTimeline) => setTakeStatus(currentTimeline, shot.shot_id, existing.take_id, "Candidate"), "take change");
    }, { disabled: !existing || existing.status === "Candidate" });
    actions.append(remove, attach, accept, reject, restore);
    row.append(label, summary, status, actions);
    return row;
  }

  renderAttachedTakeRow(timeline, shot, take, index, total, options = {}) {
    const row = el("div", `htd-take-row${options.compact ? " is-compact" : ""}`);
    const label = el("span", "htd-take-label");
    const asset = take.asset_id ? timeline.assets?.find((candidate) => candidate.asset_id === take.asset_id) : null;
    const privacyRevealed = this.isPrivacyRevealed(timeline);
    label.textContent = takeOrderLabel(index, total);
    label.title = privacyRevealed ? takeSummaryLabel(timeline, take, true) : "Private take";
    const assetSummary = el("span", "htd-take-asset-summary");
    assetSummary.textContent = assetSummaryLabel(asset, privacyRevealed);
    assetSummary.title = privacyRevealed ? (asset?.path || asset?.name || asset?.asset_id || assetSummary.textContent) : assetSummary.textContent;
    const status = el("span", "htd-take-status-pill");
    status.textContent = take.status ?? "Candidate";
    status.title = `Take Status: ${take.status ?? "Candidate"}`;
    const previewData = this.takeVideoPreviewData(timeline, take, asset, privacyRevealed);
    const actions = this.renderTakeActions(timeline, shot, take, asset, previewData, { deleteLabel: label.textContent });
    row.append(label, assetSummary, status, actions);
    return row;
  }

  renderTakeActions(timeline, shot, take, asset, previewData, options = {}) {
    const actions = el("span", "htd-take-actions");
    actions.append(iconButton("preview-video", previewData ? "Preview Take Video" : "No preview available", () => {
      if (previewData) this.openTakeVideoPreviewData(previewData);
    }, { disabled: !previewData }));
    const remove = iconButton("delete", "Delete Take Files", () => {
      this.deleteProjectTakeFromTimelineTake(shot, take, asset, { label: options.deleteLabel ?? takeStatusLabel(take) });
    });
    remove.classList.add("is-danger");
    const menuId = `${options.menuIdPrefix ?? "take-status"}-${take.take_id}`;
    const statusMenu = iconMenuControl({
      id: menuId,
      title: "Take Status",
      iconName: "take",
      value: take.status ?? "Candidate",
      options: TAKE_STATUSES,
      placement: "above-end",
      showValue: false,
      open: this.openMenu === menuId,
      onToggle: () => {
        this.openMenu = this.openMenu === menuId ? null : menuId;
        this.render();
      },
      onChange: (nextValue) => {
        this.openMenu = null;
        this.commitMutation((currentTimeline) => setTakeStatus(currentTimeline, shot.shot_id, take.take_id, nextValue), "take change");
      },
    });
    const accept = iconButton("accept", "Accept Take", () => {
      this.commitMutation((currentTimeline) => acceptTake(currentTimeline, shot.shot_id, take.take_id), "accept take");
    }, { disabled: !asset || take.status === "Accepted" });
    const reject = iconButton("reject", "Mark Take Rejected", () => {
      this.commitMutation((currentTimeline) => setTakeStatus(currentTimeline, shot.shot_id, take.take_id, "Rejected"), "take change");
    }, { disabled: take.status === "Rejected" });
    const restore = iconButton("restore", "Restore Candidate Take", () => {
      this.commitMutation((currentTimeline) => setTakeStatus(currentTimeline, shot.shot_id, take.take_id, "Candidate"), "take change");
    }, { disabled: take.status === "Candidate" });
    actions.append(remove, statusMenu, accept, reject, restore);
    return actions;
  }

  renderAssemblyReadiness(timeline, shot) {
    const row = el("div", "htd-shot-row htd-assembly-readiness");
    const label = el("span", "htd-shot-row-label");
    label.textContent = "Assembly";
    const value = el("span", "htd-assembly-status");
    value.textContent = assemblyReadinessStatus(timeline, shot);
    value.title = "Assembly Readiness State";
    row.append(label, value);
    return row;
  }

  renderAssemblyReadinessPill(timeline, shot) {
    const value = el("span", "htd-readiness-pill");
    const fullStatus = assemblyReadinessStatus(timeline, shot);
    value.textContent = assemblyReadinessPillText(fullStatus);
    value.title = `Assembly: ${fullStatus}`;
    value.classList.add(assemblyReadinessPillTone(fullStatus));
    return value;
  }

  renderShotBoundaryContext(timeline, shot) {
    const context = shotBoundaryContext(timeline, shot);
    const row = el("div", "htd-shot-boundary-context");
    const incoming = el("span", "htd-boundary-pill");
    incoming.textContent = `In: ${context.incoming?.mode ?? "None"}`;
    incoming.title = context.incoming ? `Incoming Boundary: ${context.incoming.mode}` : "Incoming Boundary: None";
    const outgoing = el("span", "htd-boundary-pill");
    outgoing.textContent = `Out: ${context.outgoing?.mode ?? "None"}`;
    outgoing.title = context.outgoing ? `Outgoing Boundary: ${context.outgoing.mode}` : "Outgoing Boundary: None";
    row.append(incoming, outgoing);
    const warning = boundaryWarningForShot(timeline, context);
    if (warning) {
      const warningElement = el("span", "htd-boundary-warning");
      warningElement.textContent = "LoRA mismatch";
      warningElement.title = warning.hint || warning.message || "Boundary LoRA stack mismatch";
      row.append(warningElement);
    }
    return row;
  }

  renderShotLoraTargets(timeline, shot) {
    const row = el("div", "htd-shot-lora-targets-row");
    const title = el("span", "htd-shot-lora-title");
    title.textContent = "LoRA Targets";
    const clear = iconButton("delete", "Clear Shot LoRA Override", () => {
      this.commitMutation((currentTimeline) => clearShotLoraOverride(currentTimeline, shot.shot_id), "shot lora clear");
    });
    clear.classList.add("is-danger");
    const targetElements = [title];
    let previousModelKey = null;
    for (const descriptor of modelLoraTargetDescriptors()) {
      if (previousModelKey && previousModelKey !== descriptor.modelKey) {
        const separator = el("span", "htd-lora-target-separator");
        separator.setAttribute("aria-hidden", "true");
        targetElements.push(separator);
      }
      targetElements.push(this.renderShotLoraTargetCompact(
        timeline,
        shot,
        descriptor.label,
        descriptor.modelKey,
        descriptor.targetKey,
      ));
      previousModelKey = descriptor.modelKey;
    }
    row.append(...targetElements, clear);
    return row;
  }

  renderShotLoraTargetRow(timeline, shot, labelText, modelKey, targetKey) {
    const stack = shot.lora_overrides?.targets?.[modelKey]?.[targetKey];
    const row = this.renderLoraSummaryRow(labelText, stack, { privacyRevealed: this.isPrivacyRevealed(timeline) });
    row.append(this.renderLoraTargetActions({
      timeline,
      labelText,
      stack,
      onEdit: () => this.openShotLoraStackEditor(shot.shot_id, labelText, modelKey, targetKey),
      onClear: () => this.commitMutation((currentTimeline) => {
        clearShotLoraTargetStack(currentTimeline, shot.shot_id, modelKey, targetKey);
      }, "shot lora clear target"),
    }));
    return row;
  }

  renderShotLoraTargetCompact(timeline, shot, labelText, modelKey, targetKey) {
    const stack = shot.lora_overrides?.targets?.[modelKey]?.[targetKey];
    const item = el("span", "htd-shot-lora-target");
    const label = el("span", "htd-shot-lora-target-label");
    label.textContent = labelText;
    item.append(label, this.renderLoraStackSummary(stack, { privacyRevealed: this.isPrivacyRevealed(timeline) }), this.renderLoraTargetActions({
      timeline,
      labelText,
      stack,
      onEdit: () => this.openShotLoraStackEditor(shot.shot_id, labelText, modelKey, targetKey),
      onClear: () => this.commitMutation((currentTimeline) => {
        clearShotLoraTargetStack(currentTimeline, shot.shot_id, modelKey, targetKey);
      }, "shot lora clear target"),
    }));
    return item;
  }

  renderPromptRow(item) {
    const control = this.renderPromptInput(item);
    if (!control) return this.container.ownerDocument.createDocumentFragment();
    const row = el("div", "htd-inspector-row is-prompt");
    row.append(control);
    return row;
  }

  renderPromptInput(item) {
    if (!shouldRenderPromptInput(this.controller.timeline, item)) {
      return null;
    }
    const input = this.renderTextField(item, "prompt", "Prompt", {
      className: "htd-prompt",
      debounced: true,
      multiline: true,
      placeholder: "Write your prompt here...",
      rows: 5,
    });
    input.dataset.referenceTrigger = PROMPT_REFERENCE_TRIGGER;
    input._heltoCharacterReferenceCompletions = getReferencePromptCompletions(this.controller.timeline);
    return this.wrapPromptReferenceIntellisense(input, item);
  }

  wrapPromptReferenceIntellisense(input, item) {
    const wrapper = el("div", "htd-prompt-wrap");
    const popup = el("div", "htd-reference-completions");
    popup.hidden = true;
    popup.setAttribute("role", "listbox");
    popup.setAttribute("aria-label", "Character reference suggestions");
    wrapper.append(input, popup);
    this.attachPromptReferenceIntellisense(input, popup, item);
    return wrapper;
  }

  attachPromptReferenceIntellisense(input, popup, item) {
    const state = {
      items: [],
      selectedIndex: 0,
      query: null,
    };

    const close = () => {
      popup.hidden = true;
      popup.replaceChildren();
      state.items = [];
      state.selectedIndex = 0;
      state.query = null;
      input.removeAttribute("aria-activedescendant");
    };

    const selectCompletion = (completion) => {
      const result = applyReferencePromptCompletion(
        input.value,
        input.selectionStart ?? input.value.length,
        completion,
        PROMPT_REFERENCE_TRIGGER,
      );
      if (!result) return;
      input.value = result.value;
      input.setSelectionRange(result.caret, result.caret);
      setLiveItemField(this.controller.timeline, item, "prompt", input.value);
      this.controller.scheduleDebouncedCommit("prompt typing", { rerender: false });
      close();
    };

    const render = () => {
      popup.replaceChildren();
      state.items.forEach((completion, index) => {
        const option = el("button", `htd-reference-completion${index === state.selectedIndex ? " is-selected" : ""}`);
        const shortcut = index < 9 ? String(index + 1) : "";
        option.type = "button";
        option.id = `htd-reference-completion-${completion.id || index}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", index === state.selectedIndex ? "true" : "false");
        option.tabIndex = -1;
        if (shortcut) {
          const badge = el("span", "htd-reference-completion-key");
          badge.textContent = shortcut;
          option.append(badge);
        }
        const text = el("span", "htd-reference-completion-text");
        const tag = el("span", "htd-reference-completion-tag");
        tag.textContent = completion.tag;
        text.append(tag);
        if (completion.description) {
          const description = el("span", "htd-reference-completion-description");
          description.textContent = completion.description;
          text.append(description);
        }
        option.append(text);
        option.addEventListener("mousedown", (event) => event.preventDefault());
        option.addEventListener("click", () => selectCompletion(completion));
        popup.append(option);
      });
      const selected = popup.children[state.selectedIndex];
      if (selected?.id) input.setAttribute("aria-activedescendant", selected.id);
    };

    const update = () => {
      const context = referencePromptCompletionContext(
        input.value,
        input.selectionStart ?? input.value.length,
        PROMPT_REFERENCE_TRIGGER,
      );
      const completions = getReferencePromptCompletions(this.controller.timeline);
      input._heltoCharacterReferenceCompletions = completions;
      if (!context || completions.length === 0) {
        close();
        return;
      }
      const items = filterReferencePromptCompletions(completions, context.query);
      if (items.length === 0) {
        close();
        return;
      }
      state.items = items;
      state.selectedIndex = state.query === context.query
        ? Math.max(0, Math.min(state.selectedIndex, items.length - 1))
        : 0;
      state.query = context.query;
      popup.hidden = false;
      render();
    };

    input.setAttribute("aria-autocomplete", "list");
    input.addEventListener("input", update);
    input.addEventListener("click", update);
    input.addEventListener("blur", close);
    input.addEventListener("keydown", (event) => {
      if (popup.hidden) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        state.selectedIndex = (state.selectedIndex + direction + state.items.length) % state.items.length;
        render();
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        selectCompletion(state.items[state.selectedIndex]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (/^[1-9]$/.test(event.key)) {
        const index = Number(event.key) - 1;
        if (state.items[index]) {
          event.preventDefault();
          selectCompletion(state.items[index]);
        }
      }
    });
  }

  renderInspectorRow(label, control, className = "") {
    const row = el("div", `htd-inspector-row ${className}`.trim());
    const rowLabel = el("span", "htd-inspector-label");
    rowLabel.textContent = label;
    row.append(rowLabel, control);
    return row;
  }

  renderInspectorControlRow(...fields) {
    const row = el("div", "htd-inspector-control-row");
    row.append(...fields.filter(Boolean));
    return row;
  }

  renderInspectorCompactField(label, control, className = "") {
    const field = el("div", `htd-inspector-compact-field ${className}`.trim());
    const fieldLabel = el("span", "htd-inspector-compact-label");
    fieldLabel.textContent = label;
    field.append(fieldLabel, control);
    return field;
  }

  renderGuideStrengthField(item) {
    const wrapper = el("div", "htd-strength-control");
    const slider = el("input", "htd-strength-slider");
    const number = el("input", "htd-number htd-strength-number");
    slider.type = "range";
    slider.min = "0";
    slider.max = "1";
    slider.step = "0.05";
    slider.title = "Guide Strength";
    slider.setAttribute("aria-label", "Guide Strength");
    number.type = "number";
    number.min = "0";
    number.max = "1";
    number.step = "0.05";
    number.title = "Guide Strength";
    number.setAttribute("aria-label", "Guide Strength");

    const setControlValues = (value) => {
      const strength = clampNumber(value, 0, 1, 1);
      slider.value = String(strength);
      number.value = strength.toFixed(2);
      return strength;
    };

    setControlValues(item.guide_strength);
    slider.addEventListener("input", () => {
      const strength = setControlValues(slider.value);
      setLiveItemField(this.controller.timeline, item, "guide_strength", strength);
      this.controller.scheduleDebouncedCommit("settings change", { delayMs: 80 });
    });
    number.addEventListener("change", () => {
      const strength = setControlValues(number.value);
      this.commitMutation((timeline) => {
        setLiveItemField(timeline, item, "guide_strength", strength);
      }, "settings change");
    });
    wrapper.append(slider, number);
    return wrapper;
  }

  renderMediaSummary(timeline, reference, fallbackType) {
    const asset = resolveMediaReference(timeline, reference);
    const row = el("div", "htd-inspector-row htd-media-summary");
    const rowLabel = el("span", "htd-inspector-label");
    rowLabel.textContent = "Media";
    const value = el("span", "htd-media-value");
    value.textContent = asset ? mediaLabel(timeline, reference, fallbackType) : `No ${fallbackType.toLowerCase()} selected`;
    value.title = asset?.path ?? value.textContent;
    row.append(rowLabel, value);
    return row;
  }

  renderTextField(item, field, title, options = {}) {
    const input = options.multiline ? el("textarea", options.className ?? "htd-field") : el("input", options.className ?? "htd-field");
    if (options.rows != null) input.rows = options.rows;
    input.value = item[field] ?? "";
    input.placeholder = options.placeholder ?? title;
    input.title = title;
    input.addEventListener("input", () => {
      setLiveItemField(this.controller.timeline, item, field, input.value);
      if (options.debounced) {
        this.controller.scheduleDebouncedCommit("prompt typing", { rerender: false });
      } else {
        this.controller.scheduleDebouncedCommit("settings change", { delayMs: 150 });
      }
    });
    if (options.debounced) {
      input.addEventListener("blur", () => {
        this.controller.flushDebouncedCommit("prompt typing", { rerender: false });
      });
    }
    return input;
  }

  renderNumberField(item, field, title, options = {}) {
    const input = el("input", "htd-number");
    input.type = "number";
    input.title = title;
    input.placeholder = title;
    input.step = String(options.step ?? 1);
    if (options.min != null) input.min = String(options.min);
    if (options.max != null) input.max = String(options.max);
    input.value = item[field] == null ? "" : String(item[field]);
    input.addEventListener("change", () => {
      const raw = input.value.trim();
      this.commitMutation((timeline) => {
        setLiveItemField(timeline, item, field, raw === "" && options.allowNull ? null : Number(raw));
      }, "settings change");
    });
    return input;
  }

  renderSelectField(item, field, title, options) {
    return selectControl(title, item[field], options, (value) => {
      this.commitMutation((timeline) => {
        setLiveItemField(timeline, item, field, value);
      }, "settings change");
    });
  }

  renderIconSelectField(item, field, title, options, iconName) {
    const value = item[field] ?? options[0] ?? "";
    return iconMenuControl({
      id: `inspector-${field}-${item.item_id}`,
      title,
      iconName,
      value,
      options,
      placement: "above-end",
      showValue: true,
      open: this.openMenu === `inspector-${field}-${item.item_id}`,
      onToggle: () => {
        const id = `inspector-${field}-${item.item_id}`;
        this.openMenu = this.openMenu === id ? null : id;
        this.render();
      },
      onChange: (nextValue) => {
        this.openMenu = null;
        this.commitMutation((timeline) => {
          setLiveItemField(timeline, item, field, nextValue);
        }, "settings change");
      },
    });
  }

  renderCheckboxField(item, field, title) {
    return toggleButton(title, title, Boolean(item[field]), () => {
      this.commitMutation((timeline) => {
        const liveItem = resolveLiveTimelineItem(timeline, item) ?? item;
        liveItem[field] = !Boolean(liveItem[field]);
      }, "settings change");
    });
  }

  renderProjectSettings(timeline) {
    const draft = this.projectSettingsDraft ?? deepClone(timeline);
    this.projectSettingsDraft = draft;
    const overlay = el("div", "htd-settings-overlay");
    const modal = el("div", "htd-settings-modal");
    const header = el("div", "htd-settings-header");
    const title = el("div", "htd-settings-title");
    title.textContent = "Project Settings";
    header.append(title, button("X", "Cancel Project Settings", () => this.cancelProjectSettings()));

    const body = el("div", "htd-settings-body");
    body.append(
      this.renderSettingReadonly("Project Folder", projectFolderDisplay(draft, this.isPrivacyRevealed(timeline), this.globalSettings())),
      this.renderDraftSettingSelect("Default Crop Mode", draft, ["project", "default_crop_mode"], CROP_MODES),
      this.renderDraftSettingText("Global Prompt", draft, ["project", "global_prompt", "prompt"], true),
      this.renderDraftSettingSelect("Global Prompt Position", draft, ["project", "global_prompt", "position"], GLOBAL_PROMPT_POSITIONS),
      this.renderDraftSettingSelect("Audio Normalization Mode", draft, ["project", "audio", "normalization_mode"], AUDIO_NORMALIZATION_MODES),
      this.renderDraftSettingNumber("Target LUFS", draft, ["project", "audio", "target_lufs"], { step: 0.5 }),
      this.renderDraftSettingNumber("True Peak Limit", draft, ["project", "audio", "true_peak_limit_db"], { step: 0.1 }),
      this.renderDraftSettingNumber("Default Audio Volume", draft, ["project", "audio", "default_volume"], { min: 0, max: 400, step: 1 }),
      this.renderDraftSettingNumber("Default Audio Fade In", draft, ["project", "audio", "default_fade_in_seconds"], { min: 0, step: 0.05 }),
      this.renderDraftSettingNumber("Default Audio Fade Out", draft, ["project", "audio", "default_fade_out_seconds"], { min: 0, step: 0.05 }),
      this.renderProjectLoraSettings(draft, { draftMode: true }),
    );
    modal.append(header, body, this.renderSettingsActions("Project Settings", () => this.saveProjectSettings(), () => this.cancelProjectSettings()));
    overlay.append(modal);
    return overlay;
  }

  renderGlobalSettings(timeline) {
    const draft = this.globalSettingsDraft ?? deepClone(this.globalSettings());
    this.globalSettingsDraft = draft;
    const overlay = el("div", "htd-settings-overlay");
    const modal = el("div", "htd-settings-modal");
    const header = el("div", "htd-settings-header");
    const title = el("div", "htd-settings-title");
    title.textContent = "Global Settings";
    header.append(title, button("X", "Cancel Global Settings", () => this.cancelGlobalSettings()));

    const body = el("div", "htd-settings-body");
    if (this.controller.globalSettingsError) {
      const status = el("div", "htd-global-settings-status");
      status.textContent = this.controller.globalSettingsError;
      body.append(status);
    }
    body.append(
      this.renderGlobalAssetRootSetting(timeline, draft),
      this.renderGlobalSettingCheckbox("Show Resolved Model Output", draft, ["timeline", "show_resolved_model_output"]),
      this.renderGlobalSettingCheckbox("Allow Gaps", draft, ["timeline", "allow_gaps"]),
      this.renderGlobalSettingCheckbox("Auto Close Gaps", draft, ["timeline", "auto_close_gaps"]),
      this.renderGlobalSettingNumber("Minimum Section Duration", draft, ["timeline", "minimum_section_duration_seconds"], { min: 0.05, step: 0.05 }),
      this.renderGlobalSettingCheckbox("Show Effective Prompt", draft, ["global_prompt", "show_effective_prompt"]),
      this.renderGlobalSettingCheckbox("Always Normalize Audio", draft, ["audio", "always_normalize"]),
      this.renderGlobalSettingCheckbox("Privacy Mode", draft, ["privacy", "mode"]),
      this.renderGlobalPrivacyKeystoreSetting(),
      this.renderGlobalSettingCheckbox("Show Section Labels", draft, ["display", "show_section_labels"]),
      this.renderGlobalSettingCheckbox("Show Thumbnails", draft, ["display", "show_thumbnails"]),
      this.renderGlobalSettingCheckbox("Show Audio Waveforms", draft, ["display", "show_audio_waveforms"]),
    );
    modal.append(header, body, this.renderSettingsActions("Global Settings", (control) => this.saveGlobalSettings(control), () => this.cancelGlobalSettings()));
    overlay.append(modal);
    return overlay;
  }

  renderReferenceManager(timeline) {
    const privacyMode = this.isGlobalPrivacyMode();
    const references = getCharacterReferences(timeline);
    const overlay = el("div", `htd-reference-overlay${privacyMode ? " privacy-mode" : ""}`);
    const modal = el("div", "htd-reference-modal");
    const header = el("div", "htd-reference-header");
    const title = el("div", "htd-reference-title");
    title.textContent = "Character References";
    const addButton = iconButton("image-plus", "Add Character Reference", () => this.openReferenceImagePicker());
    const closeButton = button("X", "Close Character References", () => this.closeReferenceManager());
    const headerActions = el("div", "htd-reference-header-actions");
    headerActions.append(addButton, closeButton);
    header.append(title, headerActions);

    const body = el("div", "htd-reference-body");
    if (references.length === 0) {
      const empty = el("div", "htd-reference-empty");
      empty.textContent = "No character references.";
      body.append(empty);
    } else {
      for (const reference of references) body.append(this.renderReferenceCard(reference, privacyMode));
    }

    modal.append(header, body);
    overlay.append(modal);
    return overlay;
  }

  renderReferenceCard(reference, privacyMode) {
    const card = el("div", "htd-reference-card");
    const image = reference.image;
    const thumb = el("button", "htd-reference-thumb");
    thumb.type = "button";
    thumb.title = image?.path || reference.label || "Character reference";
    if (image?.path) {
      const thumbnail = this.node?._timelineMediaCache?.requestThumbnail?.(image, 180);
      if (thumbnail) {
        const img = el("img");
        img.src = thumbnail;
        img.alt = reference.label || "Character reference";
        thumb.append(img);
      } else {
        thumb.append(createIconElement("references"));
      }
      thumb.addEventListener("click", async (event) => {
        if (!event.ctrlKey) return;
        event.preventDefault();
        event.stopPropagation();
        if (privacyMode && !card.matches(":hover")) return;
        const url = await this.node?._timelineMediaCache?.acquireViewUrl?.(image);
        if (!url) return;
        showMediaPreview(this.container.ownerDocument ?? globalThis.document, {
          type: ASSET_TYPE_IMAGE,
          url,
          caption: reference.label || image.name || image.path,
        });
      });
    } else {
      thumb.append(createIconElement("references"));
    }

    const meta = el("div", "htd-reference-meta");
    const labelRow = el("div", "htd-reference-row");
    const tag = el("code", "htd-reference-tag");
    tag.textContent = formatCharacterReferenceTag(reference);
    tag.title = "Prompt reference tag";
    const enabled = toggleButton("On", "Enable Character Reference", reference.enabled !== false, () => {
      this.commitMutation((timeline) => {
        setLiveReferenceField(timeline, reference, "enabled", !(findLiveReference(timeline, reference)?.enabled !== false));
      }, "settings change");
    });
    labelRow.append(tag, enabled);

    const description = el("textarea", "htd-reference-description");
    description.value = reference.description || "";
    description.placeholder = "Short character description...";
    description.title = "Character Description";
    description.rows = 3;
    description.addEventListener("input", () => {
      setLiveReferenceField(this.controller.timeline, reference, "description", description.value);
      this.controller.scheduleDebouncedCommit("reference description", { delayMs: 150, rerender: false });
    });
    description.addEventListener("blur", () => {
      this.controller.flushDebouncedCommit("reference description", { rerender: false });
    });

    const strengthRow = el("div", "htd-reference-strength-row");
    const strengthLabel = el("span", "htd-reference-strength-label");
    strengthLabel.textContent = "Strength";
    const strengthSlider = el("input", "htd-strength-slider");
    const strengthNumber = el("input", "htd-number htd-strength-number");
    strengthSlider.type = "range";
    strengthSlider.min = "0";
    strengthSlider.max = "1";
    strengthSlider.step = "0.05";
    strengthSlider.title = "Reference Strength";
    strengthNumber.type = "number";
    strengthNumber.min = "0";
    strengthNumber.max = "1";
    strengthNumber.step = "0.05";
    strengthNumber.title = "Reference Strength";
    const setStrengthControls = (value) => {
      const strength = clampNumber(value, 0, 1, 1);
      strengthSlider.value = String(strength);
      strengthNumber.value = strength.toFixed(2);
      return strength;
    };
    setStrengthControls(reference.strength);
    strengthSlider.addEventListener("input", () => {
      const strength = setStrengthControls(strengthSlider.value);
      setLiveReferenceField(this.controller.timeline, reference, "strength", strength);
      this.controller.scheduleDebouncedCommit("reference strength", { delayMs: 80, rerender: false });
    });
    strengthNumber.addEventListener("change", () => {
      const strength = setStrengthControls(strengthNumber.value);
      this.commitMutation((timeline) => {
        setLiveReferenceField(timeline, reference, "strength", strength);
      }, "settings change");
    });
    strengthRow.append(strengthLabel, strengthSlider, strengthNumber);

    const actions = el("div", "htd-reference-actions");
    const libraryItemId = referenceLibraryItemId(reference);
    let libraryButton;
    if (libraryItemId) {
      libraryButton = iconButton("library-update", "Update Director Library Character", async () => {
        await this.updateReferenceLibraryCharacter(reference, libraryItemId, libraryButton);
      });
      libraryButton.classList.add("htd-reference-library-action", "is-active");
    } else {
      libraryButton = iconButton("library-add", "Add Reference to Director Library", async () => {
        await this.addReferenceToDirectorLibrary(reference, privacyMode, libraryButton);
      });
      libraryButton.classList.add("htd-reference-library-action");
    }
    actions.append(
      libraryButton,
      iconButton("copy", "Copy Reference Tag", () => this.copyReferenceTag(reference)),
      iconButton("insert", "Insert Reference Tag", () => this.insertReferenceTag(reference)),
      iconButton("delete", "Remove Character Reference", () => {
        this.commitMutation((timeline) => removeCharacterReference(timeline, reference.id), "remove reference");
      }),
    );

    meta.append(labelRow, description, strengthRow, actions);
    card.append(thumb, meta);
    return card;
  }

  async addReferenceToDirectorLibrary(reference, privacyMode, control = null) {
    const liveReference = this.prepareReferenceLibraryAction(reference);
    if (!liveReference) return;
    await withDisabledControl(control, async () => {
      try {
        const payload = referenceLibraryPayload(liveReference, privacyMode);
        const receipt = await this.requireManagedLibrary().characters.create(
          payload.character,
          { name: payload.name, description: payload.description },
        );
        const itemId = String(receipt?.recordId ?? "").trim();
        if (!itemId) throw new Error("Director Library did not return a character id.");
        this.commitMutation((timeline) => {
          stampReferenceLibraryItemId(timeline, reference, itemId);
        }, "save reference to library");
      } catch (error) {
        this.alertReferenceLibraryError(error);
      }
    });
  }

  async updateReferenceLibraryCharacter(reference, libraryItemId, control = null) {
    const itemId = String(libraryItemId ?? "").trim();
    if (!itemId) return;
    const liveReference = this.prepareReferenceLibraryAction(reference);
    if (!liveReference) return;
    await withDisabledControl(control, async () => {
      try {
        const payload = referenceLibraryPayload(liveReference, this.isGlobalPrivacyMode());
        await this.requireManagedLibrary().characters.replace(
          itemId,
          payload.character,
          { name: payload.name, description: payload.description },
        );
      } catch (error) {
        this.alertReferenceLibraryError(error);
      }
    });
  }

  prepareReferenceLibraryAction(reference) {
    this.controller.flushDebouncedCommit("reference library save", { rerender: false });
    return findLiveReference(this.controller.timeline, reference) ?? reference;
  }

  alertReferenceLibraryError(error) {
    const alertFn = this.container.ownerDocument.defaultView?.alert ?? globalThis.alert;
    alertFn?.(error?.message || "Could not update Director Library character.");
  }

  async saveCurrentProjectToLibrary(control = null) {
    const itemId = projectLibraryItemIdFor(this.controller.timeline);
    if (itemId && !confirmProjectUpdate(this.container.ownerDocument)) return false;
    this.controller.flushDebouncedCommit("project library save", { rerender: false });
    await withDisabledControl(control, async () => {
      try {
        if (itemId) {
          const payload = projectLibraryPayload(this.controller.timeline, itemId, null, this.isGlobalPrivacyMode());
          await this.requireManagedLibrary().projects.replace(
            itemId,
            payload.project,
            { name: payload.name },
          );
          this.stampCurrentProjectLibraryItemId(itemId, { rerender: false });
          return;
        }
        const name = promptForProjectName(this.container.ownerDocument, this.controller.timeline);
        if (!name) return;
        const payload = projectLibraryPayload(this.controller.timeline, "", name, this.isGlobalPrivacyMode());
        const receipt = await this.requireManagedLibrary().projects.create(
          payload.project,
          { name: payload.name },
        );
        const nextItemId = String(receipt?.recordId ?? "").trim();
        if (!nextItemId) throw new Error("Director Library did not return a project id.");
        stampProjectName(this.controller.timeline, name);
        this.stampCurrentProjectLibraryItemId(nextItemId);
      } catch (error) {
        this.alertProjectLibraryError(error);
      }
    });
    return true;
  }

  stampCurrentProjectLibraryItemId(itemId, options = {}) {
    this.commitMutation((timeline) => {
      stampProjectLibraryItemId(timeline, itemId);
    }, "link library project", { pushUndo: false, ...options });
  }

  alertProjectLibraryError(error) {
    const alertFn = this.container.ownerDocument.defaultView?.alert ?? globalThis.alert;
    alertFn?.(error?.message || "Could not update Director Library project.");
  }

  requireManagedLibrary() {
    const library = this.controller.managedPrivacy?.library;
    if (!library) throw new Error("PRIVACY_DIRECTOR_INSTALLATION_BLOCKED");
    return library;
  }

  openReferenceManager() {
    const privacyMode = this.isGlobalPrivacyMode();
    this.referencesOpen = true;
    this.globalSettingsOpen = false;
    this.projectSettingsOpen = false;
    this.globalSettingsDraft = null;
    this.projectSettingsDraft = null;
    if (privacyMode) {
      this.privacyExternalModalOpen = true;
      this.privacyRevealActive = false;
    }
    this.render();
  }

  closeReferenceManager() {
    this.referencesOpen = false;
    if (this.privacyExternalModalOpen) this.privacyExternalModalOpen = false;
    this.render();
  }

  toggleCharacterReferences() {
    if (getCharacterReferences(this.controller.timeline).length === 0) return;
    this.commitMutation((timeline) => {
      timeline.project ??= {};
      timeline.project.metadata ??= {};
      timeline.project.metadata.character_references_enabled = !areCharacterReferencesEnabled(timeline);
    }, "toggle character references");
  }

  async openReferenceImagePicker() {
    try {
      const item = await showMediaPicker({
        assetType: ASSET_TYPE_IMAGE,
        node: this.node,
        documentRef: this.container.ownerDocument,
        mode: "reference",
        privacyMode: this.isGlobalPrivacyMode(),
      });
      if (!item) return;
      this.commitMutation((timeline) => {
        addCharacterReference(timeline, item);
      }, "add reference");
    } catch (error) {
      const alertFn = this.container.ownerDocument.defaultView?.alert ?? globalThis.alert;
      alertFn?.(error.message);
    }
  }

  async copyReferenceTag(reference) {
    const tag = formatCharacterReferenceTag(reference);
    try {
      const writeText = this.container.ownerDocument.defaultView?.navigator?.clipboard?.writeText;
      if (typeof writeText !== "function") throw new Error("Clipboard unavailable");
      await writeText.call(this.container.ownerDocument.defaultView.navigator.clipboard, tag);
    } catch (_error) {
      const promptFn = this.container.ownerDocument.defaultView?.prompt ?? globalThis.prompt;
      promptFn?.("Reference tag", tag);
    }
  }

  insertReferenceTag(reference) {
    const tag = formatCharacterReferenceTag(reference);
    this.commitMutation((timeline) => {
      const section = findSection(timeline, timeline.ui_state.selected_item_id);
      if (!section || !("prompt" in section)) return;
      const current = String(section.prompt ?? "").trimEnd();
      section.prompt = current ? `${current} ${tag}` : tag;
    }, "insert reference tag");
  }

  renderSettingsActions(title, onSave, onCancel) {
    const actions = el("div", "htd-settings-actions");
    let save;
    save = button("Save", `Save ${title}`, () => onSave(save));
    save.classList.add("is-primary");
    actions.append(save, button("Cancel", `Cancel ${title}`, onCancel));
    return actions;
  }

  renderSettingCheckbox(title, path) {
    const row = settingRow(title);
    row.append(toggleButton("On", title, Boolean(getPath(this.controller.timeline, path)), () => {
      this.commitMutation((timeline) => setPath(timeline, path, !getPath(timeline, path)), "settings change");
    }));
    return row;
  }

  renderSettingSelect(title, path, options) {
    const row = settingRow(title);
    row.append(selectControl(title, getPath(this.controller.timeline, path), options, (value) => {
      this.commitMutation((timeline) => setPath(timeline, path, value), "settings change");
    }));
    return row;
  }

  renderDraftSettingSelect(title, draft, path, options) {
    const row = settingRow(title);
    row.append(selectControl(title, getPath(draft, path), options, (value) => {
      setPath(draft, path, value);
    }));
    return row;
  }

  renderDraftSettingNumber(title, draft, path, options = {}) {
    const row = settingRow(title);
    const input = el("input", "htd-setting-number");
    input.type = "number";
    input.step = String(options.step ?? 1);
    if (options.min != null) input.min = String(options.min);
    if (options.max != null) input.max = String(options.max);
    input.value = String(getPath(draft, path) ?? "");
    input.addEventListener("change", () => {
      setPath(draft, path, Number(input.value));
    });
    row.append(input);
    return row;
  }

  renderDraftSettingText(title, draft, path, multiline = false) {
    const row = settingRow(title);
    const input = multiline ? el("textarea", "htd-setting-text") : el("input", "htd-setting-text");
    input.value = getPath(draft, path) ?? "";
    input.addEventListener("change", () => {
      setPath(draft, path, input.value);
    });
    row.append(input);
    return row;
  }

  renderGlobalAssetRootSetting(timeline, draft) {
    const settings = normalizeGlobalSettings(draft);
    const privacyRevealed = !isGlobalPrivacyMode(settings) || (this.privacyRevealActive && !this.privacyExternalModalOpen);
    if (isGlobalPrivacyMode(settings) && !privacyRevealed) {
      return this.renderSettingReadonly("Asset Root Directory", "Private path");
    }
    const row = settingRow("Asset Root Directory");
    const input = el("input", "htd-setting-text");
    input.value = settings.storage.asset_root_directory;
    input.placeholder = settings.storage.default_asset_root_directory;
    input.addEventListener("change", () => {
      setPath(draft, ["storage", "asset_root_directory"], input.value);
      this.controller.globalSettingsError = "";
    });
    row.append(input);
    return row;
  }

  renderGlobalPrivacyKeystoreSetting() {
    const row = settingRow("Privacy Keystore");
    const wrap = el("div", "htd-privacy-keystore-setting");
    const status = el("span", "htd-privacy-keystore-setting-status");
    const state = this.controller.managedPrivacy?.pack?.session?.state?.state;
    status.textContent = state === "unlocked"
      ? "Unlocked · managed by Helto Privacy"
      : state === "setup-required"
        ? "Setup required · use Helto Privacy controls"
        : state === "locked"
          ? "Locked · use Helto Privacy controls"
          : "Shared privacy unavailable";
    wrap.append(status);
    row.append(wrap);
    return row;
  }

  renderGlobalSettingCheckbox(title, draft, path) {
    const row = settingRow(title);
    row.append(toggleButton("On", title, Boolean(getPath(draft, path)), () => {
      setPath(draft, path, !getPath(draft, path));
      this.controller.globalSettingsError = "";
      this.render();
    }));
    return row;
  }

  renderGlobalSettingNumber(title, draft, path, options = {}) {
    const row = settingRow(title);
    const input = el("input", "htd-setting-number");
    input.type = "number";
    input.step = String(options.step ?? 1);
    if (options.min != null) input.min = String(options.min);
    if (options.max != null) input.max = String(options.max);
    input.value = String(getPath(draft, path) ?? "");
    input.addEventListener("change", () => {
      setPath(draft, path, Number(input.value));
      this.controller.globalSettingsError = "";
    });
    row.append(input);
    return row;
  }

  renderSettingReadonly(title, value) {
    const row = settingRow(title);
    const output = el("input", "htd-setting-text htd-setting-readonly");
    output.value = String(value ?? "");
    output.readOnly = true;
    output.title = title;
    row.append(output);
    return row;
  }

  renderSettingNumber(title, path, options = {}) {
    const row = settingRow(title);
    const input = el("input", "htd-setting-number");
    input.type = "number";
    input.step = String(options.step ?? 1);
    if (options.min != null) input.min = String(options.min);
    if (options.max != null) input.max = String(options.max);
    input.value = String(getPath(this.controller.timeline, path) ?? "");
    input.addEventListener("change", () => {
      this.commitMutation((timeline) => setPath(timeline, path, Number(input.value)), "settings change");
    });
    row.append(input);
    return row;
  }

  renderSettingText(title, path, multiline = false) {
    const row = settingRow(title);
    const input = multiline ? el("textarea", "htd-setting-text") : el("input", "htd-setting-text");
    input.value = getPath(this.controller.timeline, path) ?? "";
    input.addEventListener("change", () => {
      this.commitMutation((timeline) => setPath(timeline, path, input.value), "settings change");
    });
    row.append(input);
    return row;
  }

  renderProjectLoraSettings(timeline, options = {}) {
    const block = el("div", "htd-project-loras");
    const title = el("div", "htd-project-loras-title");
    title.textContent = "Project Model LoRAs";
    block.append(
      title,
      ...modelLoraTargetDescriptors().map((descriptor) => this.renderProjectLoraStackRow(
        timeline,
        descriptor.label,
        descriptor.modelKey,
        descriptor.targetKey,
        options,
      )),
    );
    return block;
  }

  renderProjectLoraStackRow(timeline, labelText, modelKey, targetKey, options = {}) {
    const stack = timeline.project?.model_loras?.global?.[modelKey]?.[targetKey] ?? { loras: [], ui: {} };
    const row = el("div", "htd-project-lora-row");
    const label = el("span", "htd-project-lora-label");
    label.textContent = labelText;
    const summary = this.renderLoraStackSummary(stack, { privacyRevealed: this.isPrivacyRevealed(timeline) });
    const onEdit = options.draftMode
      ? () => this.openProjectLoraStackEditor(labelText, modelKey, targetKey, { draft: timeline })
      : () => this.openProjectLoraStackEditor(labelText, modelKey, targetKey);
    const onClear = options.draftMode
      ? () => {
          clearProjectModelLoraStack(timeline, modelKey, targetKey);
          this.render();
        }
      : () => this.commitMutation((currentTimeline) => {
          clearProjectModelLoraStack(currentTimeline, modelKey, targetKey);
        }, "project lora clear target");
    row.append(label, summary, this.renderLoraTargetActions({
      timeline,
      labelText,
      stack,
      onEdit,
      onClear,
    }));
    return row;
  }

  renderLoraSummaryRow(labelText, stack, options = {}) {
    const row = el("div", "htd-lora-summary-row");
    const label = el("span", "htd-shot-row-label");
    label.textContent = labelText;
    row.append(label, this.renderLoraStackSummary(stack, options));
    return row;
  }

  renderLoraStackSummary(stack, options = {}) {
    const value = el("span", "htd-lora-count");
    const count = stack?.loras?.length ?? 0;
    value.textContent = `${count} LoRAs`;
    value.title = options.privacyRevealed === false
      ? "Private LoRA stack"
      : stack?.loras?.map((lora) => lora.name).join(", ") || value.textContent;
    return value;
  }

  renderLoraTargetActions({ timeline, labelText, stack, onEdit, onClear }) {
    const actions = el("span", "htd-lora-actions");
    const locked = this.isLoraEditingLocked(timeline);
    const editTitle = locked ? "Reveal privacy before editing LoRAs" : `Edit ${labelText} LoRAs`;
    const clearTitle = locked ? "Reveal privacy before clearing LoRAs" : `Clear ${labelText} LoRAs`;
    const edit = iconButton("lora", editTitle, onEdit, { disabled: locked });
    const clear = iconButton("delete", clearTitle, onClear, { disabled: locked || !(stack?.loras?.length > 0) });
    actions.append(edit, clear);
    return actions;
  }

  openProjectLoraStackEditor(labelText, modelKey, targetKey, options = {}) {
    const timeline = options.draft ?? this.controller.timeline;
    if (this.isLoraEditingLocked(timeline)) return;
    const stack = timeline.project?.model_loras?.global?.[modelKey]?.[targetKey];
    showTimelineLoraStackEditor({
      documentRef: this.container.ownerDocument ?? globalThis.document,
      title: `${labelText} LoRAs`,
      stack,
      profile: loraEditorProfileForTarget(modelKey, targetKey),
      onSave: (nextStack) => {
        if (options.draft) {
          setProjectModelLoraStack(timeline, modelKey, targetKey, nextStack);
          this.render();
          return;
        }
        this.commitMutation((currentTimeline) => {
          setProjectModelLoraStack(currentTimeline, modelKey, targetKey, nextStack);
        }, "project lora edit");
      },
    });
  }

  openShotLoraStackEditor(shotId, labelText, modelKey, targetKey) {
    const timeline = this.controller.timeline;
    if (this.isLoraEditingLocked(timeline)) return;
    const shot = findShot(timeline, shotId);
    const stack = shot?.lora_overrides?.targets?.[modelKey]?.[targetKey];
    showTimelineLoraStackEditor({
      documentRef: this.container.ownerDocument ?? globalThis.document,
      title: `${labelText} Shot LoRAs`,
      stack,
      profile: loraEditorProfileForTarget(modelKey, targetKey),
      onSave: (nextStack) => {
        this.commitMutation((currentTimeline) => {
          setShotLoraTargetStack(currentTimeline, shotId, modelKey, targetKey, nextStack);
        }, "shot lora edit");
      },
    });
  }

  isLoraEditingLocked(timeline) {
    return Boolean(this.isGlobalPrivacyMode() && !this.isPrivacyRevealed(timeline));
  }

  startSectionDrag(event, section, mode) {
    this.startItemDrag(event, {
      itemId: section.item_id,
      mode,
      startStart: section.start_time,
      startEnd: section.end_time,
    });
  }

  startAudioDrag(event, clip, mode) {
    this.startItemDrag(event, {
      itemId: clip.item_id,
      mode,
      startStart: clip.start_time,
      startEnd: clip.end_time,
    });
  }

  startShotDrag(event, shot) {
    this.startItemDrag(event, {
      itemId: shot.shot_id,
      mode: "shot-move",
      startStart: shot.start_time,
      startEnd: shot.end_time,
    });
  }

  startItemDrag(event, dragState) {
    if (event.button != null && event.button !== 0) {
      event.stopPropagation();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget.closest(".htd-item");
    if (dragState.mode === "move" || dragState.mode === "audio-move" || dragState.mode === "shot-move") {
      const handledSelectionOnly = this.handlePointerSelection(event, dragState.itemId, target);
      if (handledSelectionOnly) return;
    } else {
      this.commitMutation((timeline) => selectItem(timeline, dragState.itemId), "select", { pushUndo: false, rerender: false });
      this.focusTimelineItem(dragState.itemId, target);
    }
    target?.setPointerCapture?.(event.pointerId);
    const selectedIds = getSelectedItemIds(this.controller.timeline);
    const preserveMultiSelection = selectedIds.length > 1 && selectedIds.includes(dragState.itemId);
    this.focusTimelineItem(dragState.itemId, target);
    this.controller.beginTimelineGesture();
    const moveTarget = target?.ownerDocument ?? this.container.ownerDocument ?? globalThis.document;
    const timeContainer = target?.closest?.(".htd-track") ?? this.container.querySelector?.(".htd-ruler") ?? this.container;
    const pointerTime = timeFromClientX(event.clientX, timeContainer, this.controller.timeline, this.viewportWidth);
    const pointerEdgeTime = dragState.mode === "start" || dragState.mode === "audio-start"
      ? Number(dragState.startStart)
      : Number(dragState.startEnd);
    this.drag = {
      ...dragState,
      timeContainer,
      pointerTimeOffset: pointerTime - Number(dragState.startStart),
      pointerEdgeTimeOffset: pointerTime - pointerEdgeTime,
      moveTarget,
      captureTarget: target,
      hasMoved: false,
      pendingSingleSelectOnClick: preserveMultiSelection ? dragState.itemId : null,
    };
    moveTarget?.addEventListener("pointermove", this.onPointerMove);
    moveTarget?.addEventListener("pointerup", this.onPointerUp);
    moveTarget?.addEventListener("pointercancel", this.onPointerUp);
  }

  startRangeDrag(event, mode, bar) {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget?.setPointerCapture?.(event.pointerId);
    this.controller.beginTimelineGesture();
    const moveTarget = bar?.ownerDocument ?? this.container.ownerDocument ?? globalThis.document;
    this.drag = { mode, bar, moveTarget, captureTarget: event.currentTarget };
    moveTarget?.addEventListener("pointermove", this.onPointerMove);
    moveTarget?.addEventListener("pointerup", this.onPointerUp);
    moveTarget?.addEventListener("pointercancel", this.onPointerUp);
  }

  onPointerMove = (event) => {
    if (!this.drag) return;
    if (this.drag.mode === "range-start" || this.drag.mode === "range-end") {
      const timeline = this.controller.timeline;
      setTimelineRangeBoundary(timeline, this.drag.mode, rangeSecondFromClientX(event.clientX, this.drag.bar, timeline));
      this.render(timeline);
      this.drag.bar = this.container.querySelector(".htd-range-bar") ?? this.drag.bar;
      return;
    }
    const timeline = this.controller.timeline;
    const pointerTime = this.dragTimeFromClientX(event.clientX);
    if (this.drag.mode === "move") {
      this.drag.hasMoved = true;
      moveSelectedItems(timeline, this.drag.itemId, pointerTime - this.drag.pointerTimeOffset);
    } else if (this.drag.mode === "shot-move") {
      this.drag.hasMoved = true;
      moveSelectedItems(timeline, this.drag.itemId, pointerTime - this.drag.pointerTimeOffset);
    } else if (this.drag.mode === "start") {
      resizeSection(timeline, this.drag.itemId, "start", pointerTime - this.drag.pointerEdgeTimeOffset, { globalSettings: this.globalSettings() });
    } else if (this.drag.mode === "audio-move") {
      this.drag.hasMoved = true;
      moveSelectedItems(timeline, this.drag.itemId, pointerTime - this.drag.pointerTimeOffset);
    } else if (this.drag.mode === "audio-start") {
      resizeAudioClip(timeline, this.drag.itemId, "start", pointerTime - this.drag.pointerEdgeTimeOffset, { globalSettings: this.globalSettings() });
    } else if (this.drag.mode === "audio-end") {
      resizeAudioClip(timeline, this.drag.itemId, "end", pointerTime - this.drag.pointerEdgeTimeOffset, { globalSettings: this.globalSettings() });
    } else {
      resizeSection(timeline, this.drag.itemId, "end", pointerTime - this.drag.pointerEdgeTimeOffset, { globalSettings: this.globalSettings() });
    }
    this.render(timeline);
    this.drag.timeContainer = this.findDragTimeContainer(this.drag.itemId) ?? this.drag.timeContainer;
  };

  onPointerUp = (event) => {
    const moveTarget = this.drag?.moveTarget;
    const captureTarget = this.drag?.captureTarget;
    const pendingSingleSelectOnClick = this.drag?.pendingSingleSelectOnClick;
    const hasMoved = this.drag?.hasMoved;
    captureTarget?.releasePointerCapture?.(event.pointerId);
    moveTarget?.removeEventListener("pointermove", this.onPointerMove);
    moveTarget?.removeEventListener("pointerup", this.onPointerUp);
    moveTarget?.removeEventListener("pointercancel", this.onPointerUp);
    this.drag = null;
    this.controller.endTimelineGesture("drag end");
    if (pendingSingleSelectOnClick && !hasMoved) {
      this.commitMutation((timeline) => selectItem(timeline, pendingSingleSelectOnClick), "select", { pushUndo: false });
      this.focusTimelineItem(pendingSingleSelectOnClick);
    }
  };

  handlePointerSelection(event, itemId, target) {
    if (event.shiftKey) {
      this.commitMutation((timeline) => selectItemRange(timeline, itemId), "select range", { pushUndo: false });
      this.focusTimelineItem(itemId, target);
      return true;
    }
    if (event.ctrlKey || event.metaKey) {
      this.commitMutation((timeline) => toggleSelectItem(timeline, itemId), "toggle selection", { pushUndo: false });
      this.focusTimelineItem(itemId, target);
      return true;
    }
    const selectedIds = getSelectedItemIds(this.controller.timeline);
    if (selectedIds.length > 1 && selectedIds.includes(itemId)) {
      return false;
    }
    this.commitMutation((timeline) => selectItem(timeline, itemId), "select", { pushUndo: false, rerender: false });
    this.focusTimelineItem(itemId, target);
    return false;
  }

  commitMutation(mutator, reason, options = {}) {
    this.controller.updateTimeline(mutator, reason, options);
  }

  focusTimelineItem(itemId, fallbackTarget = null) {
    const target = fallbackTarget ?? Array.from(this.container.querySelectorAll?.(".htd-item, .htd-shot-band, .htd-boundary-control") ?? [])
      .find((item) => item.dataset?.itemId === itemId);
    target?.focus?.({ preventScroll: true });
  }

  dragTimeFromClientX(clientX) {
    const timeContainer = this.drag?.timeContainer ?? this.findDragTimeContainer(this.drag?.itemId) ?? this.container;
    return timeFromClientX(clientX, timeContainer, this.controller.timeline, this.viewportWidth);
  }

  findDragTimeContainer(itemId) {
    const item = Array.from(this.container.querySelectorAll?.(".htd-item") ?? [])
      .find((candidate) => candidate.dataset?.itemId === itemId);
    return item?.closest?.(".htd-track") ?? null;
  }

  showItemContextMenu(event, itemId, itemType) {
    event.preventDefault();
    event.stopPropagation();
    this.closeContextMenu({ rerender: false });
    this.openMenu = null;
    this.setPrivacyRevealActive(true);
    if (!isItemSelected(this.controller.timeline, itemId)) {
      this.commitMutation((timeline) => selectItem(timeline, itemId), "select", { pushUndo: false });
    }
    this.focusTimelineItem(itemId);

    const documentRef = this.container.ownerDocument ?? globalThis.document;
    const menu = this.renderItemContextMenu(itemId, itemType, event.clientX, event.clientY, documentRef);
    (documentRef?.body ?? this.container).append(menu);
    this.contextMenuElement = menu;
    this.contextMenuDocument = documentRef;
    this.contextMenuDocument?.addEventListener?.("pointerdown", this.onContextMenuPointerDown, true);
    this.contextMenuDocument?.addEventListener?.("keydown", this.onContextMenuKeyDown, true);
  }

  renderItemContextMenu(itemId, itemType, clientX, clientY, documentRef) {
    const menu = el("div", "htd-context-menu");
    const viewport = documentRef?.defaultView ?? globalThis.window;
    const viewportWidth = Number(viewport?.innerWidth ?? documentRef?.documentElement?.clientWidth ?? 0);
    const viewportHeight = Number(viewport?.innerHeight ?? documentRef?.documentElement?.clientHeight ?? 0);
    const left = clampNumber(Number(clientX), 4, Math.max(4, viewportWidth - 150), 4);
    const top = clampNumber(Number(clientY), 4, Math.max(4, viewportHeight - 32), 4);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.setAttribute("role", "menu");
    menu.dataset.itemId = itemId;

    const appendMenuItem = (label, onClick) => {
      const item = el("button", "htd-context-menu-item");
      item.type = "button";
      item.textContent = label;
      item.title = label;
      item.setAttribute("role", "menuitem");
      item.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.closeContextMenu({ rerender: false });
        onClick();
      });
      menu.append(item);
    };

    const selectedCount = getSelectedItemIds(this.controller.timeline).length;
    const selectedSection = selectedCount === 1 && this.controller.timeline.ui_state.selected_item_id === itemId
      ? findSection(this.controller.timeline, itemId)
      : null;
    const previewData = selectedSection?.type === ASSET_TYPE_IMAGE
      ? this.sectionImagePreviewData(selectedSection)
      : null;
    if (previewData) {
      appendMenuItem("Preview image", () => {
        this.openSectionMediaPreviewData(previewData);
      });
    }

    const replaceLabel = replaceLabelForItemType(itemType);
    if (replaceLabel && selectedCount === 1) {
      appendMenuItem(replaceLabel, () => {
        this.openMediaPicker(itemType, { mode: "replace", itemId });
      });
    }

    appendMenuItem(selectedCount > 1 ? "Duplicate Selection" : duplicateLabelForItemType(itemType), () => {
      this.commitMutation((timeline) => duplicateSelectedSection(timeline), "duplicate");
    });

    appendMenuItem(selectedCount > 1 ? "Delete Selection" : deleteLabelForItemType(itemType), () => {
      this.commitMutation((timeline) => deleteSelectedItem(timeline), "delete");
    });
    return menu;
  }

  sectionImagePreviewData(section) {
    if (section?.type !== ASSET_TYPE_IMAGE) return null;
    const timeline = this.controller.timeline;
    if (this.isGlobalPrivacyMode() && (!this.privacyRevealActive || this.privacyExternalModalOpen)) return null;
    const asset = resolveMediaReference(timeline, section.image);
    const url = this.node?._timelineMediaCache?.requestView?.(asset);
    if (!url) return null;
    return {
      type: ASSET_TYPE_IMAGE,
      url,
      caption: mediaLabel(timeline, section.image, sectionLabel(timeline, section, this.globalSettings())),
    };
  }

  takeVideoPreviewData(timeline, take, asset = null, privacyRevealed = this.isPrivacyRevealed(timeline)) {
    const resolvedAsset = asset ?? assetForId(timeline, take?.asset_id);
    if (!resolvedAsset || resolvedAsset.type !== ASSET_TYPE_VIDEO) return null;
    const url = this.node?._timelineMediaCache?.requestView?.(resolvedAsset);
    if (!url) return null;
    return {
      type: ASSET_TYPE_VIDEO,
      url,
      caption: assetDisplayLabel(resolvedAsset, privacyRevealed, "Video Take"),
      privacyMode: this.isGlobalPrivacyMode(),
    };
  }

  captureVideoPreviewData(timeline, item, privacyRevealed = this.isPrivacyRevealed(timeline)) {
    const registrationAsset = item?.take_capture?.registration?.asset ?? {};
    const path = String(item?.path ?? item?.file_path ?? registrationAsset.path ?? registrationAsset.file_path ?? "").trim();
    if (!path) return null;
    const asset = {
      ...registrationAsset,
      type: ASSET_TYPE_VIDEO,
      path,
      name: item?.name ?? item?.filename ?? registrationAsset.name ?? "Captured video",
      source_type: item?.source_type ?? registrationAsset.source_type ?? "",
    };
    const url = this.node?._timelineMediaCache?.requestView?.(asset);
    if (!url) return null;
    return {
      type: ASSET_TYPE_VIDEO,
      url,
      caption: captureSummaryLabel(item, privacyRevealed),
      privacyMode: this.isGlobalPrivacyMode(),
    };
  }

  openSectionMediaPreview(section) {
    const previewData = this.sectionImagePreviewData(section);
    if (!previewData) return false;
    return this.openSectionMediaPreviewData(previewData);
  }

  openSectionMediaPreviewData(previewData) {
    if (!previewData?.url) return false;
    showMediaPreview(this.container.ownerDocument ?? globalThis.document, previewData);
    return true;
  }

  openTakeVideoPreviewData(previewData) {
    if (!previewData?.url) return false;
    showMediaPreview(this.container.ownerDocument ?? globalThis.document, previewData);
    return true;
  }

  async deleteProjectTakeCaptureFromItem(shot, item, options = {}) {
    const path = String(options.path ?? captureMediaPath(item)).trim();
    await this.deleteProjectTakePath(shot?.shot_id, path, {
      takeId: options.takeId || captureTakeId(item),
      takeReference: item?.takeReference,
      label: options.label || captureSummaryLabel(item, this.isPrivacyRevealed(this.controller.timeline)),
    });
  }

  async attachManagedProjectCapture(shot, item, accept = false) {
    const timeline = this.controller.timeline;
    const projectRecordId = String(
      timeline?.project?.metadata?.library_item_id ?? "",
    ).trim();
    const media = this.controller.managedPrivacy?.media;
    if (
      !media?.attachProjectTake
      || !item?.sourceReference
      || !item?.takeReference
    ) {
      throw new Error("PRIVACY_DIRECTOR_PROJECT_TAKE_ATTACH_UNAVAILABLE");
    }
    await media.attachProjectTake(
      this.node,
      deepClone(timeline),
      item.sourceReference,
      item.takeReference,
      {
        accept: accept === true,
        projectRecordId,
        shotId: shot?.shot_id,
      },
    );
  }

  async deleteProjectTakeFromTimelineTake(shot, take, asset, options = {}) {
    const path = String(asset?.path ?? asset?.file_path ?? "").trim();
    await this.deleteProjectTakePath(shot?.shot_id, path, {
      takeId: take?.take_id,
      label: options.label || takeStatusLabel(take),
    });
  }

  async deleteProjectTakePath(shotId, path, options = {}) {
    const timeline = this.controller.timeline;
    const privacyRevealed = this.isPrivacyRevealed(timeline);
    const label = privacyRevealed ? (options.label || "this take") : "this private take";
    const confirmFn = this.container.ownerDocument.defaultView?.confirm ?? globalThis.confirm;
    if (!path && !options.takeId && !options.takeReference) {
      const alertFn = this.container.ownerDocument.defaultView?.alert ?? globalThis.alert;
      alertFn?.("Could not delete project take files.");
      return false;
    }
    if (!confirmFn?.(`Remove ${label} from the timeline and delete any remaining project take files?`)) return false;
    try {
      if (options.takeReference) {
        await deleteProjectTakeCapture(
          this.controller.managedPrivacy?.media,
          options.takeReference,
        );
      }
      if (path || options.takeId) {
        this.commitMutation((currentTimeline) => {
          deleteTakesByAssetPath(currentTimeline, shotId, path, options.takeId);
        }, "delete take");
      }
      const liveShot = findShot(this.controller.timeline, shotId) ?? { shot_id: shotId };
      this.requestAvailableCaptures(this.controller.timeline, liveShot, { force: true, rerender: true });
      return true;
    } catch (error) {
      const alertFn = this.container.ownerDocument.defaultView?.alert ?? globalThis.alert;
      alertFn?.(error?.message || "Could not delete project take files.");
      return false;
    }
  }

  closeContextMenu(options = {}) {
    const hadMenu = Boolean(this.contextMenuElement);
    this.contextMenuDocument?.removeEventListener?.("pointerdown", this.onContextMenuPointerDown, true);
    this.contextMenuDocument?.removeEventListener?.("keydown", this.onContextMenuKeyDown, true);
    this.contextMenuDocument = null;
    this.contextMenuElement?.remove?.();
    this.contextMenuElement = null;
    if (hadMenu && this.isGlobalPrivacyMode() && !this.container.matches?.(":hover")) {
      this.privacyRevealActive = false;
    }
    if (options.rerender) this.render();
  }

  handleContextMenuPointerDown(event) {
    if (this.contextMenuElement?.contains?.(event.target)) return;
    this.closeContextMenu({ rerender: false });
  }

  handleContextMenuKeyDown(event) {
    if (event.key !== "Escape") return;
    event.preventDefault?.();
    event.stopPropagation?.();
    this.closeContextMenu({ rerender: false });
  }

  async openMediaPicker(assetType, options = {}) {
    try {
      const item = await showMediaPicker({
        assetType,
        node: this.node,
        documentRef: this.container.ownerDocument,
        mode: options.mode ?? "add",
        privacyMode: this.isGlobalPrivacyMode(),
        managedMedia: this.controller.managedPrivacy?.media,
      });
      if (!item) return;
      const managedMedia = this.controller.managedPrivacy?.media;
      if (!managedMedia?.attachSource || !item.reference) {
        throw new Error("PRIVACY_DIRECTOR_MEDIA_UNAVAILABLE");
      }
      if (options.mode === "attach-generated-take") {
        throw new Error("PRIVACY_DIRECTOR_PROJECT_TAKE_ATTACH_UNAVAILABLE");
      }
      const timeline = deepClone(this.controller.timeline);
      let itemId = options.itemId;
      if (options.mode !== "replace") {
        const target = assetType === ASSET_TYPE_AUDIO
          ? addAudioClip(
            timeline,
            Number(timeline.ui_state?.playhead_time ?? 0),
            Number.isFinite(item.duration_seconds) && item.duration_seconds > 0
              ? item.duration_seconds : 1,
          )
          : addSection(timeline, assetType);
        itemId = target?.item_id;
      }
      if (!itemId) throw new Error("PRIVACY_DIRECTOR_MEDIA_TARGET_UNAVAILABLE");
      await managedMedia.attachSource(this.node, timeline, item.reference, {
        assetType,
        itemId,
      });
    } catch (error) {
      const alertFn = this.container.ownerDocument.defaultView?.alert ?? globalThis.alert;
      alertFn?.(error.message);
    }
  }

  openPromptOptimizer() {
    const privacyMode = this.isGlobalPrivacyMode();
    if (privacyMode) {
      this.privacyExternalModalOpen = true;
      this.privacyRevealActive = false;
      this.render();
    }
    showPromptOptimizer({
      timeline: this.controller.timeline,
      node: this.node,
      app: this.app,
      documentRef: this.container.ownerDocument,
      privacyMode,
      mediaCache: this.node?._timelineMediaCache,
      onClose: () => {
        if (!this.privacyExternalModalOpen) return;
        this.privacyExternalModalOpen = false;
        this.render();
      },
      onApply: (updates) => {
        this.commitMutation((timeline) => {
          for (const section of timeline.director_track.sections) {
            if (Object.prototype.hasOwnProperty.call(updates, section.item_id)) {
              section.prompt = updates[section.item_id] ?? "";
            }
          }
        }, "prompt optimizer apply");
      },
    });
  }

  openDirectorLibrary() {
    const privacyMode = this.isGlobalPrivacyMode();
    if (privacyMode) {
      this.privacyExternalModalOpen = true;
      this.privacyRevealActive = false;
      this.render();
    }
    showDirectorLibrary({
      timeline: this.controller.timeline,
      node: this.node,
      app: this.app,
      controller: this.controller,
      documentRef: this.container.ownerDocument,
      privacyMode,
      mediaCache: this.node?._timelineMediaCache,
      onClose: () => {
        if (!this.privacyExternalModalOpen) return;
        this.privacyExternalModalOpen = false;
        this.render();
      },
    });
  }

  handleZoomToFit() {
    this.commitMutation((timeline) => zoomToFit(timeline), "zoom to fit");
  }

  measureViewportWidth() {
    return measureStableTimelineViewportWidth(this.node, this.container);
  }

  handleNodeResize() {
    const measuredWidth = this.measureViewportWidth();
    this.applyViewportContainerWidth(measuredWidth);
    this.ensureNodeFitsContent(this.controller.timeline);
    const measuredHeight = this.getRenderedHeight(this.controller.timeline);
    const widthChanged = Math.abs(measuredWidth - this.viewportWidth) >= 1;
    const heightChanged = Math.abs(measuredHeight - this.renderedHeight) >= 1;
    if (!widthChanged && !heightChanged) {
      this.applyWidgetContainerHeight(measuredHeight, this.contentHeight);
      return;
    }
    this.viewportWidth = measuredWidth;
    this.render(this.controller.timeline);
  }

  applyViewportContainerWidth(width) {
    const stableWidth = Math.max(1, Number(width) || TIMELINE_WIDTH);
    this.container.style.width = `${stableWidth}px`;
    this.container.style.maxWidth = `${stableWidth}px`;
    if (this.container.parentElement) {
      this.container.parentElement.style.width = `${stableWidth}px`;
      this.container.parentElement.style.maxWidth = `${stableWidth}px`;
    }
  }

  getRenderedHeight(timeline = this.controller.timeline) {
    return getTimelineWidgetRenderedHeight(this.node, this.widget, timeline, this.contentHeight);
  }

  applyWidgetContainerHeight(renderedHeight, contentHeight = this.contentHeight) {
    const stableHeight = Math.max(1, Number(renderedHeight) || getTimelineWidgetHeight(this.controller.timeline));
    const stableContentHeight = Math.max(1, Number(contentHeight) || stableHeight);
    this.renderedHeight = stableHeight;
    this.container.style.height = `${stableHeight}px`;
    this.container.style.minHeight = `${stableContentHeight}px`;
    if (this.container.parentElement) {
      this.container.parentElement.style.height = `${stableHeight}px`;
      this.container.parentElement.style.minHeight = `${stableContentHeight}px`;
    }
  }

  ensureNodeFitsContent(timeline = this.controller.timeline) {
    return ensureTimelineNodeFitsContent(this.node, this.widget, timeline, this.contentHeight, this.app);
  }

  updateMeasuredContentHeight(timeline = this.controller.timeline) {
    const measuredHeight = measureIntrinsicTimelineContentHeight(this.container, this.viewportWidth, timeline);
    if (measuredHeight <= this.contentHeight + 1) return;
    this.contentHeight = measuredHeight;
    this.ensureNodeFitsContent(timeline);
    const renderedHeight = this.getRenderedHeight(timeline);
    this.applyWidgetContainerHeight(renderedHeight, this.contentHeight);
    this.applyRenderedInspectorHeight(timeline, renderedHeight);
  }

  applyRenderedInspectorHeight(timeline, renderedHeight) {
    const inspector = this.container.querySelector?.(".htd-inspector");
    if (!inspector) return;
    inspector.style.height = `${getRenderedInspectorHeight(timeline, renderedHeight)}px`;
  }

  scheduleViewportRemeasure() {
    if (this.remeasureHandle != null) return;
    const windowRef = this.container.ownerDocument?.defaultView ?? globalThis;
    const requestFrame = windowRef.requestAnimationFrame ?? ((callback) => windowRef.setTimeout(callback, 0));
    this.remeasureHandle = requestFrame(() => {
      this.remeasureHandle = null;
      const measuredWidth = this.measureViewportWidth();
      if (Math.abs(measuredWidth - this.viewportWidth) < 1) return;
      this.viewportWidth = measuredWidth;
      this.applyViewportContainerWidth(this.viewportWidth);
      this.render(this.controller.timeline);
    });
  }

  cancelViewportRemeasure() {
    if (this.remeasureHandle == null) return;
    const windowRef = this.container.ownerDocument?.defaultView ?? globalThis;
    if (windowRef.cancelAnimationFrame) {
      windowRef.cancelAnimationFrame(this.remeasureHandle);
    } else {
      windowRef.clearTimeout?.(this.remeasureHandle);
    }
    this.remeasureHandle = null;
  }

  startResizeObserver() {
    const ResizeObserverRef = this.container.ownerDocument?.defaultView?.ResizeObserver ?? globalThis.ResizeObserver;
    if (!ResizeObserverRef || this.resizeObserver) return;
    this.resizeObserver = new ResizeObserverRef((entries) => {
      const width = Number(entries?.[0]?.contentRect?.width ?? 0);
      if (!width || Math.abs(width - Number(this.observedWidth ?? 0)) < 1) return;
      this.observedWidth = width;
      this.scheduleViewportRemeasure();
    });
    this.resizeObserver.observe(this.container);
  }

  stopResizeObserver() {
    this.resizeObserver?.disconnect?.();
    this.resizeObserver = null;
    this.observedWidth = null;
  }
}

export function mountTimelineRenderer(node, app, controller) {
  if (node._timelineRenderer) return node._timelineRenderer;
  const container = document.createElement("div");
  const widgetContentHeight = () => Math.max(
    getTimelineWidgetHeight(controller.timeline),
    Number(node._timelineRenderer?.contentHeight ?? 0) || 0,
  );
  let widget = null;
  const widgetRenderedHeight = () => getTimelineWidgetRenderedHeight(node, widget, controller.timeline, widgetContentHeight());
  widget = node.addDOMWidget?.("video_timeline_director", "VideoTimelineDirector", container, {
    serialize: false,
    hideOnZoom: false,
    getMinHeight: widgetContentHeight,
    getMaxHeight: widgetRenderedHeight,
    getHeight: widgetRenderedHeight,
  });
  const renderer = new TimelineRenderer(node, app, controller, container, widget);
  node._timelineRenderer = renderer;
  node._timelineRendererWidget = widget;
  return renderer;
}

export function unmountTimelineRenderer(node) {
  node?._timelineRenderer?.destroy();
  delete node._timelineRenderer;
  delete node._timelineRendererWidget;
}

function computeGaps(timeline) {
  const duration = Number(timeline.project.duration_seconds);
  const sections = [...timeline.director_track.sections].sort((a, b) => a.start_time - b.start_time);
  const gaps = [];
  let cursor = 0;
  for (const section of sections) {
    if (section.start_time > cursor) gaps.push({ start_time: cursor, end_time: section.start_time });
    cursor = Math.max(cursor, section.end_time);
  }
  if (cursor < duration) gaps.push({ start_time: cursor, end_time: duration });
  return gaps;
}

function assemblyReadinessStatus(timeline, shot) {
  const accepted = (shot.takes ?? []).find((take) => take.take_id === shot.accepted_take_id);
  if (accepted && assetForId(timeline, accepted.asset_id)) return "Ready: accepted take";
  if (accepted) return "Blocked: missing take asset";
  if (shot.clip_instance?.asset_id) {
    return assetForId(timeline, shot.clip_instance.asset_id)
      ? "Ready: clip instance"
      : "Blocked: missing clip asset";
  }
  if ((shot.takes ?? []).some((take) => take.status === "Candidate")) return "Needs accepted take";
  return "Needs generated or imported clip";
}

function assemblyReadinessPillText(status) {
  if (String(status ?? "").startsWith("Ready:")) return "Ready";
  if (String(status ?? "").startsWith("Blocked:")) return "Blocked";
  if (status === "Needs accepted take") return "Needs take";
  return "Needs generation";
}

function assemblyReadinessPillTone(status) {
  if (String(status ?? "").startsWith("Ready:")) return "is-ready";
  if (String(status ?? "").startsWith("Blocked:")) return "is-blocked";
  if (status === "Needs accepted take") return "is-needs-take";
  return "is-needs-generation";
}

function shotBoundaryContext(timeline, shot) {
  const shots = [...(timeline.sequence?.shots ?? [])]
    .sort((a, b) => Number(a.start_time) - Number(b.start_time) || Number(a.end_time) - Number(b.end_time) || String(a.shot_id).localeCompare(String(b.shot_id)));
  const index = shots.findIndex((candidate) => candidate.shot_id === shot.shot_id);
  const previous = index > 0 ? shots[index - 1] : null;
  const next = index >= 0 && index < shots.length - 1 ? shots[index + 1] : null;
  return {
    incoming: previous ? findBoundaryBetweenShots(timeline, previous.shot_id, shot.shot_id) : null,
    outgoing: next ? findBoundaryBetweenShots(timeline, shot.shot_id, next.shot_id) : null,
  };
}

function shotDisplayLabel(timeline, shot) {
  const name = String(shot?.name ?? "").trim();
  if (name) return name;
  const shots = [...(timeline?.sequence?.shots ?? [])]
    .sort((a, b) => Number(a.start_time) - Number(b.start_time) || Number(a.end_time) - Number(b.end_time) || String(a.shot_id).localeCompare(String(b.shot_id)));
  const index = shots.findIndex((candidate) => candidate.shot_id === shot?.shot_id);
  return `Shot ${index >= 0 ? index + 1 : 1}`;
}

function boundaryWarningForShot(timeline, context) {
  const ids = new Set([
    context.incoming?.boundary_id,
    context.outgoing?.boundary_id,
  ].filter(Boolean));
  return (timeline.validation?.warnings ?? []).find((entry) => (
    entry.code === "BOUNDARY_LORA_STACK_MISMATCH" && ids.has(entry.item_id)
  )) ?? null;
}

function takeSummaryLabel(timeline, take, privacyRevealed) {
  const parts = [privacyRevealed ? take.take_id : takeStatusLabel(take)];
  const asset = assetForId(timeline, take.asset_id);
  if (asset) parts.push(assetDisplayLabel(asset, privacyRevealed, "Video Asset"));
  const model = [take.model_family, take.model_version].filter(Boolean).join(" ");
  if (model) parts.push(privacyRevealed ? model : "Model");
  if (take.seed != null) parts.push(privacyRevealed ? `seed ${take.seed}` : "Seeded");
  const loraCount = resolvedLoraCount(take.resolved_loras);
  if (loraCount > 0) parts.push(`${loraCount} LoRA${loraCount === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

function takeStatusLabel(take) {
  const status = TAKE_STATUSES.includes(take?.status) ? take.status : "Candidate";
  return `${status} take`;
}

function assetDisplayLabel(asset, privacyRevealed, fallback = "Asset") {
  if (!asset) return fallback;
  if (!privacyRevealed) return fallback;
  return asset.name || asset.path || asset.file_path || asset.asset_id || fallback;
}

function assetSummaryLabel(asset, privacyRevealed) {
  if (!asset) return "Missing asset";
  if (!privacyRevealed) return asset.source_kind === "Generated" ? "Generated video" : "Private asset";
  return assetDisplayLabel(asset, true, asset.source_kind === "Generated" ? "Generated Video" : "Video Asset");
}

function availableCapturesKey(timeline, shot, privacyRevealed, globalSettings = null) {
  const project = timeline?.project ?? {};
  const identity = project.identity ?? {};
  const storage = project.storage ?? {};
  return [
    shot?.shot_id ?? "",
    identity.project_id ?? "",
    globalAssetRootLabel(globalSettings),
    storage.project_directory_name ?? "",
    privacyRevealed ? "reveal" : "private",
  ].join("|");
}

function captureSummaryLabel(item, privacyRevealed) {
  if (!privacyRevealed) return "Captured video";
  const registration = item?.take_capture?.registration ?? {};
  const take = registration.take ?? {};
  const parts = [
    take.take_id || item?.filename || item?.name || "Captured video",
  ];
  const model = [take.model_family, take.model_version].filter(Boolean).join(" ");
  if (model) parts.push(model);
  if (take.seed != null) parts.push(`seed ${take.seed}`);
  return parts.join(" · ");
}

function captureShortSummaryLabel(item, privacyRevealed) {
  const registration = item?.take_capture?.registration ?? {};
  const take = registration.take ?? {};
  const model = [take.model_family, take.model_version].filter(Boolean).join(" ");
  const parts = [];
  if (model) parts.push(privacyRevealed ? model : "Model");
  if (take.seed != null) parts.push(privacyRevealed ? `seed ${take.seed}` : "Seeded");
  return parts.join(" · ") || (privacyRevealed ? (item?.filename || item?.name || "Captured video") : "Captured video");
}

function captureMediaPath(item) {
  return String(
    item?.path
    ?? item?.file_path
    ?? item?.take_capture?.registration?.asset?.path
    ?? item?.take_capture?.registration?.asset?.file_path
    ?? "",
  ).trim();
}

function assetMediaPath(asset) {
  return String(asset?.path ?? asset?.file_path ?? "").trim();
}

export function findAttachedTakeForCapture(timeline, shot, item) {
  if (!shot) return null;
  const capturePath = captureMediaPath(item);
  const takes = shot.takes ?? [];
  if (capturePath) {
    return takes.find((take) => assetMediaPath(assetForId(timeline, take.asset_id)) === capturePath) ?? null;
  }
  const takeId = captureTakeId(item);
  if (!takeId) return null;
  return takes.find((take) => (
    take.take_id === takeId
    && !assetMediaPath(assetForId(timeline, take.asset_id))
  )) ?? null;
}

function captureOrderLabel(index, total) {
  const number = Math.max(1, Number(total) - Number(index || 0));
  return `Capture ${String(number).padStart(3, "0")}`;
}

function takeOrderLabel(index, total) {
  const number = Math.max(1, Math.min(Number(total) || 1, Number(index || 0) + 1));
  return `Take ${String(number).padStart(3, "0")}`;
}

function latestAttachedTake(shot) {
  const takes = Array.isArray(shot?.takes) ? shot.takes : [];
  return takes.length ? takes[takes.length - 1] : null;
}

function captureTakeId(item) {
  const value = item?.take_capture?.registration?.take?.take_id;
  return value == null ? "" : String(value);
}

function projectFolderDisplay(timeline, privacyRevealed, globalSettings = null) {
  if (isGlobalPrivacyMode(globalSettings) && !privacyRevealed) return "Private path";
  const storage = timeline?.project?.storage ?? {};
  const root = globalAssetRootLabel(globalSettings);
  const directory = String(storage.project_directory_name ?? "").trim();
  return directory ? `${root.replace(/[\\/]+$/, "")}/${directory}` : root;
}

function resolvedLoraCount(resolvedLoras) {
  const targets = resolvedLoras?.targets && typeof resolvedLoras.targets === "object" && !Array.isArray(resolvedLoras.targets)
    ? resolvedLoras.targets
    : {};
  return Object.values(targets).reduce((count, stack) => count + (Array.isArray(stack) ? stack.length : 0), 0);
}

function assetForId(timeline, assetId) {
  if (assetId == null) return null;
  return (timeline.assets ?? []).find((asset) => asset.asset_id === assetId) ?? null;
}

function rangeSecondFromClientX(clientX, bar, timeline) {
  const rect = bar.getBoundingClientRect();
  const projectSeconds = getProjectWholeSeconds(timeline);
  const ratio = rect.width <= 0 ? 0 : (Number(clientX) - rect.left) / rect.width;
  return Math.round(Math.max(0, Math.min(1, ratio)) * projectSeconds);
}

function setTimelineRangeBoundary(timeline, mode, seconds) {
  const current = getTimelineViewRange(timeline);
  const next = mode === "range-start"
    ? clampTimelineViewRange(timeline, seconds, current.end)
    : clampTimelineViewRange(timeline, current.start, seconds);
  timeline.ui_state.view_start_seconds = next.start;
  timeline.ui_state.view_end_seconds = next.end;
  return next;
}

function findAudioClip(timeline, itemId) {
  if (!itemId) return null;
  for (const track of timeline.audio_tracks) {
    const clip = track.clips.find((candidate) => candidate.item_id === itemId);
    if (clip) return clip;
  }
  return null;
}

function resolveLiveTimelineItem(timeline, item) {
  if (item?.boundary_id) return findBoundary(timeline, item.boundary_id) ?? item;
  const itemId = item?.item_id;
  if (!itemId) return item;
  return findSection(timeline, itemId) ?? findAudioClip(timeline, itemId) ?? item;
}

function timelineComparisonPayload(timeline) {
  const copy = JSON.parse(JSON.stringify(timeline ?? {}));
  delete copy.validation;
  if (copy.ui_state && typeof copy.ui_state === "object") copy.ui_state.state_revision = 0;
  if (copy.project?.identity && typeof copy.project.identity === "object") {
    copy.project.identity.project_id = "__project_id__";
  }
  if (copy.project?.storage && typeof copy.project.storage === "object") {
    copy.project.storage.project_directory_name = "__project_directory__";
  }
  return JSON.stringify(copy);
}

function getInspectorHeight(timeline) {
  const selected = timeline?.director_track?.sections?.find((section) => section.item_id === timeline?.ui_state?.selected_item_id);
  const selectedAudio = findAudioClip(timeline, timeline?.ui_state?.selected_item_id);
  const selectedShot = findShot(timeline, timeline?.ui_state?.selected_item_id);
  const selectedBoundary = findBoundary(timeline, timeline?.ui_state?.selected_item_id);
  return selected || selectedAudio || selectedShot || selectedBoundary ? INSPECTOR_EDITOR_HEIGHT : INSPECTOR_HEIGHT;
}

function getRenderedInspectorHeight(timeline, widgetHeight) {
  const remainingHeight = Number(widgetHeight)
    - TOOLBAR_HEIGHT
    - RANGE_CONTROL_HEIGHT
    - getTimelineViewportHeight(timeline)
    - ROOT_GAP * 3;
  return Math.max(getInspectorHeight(timeline), remainingHeight);
}

function timelineWidgetAvailableHeight(node, widget) {
  const nodeHeight = positiveNumber(node?.size?.[1]);
  const widgetY = timelineWidgetY(widget);
  if (!nodeHeight || !widgetY || nodeHeight <= widgetY + NODE_BODY_BOTTOM_PADDING) return 0;
  return nodeHeight - widgetY - NODE_BODY_BOTTOM_PADDING;
}

function timelineWidgetY(widget) {
  return Math.max(positiveNumber(widget?.y), positiveNumber(widget?.last_y));
}

function measureIntrinsicTimelineContentHeight(container, viewportWidth, timeline) {
  const root = container?.querySelector?.(".htd-root");
  const documentRef = container?.ownerDocument ?? globalThis.document;
  if (!root?.cloneNode || !documentRef?.createElement) return getTimelineWidgetHeight(timeline);

  const clone = root.cloneNode(true);
  clone.style.position = "absolute";
  clone.style.left = "-100000px";
  clone.style.top = "0";
  clone.style.width = `${Math.max(1, Number(viewportWidth) || TIMELINE_WIDTH)}px`;
  clone.style.height = "auto";
  clone.style.minHeight = "0";
  clone.style.maxHeight = "none";
  clone.style.visibility = "hidden";
  clone.style.pointerEvents = "none";
  clone.style.overflow = "visible";

  for (const inspector of clone.querySelectorAll?.(".htd-inspector") ?? []) {
    inspector.style.height = "auto";
    inspector.style.minHeight = "";
    inspector.style.maxHeight = "none";
  }
  for (const panel of clone.querySelectorAll?.(".htd-inspector-panel") ?? []) {
    panel.style.height = "auto";
    panel.style.minHeight = "";
    panel.style.maxHeight = "none";
  }

  container.append?.(clone);
  const measuredHeight = Math.max(
    positiveNumber(clone.scrollHeight),
    positiveNumber(clone.offsetHeight),
    positiveNumber(clone.getBoundingClientRect?.().height),
  );
  clone.remove?.();
  return Math.max(getTimelineWidgetHeight(timeline), Math.ceil(measuredHeight));
}

function positiveNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function elementLayoutWidth(element) {
  const rectWidth = positiveNumber(element?.getBoundingClientRect?.().width);
  return rectWidth || positiveNumber(element?.clientWidth) || positiveNumber(element?.offsetWidth);
}

function nodeBodyWidth(node) {
  const rawWidth = Array.isArray(node?.size) ? node.size[0] : node?.size?.[0];
  const width = positiveNumber(rawWidth);
  return width > NODE_BODY_HORIZONTAL_PADDING ? width - NODE_BODY_HORIZONTAL_PADDING : width;
}

function shouldRenderPromptInput(timeline, item) {
  return Boolean(
    item &&
    "prompt" in item,
  );
}

function shouldShowWaveform(timeline, privacyRevealActive = false, globalSettings = null) {
  const settings = normalizeGlobalSettings(globalSettings);
  return Boolean(
    settings.display.show_audio_waveforms &&
    (!isGlobalPrivacyMode(settings) || privacyRevealActive),
  );
}

function renderWaveform(node, timeline, clip, itemWidth) {
  const waveform = el("div", "htd-waveform");
  waveform.setAttribute("aria-label", "Audio waveform");
  waveform.setAttribute("role", "img");
  const asset = resolveMediaReference(timeline, clip.audio);
  if (!asset?.asset_id) {
    waveform.classList.add("is-empty");
    return waveform;
  }

  const peakCount = waveformPeakCountForWidth(itemWidth);
  let payload = node?._timelineMediaCache?.requestWaveform?.(asset, peakCount) ?? node?._timelineMediaCache?.getWaveform?.(asset.asset_id, peakCount);
  const detailPeakCount = waveformPeakRequestForClip(itemWidth, clip, payload?.duration_seconds);
  if (detailPeakCount !== peakCount) {
    payload = node?._timelineMediaCache?.requestWaveform?.(asset, detailPeakCount) ?? payload;
  }

  const bars = waveformPeaksForClip(payload, clip, peakCount);
  if (!bars.length) {
    waveform.classList.add("is-loading");
    return waveform;
  }
  for (const value of bars) {
    const bar = el("span", "htd-waveform-bar");
    bar.style.height = `${Math.round(value * 100)}%`;
    waveform.append(bar);
  }
  return waveform;
}

export function waveformPeakCountForWidth(width) {
  const numericWidth = Number(width);
  if (!Number.isFinite(numericWidth)) return MIN_WAVEFORM_PEAKS;
  return Math.max(MIN_WAVEFORM_PEAKS, Math.min(MAX_WAVEFORM_PEAKS, Math.ceil(numericWidth / 2)));
}

export function waveformPeakRequestForClip(width, clip, durationSeconds) {
  const visiblePeakCount = waveformPeakCountForWidth(width);
  const sourceRange = waveformSourceRange(clip, durationSeconds);
  if (!sourceRange) return visiblePeakCount;
  const ratio = Math.max(0.01, (sourceRange.end - sourceRange.start) / sourceRange.duration);
  return Math.max(visiblePeakCount, Math.min(MAX_WAVEFORM_PEAKS, Math.ceil(visiblePeakCount / ratio)));
}

export function waveformPeaksForClip(payload, clip, targetCount = null) {
  const peaks = Array.isArray(payload?.peaks) ? payload.peaks : [];
  if (!peaks.length) return [];
  const sourceRange = waveformSourceRange(clip, payload?.duration_seconds);
  if (!sourceRange) return applyWaveformVolume(resamplePeaks(peaks, targetCount), clip);
  const startIndex = Math.max(0, Math.min(peaks.length - 1, Math.floor((sourceRange.start / sourceRange.duration) * peaks.length)));
  const endIndex = Math.max(startIndex + 1, Math.min(peaks.length, Math.ceil((sourceRange.end / sourceRange.duration) * peaks.length)));
  return applyWaveformVolume(resamplePeaks(peaks.slice(startIndex, endIndex), targetCount), clip);
}

function waveformSourceRange(clip, durationSeconds) {
  const duration = Number(durationSeconds);
  if (!Number.isFinite(duration) || duration <= 0) return null;
  const sourceIn = clampNumber(clip?.source_in ?? 0, 0, duration, 0);
  const hasExplicitSourceOut = clip?.source_out != null && clip?.source_out !== "";
  const explicitSourceOut = Number(clip?.source_out);
  const clipDuration = Math.max(0, Number(clip?.end_time ?? 0) - Number(clip?.start_time ?? 0));
  const inferredSourceOut = clipDuration > 0 ? sourceIn + clipDuration : duration;
  const sourceOut = hasExplicitSourceOut && Number.isFinite(explicitSourceOut) ? explicitSourceOut : inferredSourceOut;
  const end = clampNumber(sourceOut, sourceIn, duration, duration);
  return { start: sourceIn, end, duration };
}

function resamplePeaks(peaks, targetCount) {
  const count = Number(targetCount);
  if (!Number.isFinite(count) || count <= 0 || peaks.length === count) return peaks;
  const target = Math.max(1, Math.round(count));
  if (peaks.length === 1) return Array.from({ length: target }, () => peaks[0]);
  const values = [];
  for (let index = 0; index < target; index += 1) {
    const start = Math.floor((index / target) * peaks.length);
    const end = Math.max(start + 1, Math.ceil(((index + 1) / target) * peaks.length));
    values.push(Math.max(...peaks.slice(start, end)));
  }
  return values;
}

function applyWaveformVolume(peaks, clip) {
  const rawVolume = Number(clip?.volume ?? 100);
  const multiplier = Number.isFinite(rawVolume) ? Math.max(0, rawVolume) / 100 : 1;
  if (multiplier === 1) return peaks;
  return peaks.map((value) => Math.max(0, Math.min(1, Number(value) * multiplier)));
}

function sectionThumbnailUrl(node, timeline, section, privacyRevealActive = false, globalSettings = null) {
  const settings = normalizeGlobalSettings(globalSettings);
  if (
    (isGlobalPrivacyMode(settings) && !privacyRevealActive) ||
    settings.display.show_thumbnails === false
  ) {
    return null;
  }
  const reference = section.type === "Image" ? section.image : section.type === "Video" ? section.video : null;
  const asset = resolveMediaReference(timeline, reference);
  if (!asset?.asset_id) return null;
  return node?._timelineMediaCache?.getThumbnailUrl(asset.asset_id) ?? null;
}

function renderSectionPreview(timeline, thumbnail, itemWidth) {
  const preview = el("div", "htd-section-preview");
  preview.setAttribute("aria-hidden", "true");
  const previewHeight = Math.max(1, DIRECTOR_TRACK_HEIGHT - 10);
  const baseTileWidth = Math.max(36, Math.round(previewHeight * projectPreviewAspect(timeline)));
  const tileWidth = Math.max(12, Math.min(baseTileWidth, itemWidth));
  const repeatCount = Math.min(96, Math.max(1, Math.ceil(itemWidth / tileWidth)));
  for (let index = 0; index < repeatCount; index += 1) {
    const frame = el("div", "htd-section-preview-frame");
    frame.style.width = `${tileWidth}px`;
    const image = preview.ownerDocument.createElement("img");
    image.src = thumbnail;
    image.alt = "";
    image.draggable = false;
    frame.append(image);
    preview.append(frame);
  }
  return preview;
}

function projectPreviewAspect(timeline) {
  const aspectText = String(timeline?.project?.aspect_ratio ?? "16:9");
  const match = aspectText.match(/^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$/);
  const width = Math.max(0.01, Number(match?.[1] ?? 16));
  const height = Math.max(0.01, Number(match?.[2] ?? 9));
  const landscapeAspect = width / height;
  return timeline?.project?.orientation === "Portrait" ? 1 / landscapeAspect : landscapeAspect;
}

function sectionLabel(timeline, section, globalSettings = null) {
  const settings = normalizeGlobalSettings(globalSettings);
  if (!settings.display.show_section_labels) return "";
  if (timeline.ui_state.timeline_display_mode === "Media") {
    const reference = section.type === "Image" ? section.image : section.type === "Video" ? section.video : null;
    return mediaLabel(timeline, reference, section.type);
  }
  if (timeline.ui_state.timeline_display_mode === "Prompts" && "prompt" in section) {
    return effectivePromptLabel(timeline, section, settings) || section.type;
  }
  if (section.type === "Text") return section.prompt || "Text";
  return section.type;
}

function effectivePromptLabel(timeline, section, globalSettings = null) {
  const prompt = String(section.prompt ?? "").trim();
  const globalPrompt = timeline.project.global_prompt ?? {};
  const settings = normalizeGlobalSettings(globalSettings);
  const globalText = String(globalPrompt.prompt ?? "").trim();
  if (!globalPrompt.enabled || !settings.global_prompt.show_effective_prompt || !globalText) return prompt;
  if (!prompt) return globalText;
  return globalPrompt.position === "Suffix" ? `${prompt}, ${globalText}` : `${globalText}, ${prompt}`;
}

function inspectorTitle(text) {
  const title = el("div", "htd-inspector-title");
  title.textContent = text;
  return title;
}

function clampNumber(value, min, max, fallback) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(min, Math.min(max, numeric));
}

function findLiveReference(timeline, reference) {
  return getCharacterReferences(timeline).find((candidate) => candidate.id === reference.id) ?? null;
}

function setLiveReferenceField(timeline, reference, field, value) {
  const references = ensureCharacterReferences(timeline);
  const liveReference = references.find((candidate) => candidate.id === reference.id) ?? reference;
  liveReference[field] = value;
  return liveReference;
}

function stampReferenceLibraryItemId(timeline, reference, itemId) {
  const liveReference = findLiveReference(timeline, reference);
  if (!liveReference?.image || !itemId) return null;
  liveReference.image.metadata = liveReference.image.metadata && typeof liveReference.image.metadata === "object" && !Array.isArray(liveReference.image.metadata)
    ? liveReference.image.metadata
    : {};
  liveReference.image.metadata.library_item_id = itemId;
  return liveReference;
}

function referenceLibraryItemId(reference) {
  return String(reference?.image?.metadata?.library_item_id ?? "").trim();
}

function projectLibraryItemIdFor(timeline) {
  return String(timeline?.project?.metadata?.library_item_id ?? "").trim();
}

function stampProjectLibraryItemId(timeline, itemId) {
  if (!timeline || typeof timeline !== "object") return timeline;
  timeline.project ??= {};
  timeline.project.metadata = timeline.project.metadata && typeof timeline.project.metadata === "object" && !Array.isArray(timeline.project.metadata)
    ? timeline.project.metadata
    : {};
  const normalizedItemId = String(itemId ?? "").trim();
  if (normalizedItemId) {
    timeline.project.metadata.library_item_id = normalizedItemId;
  } else {
    delete timeline.project.metadata.library_item_id;
  }
  return timeline;
}

function referenceLibraryPayload(reference, privacyMode) {
  return {
    name: reference?.description || formatCharacterReferenceTag(reference),
    description: reference?.description || "",
    private: Boolean(privacyMode),
    character: cloneReferenceForLibrary(reference),
  };
}

function projectLibraryPayload(timeline, itemId, name = null, privacyMode = false) {
  const payloadTimeline = cloneProjectForDirectorLibrary(timeline, itemId, name);
  return {
    name: projectName(payloadTimeline),
    private: Boolean(privacyMode),
    project: payloadTimeline,
  };
}

function stampProjectName(timeline, name) {
  if (!timeline || typeof timeline !== "object") return timeline;
  timeline.project ??= {};
  timeline.project.identity = timeline.project.identity && typeof timeline.project.identity === "object" && !Array.isArray(timeline.project.identity)
    ? timeline.project.identity
    : {};
  timeline.project.identity.name = normalizedProjectName(name);
  return timeline;
}

function projectName(timeline) {
  return normalizedProjectName(timeline?.project?.identity?.name ?? timeline?.project?.metadata?.title ?? timeline?.project?.metadata?.name);
}

function normalizedProjectName(name) {
  return String(name ?? "").trim() || "Untitled Project";
}

function promptForProjectName(documentRef, timeline) {
  const promptFn = documentRef?.defaultView?.prompt ?? globalThis.prompt;
  if (typeof promptFn !== "function") return projectName(timeline);
  const value = promptFn("Project name", projectName(timeline));
  if (value == null) return "";
  return normalizedProjectName(value);
}

function cloneReferenceForLibrary(reference) {
  return JSON.parse(JSON.stringify(reference ?? {}));
}

async function withDisabledControl(control, action) {
  if (!control) return action();
  const wasDisabled = control.disabled;
  control.disabled = true;
  try {
    return await action();
  } finally {
    control.disabled = wasDisabled;
  }
}

function deleteLabelForItemType(itemType) {
  return DELETE_MENU_LABELS[itemType] ?? "Delete Item";
}

function duplicateLabelForItemType(itemType) {
  return DUPLICATE_MENU_LABELS[itemType] ?? "Duplicate Item";
}

function replaceLabelForItemType(itemType) {
  return REPLACE_MENU_LABELS[itemType] ?? null;
}

function iconButton(iconName, title, onClick, options = {}) {
  const control = button("", title, onClick);
  control.classList.add("htd-icon-button");
  control.disabled = Boolean(options.disabled);
  control.append(createIconElement(iconName));
  return control;
}

function toggleIconButton(iconName, title, active, onClick) {
  const control = iconButton(iconName, title, onClick);
  control.classList.toggle("is-active", Boolean(active));
  control.setAttribute("aria-pressed", active ? "true" : "false");
  return control;
}

function iconMenuControl({ id, title, iconName, value, options, placement = "below", showValue = false, open, onToggle, onChange }) {
  const wrapper = el("div", "htd-menu");
  wrapper.classList.toggle("opens-above", placement === "above" || placement === "above-end");
  wrapper.classList.toggle("align-end", placement === "above-end");
  if (showValue) {
    const valueLabel = el("span", "htd-menu-value");
    valueLabel.textContent = value;
    valueLabel.title = `${title}: ${value}`;
    wrapper.append(valueLabel);
  }
  const menuButton = iconButton(iconName, `${title}: ${value}`, onToggle);
  menuButton.classList.add("htd-menu-button");
  menuButton.setAttribute("aria-haspopup", "menu");
  menuButton.setAttribute("aria-expanded", open ? "true" : "false");
  wrapper.append(menuButton);
  if (open) {
    const menu = el("div", "htd-menu-list");
    menu.setAttribute("role", "menu");
    menu.id = `htd-menu-${id}`;
    for (const optionValue of options) {
      const item = el("button", "htd-menu-item");
      item.type = "button";
      item.textContent = optionValue;
      item.title = optionValue;
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", optionValue === value ? "true" : "false");
      item.classList.toggle("is-active", optionValue === value);
      item.addEventListener("click", () => onChange(optionValue));
      menu.append(item);
    }
    wrapper.append(menu);
  }
  return wrapper;
}

function button(text, title, onClick) {
  const control = el("button", "htd-button");
  control.type = "button";
  control.textContent = text;
  control.title = title;
  control.setAttribute("aria-label", title);
  control.addEventListener("click", onClick);
  return control;
}

function toggleButton(text, title, active, onClick) {
  const control = button(text, title, onClick);
  control.classList.toggle("is-active", Boolean(active));
  control.setAttribute("aria-pressed", active ? "true" : "false");
  return control;
}

function selectControl(title, value, options, onChange) {
  const select = el("select", "htd-select");
  select.title = title;
  for (const optionValue of options) {
    const option = el("option");
    option.value = optionValue;
    option.textContent = optionValue;
    select.append(option);
  }
  select.value = value ?? options[0] ?? "";
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function createIconElement(name) {
  const icon = el("span", "htd-icon");
  icon.setAttribute("aria-hidden", "true");
  icon.innerHTML = ICONS[name] ?? ICONS.settings;
  return icon;
}

const ICONS = {
  text: `<svg viewBox="0 0 24 24"><path d="M5 6h14M12 6v12M8 18h8"/></svg>`,
  image: `<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="m7 16 4-4 3 3 2-2 3 3"/><circle cx="15.5" cy="9.5" r="1.5"/></svg>`,
  video: `<svg viewBox="0 0 24 24"><rect x="4" y="6" width="12" height="12" rx="2"/><path d="m16 10 4-2v8l-4-2z"/></svg>`,
  "preview-video": `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="m10 8 6 4-6 4z"/></svg>`,
  refresh: `<svg viewBox="0 0 24 24"><path d="M20 6v5h-5"/><path d="M4 18v-5h5"/><path d="M18 9a7 7 0 0 0-11.6-2.6L4 9"/><path d="M6 15a7 7 0 0 0 11.6 2.6L20 15"/></svg>`,
  accept: `<svg viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg>`,
  reject: `<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>`,
  restore: `<svg viewBox="0 0 24 24"><path d="M5 8v6h6"/><path d="M6 14a7 7 0 1 0 2-7"/></svg>`,
  audio: `<svg viewBox="0 0 24 24"><path d="M6 15V9M10 18V6M14 16V8M18 14v-4"/></svg>`,
  shot: `<svg viewBox="0 0 24 24"><rect x="4" y="7" width="16" height="10" rx="2"/><path d="M8 7V5M16 7V5M8 19v-2M16 19v-2"/><path d="M9 12h6"/></svg>`,
  boundary: `<svg viewBox="0 0 24 24"><path d="M12 4v16"/><path d="M6 8h4M14 8h4M6 16h4M14 16h4"/></svg>`,
  take: `<svg viewBox="0 0 24 24"><path d="M5 7h14v10H5z"/><path d="m9 10 4 2-4 2z"/><path d="M7 5h10"/></svg>`,
  lora: `<svg viewBox="0 0 24 24"><path d="M6 17V7l6-3 6 3v10l-6 3z"/><path d="M9 9h6M9 13h6M12 9v8"/></svg>`,
  layers: `<svg viewBox="0 0 24 24"><path d="m12 4 8 4-8 4-8-4z"/><path d="m4 12 8 4 8-4"/><path d="m4 16 8 4 8-4"/></svg>`,
  trim: `<svg viewBox="0 0 24 24"><path d="M6 5v14M18 5v14M6 12h12"/><path d="m9 9-3 3 3 3M15 9l3 3-3 3"/></svg>`,
  magnet: `<svg viewBox="0 0 24 24"><path d="M7 5v7a5 5 0 0 0 10 0V5"/><path d="M7 9h4M13 9h4"/></svg>`,
  global: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16"/></svg>`,
  "native-audio": `<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 0 1 8-8"/><path d="M20 12a8 8 0 0 1-8 8"/><path d="M7 12v-2a5 5 0 0 1 10 0v2"/><rect x="6" y="12" width="3" height="6" rx="1"/><rect x="15" y="12" width="3" height="6" rx="1"/><path d="M12 18v2"/></svg>`,
  split: `<svg viewBox="0 0 24 24"><path d="M12 4v16"/><path d="M5 7h4M5 17h4M15 7h4M15 17h4"/></svg>`,
  duplicate: `<svg viewBox="0 0 24 24"><rect x="8" y="8" width="10" height="10" rx="2"/><path d="M6 14H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v1"/></svg>`,
  delete: `<svg viewBox="0 0 24 24"><path d="M6 7h12M10 7V5h4v2M9 10v7M15 10v7M8 7l1 12h6l1-12"/></svg>`,
  "fit-last-section": `<svg viewBox="0 0 24 24"><path d="M5 6h14M5 18h14"/><path d="M15 9l4 3-4 3"/><path d="M5 12h13"/><path d="M9 9v6"/></svg>`,
  "fit-all-sections": `<svg viewBox="0 0 24 24"><path d="M5 6h14M5 18h14"/><path d="M8 10h8v4H8z"/><path d="M5 12h3M16 12h3"/></svg>`,
  fit: `<svg viewBox="0 0 24 24"><path d="M5 9V5h4M15 5h4v4M19 15v4h-4M9 19H5v-4"/><path d="M8 8h8v8H8z"/></svg>`,
  settings: `<svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 0 1 4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.3 7A2 2 0 0 1 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 0 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.1a2 2 0 0 1 0 4H21a1.7 1.7 0 0 0-1.6 1z"/></svg>`,
  "project-settings": `<svg viewBox="0 0 24 24"><path d="M4 7h6l2 2h8v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M4 7V5a2 2 0 0 1 2-2h4l2 2h4a2 2 0 0 1 2 2v2"/><path d="M9 14h6M12 11v6"/></svg>`,
  director: `<svg viewBox="0 0 24 24"><path d="M4 7h16M4 17h16M8 4v6M16 14v6"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></svg>`,
  crop: `<svg viewBox="0 0 24 24"><path d="M6 3v12a3 3 0 0 0 3 3h12"/><path d="M3 6h12a3 3 0 0 1 3 3v12"/><path d="M9 9h6v6H9z"/></svg>`,
  timing: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/></svg>`,
  "guide-range": `<svg viewBox="0 0 24 24"><path d="M5 7h14M5 17h14"/><path d="M8 4v6M16 14v6"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></svg>`,
  "guide-frames": `<svg viewBox="0 0 24 24"><rect x="4" y="6" width="12" height="12" rx="2"/><path d="M8 10h4M8 14h4"/><path d="M17 8l3-2v12l-3-2"/></svg>`,
  references: `<svg viewBox="0 0 24 24"><path d="M7 19a5 5 0 0 1 10 0"/><circle cx="12" cy="9" r="3"/><path d="M4 5h4M16 5h4M4 5v4M20 5v4"/></svg>`,
  "reference-active": `<svg viewBox="0 0 24 24"><path d="M7 19a5 5 0 0 1 10 0"/><circle cx="12" cy="9" r="3"/><path d="m17 4 2 2 3-4"/></svg>`,
  "image-plus": `<svg viewBox="0 0 24 24"><rect x="4" y="5" width="14" height="14" rx="2"/><path d="m7 16 3-3 2 2 2-2 3 3"/><path d="M19 8v6M16 11h6"/></svg>`,
  library: `<svg viewBox="0 0 24 24"><path d="M5 5h6v14H5z"/><path d="M13 5h6v14h-6z"/><path d="M7 8h2M15 8h2M7 12h2M15 12h2"/></svg>`,
  "library-add": `<svg viewBox="0 0 24 24"><path d="M5 5h6v14H5z"/><path d="M13 5h6v14h-6z"/><path d="M17 8v6M14 11h6"/></svg>`,
  "library-update": `<svg viewBox="0 0 24 24"><path d="M5 5h6v14H5z"/><path d="M13 5h6v14h-6z"/><path d="m14.5 12 2 2 4-5"/></svg>`,
  "timeline-clear": `<svg viewBox="0 0 24 24"><path d="M5 5h14v14H5z"/><path d="M9 9h6M9 13h4"/><path d="m16 16 4 4M20 16l-4 4"/></svg>`,
  copy: `<svg viewBox="0 0 24 24"><rect x="8" y="8" width="10" height="10" rx="2"/><path d="M6 14H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v1"/></svg>`,
  insert: `<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/><path d="M4 5h5M4 19h5M15 5h5M15 19h5"/></svg>`,
  sparkle: `<svg viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/><path d="M5 3l.7 1.8 1.8.7-1.8.7L5 8l-.7-1.8-1.8-.7 1.8-.7z"/></svg>`,
};

function settingRow(title) {
  const row = el("label", "htd-setting-row");
  const labelText = el("span", "htd-setting-label");
  labelText.textContent = title;
  row.append(labelText);
  return row;
}

function toolbarSpacer() {
  const spacer = el("div", "htd-toolbar-spacer");
  spacer.setAttribute("aria-hidden", "true");
  return spacer;
}

function getPath(root, path) {
  let current = root;
  for (const key of path) current = current?.[key];
  return current;
}

function setPath(root, path, value) {
  let current = root;
  for (const key of path.slice(0, -1)) {
    current[key] ??= {};
    current = current[key];
  }
  current[path.at(-1)] = value;
}

function replaceObject(target, source) {
  for (const key of Object.keys(target)) delete target[key];
  Object.assign(target, deepClone(source));
  return target;
}

function trackLabel(iconName, title) {
  const item = el("div", "htd-track-label");
  item.title = title;
  item.setAttribute("aria-label", title);
  item.append(createIconElement(iconName));
  return item;
}

function el(tag, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  return element;
}

function installStyles(documentRef) {
  if (!documentRef || documentRef.getElementById("helto-timeline-director-style")) return;
  const style = documentRef.createElement("style");
  style.id = "helto-timeline-director-style";
  style.textContent = `
    ${htdTokenBlock(".helto-timeline-director, .htd-context-menu")}
    .helto-timeline-director { width: 100%; box-sizing: border-box; overflow: hidden; color: var(--htd-text); font: var(--htd-font-size) / var(--htd-line) var(--htd-font-sans); -webkit-font-smoothing: antialiased; }
    .htd-root { position: relative; width: 100%; height: 100%; box-sizing: border-box; display: flex; flex-direction: column; gap: 7px; }
    .htd-root.is-private:not(.is-privacy-revealed) .htd-range-control,
    .htd-root.is-private:not(.is-privacy-revealed) .htd-viewport,
    .htd-root.is-private:not(.is-privacy-revealed) .htd-inspector { visibility: hidden; }
    .htd-root.is-private:not(.is-privacy-revealed) .htd-section-label,
    .htd-root.is-private:not(.is-privacy-revealed) .htd-shot-band,
    .htd-root.is-private:not(.is-privacy-revealed) .htd-audio-label,
    .htd-root.is-private:not(.is-privacy-revealed) .htd-prompt,
    .htd-root.is-private:not(.is-privacy-revealed) .htd-media-value,
    .htd-root.is-private:not(.is-privacy-revealed) .htd-lora-count { color: transparent !important; text-shadow: none !important; }
    .htd-root.is-private:not(.is-privacy-revealed) .htd-shot-take-badge { visibility: hidden; }
    .htd-root.is-private:not(.is-privacy-revealed) .htd-prompt::placeholder { color: transparent !important; }
    .htd-privacy-status { position: absolute; left: 8px; right: 8px; bottom: 8px; z-index: 40; padding: 7px 10px; border: 1px solid var(--htd-privacy-status-border); border-radius: var(--htd-radius); background: var(--htd-privacy-status-bg); color: var(--htd-privacy-status-text); box-shadow: var(--htd-shadow-pop); }
    .htd-toolbar { position: relative; z-index: 15; box-sizing: border-box; display: flex; gap: 4px; align-items: center; min-height: 34px; padding: 5px; overflow: visible; border-radius: var(--htd-radius); background: linear-gradient(180deg, var(--htd-surface-2), var(--htd-surface)); box-shadow: inset 0 0 0 1px var(--htd-border); }
    .htd-button { min-width: 28px; height: 24px; padding: 0 8px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: linear-gradient(180deg, var(--htd-surface-3), var(--htd-surface-2)); color: var(--htd-text); cursor: pointer; white-space: nowrap; font: inherit; transition: background var(--htd-transition), border-color var(--htd-transition), color var(--htd-transition), box-shadow var(--htd-transition), transform .03s ease; }
    .htd-button:hover:not(:disabled) { background: linear-gradient(180deg, var(--htd-surface-hover), var(--htd-surface-3)); border-color: var(--htd-border-hover); color: var(--htd-text); }
    .htd-button:active:not(:disabled) { transform: translateY(1px); }
    .htd-button:focus-visible { outline: none; border-color: var(--htd-focus); box-shadow: var(--htd-ring); }
    .htd-icon-button { width: 28px; min-width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center; }
    .htd-icon { width: 16px; height: 16px; display: inline-flex; align-items: center; justify-content: center; }
    .htd-icon svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .htd-button.is-active { border-color: var(--htd-accent-border); background: linear-gradient(180deg, #4f3a2a, #3d2d20); color: var(--htd-accent-strong); box-shadow: inset 0 0 0 1px var(--htd-accent-hairline); }
    .htd-button.is-active:hover:not(:disabled) { background: linear-gradient(180deg, #5d4531, #493626); color: var(--htd-accent-strong); }
    .htd-button.is-primary { border-color: var(--htd-accent-border); background: linear-gradient(180deg, #4f3a2a, #3d2d20); color: var(--htd-accent-strong); }
    .htd-button.is-primary:hover:not(:disabled) { background: linear-gradient(180deg, #5d4531, #493626); color: var(--htd-accent-strong); }
    .htd-button:disabled { opacity: 0.4; cursor: not-allowed; }
    .htd-button.is-danger { border-color: var(--htd-danger-border); background: linear-gradient(180deg, #5c2c3d, #482331); color: var(--htd-danger-text); }
    .htd-button.is-danger:hover:not(:disabled) { border-color: var(--htd-danger); background: linear-gradient(180deg, #6e3549, #5a2a3c); color: var(--htd-danger-text-strong); }
    .htd-toolbar-spacer { width: 1px; height: 18px; margin: 0 5px; background: var(--htd-border-strong); opacity: 0.7; flex: 0 0 auto; }
    .htd-prompt-optimizer-button { margin-left: auto; }
    .htd-menu { position: relative; display: inline-flex; align-items: center; }
    .htd-menu-button { width: 34px; min-width: 34px; }
    .htd-menu-button::after { content: ""; width: 0; height: 0; margin-left: 2px; border-left: 3px solid transparent; border-right: 3px solid transparent; border-top: 4px solid currentColor; opacity: 0.78; }
    .htd-menu-value { min-width: 0; max-width: 118px; margin-right: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; align-self: center; color: var(--htd-text); }
    .htd-menu-list { position: absolute; top: 28px; left: 0; z-index: 30; min-width: 132px; padding: 5px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius); background: var(--htd-surface); box-shadow: var(--htd-shadow-pop); }
    .htd-menu.opens-above .htd-menu-list { top: auto; bottom: 28px; }
    .htd-menu.align-end .htd-menu-list { right: 0; left: auto; }
    .htd-menu-item { width: 100%; height: 24px; padding: 0 8px; border: 0; border-radius: var(--htd-radius-sm); background: transparent; color: var(--htd-text); text-align: left; cursor: pointer; white-space: nowrap; font: inherit; }
    .htd-menu-item:hover, .htd-menu-item.is-active { background: var(--htd-surface-hover); color: var(--htd-text); }
    .htd-menu-item.is-active { box-shadow: inset 2px 0 0 var(--htd-accent); }
    .htd-context-menu { position: fixed; z-index: 35; box-sizing: border-box; width: 150px; max-width: calc(100vw - 8px); padding: 5px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius); background: var(--htd-surface); color: var(--htd-text); box-shadow: var(--htd-shadow-pop); animation: helto-pop 0.15s var(--htd-ease-spring); }
    .htd-context-menu-item { width: 100%; height: 24px; padding: 0 8px; overflow: hidden; border: 0; border-radius: var(--htd-radius-sm); background: transparent; color: var(--htd-text); text-align: left; text-overflow: ellipsis; cursor: pointer; white-space: nowrap; font: inherit; }
    .htd-context-menu-item:hover { background: var(--htd-surface-hover); color: var(--htd-text); }
    @keyframes helto-pop { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
    .htd-select { min-width: 72px; max-width: 130px; height: 24px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text); }
    .htd-range-control { width: 100%; height: ${RANGE_CONTROL_HEIGHT}px; display: flex; align-items: center; gap: 0; box-sizing: border-box; }
    .htd-range-gutter { width: ${TIMELINE_RIGHT_PADDING}px; flex: 0 0 ${TIMELINE_RIGHT_PADDING}px; }
    .htd-range-bar { position: relative; height: 8px; flex: 1 1 auto; margin-right: ${TIMELINE_RIGHT_PADDING}px; border-radius: 999px; background: var(--htd-bg); border: 1px solid var(--htd-border-strong); cursor: pointer; box-sizing: border-box; }
    .htd-range-active { position: absolute; top: -1px; bottom: -1px; min-width: 8px; border-radius: 999px; background: var(--htd-accent); border: 1px solid var(--htd-accent-border); box-sizing: border-box; }
    .htd-range-handle { position: absolute; top: 50%; width: 12px; height: 18px; border: 1px solid var(--htd-text); border-radius: 4px; background: var(--htd-surface-3); transform: translate(-50%, -50%); cursor: ew-resize; box-shadow: var(--htd-shadow); transition: border-color .12s ease, box-shadow .12s ease; }
    .htd-range-handle:hover { border-color: var(--htd-accent-strong); box-shadow: 0 0 0 3px var(--htd-accent-bg); }
    .htd-range-start { left: 0; }
    .htd-range-end { left: 100%; }
    .htd-viewport { width: 100%; overflow: hidden; box-sizing: border-box; border: 1px solid var(--htd-border); border-radius: var(--htd-radius); background: var(--htd-bg); box-shadow: inset 0 1px 0 var(--htd-inset-highlight); }
    .htd-stage { position: relative; min-height: 100%; }
    .htd-ruler { position: relative; border-bottom: 1px solid var(--htd-border); background: linear-gradient(180deg, var(--htd-inset-highlight), transparent); }
    .htd-tick { position: absolute; z-index: 2; top: 3px; height: 20px; border-left: 1px solid var(--htd-border-strong); padding-left: 4px; color: var(--htd-text-dim); font-variant-numeric: tabular-nums; }
    .htd-tick.htd-end-tick { border-left-color: var(--htd-accent-border); color: var(--htd-accent-strong); font-weight: 700; }
    .htd-project-end { position: absolute; z-index: 1; top: 0; bottom: 0; border-left: 1px solid var(--htd-accent-border); background: color-mix(in srgb, var(--htd-accent) 12%, transparent); pointer-events: none; }
    .htd-playhead { position: absolute; z-index: 3; top: 0; bottom: 0; width: 2px; background: var(--htd-accent); box-shadow: 0 0 6px color-mix(in srgb, var(--htd-accent) 50%, transparent); pointer-events: none; }
    .htd-track { position: relative; border-bottom: 1px solid var(--htd-border); }
    .htd-track-label { position: sticky; left: 0; z-index: 5; width: ${TIMELINE_RIGHT_PADDING}px; height: 100%; display: flex; align-items: center; justify-content: center; background: color-mix(in srgb, var(--htd-bg) 92%, transparent); color: var(--htd-text-dim); }
    .htd-item, .htd-gap { position: absolute; top: 5px; height: calc(100% - 10px); border-radius: var(--htd-radius-sm); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; box-sizing: border-box; }
    .htd-item { touch-action: none; user-select: none; box-shadow: var(--htd-shadow); transition: box-shadow .12s ease, filter .12s ease; }
    .htd-item:hover { filter: brightness(1.08); }
    .htd-gap { border: 1px dashed var(--htd-border-strong); background: color-mix(in srgb, var(--htd-surface-hover) 16%, transparent); }
    .htd-section { padding: 0; border: 1px solid color-mix(in srgb, var(--htd-text) 22%, transparent); cursor: grab; }
    .htd-director-track .htd-section, .htd-director-track .htd-gap { top: 31px; height: calc(100% - 40px); }
    .htd-section-label { position: absolute; z-index: 3; top: 8px; left: 10px; right: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; text-shadow: 0 1px 2px color-mix(in srgb, var(--htd-bg) 82%, transparent); pointer-events: none; }
    .htd-shot-band { position: absolute; top: 5px; height: 20px; padding: 0 8px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text); cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; box-sizing: border-box; font: inherit; text-align: left; transition: border-color var(--htd-transition), color var(--htd-transition); }
    .htd-shot-band .htd-shot-take-badge { position: absolute; top: 2px; right: 3px; height: 14px; padding: 0 4px; border-radius: 7px; font-size: 10px; line-height: 14px; background: color-mix(in srgb, var(--htd-border-hover) 45%, transparent); color: var(--htd-text); pointer-events: none; }
    .htd-shot-band .htd-shot-take-badge.is-accepted { background: color-mix(in srgb, var(--htd-accent) 28%, transparent); color: var(--htd-accent-strong); }
    .htd-shot-band.is-bare-shot { cursor: grab; }
    .htd-shot-band:hover { border-color: var(--htd-border-hover); }
    .htd-shot-band.is-selected { border-color: var(--htd-accent); color: var(--htd-accent-strong); }
    .htd-shot-band.is-primary-selected { box-shadow: inset 0 0 0 1px var(--htd-text); }
    .htd-boundary-control { position: absolute; z-index: 12; top: 27px; transform: translateX(-50%); }
    .htd-boundary-control .htd-menu-button { width: 24px; min-width: 24px; height: 20px; border-color: var(--htd-border-strong); background: var(--htd-surface-2); }
    .htd-boundary-control .htd-menu-list { top: 22px; }
    .htd-section-preview { position: absolute; inset: 0; z-index: 1; display: flex; align-items: stretch; gap: 2px; overflow: hidden; background: color-mix(in srgb, var(--htd-bg) 34%, transparent); pointer-events: none; }
    .htd-section-preview-frame { flex: 0 0 auto; height: 100%; display: flex; align-items: center; justify-content: center; background: color-mix(in srgb, var(--htd-media-bg) 28%, transparent); }
    .htd-section-preview img { width: 100%; height: 100%; object-fit: contain; display: block; }
    .htd-text { background: linear-gradient(180deg, color-mix(in srgb, var(--htd-media-text) 56%, var(--htd-surface-3)), color-mix(in srgb, var(--htd-media-text) 44%, var(--htd-surface-2))); }
    .htd-image { background: linear-gradient(180deg, color-mix(in srgb, var(--htd-media-image) 56%, var(--htd-surface-3)), color-mix(in srgb, var(--htd-media-image) 44%, var(--htd-surface-2))); }
    .htd-video { background: linear-gradient(180deg, color-mix(in srgb, var(--htd-media-video) 56%, var(--htd-surface-3)), color-mix(in srgb, var(--htd-media-video) 44%, var(--htd-surface-2))); }
    .htd-audio-track { min-height: ${AUDIO_LANE_HEIGHT}px; }
    .htd-audio-clip { position: absolute; padding: 0; background: linear-gradient(180deg, color-mix(in srgb, var(--htd-media-audio) 54%, var(--htd-surface-3)), color-mix(in srgb, var(--htd-media-audio) 42%, var(--htd-surface-2))); border: 1px solid color-mix(in srgb, var(--htd-text) 22%, transparent); cursor: grab; }
    .htd-audio-label { position: absolute; z-index: 3; top: 2px; left: 6px; right: 10px; font-size: 6px; line-height: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text); text-shadow: 0 1px 2px color-mix(in srgb, var(--htd-bg) 82%, transparent); pointer-events: none; }
    .htd-waveform { position: absolute; z-index: 1; inset: 4px 9px; min-width: 0; display: flex; align-items: center; gap: 1px; opacity: 0.92; }
    .htd-waveform::after { content: ""; position: absolute; left: 0; right: 0; top: 50%; border-top: 1px solid color-mix(in srgb, var(--htd-text) 22%, transparent); pointer-events: none; }
    .htd-waveform.is-loading, .htd-waveform.is-empty { border-radius: 2px; background: repeating-linear-gradient(90deg, color-mix(in srgb, var(--htd-text) 12%, transparent) 0 2px, color-mix(in srgb, var(--htd-text) 4%, transparent) 2px 6px); opacity: 0.55; }
    .htd-waveform-bar { flex: 1 1 1px; min-width: 1px; background: linear-gradient(180deg, color-mix(in srgb, var(--htd-text) 88%, transparent), color-mix(in srgb, var(--htd-text-dim) 52%, transparent)); border-radius: 1px; }
    .htd-handle { position: absolute; z-index: 4; top: 0; bottom: 0; cursor: ew-resize; background: color-mix(in srgb, var(--htd-text) 16%, transparent); touch-action: none; user-select: none; transition: background var(--htd-transition); }
    .htd-handle:hover { background: color-mix(in srgb, var(--htd-text) 34%, transparent); }
    .htd-left { left: 0; }
    .htd-right { right: 0; }
    .is-selected { outline: 2px solid var(--htd-accent); outline-offset: -2px; }
    .htd-item.is-primary-selected { box-shadow: inset 0 0 0 2px var(--htd-text); }
    .htd-inspector { width: 100%; min-height: ${INSPECTOR_HEIGHT}px; overflow: visible; box-sizing: border-box; }
    .htd-inspector.has-selection { min-height: ${INSPECTOR_EDITOR_HEIGHT}px; }
    .htd-inspector-panel { height: 100%; min-height: ${INSPECTOR_EDITOR_HEIGHT}px; box-sizing: border-box; padding: 9px; border: 1px solid var(--htd-border); border-radius: var(--htd-radius); background: var(--htd-surface); overflow: visible; box-shadow: var(--htd-shadow); }
    .htd-inspector-panel.is-section-inspector { display: flex; flex-direction: column; gap: 7px; align-content: start; }
    .htd-inspector-panel.is-audio-inspector { display: grid; grid-template-columns: repeat(3, minmax(140px, 1fr)); grid-auto-rows: min-content; gap: 7px 10px; align-content: start; }
    .htd-inspector-panel.is-shot-inspector { display: flex; flex-direction: column; gap: 7px; align-content: start; }
    .htd-inspector-panel.is-boundary-inspector { display: flex; flex-direction: column; gap: 7px; align-content: start; }
    .htd-inspector-title { grid-column: 1 / -1; color: var(--htd-text); font-weight: 700; font-size: 12px; letter-spacing: 0.02em; line-height: 16px; }
    .htd-section-inspector-header { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-bottom: 7px; border-bottom: 1px solid var(--htd-border); }
    .htd-section-header-actions { min-width: 0; display: flex; align-items: center; gap: 6px; }
    .htd-section-shot-summary { min-width: 0; display: flex; align-items: center; gap: 6px; color: var(--htd-text-dim); }
    .htd-section-shot-name { max-width: 220px; padding: 2px 7px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .htd-section-shot-type { max-width: 92px; }
    .htd-inspector-row { min-width: 0; display: flex; align-items: center; gap: 6px; color: var(--htd-text-dim); }
    .htd-inspector-row.is-prompt { flex: 1 1 auto; flex-direction: column; align-items: stretch; }
    .htd-inspector-label { flex: 0 0 78px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text-faint); }
    .htd-inspector-row.is-prompt .htd-inspector-label { flex: 0 0 auto; font-weight: 600; color: var(--htd-text-dim); }
    .htd-inspector-control-row { min-height: 28px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
    .htd-inspector-compact-field { min-width: 0; display: inline-flex; align-items: center; gap: 6px; color: var(--htd-text-dim); }
    .htd-inspector-compact-field.is-strength { flex: 1 1 320px; }
    .htd-inspector-compact-field.is-boundary-mode { flex: 0 0 auto; }
    .htd-inspector-compact-field.is-boundary-toggle { min-height: 26px; }
    .htd-inspector-compact-label { flex: 0 0 auto; max-width: 92px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text-faint); }
    .htd-inspector-compact-field .htd-menu { flex: 0 0 auto; }
    .htd-prompt-wrap { position: relative; width: 100%; min-width: 0; flex: 1 1 auto; display: flex; align-items: stretch; }
    .htd-prompt { width: 100%; min-width: 0; flex: 1 1 auto; height: 86px; min-height: 86px; box-sizing: border-box; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text); padding: 7px 9px; line-height: 1.4; resize: none; transition: border-color .12s ease, box-shadow .12s ease; }
    .htd-prompt:focus, .htd-field:focus, .htd-number:focus, .htd-section-shot-name:focus { outline: none; border-color: var(--htd-focus); box-shadow: var(--htd-ring); }
    .htd-reference-completions { position: absolute; left: 0; right: 0; bottom: calc(100% + 4px); z-index: 45; max-height: 148px; overflow: auto; padding: 5px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius); background: var(--htd-surface); box-shadow: var(--htd-shadow-pop); }
    .htd-reference-completions[hidden] { display: none; }
    .htd-reference-completion { width: 100%; min-height: 28px; display: grid; grid-template-columns: 18px minmax(0, 1fr); align-items: center; gap: 6px; padding: 4px 6px; border: 0; border-radius: var(--htd-radius-sm); background: transparent; color: var(--htd-text); text-align: left; cursor: pointer; }
    .htd-reference-completion:hover, .htd-reference-completion.is-selected { background: var(--htd-surface-hover); color: var(--htd-text); }
    .htd-reference-completion-key { width: 16px; height: 16px; display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; background: var(--htd-accent); color: var(--htd-bg); font-size: 10px; font-weight: 700; line-height: 1; }
    .htd-reference-completion-text { min-width: 0; display: grid; gap: 1px; }
    .htd-reference-completion-tag { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-accent-strong); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; }
    .htd-reference-completion-description { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text-dim); font-size: 10px; }
    .htd-field { min-width: 0; width: 100%; height: 26px; box-sizing: border-box; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text); padding: 0 8px; transition: border-color .12s ease, box-shadow .12s ease; }
    .htd-boundary-inspector { min-width: 0; display: flex; flex-direction: column; gap: 7px; }
    .htd-boundary-prompt { min-height: 58px; height: 58px; padding: 6px 8px; line-height: 1.35; resize: vertical; }
    .htd-inspector-row.is-transition-prompt { align-items: flex-start; }
    .htd-inspector-row.is-transition-prompt .htd-inspector-label { padding-top: 6px; }
    .htd-number { width: 64px; height: 26px; box-sizing: border-box; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text); padding: 0 6px; transition: border-color .12s ease, box-shadow .12s ease; }
    .htd-strength-control { min-width: 0; flex: 1 1 auto; display: flex; align-items: center; gap: 6px; }
    .htd-strength-slider { min-width: 70px; flex: 1 1 auto; accent-color: var(--htd-accent); }
    .htd-strength-number { flex: 0 0 58px; width: 58px; }
    .htd-media-summary { grid-column: span 2; }
    .htd-inspector-panel.is-section-inspector .htd-media-summary { min-height: 24px; }
    .htd-media-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text); }
    .htd-shot-inspector { min-width: 0; display: flex; flex-direction: column; gap: 6px; padding-top: 7px; border-top: 1px solid var(--htd-border); }
    .htd-shot-inspector.is-standalone { padding-top: 0; border-top: 0; }
    .htd-shot-header-tools { min-width: 0; display: flex; align-items: center; justify-content: flex-end; gap: 6px; flex-wrap: wrap; }
    .htd-shot-header-tools .htd-inspector-compact-field { min-height: 24px; align-items: center; }
    .htd-shot-header-tools .htd-inspector-compact-label { max-width: 52px; }
    .htd-shot-name { width: min(100%, 520px); min-width: 220px; max-width: none; }
    .htd-shot-boundary-context { min-width: 0; min-height: 22px; display: flex; align-items: center; gap: 6px; }
    .htd-boundary-pill { max-width: 140px; padding: 2px 7px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .htd-boundary-warning { padding: 2px 7px; border: 1px solid var(--htd-warn-border); border-radius: var(--htd-radius-sm); background: var(--htd-warn-bg); color: var(--htd-warn); white-space: nowrap; }
    .htd-readiness-pill, .htd-take-status-pill { height: 24px; max-width: 132px; display: inline-flex; align-items: center; box-sizing: border-box; padding: 0 9px; border: 1px solid var(--htd-border-strong); border-radius: 999px; background: var(--htd-surface-2); color: var(--htd-text); line-height: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
    .htd-readiness-pill.is-ready { border-color: var(--htd-ok-border); background: var(--htd-ok-bg); color: var(--htd-ok); }
    .htd-readiness-pill.is-needs-take { border-color: var(--htd-warn-border); background: var(--htd-warn-bg); color: var(--htd-warn); }
    .htd-readiness-pill.is-needs-generation { border-color: var(--htd-info-border); background: var(--htd-info-bg); color: var(--htd-info); }
    .htd-readiness-pill.is-blocked { border-color: var(--htd-danger-border); background: var(--htd-danger-bg); color: var(--htd-danger-text); }
    .htd-shot-row, .htd-lora-summary-row { min-width: 0; min-height: 24px; display: flex; align-items: center; gap: 6px; color: var(--htd-text-dim); }
    .htd-shot-row-label { flex: 0 0 74px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text-faint); }
    .htd-shot-advanced { min-width: 0; display: grid; gap: 4px; color: var(--htd-text-dim); }
    .htd-shot-advanced-summary { width: max-content; cursor: pointer; color: var(--htd-text-dim); font-weight: 600; }
    .htd-shot-advanced-summary:hover { color: var(--htd-text); }
    .htd-shot-clip-select { max-width: 220px; }
    .htd-generated-take-select { max-width: 220px; }
    .htd-register-take-input { max-width: 240px; }
    .htd-assembly-status { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text); }
    .htd-shot-subheader { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--htd-text); font-weight: 700; }
    .htd-shot-subheader .htd-button { height: 22px; padding: 0 6px; font-size: 11px; }
    .htd-shot-card-header { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .htd-shot-card-header .htd-shot-header-tools { flex: 0 1 auto; }
    .htd-current-take-card { min-width: 0; display: grid; gap: 6px; padding: 8px; border: 1px solid var(--htd-border); border-radius: var(--htd-radius); background: var(--htd-surface-2); }
    .htd-current-take-body { min-width: 0; display: flex; align-items: stretch; gap: 10px; }
    /* Near-black covers are intentional privacy concealment — keep them opaque and dark. */
    .htd-current-take-thumb { position: relative; flex: 0 0 170px; height: 96px; box-sizing: border-box; display: flex; align-items: center; justify-content: center; border: 1px solid var(--htd-border); border-radius: var(--htd-radius-sm); background: var(--htd-privacy-cover); overflow: hidden; cursor: pointer; }
    .htd-current-take-thumb.is-empty { cursor: default; }
    .htd-current-take-thumb video { width: 100%; height: 100%; object-fit: contain; background: var(--htd-privacy-cover-strong); opacity: 1; transition: opacity 120ms ease; }
    .htd-root.is-private .htd-current-take-thumb video { opacity: 0; }
    .htd-root.is-private .htd-current-take-thumb:hover video { opacity: 1; }
    .htd-current-take-info { min-width: 0; flex: 1 1 auto; display: flex; flex-direction: column; justify-content: center; gap: 6px; }
    .htd-current-take-title-row { min-width: 0; display: flex; align-items: center; gap: 8px; }
    .htd-current-take-info .htd-take-actions { justify-content: flex-start; }
    .htd-current-take-info .htd-icon-button { width: 24px; min-width: 24px; height: 22px; padding: 0; }
    .htd-current-take-note { color: var(--htd-text-dim); font-size: 11px; }
    .htd-take-history { min-width: 0; min-height: 8px; display: flex; align-items: center; gap: 4px; }
    .htd-take-history-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--htd-border-strong); }
    .htd-take-history-dot.is-current { background: var(--htd-text); }
    .htd-take-history-dot.is-accepted { background: var(--htd-accent-strong); }
    .htd-take-history-dot.is-rejected { opacity: 0.35; }
    .htd-shot-details-body { min-width: 0; display: grid; gap: 6px; padding-top: 4px; }
    .htd-shot-details-row { min-width: 0; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .htd-shot-takes, .htd-shot-loras { min-width: 0; display: grid; gap: 4px; }
    .htd-shot-empty { color: var(--htd-text-faint); font-size: 11px; font-style: italic; }
    .htd-take-row, .htd-capture-row { min-width: 0; display: grid; grid-template-columns: minmax(92px, 0.28fr) minmax(140px, 1fr) auto auto; align-items: center; gap: 6px; }
    .htd-capture-row { grid-template-columns: minmax(92px, 0.28fr) minmax(160px, 1fr) auto; }
    .htd-take-row.is-compact, .htd-capture-row.is-compact { grid-template-columns: minmax(92px, 0.28fr) minmax(100px, 1fr) auto auto; }
    .htd-take-actions { min-width: 0; display: inline-flex; align-items: center; justify-content: flex-end; gap: 4px; }
    .htd-captures-modal .htd-take-row, .htd-captures-modal .htd-capture-row { grid-template-columns: minmax(92px, 0.28fr) minmax(180px, 1fr) 96px 164px; }
    .htd-captures-modal .htd-take-actions { width: 164px; display: grid; grid-template-columns: repeat(6, 24px); justify-content: end; gap: 4px; }
    .htd-captures-modal .htd-take-actions .htd-menu { width: 24px; min-width: 24px; }
    .htd-take-label, .htd-take-asset-summary, .htd-lora-count { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text); }
    .htd-take-asset-summary { color: var(--htd-text-dim); }
    .htd-take-status-pill { width: 96px; justify-content: center; }
    .htd-take-status-placeholder { visibility: hidden; pointer-events: none; }
    .htd-take-row .htd-button { height: 22px; padding: 0 6px; font-size: 11px; }
    .htd-take-row .htd-icon-button, .htd-capture-row .htd-icon-button { width: 24px; min-width: 24px; height: 22px; padding: 0; }
    .htd-shot-lora-targets-row { min-width: 0; min-height: 28px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; color: var(--htd-text-dim); }
    .htd-shot-lora-title { flex: 0 0 auto; color: var(--htd-text); font-weight: 700; }
    .htd-shot-lora-target { min-width: 0; display: inline-flex; align-items: center; gap: 5px; }
    .htd-shot-lora-target-label { color: var(--htd-text-faint); white-space: nowrap; }
    .htd-lora-target-separator { width: 1px; height: 18px; background: var(--htd-border-strong); opacity: 0.9; }
    .htd-project-loras { grid-column: 1 / -1; min-width: 0; display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); gap: 7px; padding-top: 8px; border-top: 1px solid var(--htd-border); }
    .htd-project-loras-title { grid-column: 1 / -1; color: var(--htd-text); font-weight: 700; }
    .htd-project-lora-row { min-width: 0; display: grid; grid-template-columns: 70px minmax(66px, 1fr) auto; align-items: center; gap: 6px; color: var(--htd-text-dim); }
    .htd-project-lora-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text-faint); }
    .htd-lora-actions { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 4px; }
    .htd-lora-actions .htd-icon-button { width: 24px; min-width: 24px; height: 22px; }
    .htd-captures-overlay { position: absolute; inset: 0; z-index: 22; display: flex; align-items: stretch; justify-content: center; background: var(--htd-overlay); backdrop-filter: blur(3px); padding: 12px; box-sizing: border-box; }
    .htd-captures-modal { width: min(860px, 100%); min-height: 0; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-lg); background: var(--htd-surface); box-shadow: var(--htd-shadow-pop); display: flex; flex-direction: column; }
    .htd-captures-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--htd-border); }
    .htd-captures-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text); font-weight: 700; }
    .htd-captures-header-actions { display: inline-flex; align-items: center; gap: 4px; }
    .htd-captures-body { min-height: 0; overflow: auto; padding: 12px; display: grid; gap: 12px; }
    .htd-captures-section { min-width: 0; display: grid; gap: 6px; }
    .htd-captures-section-title { color: var(--htd-text-dim); font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; font-size: 10px; }
    .htd-settings-overlay { position: absolute; inset: 0; z-index: 20; display: flex; align-items: stretch; justify-content: center; background: var(--htd-overlay); backdrop-filter: blur(3px); padding: 12px; box-sizing: border-box; }
    .htd-settings-modal { width: min(760px, 100%); min-height: 0; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-lg); background: var(--htd-surface); box-shadow: var(--htd-shadow-pop); display: flex; flex-direction: column; }
    .htd-settings-header { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 1px solid var(--htd-border); }
    .htd-settings-title { font-weight: 700; color: var(--htd-text); }
    .htd-settings-body { min-height: 0; overflow: auto; padding: 12px; display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)); gap: 8px; }
    .htd-settings-actions { display: flex; align-items: center; justify-content: flex-end; gap: 6px; padding: 10px 12px; border-top: 1px solid var(--htd-border); }
    .htd-global-settings-status { grid-column: 1 / -1; color: var(--htd-danger); font-size: 11px; line-height: 1.35; }
    .htd-setting-row { min-width: 0; display: flex; align-items: center; gap: 6px; justify-content: space-between; padding: 5px 8px; border: 1px solid var(--htd-border); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text-dim); }
    .htd-setting-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .htd-setting-number, .htd-setting-text { width: 120px; min-width: 0; height: 26px; box-sizing: border-box; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface); color: var(--htd-text); padding: 0 8px; }
    textarea.htd-setting-text { height: 52px; padding: 6px 8px; resize: vertical; }
    .htd-privacy-keystore-setting { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .htd-privacy-keystore-setting-status { color: var(--htd-text-dim); }
    .htd-reference-overlay { position: absolute; inset: 0; z-index: 21; display: flex; align-items: stretch; justify-content: center; background: var(--htd-overlay); backdrop-filter: blur(3px); padding: 12px; box-sizing: border-box; }
    .htd-reference-modal { width: min(820px, 100%); min-height: 0; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-lg); background: var(--htd-surface); box-shadow: var(--htd-shadow-pop); display: flex; flex-direction: column; }
    .htd-reference-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--htd-border); }
    .htd-reference-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 700; color: var(--htd-text); }
    .htd-reference-header-actions { display: inline-flex; align-items: center; gap: 4px; }
    .htd-reference-body { min-height: 0; overflow: auto; padding: 12px; display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
    ${htdScrollbarBlock(".htd-captures-body, .htd-settings-body, .htd-reference-body, .htd-reference-completions")}
    .htd-reference-empty { grid-column: 1 / -1; padding: 28px 8px; text-align: center; color: var(--htd-text-dim); }
    .htd-reference-card { min-width: 0; display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 8px; padding: 10px; border: 1px solid var(--htd-border); border-radius: var(--htd-radius); background: var(--htd-surface-2); transition: border-color .12s ease; }
    .htd-reference-card:hover { border-color: var(--htd-border-strong); }
    .htd-reference-thumb { width: 88px; height: 88px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-bg); color: var(--htd-text-dim); display: flex; align-items: center; justify-content: center; overflow: hidden; padding: 0; }
    .htd-reference-thumb img { width: 100%; height: 100%; object-fit: contain; display: block; }
    .htd-reference-meta { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
    .htd-reference-row { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 6px; }
    .htd-reference-tag { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-accent-strong); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; }
    .htd-reference-description { width: 100%; min-width: 0; height: 58px; box-sizing: border-box; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface); color: var(--htd-text); padding: 6px 8px; resize: vertical; }
    .htd-reference-strength-row { min-width: 0; display: flex; align-items: center; gap: 6px; color: var(--htd-text-dim); }
    .htd-reference-strength-label { flex: 0 0 auto; color: var(--htd-text-faint); }
    .htd-reference-actions { display: flex; align-items: center; gap: 4px; justify-content: flex-end; }
    .htd-project-library-save-button { color: var(--htd-text-dim); }
    .htd-project-library-save-button.is-active { color: var(--htd-accent-strong); }
    .htd-reference-library-action { color: var(--htd-text-dim); }
    .htd-reference-library-action.is-active { color: var(--htd-accent-strong); }
    .htd-reference-overlay.privacy-mode .htd-reference-thumb img,
    .htd-reference-overlay.privacy-mode .htd-reference-description { opacity: 0; }
    .htd-reference-overlay.privacy-mode .htd-reference-card:hover .htd-reference-thumb img,
    .htd-reference-overlay.privacy-mode .htd-reference-card:hover .htd-reference-description { opacity: 1; }
  `;
  documentRef.head.append(style);
}
