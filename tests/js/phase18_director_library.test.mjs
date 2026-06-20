import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { addSection } from "../../web/timeline/operations.js";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import {
  TimelineStateController,
  VIDEO_TIMELINE_WIDGET,
} from "../../web/timeline/state.js";
import {
  addCharacterLibraryItemToTimeline,
  createCharacterReferenceFromLibraryItem,
  findLoadedCharacterReferenceForLibraryItem,
  formatCharacterReferenceTag,
  getCharacterReferences,
  loadedCharacterReferenceLabelForLibraryItem,
  replaceTimelineCharacterReferenceFromLibraryItem,
} from "../../web/timeline/references.js";
import {
  ROUTE_PREFIX,
  TIMELINE_REPLACE_CONFIRMATION,
  clearDirectorLibraryDisplay,
  libraryDialogClassName,
  normalizeLibraryCharacterItem,
  normalizeLibraryTimelineItem,
} from "../../web/timeline/library.js";

function createWidget(name, value) {
  return { name, value, type: "string" };
}

function createNode() {
  const dirtyCalls = [];
  return {
    id: 18,
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
  return {
    addEventListener() {},
    removeEventListener() {},
    setTimeout,
    clearTimeout,
  };
}

function hiddenTimeline(node) {
  return JSON.parse(node.widgets.find((widget) => widget.name === VIDEO_TIMELINE_WIDGET).value);
}

function widgetValue(node, name) {
  return node.widgets.find((widget) => widget.name === name)?.value;
}

function characterItem(overrides = {}) {
  return {
    id: "hero",
    title: "Hero",
    description: "red jacket",
    tags: ["cast", "lead"],
    image: {
      path: "/library/hero.png",
      name: "hero.png",
      thumbnail: "data:image/png;base64,AAAA",
      metadata: {
        thumbnail_data: "data:image/png;base64,BBBB",
      },
    },
    ...overrides,
  };
}

async function testLibraryTimelineReplacementSyncsWidgetsAndUndo() {
  const node = createNode();
  const controller = new TimelineStateController(node, {}, {
    window: createWindowStub(),
    debounceMs: 10000,
  });

  controller.timeline.project.global_prompt.prompt = "pending edit";
  controller.scheduleDebouncedCommit("prompt typing");

  const next = createDefaultVideoTimeline();
  next.project.duration_seconds = 9;
  next.project.frame_rate = 12;
  next.project.aspect_ratio = "1:1";
  next.project.orientation = "Square";
  next.project.quality_preset = "High";
  next.director_track.sections.push({
    item_id: "library_section",
    type: "Text",
    start_time: 0,
    end_time: 1,
    prompt: "from library",
  });

  controller.replaceTimelineFromLibrary(next, "replace timeline from library");

  assert.equal(widgetValue(node, "duration_seconds"), 9);
  assert.equal(widgetValue(node, "frame_rate"), 12);
  assert.equal(widgetValue(node, "aspect_ratio"), "1:1");
  assert.equal(hiddenTimeline(node).project.duration_seconds, 9);
  assert.equal(hiddenTimeline(node).director_track.sections[0].prompt, "from library");
  assert.deepEqual(node.dirtyCalls.at(-1), [true, true]);
  assert.equal(controller.undoTimelineChange(), true);
  assert.equal(hiddenTimeline(node).project.global_prompt.prompt, "pending edit");
  assert.equal(hiddenTimeline(node).director_track.sections.length, 0);
}

function testCharacterLibraryHelpersAddReplaceAndDetectLoaded() {
  const timeline = createDefaultVideoTimeline();
  const section = addSection(timeline, "Text", 0);
  section.item_id = "section_prompt";
  section.prompt = "portrait";
  timeline.ui_state.selected_item_id = "section_prompt";

  const source = characterItem();
  const preview = createCharacterReferenceFromLibraryItem(timeline, source);

  assert.equal(preview.image.thumbnail, undefined);
  assert.equal(preview.image.metadata.thumbnail_data, undefined);

  const added = addCharacterLibraryItemToTimeline(timeline, source, { insertTag: true });
  assert.equal(getCharacterReferences(timeline).length, 1);
  assert.equal(added.label, "image1");
  assert.equal(added.image.metadata.library_item_id, "hero");
  assert.equal(section.prompt, "portrait @image1:character");
  assert.equal(findLoadedCharacterReferenceForLibraryItem(timeline, source)?.id, added.id);
  assert.equal(loadedCharacterReferenceLabelForLibraryItem(timeline, source), "@image1:character");

  const duplicate = addCharacterLibraryItemToTimeline(timeline, source);
  assert.equal(duplicate.id, added.id);
  assert.equal(getCharacterReferences(timeline).length, 1);

  const replacement = replaceTimelineCharacterReferenceFromLibraryItem(
    timeline,
    added.id,
    characterItem({
      id: "villain",
      title: "Villain",
      image: { path: "/library/villain.png", name: "villain.png" },
    }),
  );

  assert.equal(replacement.id, added.id);
  assert.equal(replacement.label, "image1");
  assert.equal(replacement.image.path, "/library/villain.png");
  assert.equal(formatCharacterReferenceTag(replacement), "@image1:character");
}

function testLibraryItemNormalizationAndPrivacyHelpers() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.metadata.title = "Scene One";
  timeline.project.metadata.tags = ["drama"];
  timeline.assets.push({
    asset_id: "image_001",
    type: "Image",
    source_kind: "FilePath",
    path: "/tmp/scene.png",
    name: "scene.png",
  });

  const item = normalizeLibraryTimelineItem({
    id: "timeline_1",
    timeline,
    updated_at: "2026-06-20T10:00:00Z",
  });
  const character = normalizeLibraryCharacterItem(characterItem());

  assert.equal(item.title, "Scene One");
  assert.equal(item.tags[0], "drama");
  assert.equal(item.previewAsset.path, "/tmp/scene.png");
  assert.equal(character.title, "Hero");
  assert.equal(character.image.path, "/library/hero.png");
  assert.equal(libraryDialogClassName(true), "htd-library-dialog privacy-mode");
  assert.equal(libraryDialogClassName(false), "htd-library-dialog");

  const image = {
    tagName: "IMG",
    src: "/thumbnail",
    removeAttribute(name) {
      if (name === "src") this.src = "";
    },
  };
  const description = { tagName: "DIV", textContent: "private text" };
  const root = {
    querySelectorAll(selector) {
      assert.equal(selector, ".htd-library-preview img, .htd-library-description, .htd-library-detail-description");
      return [image, description];
    },
  };
  assert.equal(clearDirectorLibraryDisplay(root), true);
  assert.equal(image.src, "");
  assert.equal(description.textContent, "");
}

