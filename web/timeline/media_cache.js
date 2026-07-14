import {
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
} from "./schema.js";
import { normalizeGlobalSettings } from "./global_settings.js";

export const MIN_WAVEFORM_PEAKS = 16;
export const MAX_WAVEFORM_PEAKS = 512;
export const DEFAULT_WAVEFORM_PEAKS = 96;

const LEASE_EXPIRY_MARGIN_MS = 1_000;
const SUPPORTED_ASSET_TYPES = new Set([
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
]);

export class TimelineMediaCache {
  constructor(node, app, managedMedia = null, fetchImpl = globalThis.fetch) {
    this.node = node;
    this.app = app;
    this.managedMedia = managedMedia;
    this.fetchImpl = typeof fetchImpl === "function" ? fetchImpl.bind(globalThis) : null;
    this.sourceReferences = new Map();
    this.assetSignatures = new Map();
    this.thumbnailUrls = new Map();
    this.viewUrls = new Map();
    this.waveforms = new Map();
    this.pendingThumbnails = new Map();
    this.pendingViews = new Map();
    this.pendingWaveforms = new Map();
    this.destroyed = false;
  }

  bindManagedMedia(managedMedia) {
    if (!managedMedia?.resolveSource || !managedMedia?.previewSource || !managedMedia?.viewSource) {
      throw new Error("PRIVACY_DIRECTOR_INSTALLATION_BLOCKED");
    }
    if (this.managedMedia === managedMedia) return this;
    this.clear();
    this.managedMedia = managedMedia;
    return this;
  }

  clear() {
    this.sourceReferences.clear();
    this.assetSignatures.clear();
    this.thumbnailUrls.clear();
    this.viewUrls.clear();
    this.waveforms.clear();
    this.pendingThumbnails.clear();
    this.pendingViews.clear();
    this.pendingWaveforms.clear();
  }

  destroy() {
    this.destroyed = true;
    this.clear();
  }

  refresh(timeline, globalSettings = null) {
    const settings = normalizeGlobalSettings(globalSettings);
    if (!this.managedMedia) return;
    for (const asset of timeline?.assets ?? []) {
      if (!asset?.asset_id || !assetPath(asset)) continue;
      if (
        (asset.type === ASSET_TYPE_IMAGE || asset.type === ASSET_TYPE_VIDEO)
        && settings.display.show_thumbnails
      ) this.requestThumbnail(asset);
    }
  }

  getThumbnailUrl(assetId, maxSize = 320) {
    return this.leaseUrl(this.thumbnailUrls, thumbnailKey(assetId, maxSize));
  }

  requestThumbnail(asset, maxSize = 320) {
    if (!validVisualAsset(asset)) return null;
    this.trackAsset(asset);
    const size = clampThumbnailSize(maxSize);
    const key = thumbnailKey(assetIdentity(asset), size);
    const cached = this.leaseUrl(this.thumbnailUrls, key);
    if (!cached && !this.pendingThumbnails.has(key)) {
      let pending;
      pending = this.loadThumbnail(asset, size).finally(() => {
        if (this.pendingThumbnails.get(key) === pending) this.pendingThumbnails.delete(key);
      });
      this.pendingThumbnails.set(key, pending);
    }
    return cached;
  }

  async acquireThumbnailUrl(asset, maxSize = 320) {
    const cached = this.requestThumbnail(asset, maxSize);
    if (cached) return cached;
    const key = thumbnailKey(assetIdentity(asset), clampThumbnailSize(maxSize));
    await this.pendingThumbnails.get(key);
    return this.leaseUrl(this.thumbnailUrls, key);
  }

  async loadThumbnail(asset, maxSize = 320) {
    try {
      const source = await this.resolveSource(asset);
      const lease = await this.managedMedia.previewSource(source, {
        maxSize: clampThumbnailSize(maxSize),
      });
      if (this.destroyed || !this.isCurrentAsset(asset)) return null;
      this.thumbnailUrls.set(
        thumbnailKey(assetIdentity(asset), maxSize),
        leaseEntry(lease),
      );
      this.notify();
      return lease.url;
    } catch (error) {
      this.forgetSource(asset);
      console.warn("Helto Director thumbnail cache failed", error);
      return null;
    }
  }

  getViewUrl(assetId) {
    return this.leaseUrl(this.viewUrls, String(assetId ?? ""));
  }

  requestView(asset) {
    if (!validAsset(asset)) return null;
    this.trackAsset(asset);
    const key = assetIdentity(asset);
    const cached = this.leaseUrl(this.viewUrls, key);
    if (!cached && !this.pendingViews.has(key)) {
      let pending;
      pending = this.loadView(asset).finally(() => {
        if (this.pendingViews.get(key) === pending) this.pendingViews.delete(key);
      });
      this.pendingViews.set(key, pending);
    }
    return cached;
  }

