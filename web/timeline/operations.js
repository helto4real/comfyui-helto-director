import {
  ASSET_SOURCE_GENERATED,
  ASSET_TYPE_VIDEO,
  BOUNDARY_MODES,
  LORA_MERGE_MODES,
  MODEL_LORA_MODEL_LTX_2_3,
  MODEL_LORA_MODEL_WAN_2_2,
  MODEL_LORA_SCHEMA_VERSION,
  MODEL_LORA_TARGET_HIGH_NOISE,
  MODEL_LORA_TARGET_LOW_NOISE,
  MODEL_LORA_TARGET_MAIN,
  SECTION_TYPE_IMAGE,
  SECTION_TYPE_TEXT,
  SECTION_TYPE_VIDEO,
  SHOT_TYPES,
  TAKE_STATUSES,
  createDefaultBoundary,
  createDefaultClipInstance,
  createDefaultLoraStack,
  createDefaultProjectModelLoras,
  createDefaultSequence,
  createDefaultShot,
  createDefaultTake,
  deepClone,
} from "./schema.js";
import { clamp, getProjectWholeSeconds, snapTime } from "./geometry.js";

const DEFAULT_SECTION_DURATION = 1.0;
const MIN_SECTION_DURATION = 0.25;

export function addSection(timeline, type = SECTION_TYPE_TEXT, startTime = null, options = {}) {
  const targetShot = resolveSectionTargetShot(timeline, options);
  const duration = getDuration(timeline);
  const sectionDuration = Math.min(DEFAULT_SECTION_DURATION, duration);
  const start = startTime == null
    ? (targetShot ? findShotSectionInsertionStart(timeline, targetShot, sectionDuration) : findGapForDuration(timeline, sectionDuration))
    : findGapForDuration(timeline, sectionDuration, clampStart(timeline, snapTime(startTime, timeline), sectionDuration));
  if (start == null) return null;
  const section = createSection(type, start, start + sectionDuration);
  timeline.director_track.sections.push(section);
  if (targetShot) {
    assignSectionToShot(timeline, section.item_id, targetShot.shot_id);
  } else {
    ensureShotForSection(timeline, section);
  }
  selectItem(timeline, section.item_id);
  sortDirectorSections(timeline);
  return section;
}

export function deleteSelectedItem(timeline) {
  const ids = new Set(getSelectedItemIds(timeline));
  if (!ids.size) return false;
  let changed = false;
  for (const id of ids) {
    if (deleteShot(timeline, id)) changed = true;
    if (deleteBoundary(timeline, id)) changed = true;
    if (deleteTakeById(timeline, id)) changed = true;
  }
  const before = timeline.director_track.sections.length;
  timeline.director_track.sections = timeline.director_track.sections.filter((section) => !ids.has(section.item_id));
  changed = changed || before !== timeline.director_track.sections.length;
  for (const track of timeline.audio_tracks) {
    const clipBefore = track.clips.length;
    track.clips = track.clips.filter((clip) => !ids.has(clip.item_id));
    if (clipBefore !== track.clips.length) {
      changed = true;
      cleanupAudioTracks(timeline);
    }
  }
  if (changed) {
    cleanupShotSectionIds(timeline);
    syncShotTimingFromSections(timeline);
    clearSelection(timeline);
  }
  return changed;
}

export function duplicateSelectedSection(timeline) {
  const selected = selectedTimelineItems(timeline);
  if (!selected.sections.length && !selected.audioClips.length) return null;
  const duration = getDuration(timeline);
  let delta = null;

  if (selected.sections.length) {
    const blockStart = Math.min(...selected.sections.map((section) => Number(section.start_time)));
    const blockEnd = Math.max(...selected.sections.map((section) => Number(section.end_time)));
    const copyStart = findGapForDuration(timeline, blockEnd - blockStart, blockEnd);
    if (copyStart == null) return null;
    delta = copyStart - blockStart;
  } else {
    const blockStart = Math.min(...selected.audioClips.map((clip) => Number(clip.start_time)));
    const blockEnd = Math.max(...selected.audioClips.map((clip) => Number(clip.end_time)));
    delta = blockEnd - blockStart;
  }

  for (const clip of selected.audioClips) {
    if (Number(clip.start_time) + delta < 0 || Number(clip.end_time) + delta > duration) return null;
  }

  const newIds = [];
  for (const section of selected.sections) {
    const copy = deepClone(section);
    copy.item_id = makeId("section");
    copy.start_time = Number(copy.start_time) + delta;
    copy.end_time = Number(copy.end_time) + delta;
    timeline.director_track.sections.push(copy);
    ensureShotForSection(timeline, copy);
    newIds.push(copy.item_id);
  }
  if (selected.audioClips.length) {
    if (!timeline.audio_tracks.length) timeline.audio_tracks.push({ track_id: "audio_track_001", clips: [] });
    const targetTrack = timeline.audio_tracks[0];
    for (const clip of selected.audioClips) {
      const copy = deepClone(clip);
      copy.item_id = makeId("audio_clip");
      copy.start_time = Number(copy.start_time) + delta;
      copy.end_time = Number(copy.end_time) + delta;
      targetTrack.clips.push(copy);
      newIds.push(copy.item_id);
    }
    autoStackAudioLanes(timeline);
  }
  setSelection(timeline, newIds);
  sortDirectorSections(timeline);
  return newIds.length ? newIds : null;
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
  ensureShotForSection(timeline, copy);
  syncShotTimingFromSections(timeline);
  selectItem(timeline, copy.item_id);
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
  syncShotTimingFromSections(timeline);
  sortDirectorSections(timeline);
  return true;
}

