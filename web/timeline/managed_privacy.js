import { normalizeVideoTimeline } from "./migration.js";
import { applyVisibleNodeProperties } from "./state.js";
import { validateVideoTimeline } from "./validation.js";

export const DIRECTOR_TIMELINE_FIELD_ID = "timeline-state";
export const DIRECTOR_TIMELINE_WIDGET = "video_timeline_json";
export const DIRECTOR_SNAPSHOT_REASONS = Object.freeze([
  "manual-save",
  "autosave",
  "export",
  "graph-to-prompt",
  "direct-queue",
  "queue",
  "queue-manager",
  "partial-execution",
  "subgraph",
  "replay",
  "serialize",
]);

const CURRENT_SCHEMA = "helto.timeline-director";
const MAX_TRANSITION_OWNERS = 1024;
const MAX_TRANSITION_OWNER_BYTES = 2 * 1024 * 1024;

function fail(message = "Director managed timeline privacy is unavailable.") {
  throw new Error(message);
}

function nodeType(node) {
  return node?.comfyClass || node?.type;
}

function isTakeCaptureNode(node) {
  return nodeType(node) === "HeltoTimelineTakeCapture";
}

function requireNode(node) {
  if (nodeType(node) !== "HeltoVideoTimelineDirector") fail();
  return node;
}

function widget(node) {
  const found = requireNode(node)?.widgets?.find((item) => item?.name === DIRECTOR_TIMELINE_WIDGET);
  if (!found) fail("Director timeline widget is unavailable.");
  return found;
}

function controller(node) {
  return requireNode(node)?._videoTimelineStateController || null;
}

function isPrivate(node) {
  return controller(node)?.globalSettings?.privacy?.mode !== false;
}

function parse(value) {
  if (value && typeof value === "object") return value;
  if (typeof value !== "string" || !value.trim()) return null;
  try { return JSON.parse(value); } catch { return null; }
}

function isCurrentEnvelope(value) {
  const parsed = parse(value);
  return Boolean(
    parsed?.encrypted === true
    && parsed?.schema === CURRENT_SCHEMA
    && parsed?.algorithm === "AES-256-GCM",
  );
}

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

function normalizedState(value) {
  const parsed = parse(value) ?? value;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || isCurrentEnvelope(parsed)) {
    fail("Director timeline plaintext is unavailable.");
  }
  const timeline = Object.keys(parsed).length === 1 && parsed.timeline
    ? parsed.timeline : parsed;
  if (!timeline || typeof timeline !== "object" || Array.isArray(timeline) || isCurrentEnvelope(timeline)) {
    fail("Director timeline plaintext is unavailable.");
  }
  return { timeline: normalizeVideoTimeline(clone(timeline)) };
}

function cancelPending(node) {
  const state = controller(node);
  if (!state?.pendingDebounce) return;
  const clearTimer = state.window?.clearTimeout ?? globalThis.clearTimeout;
  clearTimer(state.pendingDebounce);
  state.pendingDebounce = null;
}

function captureTimeline(node) {
  const target = widget(node);
  if (
    node.__directorManagedTimelineLocked
    && (isPrivate(node) || isCurrentEnvelope(target.value))
  ) {
    fail("Director timeline is locked.");
  }
  const runtime = controller(node)?.timeline;
  return normalizedState(runtime ?? target.value);
}

function markEdited(workflowHandle, node) {
  if (typeof workflowHandle?.markEdited !== "function") fail();
  return workflowHandle.markEdited(node, DIRECTOR_TIMELINE_FIELD_ID);
}

export function createDirectorGlobalModeBrowserAdapter() {
  return {
    readDeclaredMode(node) {
      if (isTakeCaptureNode(node)) return "inherit";
      const mode = controller(node)?.globalSettings?.privacy?.mode;
      return mode === false ? "public" : "private";
    },
    writeDeclaredMode(node, mode) {
      if (!["private", "public"].includes(mode)) fail("Invalid Director privacy mode.");
      if (isTakeCaptureNode(node)) return;
      const state = controller(node);
      if (!state) fail();
      state.globalSettings ||= {};
      state.globalSettings.privacy ||= {};
      state.globalSettings.privacy.mode = mode === "private";
    },
    reconcileNode(node) {
      if (!isTakeCaptureNode(node)) requireNode(node);
    },
    reconcileNodeDefinition() {},
    onPrivacySessionChange() {},
  };
}

