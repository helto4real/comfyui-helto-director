import { deepClone } from "./schema.js";
import { normalizeVideoTimeline } from "./migration.js";
import { normalizeTimelineViewRange } from "./geometry.js";
import { deleteSelectedItem } from "./operations.js";
import { validateVideoTimeline } from "./validation.js";
import { TimelineUndoStack } from "./undo.js";
import {
  fetchGlobalSettings,
  isGlobalPrivacyMode,
  normalizeGlobalSettings,
  saveGlobalSettings,
} from "./global_settings.js";
import {
  confirmUnreadablePrivacyReset,
  encryptTimelineSync,
  fetchPrivacyJson,
  isEncryptedPrivacyPayload,
  isPrivacyKeyUnavailableError,
  isPrivacyLockedError,
  isUnreadablePrivacyValueError,
  parsePrivacyPayload,
} from "./privacy.js";
import {
  isPrivacyKeystoreDialogOpen,
  showPrivacyKeystoreDialog,
} from "./privacy_unlock.js";

export const VIDEO_TIMELINE_WIDGET = "video_timeline_json";

export class TimelineStateController {
  constructor(node, app, options = {}) {
    this.node = node;
    this.app = app;
    this.window = options.window ?? globalThis.window;
    this.debounceMs = options.debounceMs ?? 300;
    this.autoLoadGlobalSettings = options.loadGlobalSettings !== false;
    this.undo = new TimelineUndoStack(options.undoLimit ?? 100);
    this.hiddenWidget = findWidget(node, VIDEO_TIMELINE_WIDGET);
    this.globalSettings = normalizeGlobalSettings(options.globalSettings);
    this.globalSettingsError = "";
    this.timeline = loadTimelineState(node);
    this.pendingDebounce = null;
    this.gestureStartState = null;
    this.timelineKeyboardScope = null;
    this.destroyed = false;
    this.privacyError = "";
    this.decryptingPrivacy = false;
    this.confirmUnreadablePrivacyReset = options.confirmUnreadablePrivacyReset ?? confirmUnreadablePrivacyReset;
    this.resetPrivacyToPlaintext = false;
    this._onKeyDown = (event) => this.handleKeyDown(event);
    this._onMouseUp = () => this.endTimelineGesture("drag end");

    hideWidget(this.hiddenWidget);
    this.installEventListeners();
    if (this.autoLoadGlobalSettings) this.loadGlobalSettings();
    if (this.hasEncryptedTimelineWidget()) {
      this.decryptTimelineWidget();
    } else {
      this.commitTimelineChange("mount", { pushUndo: false, markDirty: false });
    }
  }

  destroy() {
    this.flushDebouncedCommit();
    this.destroyed = true;
    this.timelineKeyboardScope = null;
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
    if (this.hasEncryptedTimelineWidget()) {
      this.decryptTimelineWidget();
      return this.timeline;
    }
    this.timeline = loadTimelineState(this.node);
    return this.timeline;
  }

  hasEncryptedTimelineWidget() {
    return isEncryptedPrivacyPayload(this.hiddenWidget?.value);
  }

  async decryptTimelineWidget() {
    if (this.decryptingPrivacy || !this.hasEncryptedTimelineWidget()) return false;
    const payload = parsePrivacyPayload(this.hiddenWidget?.value);
    this.decryptingPrivacy = true;
    this.privacyError = "Decrypting private timeline data...";
    this.requestRender();
    try {
      const result = await fetchPrivacyJson("decrypt", { payload });
      const state = result.state || {};
      this.timeline = normalizeVideoTimeline(state.timeline || state || {});
      applyVisibleNodeProperties(this.timeline, this.node);
      this.timeline.validation = validateVideoTimeline(this.timeline, this.globalSettings);
      this.privacyError = "";
      this.requestRender();
      this.refreshAsyncMediaCaches("privacy decrypt", {});
      return true;
    } catch (error) {
      if (await isUnreadablePrivacyValueError(error)) {
        if (await this.confirmUnreadablePrivacyReset()) {
          await this.resetUnreadableTimeline(error);
        } else {
          this.preserveUnreadableTimeline();
        }
        return false;
      }
      this.timeline = normalizeVideoTimeline("");
      this.privacyError = `Private timeline locked: ${error.message}`;
      console.error("Helto Director privacy decrypt failed", error);
      this.requestRender();
      if (isPrivacyLockedError(error)) this.promptPrivacyUnlock();
      return false;
    } finally {
      this.decryptingPrivacy = false;
    }
  }