export function resizeSection(timeline, itemId, edge, time) {
  const section = findSection(timeline, itemId);
  if (!section) return false;
  if (timeline.ui_state.section_edit_mode === "Ripple Edit") {
    return rippleResizeSection(timeline, section, edge, time);
  }
  return trimResizeSection(timeline, section, edge, time);
}

export function moveAudioClip(timeline, itemId, startTime) {
  const match = findAudioClipWithTrack(timeline, itemId);
  if (!match || match.clip.locked) return false;
  const { clip } = match;
  const duration = clip.end_time - clip.start_time;
  const start = clamp(snapTime(startTime, timeline), 0, Math.max(0, getDuration(timeline) - duration));
  clip.start_time = start;
  clip.end_time = start + duration;
  autoStackAudioLanes(timeline);
  return true;
}

export function moveSelectedItems(timeline, itemId, startTime) {
  const dragged = findTimelineItem(timeline, itemId);
  if (!dragged) return false;
  if (dragged.kind === "audio" && dragged.item.locked) return false;
  const selectedIds = new Set(getSelectedItemIds(timeline));
  if (!selectedIds.has(itemId) || selectedIds.size <= 1) {
    return dragged.kind === "audio"
      ? moveAudioClip(timeline, itemId, startTime)
      : moveSection(timeline, itemId, startTime);
  }

  const snappedStart = snapTime(startTime, timeline);
  const requestedDelta = snappedStart - Number(dragged.item.start_time);
  const bounds = selectedMoveDeltaBounds(timeline, selectedIds);
  if (!bounds.movable) return false;
  const delta = clamp(requestedDelta, bounds.min, bounds.max);
  for (const section of timeline.director_track.sections) {
    if (!selectedIds.has(section.item_id)) continue;
    section.start_time = Number(section.start_time) + delta;
    section.end_time = Number(section.end_time) + delta;
  }
  for (const track of timeline.audio_tracks) {
    for (const clip of track.clips) {
      if (!selectedIds.has(clip.item_id) || clip.locked) continue;
      clip.start_time = Number(clip.start_time) + delta;
      clip.end_time = Number(clip.end_time) + delta;
    }
  }
  sortDirectorSections(timeline);
  syncShotTimingFromSections(timeline);
  autoStackAudioLanes(timeline);
  return true;
}

export function resizeAudioClip(timeline, itemId, edge, time) {
  const match = findAudioClipWithTrack(timeline, itemId);
  if (!match || match.clip.locked) return false;
  const { clip } = match;
  const minDuration = getMinimumSectionDuration(timeline);
  const snapped = snapTime(time, timeline);
  const oldStart = Number(clip.start_time);
  const oldEnd = Number(clip.end_time);
  const sourceIn = Number(clip.source_in ?? 0);

  if (edge === "start") {
    const nextStart = clamp(snapped, 0, oldEnd - minDuration);
    clip.start_time = nextStart;
    clip.source_in = Math.max(0, sourceIn + (nextStart - oldStart));
  } else {
    const nextEnd = clamp(snapped, oldStart + minDuration, getDuration(timeline));
    const sourceOut = clip.source_out == null ? sourceIn + (oldEnd - oldStart) : Number(clip.source_out);
    clip.end_time = nextEnd;
    clip.source_out = Math.max(clip.source_in ?? 0, sourceOut + (nextEnd - oldEnd));
  }
  autoStackAudioLanes(timeline);
  return true;
}

function trimResizeSection(timeline, section, edge, time) {
  const minDuration = getMinimumSectionDuration(timeline);
  const neighbors = getSectionNeighbors(timeline, section);
  const snapped = snapTime(time, timeline);

  if (edge === "start") {
    const touchesPrevious = neighbors.previous && Math.abs(Number(section.start_time) - Number(neighbors.previous.end_time)) < 0.000001;
    if (touchesPrevious || (neighbors.previous && snapped < neighbors.previous.end_time)) {
      const start = clamp(snapped, neighbors.previous.start_time + minDuration, section.end_time - minDuration);
      neighbors.previous.end_time = start;
      section.start_time = start;
    } else {
      const minStart = neighbors.previous ? neighbors.previous.end_time : 0;
      section.start_time = clamp(snapped, minStart, section.end_time - minDuration);
    }
  } else {
    const touchesNext = neighbors.next && Math.abs(Number(section.end_time) - Number(neighbors.next.start_time)) < 0.000001;
    if (touchesNext || (neighbors.next && snapped > neighbors.next.start_time)) {
      const end = clamp(snapped, section.start_time + minDuration, neighbors.next.end_time - minDuration);
      section.end_time = end;
      neighbors.next.start_time = end;
    } else {
      const maxEnd = neighbors.next ? neighbors.next.start_time : getDuration(timeline);
      section.end_time = clamp(snapped, section.start_time + minDuration, maxEnd);
    }
  }
  sortDirectorSections(timeline);
  syncShotTimingFromSections(timeline);
  return true;
}

