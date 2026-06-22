import {
  SCHEMA_VERSION,
  VIDEO_TIMELINE_TYPE,
  SECTION_TYPE_IMAGE,
  SECTION_TYPE_TEXT,
  SECTION_TYPE_VIDEO,
  ASSET_SOURCE_FILE_PATH,
  ASSET_TYPES,
  ASSET_SOURCE_KINDS,
  BOUNDARY_MODES,
  CROP_MODE_PROJECT_DEFAULT,
  LORA_MERGE_MODES,
  MODEL_LORA_MODEL_LTX_2_3,
  MODEL_LORA_MODEL_WAN_2_2,
  MODEL_LORA_SCHEMA_VERSION,
  MODEL_LORA_TARGET_HIGH_NOISE,
  MODEL_LORA_TARGET_LOW_NOISE,
  MODEL_LORA_TARGET_MAIN,
  SEQUENCE_ID_MAIN,
  SEQUENCE_NAME_MAIN,
  SHOT_TYPES,
  TAKE_STATUSES,
  createDefaultBoundary,
  createDefaultClipInstance,
  createDefaultLoraStack,
  createDefaultProjectModelLoras,
  createDefaultSequence,
  createDefaultShot,
  createDefaultTake,
  createDefaultVideoTimeline,
  deepClone,
} from "./schema.js";
import { normalizeCharacterReferences } from "./references.js";

export function migrateVideoTimeline(value) {
  if (value == null || value === "") {
    return createDefaultVideoTimeline();
  }
  const timeline = typeof value === "string" ? JSON.parse(value) : value;
  if (!timeline || typeof timeline !== "object" || Array.isArray(timeline)) {
    return createDefaultVideoTimeline();
  }
  if (timeline.type !== VIDEO_TIMELINE_TYPE && !timeline.project) {
    return createDefaultVideoTimeline();
  }
  const migrated = deepClone(timeline);
  migrated.schema_version = SCHEMA_VERSION;
  migrated.type = VIDEO_TIMELINE_TYPE;
  return migrated;
}

export function normalizeVideoTimeline(value) {
  const migrated = migrateVideoTimeline(value);
  const normalized = fillMissing(migrated, createDefaultVideoTimeline());
  normalized.assets = normalizeAssets(normalized.assets);
  normalized.director_track = normalizeDirectorTrack(normalized.director_track);
  normalized.audio_tracks = normalizeAudioTracks(normalized.audio_tracks);
  normalized.sequence = normalizeSequence(normalized.sequence, normalized.director_track.sections);
  normalizeProjectMetadata(normalized);
  normalizeProjectModelLoras(normalized);
  normalizePrivacy(normalized);
  normalizeUiStateViewRange(normalized);
  normalizeUiStateSelection(normalized);
  return normalized;
}

function fillMissing(value, defaults) {
  if (defaults && typeof defaults === "object" && !Array.isArray(defaults)) {
    const result = value && typeof value === "object" && !Array.isArray(value)
      ? deepClone(value)
      : {};
    for (const [key, defaultValue] of Object.entries(defaults)) {
      result[key] = key in result ? fillMissing(result[key], defaultValue) : deepClone(defaultValue);
    }
    return result;
  }
  return deepClone(value);
}

function normalizeDirectorTrack(track) {
  const normalized = track && typeof track === "object" && !Array.isArray(track)
    ? deepClone(track)
    : {};
  normalized.track_id ??= "director";
  const sections = Array.isArray(normalized.sections) ? normalized.sections : [];
  normalized.sections = sections
    .filter((section) => section && typeof section === "object" && !Array.isArray(section))
    .map(normalizeSection);
  return normalized;
}

