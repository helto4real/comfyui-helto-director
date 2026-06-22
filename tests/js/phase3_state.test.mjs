import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  TimelineStateController,
  mountTimelineState,
  VIDEO_TIMELINE_WIDGET,
} from "../../web/timeline/state.js";
import { PRIVACY_SCHEMA } from "../../web/timeline/privacy.js";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";

function createWidget(name, value) {
  return { name, value, type: "string" };
}

function createNode(options = {}) {
  const dirtyCalls = [];
  return {
    id: 7,
    selected: options.selected ?? true,
    graph: {
      setDirtyCanvas(first, second) {
        dirtyCalls.push([first, second]);
      },
    },
    widgets: [
      createWidget("duration_seconds", 5.0),
      createWidget("frame_rate", 24.0),
      createWidget("aspect_ratio", "16:9"),
      createWidget("orientation", "Landscape"),
      createWidget("quality_preset", "Standard"),
      createWidget(VIDEO_TIMELINE_WIDGET, ""),
    ],
    dirtyCalls,
  };
}

function createWindowStub() {
  const listeners = {};
  return {
    listeners,
    addEventListener(type, handler) {
      listeners[type] ??= [];
      listeners[type].push(handler);
    },
    removeEventListener(type, handler) {
      listeners[type] = (listeners[type] ?? []).filter((candidate) => candidate !== handler);
    },
    setTimeout,
    clearTimeout,
  };
}

function getHiddenTimeline(node) {
  return JSON.parse(node.widgets.find((widget) => widget.name === VIDEO_TIMELINE_WIDGET).value);
}

function installPrivacyXhrStub() {
  const previous = globalThis.XMLHttpRequest;
  globalThis.XMLHttpRequest = class PrivacyXhrStub {
    open(_method, _url, _async) {
      this.status = 200;
      this.statusText = "OK";
    }

    setRequestHeader() {}

    send(body) {
      this.responseText = JSON.stringify({
        ok: true,
        envelope: {
          version: 1,
          schema: PRIVACY_SCHEMA,
          encrypted: true,
          algorithm: "AES-256-GCM",
          keyId: "test",
          nonce: "nonce",
          ciphertext: Buffer.from(String(body || "")).toString("base64"),
        },
      });
    }
  };
  return () => {
    globalThis.XMLHttpRequest = previous;
  };
}

function longMultilinePrompt() {
  return [
    "Wide establishing shot of a rainy neon street with detailed reflections.",
    "The camera glides forward while signs flicker in the background.",
    "A person in a yellow raincoat pauses under a red lantern.",
    "Keep the mood cinematic, restrained, and grounded in physical detail.",
  ].join("\n");
}

function createKeyEvent(key, target = null) {
  const event = {
    key,
    target,
    defaultPrevented: false,
    propagationStopped: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {
      this.propagationStopped = true;
    },
  };
  return event;
}

function createMockTarget({ tagName = "div", className = "", item = null, ownerDocument = null } = {}) {
  const target = {
    tagName,
    className,
    ownerDocument,
    matches(selector) {
      return selector === ".htd-item" && String(this.className).split(/\s+/).includes("htd-item");
    },
    closest(selector) {
      if (this.matches(selector)) return this;
      if (selector === ".htd-item") return item;
      for (const classToken of String(this.className).split(/\s+/).filter(Boolean)) {
        if (selector.includes(`.${classToken}`)) return this;
      }
      return null;
    },
  };
  return target;
}

function createTimelineKeyboardScope() {
  const documentRef = { activeElement: null };
  const item = createMockTarget({ className: "htd-item", ownerDocument: documentRef });
  const scope = {
    contains(candidate) {
      return candidate === item;
    },
  };
  documentRef.activeElement = item;
  return { scope, item, documentRef };
}

function addSelectedTextSection(controller, prompt = "selected") {
  controller.updateTimeline((timeline) => {
    timeline.director_track.sections.push({
      item_id: "section_001",
      type: "Text",
      start_time: 0,
      end_time: 1,
      prompt,
    });
    timeline.ui_state.selected_item_id = "section_001";
    timeline.ui_state.selected_item_ids = ["section_001"];
  }, "add");
}

