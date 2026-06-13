import {
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
} from "./schema.js";
import {
  attachMediaAsset,
  clearMediaReference,
  createBrowserFileAsset,
  createFilePathAsset,
  createWaveformBars,
  mediaLabel,
  resolveMediaReference,
} from "./media.js";
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
  addAudioClip,
  addSection,
  deleteSelectedItem,
  duplicateSelectedSection,
  moveSection,
  resizeSection,
  selectItem,
  splitSelectedSection,
  zoomToFit,
} from "./operations.js";

export const TIMELINE_WIDGET_HEIGHT = 360;

export class TimelineRenderer {
  constructor(node, app, controller, container) {
    this.node = node;
    this.app = app;
    this.controller = controller;
    this.container = container;
    this.drag = null;
    this.container.className = "helto-timeline-director";
    installStyles(container.ownerDocument ?? globalThis.document);
    this.render(controller.timeline);
  }

  destroy() {
    this.container.replaceChildren();
  }

  render(timeline = this.controller.timeline) {
    this.container.replaceChildren();
    const root = el("div", "htd-root");
    root.append(this.renderToolbar(), this.renderTimeline(timeline), this.renderInspector(timeline));
    this.container.append(root);
  }

  renderToolbar() {
    const toolbar = el("div", "htd-toolbar");
    toolbar.append(
      button("T", "Add Text Section", () => this.commitMutation((timeline) => addSection(timeline, "Text"), "add")),
      button("I", "Add Image Section", () => this.commitMutation((timeline) => addSection(timeline, "Image"), "add")),
      button("V", "Add Video Section", () => this.commitMutation((timeline) => addSection(timeline, "Video"), "add")),
      button("A", "Add Audio Clip", () => this.commitMutation((timeline) => addAudioClip(timeline), "add")),
      button("S", "Split", () => this.commitMutation((timeline) => splitSelectedSection(timeline), "split")),
      button("D", "Duplicate", () => this.commitMutation((timeline) => duplicateSelectedSection(timeline), "duplicate")),
      button("X", "Delete", () => this.commitMutation((timeline) => deleteSelectedItem(timeline), "delete")),
      button("[]", "Zoom to Fit", () => this.commitMutation((timeline) => zoomToFit(timeline), "zoom to fit")),
    );
    return toolbar;
  }

