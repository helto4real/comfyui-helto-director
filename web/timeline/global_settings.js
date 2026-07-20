import { ensureStoredPrivacyTokenCookie } from "./privacy.js";

export const GLOBAL_SETTINGS_ROUTE = "/helto_director/global_settings";

export const DEFAULT_GLOBAL_SETTINGS = {
  schema_version: 1,
  storage: {
    asset_root_directory: "",
    effective_asset_root_directory: "ComfyUI output/helto_director_projects",
    default_asset_root_directory: "ComfyUI output/helto_director_projects",
    configured: false,
  },
  timeline: {
    show_resolved_model_output: false,
    allow_gaps: true,
    auto_close_gaps: false,
    minimum_section_duration_seconds: 0.25,
  },
  global_prompt: {
    show_effective_prompt: false,
  },
  audio: {
    always_normalize: false,
  },
  privacy: {
    mode: true,
  },
  display: {
    show_section_labels: true,
    show_thumbnails: true,
    show_audio_waveforms: true,
  },
};

export function normalizeGlobalSettings(value = {}) {
  const source = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const payloadSettings = source.settings && typeof source.settings === "object" && !Array.isArray(source.settings)
    ? source.settings
    : source;
  const storageStatus = source.storage && typeof source.storage === "object" && !Array.isArray(source.storage)
    ? source.storage
    : {};
  const storage = section(payloadSettings, "storage");
  const timeline = section(payloadSettings, "timeline");
  const globalPrompt = section(payloadSettings, "global_prompt");
  const audio = section(payloadSettings, "audio");
  const privacy = section(payloadSettings, "privacy");
  const display = section(payloadSettings, "display");
  const assetRoot = stringValue(storage.asset_root_directory);
  const defaultRoot = stringValue(storageStatus.default_asset_root_directory) || DEFAULT_GLOBAL_SETTINGS.storage.default_asset_root_directory;
  const effectiveRoot = stringValue(storageStatus.effective_asset_root_directory) || assetRoot || defaultRoot;

  return {
    schema_version: 1,
    storage: {
      asset_root_directory: assetRoot,
      effective_asset_root_directory: effectiveRoot,
      default_asset_root_directory: defaultRoot,
      configured: Boolean(storageStatus.configured ?? assetRoot),
    },
    timeline: {
      show_resolved_model_output: Boolean(timeline.show_resolved_model_output),
      allow_gaps: timeline.allow_gaps !== false,
      auto_close_gaps: Boolean(timeline.auto_close_gaps),
      minimum_section_duration_seconds: positiveNumber(timeline.minimum_section_duration_seconds, DEFAULT_GLOBAL_SETTINGS.timeline.minimum_section_duration_seconds),
    },
    global_prompt: {
      show_effective_prompt: Boolean(globalPrompt.show_effective_prompt),
    },
    audio: {
      always_normalize: Boolean(audio.always_normalize),
    },
    privacy: {
      mode: privacy.mode !== false,
    },
    display: {
      show_section_labels: display.show_section_labels !== false,
      show_thumbnails: display.show_thumbnails !== false,
      show_audio_waveforms: display.show_audio_waveforms !== false,
    },
  };
}

export async function fetchGlobalSettings() {
  const response = await fetch(GLOBAL_SETTINGS_ROUTE, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || payload?.ok === false || payload?.error) {
    throw new Error(payload?.error || "Failed to load global settings.");
  }
  return normalizeGlobalSettings(payload);
}

export async function saveGlobalSettings(settings) {
  ensureStoredPrivacyTokenCookie();
  const response = await fetch(GLOBAL_SETTINGS_ROUTE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settingsPayload(settings)),
  });
  const payload = await response.json();
  if (!response.ok || payload?.ok === false || payload?.error) {
    throw new Error(payload?.error || "Failed to save global settings.");
  }
  return normalizeGlobalSettings(payload);
}

export function settingsPayload(settings) {
  const normalized = normalizeGlobalSettings(settings);
  return {
    schema_version: normalized.schema_version,
    storage: {
      asset_root_directory: normalized.storage.asset_root_directory,
    },
    timeline: { ...normalized.timeline },
    global_prompt: { ...normalized.global_prompt },
    audio: { ...normalized.audio },
    privacy: { ...normalized.privacy },
    display: { ...normalized.display },
  };
}

export function isGlobalPrivacyMode(settings) {
  return normalizeGlobalSettings(settings).privacy.mode;
}

export function globalAssetRootLabel(settings) {
  const normalized = normalizeGlobalSettings(settings);
  return normalized.storage.effective_asset_root_directory || normalized.storage.default_asset_root_directory;
}

function section(source, key) {
  const value = source[key];
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function stringValue(value) {
  return value == null ? "" : String(value).trim();
}

function positiveNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}
