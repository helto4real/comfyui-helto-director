import {
  AUDIO_NORMALIZATION_MODES,
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  CROP_MODES,
  GLOBAL_PROMPT_POSITIONS,
  SECTION_EDIT_MODES,
  SNAP_MODES,
  TIMELINE_DISPLAY_MODES,
  VIDEO_TIMING_MODES,
} from "./schema.js";
import {
  createWaveformBars,
  mediaLabel,
  resolveMediaReference,
} from "./media.js";
import {
  addPickedMediaItem,
  replacePickedSectionMedia,
} from "./media_actions.js";
import { showMediaPicker } from "./media_picker.js";
import {
  AUDIO_LANE_HEIGHT,
  DIRECTOR_TRACK_HEIGHT,
  HANDLE_WIDTH,
  RULER_HEIGHT,
  TIMELINE_WIDTH,
  getTimelineWidth,
  secondsToPixels,
  timeFromClientX,
} from "./geometry.js";
import {
  addSection,
  deleteSelectedItem,
  duplicateSelectedSection,
  moveAudioClip,
  moveSection,
  resizeAudioClip,
  resizeSection,
  selectItem,
  splitSelectedSection,
  zoomToFit,
} from "./operations.js";
import { findWidget } from "./state.js";

export const TIMELINE_WIDGET_HEIGHT = 360;

export class TimelineRenderer {
  constructor(node, app, controller, container) {
    this.node = node;
    this.app = app;
    this.controller = controller;
    this.container = container;
    this.drag = null;
    this.settingsOpen = false;
    this.viewportWidth = TIMELINE_WIDTH;
    this.container.className = "helto-timeline-director";
    installStyles(container.ownerDocument ?? globalThis.document);
    this.render(controller.timeline);
  }

  destroy() {
    this.container.replaceChildren();
  }

  render(timeline = this.controller.timeline) {
    this.viewportWidth = this.measureViewportWidth();
    this.container.replaceChildren();
    const root = el("div", "htd-root");
    root.append(this.renderToolbar(), this.renderTimeline(timeline), this.renderInspector(timeline));
    if (this.settingsOpen) root.append(this.renderProjectSettings(timeline));
    this.container.append(root);
  }