function testRendererAndLibraryContractStrings() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");
  const librarySource = readFileSync(new URL("../../web/timeline/library.js", import.meta.url), "utf8");

  assert.equal(ROUTE_PREFIX, "/helto_director/library");
  assert.equal(
    TIMELINE_REPLACE_CONFIRMATION,
    "Replace current timeline?\n\nThis will replace all current sections, audio tracks, settings and references. Media files are referenced by path and are not copied.",
  );
  assert.equal(rendererSource.includes('import { showDirectorLibrary } from "./library.js";'), true);
  assert.equal(rendererSource.includes('iconButton("library", "Director Library", () => this.openDirectorLibrary())'), true);
  assert.equal(rendererSource.includes("controller: this.controller,"), true);
  assert.equal(librarySource.includes('fetchLibraryJson(`${ROUTE_PREFIX}/items`)'), true);
  assert.equal(librarySource.includes('options.controller?.replaceTimelineFromLibrary?.(nextTimeline, "replace timeline from library")'), true);
  assert.equal(librarySource.includes("addCharacterLibraryItemToTimeline(timeline, item"), true);
  assert.equal(librarySource.includes("replaceTimelineCharacterReferenceFromLibraryItem(timeline, referenceId, item)"), true);
  assert.equal(librarySource.includes('overlay.className = libraryDialogClassName(privacyMode);'), true);
  assert.equal(librarySource.includes('const saveButton = textButton(documentRef, "Save Current", "Save Current Timeline"'), true);
  assert.equal(librarySource.includes(".htd-library-dialog.privacy-mode .htd-library-preview img"), true);
}

await testLibraryTimelineReplacementSyncsWidgetsAndUndo();
testCharacterLibraryHelpersAddReplaceAndDetectLoaded();
testLibraryItemNormalizationAndPrivacyHelpers();
testRendererAndLibraryContractStrings();

console.log("phase18 director library tests passed");
