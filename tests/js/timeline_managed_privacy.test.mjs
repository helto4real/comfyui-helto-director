import assert from "node:assert/strict";

import {
  DIRECTOR_SNAPSHOT_REASONS,
  createDirectorGlobalModeBrowserAdapter,
  createDirectorTimelineBrowserAdapter,
  prepareDirectorRender,
  runDirectorSnapshot,
} from "../../web/timeline/managed_privacy.js";

function timeline(duration = 5) {
  return {
    schema_version: 1,
    type: "VIDEO_TIMELINE",
    project: {
      duration_seconds: duration,
      frame_rate: 24,
      aspect_ratio: "16:9",
      orientation: "Landscape",
      quality_preset: "High",
    },
    ui_state: {},
    assets: [],
    sequence: { sequence_id: "main", name: "Main", shots: [], boundaries: [] },
    director_track: { track_id: "director", sections: [] },
    audio_tracks: [],
    model_outputs: {},
    validation: { is_valid: true, errors: [], warnings: [], info: [] },
  };
}

function fakeNode(widgetValue) {
  const events = {
    captures: [],
    commits: 0,
    flushes: 0,
    lockedRenders: 0,
    marks: [],
    prepares: 0,
    renders: 0,
    schedules: 0,
  };
  const controller = {
    timeline: timeline(),
    pendingDebounce: 1,
    globalSettings: { privacy: { mode: true } },
    flushDebouncedCommit() {
      events.flushes += 1;
      this.pendingDebounce = null;
      return this.commitTimelineChange("prompt typing");
    },
    scheduleDebouncedCommit() {
      events.schedules += 1;
      this.pendingDebounce = 123;
    },
    requestRender() { events.renders += 1; },
    prepareTimelineGeneration() {
      events.prepares += 1;
      const revision = Number(this.timeline.ui_state.state_revision || 0) + 1;
      this.timeline.ui_state.state_revision = revision;
      this.timeline.validation = { is_valid: true, prepared_revision: revision };
      return this.timeline;
    },
    commitTimelineChange() {
      events.commits += 1;
      this.node.widgets[0].value = "LOCAL_SYNC_ENVELOPE";
      return this.timeline;
    },
  };
  const node = {
    id: 17,
    type: "HeltoVideoTimelineDirector",
    widgets: [{ name: "video_timeline_json", value: widgetValue }],
    _videoTimelineStateController: controller,
    _timelineRenderer: {
      renderLocked() { events.lockedRenders += 1; },
    },
  };
  controller.node = node;
  return { node, controller, events };
}

function useVueDomWidget(node, widgetValue) {
  let current = widgetValue;
  const options = {
    getValue() { return current; },
    setValue(value) { current = value; },
  };
  const widget = { name: "video_timeline_json", options };
  Object.defineProperty(widget, "value", {
    configurable: false,
    get() { return options.getValue(); },
    set(value) { options.setValue(value); },
  });
  node.widgets = [widget];
  return widget;
}

function serializedNode(node) {
  node.flushTimelineBeforeSerialization?.();
  return {
    id: node.id,
    type: node.type,
    widgets_values: node.widgets.map((item) => item.value),
  };
}

function graphHarness(rootNodes, { subgraphs = [], offlineRootNodes = [] } = {}) {
  const rootGraph = {
    id: "root-runtime-id",
    _nodes: rootNodes,
    _subgraphs: new Map(),
    serialize() {
      const serialized = {
        nodes: [
          ...this._nodes.map(serializedNode),
          ...offlineRootNodes,
        ],
      };
      if (this._subgraphs.size) {
        serialized.definitions = {
          subgraphs: [...this._subgraphs.values()].map((graph) => ({
            id: graph.id,
            nodes: graph._nodes.map(serializedNode),
          })),
        };
      }
      return serialized;
    },
  };
  rootGraph.rootGraph = rootGraph;
  for (const node of rootNodes) node.graph = rootGraph;
  for (const definition of subgraphs) {
    const graph = {
      id: definition.id,
      _nodes: definition.nodes,
      rootGraph,
    };
    for (const node of graph._nodes) node.graph = graph;
    rootGraph._subgraphs.set(graph.id, graph);
  }
  return { app: { rootGraph, graph: rootGraph }, rootGraph };
}

{
  const plaintext = JSON.stringify(timeline(6));
  const { node, controller, events } = fakeNode(plaintext);
  const adapter = createDirectorTimelineBrowserAdapter({
    workflowHandle: { markEdited() {} },
  });

  adapter.reconcileNode(node);

  assert.equal(controller.timeline, null, "locked private plaintext must be withheld");
  assert.equal(node.widgets[0].value, plaintext, "locked source bytes must be preserved");
  assert.equal(events.lockedRenders, 1, "locked rendering must not dereference plaintext state");
  assert.throws(() => adapter.normalize(node), /locked|plaintext is unavailable/i);
}