function normalizeAssets(assets) {
  if (!Array.isArray(assets)) return [];
  return assets
    .filter((asset) => asset && typeof asset === "object" && !Array.isArray(asset))
    .map((asset, index) => {
      const normalized = deepClone(asset);
      normalized.asset_id ??= `asset_${String(index + 1).padStart(3, "0")}`;
      if (!ASSET_TYPES.includes(normalized.type)) normalized.type = "Image";
      if (!ASSET_SOURCE_KINDS.includes(normalized.source_kind)) normalized.source_kind = ASSET_SOURCE_FILE_PATH;
      normalized.path ??= normalized.file_path ?? null;
      normalized.name ??= basename(normalized.path ?? normalized.file_path ?? "");
      normalized.mime_type ??= "";
      normalized.size_bytes ??= null;
      normalized.metadata = normalized.metadata && typeof normalized.metadata === "object" && !Array.isArray(normalized.metadata)
        ? normalized.metadata
        : {};
      return normalized;
    });
}

function normalizeProjectMetadata(timeline) {
  const project = timeline.project ??= {};
  project.metadata = project.metadata && typeof project.metadata === "object" && !Array.isArray(project.metadata)
    ? project.metadata
    : {};
  project.metadata.character_references_enabled = project.metadata.character_references_enabled !== false;
  project.metadata.character_references = normalizeCharacterReferences(project.metadata.character_references);
}

function normalizeProjectModelLoras(timeline) {
  const project = timeline.project ??= {};
  const modelLoras = project.model_loras && typeof project.model_loras === "object" && !Array.isArray(project.model_loras)
    ? project.model_loras
    : {};
  const globalLoras = modelLoras.global && typeof modelLoras.global === "object" && !Array.isArray(modelLoras.global)
    ? modelLoras.global
    : {};
  project.model_loras = {
    schema_version: MODEL_LORA_SCHEMA_VERSION,
    global: normalizeProjectLoraTargets(globalLoras),
  };
}

function normalizeProjectLoraTargets(targets) {
  const ltx = targets[MODEL_LORA_MODEL_LTX_2_3] && typeof targets[MODEL_LORA_MODEL_LTX_2_3] === "object" && !Array.isArray(targets[MODEL_LORA_MODEL_LTX_2_3])
    ? targets[MODEL_LORA_MODEL_LTX_2_3]
    : {};
  const wan = targets[MODEL_LORA_MODEL_WAN_2_2] && typeof targets[MODEL_LORA_MODEL_WAN_2_2] === "object" && !Array.isArray(targets[MODEL_LORA_MODEL_WAN_2_2])
    ? targets[MODEL_LORA_MODEL_WAN_2_2]
    : {};
  return {
    [MODEL_LORA_MODEL_LTX_2_3]: {
      [MODEL_LORA_TARGET_MAIN]: normalizeTimelineLoraConfig(ltx[MODEL_LORA_TARGET_MAIN]),
    },
    [MODEL_LORA_MODEL_WAN_2_2]: {
      [MODEL_LORA_TARGET_HIGH_NOISE]: normalizeTimelineLoraConfig(wan[MODEL_LORA_TARGET_HIGH_NOISE]),
      [MODEL_LORA_TARGET_LOW_NOISE]: normalizeTimelineLoraConfig(wan[MODEL_LORA_TARGET_LOW_NOISE]),
    },
  };
}

function normalizeTimelineLoraConfig(config) {
  const source = config && typeof config === "object" && !Array.isArray(config) ? config : createDefaultLoraStack();
  const ui = source.ui && typeof source.ui === "object" && !Array.isArray(source.ui) ? source.ui : {};
  const loras = Array.isArray(source.loras)
    ? source.loras
      .filter((lora) => lora && typeof lora === "object" && !Array.isArray(lora) && lora.enabled !== false && lora.name)
      .map((lora) => ({
        enabled: true,
        name: String(lora.name),
        strength_model: Number(lora.strength_model ?? lora.strength ?? 1),
        strength_clip: Number(lora.strength_clip ?? lora.strength_model ?? lora.strength ?? 1),
      }))
      .filter((lora) => Number.isFinite(lora.strength_model) && Number.isFinite(lora.strength_clip) && (lora.strength_model !== 0 || lora.strength_clip !== 0))
    : [];
  return {
    version: 1,
    loras,
    ui: {
      show_strengths: String(source.show_strengths || ui.show_strengths || "single"),
      match: String(source.match || ui.match || ""),
    },
  };
}

