import { app } from "../../scripts/app.js";
import { mountTimelineMediaCache, unmountTimelineMediaCache } from "./timeline/media_cache.js";
import { mountTimelineState, unmountTimelineState } from "./timeline/state.js";
import { mountTimelineRenderer, unmountTimelineRenderer } from "./timeline/renderer.js";

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
      this._timelineDirectorLastNodeHeight = getNodeHeight(this);
      this._timelineRenderer?.handleNodeResize?.();
      return result;
    };

    const onDrawForeground = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function () {
      const result = onDrawForeground?.apply(this, arguments);
      const nodeWidth = getNodeWidth(this);
      const nodeHeight = getNodeHeight(this);
      if (
        (nodeWidth > 0 && Math.abs(nodeWidth - Number(this._timelineDirectorLastNodeWidth ?? 0)) >= 1) ||
        (nodeHeight > 0 && Math.abs(nodeHeight - Number(this._timelineDirectorLastNodeHeight ?? 0)) >= 1)
      ) {
        this._timelineDirectorLastNodeWidth = nodeWidth;
        this._timelineDirectorLastNodeHeight = nodeHeight;
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

function getNodeHeight(node) {
  const height = Number(node?.size?.[1] ?? 0);
  return Number.isFinite(height) ? height : 0;
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