async function testCommitUpdatesHiddenWidgetAndMarksGraphDirty() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });

  controller.updateTimeline((timeline) => {
    timeline.director_track.sections.push({
      item_id: "section_001",
      type: "Text",
      start_time: 0,
      end_time: 1,
      prompt: "hello",
    });
  }, "add");

  const hiddenTimeline = getHiddenTimeline(node);
  assert.equal(hiddenTimeline.director_track.sections[0].prompt, "hello");
  assert.deepEqual(node.dirtyCalls.at(-1), [true, true]);
  assert.equal(node.widgets.find((widget) => widget.name === VIDEO_TIMELINE_WIDGET).hidden, true);
}

async function testLongMultilinePromptSurvivesCommit() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });
  const prompt = longMultilinePrompt();

  controller.updateTimeline((timeline) => {
    timeline.director_track.sections.push({
      item_id: "section_001",
      type: "Text",
      start_time: 0,
      end_time: 1,
      prompt,
    });
  }, "add");

  const hiddenPrompt = getHiddenTimeline(node).director_track.sections[0].prompt;
  assert.equal(hiddenPrompt, prompt);
  assert.equal(hiddenPrompt.endsWith("physical detail."), true);
}

async function testUndoRedoUpdatesStateAndWidget() {
  const node = createNode();
  const controller = mountTimelineState(node, {}, { window: createWindowStub() });

  controller.updateTimeline((timeline) => {
    timeline.director_track.sections.push({
      item_id: "section_001",
      type: "Text",
      start_time: 0,
      end_time: 1,
      prompt: "first",
    });
  }, "add");

  assert.equal(controller.timeline.director_track.sections.length, 1);
  assert.equal(controller.undoTimelineChange(), true);
  assert.equal(getHiddenTimeline(node).director_track.sections.length, 0);
  assert.equal(controller.redoTimelineChange(), true);
  assert.equal(getHiddenTimeline(node).director_track.sections[0].prompt, "first");
}

async function testDebouncedCommit() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, {
    window: createWindowStub(),
    debounceMs: 0,
  });

  controller.timeline.project.global_prompt.prompt = "debounced";
  controller.scheduleDebouncedCommit("prompt typing");
  await new Promise((resolve) => setTimeout(resolve, 5));

  assert.equal(getHiddenTimeline(node).project.global_prompt.prompt, "debounced");
}

async function testFlushBeforeSerializationWritesPendingPromptWithoutRerender() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, {
    window: createWindowStub(),
    debounceMs: 10000,
  });
  const prompt = longMultilinePrompt();
  let renderCount = 0;
  node._timelineRenderer = {
    render() {
      renderCount += 1;
    },
  };

  controller.updateTimeline((timeline) => {
    timeline.director_track.sections.push({
      item_id: "section_001",
      type: "Text",
      start_time: 0,
      end_time: 1,
      prompt: "short",
    });
  }, "add");
  renderCount = 0;
  node.dirtyCalls.length = 0;

  controller.timeline.director_track.sections[0].prompt = prompt;
  controller.scheduleDebouncedCommit("prompt typing");
  assert.equal(getHiddenTimeline(node).director_track.sections[0].prompt, "short");

  controller.flushTimelineBeforeSerialization();

  assert.equal(getHiddenTimeline(node).director_track.sections[0].prompt, prompt);
  assert.equal(renderCount, 0);
  assert.equal(node.dirtyCalls.length, 0);
}

async function testExtensionFlushesBeforeNodeSerialize() {
  const extensionSource = readFileSync(new URL("../../web/video_timeline_director.js", import.meta.url), "utf8");

  assert.equal(extensionSource.includes("const serialize = nodeType.prototype.serialize"), true);
  assert.equal(extensionSource.includes("this.flushTimelineBeforeSerialization?.()"), true);
  assert.equal(extensionSource.includes("return serialize.apply(this, arguments)"), true);
  assert.equal(extensionSource.includes("const onResize = nodeType.prototype.onResize"), true);
  assert.equal(extensionSource.includes("this._timelineRenderer?.handleNodeResize?.()"), true);
  assert.equal(extensionSource.includes("const onDrawForeground = nodeType.prototype.onDrawForeground"), true);
  assert.equal(extensionSource.includes("_timelineDirectorLastNodeWidth"), true);
}