function rippleResizeSection(timeline, section, edge, time) {
  const minDuration = getMinimumSectionDuration(timeline);
  const snapped = snapTime(time, timeline);
  if (edge === "start") {
    const neighbors = getSectionNeighbors(timeline, section);
    const minStart = neighbors.previous ? neighbors.previous.end_time : 0;
    section.start_time = clamp(snapped, minStart, section.end_time - minDuration);
    sortDirectorSections(timeline);
    syncShotTimingFromSections(timeline);
    return true;
  }

  const oldEnd = Number(section.end_time);
  const requestedEnd = Math.max(Number(section.start_time) + minDuration, snapped);
  const following = getFollowingSections(timeline, section);
  const lastEnd = following.length ? Number(following.at(-1).end_time) : oldEnd;
  const maxDelta = getDuration(timeline) - lastEnd;
  const delta = clamp(requestedEnd - oldEnd, -Math.max(0, oldEnd - Number(section.start_time) - minDuration), maxDelta);
  section.end_time = oldEnd + delta;
  for (const candidate of following) {
    candidate.start_time += delta;
    candidate.end_time += delta;
  }
  sortDirectorSections(timeline);
  syncShotTimingFromSections(timeline);
  return true;
}

export function createShot(timeline, options = {}) {
  const sequence = ensureSequence(timeline);
  const shot = {
    ...createDefaultShot(sequence.shots.length + 1),
    shot_id: uniqueTimelineId(String(options.shot_id || `shot_${String(sequence.shots.length + 1).padStart(3, "0")}`), existingShotIds(timeline)),
    name: String(options.name ?? ""),
    type: SHOT_TYPES.includes(options.type) ? options.type : "Generated",
    start_time: Number.isFinite(Number(options.start_time)) ? Number(options.start_time) : 0,
    end_time: Number.isFinite(Number(options.end_time)) ? Number(options.end_time) : Math.min(getDuration(timeline), 1),
    section_ids: Array.isArray(options.section_ids) ? uniqueStrings(options.section_ids) : [],
    metadata: options.metadata && typeof options.metadata === "object" && !Array.isArray(options.metadata) ? deepClone(options.metadata) : {},
  };
  if (shot.end_time <= shot.start_time) {
    shot.end_time = Math.min(getDuration(timeline), shot.start_time + getMinimumSectionDuration(timeline));
  }
  sequence.shots.push(shot);
  syncShotTimingFromSections(timeline);
  selectItem(timeline, shot.shot_id);
  return shot;
}

export function deleteShot(timeline, shotId, options = {}) {
  const sequence = ensureSequence(timeline);
  const id = String(shotId ?? "");
  const shot = findShot(timeline, id);
  if (!shot) return false;
  const deleteSections = options.deleteSections !== false;
  const sectionIds = new Set(shot.section_ids ?? []);
  sequence.shots = sequence.shots.filter((candidate) => candidate.shot_id !== id);
  sequence.boundaries = sequence.boundaries.filter((boundary) => boundary.left_shot_id !== id && boundary.right_shot_id !== id);
  if (deleteSections && sectionIds.size) {
    timeline.director_track.sections = timeline.director_track.sections.filter((section) => !sectionIds.has(section.item_id));
  }
  return true;
}

export function renameShot(timeline, shotId, name) {
  const shot = findShot(timeline, shotId);
  if (!shot) return false;
  shot.name = String(name ?? "");
  return true;
}

export function changeShotType(timeline, shotId, type) {
  const shot = findShot(timeline, shotId);
  if (!shot || !SHOT_TYPES.includes(type)) return false;
  shot.type = type;
  return true;
}

export function assignSectionToShot(timeline, sectionId, shotId, options = {}) {
  const section = findSection(timeline, sectionId);
  const shot = findShot(timeline, shotId);
  if (!section || !shot) return false;
  const id = String(section.item_id);
  if (options.exclusive !== false) {
    for (const candidate of ensureSequence(timeline).shots) {
      candidate.section_ids = (candidate.section_ids ?? []).filter((candidateId) => candidateId !== id);
    }
  }
  shot.section_ids ??= [];
  if (!shot.section_ids.includes(id)) shot.section_ids.push(id);
  cleanupShotSectionIds(timeline);
  syncShotTimingFromSections(timeline);
  return true;
}

export function createOrUpdateBoundaryBetweenShots(timeline, leftShotId, rightShotId, patch = {}) {
  const sequence = ensureSequence(timeline);
  const left = findShot(timeline, leftShotId);
  const right = findShot(timeline, rightShotId);
  if (!left || !right || !areAdjacentShots(timeline, left.shot_id, right.shot_id)) return null;
  let boundary = findBoundaryBetweenShots(timeline, left.shot_id, right.shot_id);
  if (!boundary) {
    boundary = {
      ...createDefaultBoundary(sequence.boundaries.length + 1),
      boundary_id: uniqueTimelineId(`boundary_${left.shot_id}_to_${right.shot_id}`, existingBoundaryIds(timeline)),
      left_shot_id: left.shot_id,
      right_shot_id: right.shot_id,
    };
    sequence.boundaries.push(boundary);
  }
  applyBoundaryPatch(boundary, patch);
  return boundary;
}

export function changeBoundaryMode(timeline, boundaryId, mode) {
  const boundary = findBoundary(timeline, boundaryId);
  if (!boundary || !BOUNDARY_MODES.includes(mode)) return false;
  boundary.mode = mode;
  return true;
}

