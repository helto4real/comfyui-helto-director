import {
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
} from "./schema.js";

const ROUTE_PREFIX = "/helto_director/media";

export class TimelineMediaCache {
  constructor(node, app) {
    this.node = node;
    this.app = app;
    this.thumbnailUrls = new Map();
    this.waveforms = new Map();
    this.pendingWaveforms = new Set();
    this.destroyed = false;
  }

  destroy() {
    this.destroyed = true;
    this.thumbnailUrls.clear();
    this.waveforms.clear();
    this.pendingWaveforms.clear();
  }

  refresh(timeline) {
    if (timeline.project.privacy.mode || timeline.project.privacy.hide_media_previews) {
      this.thumbnailUrls.clear();
      this.waveforms.clear();
      return;
    }

    for (const asset of timeline.assets ?? []) {
      if (!asset?.asset_id || !asset.path) continue;
      if ((asset.type === ASSET_TYPE_IMAGE || asset.type === ASSET_TYPE_VIDEO) && timeline.project.display.show_thumbnails) {
        this.thumbnailUrls.set(asset.asset_id, thumbnailUrl(asset));
      }
      if (asset.type === ASSET_TYPE_AUDIO && timeline.project.display.show_audio_waveforms) {
        this.loadWaveform(asset);
      }
    }
  }

  getThumbnailUrl(assetId) {
    return this.thumbnailUrls.get(assetId) ?? null;
  }

  getWaveform(assetId) {
    return this.waveforms.get(assetId) ?? null;
  }

  async loadWaveform(asset) {
    if (this.waveforms.has(asset.asset_id) || this.pendingWaveforms.has(asset.asset_id)) return;
    this.pendingWaveforms.add(asset.asset_id);
    try {
      const response = await fetch(waveformUrl(asset));
      if (!response.ok) return;
      const payload = await response.json();
      if (this.destroyed) return;
      this.waveforms.set(asset.asset_id, payload.peaks ?? []);
      this.node?._timelineRenderer?.render?.();
    } catch (error) {
      console.warn("Helto Director waveform cache failed", error);
    } finally {
      this.pendingWaveforms.delete(asset.asset_id);
    }
  }
}

export function mountTimelineMediaCache(node, app) {
  if (node._timelineMediaCache) return node._timelineMediaCache;
  const cache = new TimelineMediaCache(node, app);
  node._timelineMediaCache = cache;
  node.getTimelineThumbnailUrl = (assetId) => cache.getThumbnailUrl(assetId);
  node.getTimelineWaveform = (assetId) => cache.getWaveform(assetId);
  return cache;
}

export function unmountTimelineMediaCache(node) {
  node?._timelineMediaCache?.destroy();
  delete node._timelineMediaCache;
}

export function thumbnailUrl(asset, maxSize = 320) {
  return `${ROUTE_PREFIX}/thumbnail?${paramsFor(asset, { max_size: maxSize })}`;
}

export function waveformUrl(asset, peaks = 96) {
  return `${ROUTE_PREFIX}/waveform?${paramsFor(asset, { peaks })}`;
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
  return asset.source_kind === "UploadedFile" ? "input" : "";
}