const lockedEnvelope = JSON.stringify({
  version: 1,
  schema: "helto.timeline-director",
  encrypted: true,
  algorithm: "AES-256-GCM",
  keyId: "synthetic-key",
  nonce: "synthetic-nonce",
  ciphertext: "SYNTHETIC_LOCKED_TIMELINE_CIPHERTEXT",
});

{
  const adapter = createDirectorTimelineBrowserAdapter();
  for (const method of [
    "settleExternalOperation",
    "identifyExternalOperationOwner",
    "resolveExternalOperationOwner",
    "readExternalOperationExact",
    "applyExternalOperation",
    "restoreExternalOperationExact",
    "reloadExternalOperationRuntime",
    "reconcileExternalOperationRuntime",
    "settleModeTransition",
    "inventoryModeTransitionOwners",
    "readModeTransitionOwnerExact",
    "applyModeTransitionOwnerExact",
    "extractDetachedModeTransitionOwnerExact",
    "restoreModeTransitionOwnerExact",
    "reloadModeTransitionRuntime",
    "reconcileModeTransitionRuntime",
  ]) {
    assert.equal(typeof adapter[method], "function", `${method} must be implemented`);
  }
}

{
  const source = JSON.stringify(timeline(6));
  const { node, controller } = fakeNode(source);
  const widget = useVueDomWidget(node, source);
  controller.pendingDebounce = null;
  controller.globalSettings.privacy.mode = false;
  const { app } = graphHarness([node]);
  const adapter = createDirectorTimelineBrowserAdapter({ app });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  adapter.reconcileNode(node);

  widget.value = JSON.stringify(timeline(7));
  assert.equal(JSON.parse(widget.value).project.duration_seconds, 7);

  const transition = adapter.settleModeTransition();
  await transition.settled;
  assert.throws(
    () => { widget.value = JSON.stringify(timeline(8)); },
    /transition is in progress/i,
    "Vue DOM widget writes must remain fail-closed while a transition is frozen",
  );
  await transition.release();

  widget.value = JSON.stringify(timeline(9));
  assert.equal(JSON.parse(widget.value).project.duration_seconds, 9);
}

{
  const originalTimeline = timeline(8);
  const original = JSON.stringify(originalTimeline);
  const { node, controller, events } = fakeNode(original);
  controller.pendingDebounce = null;
  controller.globalSettings.privacy.mode = false;
  const { app } = graphHarness([node]);
  const snapshots = [];
  const workflowHandle = {
    app,
    markEdited(owner, fieldId) { events.marks.push([owner.id, fieldId]); },
    runWithSnapshot(reason, callback) {
      snapshots.push(reason);
      return callback();
    },
  };
  const adapter = createDirectorTimelineBrowserAdapter({ app, workflowHandle });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  adapter.reconcileNode(node);

  const settlement = adapter.settleExternalOperation(node);
  await settlement.settled;
  const identity = adapter.identifyExternalOperationOwner(node);
  assert.deepEqual(identity, {
    rootGraphId: "root",
    graphId: "root",
    nodeId: "17",
    fieldId: "timeline-state",
  });
  const owner = adapter.resolveExternalOperationOwner(identity);
  const originalExact = adapter.readExternalOperationExact(owner);
  const target = timeline(13);
  await adapter.applyExternalOperation(owner, target);
  const targetExact = adapter.readExternalOperationExact(owner);
  assert.notDeepEqual(targetExact, originalExact);
  assert.equal(JSON.parse(new TextDecoder().decode(targetExact)).project.duration_seconds, 13);

  adapter.restoreExternalOperationExact(owner, targetExact);
  assert.deepEqual(adapter.readExternalOperationExact(owner), targetExact);
  await adapter.reloadExternalOperationRuntime(owner);
  adapter.reconcileExternalOperationRuntime(owner);
  assert.equal(controller.timeline.project.duration_seconds, 13);

  await adapter.applyExternalOperation(owner, timeline(21));
  adapter.restoreExternalOperationExact(owner, originalExact);
  assert.deepEqual(adapter.readExternalOperationExact(owner), originalExact);
  await adapter.reloadExternalOperationRuntime(owner);
  adapter.reconcileExternalOperationRuntime(owner);
  assert.equal(controller.timeline.project.duration_seconds, 8);
  assert.deepEqual(events.marks, [[17, "timeline-state"], [17, "timeline-state"]]);
  assert.deepEqual(snapshots, ["serialize", "serialize", "serialize"]);
  await settlement.release();
}

