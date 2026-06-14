import { deepClone } from "./schema.js";
import { normalizeVideoTimeline } from "./migration.js";
import { normalizeTimelineViewRange } from "./geometry.js";
import { validateVideoTimeline } from "./validation.js";
import { TimelineUndoStack } from "./undo.js";

export const VIDEO_TIMELINE_WIDGET = "video_timeline_json";

export class TimelineStateController {
  constructor(node, app, options = {}) {
    this.node = node;
    this.app = app;
    this.window = options.window ?? globalThis.window;
    this.debounceMs = options.debounceMs ?? 300;
    this.undo = new TimelineUndoStack(options.undoLimit ?? 100);
    this.hiddenWidget = findWidget(node, VIDEO_TIMELINE_WIDGET);
    this.timeline = loadTimelineState(node);
    this.pendingDebounce = null;
    this.gestureStartState = null;
    this.destroyed = false;
    this._onKeyDown = (event) => this.handleKeyDown(event);
    this._onMouseUp = () => this.endTimelineGesture("drag end");

    hideWidget(this.hiddenWidget);
    this.installEventListeners();
    this.commitTimelineChange("mount", { pushUndo: false, markDirty: false });
  }

  destroy() {
    this.flushDebouncedCommit();
    this.destroyed = true;
    this.window?.removeEventListener?.("keydown", this._onKeyDown, true);
    this.window?.removeEventListener?.("mouseup", this._onMouseUp, true);
    this.window?.removeEventListener?.("pointerup", this._onMouseUp, true);
  }

  installEventListeners() {
    this.window?.addEventListener?.("keydown", this._onKeyDown, true);
    this.window?.addEventListener?.("mouseup", this._onMouseUp, true);
    this.window?.addEventListener?.("pointerup", this._onMouseUp, true);
  }

  loadTimelineState() {
    this.timeline = loadTimelineState(this.node);
    return this.timeline;
  }

  updateTimeline(mutator, reason, options = {}) {
    const previousState = deepClone(this.timeline);
    mutator(this.timeline);
    return this.commitTimelineChange(reason, { ...options, previousState });
  }

  commitTimelineChange(reason, options = {}) {
    const previousState = options.previousState ?? deepClone(this.timeline);
    this.timeline = normalizeVideoTimeline(this.timeline);
    applyVisibleNodeProperties(this.timeline, this.node);
    this.timeline.validation = validateVideoTimeline(this.timeline);
    this.timeline.ui_state.state_revision = Number(this.timeline.ui_state.state_revision ?? 0) + 1;

    if (options.pushUndo !== false && serializeTimeline(previousState) !== serializeTimeline(this.timeline)) {
      this.undo.push(previousState);
    }

    writeTimelineWidget(this.node, this.timeline);
    if (options.markDirty !== false) markGraphDirty(this.node, this.app);
    if (options.rerender !== false) this.requestRender();
    this.refreshAsyncMediaCaches(reason, options);
    return this.timeline;
  }

  scheduleDebouncedCommit(reason = "prompt typing", options = {}) {
    if (this.pendingDebounce) this.window?.clearTimeout?.(this.pendingDebounce);
    const setTimer = this.window?.setTimeout ?? globalThis.setTimeout;
    this.pendingDebounce = setTimer(() => {
      this.pendingDebounce = null;
      this.commitTimelineChange(reason, options);
    }, options.delayMs ?? this.debounceMs);
  }

  flushDebouncedCommit(reason = "prompt typing") {
    if (!this.pendingDebounce) return null;
    const clearTimer = this.window?.clearTimeout ?? globalThis.clearTimeout;
    clearTimer(this.pendingDebounce);
    this.pendingDebounce = null;
    return this.commitTimelineChange(reason);
  }

  beginTimelineGesture() {
    if (!this.gestureStartState) {
      this.gestureStartState = deepClone(this.timeline);
    }
  }

  endTimelineGesture(reason = "drag end") {
    if (!this.gestureStartState) return null;
    const previousState = this.gestureStartState;
    this.gestureStartState = null;
    return this.commitTimelineChange(reason, { previousState });
  }

  undoTimelineChange() {
    const previous = this.undo.undo(this.timeline);
    if (!previous) return false;
    this.timeline = previous;
    this.commitTimelineChange("undo", { pushUndo: false });
    return true;
  }

  redoTimelineChange() {
    const next = this.undo.redo(this.timeline);
    if (!next) return false;
    this.timeline = next;
    this.commitTimelineChange("redo", { pushUndo: false });
    return true;
  }

