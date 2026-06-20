import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  addAudioClip,
  addSection,
} from "../../web/timeline/operations.js";
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
  clearTimelineLibraryItemId,
  cloneTimelineForDirectorLibrary,
  libraryPreviewAssetForItem,
  libraryDialogClassName,
  linkedTimelineLibraryItemId,
  normalizeLibraryCharacterItem,
  normalizeLibraryTimelineItem,
  shouldRequestPrivateCharacterPreview,
  shouldRequestPrivateTimelinePreview,
  stampTimelineLibraryItemId,
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
  const previewSection = addSection(timeline, "Image", 0);
  previewSection.image = { asset_id: "image_001" };
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

function testTimelineLibraryIdentityHelpers() {
  const timeline = createDefaultVideoTimeline();
  assert.equal(linkedTimelineLibraryItemId(timeline), "");
  assert.equal(stampTimelineLibraryItemId(timeline, "timeline_abc"), timeline);
  assert.equal(linkedTimelineLibraryItemId(timeline), "timeline_abc");
  assert.equal(timeline.project.metadata.library_item_id, "timeline_abc");
  clearTimelineLibraryItemId(timeline);
  assert.equal(linkedTimelineLibraryItemId(timeline), "");
  assert.equal("library_item_id" in timeline.project.metadata, false);
}

function testTimelineLibrarySaveClonePrunesUnreferencedAssets() {
  const timeline = createDefaultVideoTimeline();
  const imageSection = addSection(timeline, "Image", 0);
  const videoSection = addSection(timeline, "Video", 1);
  const audioClip = addAudioClip(timeline, 0, 1);
  timeline.project.metadata.character_references.push({
    id: "reference_001",
    label: "image1",
    image: { asset_id: "reference_image" },
  });
  imageSection.image = { asset_id: "section_image" };
  imageSection.video = { asset_id: "stale_section_video_field" };
  videoSection.video = { asset_id: "section_video" };
  videoSection.image = { asset_id: "stale_section_image_field" };
  audioClip.audio = { asset_id: "clip_audio" };
  timeline.assets.push(
    { asset_id: "section_image", type: "Image", path: "/media/section.png" },
    { asset_id: "section_video", type: "Video", path: "/media/section.mp4" },
    { asset_id: "clip_audio", type: "Audio", path: "/media/dialog.wav" },
    { asset_id: "reference_image", type: "Image", path: "/media/reference.png" },
    { asset_id: "stale_section_video_field", type: "Video", path: "/media/stale.mp4" },
    { asset_id: "stale_section_image_field", type: "Image", path: "/media/stale.png" },
    { asset_id: "orphan_image", type: "Image", path: "/media/orphan.png" },
  );

  const updatePayload = cloneTimelineForDirectorLibrary(timeline, "timeline_live");
  assert.notEqual(updatePayload, timeline);
  assert.deepEqual(
    updatePayload.assets.map((asset) => asset.asset_id),
    ["section_image", "section_video", "clip_audio", "reference_image"],
  );
  assert.equal(updatePayload.project.metadata.library_item_id, "timeline_live");
  assert.equal(timeline.assets.length, 7);
  assert.equal("library_item_id" in timeline.project.metadata, false);

  stampTimelineLibraryItemId(timeline, "existing_library_item");
  const saveAsNewPayload = cloneTimelineForDirectorLibrary(timeline, "");
  assert.equal("library_item_id" in saveAsNewPayload.project.metadata, false);
  assert.equal(timeline.project.metadata.library_item_id, "existing_library_item");
}

function testTimelinePreviewIgnoresOrphanAssetsAndUsesReferencedMedia() {
  const timeline = createDefaultVideoTimeline();
  const imageSection = addSection(timeline, "Image", 0);
  imageSection.image = { asset_id: "visible_image" };
  timeline.assets.push(
    { asset_id: "orphan_image", type: "Image", path: "/media/orphan.png" },
    { asset_id: "visible_image", type: "Image", path: "/media/visible.png", name: "visible.png" },
  );

  const item = normalizeLibraryTimelineItem({ id: "timeline_preview", timeline });
  assert.equal(item.previewAsset.path, "/media/visible.png");
  assert.equal(libraryPreviewAssetForItem(item).path, "/media/visible.png");

  const directTimeline = createDefaultVideoTimeline();
  const directSection = addSection(directTimeline, "Image", 0);
  directSection.image = { file_path: "/media/direct.png", name: "direct.png" };
  directTimeline.assets.push({ asset_id: "orphan_image", type: "Image", path: "/media/orphan.png" });
  const directItem = normalizeLibraryTimelineItem({ id: "timeline_direct_preview", timeline: directTimeline });
  assert.equal(directItem.previewAsset.path, "/media/direct.png");
}

