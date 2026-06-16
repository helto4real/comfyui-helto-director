import {
  SECTION_TYPE_IMAGE,
  SECTION_TYPE_TEXT,
  SECTION_TYPE_VIDEO,
  ASSET_TYPES,
  ASSET_SOURCE_KINDS,
} from "./schema.js";
import { normalizeVideoTimeline } from "./migration.js";
import { containsEmbeddedMedia } from "./media.js";
import {
  REFERENCE_KIND_CHARACTER,
  areCharacterReferencesEnabled,
  getCharacterReferences,
  parseReferenceTags,
} from "./references.js";

export function createValidationEntry(code, severity, source, scope, itemId, message, hint = "", details = {}) {
  return {
    code,
    severity,
    source,
    scope,
    item_id: itemId,
    message,
    hint,
    details,
  };
}

export function createValidationResult(entries = []) {
  const validation = {
    is_valid: true,
    errors: [],
    warnings: [],
    info: [],
  };
  for (const entry of entries) {
    if (entry.severity === "Error") validation.errors.push(entry);
    else if (entry.severity === "Warning") validation.warnings.push(entry);
    else if (entry.severity === "Info") validation.info.push(entry);
  }
  validation.is_valid = validation.errors.length === 0;
  return validation;
}

export function validateVideoTimeline(timeline) {
  const normalized = normalizeVideoTimeline(timeline);
  const entries = [];
  const duration = asNumber(normalized.project.duration_seconds);
  const assetsById = new Map(normalized.assets.map((asset) => [asset.asset_id, asset]));
  entries.push(...validateAssets(normalized.assets));
  entries.push(...validateCharacterReferences(getCharacterReferences(normalized)));
  const sections = [...normalized.director_track.sections].sort(
    (a, b) => (asNumber(a.start_time) ?? 0) - (asNumber(b.start_time) ?? 0),
  );
  let previousEnd = null;

  for (const section of sections) {
    const start = asNumber(section.start_time);
    const end = asNumber(section.end_time);
    if (start == null || end == null || end <= start) {
      entries.push(createValidationEntry(
        "SECTION_INVALID_TIME_RANGE",
        "Error",
        "Director",
        "Section",
        section.item_id,
        "Section requires a valid start_time and end_time.",
        "Set end_time greater than start_time.",
      ));
    } else if (duration != null && (start < 0 || end > duration)) {
      entries.push(createValidationEntry(
        "SECTION_OUTSIDE_PROJECT_DURATION",
        "Error",
        "Director",
        "Section",
        section.item_id,
        "Section must stay within Project Duration.",
        "Move or trim the section inside the project boundary.",
      ));
    }
    if (previousEnd != null && start != null && start < previousEnd) {
      entries.push(createValidationEntry(
        "DIRECTOR_SECTION_OVERLAP",
        "Error",
        "Director",
        "Section",
        section.item_id,
        "Director Track sections cannot overlap.",
        "Move or trim one section so the Director Track is sequential.",
      ));
    }
    if (end != null) previousEnd = Math.max(previousEnd ?? end, end);

    if (section.type === SECTION_TYPE_TEXT && !String(section.prompt ?? "").trim()) {
      entries.push(createValidationEntry(
        "TEXT_SECTION_EMPTY_PROMPT",
        "Error",
        "Director",
        "Section",
        section.item_id,
        "Text Section requires a non-empty prompt.",
        "Add a prompt or remove the Text Section.",
      ));
    } else if (section.type === SECTION_TYPE_IMAGE && !hasMediaReference(section.image)) {
      entries.push(createValidationEntry(
        "IMAGE_SECTION_MISSING_IMAGE",
        "Error",
        "Director",
        "Section",
        section.item_id,
        "Image Section requires an image.",
        "Choose an image or remove the Image Section.",
      ));
    } else if (section.type === SECTION_TYPE_IMAGE) {
      entries.push(...validateMediaReference(section.image, assetsById, "Section", section.item_id, "IMAGE_SECTION_MEDIA"));
    } else if (section.type === SECTION_TYPE_VIDEO && !hasMediaReference(section.video)) {
      entries.push(createValidationEntry(
        "VIDEO_SECTION_MISSING_VIDEO",
        "Error",
        "Director",
        "Section",
        section.item_id,
        "Video Section requires a video.",
        "Choose a video or remove the Video Section.",
      ));
    } else if (section.type === SECTION_TYPE_VIDEO) {
      entries.push(...validateMediaReference(section.video, assetsById, "Section", section.item_id, "VIDEO_SECTION_MEDIA"));
    }
  }

  entries.push(...validatePromptReferenceTags(sections, getCharacterReferences(normalized), areCharacterReferencesEnabled(normalized)));

  for (const track of normalized.audio_tracks) {
    const lanes = new Map();
    for (const clip of track.clips) {
      const start = asNumber(clip.start_time);
      const end = asNumber(clip.end_time);
      if (start == null || end == null || end <= start) {
        entries.push(createValidationEntry(
          "AUDIO_CLIP_INVALID_TIME_RANGE",
          "Error",
          "Director",
          "AudioClip",
          clip.item_id,
          "Audio Clip requires a valid start_time and end_time.",
          "Set end_time greater than start_time.",
        ));
      } else if (duration != null && (start < 0 || end > duration)) {
        entries.push(createValidationEntry(
          "AUDIO_CLIP_OUTSIDE_PROJECT_DURATION",
          "Error",
          "Director",
          "AudioClip",
          clip.item_id,
          "Audio Clip must stay within Project Duration.",
          "Move or trim the clip inside the project boundary.",
        ));
      }
      if (!hasMediaReference(clip.audio)) {
        entries.push(createValidationEntry(
          "AUDIO_CLIP_MISSING_AUDIO",
          "Error",
          "Director",
          "AudioClip",
          clip.item_id,
          "Audio Clip requires audio.",
          "Choose audio or remove the clip.",
        ));
      } else {
        entries.push(...validateMediaReference(clip.audio, assetsById, "AudioClip", clip.item_id, "AUDIO_CLIP_MEDIA"));
      }
      const lane = Number(clip.lane ?? 0);
      lanes.set(lane, [...(lanes.get(lane) ?? []), clip]);
    }
    for (const [lane, clips] of lanes.entries()) {
      entries.push(...validateAudioLane(track.track_id, lane, clips));
    }
  }

  for (const [index, gap] of detectDirectorGaps(normalized).entries()) {
    entries.push(createValidationEntry(
      "DIRECTOR_GAP",
      "Info",
      "Director",
      "Gap",
      `gap_${String(index + 1).padStart(3, "0")}`,
      "Director Track gap means No Guidance.",
      "This is allowed. Planner nodes may apply model-specific policy later.",
      gap,
    ));
  }

  return createValidationResult(entries);
}

