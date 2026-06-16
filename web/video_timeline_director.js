import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { mountTimelineMediaCache, unmountTimelineMediaCache } from "./timeline/media_cache.js";
import { mountTimelineState, unmountTimelineState } from "./timeline/state.js";
import { mountTimelineRenderer, unmountTimelineRenderer } from "./timeline/renderer.js";

const TIMELINE_STATUS_EVENT = "helto_timeline_status";
const TIMELINE_STATUS_STALE_MS = 45000;
const TIMELINE_STATUS_DONE_CLEAR_MS = 1500;

installTimelineStatusBarBridge(api);

app.registerExtension({
  name: "helto.videoTimelineDirector.state",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name === "HeltoWAN22TimelineSegmentedExecutor") {
      installWanSegmentedExecutorSplitStepSync(nodeType);
      return;
    }
    if (nodeData?.name !== "HeltoVideoTimelineDirector") return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      const controller = mountTimelineState(this, app);
      mountTimelineMediaCache(this, app);
      mountTimelineRenderer(this, app, controller);
      return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = onConfigure?.apply(this, arguments);
      const controller = mountTimelineState(this, app);
      mountTimelineMediaCache(this, app);
      mountTimelineRenderer(this, app, controller);
      controller.loadTimelineState();
      if (!controller.hasEncryptedTimelineWidget?.()) {
        controller.commitTimelineChange("workflow load", { pushUndo: false, markDirty: false });
      }
      return result;
    };

    const serialize = nodeType.prototype.serialize;
    if (typeof serialize === "function") {
      nodeType.prototype.serialize = function () {
        this.flushTimelineBeforeSerialization?.();
        return serialize.apply(this, arguments);
      };
    }

    const onResize = nodeType.prototype.onResize;
    nodeType.prototype.onResize = function () {
      const result = onResize?.apply(this, arguments);
      this._timelineDirectorLastNodeWidth = getNodeWidth(this);
      this._timelineRenderer?.handleNodeResize?.();
      return result;
    };

    const onDrawForeground = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function () {
      const result = onDrawForeground?.apply(this, arguments);
      const nodeWidth = getNodeWidth(this);
      if (nodeWidth > 0 && Math.abs(nodeWidth - Number(this._timelineDirectorLastNodeWidth ?? 0)) >= 1) {
        this._timelineDirectorLastNodeWidth = nodeWidth;
        this._timelineRenderer?.handleNodeResize?.();
      }
      return result;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      unmountTimelineRenderer(this);
      unmountTimelineMediaCache(this);
      unmountTimelineState(this);
      return onRemoved?.apply(this, arguments);
    };
  },
});

function getNodeWidth(node) {
  const width = Number(node?.size?.[0] ?? 0);
  return Number.isFinite(width) ? width : 0;
}

function installWanSegmentedExecutorSplitStepSync(nodeType) {
  const onNodeCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = onNodeCreated?.apply(this, arguments);
    mountWanSplitStepSync(this);
    return result;
  };

  const onConfigure = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function () {
    const result = onConfigure?.apply(this, arguments);
    mountWanSplitStepSync(this);
    syncWanPhaseSplitStep(this, { markCanvas: false });
    return result;
  };
}

function mountWanSplitStepSync(node) {
  const stepsWidget = findWidget(node, "steps");
  if (!stepsWidget || stepsWidget._heltoWanSplitStepWrapped) return;
  const originalCallback = stepsWidget.callback;
  stepsWidget.callback = function () {
    const result = originalCallback?.apply(this, arguments);
    syncWanPhaseSplitStep(node, { markCanvas: true });
    return result;
  };
  stepsWidget._heltoWanSplitStepWrapped = true;
  syncWanPhaseSplitStep(node, { markCanvas: false });
}

function syncWanPhaseSplitStep(node, { markCanvas = false } = {}) {
  const stepsWidget = findWidget(node, "steps");
  const splitWidget = findWidget(node, "phase_split_step");
  if (!stepsWidget || !splitWidget) return;
  const steps = Number(stepsWidget.value);
  const split = Math.max(1, Math.floor(Number.isFinite(steps) ? steps / 2 : 10));
  if (splitWidget.value === split) return;
  splitWidget.value = split;
  splitWidget.callback?.(split);
  if (markCanvas) app.graph?.setDirtyCanvas?.(true, true);
}

function findWidget(node, name) {
  return node?.widgets?.find((widget) => widget.name === name);
}

