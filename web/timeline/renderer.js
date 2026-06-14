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
  RANGE_CONTROL_HEIGHT,
  RULER_HEIGHT,
  TIMELINE_RIGHT_PADDING,
  TIMELINE_WIDTH,
  clampTimelineViewRange,
  durationToPixels,
  getProjectWholeSeconds,
  getPixelsPerSecond,
  getTimelineViewportHeight,
  getTimelineViewRange,
  getTimelineWidth,
  secondsToPixels,
  timeFromClientX,
} from "./geometry.js";
import {
  addSection,
  deleteSelectedItem,
  duplicateSelectedSection,
  findSection,
  moveAudioClip,
  moveSection,
  resizeAudioClip,
  resizeSection,
  selectItem,
  splitSelectedSection,
  zoomToFit,
} from "./operations.js";

const TOOLBAR_HEIGHT = 28;
const INSPECTOR_HEIGHT = 34;
const INSPECTOR_EDITOR_HEIGHT = 188;
const ROOT_GAP = 6;

export function getTimelineWidgetHeight(timeline) {
  return TOOLBAR_HEIGHT + RANGE_CONTROL_HEIGHT + getTimelineViewportHeight(timeline) + getInspectorHeight(timeline) + ROOT_GAP * 3;
}

export function setLiveItemField(timeline, item, field, value) {
  const liveItem = resolveLiveTimelineItem(timeline, item) ?? item;
  liveItem[field] = value;
  return liveItem;
}

export class TimelineRenderer {
  constructor(node, app, controller, container) {
    this.node = node;
    this.app = app;
    this.controller = controller;
    this.container = container;
    this.drag = null;
    this.settingsOpen = false;
    this.openMenu = null;
    this.remeasureHandle = null;
    this.resizeObserver = null;
    this.observedWidth = null;
    this.viewportWidth = TIMELINE_WIDTH;
    this.container.className = "helto-timeline-director";
    installStyles(container.ownerDocument ?? globalThis.document);
    this.render(controller.timeline);
    this.startResizeObserver();
  }

  destroy() {
    this.cancelViewportRemeasure();
    this.stopResizeObserver();
    this.container.replaceChildren();
  }

  render(timeline = this.controller.timeline) {
    this.viewportWidth = this.measureViewportWidth();
    this.container.style.height = `${getTimelineWidgetHeight(timeline)}px`;
    this.container.replaceChildren();
    const root = el("div", "htd-root");
    root.append(this.renderToolbar(), this.renderRangeControl(timeline), this.renderTimeline(timeline), this.renderInspector(timeline));
    if (this.settingsOpen) root.append(this.renderProjectSettings(timeline));
    this.container.append(root);
    this.scheduleViewportRemeasure();
  }

