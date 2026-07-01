import { htdTokenBlock } from "./design_tokens.js";

const PREVIEW_WIDGET_NAME = "helto_take_capture_preview";
const NATIVE_PREVIEW_WIDGET_NAMES = new Set(["$$canvas-image-preview", "$$comfy_animation_preview", "video-preview"]);
const STYLE_ID = "helto-take-capture-preview-style";
const PREVIEW_HEIGHT = 180;
const PREVIEW_WIDGET_MARGIN = 10;
const PREVIEW_BOTTOM_PADDING = 16;
const FILENAME_PREFIX_WIDGET_NAME = "filename_prefix";
const SHOT_ID_OVERRIDE_WIDGET_NAME = "shot_id_override";
const ACCEPT_WIDGET_NAME = "accept";
const UPDATE_CLIP_INSTANCE_WIDGET_NAME = "update_clip_instance";

export function takeCapturePreviewFromOutput(output, apiRef = null) {
  if (!isTakeCapturePreviewOutput(output)) return null;
  const source = Array.isArray(output?.images) ? output.images[0] : null;
  const url = takeCapturePreviewUrl(source, apiRef);
  if (!url) return null;
  return {
    privacyMode: Boolean(firstValue(output?.helto_privacy_mode)),
    source,
    url,
  };
}

export function takeCapturePreviewUrl(source, apiRef = null) {
  const filename = String(source?.filename ?? "").trim();
  if (!filename) return "";
  const params = new URLSearchParams({
    filename,
    type: String(source?.type ?? "output"),
  });
  const subfolder = String(source?.subfolder ?? "").trim();
  if (subfolder) params.set("subfolder", subfolder);
  const path = `/view?${params.toString()}`;
  return apiRef?.apiURL ? apiRef.apiURL(path) : path;
}

export function isTakeCapturePreviewOutput(output) {
  return Boolean(firstValue(output?.helto_take_capture_preview));
}

export function stripTakeCapturePreviewMedia(output) {
  if (!isTakeCapturePreviewOutput(output) || !output || typeof output !== "object") return output;
  const { images: _images, animated: _animated, ...stripped } = output;
  return stripped;
}

export function installTakeCapturePreview(nodeType, appRef, apiRef) {
  const onNodeCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = onNodeCreated?.apply(this, arguments);
    if (repairTakeCaptureShiftedSocketlessWidgetValues(this)) {
      setCanvasDirty(this, appRef);
    }
    ensureTakeCapturePreviewWidget(this);
    return result;
  };

  const onExecuted = nodeType.prototype.onExecuted;
  nodeType.prototype.onExecuted = function (output) {
    const nativeArgs = [stripTakeCapturePreviewMedia(output), ...Array.prototype.slice.call(arguments, 1)];
    const result = onExecuted?.apply(this, nativeArgs);
    syncTakeCapturePreview(this, output, { appRef, apiRef });
    return result;
  };

  const onMouseEnter = nodeType.prototype.onMouseEnter;
  nodeType.prototype.onMouseEnter = function () {
    const result = onMouseEnter?.apply(this, arguments);
    setTakeCapturePreviewReveal(this, true);
    return result;
  };

  const onMouseLeave = nodeType.prototype.onMouseLeave;
  nodeType.prototype.onMouseLeave = function () {
    const result = onMouseLeave?.apply(this, arguments);
    setTakeCapturePreviewReveal(this, false);
    return result;
  };

  const onDrawForeground = nodeType.prototype.onDrawForeground;
  nodeType.prototype.onDrawForeground = function () {
    const result = onDrawForeground?.apply(this, arguments);
    maintainTakeCapturePreview(this, { appRef });
    return result;
  };
}

