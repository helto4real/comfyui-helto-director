import { app } from "../../scripts/app.js";
import { createHtdNodeThemeLifecycle } from "./timeline/node_theme_extension.js";

const HELTO_LTX_THEME_NODE_TYPES = new Set([
  "HeltoLTX23TimelineConfig",
  "LTX 2.3 Timeline Config",
  "HeltoLTX23TimelinePlanner",
  "LTX 2.3 Timeline Planner",
  "HeltoLTX23TimelineRuntime",
  "LTX 2.3 Timeline Runtime",
  "HeltoLTX23TimelineSegmentedExecutor",
  "LTX 2.3 Timeline Segmented Executor",
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

const ltxThemeLifecycle = createHtdNodeThemeLifecycle({
  appRef: app,
  nodeTypes: HELTO_LTX_THEME_NODE_TYPES,
  patchKey: "ltx",
});

app.registerExtension({
  name: "helto.ltxTimelineTheme",
  ...ltxThemeLifecycle,
});
