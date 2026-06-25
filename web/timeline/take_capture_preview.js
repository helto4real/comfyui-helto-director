const PREVIEW_WIDGET_NAME = "helto_take_capture_preview";
const NATIVE_PREVIEW_WIDGET_NAME = "$$canvas-image-preview";
const STYLE_ID = "helto-take-capture-preview-style";
const PREVIEW_HEIGHT = 180;

export function takeCapturePreviewFromOutput(output, apiRef = null) {
  if (!isTakeCapturePreviewOutput(output) || !firstValue(output?.helto_privacy_mode)) return null;
  const source = Array.isArray(output?.images) ? output.images[0] : null;
  const url = takeCapturePreviewUrl(source, apiRef);
  if (!url) return null;
  return {
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

export function installTakeCapturePrivacyPreview(nodeType, appRef, apiRef) {
  const onNodeCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = onNodeCreated?.apply(this, arguments);
    ensureTakeCapturePreviewWidget(this);
    return result;
  };

  const onExecuted = nodeType.prototype.onExecuted;
  nodeType.prototype.onExecuted = function (output) {
    const result = onExecuted?.apply(this, arguments);
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
}

export function syncTakeCapturePreview(node, output, { appRef = null, apiRef = null } = {}) {
  const preview = takeCapturePreviewFromOutput(output, apiRef);
  if (!preview) {
    resetTakeCapturePreview(node);
    return false;
  }
  const state = ensureTakeCapturePreviewWidget(node);
  if (!state) return false;
  clearNativeTakeCapturePreview(node);
  state.url = preview.url;
  state.source = preview.source;
  state.video.src = preview.url;
  state.video.currentTime = 0;
  state.container.hidden = false;
  setTakeCapturePreviewReveal(node, false);
  setCanvasDirty(node, appRef);
  return true;
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
  if (Array.isArray(node.widgets)) {
    const nextWidgets = node.widgets.filter((widget) => widget?.name !== NATIVE_PREVIEW_WIDGET_NAME);
    if (nextWidgets.length !== node.widgets.length) {
      node.widgets = nextWidgets;
      changed = true;
    }
  }
  return changed;
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
    source: null,
    url: "",
    revealed: false,
  };
  const widgetHeight = () => (state.url ? PREVIEW_HEIGHT : -4);
  const widget = node.addDOMWidget(PREVIEW_WIDGET_NAME, "Take Capture Preview", container, {
    serialize: false,
    hideOnZoom: false,
    getMinHeight: widgetHeight,
    getMaxHeight: widgetHeight,
    getHeight: widgetHeight,
  });
  state.widget = widget;
  node._heltoTakeCapturePreview = state;
  return state;
}

function resetTakeCapturePreview(node) {
  const state = node?._heltoTakeCapturePreview;
  if (!state) return;
  state.url = "";
  state.source = null;
  state.container.hidden = true;
  state.container.classList.remove("is-revealed");
  state.video.pause?.();
  state.video.removeAttribute("src");
  state.video.load?.();
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
    /* Helto tokens used as literals (this style is global on <head>, outside the token scope). Near-black covers (#060a10 / #050505) are intentional privacy concealment — keep them opaque and dark. */
    .helto-take-capture-preview { width: 100%; height: ${PREVIEW_HEIGHT}px; box-sizing: border-box; background: #060a10; border: 1px solid #2a3346; border-radius: 6px; overflow: hidden; display: flex; align-items: center; justify-content: center; }
    .helto-take-capture-preview video { width: 100%; height: 100%; object-fit: contain; background: #050505; opacity: 0; transition: opacity 120ms ease; }
    .helto-take-capture-preview:hover video,
    .helto-take-capture-preview.is-revealed video { opacity: 1; }
  `;
  documentRef.head?.append(style);
}