export function syncTakeCapturePreview(node, output, { appRef = null, apiRef = null } = {}) {
  const preview = takeCapturePreviewFromOutput(output, apiRef);
  if (!preview) {
    resetTakeCapturePreview(node);
    return false;
  }
  const state = ensureTakeCapturePreviewWidget(node);
  if (!state) return false;
  capturePreviousHideOutputImages(node, state);
  node.hideOutputImages = true;
  suppressNativeTakeCapturePreview(node, output);
  state.url = preview.url;
  state.source = preview.source;
  state.privacyMode = preview.privacyMode;
  state.container.classList.toggle("privacy-mode", state.privacyMode);
  state.video.setAttribute("aria-label", state.privacyMode ? "Private take capture preview" : "Take capture preview");
  state.video.src = preview.url;
  state.video.currentTime = 0;
  state.container.hidden = false;
  setTakeCapturePreviewWidgetActive(state, true);
  prepareTakeCapturePreviewFitTarget(node, state);
  setTakeCapturePreviewReveal(node, false);
  ensureTakeCapturePreviewNodeFits(node);
  scheduleTakeCapturePreviewMaintenance(node, output, appRef);
  setCanvasDirty(node, appRef);
  return true;
}

export function maintainTakeCapturePreview(node, { appRef = null } = {}) {
  const state = node?._heltoTakeCapturePreview;
  if (!state?.url) return false;
  const changed = suppressNativeTakeCapturePreview(node);
  const resized = ensureTakeCapturePreviewNodeFits(node);
  if (changed || resized) {
    setCanvasDirty(node, appRef);
    return true;
  }
  return false;
}

export function setTakeCapturePreviewReveal(node, revealed) {
  const state = node?._heltoTakeCapturePreview;
  if (!state?.url) return false;
  state.revealed = Boolean(revealed);
  state.container.classList.toggle("is-revealed", state.revealed);
  if (state.revealed) {
    state.video.muted = true;
    const playResult = state.video.play?.();
    playResult?.catch?.(() => {});
  } else {
    state.video.pause?.();
    state.video.currentTime = 0;
  }
  return true;
}

export function clearNativeTakeCapturePreview(node) {
  if (!node) return false;
  let changed = false;
  if (Array.isArray(node.imgs) && node.imgs.length) {
    node.imgs = [];
    changed = true;
  }
  if (node.videoContainer !== undefined) {
    node.videoContainer = undefined;
    changed = true;
  }
  if (Array.isArray(node.widgets)) {
    const nextWidgets = [];
    for (const widget of node.widgets) {
      if (NATIVE_PREVIEW_WIDGET_NAMES.has(widget?.name)) {
        widget?.onRemove?.();
        changed = true;
      } else {
        nextWidgets.push(widget);
      }
    }
    if (nextWidgets.length !== node.widgets.length) {
      node.widgets = nextWidgets;
      changed = true;
    }
  }
  return changed;
}

export function suppressNativeTakeCapturePreview(node, output = null) {
  if (!node) return false;
  let changed = false;
  if (Array.isArray(output?.images) && node.images !== output.images) {
    node.images = output.images;
    changed = true;
  }
  if (node.animatedImages) {
    node.animatedImages = false;
    changed = true;
  }
  if (node.previewMediaType !== undefined) {
    node.previewMediaType = undefined;
    changed = true;
  }
  return clearNativeTakeCapturePreview(node) || changed;
}