{
  const { node, controller, events } = fakeNode(lockedEnvelope);
  const workflowHandle = {
    markEdited(owner, fieldId) { events.marks.push([owner.id, fieldId]); },
  };
  const adapter = createDirectorTimelineBrowserAdapter({ workflowHandle });
  adapter.reconcileNode(node);

  assert.equal(node.__directorManagedTimelineLocked, true);
  assert.equal(controller.timeline, null, "locked state must not become a default timeline");
  assert.equal(node.widgets[0].value, lockedEnvelope);
  assert.equal(adapter.readProtected(node), lockedEnvelope);
  assert.throws(() => adapter.normalize(node), /locked|plaintext is unavailable/i);

  adapter.writeProtected(node, lockedEnvelope);
  assert.equal(adapter.readProtected(node), lockedEnvelope, "unchanged ciphertext must be byte-identical");
  assert.equal(node.widgets[0].value, lockedEnvelope);
}

{
  const { node, controller, events } = fakeNode(JSON.stringify(timeline(7)));
  const workflowHandle = {
    markEdited(owner, fieldId) {
      events.marks.push([owner.id, fieldId]);
      events.captures.push({
        revision: controller.timeline.ui_state.state_revision,
        validation: { ...controller.timeline.validation },
      });
    },
  };
  const adapter = createDirectorTimelineBrowserAdapter({ workflowHandle });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  adapter.reconcileNode(node);
  controller.timeline = timeline(7);
  controller.scheduleDebouncedCommit("prompt typing");

  assert.equal(events.schedules, 1);
  assert.equal(events.prepares, 1);
  assert.deepEqual(events.marks, [[17, "timeline-state"]]);
  assert.deepEqual(events.captures, [{
    revision: 1,
    validation: { is_valid: true, prepared_revision: 1 },
  }]);
  assert.equal(events.commits, 0);

  const normalized = adapter.normalize(node);
  assert.equal(normalized.timeline.project.duration_seconds, 7);
  assert.equal(events.flushes, 0, "snapshot capture reads the marked live generation");
  controller.flushDebouncedCommit("prompt typing");
  assert.equal(events.commits, 1);
  assert.equal(controller.timeline.ui_state.state_revision, 1);
  assert.deepEqual(controller.timeline.validation, events.captures[0].validation);
  assert.deepEqual(
    events.marks,
    [[17, "timeline-state"]],
    "the later debounce flush must not advance the settled generation",
  );
  assert.notEqual(node.widgets[0].value, "LOCAL_SYNC_ENVELOPE");
  adapter.markEditorGeneration(node);
  assert.deepEqual(events.marks, [[17, "timeline-state"], [17, "timeline-state"]]);

  adapter.apply(node, { timeline: timeline(11) });
  assert.equal(controller.timeline.project.duration_seconds, 11);
  assert.equal(controller.timeline.validation.is_valid, true);
  adapter.clear(node);
  assert.equal(controller.timeline, null);
}

{
  const source = JSON.stringify(timeline(7));
  const { node, controller, events } = fakeNode(source);
  const adapter = createDirectorTimelineBrowserAdapter({
    workflowHandle: {
      markEdited() { throw new Error("PRIVACY_SNAPSHOT_REPLACEMENT_BLOCKED"); },
    },
  });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  adapter.reconcileNode(node);

  assert.throws(
    () => controller.commitTimelineChange("blocked edit"),
    /PRIVACY_SNAPSHOT_REPLACEMENT_BLOCKED/,
  );
  assert.equal(events.commits, 0, "shared replacement rejection must precede local writes");
  assert.equal(node.widgets[0].value, source);
}

{
  const { node, controller, events } = fakeNode(JSON.stringify(timeline(7)));
  const adapter = createDirectorTimelineBrowserAdapter({
    workflowHandle: {
      markEdited(owner, fieldId) { events.marks.push([owner.id, fieldId]); },
    },
  });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  adapter.reconcileNode(node);

  controller.scheduleDebouncedCommit("prompt typing");
  controller.timeline.project.duration_seconds = 9;
  controller.commitTimelineChange("unrelated immediate edit");

  assert.equal(events.commits, 1);
  assert.deepEqual(
    events.marks,
    [[17, "timeline-state"], [17, "timeline-state"]],
    "an unrelated commit must not borrow the pending debounce generation",
  );
}