  renderToolbar() {
    const toolbar = el("div", "htd-toolbar");
    toolbar.append(
      button("T", "Add Text Section", () => this.commitMutation((timeline) => addSection(timeline, "Text"), "add")),
      button("I", "Add Image Section", () => this.openMediaPicker(ASSET_TYPE_IMAGE)),
      button("V", "Add Video Section", () => this.openMediaPicker(ASSET_TYPE_VIDEO)),
      button("A", "Add Audio Clip", () => this.openMediaPicker(ASSET_TYPE_AUDIO)),
      selectControl("Timeline Display Mode", this.controller.timeline.ui_state.timeline_display_mode, TIMELINE_DISPLAY_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.timeline_display_mode = value; }, "settings change");
      }),
      selectControl("Section Edit Mode", this.controller.timeline.ui_state.section_edit_mode, SECTION_EDIT_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.section_edit_mode = value; }, "settings change");
      }),
      selectControl("Snap Mode", this.controller.timeline.ui_state.snap_mode, SNAP_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.snap_mode = value; }, "settings change");
      }),
      toggleButton("GP", "Use Global Prompt", this.controller.timeline.project.global_prompt.enabled, () => {
        this.commitMutation((timeline) => {
          timeline.project.global_prompt.enabled = !timeline.project.global_prompt.enabled;
        }, "settings change");
      }),
      button("S", "Split", () => this.commitMutation((timeline) => splitSelectedSection(timeline), "split")),
      button("D", "Duplicate", () => this.commitMutation((timeline) => duplicateSelectedSection(timeline), "duplicate")),
      button("X", "Delete", () => this.commitMutation((timeline) => deleteSelectedItem(timeline), "delete")),
      button("Fit", "Zoom to Fit", () => this.handleZoomToFit()),
      button("Set", "Project Settings", () => {
        this.settingsOpen = true;
        this.render();
      }),
    );
    return toolbar;
  }

  renderTimeline(timeline) {
    const viewport = el("div", "htd-viewport");
    viewport.scrollLeft = timeline.ui_state.scroll_x ?? 0;
    viewport.addEventListener("scroll", () => {
      this.controller.timeline.ui_state.scroll_x = viewport.scrollLeft;
    });

    const width = getTimelineWidth(timeline, this.viewportWidth);
    const stage = el("div", "htd-stage");
    stage.style.width = `${width}px`;
    stage.append(this.renderRuler(timeline, width), this.renderDirectorTrack(timeline), this.renderAudioTracks(timeline));
    viewport.append(stage);
    return viewport;
  }

  renderRuler(timeline, width) {
    const ruler = el("div", "htd-ruler");
    ruler.style.height = `${RULER_HEIGHT}px`;
    const duration = Number(timeline.project.duration_seconds);
    for (let second = 0; second <= Math.ceil(duration); second += 1) {
      const tick = el("div", "htd-tick");
      tick.style.left = `${secondsToPixels(second, timeline, this.viewportWidth)}px`;
      tick.textContent = `${second}s`;
      ruler.append(tick);
    }
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
    track.append(label("Director"));

    for (const gap of computeGaps(timeline)) {
      const item = el("div", "htd-gap");
      item.style.left = `${secondsToPixels(gap.start_time, timeline, this.viewportWidth)}px`;
      item.style.width = `${secondsToPixels(gap.end_time - gap.start_time, timeline, this.viewportWidth)}px`;
      item.title = "No Guidance";
      track.append(item);
    }

    for (const section of timeline.director_track.sections) {
      track.append(this.renderSection(timeline, section));
    }
    return track;
  }

  renderSection(timeline, section) {
    const item = el("div", `htd-item htd-section htd-${section.type.toLowerCase()}`);
    if (timeline.ui_state.selected_item_id === section.item_id) item.classList.add("is-selected");
    item.style.left = `${secondsToPixels(section.start_time, timeline, this.viewportWidth)}px`;
    item.style.width = `${Math.max(12, secondsToPixels(section.end_time - section.start_time, timeline, this.viewportWidth))}px`;
    const thumbnail = sectionThumbnailUrl(this.node, timeline, section);
    if (thumbnail) {
      item.style.backgroundImage = `linear-gradient(rgba(17,23,34,0.32), rgba(17,23,34,0.32)), url("${thumbnail}")`;
      item.style.backgroundSize = "cover";
      item.style.backgroundPosition = "center";
    }
    item.textContent = sectionLabel(timeline, section);
    item.title = sectionLabel(timeline, section);
    item.addEventListener("pointerdown", (event) => this.startSectionDrag(event, section, "move"));
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
      track.append(label("Audio"));
      for (const clip of trackData.clips) track.append(this.renderAudioClip(timeline, clip));
      wrapper.append(track);
    }
    return wrapper;
  }

  renderAudioClip(timeline, clip) {
    const item = el("div", "htd-item htd-audio-clip");
    if (timeline.ui_state.selected_item_id === clip.item_id) item.classList.add("is-selected");
    item.style.left = `${secondsToPixels(clip.start_time, timeline, this.viewportWidth)}px`;
    item.style.top = `${Number(clip.lane ?? 0) * AUDIO_LANE_HEIGHT + 4}px`;
    item.style.width = `${Math.max(12, secondsToPixels(clip.end_time - clip.start_time, timeline, this.viewportWidth))}px`;
    const clipLabel = el("div", "htd-audio-label");
    clipLabel.textContent = clip.name || mediaLabel(timeline, clip.audio, "Audio");
    item.append(clipLabel);
    if (shouldShowWaveform(timeline)) item.append(renderWaveform(this.node, timeline, clip));
    item.title = "Audio";
    item.addEventListener("pointerdown", (event) => this.startAudioDrag(event, clip, "audio-move"));

    const leftHandle = el("div", "htd-handle htd-left");
    leftHandle.style.width = `${HANDLE_WIDTH}px`;
    leftHandle.addEventListener("pointerdown", (event) => this.startAudioDrag(event, clip, "audio-start"));
    const rightHandle = el("div", "htd-handle htd-right");
    rightHandle.style.width = `${HANDLE_WIDTH}px`;
    rightHandle.addEventListener("pointerdown", (event) => this.startAudioDrag(event, clip, "audio-end"));
    item.append(leftHandle, rightHandle);
    return item;
  }

  renderInspector(timeline) {
    const inspector = el("div", "htd-inspector");
    const selected = timeline.director_track.sections.find((section) => section.item_id === timeline.ui_state.selected_item_id);
    const selectedAudio = findAudioClip(timeline, timeline.ui_state.selected_item_id);
    if (!selected && !selectedAudio) return inspector;

    if (selected?.type === ASSET_TYPE_IMAGE) {
      inspector.append(
        this.renderPromptInput(selected),
        this.renderNumberField(selected, "guide_strength", "Strength", { min: 0, max: 1, step: 0.05 }),
        this.renderSelectField(selected, "crop_mode", "Crop Mode", CROP_MODES),
      );
    } else if (selected?.type === ASSET_TYPE_VIDEO) {
      inspector.append(
        this.renderPromptInput(selected),
        this.renderNumberField(selected, "guide_strength", "Strength", { min: 0, max: 1, step: 0.05 }),
        this.renderSelectField(selected, "crop_mode", "Crop Mode", CROP_MODES),
        this.renderSelectField(selected, "timing_mode", "Timing", VIDEO_TIMING_MODES),
        this.renderNumberField(selected, "source_in", "In", { min: 0, step: 0.05 }),
        this.renderNumberField(selected, "source_out", "Out", { min: 0, step: 0.05, allowNull: true }),
      );
    } else if (selected?.type === "Text") {
      inspector.append(this.renderPromptInput(selected));
    } else if (selectedAudio) {
      inspector.append(
        this.renderTextField(selectedAudio, "name", "Name"),
        this.renderNumberField(selectedAudio, "volume", "Volume", { min: 0, max: 400, step: 1 }),
        this.renderNumberField(selectedAudio, "source_in", "In", { min: 0, step: 0.05 }),
        this.renderNumberField(selectedAudio, "source_out", "Out", { min: 0, step: 0.05, allowNull: true }),
        this.renderNumberField(selectedAudio, "fade_in", "Fade In", { min: 0, step: 0.05 }),
        this.renderNumberField(selectedAudio, "fade_out", "Fade Out", { min: 0, step: 0.05 }),
        this.renderCheckboxField(selectedAudio, "enabled", "On"),
        this.renderCheckboxField(selectedAudio, "locked", "Lock"),
      );
    }
    return inspector;
  }

  renderPromptInput(item) {
    if (this.controller.timeline.project.privacy.mode || this.controller.timeline.project.privacy.hide_text_prompts) {
      return this.container.ownerDocument.createDocumentFragment();
    }
    return this.renderTextField(item, "prompt", "Prompt", { className: "htd-prompt", debounced: true });
  }

  renderTextField(item, field, title, options = {}) {
    const input = el("input", options.className ?? "htd-field");
    input.value = item[field] ?? "";
    input.placeholder = title;
    input.title = title;
    input.addEventListener("input", () => {
      item[field] = input.value;
      if (options.debounced) {
        this.controller.scheduleDebouncedCommit("prompt typing");
      } else {
        this.controller.scheduleDebouncedCommit("settings change", { delayMs: 150 });
      }
    });
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
      this.commitMutation(() => {
        item[field] = raw === "" && options.allowNull ? null : Number(raw);
      }, "settings change");
    });
    return input;
  }

  renderSelectField(item, field, title, options) {
    return selectControl(title, item[field], options, (value) => {
      this.commitMutation(() => { item[field] = value; }, "settings change");
    });
  }

  renderCheckboxField(item, field, title) {
    return toggleButton(title, title, Boolean(item[field]), () => {
      this.commitMutation(() => { item[field] = !item[field]; }, "settings change");
    });
  }

  renderProjectSettings(timeline) {
    const overlay = el("div", "htd-settings-overlay");
    const modal = el("div", "htd-settings-modal");
    const header = el("div", "htd-settings-header");
    const title = el("div", "htd-settings-title");
    title.textContent = "Project Settings";
    header.append(title, button("X", "Close Project Settings", () => {
      this.settingsOpen = false;
      this.render();
    }));

    const body = el("div", "htd-settings-body");
    body.append(
      this.renderSettingSelect("Default Crop Mode", ["project", "default_crop_mode"], CROP_MODES),
      this.renderSettingCheckbox("Show Resolved Model Output", ["project", "settings", "show_resolved_model_output"]),
      this.renderSettingCheckbox("Allow Gaps", ["project", "settings", "allow_gaps"]),
      this.renderSettingCheckbox("Auto Close Gaps", ["project", "settings", "auto_close_gaps"]),
      this.renderSettingNumber("Minimum Section Duration", ["project", "settings", "minimum_section_duration_seconds"], { min: 0.05, step: 0.05 }),
      this.renderSettingText("Global Prompt", ["project", "global_prompt", "prompt"], true),
      this.renderSettingSelect("Global Prompt Position", ["project", "global_prompt", "position"], GLOBAL_PROMPT_POSITIONS),
      this.renderSettingCheckbox("Show Effective Prompt", ["project", "global_prompt", "show_effective_prompt"]),
      this.renderSettingCheckbox("Always Normalize Audio", ["project", "audio", "always_normalize"]),
      this.renderSettingSelect("Audio Normalization Mode", ["project", "audio", "normalization_mode"], AUDIO_NORMALIZATION_MODES),
      this.renderSettingNumber("Target LUFS", ["project", "audio", "target_lufs"], { step: 0.5 }),
      this.renderSettingNumber("True Peak Limit", ["project", "audio", "true_peak_limit_db"], { step: 0.1 }),
      this.renderSettingNumber("Default Audio Volume", ["project", "audio", "default_volume"], { min: 0, max: 400, step: 1 }),
      this.renderSettingNumber("Default Audio Fade In", ["project", "audio", "default_fade_in_seconds"], { min: 0, step: 0.05 }),
      this.renderSettingNumber("Default Audio Fade Out", ["project", "audio", "default_fade_out_seconds"], { min: 0, step: 0.05 }),
      this.renderSettingCheckbox("Privacy Mode", ["project", "privacy", "mode"]),
      this.renderSettingCheckbox("Hide Media Previews", ["project", "privacy", "hide_media_previews"]),
      this.renderSettingCheckbox("Hide Text Prompts", ["project", "privacy", "hide_text_prompts"]),
      this.renderSettingCheckbox("Encrypt Previews", ["project", "privacy", "encrypt_previews"]),
      this.renderSettingCheckbox("Show Section Labels", ["project", "display", "show_section_labels"]),
      this.renderSettingCheckbox("Show Thumbnails", ["project", "display", "show_thumbnails"]),
      this.renderSettingCheckbox("Show Audio Waveforms", ["project", "display", "show_audio_waveforms"]),
    );
    modal.append(header, body);
    overlay.append(modal);
    return overlay;
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

  startSectionDrag(event, section, mode) {
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget.closest(".htd-item");
    target?.setPointerCapture?.(event.pointerId);
    this.commitMutation((timeline) => selectItem(timeline, section.item_id), "select", { pushUndo: false });
    this.controller.beginTimelineGesture();
    this.drag = {
      itemId: section.item_id,
      mode,
      startX: event.clientX,
      startStart: section.start_time,
      startEnd: section.end_time,
    };
    target?.addEventListener("pointermove", this.onPointerMove);
    target?.addEventListener("pointerup", this.onPointerUp);
    target?.addEventListener("pointercancel", this.onPointerUp);
  }

  startAudioDrag(event, clip, mode) {
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget.closest(".htd-item");
    target?.setPointerCapture?.(event.pointerId);
    this.commitMutation((timeline) => selectItem(timeline, clip.item_id), "select", { pushUndo: false });
    this.controller.beginTimelineGesture();
    this.drag = {
      itemId: clip.item_id,
      mode,
      startX: event.clientX,
      startStart: clip.start_time,
      startEnd: clip.end_time,
    };
    target?.addEventListener("pointermove", this.onPointerMove);
    target?.addEventListener("pointerup", this.onPointerUp);
    target?.addEventListener("pointercancel", this.onPointerUp);
  }

  onPointerMove = (event) => {
    if (!this.drag) return;
    const deltaSeconds = timeFromClientX(event.clientX, event.currentTarget.parentElement, this.controller.timeline, this.viewportWidth)
      - timeFromClientX(this.drag.startX, event.currentTarget.parentElement, this.controller.timeline, this.viewportWidth);
    const timeline = this.controller.timeline;
    if (this.drag.mode === "move") {
      moveSection(timeline, this.drag.itemId, this.drag.startStart + deltaSeconds);
    } else if (this.drag.mode === "start") {
      resizeSection(timeline, this.drag.itemId, "start", this.drag.startStart + deltaSeconds);
    } else if (this.drag.mode === "audio-move") {
      moveAudioClip(timeline, this.drag.itemId, this.drag.startStart + deltaSeconds);
    } else if (this.drag.mode === "audio-start") {
      resizeAudioClip(timeline, this.drag.itemId, "start", this.drag.startStart + deltaSeconds);
    } else if (this.drag.mode === "audio-end") {
      resizeAudioClip(timeline, this.drag.itemId, "end", this.drag.startEnd + deltaSeconds);
    } else {
      resizeSection(timeline, this.drag.itemId, "end", this.drag.startEnd + deltaSeconds);
    }
    this.render(timeline);
  };

  onPointerUp = (event) => {
    event.currentTarget?.releasePointerCapture?.(event.pointerId);
    event.currentTarget?.removeEventListener("pointermove", this.onPointerMove);
    event.currentTarget?.removeEventListener("pointerup", this.onPointerUp);
    event.currentTarget?.removeEventListener("pointercancel", this.onPointerUp);
    this.drag = null;
    this.controller.endTimelineGesture("drag end");
  };

  commitMutation(mutator, reason, options = {}) {
    this.controller.updateTimeline(mutator, reason, options);
  }

  async openMediaPicker(assetType, options = {}) {
    try {
      const item = await showMediaPicker({
        assetType,
        node: this.node,
        documentRef: this.container.ownerDocument,
        mode: options.mode ?? "add",
        privacyMode: Boolean(
          this.controller.timeline.project.privacy.mode ||
          this.controller.timeline.project.privacy.hide_media_previews,
        ),
      });
      if (!item) return;
      const reason = options.mode === "replace" ? "replace media" : "add";
      this.commitMutation((timeline) => {
        if (options.mode === "replace") {
          replacePickedSectionMedia(timeline, options.itemId, assetType, item);
        } else {
          addPickedMediaItem(timeline, assetType, item);
        }
      }, reason);
    } catch (error) {
      const alertFn = this.container.ownerDocument.defaultView?.alert ?? globalThis.alert;
      alertFn?.(error.message);
    }
  }

  handleZoomToFit() {
    setNodeZoomWidgetValue(this.node, 1.0);
    this.commitMutation((timeline) => zoomToFit(timeline), "zoom to fit");
  }

  measureViewportWidth() {
    const viewport = this.container.querySelector?.(".htd-viewport");
    return Math.max(1, viewport?.clientWidth || this.container.clientWidth || TIMELINE_WIDTH);
  }
}

