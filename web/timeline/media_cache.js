import {
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
} from "./schema.js";

const ROUTE_PREFIX = "/helto_director/media";
export const MIN_WAVEFORM_PEAKS = 16;
export const MAX_WAVEFORM_PEAKS = 512;
export const DEFAULT_WAVEFORM_PEAKS = 96;

export class TimelineMediaCache {
  constructor(node, app) {
    this.node = node;
    this.app = app;
    this.thumbnailUrls = new Map();
    this.waveforms = new Map();
    this.pendingWaveforms = new Set();
    this.destroyed = false;
    this.privacyMode = false;
  }

  destroy() {
    this.destroyed = true;
    this.thumbnailUrls.clear();
    this.waveforms.clear();
    this.pendingWaveforms.clear();
  }

  refresh(timeline) {
    const nextPrivacyMode = Boolean(timeline.project.privacy.mode);
    if (nextPrivacyMode !== this.privacyMode) {
      this.thumbnailUrls.clear();
      this.waveforms.clear();
      this.pendingWaveforms.clear();
    }
    this.privacyMode = nextPrivacyMode;

    for (const asset of timeline.assets ?? []) {
      if (!asset?.asset_id || !asset.path) continue;
      if ((asset.type === ASSET_TYPE_IMAGE || asset.type === ASSET_TYPE_VIDEO) && timeline.project.display.show_thumbnails) {
        this.thumbnailUrls.set(asset.asset_id, thumbnailUrl(asset, 320, this.privacyMode));
      }
    }
  }

  getThumbnailUrl(assetId) {
    return this.thumbnailUrls.get(assetId) ?? null;
  }

  getWaveform(assetId, peaks = DEFAULT_WAVEFORM_PEAKS) {
    return this.waveforms.get(waveformKey(assetId, peaks)) ?? null;
  }

  requestWaveform(asset, peaks = DEFAULT_WAVEFORM_PEAKS) {
    if (!asset?.asset_id || !asset.path) return null;
    const peakCount = clampWaveformPeaks(peaks);
    const key = waveformKey(asset.asset_id, peakCount, this.privacyMode);
    const cached = this.waveforms.get(key) ?? null;
    if (!cached) this.loadWaveform(asset, peakCount);
    return cached;
  }

  async loadWaveform(asset, peaks = DEFAULT_WAVEFORM_PEAKS) {
    const peakCount = clampWaveformPeaks(peaks);
    const key = waveformKey(asset.asset_id, peakCount, this.privacyMode);
    if (this.waveforms.has(key) || this.pendingWaveforms.has(key)) return;
    this.pendingWaveforms.add(key);
    try {
      const response = await fetch(waveformUrl(asset, peakCount, this.privacyMode));
      if (!response.ok) return;
      const payload = await response.json();
      if (this.destroyed) return;
      this.waveforms.set(key, {
        duration_seconds: payload.duration_seconds ?? null,
        sample_rate: payload.sample_rate ?? null,
        channels: payload.channels ?? 0,
        peaks: Array.isArray(payload.peaks) ? payload.peaks : [],
      });
      this.node?._timelineRenderer?.render?.();
    } catch (error) {
      console.warn("Helto Director waveform cache failed", error);
    } finally {
      this.pendingWaveforms.delete(key);
    }
  }
}

export function mountTimelineMediaCache(node, app) {
  if (node._timelineMediaCache) return node._timelineMediaCache;
  const cache = new TimelineMediaCache(node, app);
  node._timelineMediaCache = cache;
  node.getTimelineThumbnailUrl = (assetId) => cache.getThumbnailUrl(assetId);
  node.getTimelineWaveform = (assetId, peaks) => cache.getWaveform(assetId, peaks);
  node.requestTimelineWaveform = (asset, peaks) => cache.requestWaveform(asset, peaks);
  return cache;
}

export function unmountTimelineMediaCache(node) {
  node?._timelineMediaCache?.destroy();
  delete node._timelineMediaCache;
}

export function thumbnailUrl(asset, maxSize = 320, privacyMode = false) {
  return `${ROUTE_PREFIX}/thumbnail?${paramsFor(asset, { max_size: maxSize, ...(privacyMode ? { privacy: 1 } : {}) })}`;
}

export function waveformUrl(asset, peaks = 96, privacyMode = false) {
  return `${ROUTE_PREFIX}/waveform?${paramsFor(asset, { peaks: clampWaveformPeaks(peaks), ...(privacyMode ? { privacy: 1 } : {}) })}`;
}

export function mediaViewUrl(asset) {
  if (!asset?.path) return "";
  return `${ROUTE_PREFIX}/view?${paramsFor(asset)}`;
}

export function clampWaveformPeaks(peaks) {
  const value = Number(peaks);
  if (!Number.isFinite(value)) return DEFAULT_WAVEFORM_PEAKS;
  return Math.max(MIN_WAVEFORM_PEAKS, Math.min(MAX_WAVEFORM_PEAKS, Math.round(value)));
}

function waveformKey(assetId, peaks, privacyMode = false) {
  return `${assetId}:${clampWaveformPeaks(peaks)}:${privacyMode ? "private" : "plain"}`;
}

function paramsFor(asset, extra = {}) {
  const params = new URLSearchParams({
    path: asset.path,
    type: sourceTypeForAsset(asset),
    ...Object.fromEntries(Object.entries(extra).map(([key, value]) => [key, String(value)])),
  });
  return params.toString();
}

function sourceTypeForAsset(asset) {
  const sourceType = String(asset?.source_type ?? asset?.metadata?.source_type ?? "").trim();
  if (sourceType) return sourceType;
  return asset.source_kind === "UploadedFile" ? "input" : "";
}
