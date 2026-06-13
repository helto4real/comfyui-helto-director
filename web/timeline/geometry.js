export const DEFAULT_PIXELS_PER_SECOND = 96;
export const TIMELINE_WIDTH = 960;
export const DIRECTOR_TRACK_HEIGHT = 44;
export const AUDIO_LANE_HEIGHT = 34;
export const RULER_HEIGHT = 28;
export const HANDLE_WIDTH = 8;

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function getPixelsPerSecond(timeline, viewportWidth = TIMELINE_WIDTH) {
  const duration = Math.max(0.25, Number(timeline?.project?.duration_seconds ?? 5));
  const zoom = Math.max(0.1, Number(timeline?.ui_state?.zoom_level ?? 1));
  return Math.max(24, (viewportWidth / duration) * zoom);
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
  return Math.max(viewportWidth, secondsToPixels(timeline.project.duration_seconds, timeline, viewportWidth));
}

export function timeFromClientX(clientX, container, timeline, viewportWidth = TIMELINE_WIDTH) {
  const rect = container.getBoundingClientRect();
  const scrollLeft = container.scrollLeft ?? 0;
  return clampProjectTime(pixelsToSeconds(clientX - rect.left + scrollLeft, timeline, viewportWidth), timeline);
}
