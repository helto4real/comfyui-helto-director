export const DEFAULT_PIXELS_PER_SECOND = 96;
export const MIN_PIXELS_PER_SECOND = 1;
export const TIMELINE_WIDTH = 960;
export const DIRECTOR_TRACK_HEIGHT = 132;
export const AUDIO_LANE_HEIGHT = 34;
export const RULER_HEIGHT = 28;
export const HANDLE_WIDTH = 8;
export const TIMELINE_VIEWPORT_BORDER_HEIGHT = 2;
export const TIMELINE_RIGHT_PADDING = 28;

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

export function getPixelsPerSecond(timeline, viewportWidth = TIMELINE_WIDTH) {
  const duration = Math.max(0.25, Number(timeline?.project?.duration_seconds ?? 5));
  const zoom = Math.max(0.1, Number(timeline?.ui_state?.zoom_level ?? 1));
  const fittedWidth = Math.max(1, Number(viewportWidth) - TIMELINE_RIGHT_PADDING);
  return Math.max(MIN_PIXELS_PER_SECOND, (fittedWidth / duration) * zoom);
}

export function secondsToPixels(seconds, timeline, viewportWidth = TIMELINE_WIDTH) {
  return Number(seconds) * getPixelsPerSecond(timeline, viewportWidth);
}

export function pixelsToSeconds(pixels, timeline, viewportWidth = TIMELINE_WIDTH) {
  return Number(pixels) / getPixelsPerSecond(timeline, viewportWidth);
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
  return Math.max(
    viewportWidth,
    secondsToPixels(timeline.project.duration_seconds, timeline, viewportWidth) + TIMELINE_RIGHT_PADDING,
  );
}

export function timeFromClientX(clientX, container, timeline, viewportWidth = TIMELINE_WIDTH) {
  const rect = container.getBoundingClientRect();
  const scrollLeft = container.scrollLeft ?? 0;
  return clampProjectTime(pixelsToSeconds(clientX - rect.left + scrollLeft, timeline, viewportWidth), timeline);
}