{
  const { node, controller, events } = fakeNode(lockedEnvelope);
  controller.updateTimeline = function updateTimeline(mutator) {
    mutator(this.timeline);
    return this.commitTimelineChange("direct edit");
  };
  controller.flushTimelineBeforeSerialization = function flushForSerialization() {
    return this.flushDebouncedCommit("serialize", { markDirty: false });
  };
  node.flushTimelineBeforeSerialization = () => controller.flushTimelineBeforeSerialization();
  const { app, rootGraph } = graphHarness([node]);
  const adapter = createDirectorTimelineBrowserAdapter({
    app,
    workflowHandle: {
      markEdited(owner, fieldId) { events.marks.push([owner.id, fieldId]); },
    },
  });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  adapter.reconcileNode(node);

  const privateToPublic = adapter.settleModeTransition();
  assert.deepEqual(
    await privateToPublic.settled,
    { offlineRepresentationCount: 0 },
  );
  assert.equal(events.flushes, 1, "pending debounce must settle before inventory");
  const [owner] = await adapter.inventoryModeTransitionOwners();
  assert.deepEqual(
    { rootGraphId: owner.rootGraphId, graphId: owner.graphId, nodeId: owner.nodeId },
    { rootGraphId: "root", graphId: "root", nodeId: "17" },
  );
  assert.equal(
    Buffer.from(await adapter.readModeTransitionOwnerExact(owner.owner)).toString(),
    lockedEnvelope,
  );

  const commitsBeforeFreezeChecks = events.commits;
  assert.throws(() => controller.commitTimelineChange("blocked"), /transition is in progress/i);
  assert.throws(() => controller.scheduleDebouncedCommit("blocked"), /transition is in progress/i);
  assert.throws(
    () => controller.updateTimeline((value) => { value.project.duration_seconds = 99; }),
    /transition is in progress/i,
  );
  assert.throws(() => { node.widgets[0].value = "DIRECT_WRITE"; }, /transition is in progress/i);
  rootGraph.serialize();
  assert.equal(
    events.commits,
    commitsBeforeFreezeChecks,
    "serialization must read the frozen widget without another commit",
  );

  const publicExact = new TextEncoder().encode(JSON.stringify(timeline(9)));
  await adapter.applyModeTransitionOwnerExact(owner.owner, publicExact);
  assert.throws(
    () => adapter.reconcileModeTransitionRuntime(owner.owner),
    /readback is incomplete/i,
  );
  assert.deepEqual(
    await adapter.readModeTransitionOwnerExact(owner.owner),
    publicExact,
  );
  assert.deepEqual(
    await adapter.extractDetachedModeTransitionOwnerExact(
      owner.owner,
      rootGraph.serialize(),
    ),
    publicExact,
  );
  await adapter.reloadModeTransitionRuntime(owner.owner);
  await adapter.reconcileModeTransitionRuntime(owner.owner);
  assert.equal(controller.timeline.project.duration_seconds, 9);
  assert.equal(node.widgets[0].value, new TextDecoder().decode(publicExact));
  await privateToPublic.release();

  controller.globalSettings.privacy.mode = false;
  const publicToPrivate = adapter.settleModeTransition();
  assert.deepEqual(
    await publicToPrivate.settled,
    { offlineRepresentationCount: 0 },
  );
  const [privateOwner] = await adapter.inventoryModeTransitionOwners();
  const privateExact = new TextEncoder().encode(lockedEnvelope);
  await adapter.applyModeTransitionOwnerExact(privateOwner.owner, privateExact);
  assert.deepEqual(
    await adapter.readModeTransitionOwnerExact(privateOwner.owner),
    privateExact,
  );
  await adapter.reloadModeTransitionRuntime(privateOwner.owner);
  await adapter.reconcileModeTransitionRuntime(privateOwner.owner);
  await publicToPrivate.release();
  adapter.onPrivacySessionChange({ state: "locked" });
  assert.equal(controller.timeline, null, "a locked private target must withhold runtime plaintext");
  assert.equal(node.widgets[0].value, lockedEnvelope);
}

