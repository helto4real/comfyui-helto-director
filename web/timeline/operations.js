import {
  SECTION_TYPE_IMAGE,
  SECTION_TYPE_TEXT,
  SECTION_TYPE_VIDEO,
  deepClone,
} from "./schema.js";
import { clamp, snapTime } from "./geometry.js";

const DEFAULT_SECTION_DURATION = 1.0;
const MIN_SECTION_DURATION = 0.25;

export function addSection(timeline, type = SECTION_TYPE_TEXT, startTime = null) {
  const duration = getDuration(timeline);
  const sectionDuration = Math.min(DEFAULT_SECTION_DURATION, duration);
  const start = startTime == null
    ? findGapForDuration(timeline, sectionDuration)
    : findGapForDuration(timeline, sectionDuration, clampStart(timeline, snapTime(startTime, timeline), sectionDuration));
  if (start == null) return null;
  const section = createSection(type, start, start + sectionDuration);
  timeline.director_track.sections.push(section);
  timeline.ui_state.selected_item_id = section.item_id;
  sortDirectorSections(timeline);
  return section;
}

export function deleteSelectedItem(timeline) {
  const id = timeline.ui_state.selected_item_id;
  if (!id) return false;
  const before = timeline.director_track.sections.length;
  timeline.director_track.sections = timeline.director_track.sections.filter((section) => section.item_id !== id);
  if (before !== timeline.director_track.sections.length) {
    timeline.ui_state.selected_item_id = null;
    return true;
  }
  for (const track of timeline.audio_tracks) {
    const clipBefore = track.clips.length;
    track.clips = track.clips.filter((clip) => clip.item_id !== id);
    if (clipBefore !== track.clips.length) {
      timeline.ui_state.selected_item_id = null;
      cleanupAudioTracks(timeline);
      return true;
    }
  }
  return false;
}

export function duplicateSelectedSection(timeline) {
  const section = getSelectedSection(timeline);
  if (!section) return null;
  const copy = deepClone(section);
  const sectionDuration = copy.end_time - copy.start_time;
  const preferred = section.end_time;
  copy.item_id = makeId("section");
  copy.start_time = findGapForDuration(timeline, sectionDuration, preferred);
  if (copy.start_time == null) return null;
  copy.end_time = copy.start_time + sectionDuration;
  timeline.director_track.sections.push(copy);
  timeline.ui_state.selected_item_id = copy.item_id;
  sortDirectorSections(timeline);
  return copy;
}

export function splitSelectedSection(timeline, splitTime = null) {
  const section = getSelectedSection(timeline);
  if (!section) return null;
  const time = snapTime(splitTime ?? (section.start_time + section.end_time) / 2, timeline);
  if (time <= section.start_time || time >= section.end_time) return null;
  const copy = deepClone(section);
  copy.item_id = makeId("section");
  copy.start_time = time;
  section.end_time = time;

  if (section.type === SECTION_TYPE_VIDEO && section.source_out != null) {
    const originalSourceOut = Number(copy.source_out);
    const originalSourceIn = Number(copy.source_in ?? 0);
    const ratio = (time - section.start_time) / (copy.end_time - section.start_time);
    const splitSource = originalSourceIn + (originalSourceOut - originalSourceIn) * ratio;
    section.source_out = splitSource;
    copy.source_in = splitSource;
  }

  timeline.director_track.sections.push(copy);
  timeline.ui_state.selected_item_id = copy.item_id;
  sortDirectorSections(timeline);
  return copy;
}

export function moveSection(timeline, itemId, startTime) {
  const section = findSection(timeline, itemId);
  if (!section) return false;
  const duration = section.end_time - section.start_time;
  const bounds = sectionMovementBounds(timeline, section);
  const start = clamp(snapTime(startTime, timeline), bounds.min, bounds.max - duration);
  section.start_time = start;
  section.end_time = start + duration;
  sortDirectorSections(timeline);
  return true;
}

export function resizeSection(timeline, itemId, edge, time) {
  const section = findSection(timeline, itemId);
  if (!section) return false;
  const minDuration = getMinimumSectionDuration(timeline);
  const neighbors = getSectionNeighbors(timeline, section);
  const snapped = snapTime(time, timeline);

  if (edge === "start") {
    const minStart = neighbors.previous ? neighbors.previous.end_time : 0;
    section.start_time = clamp(snapped, minStart, section.end_time - minDuration);
  } else {
    const maxEnd = neighbors.next ? neighbors.next.start_time : getDuration(timeline);
    section.end_time = clamp(snapped, section.start_time + minDuration, maxEnd);
  }
  return true;
}