  renderToolbar() {
    const toolbar = el("div", "htd-toolbar");
    toolbar.append(
      iconButton("text", "Add Text Section", () => this.commitMutation((timeline) => addSection(timeline, "Text"), "add")),
      iconButton("image", "Add Image Section", () => this.openMediaPicker(ASSET_TYPE_IMAGE)),
      iconButton("video", "Add Video Section", () => this.openMediaPicker(ASSET_TYPE_VIDEO)),
      iconButton("audio", "Add Audio Clip", () => this.openMediaPicker(ASSET_TYPE_AUDIO)),
      this.renderToolbarMenu("display", "Display Mode", "layers", this.controller.timeline.ui_state.timeline_display_mode, TIMELINE_DISPLAY_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.timeline_display_mode = value; }, "settings change");
      }),
      this.renderToolbarMenu("edit", "Edit Mode", "trim", this.controller.timeline.ui_state.section_edit_mode, SECTION_EDIT_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.section_edit_mode = value; }, "settings change");
      }),
      this.renderToolbarMenu("snap", "Snap Mode", "magnet", this.controller.timeline.ui_state.snap_mode, SNAP_MODES, (value) => {
        this.commitMutation((timeline) => { timeline.ui_state.snap_mode = value; }, "settings change");
      }),
      toggleIconButton("global", "Use Global Prompt", this.controller.timeline.project.global_prompt.enabled, () => {
        this.commitMutation((timeline) => {
          timeline.project.global_prompt.enabled = !timeline.project.global_prompt.enabled;
        }, "settings change");
      }),
      iconButton("split", "Split", () => this.commitMutation((timeline) => splitSelectedSection(timeline), "split")),
      iconButton("duplicate", "Duplicate", () => this.commitMutation((timeline) => duplicateSelectedSection(timeline), "duplicate")),
      iconButton("delete", "Delete", () => this.commitMutation((timeline) => deleteSelectedItem(timeline), "delete")),
      iconButton("fit", "Zoom to Fit", () => this.handleZoomToFit()),
      iconButton("settings", "Project Settings", () => {
        this.settingsOpen = true;
        this.render();
      }),
    );
    return toolbar;
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
      const tick = el("div", "htd-tick");
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

    for (const section of timeline.director_track.sections) {
      track.append(this.renderSection(timeline, section));
    }
    return track;
  }

  renderSection(timeline, section) {
    const item = el("div", `htd-item htd-section htd-${section.type.toLowerCase()}`);
    if (timeline.ui_state.selected_item_id === section.item_id) item.classList.add("is-selected");
    item.style.left = `${secondsToPixels(section.start_time, timeline, this.viewportWidth)}px`;
    const itemWidth = Math.max(12, durationToPixels(section.end_time - section.start_time, timeline, this.viewportWidth));
    item.style.width = `${itemWidth}px`;
    const thumbnail = sectionThumbnailUrl(this.node, timeline, section);
    if (thumbnail) {
      item.classList.add("has-preview");
      item.append(renderSectionPreview(timeline, thumbnail, itemWidth));
    }
    const labelText = sectionLabel(timeline, section);
    const labelElement = el("span", "htd-section-label");
    labelElement.textContent = labelText;
    item.append(labelElement);
    item.title = labelText;
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
      track.append(trackLabel("audio", "Audio"));
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
    item.style.width = `${Math.max(12, durationToPixels(clip.end_time - clip.start_time, timeline, this.viewportWidth))}px`;
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
    inspector.classList.toggle("has-selection", Boolean(selected || selectedAudio));
    if (!selected && !selectedAudio) return inspector;

    const panel = el("div", "htd-inspector-panel");
    if (selected?.type === ASSET_TYPE_IMAGE) {
      panel.classList.add("is-section-inspector");
      panel.append(
        inspectorTitle("Image Section"),
        this.renderInspectorControlRow(
          this.renderInspectorCompactField("Guide Strength:", this.renderGuideStrengthField(selected), "is-strength"),
          this.renderInspectorCompactField("Crop Mode:", this.renderIconSelectField(selected, "crop_mode", "Crop Mode", CROP_MODES, "crop")),
        ),
        this.renderPromptRow(selected),
      );
    } else if (selected?.type === ASSET_TYPE_VIDEO) {
      panel.classList.add("is-section-inspector");
      panel.append(
        inspectorTitle("Video Section"),
        this.renderInspectorControlRow(
          this.renderInspectorCompactField("Guide Strength:", this.renderGuideStrengthField(selected), "is-strength"),
          this.renderInspectorCompactField("Crop Mode:", this.renderIconSelectField(selected, "crop_mode", "Crop Mode", CROP_MODES, "crop")),
          this.renderInspectorCompactField("Timing Mode:", this.renderIconSelectField(selected, "timing_mode", "Timing Mode", VIDEO_TIMING_MODES, "timing")),
        ),
        this.renderInspectorControlRow(
          this.renderInspectorCompactField("Source In:", this.renderNumberField(selected, "source_in", "Source In", { min: 0, step: 0.05 })),
          this.renderInspectorCompactField("Source Out:", this.renderNumberField(selected, "source_out", "Source Out", { min: 0, step: 0.05, allowNull: true })),
        ),
        this.renderPromptRow(selected),
      );
    } else if (selected?.type === "Text") {
      panel.classList.add("is-section-inspector");
      panel.append(
        inspectorTitle("Text Section"),
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
    }
    inspector.append(panel);
    return inspector;
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
    return this.renderTextField(item, "prompt", "Prompt", {
      className: "htd-prompt",
      debounced: true,
      multiline: true,
      placeholder: "Write your prompt here...",
      rows: 5,
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

  startItemDrag(event, dragState) {
    event.preventDefault();
    event.stopPropagation();
    const target = event.currentTarget.closest(".htd-item");
    target?.setPointerCapture?.(event.pointerId);
    this.commitMutation((timeline) => selectItem(timeline, dragState.itemId), "select", { pushUndo: false, rerender: false });
    this.controller.beginTimelineGesture();
    const moveTarget = target?.ownerDocument ?? this.container.ownerDocument ?? globalThis.document;
    this.drag = {
      ...dragState,
      startX: event.clientX,
      moveTarget,
      captureTarget: target,
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
    const deltaSeconds = (Number(event.clientX) - Number(this.drag.startX)) / getPixelsPerSecond(timeline, this.viewportWidth);
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
    const moveTarget = this.drag?.moveTarget;
    const captureTarget = this.drag?.captureTarget;
    captureTarget?.releasePointerCapture?.(event.pointerId);
    moveTarget?.removeEventListener("pointermove", this.onPointerMove);
    moveTarget?.removeEventListener("pointerup", this.onPointerUp);
    moveTarget?.removeEventListener("pointercancel", this.onPointerUp);
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
    this.commitMutation((timeline) => zoomToFit(timeline), "zoom to fit");
  }

  measureViewportWidth() {
    const viewport = this.container.querySelector?.(".htd-viewport");
    return Math.max(1, viewport?.clientWidth || this.container.clientWidth || TIMELINE_WIDTH);
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
  const widgetHeight = () => getTimelineWidgetHeight(controller.timeline);
  const widget = node.addDOMWidget?.("video_timeline_director", "VideoTimelineDirector", container, {
    serialize: false,
    hideOnZoom: false,
    getMinHeight: widgetHeight,
    getMaxHeight: widgetHeight,
    getHeight: widgetHeight,
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
  const itemId = item?.item_id;
  if (!itemId) return item;
  return findSection(timeline, itemId) ?? findAudioClip(timeline, itemId) ?? item;
}

function getInspectorHeight(timeline) {
  const selected = timeline?.director_track?.sections?.find((section) => section.item_id === timeline?.ui_state?.selected_item_id);
  const selectedAudio = findAudioClip(timeline, timeline?.ui_state?.selected_item_id);
  return selected || selectedAudio ? INSPECTOR_EDITOR_HEIGHT : INSPECTOR_HEIGHT;
}

function shouldRenderPromptInput(timeline, item) {
  return Boolean(
    item &&
    "prompt" in item &&
    !timeline?.project?.privacy?.mode &&
    !timeline?.project?.privacy?.hide_text_prompts,
  );
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
  if (
    timeline.project.privacy.mode ||
    timeline.project.privacy.hide_media_previews ||
    timeline.project.display.show_thumbnails === false
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

function iconButton(iconName, title, onClick) {
  const control = button("", title, onClick);
  control.classList.add("htd-icon-button");
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
  audio: `<svg viewBox="0 0 24 24"><path d="M6 15V9M10 18V6M14 16V8M18 14v-4"/></svg>`,
  layers: `<svg viewBox="0 0 24 24"><path d="m12 4 8 4-8 4-8-4z"/><path d="m4 12 8 4 8-4"/><path d="m4 16 8 4 8-4"/></svg>`,
  trim: `<svg viewBox="0 0 24 24"><path d="M6 5v14M18 5v14M6 12h12"/><path d="m9 9-3 3 3 3M15 9l3 3-3 3"/></svg>`,
  magnet: `<svg viewBox="0 0 24 24"><path d="M7 5v7a5 5 0 0 0 10 0V5"/><path d="M7 9h4M13 9h4"/></svg>`,
  global: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16"/></svg>`,
  split: `<svg viewBox="0 0 24 24"><path d="M12 4v16"/><path d="M5 7h4M5 17h4M15 7h4M15 17h4"/></svg>`,
  duplicate: `<svg viewBox="0 0 24 24"><rect x="8" y="8" width="10" height="10" rx="2"/><path d="M6 14H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v1"/></svg>`,
  delete: `<svg viewBox="0 0 24 24"><path d="M6 7h12M10 7V5h4v2M9 10v7M15 10v7M8 7l1 12h6l1-12"/></svg>`,
  fit: `<svg viewBox="0 0 24 24"><path d="M5 9V5h4M15 5h4v4M19 15v4h-4M9 19H5v-4"/><path d="M8 8h8v8H8z"/></svg>`,
  settings: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M4.8 7.5l2.6 1.5M16.6 15l2.6 1.5M19.2 7.5 16.6 9M7.4 15l-2.6 1.5"/></svg>`,
  director: `<svg viewBox="0 0 24 24"><path d="M4 7h16M4 17h16M8 4v6M16 14v6"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></svg>`,
  crop: `<svg viewBox="0 0 24 24"><path d="M6 3v12a3 3 0 0 0 3 3h12"/><path d="M3 6h12a3 3 0 0 1 3 3v12"/><path d="M9 9h6v6H9z"/></svg>`,
  timing: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/></svg>`,
};

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
    .helto-timeline-director { overflow: hidden; color: #d8dde8; font: 12px/1.3 system-ui, sans-serif; }
    .htd-root { position: relative; height: 100%; display: flex; flex-direction: column; gap: 6px; }
    .htd-toolbar { position: relative; z-index: 15; display: flex; gap: 4px; align-items: center; min-height: 28px; overflow: visible; }
    .htd-button { min-width: 28px; height: 24px; padding: 0 7px; border: 1px solid #4b5568; border-radius: 4px; background: #202633; color: #f2f5f8; cursor: pointer; white-space: nowrap; }
    .htd-icon-button { width: 28px; min-width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center; }
    .htd-icon { width: 16px; height: 16px; display: inline-flex; align-items: center; justify-content: center; }
    .htd-icon svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .htd-button.is-active { border-color: #d6b65a; background: #4b3d1e; color: #fff1b8; }
    .htd-menu { position: relative; display: inline-flex; align-items: center; }
    .htd-menu-button { width: 34px; min-width: 34px; }
    .htd-menu-button::after { content: ""; width: 0; height: 0; margin-left: 2px; border-left: 3px solid transparent; border-right: 3px solid transparent; border-top: 4px solid currentColor; opacity: 0.78; }
    .htd-menu-value { min-width: 0; max-width: 118px; margin-right: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; align-self: center; color: #eef2f7; }
    .htd-menu-list { position: absolute; top: 28px; left: 0; z-index: 30; min-width: 132px; padding: 4px; border: 1px solid #465064; border-radius: 4px; background: #151c29; box-shadow: 0 8px 20px rgba(0,0,0,0.42); }
    .htd-menu.opens-above .htd-menu-list { top: auto; bottom: 28px; }
    .htd-menu.align-end .htd-menu-list { right: 0; left: auto; }
    .htd-menu-item { width: 100%; height: 24px; padding: 0 8px; border: 0; border-radius: 3px; background: transparent; color: #d8dde8; text-align: left; cursor: pointer; white-space: nowrap; }
    .htd-menu-item:hover, .htd-menu-item.is-active { background: #293244; color: #f7f9fc; }
    .htd-select { min-width: 72px; max-width: 130px; height: 24px; border: 1px solid #4b5568; border-radius: 4px; background: #202633; color: #f2f5f8; }
    .htd-range-control { height: ${RANGE_CONTROL_HEIGHT}px; display: flex; align-items: center; gap: 0; box-sizing: border-box; }
    .htd-range-gutter { width: ${TIMELINE_RIGHT_PADDING}px; flex: 0 0 ${TIMELINE_RIGHT_PADDING}px; }
    .htd-range-bar { position: relative; height: 8px; flex: 1 1 auto; margin-right: ${TIMELINE_RIGHT_PADDING}px; border-radius: 999px; background: #111722; border: 1px solid #3d4658; cursor: pointer; box-sizing: border-box; }
    .htd-range-active { position: absolute; top: -1px; bottom: -1px; min-width: 8px; border-radius: 999px; background: linear-gradient(90deg, rgba(123, 148, 180, 0.95), rgba(226, 194, 92, 0.82)); border: 1px solid rgba(242, 209, 107, 0.72); box-sizing: border-box; }
    .htd-range-handle { position: absolute; top: 50%; width: 12px; height: 18px; border: 1px solid #d8dde8; border-radius: 3px; background: #202633; transform: translate(-50%, -50%); cursor: ew-resize; box-shadow: 0 1px 4px rgba(0,0,0,0.36); }
    .htd-range-start { left: 0; }
    .htd-range-end { left: 100%; }
    .htd-viewport { overflow: hidden; box-sizing: border-box; border: 1px solid #3d4658; border-radius: 4px; background: #111722; }
    .htd-stage { position: relative; min-height: 100%; }
    .htd-ruler { position: relative; border-bottom: 1px solid #31394a; }
    .htd-tick { position: absolute; z-index: 2; top: 3px; height: 20px; border-left: 1px solid #394255; padding-left: 4px; color: #9ba8bd; }
    .htd-project-end { position: absolute; z-index: 1; top: 0; bottom: 0; border-left: 1px solid rgba(226, 194, 92, 0.66); background: linear-gradient(90deg, rgba(226,194,92,0.12), rgba(226,194,92,0.03), rgba(17,23,34,0)); pointer-events: none; }
    .htd-playhead { position: absolute; z-index: 3; top: 0; bottom: 0; width: 2px; background: #e4c15c; pointer-events: none; }
    .htd-track { position: relative; border-bottom: 1px solid #273043; }
    .htd-track-label { position: sticky; left: 0; z-index: 5; width: ${TIMELINE_RIGHT_PADDING}px; height: 100%; display: flex; align-items: center; justify-content: center; background: rgba(17, 23, 34, 0.92); color: #9ba8bd; }
    .htd-item, .htd-gap { position: absolute; top: 5px; height: calc(100% - 10px); border-radius: 4px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; box-sizing: border-box; }
    .htd-item { touch-action: none; user-select: none; }
    .htd-gap { border: 1px dashed #3d4658; background: rgba(80, 88, 105, 0.16); }
    .htd-section { padding: 0; border: 1px solid rgba(255,255,255,0.28); cursor: grab; }
    .htd-section-label { position: absolute; z-index: 3; top: 8px; left: 10px; right: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-shadow: 0 1px 2px rgba(0,0,0,0.82); pointer-events: none; }
    .htd-section-preview { position: absolute; inset: 0; z-index: 1; display: flex; align-items: stretch; gap: 2px; overflow: hidden; background: rgba(6,10,16,0.34); pointer-events: none; }
    .htd-section-preview-frame { flex: 0 0 auto; height: 100%; display: flex; align-items: center; justify-content: center; background: rgba(4,7,11,0.28); }
    .htd-section-preview img { width: 100%; height: 100%; object-fit: contain; display: block; }
    .htd-text { background: #365d8f; }
    .htd-image { background: #4f7b52; }
    .htd-video { background: #7a5b35; }
    .htd-audio-track { min-height: ${AUDIO_LANE_HEIGHT}px; }
    .htd-audio-clip { padding: 4px 10px; background: #6c4a8f; border: 1px solid rgba(255,255,255,0.25); display: flex; flex-direction: column; gap: 3px; cursor: grab; }
    .htd-audio-label { overflow: hidden; text-overflow: ellipsis; }
    .htd-waveform { height: 12px; display: flex; align-items: center; gap: 1px; opacity: 0.88; }
    .htd-waveform-bar { flex: 1 1 1px; min-width: 1px; background: rgba(255,255,255,0.72); border-radius: 1px; }
    .htd-handle { position: absolute; z-index: 4; top: 0; bottom: 0; cursor: ew-resize; background: rgba(255,255,255,0.16); touch-action: none; user-select: none; }
    .htd-left { left: 0; }
    .htd-right { right: 0; }
    .is-selected { outline: 2px solid #f2d16b; outline-offset: -2px; }
    .htd-inspector { min-height: ${INSPECTOR_HEIGHT}px; overflow: visible; box-sizing: border-box; }
    .htd-inspector.has-selection { min-height: ${INSPECTOR_EDITOR_HEIGHT}px; }
    .htd-inspector-panel { height: 100%; min-height: ${INSPECTOR_EDITOR_HEIGHT}px; box-sizing: border-box; padding: 7px; border: 1px solid #30394c; border-radius: 4px; background: rgba(17, 23, 34, 0.48); overflow: visible; }
    .htd-inspector-panel.is-section-inspector { display: flex; flex-direction: column; gap: 6px; align-content: start; }
    .htd-inspector-panel.is-audio-inspector { display: grid; grid-template-columns: repeat(3, minmax(140px, 1fr)); grid-auto-rows: min-content; gap: 6px 8px; align-content: start; }
    .htd-inspector-title { grid-column: 1 / -1; color: #eef2f7; font-weight: 600; line-height: 16px; }
    .htd-inspector-row { min-width: 0; display: flex; align-items: center; gap: 6px; color: #c7d0df; }
    .htd-inspector-row.is-prompt { flex: 1 1 auto; flex-direction: column; align-items: stretch; }
    .htd-inspector-label { flex: 0 0 78px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #9ba8bd; }
    .htd-inspector-row.is-prompt .htd-inspector-label { flex: 0 0 auto; }
    .htd-inspector-control-row { min-height: 28px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
    .htd-inspector-compact-field { min-width: 0; display: inline-flex; align-items: center; gap: 6px; color: #c7d0df; }
    .htd-inspector-compact-field.is-strength { flex: 1 1 320px; }
    .htd-inspector-compact-label { flex: 0 0 auto; max-width: 92px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #9ba8bd; }
    .htd-inspector-compact-field .htd-menu { flex: 0 0 auto; }
    .htd-prompt { width: 100%; min-width: 0; flex: 1 1 auto; height: 86px; min-height: 86px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 6px 8px; line-height: 1.3; resize: none; }
    .htd-field { min-width: 0; width: 100%; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 8px; }
    .htd-number { width: 64px; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 6px; }
    .htd-strength-control { min-width: 0; flex: 1 1 auto; display: flex; align-items: center; gap: 6px; }
    .htd-strength-slider { min-width: 70px; flex: 1 1 auto; accent-color: #d6b65a; }
    .htd-strength-number { flex: 0 0 58px; width: 58px; }
    .htd-media-summary { grid-column: span 2; }
    .htd-inspector-panel.is-section-inspector .htd-media-summary { min-height: 24px; }
    .htd-media-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #eef2f7; }
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
