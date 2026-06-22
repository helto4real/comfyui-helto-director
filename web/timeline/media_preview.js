import {
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
} from "./schema.js";

const PREVIEW_CLASS = "pr-image-large-preview";
const STYLE_ID = "helto-media-preview-style";

export function showMediaPreview(documentRef = globalThis.document, options = {}) {
  if (!documentRef || !options?.url) return null;
  installMediaPreviewStyles(documentRef);
  closeMediaPreview(documentRef);

  const overlay = documentRef.createElement("div");
  overlay.className = `${PREVIEW_CLASS}${options.privacyMode ? " privacy-mode" : ""}`;
  const panel = documentRef.createElement("div");
  panel.className = "pr-image-large-preview-panel";

  const close = documentRef.createElement("button");
  close.className = "pr-image-large-preview-close";
  close.type = "button";
  close.title = "Close preview";
  close.setAttribute("aria-label", "Close preview");
  close.textContent = "x";
  panel.append(close);

  if (normalizePreviewType(options.type) === "video") {
    panel.classList.add("is-video");
    appendVideoPreview(documentRef, panel, options.url);
  } else {
    const image = documentRef.createElement("img");
    image.src = options.url;
    image.alt = "";
    panel.append(image);
  }

  if (options.caption) {
    const caption = documentRef.createElement("div");
    caption.className = "pr-image-large-preview-caption";
    caption.textContent = options.caption;
    panel.append(caption);
  }

  const keydownTarget = documentRef;
  const onKeyDown = (event) => {
    if (event.key === "Escape") closeMediaPreview(documentRef);
  };
  overlay._htdMediaPreviewCleanup = () => {
    keydownTarget.removeEventListener?.("keydown", onKeyDown, true);
    const video = overlay.querySelector("video");
    video?.pause?.();
  };
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay || event.target.closest(".pr-image-large-preview-close")) {
      closeMediaPreview(documentRef);
    }
  });
  keydownTarget.addEventListener?.("keydown", onKeyDown, true);
  overlay.append(panel);
  documentRef.body.append(overlay);
  return overlay;
}

export function closeMediaPreview(documentRef = globalThis.document) {
  for (const overlay of documentRef?.querySelectorAll?.(`.${PREVIEW_CLASS}`) ?? []) {
    overlay?._htdMediaPreviewCleanup?.();
    overlay?.remove();
  }
}

function appendVideoPreview(documentRef, panel, url) {
  const video = documentRef.createElement("video");
  video.src = url;
  video.preload = "metadata";
  video.playsInline = true;
  video.muted = true;
  panel.append(video);

  const controls = documentRef.createElement("div");
  controls.className = "pr-image-large-preview-controls";
  const play = previewButton(documentRef, "Play preview");
  const stop = previewButton(documentRef, "Stop preview");
  const mute = previewButton(documentRef, "Audio muted");

  const sync = () => {
    const playing = !video.paused && !video.ended;
    play.textContent = playing ? "Pause" : "Play";
    play.title = playing ? "Pause preview" : "Play preview";
    play.setAttribute("aria-label", play.title);
    mute.textContent = video.muted ? "Muted" : "Audio On";
    mute.title = video.muted ? "Audio muted" : "Audio enabled";
    mute.setAttribute("aria-label", mute.title);
  };

  play.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (video.paused || video.ended) {
      try {
        await video.play();
      } catch (_error) {
        video.pause();
      }
    } else {
      video.pause();
    }
    sync();
  });
  stop.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    video.pause();
    video.currentTime = 0;
    sync();
  });
  mute.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    video.muted = !video.muted;
    sync();
  });
  video.addEventListener("play", sync);
  video.addEventListener("pause", sync);
  video.addEventListener("ended", sync);

  stop.textContent = "Stop";
  controls.append(play, stop, mute);
  panel.append(controls);
  sync();
}

function previewButton(documentRef, title) {
  const button = documentRef.createElement("button");
  button.className = "pr-image-large-preview-control";
  button.type = "button";
  button.title = title;
  button.setAttribute("aria-label", title);
  return button;
}

function normalizePreviewType(type) {
  const value = String(type ?? "").toLowerCase();
  if (value === "video" || type === ASSET_TYPE_VIDEO) return "video";
  if (value === "image" || type === ASSET_TYPE_IMAGE) return "image";
  return "image";
}

function installMediaPreviewStyles(documentRef) {
  if (!documentRef || documentRef.getElementById(STYLE_ID)) return;
  const style = documentRef.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .pr-image-large-preview { position: fixed; inset: 0; z-index: 10050; background: rgba(0,0,0,0.78); display: flex; align-items: center; justify-content: center; }
    .pr-image-large-preview-panel { position: relative; max-width: calc(100vw - 64px); max-height: calc(100vh - 64px); background: #111; border: 1px solid #444; border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
    .pr-image-large-preview-panel img, .pr-image-large-preview-panel video { max-width: 100%; max-height: calc(100vh - 132px); object-fit: contain; background: #050505; }
    .pr-image-large-preview-panel video { min-width: min(720px, calc(100vw - 96px)); }
    .pr-image-large-preview.privacy-mode .pr-image-large-preview-panel img,
    .pr-image-large-preview.privacy-mode .pr-image-large-preview-panel video { opacity: 0; transition: opacity 120ms ease; }
    .pr-image-large-preview.privacy-mode .pr-image-large-preview-panel:hover img,
    .pr-image-large-preview.privacy-mode .pr-image-large-preview-panel:hover video { opacity: 1; }
    .pr-image-large-preview-close { position: absolute; top: 8px; right: 8px; width: 26px; height: 26px; border-radius: 50%; border: 1px solid #555; background: #1e1e1e; color: #eee; cursor: pointer; z-index: 1; }
    .pr-image-large-preview-caption { color: #ddd; font-size: 12px; text-align: center; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pr-image-large-preview-controls { display: flex; justify-content: center; gap: 8px; }
    .pr-image-large-preview-control { min-width: 68px; background: #252525; color: #eee; border: 1px solid #555; border-radius: 4px; padding: 6px 10px; cursor: pointer; }
    .pr-image-large-preview-control:hover { background: #333; }
  `;
  documentRef.head.append(style);
}