export function addAudioClip(timeline, startTime = 0, duration = 1) {
  const clip = {
    item_id: makeId("audio_clip"),
    audio: null,
    start_time: clamp(snapTime(startTime, timeline), 0, getDuration(timeline)),
    end_time: clamp(snapTime(startTime + duration, timeline), 0, getDuration(timeline)),
    source_in: 0,
    source_out: null,
    volume: timeline.project.audio.default_volume,
    normalization: {},
    fade_in: timeline.project.audio.default_fade_in_seconds,
    fade_out: timeline.project.audio.default_fade_out_seconds,
    enabled: true,
    locked: false,
    name: "",
    lane: 0,
  };
  if (clip.end_time <= clip.start_time) clip.end_time = Math.min(getDuration(timeline), clip.start_time + MIN_SECTION_DURATION);
  if (timeline.audio_tracks.length === 0) timeline.audio_tracks.push({ track_id: "audio_track_001", clips: [] });
  timeline.audio_tracks[0].clips.push(clip);
  autoStackAudioLanes(timeline);
  timeline.ui_state.selected_item_id = clip.item_id;
  return clip;
}

export function autoStackAudioLanes(timeline) {
  for (const track of timeline.audio_tracks) {
    const lanes = [];
    const clips = [...track.clips].sort((a, b) => a.start_time - b.start_time);
    for (const clip of clips) {
      let lane = 0;
      while (lanes[lane] != null && clip.start_time < lanes[lane]) lane += 1;
      clip.lane = lane;
      lanes[lane] = clip.end_time;
    }
    track.clips.sort((a, b) => a.lane - b.lane || a.start_time - b.start_time);
  }
  cleanupAudioTracks(timeline);
  return timeline;
}

export function zoomToFit(timeline) {
  timeline.ui_state.zoom_level = 1.0;
  timeline.ui_state.scroll_x = 0.0;
  return timeline;
}

export function findSection(timeline, itemId) {
  return timeline.director_track.sections.find((section) => section.item_id === itemId);
}

export function getSelectedSection(timeline) {
  return findSection(timeline, timeline.ui_state.selected_item_id);
}

export function selectItem(timeline, itemId) {
  timeline.ui_state.selected_item_id = itemId;
  return itemId;
}

export function sortDirectorSections(timeline) {
  timeline.director_track.sections.sort((a, b) => a.start_time - b.start_time || a.end_time - b.end_time);
}

function createSection(type, start, end) {
  const section = {
    item_id: makeId("section"),
    type,
    start_time: start,
    end_time: end,
  };
  if (type === SECTION_TYPE_IMAGE) {
    section.image = null;
    section.prompt = "";
    section.guide_strength = 1.0;
    section.crop_mode = "Project Default";
  } else if (type === SECTION_TYPE_VIDEO) {
    section.video = null;
    section.prompt = "";
    section.guide_strength = 1.0;
    section.crop_mode = "Project Default";
    section.source_in = 0.0;
    section.source_out = null;
    section.timing_mode = "Fit to Section";
  } else {
    section.prompt = "Text";
  }
  return section;
}

function findGapForDuration(timeline, duration, preferredStart = 0) {
  const projectDuration = getDuration(timeline);
  const sections = [...timeline.director_track.sections].sort((a, b) => a.start_time - b.start_time);
  const gaps = [];
  let cursor = 0;
  for (const section of sections) {
    if (section.start_time > cursor) gaps.push({ start: cursor, end: section.start_time });
    cursor = Math.max(cursor, section.end_time);
  }
  if (cursor < projectDuration) gaps.push({ start: cursor, end: projectDuration });

  for (const gap of gaps) {
    const start = Math.max(gap.start, preferredStart);
    if (gap.end - start >= duration) return start;
  }
  const gap = gaps.find((candidate) => candidate.end - candidate.start >= duration);
  return gap ? gap.start : null;
}

function clampStart(timeline, start, duration) {
  return clamp(start, 0, Math.max(0, getDuration(timeline) - duration));
}

function sectionMovementBounds(timeline, section) {
  const neighbors = getSectionNeighbors(timeline, section);
  return {
    min: neighbors.previous ? neighbors.previous.end_time : 0,
    max: neighbors.next ? neighbors.next.start_time : getDuration(timeline),
  };
}

function getSectionNeighbors(timeline, section) {
  const sections = [...timeline.director_track.sections].sort((a, b) => a.start_time - b.start_time);
  const index = sections.findIndex((candidate) => candidate.item_id === section.item_id);
  return {
    previous: index > 0 ? sections[index - 1] : null,
    next: index >= 0 && index < sections.length - 1 ? sections[index + 1] : null,
  };
}

function cleanupAudioTracks(timeline) {
  timeline.audio_tracks = timeline.audio_tracks
    .map((track) => ({ ...track, clips: track.clips.filter(Boolean) }))
    .filter((track) => track.clips.length > 0);
}

function getDuration(timeline) {
  return Number(timeline.project.duration_seconds ?? 5);
}

function getMinimumSectionDuration(timeline) {
  return Number(timeline.project.settings?.minimum_section_duration_seconds ?? MIN_SECTION_DURATION);
}

function makeId(prefix) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}