async function testGestureMouseupCommitBoundary() {
  const node = createNode();
  const windowStub = createWindowStub();
  const controller = new TimelineStateController(node, {}, { window: windowStub });

  controller.beginTimelineGesture();
  controller.timeline.ui_state.view_start_seconds = 1;
  controller.timeline.ui_state.view_end_seconds = 4;
  windowStub.listeners.mouseup[0]();

  assert.equal(getHiddenTimeline(node).ui_state.view_start_seconds, 1);
  assert.equal(getHiddenTimeline(node).ui_state.view_end_seconds, 4);
  assert.equal(controller.undoTimelineChange(), true);
  assert.equal(getHiddenTimeline(node).ui_state.view_start_seconds, 0);
  assert.equal(getHiddenTimeline(node).ui_state.view_end_seconds, 5);
}

async function testDeleteKeyRemovesSelectedItem() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });

  addSelectedTextSection(controller, "delete me");

  const event = createKeyEvent("Delete");
  controller.handleKeyDown(event);

  assert.equal(getHiddenTimeline(node).director_track.sections.length, 0);
  assert.equal(getHiddenTimeline(node).ui_state.selected_item_id, null);
  assert.deepEqual(getHiddenTimeline(node).ui_state.selected_item_ids, []);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.propagationStopped, true);
}

async function testDeleteKeyRemovesMixedSelectedItems() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });

  controller.updateTimeline((timeline) => {
    timeline.director_track.sections.push(
      {
        item_id: "section_001",
        type: "Text",
        start_time: 0,
        end_time: 1,
        prompt: "delete section",
      },
      {
        item_id: "section_keep",
        type: "Text",
        start_time: 2,
        end_time: 3,
        prompt: "keep section",
      },
    );
    timeline.audio_tracks.push({
      track_id: "audio_track_001",
      clips: [{
        item_id: "audio_001",
        start_time: 0.5,
        end_time: 1.5,
        lane: 0,
        audio: null,
      }],
    });
    timeline.ui_state.selected_item_id = "audio_001";
    timeline.ui_state.selected_item_ids = ["section_001", "audio_001"];
  }, "add mixed selection");

  const event = createKeyEvent("Backspace");
  controller.handleKeyDown(event);
  const hiddenTimeline = getHiddenTimeline(node);

  assert.deepEqual(hiddenTimeline.director_track.sections.map((section) => section.item_id), ["section_keep"]);
  assert.equal(hiddenTimeline.audio_tracks.length, 0);
  assert.equal(hiddenTimeline.ui_state.selected_item_id, null);
  assert.deepEqual(hiddenTimeline.ui_state.selected_item_ids, []);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.propagationStopped, true);
}

async function testDeleteKeyRemovesTimelineItemWhenNodeInactiveButTimelineItemFocused() {
  const node = createNode({ selected: false });
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });
  const { scope, item } = createTimelineKeyboardScope();
  controller.setTimelineKeyboardScope(scope);

  addSelectedTextSection(controller, "delete focused item");

  const event = createKeyEvent("Delete", item);
  controller.handleKeyDown(event);

  assert.equal(getHiddenTimeline(node).director_track.sections.length, 0);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.propagationStopped, true);
}

async function testDeleteKeyIsIgnoredWhenInactiveNodeAndFocusOutsideTimelineItem() {
  const node = createNode({ selected: false });
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });
  const { scope, documentRef } = createTimelineKeyboardScope();
  const outside = createMockTarget({ ownerDocument: documentRef });
  documentRef.activeElement = outside;
  controller.setTimelineKeyboardScope(scope);

  addSelectedTextSection(controller, "keep outside focus");

  const event = createKeyEvent("Delete", outside);
  controller.handleKeyDown(event);

  assert.equal(getHiddenTimeline(node).director_track.sections.length, 1);
  assert.equal(event.defaultPrevented, false);
}