  preserveUnreadableTimeline() {
    this.timeline = normalizeVideoTimeline("");
    applyVisibleNodeProperties(this.timeline, this.node);
    this.timeline.validation = validateVideoTimeline(this.timeline, this.globalSettings);
    this.privacyError = "Private timeline could not be decrypted. The encrypted value was preserved.";
    this.requestRender();
    this.refreshAsyncMediaCaches("privacy preserve", {});
  }

  async resetUnreadableTimeline(error) {
    const disablePrivacy = await isPrivacyKeyUnavailableError(error);
    this.timeline = normalizeVideoTimeline("");
    applyVisibleNodeProperties(this.timeline, this.node);
    this.timeline.validation = validateVideoTimeline(this.timeline, this.globalSettings);
    this.resetPrivacyToPlaintext = disablePrivacy;

    if (disablePrivacy) {
      const settings = normalizeGlobalSettings(this.globalSettings);
      settings.privacy.mode = false;
      this.globalSettings = settings;
      try {
        this.globalSettings = await saveGlobalSettings(settings);
        this.globalSettingsError = "";
      } catch (settingsError) {
        this.globalSettingsError = `Global privacy could not be disabled after reset: ${settingsError.message}`;
      }
    }

    try {
      writeTimelineWidget(this.node, this.timeline, this.globalSettings);
      this.privacyError = "Unreadable private timeline was reset to defaults.";
    } catch (writeError) {
      this.privacyError = `Private timeline reset could not be saved: ${writeError.message}`;
    }
    markGraphDirty(this.node, this.app);
    this.requestRender();
    this.refreshAsyncMediaCaches("privacy reset", {});
  }

  async promptPrivacyUnlock(onUnlocked = null) {
    const documentRef = globalThis.document;
    if (!documentRef?.body || isPrivacyKeystoreDialogOpen(documentRef)) return false;
    const unlocked = await showPrivacyKeystoreDialog("unlock", { documentRef });
    if (!unlocked) return false;
    this.privacyError = "";
    if (onUnlocked) {
      try {
        return await onUnlocked();
      } catch (error) {
        this.privacyError = `Privacy encryption failed: ${error.message}`;
        this.requestRender();
        return false;
      }
    }
    return this.decryptTimelineWidget();
  }