function installTimelineStatusBarBridge(apiRef) {
  if (!apiRef || apiRef._heltoTimelineStatusBarBridgeInstalled) return;
  apiRef._heltoTimelineStatusBarBridgeInstalled = true;

  const state = {
    activeNodeId: null,
    latestByNodeId: new Map(),
    clearTimer: null,
    staleTimer: null,
  };

  apiRef.addEventListener?.(TIMELINE_STATUS_EVENT, (event) => {
    const payload = normalizeTimelineStatusPayload(event?.detail ?? event?.data ?? event);
    if (!payload) return;
    state.activeNodeId = payload.node_id;
    state.latestByNodeId.set(payload.node_id, payload);
    showTimelineStatusLabel(state, payload);
    scheduleTimelineStatusStaleClear(state, payload.node_id);
    if (payload.stage === "timeline.done") {
      scheduleTimelineStatusClear(state, TIMELINE_STATUS_DONE_CLEAR_MS);
    }
  });

  apiRef.addEventListener?.("executing", (event) => {
    const nodeId = normalizeTimelineStatusNodeId(event?.detail?.node ?? event?.data?.node ?? event?.node);
    if (!nodeId) {
      clearTimelineStatusLabel(state);
      return;
    }
    state.activeNodeId = nodeId;
    const latest = state.latestByNodeId.get(nodeId);
    if (latest) {
      showTimelineStatusLabel(state, latest);
    } else {
      clearTimelineStatusLabel(state);
    }
  });
}

function normalizeTimelineStatusPayload(payload) {
  if (!payload || typeof payload !== "object") return null;
  const nodeId = normalizeTimelineStatusNodeId(payload.node_id);
  const label = typeof payload.label === "string" ? payload.label.trim() : "";
  if (!nodeId || !label) return null;
  return {
    node_id: nodeId,
    stage: typeof payload.stage === "string" ? payload.stage : "",
    label,
    current: Number(payload.current) || 0,
    total: Number(payload.total) || 0,
    model: typeof payload.model === "string" ? payload.model : "",
    segment_index: payload.segment_index,
    segment_count: payload.segment_count,
    frame_count: payload.frame_count,
    encrypted_spill: payload.encrypted_spill === true,
  };
}

function normalizeTimelineStatusNodeId(nodeId) {
  if (nodeId === null || nodeId === undefined || nodeId === false) return "";
  return String(nodeId);
}

function showTimelineStatusLabel(state, payload) {
  clearTimeout(state.clearTimer);
  const overlay = getTimelineStatusOverlay();
  if (!overlay) return;
  overlay.textContent = payload.label;
  overlay.dataset.stage = payload.stage;
  overlay.dataset.nodeId = payload.node_id;
  overlay.classList.add("is-visible");
}

function scheduleTimelineStatusStaleClear(state, nodeId) {
  clearTimeout(state.staleTimer);
  state.staleTimer = setTimeout(() => {
    if (state.activeNodeId === nodeId) clearTimelineStatusLabel(state);
  }, TIMELINE_STATUS_STALE_MS);
}

function scheduleTimelineStatusClear(state, delayMs) {
  clearTimeout(state.clearTimer);
  state.clearTimer = setTimeout(() => clearTimelineStatusLabel(state), delayMs);
}

function clearTimelineStatusLabel(state) {
  clearTimeout(state.clearTimer);
  clearTimeout(state.staleTimer);
  const overlay = getTimelineStatusOverlay({ create: false });
  if (overlay) {
    overlay.classList.remove("is-visible");
    overlay.textContent = "";
    delete overlay.dataset.stage;
    delete overlay.dataset.nodeId;
  }
  state.activeNodeId = null;
}

function getTimelineStatusOverlay({ create = true } = {}) {
  const documentRef = globalThis.document;
  if (!documentRef) return null;
  let overlay = documentRef.querySelector(".helto-timeline-status-bar-bridge");
  if (overlay || !create) return overlay;
  ensureTimelineStatusStyles(documentRef);
  overlay = documentRef.createElement("div");
  overlay.className = "helto-timeline-status-bar-bridge";
  overlay.setAttribute("role", "status");
  overlay.setAttribute("aria-live", "polite");
  documentRef.body?.append(overlay);
  return overlay;
}

function ensureTimelineStatusStyles(documentRef) {
  if (documentRef.getElementById("helto-timeline-status-bar-bridge-styles")) return;
  const style = documentRef.createElement("style");
  style.id = "helto-timeline-status-bar-bridge-styles";
  style.textContent = `
    .helto-timeline-status-bar-bridge {
      position: fixed;
      top: 2px;
      left: 8px;
      z-index: 100000;
      max-width: min(900px, calc(100vw - 16px));
      min-height: 18px;
      padding: 1px 8px;
      box-sizing: border-box;
      border-radius: 3px;
      background: rgba(0, 0, 0, 0.42);
      color: #fff;
      font: 700 13px/18px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      opacity: 0;
      overflow: hidden;
      pointer-events: none;
      text-overflow: ellipsis;
      text-shadow: 0 1px 2px rgba(0, 0, 0, 0.65);
      transition: opacity 120ms ease;
      white-space: nowrap;
    }
    .helto-timeline-status-bar-bridge.is-visible {
      opacity: 1;
    }
  `;
  documentRef.head?.append(style);
}