function validateCharacterReferences(references) {
  const entries = [];
  const seenLabels = new Set();
  for (const reference of references) {
    const itemId = reference.id || reference.label || "reference";
    if (seenLabels.has(reference.label)) {
      entries.push(createValidationEntry(
        "CHARACTER_REFERENCE_DUPLICATE_LABEL",
        "Error",
        "Director",
        "CharacterReference",
        itemId,
        "Character reference labels must be unique.",
        "Rename or remove the duplicate reference so prompt tags are unambiguous.",
        { label: reference.label },
      ));
    }
    seenLabels.add(reference.label);
    if (containsEmbeddedMedia(reference) || containsEmbeddedMedia(reference.image)) {
      entries.push(createValidationEntry(
        "CHARACTER_REFERENCE_EMBEDDED_MEDIA_NOT_ALLOWED",
        "Error",
        "Director",
        "CharacterReference",
        itemId,
        "Character references must not embed media, thumbnails, or waveform data in workflow JSON.",
        "Store only a file/source reference and regenerate previews from cache.",
      ));
    }
    if (reference.enabled !== false && !hasMediaReference(reference.image)) {
      entries.push(createValidationEntry(
        "CHARACTER_REFERENCE_MISSING_IMAGE",
        "Error",
        "Director",
        "CharacterReference",
        itemId,
        "Enabled character reference requires an image.",
        "Choose an image, disable the reference, or remove it.",
        { label: reference.label },
      ));
    }
  }
  return entries;
}

function validatePromptReferenceTags(sections, references, referencesEnabled) {
  const entries = [];
  const referencesByLabel = new Map(references.map((reference) => [reference.label, reference]));
  const seenWarnings = new Set();
  for (const section of sections) {
    const prompt = section.prompt ?? "";
    for (const tag of parseReferenceTags(prompt)) {
      if (tag.kind !== REFERENCE_KIND_CHARACTER) continue;
      const reference = referencesByLabel.get(tag.label);
      const key = `${section.item_id}:${tag.token}`;
      if (seenWarnings.has(key)) continue;
      seenWarnings.add(key);
      if (!referencesEnabled) {
        entries.push(createValidationEntry(
          "PROMPT_REFERENCE_DISABLED",
          "Warning",
          "Director",
          "Section",
          section.item_id,
          "Prompt references are currently disabled.",
          "Turn on character references or remove the tag.",
          { token: tag.token, label: tag.label, global_disabled: true },
        ));
      } else if (!reference) {
        entries.push(createValidationEntry(
          "PROMPT_REFERENCE_UNKNOWN",
          "Warning",
          "Director",
          "Section",
          section.item_id,
          "Prompt references a missing character reference.",
          "Add the referenced character image or remove the tag.",
          { token: tag.token, label: tag.label },
        ));
      } else if (reference.enabled === false) {
        entries.push(createValidationEntry(
          "PROMPT_REFERENCE_DISABLED",
          "Warning",
          "Director",
          "Section",
          section.item_id,
          "Prompt references a disabled character reference.",
          "Enable the reference or remove the tag.",
          { token: tag.token, label: tag.label },
        ));
      }
    }
  }
  return entries;
}