  renderTimeline(timeline) {
    const viewport = el("div", "htd-viewport");
    viewport.scrollLeft = timeline.ui_state.scroll_x ?? 0;
    viewport.addEventListener("scroll", () => {
      this.controller.timeline.ui_state.scroll_x = viewport.scrollLeft;
    });

    const width = getTimelineWidth(timeline, TIMELINE_WIDTH);
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
      tick.style.left = `${secondsToPixels(second, timeline, TIMELINE_WIDTH)}px`;
      tick.textContent = `${second}s`;
      ruler.append(tick);
    }
    const playhead = el("div", "htd-playhead");
    playhead.style.left = `${secondsToPixels(timeline.ui_state.playhead_time ?? 0, timeline, TIMELINE_WIDTH)}px`;
    ruler.append(playhead);
    ruler.addEventListener("pointerdown", (event) => {
      timeline.ui_state.playhead_time = timeFromClientX(event.clientX, ruler, timeline, TIMELINE_WIDTH);
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
      item.style.left = `${secondsToPixels(gap.start_time, timeline, TIMELINE_WIDTH)}px`;
      item.style.width = `${secondsToPixels(gap.end_time - gap.start_time, timeline, TIMELINE_WIDTH)}px`;
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
    item.style.left = `${secondsToPixels(section.start_time, timeline, TIMELINE_WIDTH)}px`;
    item.style.width = `${Math.max(12, secondsToPixels(section.end_time - section.start_time, timeline, TIMELINE_WIDTH))}px`;
    item.textContent = sectionLabel(section);
    item.title = sectionLabel(section);
    item.addEventListener("pointerdown", (event) => this.startSectionDrag(event, section, "move"));

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
    item.style.left = `${secondsToPixels(clip.start_time, timeline, TIMELINE_WIDTH)}px`;
    item.style.top = `${Number(clip.lane ?? 0) * AUDIO_LANE_HEIGHT + 4}px`;
    item.style.width = `${Math.max(12, secondsToPixels(clip.end_time - clip.start_time, timeline, TIMELINE_WIDTH))}px`;
    const clipLabel = el("div", "htd-audio-label");
    clipLabel.textContent = clip.name || mediaLabel(timeline, clip.audio, "Audio");
    item.append(clipLabel);
    if (shouldShowWaveform(timeline)) item.append(renderWaveform(timeline, clip));
    item.title = "Audio";
    item.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      this.commitMutation((timelineState) => selectItem(timelineState, clip.item_id), "select", { pushUndo: false });
    });
    return item;
  }

  renderInspector(timeline) {
    const inspector = el("div", "htd-inspector");
    const selected = timeline.director_track.sections.find((section) => section.item_id === timeline.ui_state.selected_item_id);
    const selectedAudio = findAudioClip(timeline, timeline.ui_state.selected_item_id);
    if (!selected && !selectedAudio) return inspector;

    if (selected?.type === ASSET_TYPE_IMAGE) {
      inspector.append(this.renderMediaControls(timeline, selected, ASSET_TYPE_IMAGE, selected.image));
    } else if (selected?.type === ASSET_TYPE_VIDEO) {
      inspector.append(this.renderMediaControls(timeline, selected, ASSET_TYPE_VIDEO, selected.video));
    } else if (selectedAudio) {
      inspector.append(this.renderMediaControls(timeline, selectedAudio, ASSET_TYPE_AUDIO, selectedAudio.audio));
    }

    if (selected && "prompt" in selected && !timeline.project.privacy.hide_text_prompts) {
      const input = el("input", "htd-prompt");
      input.value = selected.prompt ?? "";
      input.placeholder = "Prompt";
      input.addEventListener("input", () => {
        selected.prompt = input.value;
        this.controller.scheduleDebouncedCommit("prompt typing");
      });
      inspector.append(input);
    }
    return inspector;
  }

  renderMediaControls(timeline, item, assetType, reference) {
    const controls = el("div", "htd-media-controls");
    const pathInput = el("input", "htd-media-path");
    pathInput.value = resolveMediaReference(timeline, reference)?.path ?? "";
    pathInput.placeholder = `${assetType} file path`;

    const fileInput = el("input", "htd-file-input");
    fileInput.type = "file";
    fileInput.accept = acceptForAssetType(assetType);
    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      this.commitMutation((timelineState) => {
        attachMediaAsset(timelineState, item.item_id, createBrowserFileAsset(file, assetType));
      }, "replace media");
    });

    controls.append(
      pathInput,
      button("Attach", `Attach ${assetType}`, () => {
        const path = pathInput.value.trim();
        if (!path) return;
        this.commitMutation((timelineState) => {
          attachMediaAsset(timelineState, item.item_id, createFilePathAsset(assetType, path));
        }, "replace media");
      }),
      button("Choose", `Choose ${assetType}`, () => fileInput.click()),
      button("Clear", `Clear ${assetType}`, () => {
        this.commitMutation((timelineState) => clearMediaReference(timelineState, item.item_id), "replace media");
      }),
      fileInput,
    );
    return controls;
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

  onPointerMove = (event) => {
    if (!this.drag) return;
    const deltaSeconds = timeFromClientX(event.clientX, event.currentTarget.parentElement, this.controller.timeline, TIMELINE_WIDTH)
      - timeFromClientX(this.drag.startX, event.currentTarget.parentElement, this.controller.timeline, TIMELINE_WIDTH);
    const timeline = this.controller.timeline;
    if (this.drag.mode === "move") {
      moveSection(timeline, this.drag.itemId, this.drag.startStart + deltaSeconds);
    } else if (this.drag.mode === "start") {
      resizeSection(timeline, this.drag.itemId, "start", this.drag.startStart + deltaSeconds);
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

function renderWaveform(timeline, clip) {
  const waveform = el("div", "htd-waveform");
  const asset = resolveMediaReference(timeline, clip.audio);
  const bars = createWaveformBars(asset?.asset_id ?? asset?.path ?? clip.item_id);
  for (const value of bars) {
    const bar = el("span", "htd-waveform-bar");
    bar.style.height = `${Math.round(value * 100)}%`;
    waveform.append(bar);
  }
  return waveform;
}

function sectionLabel(section) {
  if (section.type === "Text") return section.prompt || "Text";
  return section.type;
}

function acceptForAssetType(assetType) {
  if (assetType === ASSET_TYPE_IMAGE) return "image/*";
  if (assetType === ASSET_TYPE_VIDEO) return "video/*";
  if (assetType === ASSET_TYPE_AUDIO) return "audio/*";
  return "";
}

function button(text, title, onClick) {
  const control = el("button", "htd-button");
  control.type = "button";
  control.textContent = text;
  control.title = title;
  control.addEventListener("click", onClick);
  return control;
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
    .htd-root { height: 100%; display: flex; flex-direction: column; gap: 6px; }
    .htd-toolbar { display: flex; gap: 4px; align-items: center; min-height: 28px; }
    .htd-button { min-width: 28px; height: 24px; padding: 0 7px; border: 1px solid #4b5568; border-radius: 4px; background: #202633; color: #f2f5f8; cursor: pointer; white-space: nowrap; }
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
    .htd-audio-clip { padding: 4px 6px; background: #6c4a8f; border: 1px solid rgba(255,255,255,0.25); display: flex; flex-direction: column; gap: 3px; }
    .htd-audio-label { overflow: hidden; text-overflow: ellipsis; }
    .htd-waveform { height: 12px; display: flex; align-items: center; gap: 1px; opacity: 0.88; }
    .htd-waveform-bar { flex: 1 1 1px; min-width: 1px; background: rgba(255,255,255,0.72); border-radius: 1px; }
    .htd-handle { position: absolute; top: 0; bottom: 0; cursor: ew-resize; background: rgba(255,255,255,0.16); }
    .htd-left { left: 0; }
    .htd-right { right: 0; }
    .is-selected { outline: 2px solid #f2d16b; outline-offset: -2px; }
    .htd-inspector { min-height: 34px; display: flex; align-items: center; gap: 5px; }
    .htd-prompt { min-width: 100px; flex: 1 1 auto; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 8px; }
    .htd-media-controls { display: flex; gap: 4px; min-width: 0; flex: 1 1 auto; }
    .htd-media-path { min-width: 120px; flex: 1 1 auto; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 8px; }
    .htd-file-input { display: none; }
  `;
  documentRef.head.append(style);
}