export function createDirectorTimelineBrowserAdapter({ workflowHandle = null, app = null } = {}) {
  const owners = new Set();
  const guardedWidgets = new WeakSet();
  const expectedReadbacks = new WeakMap();
  const externalReadbacks = new WeakMap();
  const encoder = new TextEncoder();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const appRef = app ?? workflowHandle?.app ?? null;
  let internalMutationDepth = 0;
  let locked = true;
  let transitionDepth = 0;

  function frozen() {
    return transitionDepth > 0;
  }

  function internalMutation() {
    return internalMutationDepth > 0;
  }

  function withInternalMutation(callback) {
    internalMutationDepth += 1;
    try {
      return callback();
    } finally {
      internalMutationDepth -= 1;
    }
  }

  function requireTransition() {
    if (!frozen()) fail("Director timeline transition is not settled.");
  }

  function requireMutable() {
    if (frozen() && !internalMutation()) {
      fail("Director timeline transition is in progress.");
    }
  }

  function exactBytes(value) {
    if (value instanceof Uint8Array) return new Uint8Array(value);
    if (value instanceof ArrayBuffer) return new Uint8Array(value.slice(0));
    if (ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer.slice(
        value.byteOffset,
        value.byteOffset + value.byteLength,
      ));
    }
    fail("Director timeline exact bytes are invalid.");
  }

  function equalBytes(left, right) {
    if (left.byteLength !== right.byteLength) return false;
    return left.every((value, index) => value === right[index]);
  }

  function encodeWidgetValue(value) {
    if (typeof value !== "string") fail("Director timeline widget is not exact UTF-8.");
    const encoded = encoder.encode(value);
    let roundTrip;
    try {
      roundTrip = decoder.decode(encoded);
    } catch {
      fail("Director timeline widget is not exact UTF-8.");
    }
    if (roundTrip !== value) fail("Director timeline widget is not exact UTF-8.");
    return encoded;
  }

  function decodeWidgetValue(value) {
    try {
      return decoder.decode(exactBytes(value));
    } catch {
      fail("Director timeline widget is not exact UTF-8.");
    }
  }

  function protectedValue(node) {
    return widget(node).value;
  }

  function bindWidgetGuard(node) {
    const target = widget(node);
    if (guardedWidgets.has(target)) return;
    const descriptor = Object.getOwnPropertyDescriptor(target, "value");
    if (descriptor && descriptor.configurable === false) {
      const options = target.options;
      const setValue = options?.setValue;
      if (typeof setValue !== "function") {
        fail("Director timeline widget cannot be transition-frozen.");
      }
      options.setValue = function guardedSetValue(value) {
        requireMutable();
        return setValue.call(this, value);
      };
      guardedWidgets.add(target);
      return;
    }
    if (descriptor?.get && !descriptor.set) {
      fail("Director timeline widget cannot be transition-frozen.");
    }
    let current = descriptor?.get ? descriptor.get.call(target) : target.value;
    Object.defineProperty(target, "value", {
      configurable: true,
      enumerable: descriptor?.enumerable ?? true,
      get() {
        return descriptor?.get ? descriptor.get.call(target) : current;
      },
      set(value) {
        requireMutable();
        if (descriptor?.set) descriptor.set.call(target, value);
        else current = value;
      },
    });
    guardedWidgets.add(target);
  }

  function withholdLockedPlaintext(node) {
    if (locked && (isPrivate(node) || isCurrentEnvelope(widget(node).value))) {
      const state = controller(node);
      if (state?.timeline != null) {
        state.timeline = null;
        state.privacyError = "Private timeline locked";
        state.node?._timelineRenderer?.renderLocked?.();
      }
    }
  }

  function recordGeneration(node, options) {
    return markEdited(workflowHandle, node, options);
  }

  function bindControllerGuards(node) {
    const state = controller(node);
    if (!state || state.__directorManagedPrivacyBound) return;
    const originalCommit = state.commitTimelineChange?.bind(state);
    const originalFlush = state.flushDebouncedCommit?.bind(state);
    const originalSchedule = state.scheduleDebouncedCommit?.bind(state);
    if (originalFlush) {
      state.flushDebouncedCommit = function managedTimelineFlush() {
        if (frozen() && !internalMutation()) return null;
        if (!state.pendingDebounce) return originalFlush(...arguments);
        state.__directorManagedFlushingPending = true;
        try {
          return originalFlush(...arguments);
        } finally {
          state.__directorManagedFlushingPending = false;
          state.__directorManagedPendingGeneration = false;
        }
      };
    }
    if (originalCommit) {
      state.commitTimelineChange = function managedTimelineCommit() {
        requireMutable();
        const generationAlreadyMarked = Boolean(
          state.__directorManagedPendingGeneration
          && state.__directorManagedFlushingPending,
        );
        const protectedSource = isPrivate(node) ? protectedValue(node) : null;
        state.__directorManagedPendingGeneration = false;
        cancelPending(node);
        if (!generationAlreadyMarked) {
          state.prepareTimelineGeneration?.();
          recordGeneration(node);
        }
        const args = [...arguments];
        args[1] = { ...(args[1] || {}), preparedGeneration: true };
        try {
          return originalCommit(...args);
        } finally {
          if (typeof protectedSource === "string") {
            widget(node).value = protectedSource;
          }
        }
      };
    }
    if (originalSchedule) {
      state.scheduleDebouncedCommit = function managedTimelineSchedule() {
        requireMutable();
        state.prepareTimelineGeneration?.();
        const result = originalSchedule(...arguments);
        try {
          recordGeneration(node);
          state.__directorManagedPendingGeneration = true;
        } catch (error) {
          cancelPending(node);
          state.__directorManagedPendingGeneration = false;
          throw error;
        }
        return result;
      };
    }
    const guardedMutators = [
      "beginTimelineGesture",
      "deleteSelectedTimelineItem",
      "endTimelineGesture",
      "loadTimelineState",
      "prepareTimelineGeneration",
      "redoTimelineChange",
      "replaceTimeline",
      "replaceTimelineFromLibrary",
      "retryPrivateWidgetWrite",
      "undoTimelineChange",
      "updateGlobalSettings",
      "updateTimeline",
    ];
    for (const name of guardedMutators) {
      const original = state[name]?.bind(state);
      if (!original) continue;
      state[name] = function managedTimelineMutation() {
        requireMutable();
        return original(...arguments);
      };
    }
    const originalSerializationFlush = state.flushTimelineBeforeSerialization?.bind(state);
    if (originalSerializationFlush) {
      state.flushTimelineBeforeSerialization = function managedSerializationFlush() {
        if (frozen() && !internalMutation()) return state.timeline;
        return originalSerializationFlush(...arguments);
      };
    }
    state.__directorManagedPrivacyBound = true;
  }

  function reconcileOwner(node) {
    requireNode(node);
    owners.add(node);
    node.__directorManagedTimelineLocked = locked;
    node.__directorManagedTimelineTransitionFrozen = frozen();
    bindWidgetGuard(node);
    bindControllerGuards(node);
    withholdLockedPlaintext(node);
  }

  function graphValues(value) {
    if (value instanceof Map) return [...value.values()];
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object") return Object.values(value);
    return [];
  }

  function stableLocatorPart(value) {
    const normalized = String(value ?? "");
    if (!/^[A-Za-z0-9._~:-]{1,128}$/u.test(normalized)) {
      fail("Director timeline owner identity is invalid.");
    }
    return normalized;
  }

  function liveGraphEntries() {
    const firstOwner = owners.values().next().value;
    const candidate = appRef?.rootGraph
      ?? appRef?.graph?.rootGraph
      ?? appRef?.graph
      ?? firstOwner?.graph?.rootGraph
      ?? firstOwner?.graph
      ?? null;
    if (!candidate) {
      return owners.size ? [{ graph: null, graphId: "root", nodes: [...owners] }] : [];
    }
    const root = candidate.rootGraph ?? candidate;
    const entries = [{ graph: root, graphId: "root", nodes: root?._nodes ?? [] }];
    const seen = new Set([root]);
    const pending = [
      ...graphValues(root?._subgraphs),
      ...graphValues(root?.subgraphs),
    ];
    for (let index = 0; index < pending.length; index += 1) {
      const graph = pending[index];
      if (!graph || seen.has(graph)) continue;
      seen.add(graph);
      entries.push({
        graph,
        graphId: stableLocatorPart(graph.id),
        nodes: Array.isArray(graph._nodes) ? graph._nodes : [],
      });
      pending.push(
        ...graphValues(graph._subgraphs),
        ...graphValues(graph.subgraphs),
      );
    }
    return entries;
  }

  function liveOwnerRecords() {
    const records = [];
    const identities = new Set();
    for (const entry of liveGraphEntries()) {
      for (const node of entry.nodes) {
        if (nodeType(node) !== "HeltoVideoTimelineDirector") continue;
        reconcileOwner(node);
        const widgetIndex = node.widgets.findIndex(
          (item) => item?.name === DIRECTOR_TIMELINE_WIDGET,
        );
        if (widgetIndex < 0) fail("Director timeline widget is unavailable.");
        const locator = Object.freeze({
          rootGraphId: "root",
          graphId: stableLocatorPart(entry.graphId),
          nodeId: stableLocatorPart(node.id),
        });
        const identity = JSON.stringify(locator);
        if (identities.has(identity)) fail("Director timeline owner identity is duplicated.");
        identities.add(identity);
        records.push(Object.freeze({ node, locator, widgetIndex }));
        if (records.length > MAX_TRANSITION_OWNERS) {
          fail("Director timeline transition owner limit was exceeded.");
        }
      }
    }
    return records;
  }

  function serializedGraphEntries(serialized) {
    if (!serialized || typeof serialized !== "object" || !Array.isArray(serialized.nodes)) {
      fail("Director detached workflow serialization is invalid.");
    }
    const entries = [{ graphId: "root", nodes: serialized.nodes }];
    const definitions = serialized.definitions?.subgraphs;
    if (definitions != null && !Array.isArray(definitions)) {
      fail("Director detached workflow serialization is invalid.");
    }
    for (const graph of definitions ?? []) {
      if (!graph || typeof graph !== "object" || !Array.isArray(graph.nodes)) {
        fail("Director detached workflow serialization is invalid.");
      }
      entries.push({ graphId: stableLocatorPart(graph.id), nodes: graph.nodes });
    }
    return entries;
  }

  function serializedOwnerLocators(serialized) {
    const locators = [];
    for (const entry of serializedGraphEntries(serialized)) {
      for (const node of entry.nodes) {
        if (nodeType(node) !== "HeltoVideoTimelineDirector") continue;
        locators.push({
          rootGraphId: "root",
          graphId: entry.graphId,
          nodeId: stableLocatorPart(node.id),
        });
      }
    }
    return locators;
  }

  function detachedSerialization() {
    const graph = appRef?.rootGraph ?? appRef?.graph ?? liveGraphEntries()[0]?.graph;
    if (typeof graph?.serialize !== "function") {
      fail("Director detached workflow serialization is unavailable.");
    }
    try {
      return graph.serialize();
    } catch {
      fail("Director detached workflow serialization is unavailable.");
    }
  }

  function offlineRepresentationCount(records, serialized) {
    const liveCounts = new Map();
    for (const { locator } of records) {
      const identity = JSON.stringify(locator);
      liveCounts.set(identity, (liveCounts.get(identity) ?? 0) + 1);
    }
    let count = 0;
    for (const locator of serializedOwnerLocators(serialized)) {
      const identity = JSON.stringify(locator);
      const remaining = liveCounts.get(identity) ?? 0;
      if (remaining > 0) liveCounts.set(identity, remaining - 1);
      else count += 1;
    }
    return count;
  }

  function requireOwner(owner) {
    if (!owner || typeof owner !== "object") fail("Director timeline owner is invalid.");
    const node = requireNode(owner.node);
    if (!owners.has(node)) fail("Director timeline owner is not live.");
    return owner;
  }

  function writeOwnerExact(owner, exact) {
    requireTransition();
    const record = requireOwner(owner);
    const bytes = exactBytes(exact);
    if (bytes.byteLength > MAX_TRANSITION_OWNER_BYTES) {
      fail("Director timeline transition owner is oversized.");
    }
    const value = decodeWidgetValue(bytes);
    withInternalMutation(() => {
      widget(record.node).value = value;
    });
    expectedReadbacks.set(record.node, { exact: bytes, verified: false, reloaded: false });
  }

  function externalOwnerRecord(owner) {
    const node = requireNode(owner?.node ?? owner);
    reconcileOwner(node);
    const record = liveOwnerRecords().find((item) => item.node === node);
    if (!record) fail("Director timeline owner is not live.");
    return record;
  }

  function externalOwnerIdentity(owner) {
    const record = externalOwnerRecord(owner);
    return Object.freeze({
      ...record.locator,
      fieldId: DIRECTOR_TIMELINE_FIELD_ID,
    });
  }

  function writeExternalOwnerExact(owner, exact) {
    requireTransition();
    const record = externalOwnerRecord(owner);
    const bytes = exactBytes(exact);
    if (bytes.byteLength > MAX_TRANSITION_OWNER_BYTES) {
      fail("Director timeline external-operation owner is oversized.");
    }
    const value = decodeWidgetValue(bytes);
    withInternalMutation(() => {
      widget(record.node).value = value;
    });
    externalReadbacks.set(record.node, {
      exact: bytes,
      verified: false,
      reloaded: false,
    });
  }

  function requireExternalReadback(owner) {
    const record = externalOwnerRecord(owner);
    const expectation = externalReadbacks.get(record.node);
    if (!expectation?.verified) {
      fail("Director timeline external-operation readback is incomplete.");
    }
    const current = encodeWidgetValue(widget(record.node).value);
    if (!equalBytes(current, expectation.exact)) {
      fail("Director timeline external-operation readback diverged.");
    }
    return { record, expectation };
  }

  function requireVerifiedReadback(owner) {
    const record = requireOwner(owner);
    const expectation = expectedReadbacks.get(record.node);
    if (!expectation?.verified) fail("Director timeline transition readback is incomplete.");
    const current = encodeWidgetValue(widget(record.node).value);
    if (!equalBytes(current, expectation.exact)) {
      fail("Director timeline transition readback diverged.");
    }
    return { record, expectation };
  }

  function detachedNode(owner, serialized) {
    const record = requireOwner(owner);
    const graph = serializedGraphEntries(serialized).find(
      (entry) => entry.graphId === record.locator.graphId,
    );
    const node = graph?.nodes.find(
      (item) => String(item?.id) === record.locator.nodeId,
    );
    if (!node || nodeType(node) !== "HeltoVideoTimelineDirector") {
      fail("Director detached timeline owner is unavailable.");
    }
    return { node, record };
  }

  function detachedWidgetValue(owner, serialized) {
    const { node, record } = detachedNode(owner, serialized);
    if (Array.isArray(node.widgets_values)) {
      const value = node.widgets_values[record.widgetIndex];
      if (typeof value !== "string") fail("Director detached timeline widget is invalid.");
      return value;
    }
    if (Array.isArray(node.widgets)) {
      const value = node.widgets.find(
        (item) => item?.name === DIRECTOR_TIMELINE_WIDGET,
      )?.value;
      if (typeof value !== "string") fail("Director detached timeline widget is invalid.");
      return value;
    }
    fail("Director detached timeline widget is unavailable.");
  }

  function reloadPublicRuntime(owner) {
    const { record, expectation } = requireVerifiedReadback(owner);
    const current = widget(record.node).value;
    if (isCurrentEnvelope(current)) return;
    const normalized = normalizedState(current).timeline;
    const state = controller(record.node);
    if (!state) fail();
    withInternalMutation(() => {
      state.timeline = normalized;
      applyVisibleNodeProperties(state.timeline, record.node);
      state.timeline.validation = validateVideoTimeline(
        state.timeline,
        state.globalSettings,
      );
      state.privacyError = "";
      state.requestRender?.();
      state.refreshAsyncMediaCaches?.("managed privacy transition", {});
    });
    expectation.reloaded = true;
  }

  return {
    capture(node) {
      return captureTimeline(node);
    },
    normalize(node) {
      return captureTimeline(node);
    },
    readProtected(node) {
      requireNode(node);
      return protectedValue(node);
    },
    writeProtected(node, value) {
      requireNode(node);
      if (value == null) fail();
      const serialized = typeof value === "string" ? value : JSON.stringify(value);
      withInternalMutation(() => {
        widget(node).value = serialized;
      });
    },
    apply(node, value) {
      const normalized = normalizedState(value).timeline;
      const state = controller(node);
      if (!state) fail();
      state.timeline = normalized;
      applyVisibleNodeProperties(state.timeline, node);
      state.timeline.validation = validateVideoTimeline(
        state.timeline,
        state.globalSettings,
      );
      state.privacyError = "";
      state.requestRender?.();
      state.refreshAsyncMediaCaches?.("managed privacy reveal", {});
    },
    clear(node) {
      const state = controller(node);
      if (state) {
        state.timeline = null;
        state.privacyError = "Private timeline locked";
        state.node?._timelineRenderer?.renderLocked?.();
      }
    },
    markEditorGeneration(node) {
      return recordGeneration(node);
    },
    reconcileNode(node) {
      reconcileOwner(node);
    },
    reconcileNodeDefinition() {},
    onPrivacySessionChange(snapshot) {
      locked = snapshot?.state !== "ready" && snapshot?.state !== "unlocked";
      for (const node of owners) {
        node.__directorManagedTimelineLocked = locked;
        withholdLockedPlaintext(node);
      }
    },
    settleModeTransition() {
      transitionDepth += 1;
      for (const node of owners) node.__directorManagedTimelineTransitionFrozen = true;
      let released = false;
      const settled = Promise.resolve().then(() => {
        const before = liveOwnerRecords();
        withInternalMutation(() => {
          for (const { node } of before) {
            const state = controller(node);
            if (state?.pendingDebounce && state.timeline != null && !locked) {
              try {
                state.flushDebouncedCommit?.("managed privacy transition settle", {
                  markDirty: false,
                  rerender: false,
                });
              } finally {
                cancelPending(node);
              }
            } else {
              cancelPending(node);
            }
          }
        });
        const records = liveOwnerRecords();
        const serialized = detachedSerialization();
        return Object.freeze({
          offlineRepresentationCount: offlineRepresentationCount(records, serialized),
        });
      });
      return Object.freeze({
        settled,
        async release() {
          if (released) return;
          released = true;
          transitionDepth = Math.max(0, transitionDepth - 1);
          for (const node of owners) {
            node.__directorManagedTimelineTransitionFrozen = frozen();
          }
        },
      });
    },
    settleExternalOperation(owner) {
      externalOwnerRecord(owner);
      transitionDepth += 1;
      for (const node of owners) node.__directorManagedTimelineTransitionFrozen = true;
      let released = false;
      const settled = Promise.resolve().then(async () => {
        const records = liveOwnerRecords();
        withInternalMutation(() => {
          for (const { node } of records) {
            const state = controller(node);
            if (state?.pendingDebounce && state.timeline != null && !locked) {
              try {
                state.flushDebouncedCommit?.("managed external operation settle", {
                  markDirty: false,
                  rerender: false,
                });
              } finally {
                cancelPending(node);
              }
            } else {
              cancelPending(node);
            }
          }
        });
        if (typeof workflowHandle?.runWithSnapshot !== "function") fail();
        await workflowHandle.runWithSnapshot("serialize", async () => null);
      });
      return Object.freeze({
        settled,
        async release() {
          if (released) return;
          released = true;
          transitionDepth = Math.max(0, transitionDepth - 1);
          for (const node of owners) {
            node.__directorManagedTimelineTransitionFrozen = frozen();
          }
        },
      });
    },
    identifyExternalOperationOwner(owner) {
      requireTransition();
      return externalOwnerIdentity(owner);
    },
    resolveExternalOperationOwner(identity) {
      requireTransition();
      if (
        !identity
        || identity.fieldId !== DIRECTOR_TIMELINE_FIELD_ID
        || identity.rootGraphId !== "root"
      ) fail("Director timeline external-operation identity is invalid.");
      const record = liveOwnerRecords().find((item) => (
        item.locator.rootGraphId === identity.rootGraphId
        && item.locator.graphId === identity.graphId
        && item.locator.nodeId === identity.nodeId
      ));
      if (!record) fail("Director timeline external-operation owner is unavailable.");
      return record;
    },
    readExternalOperationExact(owner) {
      requireTransition();
      const record = externalOwnerRecord(owner);
      const exact = encodeWidgetValue(widget(record.node).value);
      if (exact.byteLength > MAX_TRANSITION_OWNER_BYTES) {
        fail("Director timeline external-operation owner is oversized.");
      }
      const expectation = externalReadbacks.get(record.node);
      if (expectation && equalBytes(exact, expectation.exact)) {
        expectation.verified = true;
      }
      return exact;
    },
    async applyExternalOperation(owner, value) {
      requireTransition();
      const record = externalOwnerRecord(owner);
      const normalized = normalizedState(value).timeline;
      const state = controller(record.node);
      if (!state) fail();
      withInternalMutation(() => {
        state.timeline = normalized;
        applyVisibleNodeProperties(state.timeline, record.node);
        state.timeline.validation = validateVideoTimeline(
          state.timeline,
          state.globalSettings,
        );
        state.privacyError = "";
        if (!isPrivate(record.node)) widget(record.node).value = JSON.stringify(normalized);
        recordGeneration(record.node);
        state.requestRender?.();
        state.refreshAsyncMediaCaches?.("managed external operation", {});
      });
      if (typeof workflowHandle?.runWithSnapshot !== "function") fail();
      await workflowHandle.runWithSnapshot("serialize", async () => null);
    },
    restoreExternalOperationExact(owner, exact) {
      writeExternalOwnerExact(owner, exact);
    },
    async reloadExternalOperationRuntime(owner) {
      requireTransition();
      const { record, expectation } = requireExternalReadback(owner);
      if (isCurrentEnvelope(widget(record.node).value)) {
        if (typeof workflowHandle?.reload !== "function") fail();
        await workflowHandle.reload(record.node, DIRECTOR_TIMELINE_FIELD_ID);
      } else {
        const normalized = normalizedState(widget(record.node).value).timeline;
        const state = controller(record.node);
        if (!state) fail();
        withInternalMutation(() => {
          state.timeline = normalized;
          applyVisibleNodeProperties(state.timeline, record.node);
          state.timeline.validation = validateVideoTimeline(
            state.timeline,
            state.globalSettings,
          );
          state.privacyError = "";
        });
      }
      if (!equalBytes(
        encodeWidgetValue(widget(record.node).value),
        expectation.exact,
      )) fail("Director timeline external-operation reload diverged.");
      expectation.reloaded = true;
    },
    reconcileExternalOperationRuntime(owner) {
      requireTransition();
      const { record, expectation } = requireExternalReadback(owner);
      if (!expectation.reloaded) {
        fail("Director timeline external-operation runtime was not reloaded.");
      }
      controller(record.node)?.requestRender?.();
      externalReadbacks.delete(record.node);
    },
    inventoryModeTransitionOwners() {
      requireTransition();
      return liveOwnerRecords().map((owner) => Object.freeze({
        owner,
        ...owner.locator,
      }));
    },
    readModeTransitionOwnerExact(owner) {
      requireTransition();
      const record = requireOwner(owner);
      const exact = encodeWidgetValue(widget(record.node).value);
      if (exact.byteLength > MAX_TRANSITION_OWNER_BYTES) {
        fail("Director timeline transition owner is oversized.");
      }
      const expectation = expectedReadbacks.get(record.node);
      if (expectation && equalBytes(exact, expectation.exact)) {
        expectation.verified = true;
      }
      return exact;
    },
    applyModeTransitionOwnerExact(owner, exact) {
      writeOwnerExact(owner, exact);
    },
    extractDetachedModeTransitionOwnerExact(owner, serialized) {
      requireTransition();
      const exact = encodeWidgetValue(detachedWidgetValue(owner, serialized));
      if (exact.byteLength > MAX_TRANSITION_OWNER_BYTES) {
        fail("Director timeline transition owner is oversized.");
      }
      return exact;
    },
    restoreModeTransitionOwnerExact(owner, exact) {
      writeOwnerExact(owner, exact);
    },
    reloadModeTransitionRuntime(owner) {
      requireTransition();
      reloadPublicRuntime(owner);
    },
    reconcileModeTransitionRuntime(owner) {
      requireTransition();
      const { record, expectation } = requireVerifiedReadback(owner);
      const value = widget(record.node).value;
      if (isCurrentEnvelope(value)) {
        withholdLockedPlaintext(record.node);
      } else if (!expectation.reloaded) {
        fail("Director public timeline runtime was not reloaded.");
      }
      controller(record.node)?.requestRender?.();
      expectedReadbacks.delete(record.node);
    },
  };
}

export function runDirectorSnapshot(workflowHandle, reason, callback) {
  if (!DIRECTOR_SNAPSHOT_REASONS.includes(reason)) fail("Invalid Director snapshot reason.");
  if (typeof workflowHandle?.runWithSnapshot !== "function" || typeof callback !== "function") fail();
  return workflowHandle.runWithSnapshot(reason, callback);
}

export function prepareDirectorRender(executionHandle, node) {
  if (typeof executionHandle?.prepare !== "function") fail();
  return executionHandle.prepare(node, "render-timeline");
}