export function addTakeMetadata(timeline, shotId, takeData = {}) {
  const shot = findShot(timeline, shotId);
  if (!shot) return null;
  const requestedAccepted = takeData.status === "Accepted";
  shot.takes ??= [];
  const take = {
    ...createDefaultTake(shot.takes.length + 1),
    ...deepClone(takeData),
    take_id: uniqueTimelineId(String(takeData.take_id || `take_${String(shot.takes.length + 1).padStart(3, "0")}`), existingTakeIds(shot)),
    status: TAKE_STATUSES.includes(takeData.status) ? takeData.status : "Candidate",
    asset_id: takeData.asset_id == null ? null : String(takeData.asset_id),
    metadata: takeData.metadata && typeof takeData.metadata === "object" && !Array.isArray(takeData.metadata) ? deepClone(takeData.metadata) : {},
    resolved_loras: takeData.resolved_loras ?? null,
  };
  shot.takes.push(take);
  if (requestedAccepted && !acceptTake(timeline, shotId, take.take_id)) {
    take.status = "Candidate";
  }
  return take;
}

export function attachVideoAssetAsTake(timeline, shotId, assetId, takeData = {}) {
  const shot = findShot(timeline, shotId);
  const asset = videoAssetForId(timeline, assetId);
  if (!shot || !asset) return null;
  return addTakeMetadata(timeline, shot.shot_id, {
    ...deepClone(takeData),
    asset_id: asset.asset_id,
    status: TAKE_STATUSES.includes(takeData.status) ? takeData.status : "Candidate",
  });
}

export function acceptTake(timeline, shotId, takeId) {
  const shot = findShot(timeline, shotId);
  const take = shot?.takes?.find((candidate) => candidate.take_id === takeId);
  if (!shot || !take || !assetExists(timeline, take.asset_id)) return false;
  for (const candidate of shot.takes) {
    if (candidate.status === "Accepted") candidate.status = "Candidate";
  }
  take.status = "Accepted";
  shot.accepted_take_id = take.take_id;
  const asset = videoAssetForId(timeline, take.asset_id);
  if (asset) {
    shot.clip_instance = {
      ...createDefaultClipInstance(),
      asset_id: String(asset.asset_id),
    };
  }
  return true;
}

export function setTakeStatus(timeline, shotId, takeId, status) {
  const shot = findShot(timeline, shotId);
  const take = shot?.takes?.find((candidate) => candidate.take_id === takeId);
  if (!shot || !take || !TAKE_STATUSES.includes(status)) return false;
  if (status === "Accepted") return acceptTake(timeline, shotId, takeId);
  take.status = status;
  if (shot.accepted_take_id === take.take_id) {
    shot.accepted_take_id = null;
    clearClipInstanceForTake(shot, take);
  }
  return true;
}

export function deleteTake(timeline, shotId, takeId) {
  const shot = findShot(timeline, shotId);
  if (!shot) return false;
  const takeIndex = (shot.takes ?? []).findIndex((candidate) => candidate.take_id === takeId);
  if (takeIndex < 0) return false;
  const [take] = shot.takes.splice(takeIndex, 1);
  if (shot.accepted_take_id === take.take_id) shot.accepted_take_id = null;
  clearClipInstanceForTake(shot, take);
  pruneUnreferencedGeneratedAsset(timeline, take.asset_id);
  return true;
}

export function setClipInstanceFromAsset(timeline, shotId, assetId, patch = {}) {
  const shot = findShot(timeline, shotId);
  const asset = timeline.assets?.find((candidate) => candidate.asset_id === assetId && candidate.type === ASSET_TYPE_VIDEO);
  if (!shot || !asset) return false;
  shot.clip_instance = {
    ...createDefaultClipInstance(),
    ...deepClone(patch),
    asset_id: String(asset.asset_id),
  };
  shot.type = "Imported";
  return true;
}

export function setProjectModelLoraStack(timeline, modelKey, targetKey, stack) {
  if (!isValidLoraTarget(modelKey, targetKey)) return false;
  timeline.project ??= {};
  timeline.project.model_loras ??= createDefaultProjectModelLoras();
  timeline.project.model_loras.schema_version = MODEL_LORA_SCHEMA_VERSION;
  timeline.project.model_loras.global ??= {};
  timeline.project.model_loras.global[modelKey] ??= {};
  timeline.project.model_loras.global[modelKey][targetKey] = normalizeTimelineLoraStack(stack);
  return true;
}

export function clearProjectModelLoraStack(timeline, modelKey, targetKey) {
  return setProjectModelLoraStack(timeline, modelKey, targetKey, createDefaultLoraStack());
}

export function setShotLoraMergeMode(timeline, shotId, mergeMode) {
  const shot = findShot(timeline, shotId);
  if (!shot || !LORA_MERGE_MODES.includes(mergeMode)) return false;
  shot.lora_overrides ??= { enabled: false, merge_mode: "Inherit Global", targets: {} };
  shot.lora_overrides.merge_mode = mergeMode;
  shot.lora_overrides.enabled = mergeMode !== "Inherit Global";
  shot.lora_overrides.targets ??= {};
  return true;
}

