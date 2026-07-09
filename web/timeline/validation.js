import {
  SECTION_TYPE_IMAGE,
  SECTION_TYPE_TEXT,
  SECTION_TYPE_VIDEO,
  ASSET_TYPES,
  ASSET_SOURCE_KINDS,
  BOUNDARY_MODES,
  LORA_MERGE_MODES,
  MODEL_LORA_TARGET_DESCRIPTORS,
} from "./schema.js";
import { migrateVideoTimeline, normalizeVideoTimeline } from "./migration.js";
import { normalizeGlobalSettings } from "./global_settings.js";
import { containsEmbeddedMedia } from "./media.js";
import {
  REFERENCE_KIND_CHARACTER,
  areCharacterReferencesEnabled,
  getCharacterReferences,
  parseReferenceTags,
} from "./references.js";

const VALID_MODEL_LORA_TARGETS = Object.fromEntries(
  Object.entries(MODEL_LORA_TARGET_DESCRIPTORS).map(([modelKey, descriptor]) => [
    modelKey,
    new Set(Object.keys(descriptor.targets)),
  ]),
);

const RESOLVED_LORA_TARGETS_BY_FAMILY = Object.fromEntries(
  Object.values(MODEL_LORA_TARGET_DESCRIPTORS).flatMap((descriptor) => (
    descriptor.familyAliases.map((familyAlias) => [familyAlias, new Set(Object.keys(descriptor.targets))])
  )),
);

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