  retryPrivateWidgetWrite() {
    writeTimelineWidget(this.node, this.timeline, this.globalSettings);
    this.privacyError = "";
    markGraphDirty(this.node, this.app);
    this.requestRender();
    return true;
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
    this.timeline.validation = validateVideoTimeline(this.timeline, this.globalSettings);
    this.timeline.ui_state.state_revision = Number(this.timeline.ui_state.state_revision ?? 0) + 1;

    if (options.pushUndo !== false && serializeTimeline(previousState) !== serializeTimeline(this.timeline)) {
      this.undo.push(previousState);
    }

    try {
      writeTimelineWidget(this.node, this.timeline, this.globalSettings);
      if (!this.decryptingPrivacy) this.privacyError = "";
    } catch (error) {
      this.privacyError = `Privacy encryption failed: ${error.message}`;
      this.requestRender();
      if (isPrivacyLockedError(error)) this.promptPrivacyUnlock(() => this.retryPrivateWidgetWrite());
      throw error;
    }
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

  flushDebouncedCommit(reason = "prompt typing", options = {}) {
    if (!this.pendingDebounce) return null;
    const clearTimer = this.window?.clearTimeout ?? globalThis.clearTimeout;
    clearTimer(this.pendingDebounce);
    this.pendingDebounce = null;
    return this.commitTimelineChange(reason, options);
  }

  flushTimelineBeforeSerialization() {
    if (this.pendingDebounce) {
      return this.flushDebouncedCommit("prompt typing", {
        markDirty: false,
        rerender: false,
      });
    }
    return this.commitTimelineChange("serialize timeline", {
      pushUndo: false,
      markDirty: false,
      rerender: false,
    });
  }

  replaceTimeline(nextTimeline, reason = "replace timeline", options = {}) {
    this.flushDebouncedCommit(options.flushReason ?? "timeline replace flush", {
      markDirty: false,
      rerender: false,
    });
    const previousState = deepClone(this.timeline);
    this.timeline = normalizeVideoTimeline(nextTimeline);
    writeVisibleNodeProperties(this.timeline, this.node);
    return this.commitTimelineChange(reason, { previousState });
  }

  replaceTimelineFromLibrary(nextTimeline, reason = "replace timeline from library") {
    return this.replaceTimeline(nextTimeline, reason, { flushReason: "library replace flush" });
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

  deleteSelectedTimelineItem() {
    if (!this.timeline?.ui_state?.selected_item_id) return false;
    const previousState = deepClone(this.timeline);
    if (!deleteSelectedItem(this.timeline)) {
      this.timeline = previousState;
      return false;
    }
    this.commitTimelineChange("delete", { previousState });
    return true;
  }

  setTimelineKeyboardScope(element) {
    this.timelineKeyboardScope = element ?? null;
  }

  handleKeyDown(event) {
    if (isInteractiveKeyTarget(event)) {
      return;
    }
    if (!isNodeActive(this.node, this.app) && !isTimelineItemShortcutScope(this.timelineKeyboardScope, event)) {
      return;
    }
    let didChange = false;
    if (isDeleteEvent(event)) {
      didChange = this.deleteSelectedTimelineItem();
    } else if (isUndoRedoEvent(event)) {
      didChange = isRedoEvent(event) ? this.redoTimelineChange() : this.undoTimelineChange();
    }
    if (didChange) {
      event.preventDefault?.();
      event.stopPropagation?.();
    }
  }

  requestRender() {
    this.node?._timelineRenderer?.render?.(this.timeline);
  }

  refreshAsyncMediaCaches() {
    this.node?._timelineMediaCache?.refresh?.(this.timeline, this.globalSettings);
  }

  async loadGlobalSettings() {
    try {
      this.globalSettings = await fetchGlobalSettings();
      if (this.resetPrivacyToPlaintext && isGlobalPrivacyMode(this.globalSettings)) {
        const settings = normalizeGlobalSettings(this.globalSettings);
        settings.privacy.mode = false;
        this.globalSettings = await saveGlobalSettings(settings);
      }
      this.globalSettingsError = "";
      this.timeline.validation = validateVideoTimeline(this.timeline, this.globalSettings);
      if (!this.hasEncryptedTimelineWidget() && !this.decryptingPrivacy) {
        try {
          writeTimelineWidget(this.node, this.timeline, this.globalSettings);
        } catch (error) {
          this.privacyError = `Privacy encryption failed: ${error.message}`;
          if (isPrivacyLockedError(error)) this.promptPrivacyUnlock(() => this.retryPrivateWidgetWrite());
        }
      }
      this.requestRender();
      this.refreshAsyncMediaCaches("global settings", {});
      return this.globalSettings;
    } catch (error) {
      this.globalSettingsError = `Global settings unavailable: ${error.message}`;
      console.warn("Helto Director global settings failed", error);
      this.requestRender();
      return this.globalSettings;
    }
  }

  async updateGlobalSettings(mutator) {
    const previousPrivacy = isGlobalPrivacyMode(this.globalSettings);
    const next = normalizeGlobalSettings(this.globalSettings);
    mutator(next);
    this.globalSettings = await saveGlobalSettings(next);
    this.globalSettingsError = "";
    this.timeline.validation = validateVideoTimeline(this.timeline, this.globalSettings);
    if (previousPrivacy !== isGlobalPrivacyMode(this.globalSettings)) {
      writeTimelineWidget(this.node, this.timeline, this.globalSettings);
    }
    this.requestRender();
    this.refreshAsyncMediaCaches("global settings", {});
    return this.globalSettings;
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
  node.flushDebouncedTimelineCommit = (reason, commitOptions) => controller.flushDebouncedCommit(reason, commitOptions);
  node.flushTimelineBeforeSerialization = () => controller.flushTimelineBeforeSerialization();
  node.replaceTimeline = (nextTimeline, reason) => controller.replaceTimeline(nextTimeline, reason);
  node.replaceTimelineFromLibrary = (nextTimeline, reason) => controller.replaceTimelineFromLibrary(nextTimeline, reason);
  node.beginTimelineGesture = () => controller.beginTimelineGesture();
  node.endTimelineGesture = (reason) => controller.endTimelineGesture(reason);
  node.undoTimelineChange = () => controller.undoTimelineChange();
  node.redoTimelineChange = () => controller.redoTimelineChange();
  node.deleteSelectedTimelineItem = () => controller.deleteSelectedTimelineItem();
  node.setTimelineKeyboardScope = (element) => controller.setTimelineKeyboardScope(element);
  node.updateTimelineGlobalSettings = (mutator) => controller.updateGlobalSettings(mutator);
  return controller;
}

export function unmountTimelineState(node) {
  node?._videoTimelineStateController?.destroy();
  delete node._videoTimelineStateController;
}

export function loadTimelineState(node) {
  const widget = findWidget(node, VIDEO_TIMELINE_WIDGET);
  if (isEncryptedPrivacyPayload(widget?.value)) {
    return normalizeVideoTimeline("");
  }
  const timeline = normalizeVideoTimeline(widget?.value ?? "");
  applyVisibleNodeProperties(timeline, node);
  timeline.validation = validateVideoTimeline(timeline);
  return timeline;
}

export function writeTimelineWidget(node, timeline, globalSettings = null) {
  const widget = findWidget(node, VIDEO_TIMELINE_WIDGET);
  if (widget) {
    if (isGlobalPrivacyMode(globalSettings)) {
      const envelope = encryptTimelineSync(timeline);
      widget.value = JSON.stringify(envelope);
    } else {
      widget.value = serializeTimeline(timeline);
    }
  }
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

export function writeVisibleNodeProperties(timeline, node) {
  const project = timeline?.project ?? {};
  setWidgetValue(node, "duration_seconds", project.duration_seconds);
  setWidgetValue(node, "frame_rate", project.frame_rate);
  setWidgetValue(node, "aspect_ratio", project.aspect_ratio);
  setWidgetValue(node, "orientation", project.orientation);
  setWidgetValue(node, "quality_preset", project.quality_preset);
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

function setWidgetValue(node, name, value) {
  if (value == null) return;
  const widget = findWidget(node, name);
  if (widget) widget.value = value;
}

function isUndoRedoEvent(event) {
  const key = String(event.key ?? "").toLowerCase();
  return (event.ctrlKey || event.metaKey) && (key === "z" || key === "y");
}

function isDeleteEvent(event) {
  return event.key === "Delete" || event.key === "Backspace";
}

function isRedoEvent(event) {
  const key = String(event.key ?? "").toLowerCase();
  return (event.ctrlKey || event.metaKey) && (key === "y" || (key === "z" && event.shiftKey));
}

function isInteractiveKeyTarget(event) {
  return eventTargets(event).some((target) => isInteractiveElement(target));
}

function isInteractiveElement(target) {
  const tagName = String(target?.tagName ?? "").toLowerCase();
  if (target?.isContentEditable || tagName === "input" || tagName === "textarea" || tagName === "select" || tagName === "button") {
    return true;
  }
  return Boolean(target?.closest?.(
    "input, textarea, select, button, [contenteditable='true'], [role='button'], [role='menuitem'], .htd-menu, .htd-context-menu, .htd-settings-overlay, .htd-reference-overlay, .htd-lora-editor-dialog, .htd-lora-info-dialog, .htd-library-dialog, .pr-image-browser-dialog, .pr-image-large-preview",
  ));
}

function isTimelineItemShortcutScope(scope, event) {
  if (!scope) return false;
  return eventTargets(event).some((target) => isTimelineItemTarget(scope, target));
}

function isTimelineItemTarget(scope, target) {
  if (!target) return false;
  const item = target.matches?.(".htd-item") ? target : target.closest?.(".htd-item");
  return Boolean(item && (scope === item || scope.contains?.(item)));
}

function eventTargets(event) {
  const targets = [];
  if (event?.target) targets.push(event.target);
  const documentRef = event?.target?.ownerDocument ?? event?.currentTarget?.document ?? globalThis.document;
  const activeElement = documentRef?.activeElement;
  if (activeElement && !targets.includes(activeElement)) targets.push(activeElement);
  return targets;
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