export function setShotLoraTargetStack(timeline, shotId, modelKey, targetKey, stack) {
  const shot = findShot(timeline, shotId);
  if (!shot || !isValidLoraTarget(modelKey, targetKey)) return false;
  const normalizedStack = normalizeTimelineLoraStack(stack);
  shot.lora_overrides ??= { enabled: true, merge_mode: "Add To Global", targets: {} };
  shot.lora_overrides.enabled = true;
  if (
    normalizedStack.loras.length > 0
    && (shot.lora_overrides.merge_mode === "Inherit Global" || shot.lora_overrides.merge_mode === "Disable LoRAs")
  ) {
    shot.lora_overrides.merge_mode = "Add To Global";
  }
  shot.lora_overrides.targets ??= {};
  shot.lora_overrides.targets[modelKey] ??= {};
  shot.lora_overrides.targets[modelKey][targetKey] = normalizedStack;
  return true;
}

export function clearShotLoraTargetStack(timeline, shotId, modelKey, targetKey) {
  const shot = findShot(timeline, shotId);
  if (!shot || !isValidLoraTarget(modelKey, targetKey)) return false;
  shot.lora_overrides ??= { enabled: false, merge_mode: "Inherit Global", targets: {} };
  shot.lora_overrides.targets ??= {};
  const modelTargets = shot.lora_overrides.targets[modelKey];
  if (modelTargets && typeof modelTargets === "object" && !Array.isArray(modelTargets)) {
    delete modelTargets[targetKey];
    if (!Object.keys(modelTargets).length) delete shot.lora_overrides.targets[modelKey];
  }
  return true;
}

export function clearShotLoraOverride(timeline, shotId) {
  const shot = findShot(timeline, shotId);
  if (!shot) return false;
  shot.lora_overrides = deepClone(createDefaultShot(1).lora_overrides);
  return true;
}

export function findShot(timeline, shotId) {
  return ensureSequence(timeline).shots.find((shot) => shot.shot_id === shotId) ?? null;
}

export function findBoundary(timeline, boundaryId) {
  return ensureSequence(timeline).boundaries.find((boundary) => boundary.boundary_id === boundaryId) ?? null;
}

export function findShotForSection(timeline, sectionId) {
  return ensureSequence(timeline).shots.find((shot) => (shot.section_ids ?? []).includes(sectionId)) ?? null;
}

export function findBoundaryBetweenShots(timeline, leftShotId, rightShotId) {
  return ensureSequence(timeline).boundaries
    .find((boundary) => boundary.left_shot_id === leftShotId && boundary.right_shot_id === rightShotId) ?? null;
}

export function adjacentShotPairs(timeline) {
  const shots = orderedShots(timeline);
  const pairs = [];
  for (let index = 0; index < shots.length - 1; index += 1) {
    pairs.push([shots[index], shots[index + 1]]);
  }
  return pairs;
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
  selectItem(timeline, clip.item_id);
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
  timeline.ui_state.view_start_seconds = 0;
  timeline.ui_state.view_end_seconds = getProjectWholeSeconds(timeline);
  return timeline;
}

export function hasDirectorSectionOverflow(timeline) {
  const duration = getDuration(timeline);
  return directorSections(timeline).some((section) => Number(section.end_time) > duration);
}

export function canFitLastDirectorSectionToDuration(timeline) {
  const duration = getDuration(timeline);
  const last = directorSections(timeline).at(-1);
  return Boolean(last) && Number(last.end_time) > duration && duration > Number(last.start_time);
}

export function fitLastDirectorSectionToDuration(timeline) {
  const duration = getDuration(timeline);
  const sections = directorSections(timeline);
  if (!sections.length) return false;
  const last = sections.at(-1);
  if (!canFitLastDirectorSectionToDuration(timeline)) return false;
  last.end_time = duration;
  sortDirectorSections(timeline);
  syncShotTimingFromSections(timeline);
  return true;
}

export function fitDirectorSectionsEvenlyToDuration(timeline) {
  const duration = getDuration(timeline);
  const sections = directorSections(timeline);
  const maxEnd = Math.max(0, ...sections.map((section) => Number(section.end_time) || 0));
  if (!sections.length || duration <= 0 || maxEnd <= 0 || maxEnd <= duration) return false;
  const scale = duration / maxEnd;
  for (const section of sections) {
    section.start_time = Number(section.start_time) * scale;
    section.end_time = Number(section.end_time) * scale;
  }
  sortDirectorSections(timeline);
  syncShotTimingFromSections(timeline);
  return true;
}

export function findSection(timeline, itemId) {
  return timeline.director_track.sections.find((section) => section.item_id === itemId);
}

export function getSelectedSection(timeline) {
  return findSection(timeline, timeline.ui_state.selected_item_id);
}

export function selectItem(timeline, itemId) {
  setSelection(timeline, itemId ? [itemId] : []);
  return itemId;
}

export function toggleSelectItem(timeline, itemId) {
  if (!itemId || !findTimelineItem(timeline, itemId)) return getSelectedItemIds(timeline);
  const selected = getSelectedItemIds(timeline);
  const index = selected.indexOf(itemId);
  if (index >= 0) {
    selected.splice(index, 1);
  } else {
    selected.push(itemId);
  }
  setSelection(timeline, selected);
  return timeline.ui_state.selected_item_ids;
}

export function selectItemRange(timeline, itemId) {
  const target = findTimelineItem(timeline, itemId);
  if (!target) return [];
  const ordered = timelineItemsForKind(timeline, target.kind);
  const selected = getSelectedItemIds(timeline);
  const anchorId = [timeline.ui_state.selected_item_id, ...[...selected].reverse()]
    .find((id) => id && findTimelineItem(timeline, id)?.kind === target.kind) ?? itemId;
  const anchorIndex = ordered.findIndex((item) => item.item_id === anchorId);
  const targetIndex = ordered.findIndex((item) => item.item_id === itemId);
  if (anchorIndex < 0 || targetIndex < 0) {
    selectItem(timeline, itemId);
    return timeline.ui_state.selected_item_ids;
  }
  const start = Math.min(anchorIndex, targetIndex);
  const end = Math.max(anchorIndex, targetIndex);
  setSelection(timeline, ordered.slice(start, end + 1).map((item) => item.item_id), itemId);
  return timeline.ui_state.selected_item_ids;
}