  async acquireViewUrl(asset) {
    const cached = this.requestView(asset);
    if (cached) return cached;
    const key = assetIdentity(asset);
    await this.pendingViews.get(key);
    return this.leaseUrl(this.viewUrls, key);
  }

  async loadView(asset) {
    try {
      const source = await this.resolveSource(asset);
      const lease = await this.managedMedia.viewSource(source);
      if (this.destroyed || !this.isCurrentAsset(asset)) return null;
      this.viewUrls.set(assetIdentity(asset), leaseEntry(lease));
      this.notify();
      return lease.url;
    } catch (error) {
      this.forgetSource(asset);
      console.warn("Helto Director media view cache failed", error);
      return null;
    }
  }

  getWaveform(assetId, peaks = DEFAULT_WAVEFORM_PEAKS) {
    return this.waveforms.get(waveformKey(assetId, peaks)) ?? null;
  }

  requestWaveform(asset, peaks = DEFAULT_WAVEFORM_PEAKS) {
    if (!validAsset(asset) || asset.type !== ASSET_TYPE_AUDIO) return null;
    this.trackAsset(asset);
    const peakCount = clampWaveformPeaks(peaks);
    const key = waveformKey(assetIdentity(asset), peakCount);
    const cached = this.waveforms.get(key) ?? null;
    if (!cached && !this.pendingWaveforms.has(key)) {
      let pending;
      pending = this.loadWaveform(asset, peakCount).finally(() => {
        if (this.pendingWaveforms.get(key) === pending) this.pendingWaveforms.delete(key);
      });
      this.pendingWaveforms.set(key, pending);
    }
    return cached;
  }

  async loadWaveform(asset, peaks = DEFAULT_WAVEFORM_PEAKS) {
    const peakCount = clampWaveformPeaks(peaks);
    const key = waveformKey(assetIdentity(asset), peakCount);
    try {
      if (!this.fetchImpl) throw new Error("PRIVACY_DIRECTOR_MEDIA_FETCH_UNAVAILABLE");
      const source = await this.resolveSource(asset);
      const lease = await this.managedMedia.previewSource(source, { peaks: peakCount });
      const response = await this.fetchImpl(lease.url, { cache: "no-store" });
      if (!response.ok) throw new Error("PRIVACY_DIRECTOR_WAVEFORM_UNAVAILABLE");
      const payload = normalizeWaveform(await response.json(), peakCount);
      if (this.destroyed || !this.isCurrentAsset(asset)) return null;
      this.waveforms.set(key, payload);
      this.notify();
      return payload;
    } catch (error) {
      this.forgetSource(asset);
      console.warn("Helto Director waveform cache failed", error);
      return null;
    }
  }

  async resolveSource(asset) {
    if (!this.managedMedia) throw new Error("PRIVACY_DIRECTOR_INSTALLATION_BLOCKED");
    if (!validAsset(asset)) throw new Error("PRIVACY_DIRECTOR_MEDIA_INVALID");
    const key = sourceKey(asset);
    let pending = this.sourceReferences.get(key);
    if (!pending) {
      pending = Promise.resolve(this.managedMedia.resolveSource({
        assetType: asset.type,
        path: assetPath(asset),
        sourceType: assetSourceType(asset),
      })).catch((error) => {
        this.sourceReferences.delete(key);
        throw error;
      });
      this.sourceReferences.set(key, pending);
    }
    return pending;
  }

  forgetSource(asset) {
    if (validAsset(asset)) this.sourceReferences.delete(sourceKey(asset));
  }

  trackAsset(asset) {
    const identity = assetIdentity(asset);
    const signature = sourceKey(asset);
    const previous = this.assetSignatures.get(identity);
    if (previous && previous !== signature) {
      this.viewUrls.delete(identity);
      for (const key of this.thumbnailUrls.keys()) {
        if (key.startsWith(`${identity}:`)) this.thumbnailUrls.delete(key);
      }
      for (const key of this.pendingThumbnails.keys()) {
        if (key.startsWith(`${identity}:`)) this.pendingThumbnails.delete(key);
      }
      this.pendingViews.delete(identity);
      for (const key of this.waveforms.keys()) {
        if (key.startsWith(`${identity}:`)) this.waveforms.delete(key);
      }
      for (const key of this.pendingWaveforms.keys()) {
        if (key.startsWith(`${identity}:`)) this.pendingWaveforms.delete(key);
      }
    }
    this.assetSignatures.set(identity, signature);
  }

