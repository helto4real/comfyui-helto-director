import { app } from "../../scripts/app.js";
import { mountTimelineState, unmountTimelineState } from "./timeline/state.js";

app.registerExtension({
  name: "helto.videoTimelineDirector.state",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "HeltoVideoTimelineDirector") return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      mountTimelineState(this, app);
      return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = onConfigure?.apply(this, arguments);
      const controller = mountTimelineState(this, app);
      controller.loadTimelineState();
      controller.commitTimelineChange("workflow load", { pushUndo: false, markDirty: false });
      return result;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      unmountTimelineState(this);
      return onRemoved?.apply(this, arguments);
    };
  },
});