const SECTION_SHOT_TOUCH_TOLERANCE_SECONDS = 1e-6;

function normalizeSequence(sequence, sections = []) {
  const normalized = fillMissing(
    sequence && typeof sequence === "object" && !Array.isArray(sequence) ? sequence : {},
    createDefaultSequence(),
  );
  normalized.sequence_id = String(normalized.sequence_id || SEQUENCE_ID_MAIN);
  normalized.name = String(normalized.name || SEQUENCE_NAME_MAIN);
  const shots = Array.isArray(normalized.shots) ? normalized.shots : [];
  const shotItems = shots
    .filter((shot) => shot && typeof shot === "object" && !Array.isArray(shot));
  if (!shotItems.length && Array.isArray(sections) && sections.length) {
    normalized.shots = deriveShotsFromSections(sections);
    normalized.boundaries = deriveBoundariesFromSections(sections, normalized.shots);
  } else {
    normalized.shots = shotItems.map((shot, index) => normalizeShot(shot, index));
    const boundaries = Array.isArray(normalized.boundaries) ? normalized.boundaries : [];
    normalized.boundaries = boundaries
      .filter((boundary) => boundary && typeof boundary === "object" && !Array.isArray(boundary))
      .map((boundary, index) => normalizeBoundary(boundary, index));
  }
  return normalized;
}

function deriveShotsFromSections(sections) {
  const usedShotIds = new Set();
  return sections.map((section, index) => {
    const fallbackSectionId = `section_${String(index + 1).padStart(3, "0")}`;
    const sectionId = section.item_id == null || section.item_id === ""
      ? fallbackSectionId
      : String(section.item_id);
    const shotId = uniqueTimelineId(
      `shot_${sanitizeTimelineId(sectionId, fallbackSectionId)}`,
      usedShotIds,
    );
    return normalizeShot({
      ...createDefaultShot(index + 1),
      shot_id: shotId,
      start_time: section.start_time,
      end_time: section.end_time,
      section_ids: [sectionId],
    }, index);
  });
}

function deriveBoundariesFromSections(sections, shots) {
  const usedBoundaryIds = new Set();
  const boundaries = [];
  const count = Math.max(0, Math.min(sections.length, shots.length) - 1);
  for (let index = 0; index < count; index += 1) {
    const leftEnd = asNumber(sections[index]?.end_time, null);
    const rightStart = asNumber(sections[index + 1]?.start_time, null);
    if (leftEnd == null || rightStart == null) continue;
    if (Math.abs(leftEnd - rightStart) > SECTION_SHOT_TOUCH_TOLERANCE_SECONDS) continue;
    const leftShotId = shots[index].shot_id;
    const rightShotId = shots[index + 1].shot_id;
    const boundaryId = uniqueTimelineId(
      `boundary_${leftShotId}_to_${rightShotId}`,
      usedBoundaryIds,
    );
    boundaries.push(normalizeBoundary({
      ...createDefaultBoundary(boundaries.length + 1),
      boundary_id: boundaryId,
      left_shot_id: leftShotId,
      right_shot_id: rightShotId,
      mode: "Hard Cut",
    }, boundaries.length));
  }
  return boundaries;
}

function sanitizeTimelineId(value, fallback) {
  const sanitized = String(value ?? "").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
  return sanitized || fallback;
}

function uniqueTimelineId(baseId, usedIds) {
  let candidate = baseId;
  let suffix = 2;
  while (usedIds.has(candidate)) {
    candidate = `${baseId}_${suffix}`;
    suffix += 1;
  }
  usedIds.add(candidate);
  return candidate;
}

