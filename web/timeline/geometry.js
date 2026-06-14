export const DEFAULT_PIXELS_PER_SECOND = 96;
export const MIN_PIXELS_PER_SECOND = 1;
export const TIMELINE_WIDTH = 960;
export const DIRECTOR_TRACK_HEIGHT = 132;
export const AUDIO_LANE_HEIGHT = 34;
export const RULER_HEIGHT = 28;
export const HANDLE_WIDTH = 8;
export const TIMELINE_VIEWPORT_BORDER_HEIGHT = 2;
export const TIMELINE_RIGHT_PADDING = 28;
export const RANGE_CONTROL_HEIGHT = 26;

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function getAudioTracksHeight(timeline) {
  const tracks = timeline?.audio_tracks?.length ? timeline.audio_tracks : [{ clips: [] }];
  return tracks.reduce((total, track) => {
    const clips = Array.isArray(track?.clips) ? track.clips : [];
    const maxLane = Math.max(0, ...clips.map((clip) => Number(clip?.lane ?? 0)));
    return total + (maxLane + 1) * AUDIO_LANE_HEIGHT;
  }, 0);
}

export function getTimelineViewportHeight(timeline) {
  return RULER_HEIGHT + DIRECTOR_TRACK_HEIGHT + getAudioTracksHeight(timeline) + TIMELINE_VIEWPORT_BORDER_HEIGHT;
}

export function getProjectWholeSeconds(timeline) {
  const rawDuration = Number(timeline?.project?.duration_seconds ?? 5);
  const duration = Math.max(0.25, Number.isFinite(rawDuration) ? rawDuration : 5);
  return Math.max(1, Math.ceil(duration));
}

export function normalizeTimelineViewRange(timeline) {
  const uiState = timeline.ui_state ??= {};
  const projectSeconds = getProjectWholeSeconds(timeline);
  const start = Number.isFinite(Number(uiState.view_start_seconds))
    ? Number(uiState.view_start_seconds)
    : 0;
  const end = Number.isFinite(Number(uiState.view_end_seconds))
    ? Number(uiState.view_end_seconds)
    : projectSeconds;
  const range = clampTimelineViewRange(timeline, start, end);
  uiState.view_start_seconds = range.start;
  uiState.view_end_seconds = range.end;
  return range;
}

export function getTimelineViewRange(timeline) {
  const uiState = timeline?.ui_state ?? {};
  return clampTimelineViewRange(
    timeline,
    uiState.view_start_seconds,
    uiState.view_end_seconds,
  );
}

export function clampTimelineViewRange(timeline, startSeconds, endSeconds) {
  const projectSeconds = getProjectWholeSeconds(timeline);
  let start = Math.round(Number(startSeconds));
  let end = Math.round(Number(endSeconds));
  if (!Number.isFinite(start)) start = 0;
  if (!Number.isFinite(end)) end = projectSeconds;
  start = clamp(start, 0, Math.max(0, projectSeconds - 1));
  end = clamp(end, start + 1, projectSeconds);
  if (end - start < 1) {
    if (start >= projectSeconds) {
      start = Math.max(0, projectSeconds - 1);
      end = projectSeconds;
    } else {
      end = Math.min(projectSeconds, start + 1);
    }
  }
  return { start, end };
}

export function getVisibleTimelineSeconds(timeline) {
  const range = getTimelineViewRange(timeline);
  return range.end - range.start;
}

export function getPixelsPerSecond(timeline, viewportWidth = TIMELINE_WIDTH) {
  const visibleSeconds = getVisibleTimelineSeconds(timeline);
  const fittedWidth = Math.max(1, Number(viewportWidth) - TIMELINE_RIGHT_PADDING);
  return Math.max(MIN_PIXELS_PER_SECOND, fittedWidth / visibleSeconds);
}

export function secondsToPixels(seconds, timeline, viewportWidth = TIMELINE_WIDTH) {
  const range = getTimelineViewRange(timeline);
  return (Number(seconds) - range.start) * getPixelsPerSecond(timeline, viewportWidth);
}

export function durationToPixels(seconds, timeline, viewportWidth = TIMELINE_WIDTH) {
  return Number(seconds) * getPixelsPerSecond(timeline, viewportWidth);
}

export function pixelsToSeconds(pixels, timeline, viewportWidth = TIMELINE_WIDTH) {
  const range = getTimelineViewRange(timeline);
  return range.start + Number(pixels) / getPixelsPerSecond(timeline, viewportWidth);
}

export function snapTime(time, timeline) {
  const mode = timeline?.ui_state?.snap_mode ?? "Frames";
  const frameRate = Number(timeline?.project?.frame_rate ?? 24);
  if (mode === "None") return Number(time);
  const interval = mode === "Seconds" ? 1 : 1 / Math.max(1, frameRate);
  return Math.round(Number(time) / interval) * interval;
}

export function clampProjectTime(time, timeline) {
  return clamp(Number(time), 0, Number(timeline?.project?.duration_seconds ?? 5));
}

export function getTimelineWidth(timeline, viewportWidth = TIMELINE_WIDTH) {
  return Math.max(1, Number(viewportWidth));
}

export function timeFromClientX(clientX, container, timeline, viewportWidth = TIMELINE_WIDTH) {
  const rect = container.getBoundingClientRect();
  return clampProjectTime(pixelsToSeconds(clientX - rect.left, timeline, viewportWidth), timeline);
}