export function ensureTakeCapturePreviewWidget(node, documentRef = globalThis.document) {
  if (!node || node._heltoTakeCapturePreview) return node?._heltoTakeCapturePreview ?? null;
  if (!documentRef?.createElement || !node.addDOMWidget) return null;
  installTakeCapturePreviewStyles(documentRef);

  const container = documentRef.createElement("div");
  container.className = "helto-take-capture-preview";
  container.hidden = true;

  const video = documentRef.createElement("video");
  video.preload = "metadata";
  video.playsInline = true;
  video.muted = true;
  video.loop = true;
  video.controls = false;
  video.setAttribute("aria-label", "Private take capture preview");
  container.append(video);
  container.addEventListener?.("mouseenter", () => setTakeCapturePreviewReveal(node, true));
  container.addEventListener?.("mouseleave", () => setTakeCapturePreviewReveal(node, false));

  const state = {
    container,
    video,
    hasPreviousHideOutputImages: false,
    previousHideOutputImages: undefined,
    previousHideOutputImagesWasOwnProperty: false,
    privacyMode: false,
    source: null,
    url: "",
    revealed: false,
  };
  const widgetHeight = () => takeCapturePreviewStateOuterHeight(state);
  const widget = node.addDOMWidget(PREVIEW_WIDGET_NAME, "Take Capture Preview", container, {
    serialize: false,
    hideOnZoom: false,
    margin: PREVIEW_WIDGET_MARGIN,
    getMinHeight: widgetHeight,
    getMaxHeight: widgetHeight,
    getHeight: widgetHeight,
  });
  state.widget = widget;
  state.fitTargetHeight = 0;
  setTakeCapturePreviewWidgetActive(state, false);
  node._heltoTakeCapturePreview = state;
  return state;
}

export function ensureTakeCapturePreviewNodeFits(node) {
  if (!node || typeof node.setSize !== "function") return false;
  const state = node._heltoTakeCapturePreview;
  if (!state?.url) return false;
  const currentWidth = finiteNumber(node.size?.[0], 0);
  const currentHeight = finiteNumber(node.size?.[1], 0);
  const nextWidth = currentWidth;
  const nextHeight = Math.max(currentHeight, takeCapturePreviewRequiredNodeHeight(node));
  if (nextWidth <= currentWidth && nextHeight <= currentHeight) return false;
  node.setSize([nextWidth, nextHeight]);
  return true;
}

export function repairTakeCaptureShiftedSocketlessWidgetValues(node) {
  const shotIdOverrideWidget = findNodeWidget(node, SHOT_ID_OVERRIDE_WIDGET_NAME);
  const filenamePrefixWidget = findNodeWidget(node, FILENAME_PREFIX_WIDGET_NAME);
  if (!shotIdOverrideWidget || !filenamePrefixWidget) return false;
  const shiftedFilenamePrefix = String(shotIdOverrideWidget.value ?? "").trim();
  if (!shiftedFilenamePrefix || !isBooleanLikeWidgetValue(filenamePrefixWidget.value)) return false;

  const acceptWidget = findNodeWidget(node, ACCEPT_WIDGET_NAME);
  const updateClipInstanceWidget = findNodeWidget(node, UPDATE_CLIP_INSTANCE_WIDGET_NAME);
  const shiftedAccept = filenamePrefixWidget.value;
  const shiftedUpdateClipInstance = acceptWidget?.value;

  shotIdOverrideWidget.value = "";
  filenamePrefixWidget.value = shiftedFilenamePrefix;
  if (acceptWidget) {
    acceptWidget.value = booleanFromWidgetValue(shiftedAccept, false);
  }
  if (updateClipInstanceWidget && isBooleanLikeWidgetValue(shiftedUpdateClipInstance)) {
    updateClipInstanceWidget.value = booleanFromWidgetValue(shiftedUpdateClipInstance, updateClipInstanceWidget.value);
  }
  return true;
}

export function takeCapturePreviewRequiredNodeHeight(node) {
  const state = node?._heltoTakeCapturePreview;
  if (!state?.url) return 0;
  return prepareTakeCapturePreviewFitTarget(node, state);
}

function resetTakeCapturePreview(node) {
  const state = node?._heltoTakeCapturePreview;
  if (!state) return;
  state.url = "";
  state.source = null;
  state.privacyMode = false;
  state.container.hidden = true;
  setTakeCapturePreviewWidgetActive(state, false);
  state.container.classList.remove("is-revealed");
  state.container.classList.remove("privacy-mode");
  state.video.pause?.();
  state.video.removeAttribute("src");
  state.video.load?.();
  restorePreviousHideOutputImages(node, state);
}

function setTakeCapturePreviewWidgetActive(state, active) {
  if (!state?.widget) return;
  state.widget.hidden = !active;
}