export function clearSelection(timeline) {
  setSelection(timeline, []);
  return [];
}

export function collapseSelection(timeline, itemId = null) {
  const primary = itemId ?? timeline.ui_state.selected_item_id;
  if (primary && findTimelineItem(timeline, primary)) return selectItem(timeline, primary);
  const selected = getSelectedItemIds(timeline);
  return selectItem(timeline, selected.at(-1) ?? null);
}

export function getSelectedItemIds(timeline) {
  normalizeSelection(timeline);
  return [...(timeline.ui_state.selected_item_ids ?? [])];
}

export function isItemSelected(timeline, itemId) {
  return getSelectedItemIds(timeline).includes(itemId);
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
    section.video_guidance_range = "Last Frames";
    section.video_guidance_frame_count = 17;
  } else {
    section.prompt = "";
  }
  return section;
}

function setSelection(timeline, ids, primaryId = null) {
  const selected = [];
  for (const rawId of ids ?? []) {
    const id = String(rawId ?? "");
    if (!id || !findTimelineItem(timeline, id) || selected.includes(id)) continue;
    selected.push(id);
  }
  const primaryValue = primaryId == null ? null : String(primaryId);
  const primary = primaryValue && selected.includes(primaryValue) ? primaryValue : selected.at(-1);
  if (primary) {
    selected.splice(selected.indexOf(primary), 1);
    selected.push(primary);
  }
  timeline.ui_state ??= {};
  timeline.ui_state.selected_item_ids = selected;
  timeline.ui_state.selected_item_id = selected.at(-1) ?? null;
  return selected;
}

function normalizeSelection(timeline) {
  const uiState = timeline.ui_state ??= {};
  const ids = Array.isArray(uiState.selected_item_ids) && uiState.selected_item_ids.length
    ? uiState.selected_item_ids
    : (uiState.selected_item_id ? [uiState.selected_item_id] : []);
  return setSelection(timeline, ids, uiState.selected_item_id);
}

function selectedTimelineItems(timeline) {
  const selectedIds = new Set(getSelectedItemIds(timeline));
  return {
    sections: timeline.director_track.sections.filter((section) => selectedIds.has(section.item_id)),
    audioClips: timeline.audio_tracks.flatMap((track) => track.clips).filter((clip) => selectedIds.has(clip.item_id)),
  };
}

function selectedMoveDeltaBounds(timeline, selectedIds) {
  const duration = getDuration(timeline);
  let min = -Infinity;
  let max = Infinity;
  let movable = false;
  const unselectedSections = timeline.director_track.sections.filter((section) => !selectedIds.has(section.item_id));
  for (const section of timeline.director_track.sections) {
    if (!selectedIds.has(section.item_id)) continue;
    movable = true;
    min = Math.max(min, -Number(section.start_time));
    max = Math.min(max, duration - Number(section.end_time));
    for (const other of unselectedSections) {
      if (Number(other.end_time) <= Number(section.start_time)) {
        min = Math.max(min, Number(other.end_time) - Number(section.start_time));
      } else if (Number(other.start_time) >= Number(section.end_time)) {
        max = Math.min(max, Number(other.start_time) - Number(section.end_time));
      }
    }
  }
  for (const track of timeline.audio_tracks) {
    for (const clip of track.clips) {
      if (!selectedIds.has(clip.item_id) || clip.locked) continue;
      movable = true;
      min = Math.max(min, -Number(clip.start_time));
      max = Math.min(max, duration - Number(clip.end_time));
    }
  }
  return { min, max, movable };
}

function findTimelineItem(timeline, itemId) {
  const section = findSection(timeline, itemId);
  if (section) return { kind: "section", item: section };
  const shot = findShot(timeline, itemId);
  if (shot) return { kind: "shot", item: shot };
  const boundary = findBoundary(timeline, itemId);
  if (boundary) return { kind: "boundary", item: boundary };
  const take = findTakeWithShot(timeline, itemId);
  if (take) return { kind: "take", item: take.take, shot: take.shot };
  const match = findAudioClipWithTrack(timeline, itemId);
  if (match) return { kind: "audio", item: match.clip, track: match.track };
  return null;
}

function timelineItemsForKind(timeline, kind) {
  if (kind === "section") {
    return [...timeline.director_track.sections].sort((a, b) => Number(a.start_time) - Number(b.start_time) || Number(a.end_time) - Number(b.end_time));
  }
  if (kind === "shot") {
    return orderedShots(timeline).map((shot) => ({ ...shot, item_id: shot.shot_id }));
  }
  if (kind === "boundary") {
    return [...ensureSequence(timeline).boundaries].map((boundary) => ({ ...boundary, item_id: boundary.boundary_id }));
  }
  if (kind === "take") {
    return ensureSequence(timeline).shots
      .flatMap((shot) => (shot.takes ?? []).map((take) => ({ ...take, item_id: take.take_id })));
  }
  return timeline.audio_tracks
    .flatMap((track) => track.clips)
    .sort((a, b) => Number(a.start_time) - Number(b.start_time) || Number(a.lane ?? 0) - Number(b.lane ?? 0) || Number(a.end_time) - Number(b.end_time));
}