async function testDeleteKeyIsIgnoredOnInteractiveTimelineControls() {
  for (const tagName of ["input", "textarea", "select", "button"]) {
    const node = createNode({ selected: false });
    const controller = new TimelineStateController(node, {}, { window: createWindowStub() });
    const { scope, item, documentRef } = createTimelineKeyboardScope();
    const control = createMockTarget({ tagName, item, ownerDocument: documentRef });
    documentRef.activeElement = control;
    controller.setTimelineKeyboardScope(scope);

    addSelectedTextSection(controller, `keep ${tagName}`);

    const event = createKeyEvent("Delete", control);
    controller.handleKeyDown(event);

    assert.equal(getHiddenTimeline(node).director_track.sections.length, 1);
    assert.equal(event.defaultPrevented, false);
  }
}

async function testDeleteKeyIsIgnoredInsideDirectorLibraryDialog() {
  const node = createNode({ selected: false });
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });
  const { scope, documentRef } = createTimelineKeyboardScope();
  const libraryDialog = createMockTarget({ className: "htd-library-dialog", ownerDocument: documentRef });
  documentRef.activeElement = libraryDialog;
  controller.setTimelineKeyboardScope(scope);

  addSelectedTextSection(controller, "keep library focus");

  const event = createKeyEvent("Delete", libraryDialog);
  controller.handleKeyDown(event);

  assert.equal(getHiddenTimeline(node).director_track.sections.length, 1);
  assert.equal(event.defaultPrevented, false);
}

async function testDeleteKeyIsIgnoredWhileTyping() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });

  addSelectedTextSection(controller, "keep me");

  const event = createKeyEvent("Delete", { tagName: "textarea" });
  controller.handleKeyDown(event);

  assert.equal(getHiddenTimeline(node).director_track.sections.length, 1);
  assert.equal(getHiddenTimeline(node).director_track.sections[0].prompt, "keep me");
  assert.equal(event.defaultPrevented, false);
}

async function testUndoRestoresDeleteKeyRemoval() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, { window: createWindowStub() });

  addSelectedTextSection(controller, "restore me");

  controller.handleKeyDown(createKeyEvent("Delete"));

  assert.equal(getHiddenTimeline(node).director_track.sections.length, 0);
  assert.equal(controller.undoTimelineChange(), true);
  assert.equal(getHiddenTimeline(node).director_track.sections.length, 1);
  assert.equal(getHiddenTimeline(node).director_track.sections[0].prompt, "restore me");
}

async function testReplaceTimelineClearsLibraryLinkAndIsUndoable() {
  const node = createNode();
  const controller = mountTimelineState(node, {}, { window: createWindowStub() });

  controller.updateTimeline((timeline) => {
    timeline.project.metadata.library_item_id = "timeline_123";
    timeline.project.metadata.character_references.push({
      id: "reference_001",
      label: "image1",
      image: { asset_id: "asset_001" },
    });
    timeline.assets.push({
      asset_id: "asset_001",
      type: "Image",
      source_kind: "FilePath",
      path: "/media/reference.png",
      name: "reference.png",
    });
    timeline.director_track.sections.push({
      item_id: "section_001",
      type: "Text",
      start_time: 0,
      end_time: 1,
      prompt: "linked timeline",
    });
    timeline.ui_state.selected_item_id = "section_001";
  }, "link timeline");

  controller.replaceTimeline(createDefaultVideoTimeline(), "clear current timeline");

  const cleared = getHiddenTimeline(node);
  assert.equal("library_item_id" in cleared.project.metadata, false);
  assert.equal(cleared.assets.length, 0);
  assert.equal(cleared.director_track.sections.length, 0);
  assert.equal(cleared.audio_tracks.length, 0);
  assert.equal(cleared.project.metadata.character_references.length, 0);
  assert.equal(cleared.ui_state.selected_item_id, null);
  assert.deepEqual(cleared.ui_state.selected_item_ids, []);

  assert.equal(controller.undoTimelineChange(), true);
  const restored = getHiddenTimeline(node);
  assert.equal(restored.project.metadata.library_item_id, "timeline_123");
  assert.equal(restored.assets.length, 1);
  assert.equal(restored.director_track.sections.length, 1);
}

