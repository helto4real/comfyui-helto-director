import {
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
} from "./schema.js";
import { addAudioClip, addSection, findSection } from "./operations.js";
import { attachMediaAsset, createFilePathAsset } from "./media.js";


export function createPickedMediaAsset(assetType, item) {
  const path = String(item?.path ?? "").trim();
  if (!path) return null;
  return createFilePathAsset(assetType, path, {
    name: item.name ?? basename(item.filename ?? path),
    mime_type: item.mime_type ?? "",
    size_bytes: Number.isFinite(item.size) ? item.size : null,
    browser_alias: item.folder_alias ?? null,
    browser_filename: item.filename ?? null,
    mtime: Number.isFinite(item.mtime) ? item.mtime : null,
    width: Number.isFinite(item.width) ? item.width : null,
    height: Number.isFinite(item.height) ? item.height : null,
    duration_seconds: Number.isFinite(item.duration_seconds) ? item.duration_seconds : null,
  });
}

export function addPickedMediaItem(timeline, assetType, item) {
  const asset = createPickedMediaAsset(assetType, item);
  if (!asset) return null;
  if (assetType === ASSET_TYPE_IMAGE || assetType === ASSET_TYPE_VIDEO) {
    const section = addSection(timeline, assetType);
    if (!section) return null;
    attachMediaAsset(timeline, section.item_id, asset);
    return section;
  }
  if (assetType === ASSET_TYPE_AUDIO) {
    const start = Number(timeline.ui_state.playhead_time ?? 0);
    const duration = Number.isFinite(item.duration_seconds) && item.duration_seconds > 0
      ? item.duration_seconds
      : 1;
    const clip = addAudioClip(timeline, start, duration);
    attachMediaAsset(timeline, clip.item_id, asset);
    return clip;
  }
  return null;
}

export function replacePickedSectionMedia(timeline, itemId, assetType, item) {
  if (assetType !== ASSET_TYPE_IMAGE && assetType !== ASSET_TYPE_VIDEO) return null;
  const section = findSection(timeline, itemId);
  if (!section || section.type !== assetType) return null;
  const asset = createPickedMediaAsset(assetType, item);
  if (!asset) return null;
  attachMediaAsset(timeline, section.item_id, asset);
  timeline.ui_state.selected_item_id = section.item_id;
  return section;
}

function basename(path) {
  return String(path ?? "").split(/[\\/]/).filter(Boolean).pop() ?? "";
}
