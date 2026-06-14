import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  TimelineStateController,
  mountTimelineState,
  VIDEO_TIMELINE_WIDGET,
} from "../../web/timeline/state.js";

function createWidget(name, value) {
  return { name, value, type: "string" };
}

function createNode() {
  const dirtyCalls = [];
  return {
    id: 7,
    selected: true,
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

function longMultilinePrompt() {
  return [
    "Wide establishing shot of a rainy neon street with detailed reflections.",
    "The camera glides forward while signs flicker in the background.",
    "A person in a yellow raincoat pauses under a red lantern.",
    "Keep the mood cinematic, restrained, and grounded in physical detail.",
  ].join("\n");
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

await testCommitUpdatesHiddenWidgetAndMarksGraphDirty();
await testLongMultilinePromptSurvivesCommit();
await testUndoRedoUpdatesStateAndWidget();
await testDebouncedCommit();
await testFlushBeforeSerializationWritesPendingPromptWithoutRerender();
await testExtensionFlushesBeforeNodeSerialize();
await testGestureMouseupCommitBoundary();

console.log("phase3 state tests passed");
