import {
  SCHEMA_VERSION,
  VIDEO_TIMELINE_TYPE,
  SECTION_TYPE_IMAGE,
  SECTION_TYPE_TEXT,
  SECTION_TYPE_VIDEO,
  CROP_MODE_PROJECT_DEFAULT,
  createDefaultVideoTimeline,
  deepClone,
} from "./schema.js";

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
  normalized.director_track = normalizeDirectorTrack(normalized.director_track);
  normalized.audio_tracks = normalizeAudioTracks(normalized.audio_tracks);
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
