import {
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  ASSET_SOURCE_GENERATED,
  TAKE_STATUSES,
  deepClone,
} from "./schema.js";
import {
  addAudioClip,
  addSection,
  attachVideoAssetAsTake,
  findSection,
} from "./operations.js";
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

export function attachPickedGeneratedVideoAsTake(timeline, shotId, item, takeData = {}) {
  const asset = createPickedMediaAsset(ASSET_TYPE_VIDEO, item);
  if (!asset) return null;
  const capture = normalizeGeneratedTakeCapture(item?.take_capture);
  const registration = capture?.registration && typeof capture.registration === "object"
    ? capture.registration
    : null;
  const registrationAsset = registration?.asset && typeof registration.asset === "object"
    ? registration.asset
    : null;
  asset.source_kind = ASSET_SOURCE_GENERATED;
  if (registrationAsset?.asset_id) asset.asset_id = String(registrationAsset.asset_id);
  if (registrationAsset?.name) asset.name = String(registrationAsset.name);
  if (registrationAsset?.mime_type) asset.mime_type = String(registrationAsset.mime_type);
  if (Number.isFinite(registrationAsset?.size_bytes)) asset.size_bytes = Number(registrationAsset.size_bytes);
  asset.metadata = {
    ...(asset.metadata ?? {}),
    ...safeObject(registrationAsset?.metadata),
    ...mediaMetadataFromCapture(capture?.media),
    shot_id: shotId,
    source_kind: ASSET_SOURCE_GENERATED,
  };
  timeline.assets ??= [];
  timeline.assets.push(asset);
  const captureTake = takeDataFromRegistration(registration?.take);
  const take = attachVideoAssetAsTake(timeline, shotId, asset.asset_id, {
    ...captureTake,
    ...deepClone(takeData),
  });
  return take ? { asset, take } : null;
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

function normalizeGeneratedTakeCapture(capture) {
  if (!capture || typeof capture !== "object" || capture.type !== "HELTO_GENERATED_TAKE_CAPTURE") return null;
  if (!capture.registration || typeof capture.registration !== "object") return null;
  return capture;
}

function takeDataFromRegistration(take) {
  if (!take || typeof take !== "object") return {};
  const copy = deepClone(take);
  delete copy.shot_id;
  if (!TAKE_STATUSES.includes(copy.status)) copy.status = "Candidate";
  copy.metadata = safeObject(copy.metadata);
  copy.resolved_loras = copy.resolved_loras ?? null;
  return copy;
}

function mediaMetadataFromCapture(media) {
  const metadata = {};
  if (!media || typeof media !== "object") return metadata;
  for (const key of ["frame_rate", "frame_count", "duration_seconds", "width", "height"]) {
    if (media[key] !== undefined && media[key] !== null && media[key] !== "") {
      metadata[key] = media[key];
    }
  }
  return metadata;
}

function safeObject(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? deepClone(value)
    : {};
}
