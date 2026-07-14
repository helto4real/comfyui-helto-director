import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { mountTimelineMediaCache, unmountTimelineMediaCache } from "./timeline/media_cache.js";
import { mountTimelineState, unmountTimelineState } from "./timeline/state.js";
import { mountTimelineRenderer, unmountTimelineRenderer } from "./timeline/renderer.js";
import {
  installTakeCapturePreview,
  installTakeCaptureResultListener,
} from "./timeline/take_capture_preview.js";
import { createHtdNodeThemeLifecycle } from "./timeline/node_theme_extension.js";
import {
  bindDirectorManagedPrivacy,
  directorManagedPrivacy,
} from "./timeline/managed_connector.js";

const HELTO_DIRECTOR_THEME_NODE_TYPES = new Set([
  "HeltoVideoTimelineDirector",
  "Video Timeline Director",
  "HeltoTimelineLoraConfiguration",
  "Timeline LoRA Configuration",
  "HeltoTimelineTakeCapture",
  "Timeline Take Capture",
  "HeltoTimelineSequenceAssembler",
  "Timeline Sequence Assembler",
]);

const directorThemeLifecycle = createHtdNodeThemeLifecycle({
  appRef: app,
  nodeTypes: HELTO_DIRECTOR_THEME_NODE_TYPES,
  patchKey: "director",
});

app.registerExtension({
  name: "helto.videoTimelineDirector.state",

  setup() {
    // Director-side safety net: apply take capture results from the global
    // execution event stream even if the capture node's own hook misses them.
    installTakeCaptureResultListener(app, api);
    directorThemeLifecycle.setup();
    directorManagedPrivacy().catch(() => {});
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    directorThemeLifecycle.beforeRegisterNodeDef(nodeType, nodeData);
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
      bindManagedPrivacyOrBlock(this, controller);
      return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = onConfigure?.apply(this, arguments);
      const controller = mountTimelineState(this, app);
      mountTimelineMediaCache(this, app);
      mountTimelineRenderer(this, app, controller);
      bindManagedPrivacyOrBlock(this, controller);
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
    directorThemeLifecycle.nodeCreated(node);
  },

  loadedGraphNode(node) {
    directorThemeLifecycle.loadedGraphNode(node);
  },
});

function bindManagedPrivacyOrBlock(node, controller) {
  bindDirectorManagedPrivacy(node, controller).catch(() => {
    controller.blockManagedPrivacy?.();
  });
}

function getNodeWidth(node) {
  const width = Number(node?.size?.[0] ?? 0);
  return Number.isFinite(width) ? width : 0;
}

function getNodeHeight(node) {
  const height = Number(node?.size?.[1] ?? 0);
  return Number.isFinite(height) ? height : 0;
}