  handleKeyDown(event) {
    if (!isUndoRedoEvent(event) || isTextInputEvent(event) || !isNodeActive(this.node, this.app)) {
      return;
    }
    const didChange = isRedoEvent(event) ? this.redoTimelineChange() : this.undoTimelineChange();
    if (didChange) {
      event.preventDefault?.();
      event.stopPropagation?.();
    }
  }

  requestRender() {
    this.node?._timelineRenderer?.render?.(this.timeline);
  }

  refreshAsyncMediaCaches() {
    this.node?._timelineMediaCache?.refresh?.(this.timeline);
  }
}

export function mountTimelineState(node, app, options = {}) {
  if (node._videoTimelineStateController) return node._videoTimelineStateController;
  const controller = new TimelineStateController(node, app, options);
  node._videoTimelineStateController = controller;
  node.loadTimelineState = () => controller.loadTimelineState();
  node.commitTimelineChange = (reason, commitOptions) => controller.commitTimelineChange(reason, commitOptions);
  node.updateTimelineState = (mutator, reason, commitOptions) => controller.updateTimeline(mutator, reason, commitOptions);
  node.scheduleDebouncedTimelineCommit = (reason, commitOptions) => controller.scheduleDebouncedCommit(reason, commitOptions);
  node.flushDebouncedTimelineCommit = (reason) => controller.flushDebouncedCommit(reason);
  node.beginTimelineGesture = () => controller.beginTimelineGesture();
  node.endTimelineGesture = (reason) => controller.endTimelineGesture(reason);
  node.undoTimelineChange = () => controller.undoTimelineChange();
  node.redoTimelineChange = () => controller.redoTimelineChange();
  return controller;
}

export function unmountTimelineState(node) {
  node?._videoTimelineStateController?.destroy();
  delete node._videoTimelineStateController;
}

export function loadTimelineState(node) {
  const widget = findWidget(node, VIDEO_TIMELINE_WIDGET);
  const timeline = normalizeVideoTimeline(widget?.value ?? "");
  applyVisibleNodeProperties(timeline, node);
  timeline.validation = validateVideoTimeline(timeline);
  return timeline;
}

export function writeTimelineWidget(node, timeline) {
  const widget = findWidget(node, VIDEO_TIMELINE_WIDGET);
  if (widget) widget.value = serializeTimeline(timeline);
  return widget;
}

export function serializeTimeline(timeline) {
  return JSON.stringify(timeline);
}

export function findWidget(node, name) {
  return node?.widgets?.find((widget) => widget.name === name);
}

export function hideWidget(widget) {
  if (!widget) return;
  widget.type = "hidden";
  widget.hidden = true;
  widget.computeSize = () => [0, -4];
}

export function applyVisibleNodeProperties(timeline, node) {
  const project = timeline.project ??= {};
  const duration = getWidgetNumber(node, "duration_seconds");
  const frameRate = getWidgetNumber(node, "frame_rate");
  if (duration != null) project.duration_seconds = duration;
  if (frameRate != null) project.frame_rate = frameRate;
  project.aspect_ratio = getWidgetValue(node, "aspect_ratio", project.aspect_ratio);
  project.orientation = getWidgetValue(node, "orientation", project.orientation);
  project.quality_preset = getWidgetValue(node, "quality_preset", project.quality_preset);
  normalizeTimelineViewRange(timeline);
  return timeline;
}

export function markGraphDirty(node, app) {
  node?.graph?.setDirtyCanvas?.(true, true);
  app?.graph?.setDirtyCanvas?.(true, true);
  app?.canvas?.setDirty?.(true, true);
}

function getWidgetValue(node, name, fallback = undefined) {
  const widget = findWidget(node, name);
  return widget?.value ?? fallback;
}

function getWidgetNumber(node, name) {
  const value = Number(getWidgetValue(node, name));
  return Number.isFinite(value) ? value : null;
}

function isUndoRedoEvent(event) {
  const key = String(event.key ?? "").toLowerCase();
  return (event.ctrlKey || event.metaKey) && (key === "z" || key === "y");
}

function isRedoEvent(event) {
  const key = String(event.key ?? "").toLowerCase();
  return (event.ctrlKey || event.metaKey) && (key === "y" || (key === "z" && event.shiftKey));
}

function isTextInputEvent(event) {
  const target = event.target;
  const tagName = String(target?.tagName ?? "").toLowerCase();
  return Boolean(target?.isContentEditable || tagName === "input" || tagName === "textarea" || tagName === "select");
}

function isNodeActive(node, app) {
  const canvas = app?.canvas;
  return Boolean(
    node?.selected ||
    node?.is_selected ||
    canvas?.current_node === node ||
    canvas?.selected_nodes?.[node.id],
  );
}