export function validateVideoTimeline(timeline, globalSettings = null) {
  const migrated = migrateVideoTimeline(timeline);
  const normalized = normalizeVideoTimeline(migrated);
  const settings = normalizeGlobalSettings(globalSettings);
  const entries = [];
  const duration = asNumber(normalized.project.duration_seconds);
  const assetsById = new Map(normalized.assets.map((asset) => [asset.asset_id, asset]));
  const rawSequence = rawObject(migrated.sequence);
  const rawModelLoras = rawObject(rawObject(migrated.project).model_loras);
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
    } else if (end - start < settings.timeline.minimum_section_duration_seconds) {
      entries.push(createValidationEntry(
        "SECTION_BELOW_MINIMUM_DURATION",
        "Error",
        "Director",
        "Section",
        section.item_id,
        "Section is shorter than the global minimum duration.",
        "Extend the section or lower Minimum Section Duration in Global Settings.",
        {
          minimum_section_duration_seconds: settings.timeline.minimum_section_duration_seconds,
          duration_seconds: end - start,
        },
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
  entries.push(...validateProjectModelLoras(normalized.project.model_loras, rawModelLoras));
  entries.push(...validateSequence(normalized.sequence, rawSequence, duration, assetsById, normalized.director_track.sections, normalized.project.model_loras));

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
    const allowGaps = settings.timeline.allow_gaps;
    const autoCloseGaps = settings.timeline.auto_close_gaps;
    entries.push(createValidationEntry(
      "DIRECTOR_GAP",
      allowGaps ? "Info" : "Error",
      "Director",
      "Gap",
      `gap_${String(index + 1).padStart(3, "0")}`,
      allowGaps ? "Director Track gap means No Guidance." : "Director Track gaps are disabled in Global Settings.",
      allowGaps ? "This is allowed. Planner nodes may apply model-specific policy later." : "Close the gap or turn on Allow Gaps in Global Settings.",
      {
        ...gap,
        allow_gaps: allowGaps,
        auto_close_gaps: autoCloseGaps,
      },
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

function validateProjectModelLoras(modelLoras, rawModelLoras) {
  const entries = [];
  const rawGlobal = rawObject(rawModelLoras.global);
  for (const [modelKey, targets] of Object.entries(rawGlobal)) {
    if (!VALID_MODEL_LORA_TARGETS[modelKey]) {
      entries.push(createValidationEntry(
        "MODEL_LORA_MODEL_TARGET_INVALID",
        "Error",
        "Director",
        "ProjectLoRA",
        String(modelKey),
        "Project LoRA model target is not supported.",
        "Use the model-targeted LoRA keys defined by the timeline schema.",
        { model: modelKey },
      ));
      continue;
    }
    if (!targets || typeof targets !== "object" || Array.isArray(targets)) {
      entries.push(createValidationEntry(
        "MODEL_LORA_TARGETS_INVALID",
        "Error",
        "Director",
        "ProjectLoRA",
        String(modelKey),
        "Project LoRA targets must be an object.",
        "Use named target stacks for this model.",
        { model: modelKey },
      ));
      continue;
    }
    for (const [targetKey, stack] of Object.entries(targets)) {
      if (!VALID_MODEL_LORA_TARGETS[modelKey].has(targetKey)) {
        entries.push(createValidationEntry(
          "MODEL_LORA_TARGET_INVALID",
          "Error",
          "Director",
          "ProjectLoRA",
          `${modelKey}.${targetKey}`,
          "Project LoRA target is not supported for this model.",
          "Use only the target names defined by the timeline schema.",
          { model: modelKey, target: targetKey },
        ));
      }
      entries.push(...validateLoraPayload(stack, "ProjectLoRA", `${modelKey}.${targetKey}`, "MODEL_LORA_STACK"));
    }
  }

  for (const [modelKey, targets] of Object.entries(rawObject(modelLoras.global))) {
    for (const [targetKey, stack] of Object.entries(rawObject(targets))) {
      entries.push(...validateLoraPayload(stack, "ProjectLoRA", `${modelKey}.${targetKey}`, "MODEL_LORA_STACK"));
    }
  }
  return entries;
}

function validateSequence(sequence, rawSequence, duration, assetsById, sections, modelLoras) {
  const rawShots = Array.isArray(rawSequence.shots)
    ? rawSequence.shots.filter((shot) => shot && typeof shot === "object" && !Array.isArray(shot))
    : [];
  const rawBoundaries = Array.isArray(rawSequence.boundaries)
    ? rawSequence.boundaries.filter((boundary) => boundary && typeof boundary === "object" && !Array.isArray(boundary))
    : [];
  return [
    ...validateRawSequenceModes(rawShots, rawBoundaries),
    ...validateShots(sequence.shots ?? [], duration, assetsById, sections),
    ...validateBoundaries(sequence.boundaries ?? [], sequence.shots ?? [], modelLoras),
  ];
}

function validateRawSequenceModes(rawShots, rawBoundaries) {
  const entries = [];
  rawShots.forEach((shot, index) => {
    const shotId = String(shot.shot_id || `shot_${String(index + 1).padStart(3, "0")}`);
    const overrides = shot.lora_overrides;
    if (overrides && typeof overrides === "object" && !Array.isArray(overrides)) {
      if (overrides.merge_mode != null && !LORA_MERGE_MODES.includes(overrides.merge_mode)) {
        entries.push(createValidationEntry(
          "SHOT_LORA_MERGE_MODE_INVALID",
          "Error",
          "Director",
          "Shot",
          shotId,
          "Shot LoRA merge mode is not supported.",
          "Use Inherit Global, Add To Global, Replace Global, or Disable LoRAs.",
          { merge_mode: overrides.merge_mode },
        ));
      }
      entries.push(...validateLoraTargetTree(overrides.targets, "Shot", shotId, "SHOT_LORA"));
    }
    const takes = Array.isArray(shot.takes) ? shot.takes : [];
    for (const take of takes) {
      if (!take || typeof take !== "object" || Array.isArray(take)) continue;
      const status = take.status;
      if (status != null && !["Candidate", "Accepted", "Rejected"].includes(status)) {
        entries.push(createValidationEntry(
          "TAKE_STATUS_INVALID",
          "Error",
          "Director",
          "Take",
          String(take.take_id || "take"),
          "Take status is not supported.",
          "Use Candidate, Accepted, or Rejected.",
          { status, shot_id: shotId },
        ));
      }
    }
  });
  rawBoundaries.forEach((boundary, index) => {
    const mode = boundary.mode;
    if (mode != null && !BOUNDARY_MODES.includes(mode)) {
      entries.push(createValidationEntry(
        "BOUNDARY_MODE_INVALID",
        "Error",
        "Director",
        "Boundary",
        String(boundary.boundary_id || `boundary_${String(index + 1).padStart(3, "0")}`),
        "Boundary mode is not supported.",
        "Use one of the generic boundary modes defined by the timeline schema.",
        { mode },
      ));
    }
  });
  return entries;
}

function validateLoraTargetTree(targets, scope, itemId, codePrefix) {
  const entries = [];
  if (targets == null) return entries;
  if (!targets || typeof targets !== "object" || Array.isArray(targets)) {
    return [createValidationEntry(
      `${codePrefix}_TARGETS_INVALID`,
      "Error",
      "Director",
      scope,
      itemId,
      "LoRA targets must be an object.",
      "Use model keys with named target stacks.",
    )];
  }
  for (const [modelKey, modelTargets] of Object.entries(targets)) {
    if (!VALID_MODEL_LORA_TARGETS[modelKey]) {
      entries.push(createValidationEntry(
        `${codePrefix}_MODEL_TARGET_INVALID`,
        "Error",
        "Director",
        scope,
        itemId,
        "LoRA model target is not supported.",
        "Use the model-targeted LoRA keys defined by the timeline schema.",
        { model: modelKey },
      ));
      continue;
    }
    if (!modelTargets || typeof modelTargets !== "object" || Array.isArray(modelTargets)) {
      entries.push(createValidationEntry(
        `${codePrefix}_TARGETS_INVALID`,
        "Error",
        "Director",
        scope,
        itemId,
        "LoRA model targets must be an object.",
        "Use named target stacks for this model.",
        { model: modelKey },
      ));
      continue;
    }
    for (const [targetKey, stack] of Object.entries(modelTargets)) {
      if (!VALID_MODEL_LORA_TARGETS[modelKey].has(targetKey)) {
        entries.push(createValidationEntry(
          `${codePrefix}_TARGET_INVALID`,
          "Error",
          "Director",
          scope,
          itemId,
          "LoRA target is not supported for this model.",
          "Use only the target names defined by the timeline schema.",
          { model: modelKey, target: targetKey },
        ));
      }
      entries.push(...validateLoraPayload(stack, scope, itemId, `${codePrefix}_STACK`));
    }
  }
  return entries;
}

function validateShots(shots, duration, assetsById, sections) {
  const entries = [];
  const seenShotIds = new Set();
  const assignedSectionIds = new Map();
  const sectionIds = new Set(sections.map((section) => section.item_id));
  const sortedShots = [...shots].sort((a, b) => (asNumber(a.start_time) ?? 0) - (asNumber(b.start_time) ?? 0));
  let previousEnd = null;
  let previousId = null;
  for (const shot of sortedShots) {
    const shotId = String(shot.shot_id || "shot");
    const start = asNumber(shot.start_time);
    const end = asNumber(shot.end_time);
    if (seenShotIds.has(shotId)) {
      entries.push(createValidationEntry(
        "SHOT_DUPLICATE_ID",
        "Error",
        "Director",
        "Shot",
        shotId,
        "Shot IDs must be unique.",
        "Rename or remove the duplicate shot.",
      ));
    }
    seenShotIds.add(shotId);
    if (start == null || end == null || end <= start) {
      entries.push(createValidationEntry(
        "SHOT_INVALID_TIME_RANGE",
        "Error",
        "Director",
        "Shot",
        shotId,
        "Shot requires a valid start_time and end_time.",
        "Set end_time greater than start_time.",
        { start_time: shot.start_time, end_time: shot.end_time },
      ));
    } else if (duration != null && (start < 0 || end > duration)) {
      entries.push(createValidationEntry(
        "SHOT_OUTSIDE_PROJECT_DURATION",
        "Error",
        "Director",
        "Shot",
        shotId,
        "Shot must stay within Project Duration.",
        "Move or trim the shot inside the project boundary.",
        { duration_seconds: duration, start_time: start, end_time: end },
      ));
    }
    if (previousEnd != null && start != null && start < previousEnd) {
      entries.push(createValidationEntry(
        "SHOT_OVERLAP",
        "Error",
        "Director",
        "Shot",
        shotId,
        "Shots cannot overlap.",
        "Move or trim one shot so the sequence is sequential.",
        { previous_shot_id: previousId },
      ));
    }
    if (end != null && (previousEnd == null || end > previousEnd)) {
      previousEnd = end;
      previousId = shotId;
    }
    for (const sectionId of shot.section_ids ?? []) {
      if (!sectionIds.has(sectionId)) {
        entries.push(createValidationEntry(
          "SHOT_SECTION_NOT_FOUND",
          "Error",
          "Director",
          "Shot",
          shotId,
          "Shot references a missing Director section.",
          "Assign the shot to an existing section or remove the stale section ID.",
          { section_id: sectionId },
        ));
      } else if (assignedSectionIds.has(sectionId) && assignedSectionIds.get(sectionId) !== shotId) {
        entries.push(createValidationEntry(
          "SECTION_ASSIGNED_TO_MULTIPLE_SHOTS",
          "Error",
          "Director",
          "Shot",
          shotId,
          "Director section is assigned to more than one shot.",
          "Keep each section attached to one shot until multi-shot section semantics are defined.",
          { section_id: sectionId, previous_shot_id: assignedSectionIds.get(sectionId) },
        ));
      } else {
        assignedSectionIds.set(sectionId, shotId);
      }
    }
    entries.push(...validateShotMediaAndTakes(shot, assetsById));
  }
  return entries;
}

function validateShotMediaAndTakes(shot, assetsById) {
  const entries = [];
  const shotId = String(shot.shot_id || "shot");
  const clipInstance = shot.clip_instance;
  if (clipInstance?.asset_id && !assetsById.has(clipInstance.asset_id)) {
    entries.push(createValidationEntry(
      "SHOT_CLIP_INSTANCE_ASSET_NOT_FOUND",
      "Error",
      "Director",
      "Shot",
      shotId,
      "Shot clip instance points to a missing asset record.",
      "Choose the clip again or remove the stale asset reference.",
      { asset_id: clipInstance.asset_id },
    ));
  }
  if (shot.type === "Imported" && !clipInstance?.asset_id) {
    entries.push(createValidationEntry(
      "IMPORTED_SHOT_MISSING_CLIP_ASSET",
      "Warning",
      "Director",
      "Shot",
      shotId,
      "Imported shot does not point to a clip asset.",
      "Attach an imported clip before using this shot as media.",
    ));
  }
  if (["Generated", "Extended", "Edited"].includes(shot.type) && !(shot.section_ids ?? []).length) {
    entries.push(createValidationEntry(
      "SHOT_MISSING_INTENT",
      "Warning",
      "Director",
      "Shot",
      shotId,
      "Generated or edited shot has no section intent.",
      "Assign at least one Director section or keep the shot as a placeholder/import.",
    ));
  }
  const takeIds = new Set();
  for (const take of shot.takes ?? []) {
    const takeId = String(take.take_id || "take");
    if (takeIds.has(takeId)) {
      entries.push(createValidationEntry(
        "TAKE_DUPLICATE_ID",
        "Error",
        "Director",
        "Take",
        takeId,
        "Take IDs must be unique within a shot.",
        "Rename or remove the duplicate take.",
        { shot_id: shotId },
      ));
    }
    takeIds.add(takeId);
    if (take.asset_id != null && !assetsById.has(take.asset_id)) {
      entries.push(createValidationEntry(
        "TAKE_ASSET_NOT_FOUND",
        "Error",
        "Director",
        "Take",
        takeId,
        "Take points to a missing asset record.",
        "Choose the output asset again or remove the stale take asset reference.",
        { shot_id: shotId, asset_id: take.asset_id },
      ));
    }
    entries.push(...validateTakeResolvedLoras(take, shotId));
  }
  if (shot.accepted_take_id != null && !takeIds.has(shot.accepted_take_id)) {
    entries.push(createValidationEntry(
      "SHOT_ACCEPTED_TAKE_NOT_FOUND",
      "Error",
      "Director",
      "Shot",
      shotId,
      "Shot accepted_take_id does not match one of its takes.",
      "Accept an existing take or clear the accepted take.",
      { accepted_take_id: shot.accepted_take_id },
    ));
  }
  return entries;
}

function validateTakeResolvedLoras(take, shotId) {
  const entries = [];
  const resolved = take.resolved_loras;
  if (resolved == null) return entries;
  const takeId = String(take.take_id || "take");
  if (!resolved || typeof resolved !== "object" || Array.isArray(resolved)) {
    return [createValidationEntry(
      "TAKE_RESOLVED_LORAS_INVALID",
      "Error",
      "Director",
      "Take",
      takeId,
      "Take resolved_loras must be an object when present.",
      "Store the resolved model family, version, and target LoRA rows.",
      { shot_id: shotId },
    )];
  }
  if (containsEmbeddedMedia(resolved)) {
    entries.push(createValidationEntry(
      "TAKE_RESOLVED_LORAS_EMBEDDED_MEDIA_NOT_ALLOWED",
      "Error",
      "Director",
      "Take",
      takeId,
      "Take resolved_loras must not embed media or preview payloads.",
      "Store only LoRA names and numeric strengths.",
      { shot_id: shotId },
    ));
  }
  const takeFamily = String(take.model_family || "").trim();
  const resolvedFamily = String(resolved.model_family || "").trim();
  const takeVersion = String(take.model_version || "").trim();
  const resolvedVersion = String(resolved.model_version || "").trim();
  if (takeFamily && resolvedFamily && takeFamily.toLowerCase() !== resolvedFamily.toLowerCase()) {
    entries.push(createValidationEntry(
      "TAKE_RESOLVED_LORAS_MODEL_MISMATCH",
      "Error",
      "Director",
      "Take",
      takeId,
      "Take resolved_loras model family does not match the take metadata.",
      "Update the snapshot family or regenerate the take metadata.",
      { shot_id: shotId, take_model_family: takeFamily, resolved_model_family: resolvedFamily },
    ));
  }
  if (takeVersion && resolvedVersion && takeVersion !== resolvedVersion) {
    entries.push(createValidationEntry(
      "TAKE_RESOLVED_LORAS_MODEL_VERSION_MISMATCH",
      "Error",
      "Director",
      "Take",
      takeId,
      "Take resolved_loras model version does not match the take metadata.",
      "Update the snapshot version or regenerate the take metadata.",
      { shot_id: shotId, take_model_version: takeVersion, resolved_model_version: resolvedVersion },
    ));
  }
  const targets = resolved.targets;
  if (!targets || typeof targets !== "object" || Array.isArray(targets)) {
    entries.push(createValidationEntry(
      "TAKE_RESOLVED_LORAS_TARGETS_INVALID",
      "Error",
      "Director",
      "Take",
      takeId,
      "Take resolved_loras targets must be an object.",
      "Store each runtime target as a list of resolved LoRA rows.",
      { shot_id: shotId },
    ));
    return entries;
  }
  const allowedTargets = resolvedLoraTargetsForTake(takeFamily || resolvedFamily);
  for (const [targetKey, rows] of Object.entries(targets)) {
    if (allowedTargets && !allowedTargets.has(targetKey)) {
      entries.push(createValidationEntry(
        "TAKE_RESOLVED_LORAS_TARGET_INVALID",
        "Error",
        "Director",
        "Take",
        takeId,
        "Take resolved_loras target does not match the take model.",
        "Use target keys appropriate for the take model family.",
        { shot_id: shotId, target: targetKey, model_family: takeFamily || resolvedFamily },
      ));
    }
    if (!Array.isArray(rows)) {
      entries.push(createValidationEntry(
        "TAKE_RESOLVED_LORAS_TARGET_ROWS_INVALID",
        "Error",
        "Director",
        "Take",
        takeId,
        "Take resolved_loras target must be a list.",
        "Store resolved LoRA rows as an array.",
        { shot_id: shotId, target: targetKey },
      ));
      continue;
    }
    rows.forEach((row, rowIndex) => {
      if (!row || typeof row !== "object" || Array.isArray(row) || !row.name) {
        entries.push(createValidationEntry(
          "TAKE_RESOLVED_LORAS_ROW_INVALID",
          "Error",
          "Director",
          "Take",
          takeId,
          "Take resolved_loras row requires a LoRA name.",
          "Store each resolved LoRA row with a name and strengths.",
          { shot_id: shotId, target: targetKey, row_index: rowIndex },
        ));
      }
    });
  }
  return entries;
}

function resolvedLoraTargetsForTake(modelFamily) {
  const key = String(modelFamily || "").trim().toLowerCase().replace(/[ .]/g, "_");
  if (!key) return null;
  for (const [familyKey, targets] of Object.entries(RESOLVED_LORA_TARGETS_BY_FAMILY)) {
    if (key === familyKey || key.startsWith(`${familyKey}_`)) return targets;
  }
  return null;
}

function validateBoundaries(boundaries, shots, modelLoras) {
  const entries = [];
  const shotsById = new Map(shots.map((shot) => [shot.shot_id, shot]));
  const seenBoundaryIds = new Set();
  for (const boundary of boundaries) {
    const boundaryId = String(boundary.boundary_id || "boundary");
    const leftId = boundary.left_shot_id;
    const rightId = boundary.right_shot_id;
    if (seenBoundaryIds.has(boundaryId)) {
      entries.push(createValidationEntry(
        "BOUNDARY_DUPLICATE_ID",
        "Error",
        "Director",
        "Boundary",
        boundaryId,
        "Boundary IDs must be unique.",
        "Rename or remove the duplicate boundary.",
      ));
    }
    seenBoundaryIds.add(boundaryId);
    if (!shotsById.has(leftId)) {
      entries.push(createValidationEntry(
        "BOUNDARY_LEFT_SHOT_NOT_FOUND",
        "Error",
        "Director",
        "Boundary",
        boundaryId,
        "Boundary references a missing left shot.",
        "Choose an existing left shot or remove the stale boundary.",
        { left_shot_id: leftId },
      ));
    }
    if (!shotsById.has(rightId)) {
      entries.push(createValidationEntry(
        "BOUNDARY_RIGHT_SHOT_NOT_FOUND",
        "Error",
        "Director",
        "Boundary",
        boundaryId,
        "Boundary references a missing right shot.",
        "Choose an existing right shot or remove the stale boundary.",
        { right_shot_id: rightId },
      ));
    }
    if (leftId === rightId && leftId != null) {
      entries.push(createValidationEntry(
        "BOUNDARY_SELF_REFERENCE",
        "Error",
        "Director",
        "Boundary",
        boundaryId,
        "Boundary cannot connect a shot to itself.",
        "Choose two adjacent shots or remove the boundary.",
        { shot_id: leftId },
      ));
    }
    if (["Continuous Shot", "Blend Seam"].includes(boundary.mode) && shotsById.has(leftId) && shotsById.has(rightId)) {
      const leftLoras = effectiveLoraSignature(modelLoras, shotsById.get(leftId));
      const rightLoras = effectiveLoraSignature(modelLoras, shotsById.get(rightId));
      if (leftLoras !== rightLoras) {
        entries.push(createValidationEntry(
          "BOUNDARY_LORA_STACK_MISMATCH",
          "Warning",
          "Director",
          "Boundary",
          boundaryId,
          "Adjacent shots resolve to different LoRA stacks.",
          boundary.mode === "Continuous Shot"
            ? "Use Hard Cut, keep LoRAs consistent, or accept the style change across this boundary."
            : "Blend seams may show style changes when adjacent LoRA stacks differ.",
          { mode: boundary.mode, left_shot_id: leftId, right_shot_id: rightId },
        ));
      }
    }
  }
  return entries;
}

function effectiveLoraSignature(modelLoras, shot) {
  const globalTargets = rawObject(modelLoras.global);
  const overrides = rawObject(shot.lora_overrides);
  if (!overrides.enabled) return stableJson(globalTargets);
  const targets = rawObject(overrides.targets);
  if (overrides.merge_mode === "Disable LoRAs") return stableJson({});
  if (overrides.merge_mode === "Replace Global") return stableJson(targets);
  if (overrides.merge_mode === "Add To Global") {
    const merged = clone(globalTargets);
    mergeLoraTargets(merged, targets);
    return stableJson(merged);
  }
  return stableJson(globalTargets);
}

function mergeLoraTargets(base, overrides) {
  for (const [modelKey, modelTargets] of Object.entries(overrides)) {
    if (!modelTargets || typeof modelTargets !== "object" || Array.isArray(modelTargets)) continue;
    const baseModel = rawObject(base[modelKey]);
    base[modelKey] = baseModel;
    for (const [targetKey, stack] of Object.entries(modelTargets)) {
      if (!stack || typeof stack !== "object" || Array.isArray(stack)) continue;
      const baseStack = rawObject(baseModel[targetKey]);
      baseModel[targetKey] = baseStack;
      const baseLoras = Array.isArray(baseStack.loras) ? baseStack.loras : [];
      baseStack.loras = [...baseLoras, ...(Array.isArray(stack.loras) ? clone(stack.loras) : [])];
      if (stack.ui && typeof stack.ui === "object" && !Array.isArray(stack.ui)) {
        baseStack.ui = clone(stack.ui);
      }
      baseStack.version ??= 1;
    }
  }
}

function validateLoraPayload(payload, scope, itemId, codePrefix) {
  if (!containsEmbeddedMedia(payload)) return [];
  return [createValidationEntry(
    `${codePrefix}_EMBEDDED_MEDIA_NOT_ALLOWED`,
    "Error",
    "Director",
    scope,
    itemId,
    "LoRA stack must not embed media or preview payloads.",
    "Store only LoRA names, numeric strengths, and UI preferences.",
  )];
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

function rawObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function stableJson(value) {
  return JSON.stringify(sortForJson(value));
}

function sortForJson(value) {
  if (Array.isArray(value)) return value.map(sortForJson);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, sortForJson(child)]),
  );
}