function ensureSequence(timeline) {
  timeline.sequence ??= createDefaultSequence();
  timeline.sequence.shots = Array.isArray(timeline.sequence.shots) ? timeline.sequence.shots : [];
  timeline.sequence.boundaries = Array.isArray(timeline.sequence.boundaries) ? timeline.sequence.boundaries : [];
  return timeline.sequence;
}

function ensureShotForSection(timeline, section) {
  if (!section?.item_id || findShotForSection(timeline, section.item_id)) return null;
  const sequence = ensureSequence(timeline);
  const shot = {
    ...createDefaultShot(sequence.shots.length + 1),
    shot_id: uniqueTimelineId(`shot_${sanitizeTimelineId(section.item_id, `section_${sequence.shots.length + 1}`)}`, existingShotIds(timeline)),
    start_time: Number(section.start_time ?? 0),
    end_time: Number(section.end_time ?? section.start_time ?? 0),
    section_ids: [String(section.item_id)],
  };
  sequence.shots.push(shot);
  return shot;
}

function cleanupShotSectionIds(timeline) {
  const existingSections = new Set((timeline.director_track?.sections ?? []).map((section) => section.item_id));
  const sequence = ensureSequence(timeline);
  for (const shot of sequence.shots) {
    shot.section_ids = (shot.section_ids ?? []).filter((sectionId) => existingSections.has(sectionId));
  }
  sequence.shots = sequence.shots
    .filter((shot) => {
      if ((shot.section_ids ?? []).length) return true;
      if ((shot.takes ?? []).length) return true;
      if (shot.clip_instance?.asset_id) return true;
      return Boolean(shot.name || Object.keys(shot.metadata ?? {}).length);
    });
  const shotIds = new Set(sequence.shots.map((shot) => shot.shot_id));
  sequence.boundaries = sequence.boundaries.filter((boundary) => shotIds.has(boundary.left_shot_id) && shotIds.has(boundary.right_shot_id));
}

function syncShotTimingFromSections(timeline) {
  const sectionById = new Map((timeline.director_track?.sections ?? []).map((section) => [section.item_id, section]));
  for (const shot of ensureSequence(timeline).shots) {
    const sections = (shot.section_ids ?? []).map((sectionId) => sectionById.get(sectionId)).filter(Boolean);
    if (!sections.length) continue;
    shot.start_time = Math.min(...sections.map((section) => Number(section.start_time)));
    shot.end_time = Math.max(...sections.map((section) => Number(section.end_time)));
  }
}

function orderedShots(timeline) {
  return [...ensureSequence(timeline).shots]
    .sort((a, b) => Number(a.start_time) - Number(b.start_time) || Number(a.end_time) - Number(b.end_time) || String(a.shot_id).localeCompare(String(b.shot_id)));
}

function areAdjacentShots(timeline, leftShotId, rightShotId) {
  const shots = orderedShots(timeline);
  const leftIndex = shots.findIndex((shot) => shot.shot_id === leftShotId);
  return leftIndex >= 0 && shots[leftIndex + 1]?.shot_id === rightShotId;
}

function applyBoundaryPatch(boundary, patch) {
  if (BOUNDARY_MODES.includes(patch.mode)) boundary.mode = patch.mode;
  if (Number.isFinite(Number(patch.tail_frames))) boundary.tail_frames = Math.max(0, Math.round(Number(patch.tail_frames)));
  if (Number.isFinite(Number(patch.blend_frames))) boundary.blend_frames = Math.max(0, Math.round(Number(patch.blend_frames)));
  if (patch.transition_prompt != null) boundary.transition_prompt = String(patch.transition_prompt);
  if (patch.reuse_character_refs != null) boundary.reuse_character_refs = patch.reuse_character_refs !== false;
  if (patch.reuse_style != null) boundary.reuse_style = patch.reuse_style !== false;
  if (patch.metadata && typeof patch.metadata === "object" && !Array.isArray(patch.metadata)) boundary.metadata = deepClone(patch.metadata);
}

function deleteBoundary(timeline, boundaryId) {
  const sequence = ensureSequence(timeline);
  const before = sequence.boundaries.length;
  sequence.boundaries = sequence.boundaries.filter((boundary) => boundary.boundary_id !== boundaryId);
  return before !== sequence.boundaries.length;
}

function deleteTakeById(timeline, takeId) {
  for (const shot of ensureSequence(timeline).shots) {
    if (deleteTake(timeline, shot.shot_id, takeId)) return true;
  }
  return false;
}

function findTakeWithShot(timeline, takeId) {
  for (const shot of ensureSequence(timeline).shots) {
    const take = (shot.takes ?? []).find((candidate) => candidate.take_id === takeId);
    if (take) return { shot, take };
  }
  return null;
}

function existingShotIds(timeline) {
  return new Set(ensureSequence(timeline).shots.map((shot) => shot.shot_id));
}

function existingBoundaryIds(timeline) {
  return new Set(ensureSequence(timeline).boundaries.map((boundary) => boundary.boundary_id));
}

function existingTakeIds(shot) {
  return new Set((shot.takes ?? []).map((take) => take.take_id));
}

