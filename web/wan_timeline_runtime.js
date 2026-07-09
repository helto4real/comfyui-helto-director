import { app } from "../../scripts/app.js";
import { applyHtdNodeTheme } from "./timeline/design_tokens.js";

const WAN_SEGMENTED_EXECUTOR = "HeltoWAN22TimelineSegmentedExecutor";

const HELTO_WAN_THEME_NODE_TYPES = new Set([
  "HeltoWAN22TimelineConfig",
  "WAN 2.2 Timeline Config",
  "HeltoWAN22TimelinePlanner",
  "WAN 2.2 Timeline Planner",
  "HeltoWAN22TimelineRuntime",
  "WAN 2.2 Timeline Runtime",
  WAN_SEGMENTED_EXECUTOR,
  "WAN 2.2 Timeline Segmented Executor",
]);

app.registerExtension({
  name: "helto.wanTimelineRuntime",

  setup() {
    requestAnimationFrame(() => {
      for (const node of app.graph?._nodes || []) {
        applyWanNodeTheme(node);
      }
    });
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (isWanThemeNodeData(nodeData)) {
      patchWanThemeNodeType(nodeType);
    }
    if (nodeData?.name === WAN_SEGMENTED_EXECUTOR) {
      installWanSegmentedExecutorSplitStepSync(nodeType);
    }
  },

  nodeCreated(node) {
    applyWanNodeTheme(node);
  },

  loadedGraphNode(node) {
    applyWanNodeTheme(node);
  },
});

function installWanSegmentedExecutorSplitStepSync(nodeType) {
  if (nodeType.prototype.__heltoWanSplitStepSyncPatched) {
    return;
  }
  nodeType.prototype.__heltoWanSplitStepSyncPatched = true;

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

function isWanThemeNodeData(nodeData) {
  return (
    HELTO_WAN_THEME_NODE_TYPES.has(String(nodeData?.name || "")) ||
    HELTO_WAN_THEME_NODE_TYPES.has(String(nodeData?.display_name || ""))
  );
}

function isWanThemeNode(node) {
  return [
    node?.type,
    node?.comfyClass,
    node?.class_type,
    node?.constructor?.type,
    node?.constructor?.comfyClass,
    node?.title,
  ]
    .map((value) => String(value || ""))
    .filter(Boolean)
    .some((candidate) => HELTO_WAN_THEME_NODE_TYPES.has(candidate));
}

function applyWanNodeTheme(node) {
  if (!isWanThemeNode(node)) {
    return false;
  }
  return applyHtdNodeTheme(node, { appRef: app });
}

function patchWanThemeNodeType(nodeType) {
  if (nodeType.prototype.__heltoWanNodeThemePatched) {
    return;
  }
  nodeType.prototype.__heltoWanNodeThemePatched = true;

  const originalCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = originalCreated?.apply(this, arguments);
    applyWanNodeTheme(this);
    return result;
  };

  const originalConfigure = nodeType.prototype.configure;
  nodeType.prototype.configure = function () {
    const result = originalConfigure?.apply(this, arguments);
    applyWanNodeTheme(this);
    return result;
  };

  const originalOnConfigure = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function () {
    const result = originalOnConfigure?.apply(this, arguments);
    applyWanNodeTheme(this);
    return result;
  };
}

function findWidget(node, name) {
  return node?.widgets?.find((widget) => widget.name === name);
}