async function testPrivacyModeWritesEncryptedHiddenWidget() {
  const restoreXhr = installPrivacyXhrStub();
  try {
    const node = createNode();
    const controller = new TimelineStateController(node, {}, { window: createWindowStub() });

    controller.updateTimeline((timeline) => {
      timeline.project.privacy.mode = true;
      timeline.project.global_prompt.prompt = "private global";
      timeline.assets.push({
        asset_id: "asset_001",
        type: "Image",
        source_kind: "FilePath",
        path: "/private/reference.png",
        name: "reference.png",
      });
      timeline.director_track.sections.push({
        item_id: "section_001",
        type: "Image",
        start_time: 0,
        end_time: 1,
        prompt: "private prompt",
        image: { asset_id: "asset_001" },
      });
    }, "privacy");

    const hiddenValue = node.widgets.find((widget) => widget.name === VIDEO_TIMELINE_WIDGET).value;
    const payload = JSON.parse(hiddenValue);
    assert.equal(payload.encrypted, true);
    assert.equal(payload.schema, PRIVACY_SCHEMA);
    assert.equal(hiddenValue.includes("private prompt"), false);
    assert.equal(hiddenValue.includes("reference.png"), false);
  } finally {
    restoreXhr();
  }
}

async function testEncryptedWorkflowLoadDecryptsBeforeRender() {
  const previousFetch = globalThis.fetch;
  const node = createNode();
  node.widgets.find((widget) => widget.name === VIDEO_TIMELINE_WIDGET).value = JSON.stringify({
    version: 1,
    schema: PRIVACY_SCHEMA,
    encrypted: true,
    algorithm: "AES-256-GCM",
    keyId: "test",
    nonce: "nonce",
    ciphertext: "cipher",
  });
  globalThis.fetch = async () => ({
    ok: true,
    statusText: "OK",
    text: async () => JSON.stringify({
      ok: true,
      state: {
        timeline: {
          type: "VIDEO_TIMELINE",
          project: { privacy: { mode: true } },
          director_track: {
            sections: [{
              item_id: "section_001",
              type: "Text",
              start_time: 0,
              end_time: 1,
              prompt: "decrypted prompt",
            }],
          },
        },
      },
    }),
  });
  try {
    const controller = new TimelineStateController(node, {}, { window: createWindowStub() });
    await new Promise((resolve) => setTimeout(resolve, 5));

    assert.equal(controller.timeline.director_track.sections[0].prompt, "decrypted prompt");
    assert.equal(controller.timeline.project.privacy.mode, true);
  } finally {
    globalThis.fetch = previousFetch;
  }
}

await testCommitUpdatesHiddenWidgetAndMarksGraphDirty();
await testLongMultilinePromptSurvivesCommit();
await testUndoRedoUpdatesStateAndWidget();
await testDebouncedCommit();
await testFlushBeforeSerializationWritesPendingPromptWithoutRerender();
await testExtensionFlushesBeforeNodeSerialize();
await testGestureMouseupCommitBoundary();
await testDeleteKeyRemovesSelectedItem();
await testDeleteKeyRemovesMixedSelectedItems();
await testDeleteKeyRemovesTimelineItemWhenNodeInactiveButTimelineItemFocused();
await testDeleteKeyIsIgnoredWhenInactiveNodeAndFocusOutsideTimelineItem();
await testDeleteKeyIsIgnoredOnInteractiveTimelineControls();
await testDeleteKeyIsIgnoredInsideDirectorLibraryDialog();
await testDeleteKeyIsIgnoredWhileTyping();
await testUndoRestoresDeleteKeyRemoval();
await testReplaceTimelineClearsLibraryLinkAndIsUndoable();
await testPrivacyModeWritesEncryptedHiddenWidget();
await testEncryptedWorkflowLoadDecryptsBeforeRender();

console.log("phase3 state tests passed");
