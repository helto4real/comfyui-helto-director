import { app } from "../../scripts/app.js";
import { createHtdNodeThemeLifecycle } from "./timeline/node_theme_extension.js";

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

const wanThemeLifecycle = createHtdNodeThemeLifecycle({
  appRef: app,
  nodeTypes: HELTO_WAN_THEME_NODE_TYPES,
  patchKey: "wan",
});

app.registerExtension({
  name: "helto.wanTimelineRuntime",

  setup() {
    wanThemeLifecycle.setup();
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    wanThemeLifecycle.beforeRegisterNodeDef(nodeType, nodeData);
    if (nodeData?.name === WAN_SEGMENTED_EXECUTOR) {
      installWanSegmentedExecutorSplitStepSync(nodeType);
    }
  },

  nodeCreated(node) {
    wanThemeLifecycle.nodeCreated(node);
  },

  loadedGraphNode(node) {
    wanThemeLifecycle.loadedGraphNode(node);
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

function findWidget(node, name) {
  return node?.widgets?.find((widget) => widget.name === name);
}