export function mountTimelineRenderer(node, app, controller) {
  if (node._timelineRenderer) return node._timelineRenderer;
  const container = document.createElement("div");
  const widget = node.addDOMWidget?.("video_timeline_director", "VideoTimelineDirector", container, {
    serialize: false,
    hideOnZoom: false,
    getMinHeight: () => TIMELINE_WIDGET_HEIGHT,
    getMaxHeight: () => TIMELINE_WIDGET_HEIGHT,
    getHeight: () => TIMELINE_WIDGET_HEIGHT,
  });
  const renderer = new TimelineRenderer(node, app, controller, container);
  node._timelineRenderer = renderer;
  node._timelineRendererWidget = widget;
  return renderer;
}

export function unmountTimelineRenderer(node) {
  node?._timelineRenderer?.destroy();
  delete node._timelineRenderer;
  delete node._timelineRendererWidget;
}

export function setNodeZoomWidgetValue(node, value) {
  const widget = findWidget(node, "zoom_level");
  if (!widget) return false;
  widget.value = value;
  widget.callback?.call(widget, value);
  return true;
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

function findAudioClip(timeline, itemId) {
  if (!itemId) return null;
  for (const track of timeline.audio_tracks) {
    const clip = track.clips.find((candidate) => candidate.item_id === itemId);
    if (clip) return clip;
  }
  return null;
}

function shouldShowWaveform(timeline) {
  return Boolean(
    timeline.project.display.show_audio_waveforms &&
    !timeline.project.privacy.mode &&
    !timeline.project.privacy.hide_media_previews,
  );
}

function renderWaveform(node, timeline, clip) {
  const waveform = el("div", "htd-waveform");
  const asset = resolveMediaReference(timeline, clip.audio);
  const bars = node?._timelineMediaCache?.getWaveform(asset?.asset_id) ?? createWaveformBars(asset?.asset_id ?? asset?.path ?? clip.item_id);
  for (const value of bars) {
    const bar = el("span", "htd-waveform-bar");
    bar.style.height = `${Math.round(value * 100)}%`;
    waveform.append(bar);
  }
  return waveform;
}

function sectionThumbnailUrl(node, timeline, section) {
  if (timeline.project.privacy.mode || timeline.project.privacy.hide_media_previews) return null;
  const reference = section.type === "Image" ? section.image : section.type === "Video" ? section.video : null;
  const asset = resolveMediaReference(timeline, reference);
  if (!asset?.asset_id) return null;
  return node?._timelineMediaCache?.getThumbnailUrl(asset.asset_id) ?? null;
}

function sectionLabel(timeline, section) {
  if (!timeline.project.display.show_section_labels) return "";
  if (timeline.project.privacy.mode || timeline.project.privacy.hide_text_prompts) return section.type;
  if (timeline.ui_state.timeline_display_mode === "Media") {
    const reference = section.type === "Image" ? section.image : section.type === "Video" ? section.video : null;
    return mediaLabel(timeline, reference, section.type);
  }
  if (timeline.ui_state.timeline_display_mode === "Prompts" && "prompt" in section) {
    return effectivePromptLabel(timeline, section) || section.type;
  }
  if (section.type === "Text") return section.prompt || "Text";
  return section.type;
}

function effectivePromptLabel(timeline, section) {
  const prompt = String(section.prompt ?? "").trim();
  const globalPrompt = timeline.project.global_prompt ?? {};
  const globalText = String(globalPrompt.prompt ?? "").trim();
  if (!globalPrompt.enabled || !globalPrompt.show_effective_prompt || !globalText) return prompt;
  if (!prompt) return globalText;
  return globalPrompt.position === "Suffix" ? `${prompt}, ${globalText}` : `${globalText}, ${prompt}`;
}

function button(text, title, onClick) {
  const control = el("button", "htd-button");
  control.type = "button";
  control.textContent = text;
  control.title = title;
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

function settingRow(title) {
  const row = el("label", "htd-setting-row");
  const labelText = el("span", "htd-setting-label");
  labelText.textContent = title;
  row.append(labelText);
  return row;
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

function label(text) {
  const item = el("div", "htd-track-label");
  item.textContent = text;
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
    .helto-timeline-director { height: ${TIMELINE_WIDGET_HEIGHT}px; overflow: hidden; color: #d8dde8; font: 12px/1.3 system-ui, sans-serif; }
    .htd-root { position: relative; height: 100%; display: flex; flex-direction: column; gap: 6px; }
    .htd-toolbar { display: flex; gap: 4px; align-items: center; min-height: 28px; overflow-x: auto; overflow-y: hidden; }
    .htd-button { min-width: 28px; height: 24px; padding: 0 7px; border: 1px solid #4b5568; border-radius: 4px; background: #202633; color: #f2f5f8; cursor: pointer; white-space: nowrap; }
    .htd-button.is-active { border-color: #d6b65a; background: #4b3d1e; color: #fff1b8; }
    .htd-select { min-width: 72px; max-width: 130px; height: 24px; border: 1px solid #4b5568; border-radius: 4px; background: #202633; color: #f2f5f8; }
    .htd-viewport { overflow-x: auto; overflow-y: hidden; border: 1px solid #3d4658; border-radius: 4px; background: #111722; height: 245px; }
    .htd-stage { position: relative; min-height: 100%; }
    .htd-ruler { position: relative; border-bottom: 1px solid #31394a; }
    .htd-tick { position: absolute; top: 3px; height: 20px; border-left: 1px solid #394255; padding-left: 4px; color: #9ba8bd; }
    .htd-playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: #e4c15c; pointer-events: none; }
    .htd-track { position: relative; border-bottom: 1px solid #273043; }
    .htd-track-label { position: sticky; left: 0; z-index: 5; width: 56px; height: 100%; display: flex; align-items: center; padding-left: 6px; background: rgba(17, 23, 34, 0.92); color: #9ba8bd; }
    .htd-item, .htd-gap { position: absolute; top: 5px; height: calc(100% - 10px); border-radius: 4px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; box-sizing: border-box; }
    .htd-gap { border: 1px dashed #3d4658; background: rgba(80, 88, 105, 0.16); }
    .htd-section { padding: 8px 10px; border: 1px solid rgba(255,255,255,0.28); cursor: grab; }
    .htd-text { background: #365d8f; }
    .htd-image { background: #4f7b52; }
    .htd-video { background: #7a5b35; }
    .htd-audio-track { min-height: ${AUDIO_LANE_HEIGHT}px; }
    .htd-audio-clip { padding: 4px 10px; background: #6c4a8f; border: 1px solid rgba(255,255,255,0.25); display: flex; flex-direction: column; gap: 3px; cursor: grab; }
    .htd-audio-label { overflow: hidden; text-overflow: ellipsis; }
    .htd-waveform { height: 12px; display: flex; align-items: center; gap: 1px; opacity: 0.88; }
    .htd-waveform-bar { flex: 1 1 1px; min-width: 1px; background: rgba(255,255,255,0.72); border-radius: 1px; }
    .htd-handle { position: absolute; top: 0; bottom: 0; cursor: ew-resize; background: rgba(255,255,255,0.16); }
    .htd-left { left: 0; }
    .htd-right { right: 0; }
    .is-selected { outline: 2px solid #f2d16b; outline-offset: -2px; }
    .htd-inspector { min-height: 34px; display: flex; align-items: center; gap: 5px; overflow-x: auto; overflow-y: hidden; }
    .htd-prompt { min-width: 100px; flex: 1 1 auto; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 8px; }
    .htd-field { min-width: 70px; max-width: 140px; flex: 1 1 80px; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 8px; }
    .htd-number { width: 64px; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 6px; }
    .htd-settings-overlay { position: absolute; inset: 0; z-index: 20; display: flex; align-items: stretch; justify-content: center; background: rgba(8, 11, 17, 0.82); padding: 10px; box-sizing: border-box; }
    .htd-settings-modal { width: min(760px, 100%); min-height: 0; border: 1px solid #465064; border-radius: 6px; background: #121925; box-shadow: 0 12px 34px rgba(0,0,0,0.4); display: flex; flex-direction: column; }
    .htd-settings-header { display: flex; align-items: center; justify-content: space-between; padding: 8px; border-bottom: 1px solid #30394c; }
    .htd-settings-title { font-weight: 600; color: #eef2f7; }
    .htd-settings-body { min-height: 0; overflow: auto; padding: 8px; display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)); gap: 7px; }
    .htd-setting-row { min-width: 0; display: flex; align-items: center; gap: 6px; justify-content: space-between; color: #c7d0df; }
    .htd-setting-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .htd-setting-number, .htd-setting-text { width: 120px; min-width: 0; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 8px; }
    textarea.htd-setting-text { height: 52px; padding: 6px 8px; resize: vertical; }
  `;
  documentRef.head.append(style);
}