  isCurrentAsset(asset) {
    return this.assetSignatures.get(assetIdentity(asset)) === sourceKey(asset);
  }

  leaseUrl(collection, key) {
    const entry = collection.get(key);
    if (!entry) return null;
    if (entry.expiresAt <= Date.now() + LEASE_EXPIRY_MARGIN_MS) {
      collection.delete(key);
      return null;
    }
    return entry.url;
  }

  notify() {
    this.node?._timelineRenderer?.render?.();
  }
}

export function mountTimelineMediaCache(node, app) {
  if (node._timelineMediaCache) return node._timelineMediaCache;
  const cache = new TimelineMediaCache(node, app);
  node._timelineMediaCache = cache;
  node.getTimelineThumbnailUrl = (assetId, maxSize) => cache.getThumbnailUrl(assetId, maxSize);
  node.getTimelineWaveform = (assetId, peaks) => cache.getWaveform(assetId, peaks);
  node.requestTimelineWaveform = (asset, peaks) => cache.requestWaveform(asset, peaks);
  return cache;
}

export function unmountTimelineMediaCache(node) {
  node?._timelineMediaCache?.destroy();
  delete node._timelineMediaCache;
}

export function clampWaveformPeaks(peaks) {
  const value = Number(peaks);
  if (!Number.isFinite(value)) return DEFAULT_WAVEFORM_PEAKS;
  return Math.max(MIN_WAVEFORM_PEAKS, Math.min(MAX_WAVEFORM_PEAKS, Math.round(value)));
}

function clampThumbnailSize(maxSize) {
  const value = Number(maxSize);
  if (!Number.isFinite(value)) return 320;
  return Math.max(32, Math.min(2048, Math.round(value)));
}

function assetPath(asset) {
  return String(asset?.path ?? asset?.file_path ?? "").trim();
}

function assetSourceType(asset) {
  return String(asset?.source_type ?? asset?.metadata?.source_type ?? "").trim();
}

function validAsset(asset) {
  return Boolean(
    asset
    && SUPPORTED_ASSET_TYPES.has(asset.type)
    && assetPath(asset),
  );
}

function validVisualAsset(asset) {
  return validAsset(asset)
    && (asset.type === ASSET_TYPE_IMAGE || asset.type === ASSET_TYPE_VIDEO);
}

function sourceKey(asset) {
  return `${asset.type}\0${assetPath(asset)}`;
}

function assetIdentity(asset) {
  const id = String(asset?.asset_id ?? "").trim();
  return id || sourceKey(asset);
}

function thumbnailKey(assetId, maxSize) {
  return `${String(assetId ?? "")}:${clampThumbnailSize(maxSize)}`;
}

function waveformKey(assetId, peaks) {
  return `${String(assetId ?? "")}:${clampWaveformPeaks(peaks)}`;
}

function leaseEntry(lease) {
  if (
    !lease
    || typeof lease.url !== "string"
    || !lease.url
    || !Number.isInteger(lease.expiresInSeconds)
    || lease.expiresInSeconds < 1
  ) throw new Error("PRIVACY_DIRECTOR_MEDIA_INVALID");
  return Object.freeze({
    url: lease.url,
    expiresAt: Date.now() + lease.expiresInSeconds * 1_000,
  });
}

function normalizeWaveform(payload, expectedPeaks) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("PRIVACY_DIRECTOR_MEDIA_INVALID");
  }
  const peaks = payload.peaks;
  if (
    !Array.isArray(peaks)
    || peaks.length !== expectedPeaks
    || peaks.some((peak) => typeof peak !== "number" || !Number.isFinite(peak) || peak < 0 || peak > 1)
  ) throw new Error("PRIVACY_DIRECTOR_MEDIA_INVALID");
  const duration = payload.duration_seconds;
  const sampleRate = payload.sample_rate;
  const channels = payload.channels;
  if (duration !== null && (typeof duration !== "number" || !Number.isFinite(duration) || duration < 0)) {
    throw new Error("PRIVACY_DIRECTOR_MEDIA_INVALID");
  }
  if (sampleRate !== null && (!Number.isInteger(sampleRate) || sampleRate < 1)) {
    throw new Error("PRIVACY_DIRECTOR_MEDIA_INVALID");
  }
  if (!Number.isInteger(channels) || channels < 0) {
    throw new Error("PRIVACY_DIRECTOR_MEDIA_INVALID");
  }
  return Object.freeze({
    duration_seconds: duration,
    sample_rate: sampleRate,
    channels,
    peaks: Object.freeze([...peaks]),
  });
}