function normalizeShot(shot, index) {
  const normalized = fillMissing(shot, createDefaultShot(index + 1));
  normalized.shot_id = String(normalized.shot_id || `shot_${String(index + 1).padStart(3, "0")}`);
  normalized.name = String(normalized.name || "");
  if (!SHOT_TYPES.includes(normalized.type)) normalized.type = createDefaultShot(index + 1).type;
  normalized.start_time = asNumber(normalized.start_time, 0);
  normalized.end_time = asNumber(normalized.end_time, normalized.start_time);
  const sectionIds = Array.isArray(normalized.section_ids) ? normalized.section_ids : [];
  normalized.section_ids = sectionIds.filter((sectionId) => sectionId != null).map(String);
  normalized.lora_overrides = normalizeShotLoraOverrides(normalized.lora_overrides);
  const takes = Array.isArray(normalized.takes) ? normalized.takes : [];
  normalized.takes = takes
    .filter((take) => take && typeof take === "object" && !Array.isArray(take))
    .map((take, takeIndex) => normalizeTake(take, takeIndex));
  normalized.accepted_take_id = normalized.accepted_take_id == null ? null : String(normalized.accepted_take_id);
  normalized.clip_instance = normalizeClipInstance(normalized.clip_instance);
  normalized.metadata = normalized.metadata && typeof normalized.metadata === "object" && !Array.isArray(normalized.metadata)
    ? normalized.metadata
    : {};
  return normalized;
}

function normalizeBoundary(boundary, index) {
  const normalized = fillMissing(boundary, createDefaultBoundary(index + 1));
  normalized.boundary_id = String(normalized.boundary_id || `boundary_${String(index + 1).padStart(3, "0")}`);
  normalized.left_shot_id = normalized.left_shot_id == null ? null : String(normalized.left_shot_id);
  normalized.right_shot_id = normalized.right_shot_id == null ? null : String(normalized.right_shot_id);
  if (!BOUNDARY_MODES.includes(normalized.mode)) normalized.mode = createDefaultBoundary(index + 1).mode;
  normalized.tail_frames = asInteger(normalized.tail_frames, 5);
  normalized.blend_frames = asInteger(normalized.blend_frames, 3);
  normalized.transition_prompt = String(normalized.transition_prompt || "");
  normalized.reuse_character_refs = normalized.reuse_character_refs !== false;
  normalized.reuse_style = normalized.reuse_style !== false;
  normalized.metadata = normalized.metadata && typeof normalized.metadata === "object" && !Array.isArray(normalized.metadata)
    ? normalized.metadata
    : {};
  return normalized;
}

function normalizeTake(take, index) {
  const normalized = fillMissing(take, createDefaultTake(index + 1));
  normalized.take_id = String(normalized.take_id || `take_${String(index + 1).padStart(3, "0")}`);
  if (!TAKE_STATUSES.includes(normalized.status)) normalized.status = createDefaultTake(index + 1).status;
  normalized.asset_id = normalized.asset_id ?? null;
  normalized.seed = normalized.seed ?? null;
  normalized.model_family = String(normalized.model_family || "");
  normalized.model_version = String(normalized.model_version || "");
  normalized.plan_hash = String(normalized.plan_hash || "");
  normalized.prompt_hash = String(normalized.prompt_hash || "");
  normalized.resolved_loras = normalized.resolved_loras ?? null;
  normalized.metadata = normalized.metadata && typeof normalized.metadata === "object" && !Array.isArray(normalized.metadata)
    ? normalized.metadata
    : {};
  return normalized;
}

