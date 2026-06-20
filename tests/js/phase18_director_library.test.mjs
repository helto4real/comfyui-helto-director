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
  applyCharacterPreviewPayload,
  applyTimelinePreviewPayload,
  clearDirectorLibraryDisplay,
  libraryPreviewAssetForItem,
  libraryDialogClassName,
  normalizeLibraryCharacterItem,
  normalizeLibraryTimelineItem,
  shouldRequestPrivateCharacterPreview,
  shouldRequestPrivateTimelinePreview,
} from "../../web/timeline/library.js";
import { thumbnailUrl } from "../../web/timeline/media_cache.js";

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
  const shellItem = normalizeLibraryTimelineItem({
    id: "timeline_shell",
    name: "Shell Timeline",
    preview_assets: [
      {
        asset_id: "shell_image",
        type: "Image",
        source_kind: "FilePath",
        path: "/tmp/shell.png",
        name: "shell.png",
        thumbnail: "must not normalize",
      },
      {
        asset_id: "shell_audio",
        type: "Audio",
        source_kind: "FilePath",
        path: "/tmp/shell.wav",
        name: "shell.wav",
      },
    ],
  });
  assert.equal(shellItem.timeline, null);
  assert.equal(shellItem.previewAsset.path, "/tmp/shell.png");
  assert.equal(shellItem.previewAssets.length, 1);
  assert.equal("thumbnail" in shellItem.previewAsset, false);
  const privateShellItem = normalizeLibraryTimelineItem({
    id: "private_shell",
    name: "Private Shell",
    is_private: true,
    preview_assets: [{ type: "Image", path: "/private/secret.png" }],
  });
  assert.equal(privateShellItem.previewAsset, null);
  assert.equal(privateShellItem.previewAssets.length, 0);
  assert.equal(shouldRequestPrivateTimelinePreview(privateShellItem, true), true);
  assert.equal(shouldRequestPrivateTimelinePreview(privateShellItem, false), false);
  const hydratedPrivateShellItem = applyTimelinePreviewPayload(privateShellItem, {
    item: {
      id: "private_shell",
      is_private: true,
      preview_assets: [{ type: "Image", path: "/private/revealed.png" }],
    },
    preview_assets: [
      {
        asset_id: "private_image",
        type: "Image",
        source_kind: "FilePath",
        path: "/private/revealed.png",
        name: "revealed.png",
        thumbnail: "must not normalize",
      },
    ],
  });
  assert.equal(hydratedPrivateShellItem.previewAsset.path, "/private/revealed.png");
  assert.equal(hydratedPrivateShellItem.previewAssets.length, 1);
  assert.equal(hydratedPrivateShellItem.previewHydrated, true);
  assert.equal("thumbnail" in hydratedPrivateShellItem.previewAsset, false);
  assert.equal(shouldRequestPrivateTimelinePreview(hydratedPrivateShellItem, true), false);
  assert.equal(character.title, "Hero");
  assert.equal(character.image.path, "/library/hero.png");
  assert.equal(character.previewAsset.path, "/library/hero.png");
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
      assert.equal(selector, ".htd-library-preview img, .htd-library-strip-thumb img, .htd-library-description, .htd-library-detail-description");
      return [image, description];
    },
  };
  assert.equal(clearDirectorLibraryDisplay(root), true);
  assert.equal(image.src, "");
  assert.equal(description.textContent, "");
}

function testCharacterLibraryPreviewUsesImageSourceMetadata() {
  const character = normalizeLibraryCharacterItem(characterItem({
    image: {
      path: "characters/hero.png",
      name: "hero.png",
      source_kind: "FilePath",
      source_type: "output",
      thumbnail: "data:image/png;base64,AAAA",
      metadata: {
        source_type: "output",
        thumbnail_data: "data:image/png;base64,BBBB",
      },
    },
  }));
  const previewAsset = libraryPreviewAssetForItem(character);
  const url = thumbnailUrl(previewAsset, 320, false);

  assert.equal(previewAsset.path, "characters/hero.png");
  assert.equal(previewAsset.source_kind, "FilePath");
  assert.equal(previewAsset.source_type, "output");
  assert.equal("thumbnail" in previewAsset, false);
  assert.equal("metadata" in previewAsset, false);
  assert.ok(url.startsWith("/helto_director/media/thumbnail?"));
  assert.ok(url.includes("path=characters%2Fhero.png"));
  assert.ok(url.includes("type=output"));
}