{
  const original = JSON.stringify(timeline(14));
  const { node, controller } = fakeNode(original);
  controller.pendingDebounce = null;
  controller.globalSettings.privacy.mode = false;
  const { app, rootGraph } = graphHarness([node]);
  const adapter = createDirectorTimelineBrowserAdapter({
    app,
    workflowHandle: { markEdited() {} },
  });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  adapter.reconcileNode(node);
  const settlement = adapter.settleModeTransition();
  await settlement.settled;
  const [owner] = await adapter.inventoryModeTransitionOwners();
  const originalExact = await adapter.readModeTransitionOwnerExact(owner.owner);

  await adapter.applyModeTransitionOwnerExact(
    owner.owner,
    new TextEncoder().encode(lockedEnvelope),
  );
  await adapter.readModeTransitionOwnerExact(owner.owner);
  await adapter.restoreModeTransitionOwnerExact(owner.owner, originalExact);
  assert.deepEqual(
    await adapter.readModeTransitionOwnerExact(owner.owner),
    originalExact,
  );
  assert.deepEqual(
    await adapter.extractDetachedModeTransitionOwnerExact(
      owner.owner,
      rootGraph.serialize(),
    ),
    originalExact,
  );
  await adapter.reloadModeTransitionRuntime(owner.owner);
  await adapter.reconcileModeTransitionRuntime(owner.owner);
  assert.equal(node.widgets[0].value, original, "rollback must restore exact original bytes");
  assert.equal(controller.timeline.project.duration_seconds, 14);
  await settlement.release();
}

{
  const root = fakeNode(lockedEnvelope);
  const nested = fakeNode(lockedEnvelope);
  root.controller.pendingDebounce = null;
  nested.controller.pendingDebounce = null;
  const { app } = graphHarness([root.node], {
    subgraphs: [{ id: "subgraph-A", nodes: [nested.node] }],
  });
  const adapter = createDirectorTimelineBrowserAdapter({
    app,
    workflowHandle: { markEdited() {} },
  });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  const settlement = adapter.settleModeTransition();
  assert.deepEqual(await settlement.settled, { offlineRepresentationCount: 0 });
  const inventory = await adapter.inventoryModeTransitionOwners();
  assert.deepEqual(
    inventory.map(({ rootGraphId, graphId, nodeId }) => ({
      rootGraphId,
      graphId,
      nodeId,
    })),
    [
      { rootGraphId: "root", graphId: "root", nodeId: "17" },
      { rootGraphId: "root", graphId: "subgraph-A", nodeId: "17" },
    ],
  );
  await settlement.release();
}

{
  const live = fakeNode(lockedEnvelope);
  live.controller.pendingDebounce = null;
  const { app } = graphHarness([live.node], {
    offlineRootNodes: [{
      id: 99,
      type: "HeltoVideoTimelineDirector",
      widgets_values: [lockedEnvelope],
    }],
  });
  const adapter = createDirectorTimelineBrowserAdapter({
    app,
    workflowHandle: { markEdited() {} },
  });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  const settlement = adapter.settleModeTransition();
  assert.deepEqual(
    await settlement.settled,
    { offlineRepresentationCount: 1 },
    "offline serialized Director owners must be reported so shared mode transition blocks",
  );
  await settlement.release();
}

{
  const oversized = "x".repeat(2 * 1024 * 1024 + 1);
  const { node, controller } = fakeNode(oversized);
  controller.pendingDebounce = null;
  controller.globalSettings.privacy.mode = false;
  const { app } = graphHarness([node]);
  const adapter = createDirectorTimelineBrowserAdapter({
    app,
    workflowHandle: { markEdited() {} },
  });
  adapter.onPrivacySessionChange({ state: "unlocked" });
  const settlement = adapter.settleModeTransition();
  await settlement.settled;
  const [owner] = await adapter.inventoryModeTransitionOwners();
  assert.throws(
    () => adapter.readModeTransitionOwnerExact(owner.owner),
    /oversized/i,
  );
  await settlement.release();
}

{
  const { node, controller } = fakeNode("");
  const mode = createDirectorGlobalModeBrowserAdapter();
  assert.equal(mode.readDeclaredMode(node), "private");
  mode.writeDeclaredMode(node, "public");
  assert.equal(controller.globalSettings.privacy.mode, false);
  assert.throws(() => mode.writeDeclaredMode(node, "inherit"), /Invalid/);
}

{
  const calls = [];
  const workflow = {
    runWithSnapshot(reason, callback) {
      calls.push(reason);
      return callback();
    },
  };
  for (const reason of ["manual-save", "export", "direct-queue", "queue", "queue-manager", "replay"]) {
    assert.equal(runDirectorSnapshot(workflow, reason, () => reason), reason);
  }
  assert.deepEqual(
    calls,
    ["manual-save", "export", "direct-queue", "queue", "queue-manager", "replay"],
  );
  assert.equal(DIRECTOR_SNAPSHOT_REASONS.includes("render"), false);
  assert.throws(() => runDirectorSnapshot(workflow, "render", () => null), /Invalid/);

  const execution = {
    prepare(node, projectionId) { return [node.id, projectionId]; },
  };
  assert.deepEqual(
    prepareDirectorRender(execution, { id: 17 }),
    [17, "render-timeline"],
  );
}

console.log("timeline managed privacy tests passed");
