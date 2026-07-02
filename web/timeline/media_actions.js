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
  addTakeMetadata,
  attachVideoAssetAsTake,
  findSection,
} from "./operations.js";
import { attachMediaAsset, createFilePathAsset } from "./media.js";
import { ensureStoredPrivacyTokenCookie } from "./privacy.js";

const MEDIA_BROWSER_ROUTE_PREFIX = "/helto_director/media_browser";


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
  const captureTake = takeDataFromRegistration(registration?.take);
  asset.source_kind = ASSET_SOURCE_GENERATED;
  const preferredAssetId = registrationAsset?.asset_id ? String(registrationAsset.asset_id) : asset.asset_id;
  asset.asset_id = uniqueAssetId(timeline, preferredAssetId, asset.path ?? asset.file_path ?? item?.path);
  if (registrationAsset?.name) asset.name = String(registrationAsset.name);
  if (registrationAsset?.mime_type) asset.mime_type = String(registrationAsset.mime_type);
  if (Number.isFinite(registrationAsset?.size_bytes)) asset.size_bytes = Number(registrationAsset.size_bytes);
  asset.metadata = {
    ...(asset.metadata ?? {}),
    ...safeObject(registrationAsset?.metadata),
    ...mediaMetadataFromCapture(capture?.media),
    shot_id: shotId,
    take_id: captureTake.take_id ?? null,
    source_kind: ASSET_SOURCE_GENERATED,
  };
  const savedAsset = upsertTimelineAsset(timeline, asset);
  const take = attachVideoAssetAsTake(timeline, shotId, savedAsset.asset_id, {
    ...captureTake,
    ...deepClone(takeData),
  });
  return take ? { asset: savedAsset, take } : null;
}

export async function fetchProjectTakeCaptures(timeline, shotId, privacyMode = false) {
  if (privacyMode) ensureStoredPrivacyTokenCookie();
  const response = await fetch(`${MEDIA_BROWSER_ROUTE_PREFIX}/project_takes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project: timeline?.project ?? {},
      shot_id: shotId,
      privacy: Boolean(privacyMode),
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error || "Failed to load project take captures.");
  }
  return payload;
}

export async function deleteProjectTakeCapture(timeline, shotId, path, options = {}) {
  const response = await fetch(`${MEDIA_BROWSER_ROUTE_PREFIX}/project_takes/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project: timeline?.project ?? {},
      shot_id: shotId,
      path,
      take_id: options.takeId ?? "",
      privacy: Boolean(options.privacyMode),
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.error || "Failed to delete project take.");
  }
  return payload;
}

export function registerGeneratedTakePayload(timeline, shotId, payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const registration = registrationFromPayload(payload);
  const summary = payload.summary && typeof payload.summary === "object" ? payload.summary : null;
  const media = payload.media && typeof payload.media === "object" ? payload.media : null;
  const registrationAsset = registration?.asset && typeof registration.asset === "object"
    ? registration.asset
    : null;
  const takeData = takeDataFromRegistration(takePayloadFromRegistration(registration, summary));
  const assetPayload = {
    ...safeObject(registrationAsset),
    ...safeObject(summary),
  };
  const assetPath = stringValue(
    registrationAsset?.path
    ?? registrationAsset?.file_path
    ?? registration?.path
    ?? registration?.file_path
    ?? media?.path
    ?? media?.file_path
    ?? summary?.path,
  );
  const requestedAssetId = stringValue(
    registrationAsset?.asset_id
    ?? registration?.asset_id
    ?? takeData.asset_id
    ?? summary?.asset_id,
  );
  let asset = null;
  let assetId = requestedAssetId;
  if (assetPath) {
    asset = createFilePathAsset(ASSET_TYPE_VIDEO, assetPath, {
      name: assetPayload.name ?? assetPayload.filename ?? basename(assetPath),
      mime_type: assetPayload.mime_type ?? "",
      size_bytes: Number.isFinite(assetPayload.size_bytes) ? Number(assetPayload.size_bytes) : null,
      frame_rate: assetPayload.frame_rate ?? media?.frame_rate ?? null,
      frame_count: assetPayload.frame_count ?? media?.frame_count ?? null,
      duration_seconds: assetPayload.duration_seconds ?? media?.duration_seconds ?? null,
      width: assetPayload.width ?? media?.width ?? null,
      height: assetPayload.height ?? media?.height ?? null,
    });
    asset.source_kind = ASSET_SOURCE_GENERATED;
    asset.asset_id = uniqueAssetId(timeline, requestedAssetId || asset.asset_id, assetPath);
    asset.metadata = {
      ...(asset.metadata ?? {}),
      ...safeObject(registrationAsset?.metadata),
      ...mediaMetadataFromCapture(media),
      shot_id: shotId,
      take_id: takeData.take_id ?? summary?.take_id ?? null,
      source_kind: ASSET_SOURCE_GENERATED,
    };
    upsertTimelineAsset(timeline, asset);
    assetId = asset.asset_id;
  }
  if (!assetId) return null;
  const take = addTakeMetadata(timeline, shotId, {
    ...takeData,
    take_id: takeData.take_id ?? summary?.take_id,
    asset_id: assetId,
    status: TAKE_STATUSES.includes(takeData.status) ? takeData.status : "Candidate",
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

function registrationFromPayload(payload) {
  if (payload.type === "HELTO_GENERATED_TAKE_CAPTURE") {
    return payload.registration && typeof payload.registration === "object" ? payload.registration : null;
  }
  if (payload.registration && typeof payload.registration === "object") return payload.registration;
  return payload;
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

function takePayloadFromRegistration(registration, summary) {
  if (registration?.take && typeof registration.take === "object") return registration.take;
  if (summary && typeof summary === "object") {
    const metadata = {};
    for (const key of ["storage_action", "media_type"]) {
      if (summary[key] !== undefined && summary[key] !== null && summary[key] !== "") {
        metadata[key] = summary[key];
      }
    }
    return {
      take_id: summary.take_id,
      asset_id: summary.asset_id,
      status: summary.accepted ? "Accepted" : "Candidate",
      metadata,
    };
  }
  return registration ?? {};
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

function stringValue(value) {
  const text = String(value ?? "").trim();
  return text || "";
}

function uniqueAssetId(timeline, preferredId, path) {
  const base = stringValue(preferredId) || "asset_generated";
  const existing = timeline.assets?.find((asset) => asset.asset_id === base);
  if (!existing || existing.path === path || existing.file_path === path) return base;
  let suffix = 2;
  while (timeline.assets?.some((asset) => asset.asset_id === `${base}_${suffix}`)) {
    suffix += 1;
  }
  return `${base}_${suffix}`;
}

function upsertTimelineAsset(timeline, asset) {
  timeline.assets ??= [];
  const index = timeline.assets.findIndex((candidate) => candidate.asset_id === asset.asset_id);
  if (index >= 0) {
    timeline.assets[index] = {
      ...timeline.assets[index],
      ...deepClone(asset),
      metadata: {
        ...(timeline.assets[index].metadata ?? {}),
        ...(asset.metadata ?? {}),
      },
    };
    return timeline.assets[index];
  }
  timeline.assets.push(asset);
  return asset;
}
