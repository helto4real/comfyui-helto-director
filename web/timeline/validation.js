import {
  SECTION_TYPE_IMAGE,
  SECTION_TYPE_TEXT,
  SECTION_TYPE_VIDEO,
} from "./schema.js";
import { normalizeVideoTimeline } from "./migration.js";

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