function normalizeClipInstance(clipInstance) {
  if (clipInstance == null) return null;
  const normalized = fillMissing(
    clipInstance && typeof clipInstance === "object" && !Array.isArray(clipInstance) ? clipInstance : {},
    createDefaultClipInstance(),
  );
  normalized.asset_id = normalized.asset_id == null ? null : String(normalized.asset_id);
  normalized.source_in = asNumber(normalized.source_in, 0);
  normalized.source_out = normalized.source_out == null ? null : asNumber(normalized.source_out, null);
  normalized.speed = asNumber(normalized.speed, 1);
  normalized.enabled = normalized.enabled !== false;
  return normalized;
}

function normalizeShotLoraOverrides(overrides) {
  const normalized = overrides && typeof overrides === "object" && !Array.isArray(overrides)
    ? deepClone(overrides)
    : {};
  normalized.enabled = Boolean(normalized.enabled);
  if (!LORA_MERGE_MODES.includes(normalized.merge_mode)) normalized.merge_mode = "Inherit Global";
  normalized.targets = normalizeOptionalLoraTargets(normalized.targets);
  return normalized;
}

function normalizeOptionalLoraTargets(targets) {
  if (!targets || typeof targets !== "object" || Array.isArray(targets)) return {};
  const normalized = {};
  const ltx = targets[MODEL_LORA_MODEL_LTX_2_3];
  if (ltx && typeof ltx === "object" && !Array.isArray(ltx) && MODEL_LORA_TARGET_MAIN in ltx) {
    normalized[MODEL_LORA_MODEL_LTX_2_3] = {
      [MODEL_LORA_TARGET_MAIN]: normalizeTimelineLoraConfig(ltx[MODEL_LORA_TARGET_MAIN]),
    };
  }
  const wan = targets[MODEL_LORA_MODEL_WAN_2_2];
  const wanTargets = {};
  if (wan && typeof wan === "object" && !Array.isArray(wan)) {
    if (MODEL_LORA_TARGET_HIGH_NOISE in wan) {
      wanTargets[MODEL_LORA_TARGET_HIGH_NOISE] = normalizeTimelineLoraConfig(wan[MODEL_LORA_TARGET_HIGH_NOISE]);
    }
    if (MODEL_LORA_TARGET_LOW_NOISE in wan) {
      wanTargets[MODEL_LORA_TARGET_LOW_NOISE] = normalizeTimelineLoraConfig(wan[MODEL_LORA_TARGET_LOW_NOISE]);
    }
  }
  if (Object.keys(wanTargets).length) normalized[MODEL_LORA_MODEL_WAN_2_2] = wanTargets;
  return normalized;
}

function normalizeSection(section, index) {
  const normalized = deepClone(section);
  normalized.item_id ??= `section_${String(index + 1).padStart(3, "0")}`;
  normalized.start_time ??= 0.0;
  normalized.end_time ??= normalized.start_time;
  if (normalized.type === SECTION_TYPE_IMAGE) {
    normalized.image ??= null;
    normalized.prompt ??= "";
    normalized.guide_strength ??= 1.0;
    normalized.crop_mode ??= CROP_MODE_PROJECT_DEFAULT;
  } else if (normalized.type === SECTION_TYPE_TEXT) {
    normalized.prompt ??= "";
  } else if (normalized.type === SECTION_TYPE_VIDEO) {
    normalized.video ??= null;
    normalized.prompt ??= "";
    normalized.guide_strength ??= 1.0;
    normalized.crop_mode ??= CROP_MODE_PROJECT_DEFAULT;
    normalized.source_in ??= 0.0;
    normalized.source_out ??= null;
    normalized.timing_mode ??= "Fit to Section";
    normalized.video_guidance_range ??= "Last Frames";
    normalized.video_guidance_frame_count ??= 17;
  }
  return normalized;
}