function validateAssets(assets) {
  const entries = [];
  const seen = new Set();
  for (const asset of assets) {
    if (seen.has(asset.asset_id)) {
      entries.push(createValidationEntry(
        "ASSET_DUPLICATE_ID",
        "Error",
        "Director",
        "Asset",
        asset.asset_id,
        "Asset IDs must be unique.",
        "Replace or remove the duplicate asset record.",
      ));
    }
    seen.add(asset.asset_id);
    if (!ASSET_TYPES.includes(asset.type)) {
      entries.push(createValidationEntry(
        "ASSET_UNSUPPORTED_TYPE",
        "Error",
        "Director",
        "Asset",
        asset.asset_id,
        "Asset type is not supported.",
        "Use Image, Video, or Audio.",
        { type: asset.type },
      ));
    }
    if (!ASSET_SOURCE_KINDS.includes(asset.source_kind)) {
      entries.push(createValidationEntry(
        "ASSET_UNSUPPORTED_SOURCE_KIND",
        "Error",
        "Director",
        "Asset",
        asset.asset_id,
        "Asset source kind is not supported.",
        "Use FilePath, UploadedFile, Generated, or ComfyUIInput.",
        { source_kind: asset.source_kind },
      ));
    }
    if (containsEmbeddedMedia(asset)) {
      entries.push(createValidationEntry(
        "ASSET_EMBEDDED_MEDIA_NOT_ALLOWED",
        "Error",
        "Director",
        "Asset",
        asset.asset_id,
        "Assets must not embed media, thumbnails, or waveform data in workflow JSON.",
        "Store only a file/source reference and regenerate previews from cache.",
      ));
    }
  }
  return entries;
}

function validateMediaReference(reference, assetsById, scope, itemId, codePrefix) {
  if (containsEmbeddedMedia(reference)) {
    return [createValidationEntry(
      `${codePrefix}_EMBEDDED_MEDIA_NOT_ALLOWED`,
      "Error",
      "Director",
      scope,
      itemId,
      "Media references must not embed media, thumbnails, or waveform data in workflow JSON.",
      "Reference an asset_id or file path instead.",
    )];
  }
  if (reference && typeof reference === "object" && reference.asset_id && !assetsById.has(reference.asset_id)) {
    return [createValidationEntry(
      `${codePrefix}_ASSET_NOT_FOUND`,
      "Error",
      "Director",
      scope,
      itemId,
      "Media reference points to a missing asset record.",
      "Choose the media again or remove the stale reference.",
      { asset_id: reference.asset_id },
    )];
  }
  return [];
}

function validateAudioLane(trackId, lane, clips) {
  const entries = [];
  const sorted = [...clips].sort((a, b) => (asNumber(a.start_time) ?? 0) - (asNumber(b.start_time) ?? 0));
  let previousEnd = null;
  let previousId = null;
  for (const clip of sorted) {
    const start = asNumber(clip.start_time);
    const end = asNumber(clip.end_time);
    if (previousEnd != null && start != null && start < previousEnd) {
      entries.push(createValidationEntry(
        "AUDIO_CLIP_LANE_OVERLAP",
        "Error",
        "Director",
        "AudioClip",
        clip.item_id,
        "Audio Clips cannot overlap within the same lane.",
        "Move one clip to another lane or trim the overlap.",
        { track_id: trackId, lane, previous_item_id: previousId },
      ));
    }
    if (end != null && (previousEnd == null || end > previousEnd)) {
      previousEnd = end;
      previousId = clip.item_id;
    }
  }
  return entries;
}

export function detectDirectorGaps(timeline) {
  const normalized = normalizeVideoTimeline(timeline);
  const duration = Number(normalized.project.duration_seconds);
  const sections = normalized.director_track.sections
    .filter((section) => asNumber(section.start_time) != null && asNumber(section.end_time) != null)
    .sort((a, b) => Number(a.start_time) - Number(b.start_time));

  const gaps = [];
  let cursor = 0.0;
  for (const section of sections) {
    const start = Math.max(0.0, Number(section.start_time));
    const end = Math.max(start, Number(section.end_time));
    if (start > cursor) gaps.push(gap(cursor, Math.min(start, duration)));
    cursor = Math.max(cursor, Math.min(end, duration));
  }
  if (cursor < duration) gaps.push(gap(cursor, duration));
  return gaps.filter((item) => item.duration_seconds > 0);
}

function gap(startTime, endTime) {
  return {
    type: "No Guidance",
    start_time: startTime,
    end_time: endTime,
    duration_seconds: endTime - startTime,
  };
}

function hasMediaReference(value) {
  if (typeof value === "string") return value.trim().length > 0;
  if (value && typeof value === "object") {
    return Boolean(value.asset_id || value.path || value.file_path);
  }
  return value != null;
}

function asNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