function testRendererAndLibraryContractStrings() {
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");
  const librarySource = readFileSync(new URL("../../web/timeline/library.js", import.meta.url), "utf8");
  const mediaPreviewSource = readFileSync(new URL("../../web/timeline/media_preview.js", import.meta.url), "utf8");

  assert.equal(ROUTE_PREFIX, "/helto_director/library");
  assert.equal(
    TIMELINE_REPLACE_CONFIRMATION,
    "Replace current timeline?\n\nThis will replace all current sections, audio tracks, settings and references. Media files are referenced by path and are not copied.",
  );
  assert.equal(rendererSource.includes('showDirectorLibrary,'), true);
  assert.equal(rendererSource.includes('cloneTimelineForDirectorLibrary,'), true);
  assert.equal(rendererSource.includes('iconButton("library", "Director Library", () => this.openDirectorLibrary())'), true);
  const directorLibraryButtonIndex = rendererSource.indexOf('iconButton("library", "Director Library", () => this.openDirectorLibrary())');
  const timelineSaveButtonIndex = rendererSource.indexOf("timelineLibraryButton,", directorLibraryButtonIndex);
  assert.notEqual(directorLibraryButtonIndex, -1);
  assert.notEqual(timelineSaveButtonIndex, -1);
  assert.equal(rendererSource.includes("controller: this.controller,"), true);
  assert.equal(librarySource.includes('fetchLibraryJson(`${ROUTE_PREFIX}/items`)'), true);
  assert.equal(librarySource.includes('fetchLibraryJson(`${ROUTE_PREFIX}/timelines/${encodeURIComponent(item.id)}/preview`, { method: "POST" })'), true);
  assert.equal(librarySource.includes('fetchLibraryJson(`${ROUTE_PREFIX}/characters/${encodeURIComponent(item.id)}/preview`, { method: "POST" })'), true);
  assert.equal(librarySource.includes("card.addEventListener(\"pointerenter\", revealPreview);"), true);
  assert.equal(librarySource.includes('options.controller?.replaceTimelineFromLibrary?.(nextTimeline, "replace timeline from library")'), true);
  assert.equal(librarySource.includes("addCharacterLibraryItemToTimeline(timeline, item"), true);
  assert.equal(librarySource.includes("replaceTimelineCharacterReferenceFromLibraryItem(timeline, referenceId, item)"), true);
  assert.equal(librarySource.includes('overlay.className = libraryDialogClassName(privacyMode);'), true);
  assert.equal(librarySource.includes('const saveButton = iconButton(documentRef, "plus", "Add Current Timeline to Library"'), true);
  assert.equal(librarySource.includes('showTimelineSaveChoicePopup(documentRef, saveButton'), true);
  assert.equal(librarySource.includes('stampTimelineLibraryItemId(deepClone(full.timeline), item.id)'), true);
  assert.equal(librarySource.includes('updateCurrentTimelineLibraryItem({'), true);
  assert.equal(librarySource.includes('renderEditableLibraryTitle(documentRef, item, TAB_TIMELINES, context)'), true);
  assert.equal(librarySource.includes('renderLibraryActionMenu(documentRef, `${TAB_TIMELINES}:${item.id}`, "More Timeline Actions"'), true);
  assert.equal(librarySource.includes('renderLibraryActionMenu(documentRef, `${TAB_CHARACTERS}:${item.id}`, "More Character Actions"'), true);
  assert.equal(librarySource.includes('panel.append(header, controls, tabs, body, status, actions);'), true);
  assert.equal(librarySource.includes('renderTimelineMediaStrip(documentRef, item, privacyMode)'), true);
  assert.equal(librarySource.includes('renderCharacterDetails(documentRef, details, item, timeline, privacyMode, context)'), true);
  assert.equal(librarySource.includes('import { showMediaPreview } from "./media_preview.js";'), true);
  assert.equal(librarySource.includes("function openLibraryMediaPreview(documentRef, asset, caption)"), true);
  assert.equal(librarySource.includes("return showMediaPreview(documentRef, {"), true);
  assert.equal(librarySource.includes("windowOpen(documentRef"), false);
  assert.equal(mediaPreviewSource.includes("z-index: 10050"), true);
  assert.equal(librarySource.includes('htd-library-filter-toggle'), true);
  assert.equal(librarySource.includes('htd-library-inspector-actions'), true);
  assert.equal(librarySource.includes('timelinePreviewAssets(item).slice(0, 3)'), true);
  assert.equal(librarySource.includes(".htd-library-dialog.privacy-mode .htd-library-preview img"), true);
  assert.equal(librarySource.includes(".htd-library-dialog.privacy-mode .htd-library-strip-thumb img"), true);
  assert.equal(rendererSource.includes('const DIRECTOR_LIBRARY_ROUTE = "/helto_director/library";'), true);
  assert.equal(rendererSource.includes("const timelineLibraryItemId = timelineLibraryItemIdFor(this.controller.timeline);"), true);
  assert.equal(rendererSource.includes('const timelineLibraryButton = iconButton('), true);
  assert.equal(rendererSource.includes('timelineLibraryItemId ? "library-update" : "library-add"'), true);
  assert.equal(rendererSource.includes('timelineLibraryItemId ? "Update Current Timeline in Library" : "Add Current Timeline to Library"'), true);
  assert.equal(rendererSource.includes("async () => this.saveCurrentTimelineToLibrary(timelineLibraryButton)"), true);
  assert.equal(rendererSource.includes('timelineLibraryButton.classList.add("htd-timeline-library-save-button");'), true);
  assert.equal(rendererSource.includes('timelineLibraryButton.classList.toggle("is-active", Boolean(timelineLibraryItemId));'), true);
  assert.equal(rendererSource.includes('async saveCurrentTimelineToLibrary(control = null)'), true);
  assert.equal(rendererSource.includes('const itemId = timelineLibraryItemIdFor(this.controller.timeline);'), true);
  assert.equal(rendererSource.includes('fetchDirectorLibraryJson(`${DIRECTOR_LIBRARY_ROUTE}/timelines/${encodeURIComponent(itemId)}`'), true);
  assert.equal(rendererSource.includes('method: "PUT"'), true);
  assert.equal(rendererSource.includes("body: JSON.stringify(timelineLibraryPayload(this.controller.timeline, itemId))"), true);
  assert.equal(rendererSource.includes('fetchDirectorLibraryJson(`${DIRECTOR_LIBRARY_ROUTE}/timelines`'), true);
  assert.equal(rendererSource.includes('method: "POST"'), true);
  assert.equal(rendererSource.includes('body: JSON.stringify(timelineLibraryPayload(this.controller.timeline, ""))'), true);
  assert.equal(rendererSource.includes('const nextItemId = String(data?.item?.id ?? "").trim();'), true);
  assert.equal(rendererSource.includes("this.stampCurrentTimelineLibraryItemId(nextItemId);"), true);
  assert.equal(rendererSource.includes("stampTimelineLibraryItemId(timeline, itemId)"), true);
  assert.equal(rendererSource.includes('iconButton("library-add", "Add Reference to Director Library"'), true);
  assert.equal(rendererSource.includes('iconButton("library-update", "Update Director Library Character"'), true);
  assert.equal(rendererSource.includes("stampReferenceLibraryItemId(timeline, reference, itemId)"), true);
  assert.equal(rendererSource.includes('fetchDirectorLibraryJson(`${DIRECTOR_LIBRARY_ROUTE}/characters/${encodeURIComponent(itemId)}`'), true);
  assert.equal(librarySource.includes("function collectTimelineAssetReferences(timeline)"), true);
  assert.equal(librarySource.includes("cloneTimelineForDirectorLibrary(timeline, itemId)"), true);
}

await testLibraryTimelineReplacementSyncsWidgetsAndUndo();
testCharacterLibraryHelpersAddReplaceAndDetectLoaded();
testLibraryItemNormalizationAndPrivacyHelpers();
testCharacterLibraryPreviewUsesImageSourceMetadata();
testPrivateCharacterPreviewHydratesImageShell();
testTimelineLibraryIdentityHelpers();
testTimelineLibrarySaveClonePrunesUnreferencedAssets();
testTimelinePreviewIgnoresOrphanAssetsAndUsesReferencedMedia();
testRendererAndLibraryContractStrings();

console.log("phase18 director library tests passed");