function normalizeAudioTracks(audioTracks) {
  if (!Array.isArray(audioTracks)) return [];
  return audioTracks
    .filter((track) => track && typeof track === "object" && !Array.isArray(track))
    .map((track, trackIndex) => {
      const normalized = deepClone(track);
      normalized.track_id ??= `audio_track_${String(trackIndex + 1).padStart(3, "0")}`;
      const clips = Array.isArray(normalized.clips) ? normalized.clips : [];
      normalized.clips = clips
        .filter((clip) => clip && typeof clip === "object" && !Array.isArray(clip))
        .map((clip, clipIndex) => normalizeAudioClip(clip, clipIndex));
      return normalized;
    });
}

function normalizeAudioClip(clip, index) {
  const normalized = deepClone(clip);
  normalized.item_id ??= `audio_clip_${String(index + 1).padStart(3, "0")}`;
  normalized.audio ??= null;
  normalized.start_time ??= 0.0;
  normalized.end_time ??= normalized.start_time;
  normalized.source_in ??= 0.0;
  normalized.source_out ??= null;
  normalized.volume ??= 100.0;
  normalized.normalization ??= {};
  normalized.fade_in ??= 0.0;
  normalized.fade_out ??= 0.0;
  normalized.enabled ??= true;
  normalized.locked ??= false;
  normalized.name ??= "";
  normalized.lane ??= 0;
  return normalized;
}

function normalizePrivacy(timeline) {
  const project = timeline.project ??= {};
  const privacy = project.privacy && typeof project.privacy === "object" && !Array.isArray(project.privacy)
    ? project.privacy
    : {};
  project.privacy = {
    mode: Boolean(
      privacy.mode ||
      privacy.hide_media_previews ||
      privacy.hide_text_prompts ||
      privacy.encrypt_previews,
    ),
  };
}

function normalizeUiStateViewRange(timeline) {
  const uiState = timeline.ui_state ??= {};
  const rawDuration = Number(timeline?.project?.duration_seconds ?? 5);
  const duration = Number.isFinite(rawDuration) ? rawDuration : 5;
  const projectSeconds = Math.max(1, Math.ceil(Math.max(0.25, duration)));
  let start = Math.round(Number(uiState.view_start_seconds));
  let end = Math.round(Number(uiState.view_end_seconds));
  if (!Number.isFinite(start)) start = 0;
  if (!Number.isFinite(end)) end = projectSeconds;
  start = Math.max(0, Math.min(start, Math.max(0, projectSeconds - 1)));
  end = Math.max(start + 1, Math.min(end, projectSeconds));
  uiState.view_start_seconds = start;
  uiState.view_end_seconds = end;
}

function normalizeUiStateSelection(timeline) {
  const uiState = timeline.ui_state ??= {};
  const existing = new Set([
    ...(timeline.director_track?.sections ?? []).map((section) => section.item_id),
    ...(timeline.audio_tracks ?? []).flatMap((track) => (track.clips ?? []).map((clip) => clip.item_id)),
  ].filter(Boolean));
  const rawIds = Array.isArray(uiState.selected_item_ids) && uiState.selected_item_ids.length
    ? uiState.selected_item_ids
    : (uiState.selected_item_id ? [uiState.selected_item_id] : []);
  const selected = [];
  for (const rawId of rawIds) {
    const id = String(rawId ?? "");
    if (!id || !existing.has(id) || selected.includes(id)) continue;
    selected.push(id);
  }
  const primaryValue = uiState.selected_item_id == null ? null : String(uiState.selected_item_id);
  const primary = primaryValue && existing.has(primaryValue)
    ? primaryValue
    : selected.at(-1);
  if (primary && selected.includes(primary)) {
    selected.splice(selected.indexOf(primary), 1);
    selected.push(primary);
  }
  uiState.selected_item_ids = selected;
  uiState.selected_item_id = selected.at(-1) ?? null;
}

function asNumber(value, fallback) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : fallback;
}

function asInteger(value, fallback) {
  const numberValue = Number.parseInt(value, 10);
  return Number.isFinite(numberValue) ? numberValue : fallback;
}

function basename(path) {
  return String(path ?? "").split(/[\\/]/).filter(Boolean).pop() ?? "";
}