function uniqueStrings(values) {
  const result = [];
  for (const value of values ?? []) {
    const stringValue = String(value ?? "");
    if (!stringValue || result.includes(stringValue)) continue;
    result.push(stringValue);
  }
  return result;
}

function uniqueTimelineId(baseId, existingIds) {
  const base = sanitizeTimelineId(baseId, "item");
  let candidate = base;
  let suffix = 2;
  while (existingIds.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  existingIds.add(candidate);
  return candidate;
}

function sanitizeTimelineId(value, fallback) {
  const sanitized = String(value ?? "").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
  return sanitized || fallback;
}

function assetExists(timeline, assetId) {
  return Boolean(assetId && timeline.assets?.some((asset) => asset.asset_id === assetId));
}

function videoAssetForId(timeline, assetId) {
  if (assetId == null) return null;
  return timeline.assets?.find((asset) => asset.asset_id === assetId && asset.type === ASSET_TYPE_VIDEO) ?? null;
}

function clearClipInstanceForTake(shot, take) {
  if (
    shot.clip_instance
    && take?.asset_id != null
    && shot.clip_instance.asset_id === take.asset_id
  ) {
    shot.clip_instance = null;
  }
}

function pruneUnreferencedGeneratedAsset(timeline, assetId) {
  if (!assetId || !Array.isArray(timeline.assets)) return false;
  const asset = timeline.assets.find((candidate) => candidate.asset_id === assetId);
  if (!asset || !isGeneratedAsset(asset) || assetReferenced(timeline, assetId)) return false;
  timeline.assets = timeline.assets.filter((candidate) => candidate.asset_id !== assetId);
  return true;
}

function isGeneratedAsset(asset) {
  return asset?.source_kind === ASSET_SOURCE_GENERATED || asset?.metadata?.source_kind === ASSET_SOURCE_GENERATED;
}

function assetReferenced(timeline, assetId) {
  const id = String(assetId);
  for (const section of timeline.director_track?.sections ?? []) {
    if (mediaReferenceAssetId(section.image) === id || mediaReferenceAssetId(section.video) === id) return true;
  }
  for (const track of timeline.audio_tracks ?? []) {
    for (const clip of track.clips ?? []) {
      if (mediaReferenceAssetId(clip.audio) === id) return true;
    }
  }
  for (const shot of ensureSequence(timeline).shots) {
    if (shot.clip_instance?.asset_id === id) return true;
    if ((shot.takes ?? []).some((take) => take.asset_id === id)) return true;
  }
  return false;
}

function mediaReferenceAssetId(reference) {
  return reference?.asset_id == null ? null : String(reference.asset_id);
}

function isValidLoraTarget(modelKey, targetKey) {
  return (
    (modelKey === MODEL_LORA_MODEL_LTX_2_3 && targetKey === MODEL_LORA_TARGET_MAIN) ||
    (modelKey === MODEL_LORA_MODEL_WAN_2_2 && (targetKey === MODEL_LORA_TARGET_HIGH_NOISE || targetKey === MODEL_LORA_TARGET_LOW_NOISE))
  );
}

function normalizeTimelineLoraStack(stack) {
  const source = stack && typeof stack === "object" && !Array.isArray(stack) ? stack : createDefaultLoraStack();
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

function findShotSectionInsertionStart(timeline, shot, duration) {
  const shotSections = sectionsForShot(timeline, shot);
  const preferredStart = shotSections.length
    ? Math.max(...shotSections.map((section) => Number(section.end_time)))
    : Number(shot.start_time ?? 0);
  return findGapForDuration(timeline, duration, clampStart(timeline, preferredStart, duration));
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

function getFollowingSections(timeline, section) {
  return [...timeline.director_track.sections]
    .sort((a, b) => a.start_time - b.start_time)
    .filter((candidate) => candidate.item_id !== section.item_id && candidate.start_time >= section.end_time);
}

function directorSections(timeline) {
  return [...(timeline.director_track?.sections ?? [])].sort((a, b) => Number(a.start_time) - Number(b.start_time) || Number(a.end_time) - Number(b.end_time));
}

function sectionsForShot(timeline, shot) {
  const sectionIds = new Set((shot?.section_ids ?? []).map((sectionId) => String(sectionId)));
  return directorSections(timeline).filter((section) => sectionIds.has(String(section.item_id)));
}

function resolveSectionTargetShot(timeline, options = {}) {
  if (options.forceStandalone) return null;
  if (options.shotId) return compatibleSectionTargetShot(timeline, findShot(timeline, options.shotId));
  const selectedId = timeline.ui_state?.selected_item_id;
  const selectedShot = findShot(timeline, selectedId);
  if (selectedShot) return compatibleSectionTargetShot(timeline, selectedShot);
  const selectedSection = findSection(timeline, selectedId);
  if (selectedSection) return compatibleSectionTargetShot(timeline, findShotForSection(timeline, selectedSection.item_id));
  return null;
}

function compatibleSectionTargetShot(timeline, shot) {
  if (!shot || shot.type === "Imported") return null;
  if (!["Generated", "Extended", "Edited", "Placeholder"].includes(shot.type)) return null;
  return findShot(timeline, shot.shot_id) ?? null;
}

function findAudioClipWithTrack(timeline, itemId) {
  for (const track of timeline.audio_tracks) {
    const clip = track.clips.find((candidate) => candidate.item_id === itemId);
    if (clip) return { track, clip };
  }
  return null;
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
