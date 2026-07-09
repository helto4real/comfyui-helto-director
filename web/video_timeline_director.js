import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { mountTimelineMediaCache, unmountTimelineMediaCache } from "./timeline/media_cache.js";
import { mountTimelineState, unmountTimelineState } from "./timeline/state.js";
import { mountTimelineRenderer, unmountTimelineRenderer } from "./timeline/renderer.js";
import {
  installTakeCapturePreview,
  installTakeCaptureResultListener,
} from "./timeline/take_capture_preview.js";
import { applyHtdNodeTheme } from "./timeline/design_tokens.js";

const HELTO_DIRECTOR_THEME_NODE_TYPES = new Set([
  "HeltoVideoTimelineDirector",
  "Video Timeline Director",
  "HeltoLTX23TimelineConfig",
  "LTX 2.3 Timeline Config",
  "HeltoLTX23TimelinePlanner",
  "LTX 2.3 Timeline Planner",
  "HeltoLTX23TimelineRuntime",
  "LTX 2.3 Timeline Runtime",
  "HeltoLTX23TimelineSegmentedExecutor",
  "LTX 2.3 Timeline Segmented Executor",
  "HeltoTimelineLoraConfiguration",
  "Timeline LoRA Configuration",
  "HeltoTimelineTakeCapture",
  "Timeline Take Capture",
  "HeltoTimelineSequenceAssembler",
  "Timeline Sequence Assembler",
  "HeltoLTX23TimelineCropReferenceTail",
  "LTX 2.3 Timeline Crop Reference Tail",
  "HeltoLTX23TimelineReferenceImageSelector",
  "LTX 2.3 Timeline Reference Image Selector",
  "HeltoLTX23TimelineIdentityAnchorLatentAware",
  "LTX 2.3 Timeline Identity Anchor: Latent Aware",
  "HeltoLTX23TimelineIdentityAnchorFace",
  "LTX 2.3 Timeline Identity Anchor: Face",
  "HeltoLTX23TimelineIdentityAnchorCombine",
  "LTX 2.3 Timeline Identity Anchor: Combine",
  "HeltoLTX23TimelineApplyIdentityAnchor",
  "LTX 2.3 Timeline Apply Identity Anchor",
]);

app.registerExtension({
  name: "helto.videoTimelineDirector.state",

  setup() {
    // Director-side safety net: apply take capture results from the global
    // execution event stream even if the capture node's own hook misses them.
    installTakeCaptureResultListener(app, api);
    requestAnimationFrame(() => {
      for (const node of app.graph?._nodes || []) {
        applyHeltoDirectorNodeTheme(node);
      }
    });
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (isHeltoDirectorThemeNodeData(nodeData)) {
      patchHeltoDirectorThemeNodeType(nodeType);
    }
    if (nodeData?.name === "HeltoTimelineTakeCapture") {
      installTakeCapturePreview(nodeType, app, api);
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

  nodeCreated(node) {
    applyHeltoDirectorNodeTheme(node);
  },

  loadedGraphNode(node) {
    applyHeltoDirectorNodeTheme(node);
  },
});

function heltoDirectorNodeTypeCandidates(node) {
  return [
    node?.type,
    node?.comfyClass,
    node?.class_type,
    node?.constructor?.type,
    node?.constructor?.comfyClass,
    node?.title,
  ].map((value) => String(value || "")).filter(Boolean);
}

function isHeltoDirectorThemeNodeData(nodeData) {
  return (
    HELTO_DIRECTOR_THEME_NODE_TYPES.has(String(nodeData?.name || "")) ||
    HELTO_DIRECTOR_THEME_NODE_TYPES.has(String(nodeData?.display_name || ""))
  );
}

function isHeltoDirectorThemeNode(node) {
  return heltoDirectorNodeTypeCandidates(node).some((candidate) => HELTO_DIRECTOR_THEME_NODE_TYPES.has(candidate));
}

function applyHeltoDirectorNodeTheme(node) {
  if (!isHeltoDirectorThemeNode(node)) {
    return false;
  }
  return applyHtdNodeTheme(node, { appRef: app });
}

function patchHeltoDirectorThemeNodeType(nodeType) {
  if (nodeType.prototype.__heltoDirectorNodeThemePatched) {
    return;
  }
  nodeType.prototype.__heltoDirectorNodeThemePatched = true;

  const originalCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = originalCreated?.apply(this, arguments);
    applyHeltoDirectorNodeTheme(this);
    return result;
  };

  const originalConfigure = nodeType.prototype.configure;
  nodeType.prototype.configure = function () {
    const result = originalConfigure?.apply(this, arguments);
    applyHeltoDirectorNodeTheme(this);
    return result;
  };

  const originalOnConfigure = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function () {
    const result = originalOnConfigure?.apply(this, arguments);
    applyHeltoDirectorNodeTheme(this);
    return result;
  };
}

function getNodeWidth(node) {
  const width = Number(node?.size?.[0] ?? 0);
  return Number.isFinite(width) ? width : 0;
}

function getNodeHeight(node) {
  const height = Number(node?.size?.[1] ?? 0);
  return Number.isFinite(height) ? height : 0;
}
