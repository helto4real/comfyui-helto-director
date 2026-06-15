import { app } from "../../scripts/app.js";
import { mountTimelineMediaCache, unmountTimelineMediaCache } from "./timeline/media_cache.js";
import { mountTimelineState, unmountTimelineState } from "./timeline/state.js";
import { mountTimelineRenderer, unmountTimelineRenderer } from "./timeline/renderer.js";

app.registerExtension({
  name: "helto.videoTimelineDirector.state",

  async beforeRegisterNodeDef(nodeType, nodeData) {
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
