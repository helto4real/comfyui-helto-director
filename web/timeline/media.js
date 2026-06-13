import {
  ASSET_SOURCE_FILE_PATH,
  ASSET_SOURCE_UPLOADED_FILE,
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  deepClone,
} from "./schema.js";

const MEDIA_FIELDS_BY_TYPE = {
  Image: "image",
  Video: "video",
  Audio: "audio",
};

const IMAGE_EXTENSIONS = new Set([".apng", ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"]);
const VIDEO_EXTENSIONS = new Set([".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"]);
const AUDIO_EXTENSIONS = new Set([".aac", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".weba"]);

export function mediaFieldForAssetType(assetType) {
  return MEDIA_FIELDS_BY_TYPE[assetType] ?? null;
}

export function inferAssetTypeFromPath(path, fallback = ASSET_TYPE_IMAGE) {
  const extension = extensionForPath(path);
  if (IMAGE_EXTENSIONS.has(extension)) return ASSET_TYPE_IMAGE;
  if (VIDEO_EXTENSIONS.has(extension)) return ASSET_TYPE_VIDEO;
  if (AUDIO_EXTENSIONS.has(extension)) return ASSET_TYPE_AUDIO;
  return fallback;
}

export function createFilePathAsset(assetType, path, metadata = {}) {
  const normalizedPath = String(path ?? "").trim();
  return createAsset({
    type: assetType,
    source_kind: ASSET_SOURCE_FILE_PATH,
    path: normalizedPath,
    name: metadata.name ?? basename(normalizedPath),
    mime_type: metadata.mime_type ?? "",
    size_bytes: metadata.size_bytes ?? null,
    metadata,
  });
}

export function createBrowserFileAsset(file, fallbackType = ASSET_TYPE_IMAGE) {
  const path = String(file?.path ?? file?.webkitRelativePath ?? "").trim();
  const name = String(file?.name ?? basename(path) ?? "").trim();
  const assetType = inferAssetTypeFromPath(name || path, fallbackType);
  return createAsset({
    type: assetType,
    source_kind: path ? ASSET_SOURCE_FILE_PATH : ASSET_SOURCE_UPLOADED_FILE,
    path: path || null,
    name,
    mime_type: file?.type ?? "",
    size_bytes: Number.isFinite(file?.size) ? file.size : null,
    metadata: {
      last_modified: Number.isFinite(file?.lastModified) ? file.lastModified : null,
    },
  });
}

export function attachMediaAsset(timeline, itemId, asset) {
  const normalizedAsset = upsertAsset(timeline, asset);
  const field = mediaFieldForAssetType(normalizedAsset.type);
  const item = findMediaTarget(timeline, itemId, normalizedAsset.type);
  if (!item || !field) return null;
  item[field] = { asset_id: normalizedAsset.asset_id };
  if ("name" in item && !item.name) item.name = normalizedAsset.name ?? "";
  return normalizedAsset;
}

export function clearMediaReference(timeline, itemId) {
  const item = findAnyMediaTarget(timeline, itemId);
  if (!item) return false;
  for (const field of Object.values(MEDIA_FIELDS_BY_TYPE)) {
    if (field in item) item[field] = null;
  }
  return true;
}

export function resolveMediaReference(timeline, reference) {
  if (!reference) return null;
  if (typeof reference === "string") return { path: reference, name: basename(reference) };
  if (reference.asset_id) {
    return timeline.assets.find((asset) => asset.asset_id === reference.asset_id) ?? null;
  }
  if (reference.path || reference.file_path) {
    return { path: reference.path ?? reference.file_path, name: reference.name ?? basename(reference.path ?? reference.file_path) };
  }
  return null;
}

export function mediaLabel(timeline, reference, fallback = "No media") {
  const asset = resolveMediaReference(timeline, reference);
  return asset?.name || asset?.path || asset?.file_path || fallback;
}

export function createWaveformBars(seedValue, count = 32) {
  let seed = stableHash(String(seedValue ?? "audio"));
  const bars = [];
  for (let index = 0; index < count; index += 1) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    bars.push(0.18 + ((seed % 1000) / 1000) * 0.78);
  }
  return bars;
}

export function containsEmbeddedMedia(value) {
  if (!value || typeof value !== "object") return false;
  const stack = [value];
  while (stack.length) {
    const current = stack.pop();
    if (!current || typeof current !== "object") continue;
    for (const [key, child] of Object.entries(current)) {
      if (["data", "blob", "bytes", "thumbnail", "thumbnail_data", "waveform", "waveform_data"].includes(key)) {
        return true;
      }
      if (typeof child === "string" && /^(data:|blob:)/.test(child)) return true;
      if (child && typeof child === "object") stack.push(child);
    }
  }
  return false;
}

export function findAnyMediaTarget(timeline, itemId) {
  return findMediaTarget(timeline, itemId) ?? null;
}

function createAsset(asset) {
  const copy = deepClone(asset);
  copy.asset_id ??= makeAssetId(copy.type, copy.path ?? copy.name ?? "");
  copy.name ??= basename(copy.path ?? "");
  copy.metadata ??= {};
  return copy;
}

function upsertAsset(timeline, asset) {
  timeline.assets ??= [];
  const existingIndex = timeline.assets.findIndex((candidate) => candidate.asset_id === asset.asset_id);
  if (existingIndex >= 0) {
    timeline.assets[existingIndex] = { ...timeline.assets[existingIndex], ...deepClone(asset) };
    return timeline.assets[existingIndex];
  }
  const copy = deepClone(asset);
  timeline.assets.push(copy);
  return copy;
}

function findMediaTarget(timeline, itemId, assetType = null) {
  if (!itemId) return null;
  if (!assetType || assetType === ASSET_TYPE_IMAGE || assetType === ASSET_TYPE_VIDEO) {
    const section = timeline.director_track.sections.find((candidate) => candidate.item_id === itemId);
    if (section && (!assetType || section.type === assetType)) return section;
  }
  if (!assetType || assetType === ASSET_TYPE_AUDIO) {
    for (const track of timeline.audio_tracks) {
      const clip = track.clips.find((candidate) => candidate.item_id === itemId);
      if (clip) return clip;
    }
  }
  return null;
}

function makeAssetId(assetType, seed) {
  return `${String(assetType).toLowerCase()}_${stableHash(seed).toString(36)}_${Date.now().toString(36)}`;
}

function stableHash(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function basename(path) {
  return String(path ?? "").split(/[\\/]/).filter(Boolean).pop() ?? "";
}

function extensionForPath(path) {
  const name = basename(path).toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot) : "";
}