function testPrivateCharacterPreviewHydratesImageShell() {
  const privateCharacter = normalizeLibraryCharacterItem({
    id: "private_hero",
    name: "Private Hero",
    is_private: true,
    summary: { is_private: true },
  });
  assert.equal(privateCharacter.previewAsset, null);
  assert.equal(privateCharacter.previewHydrated, false);
  assert.equal(shouldRequestPrivateCharacterPreview(privateCharacter, true), true);
  assert.equal(shouldRequestPrivateCharacterPreview(privateCharacter, false), false);

  const hydrated = applyCharacterPreviewPayload(privateCharacter, {
    item: {
      id: "private_hero",
      is_private: true,
      character: {
        label: "image3",
        description: "private hero",
        image: {
          path: "/private/hero.png",
          source_kind: "FilePath",
          thumbnail: "must not normalize",
        },
      },
    },
    character: {
      label: "image3",
      description: "private hero",
      image: {
        path: "/private/hero.png",
        source_kind: "FilePath",
        thumbnail: "must not normalize",
      },
    },
  });

  assert.equal(hydrated.previewAsset.path, "/private/hero.png");
  assert.equal(hydrated.description, "private hero");
  assert.equal(hydrated.previewHydrated, true);
  assert.equal("thumbnail" in hydrated.previewAsset, false);
  assert.equal(shouldRequestPrivateCharacterPreview(hydrated, true), false);
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
  assert.equal(librarySource.includes('fetchLibraryJson(`${ROUTE_PREFIX}/timelines/${encodeURIComponent(item.id)}/preview`, { method: "POST" })'), true);
  assert.equal(librarySource.includes('fetchLibraryJson(`${ROUTE_PREFIX}/characters/${encodeURIComponent(item.id)}/preview`, { method: "POST" })'), true);
  assert.equal(librarySource.includes("card.addEventListener(\"pointerenter\", revealPreview);"), true);
  assert.equal(librarySource.includes('options.controller?.replaceTimelineFromLibrary?.(nextTimeline, "replace timeline from library")'), true);
  assert.equal(librarySource.includes("addCharacterLibraryItemToTimeline(timeline, item"), true);
  assert.equal(librarySource.includes("replaceTimelineCharacterReferenceFromLibraryItem(timeline, referenceId, item)"), true);
  assert.equal(librarySource.includes('overlay.className = libraryDialogClassName(privacyMode);'), true);
  assert.equal(librarySource.includes('const saveButton = textButton(documentRef, "Save Current", "Save Current Timeline"'), true);
  assert.equal(librarySource.includes('panel.append(header, controls, tabs, body, status, actions);'), true);
  assert.equal(librarySource.includes('renderTimelineMediaStrip(documentRef, item, privacyMode)'), true);
  assert.equal(librarySource.includes('renderCharacterDetails(documentRef, details, item, timeline, privacyMode, context)'), true);
  assert.equal(librarySource.includes('htd-library-filter-toggle'), true);
  assert.equal(librarySource.includes('htd-library-inspector-actions'), true);
  assert.equal(librarySource.includes('timelinePreviewAssets(item).slice(0, 3)'), true);
  assert.equal(librarySource.includes(".htd-library-dialog.privacy-mode .htd-library-preview img"), true);
  assert.equal(librarySource.includes(".htd-library-dialog.privacy-mode .htd-library-strip-thumb img"), true);
}

await testLibraryTimelineReplacementSyncsWidgetsAndUndo();
testCharacterLibraryHelpersAddReplaceAndDetectLoaded();
testLibraryItemNormalizationAndPrivacyHelpers();
testCharacterLibraryPreviewUsesImageSourceMetadata();
testPrivateCharacterPreviewHydratesImageShell();
testRendererAndLibraryContractStrings();

console.log("phase18 director library tests passed");