function capturePreviousHideOutputImages(node, state) {
  if (state.hasPreviousHideOutputImages) return;
  state.previousHideOutputImagesWasOwnProperty = Object.prototype.hasOwnProperty.call(node, "hideOutputImages");
  state.previousHideOutputImages = node.hideOutputImages;
  state.hasPreviousHideOutputImages = true;
}

function restorePreviousHideOutputImages(node, state) {
  if (!state.hasPreviousHideOutputImages) return;
  if (state.previousHideOutputImagesWasOwnProperty) {
    node.hideOutputImages = state.previousHideOutputImages;
  } else {
    delete node.hideOutputImages;
  }
  state.previousHideOutputImages = undefined;
  state.previousHideOutputImagesWasOwnProperty = false;
  state.hasPreviousHideOutputImages = false;
}

function scheduleTakeCapturePreviewMaintenance(node, output = null, appRef = null) {
  const refresh = () => {
    const changed = suppressNativeTakeCapturePreview(node, output);
    const resized = ensureTakeCapturePreviewNodeFits(node);
    if (changed || resized) {
      setCanvasDirty(node, appRef);
    }
  };
  if (typeof globalThis.queueMicrotask === "function") {
    globalThis.queueMicrotask(refresh);
  }
  if (typeof globalThis.requestAnimationFrame === "function") {
    globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(refresh));
  } else if (typeof globalThis.setTimeout === "function") {
    globalThis.setTimeout(refresh, 0);
  }
}

function prepareTakeCapturePreviewFitTarget(node, state) {
  if (!state?.url) return 0;
  const currentHeight = finiteNumber(node?.size?.[1], 0);
  const previewHeight = takeCapturePreviewStateOuterHeight(state) + PREVIEW_BOTTOM_PADDING;
  if (!positiveFiniteNumber(state.fitTargetHeight)) {
    state.fitTargetHeight = Math.ceil(currentHeight + previewHeight);
  }
  return state.fitTargetHeight;
}

function takeCapturePreviewStateOuterHeight(state) {
  if (!state?.url) return 0;
  const margin = finiteNumber(state.widget?.margin ?? state.widget?.options?.margin, PREVIEW_WIDGET_MARGIN);
  return PREVIEW_HEIGHT + margin * 2;
}

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function positiveFiniteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function findNodeWidget(node, name) {
  return Array.isArray(node?.widgets) ? node.widgets.find((widget) => widget?.name === name) : null;
}

function isBooleanLikeWidgetValue(value) {
  if (typeof value === "boolean") return true;
  if (typeof value !== "string") return false;
  const text = value.trim().toLowerCase();
  return text === "true" || text === "false";
}

function booleanFromWidgetValue(value, fallback) {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const text = value.trim().toLowerCase();
    if (text === "true") return true;
    if (text === "false") return false;
  }
  return Boolean(fallback);
}

function setCanvasDirty(node, appRef = null) {
  node?.graph?.setDirtyCanvas?.(true, true);
  appRef?.canvas?.setDirty?.(true, true);
}

function firstValue(value) {
  return Array.isArray(value) ? value[0] : value;
}

function installTakeCapturePreviewStyles(documentRef) {
  if (documentRef.getElementById?.(STYLE_ID)) return;
  const style = documentRef.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    ${htdTokenBlock(".helto-take-capture-preview")}
    /* Near-black covers (#060a10 / #050505) are intentional privacy concealment — keep them opaque and dark. */
    .helto-take-capture-preview { width: 100%; height: ${PREVIEW_HEIGHT}px; box-sizing: border-box; background: #060a10; border: 1px solid var(--htd-border); border-radius: var(--htd-radius); overflow: hidden; display: flex; align-items: center; justify-content: center; }
    .helto-take-capture-preview video { width: 100%; height: 100%; object-fit: contain; background: #050505; opacity: 0; transition: opacity 120ms ease; }
    .helto-take-capture-preview:hover video,
    .helto-take-capture-preview.is-revealed video { opacity: 1; }
  `;
  documentRef.head?.append(style);
}
