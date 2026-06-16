import assert from "node:assert/strict";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import { addSection } from "../../web/timeline/operations.js";
import {
  addCharacterReference,
  applyReferencePromptCompletion,
  areCharacterReferencesEnabled,
  filterReferencePromptCompletions,
  formatCharacterReferenceTag,
  getCharacterReferences,
  getReferencePromptCompletions,
  parseReferenceTags,
  referencePromptCompletionContext,
  removeCharacterReference,
} from "../../web/timeline/references.js";
import { validateVideoTimeline } from "../../web/timeline/validation.js";

function pickedItem(path, filename, metadata = {}) {
  return {
    path,
    filename,
    name: filename,
    folder_alias: "input",
    ...metadata,
  };
}

function testDefaultTimelineHasEmptyCharacterReferences() {
  const timeline = createDefaultVideoTimeline();

  assert.deepEqual(timeline.project.metadata.character_references, []);
  assert.equal(timeline.project.metadata.character_references_enabled, true);
  assert.equal(areCharacterReferencesEnabled(timeline), true);
  assert.deepEqual(getCharacterReferences(timeline), []);
}

function testAddRemoveReferenceDoesNotCreateTimelineMedia() {
  const timeline = createDefaultVideoTimeline();
  const reference = addCharacterReference(timeline, pickedItem("/media/hero.png", "hero.png", { width: 640, height: 480 }));

  assert.equal(timeline.assets.length, 0);
  assert.equal(timeline.director_track.sections.length, 0);
  assert.equal(reference.label, "image1");
  assert.equal(reference.kind, "character");
  assert.equal(reference.image.path, "/media/hero.png");
  assert.equal(reference.image.metadata.browser_alias, "input");
  assert.equal(formatCharacterReferenceTag(reference), "@image1:character");
  assert.equal(removeCharacterReference(timeline, reference.id), true);
  assert.deepEqual(getCharacterReferences(timeline), []);
}

function testReferenceTagParsingAndCompletions() {
  const timeline = createDefaultVideoTimeline();
  const reference = addCharacterReference(timeline, pickedItem("/media/hero.png", "hero.png"));
  reference.description = "black bob haircut and red jacket";

  const tags = parseReferenceTags("close-up of @image1:character[0.8] turning around");
  const completions = getReferencePromptCompletions(timeline);

  assert.equal(formatCharacterReferenceTag("image1", 0.8), "@image1:character[0.8]");
  assert.equal(tags.length, 1);
  assert.equal(tags[0].label, "image1");
  assert.equal(tags[0].kind, "character");
  assert.equal(tags[0].strength_override, 0.8);
  assert.deepEqual(completions[0], {
    id: reference.id,
    label: "image1",
    tag: "@image1:character",
    description: "black bob haircut and red jacket",
    trigger: "@",
  });
}

function testGlobalReferenceToggleDisablesCompletions() {
  const timeline = createDefaultVideoTimeline();
  addCharacterReference(timeline, pickedItem("/media/hero.png", "hero.png"));

  assert.equal(getReferencePromptCompletions(timeline).length, 1);
  timeline.project.metadata.character_references_enabled = false;

  assert.equal(areCharacterReferencesEnabled(timeline), false);
  assert.deepEqual(getReferencePromptCompletions(timeline), []);
}

function testPromptReferenceIntellisenseHelpers() {
  const timeline = createDefaultVideoTimeline();
  const hero = addCharacterReference(timeline, pickedItem("/media/hero.png", "hero.png"));
  hero.description = "red jacket";
  const villain = addCharacterReference(timeline, pickedItem("/media/villain.png", "villain.png"));
  villain.description = "silver mask";
  const completions = getReferencePromptCompletions(timeline);

  assert.deepEqual(referencePromptCompletionContext("hello @", 7), {
    trigger: "@",
    triggerStart: 6,
    replaceStart: 6,
    replaceEnd: 7,
    query: "",
  });
  assert.equal(referencePromptCompletionContext("email@test", 10), null);
  assert.equal(filterReferencePromptCompletions(completions, "mask")[0].label, "image2");
  assert.equal(filterReferencePromptCompletions(completions, "image1")[0].label, "image1");

  const inserted = applyReferencePromptCompletion("walk with @im toward camera", 13, completions[1]);
  assert.equal(inserted.value, "walk with @image2:character toward camera");
  assert.equal(inserted.caret, "walk with @image2:character ".length);

  const insertedAtEnd = applyReferencePromptCompletion("@", 1, completions[0]);
  assert.equal(insertedAtEnd.value, "@image1:character ");
  assert.equal(insertedAtEnd.caret, insertedAtEnd.value.length);
}

function testReferenceValidation() {
  const timeline = createDefaultVideoTimeline();
  addCharacterReference(timeline, pickedItem("/media/hero.png", "hero.png"));
  timeline.project.metadata.character_references.push({
    id: "duplicate",
    label: "image1",
    kind: "character",
    enabled: true,
    description: "",
    strength: 1,
    image: { path: "/media/other.png", thumbnail: "data:image/png;base64,AAAA" },
  });
  timeline.project.metadata.character_references.push({
    id: "missing",
    label: "image2",
    kind: "character",
    enabled: true,
    description: "",
    strength: 1,
    image: null,
  });

  const validation = validateVideoTimeline(timeline);
  const codes = validation.errors.map((entry) => entry.code);

  assert.equal(validation.is_valid, false);
  assert.equal(codes.includes("CHARACTER_REFERENCE_DUPLICATE_LABEL"), true);
  assert.equal(codes.includes("CHARACTER_REFERENCE_EMBEDDED_MEDIA_NOT_ALLOWED"), true);
  assert.equal(codes.includes("CHARACTER_REFERENCE_MISSING_IMAGE"), true);
}

function testPromptReferenceWarnings() {
  const timeline = createDefaultVideoTimeline();
  const reference = addCharacterReference(timeline, pickedItem("/media/hero.png", "hero.png"));
  reference.enabled = false;
  const section = addSection(timeline, "Text", 0);
  section.prompt = "@image1:character and @image2:character walking forward";

  const validation = validateVideoTimeline(timeline);
  const warningCodes = validation.warnings.map((entry) => entry.code);

  assert.equal(validation.errors.length, 0);
  assert.equal(warningCodes.includes("PROMPT_REFERENCE_DISABLED"), true);
  assert.equal(warningCodes.includes("PROMPT_REFERENCE_UNKNOWN"), true);
}

function testGlobalReferenceToggleWarningsAreDisabled() {
  const timeline = createDefaultVideoTimeline();
  addCharacterReference(timeline, pickedItem("/media/hero.png", "hero.png"));
  timeline.project.metadata.character_references_enabled = false;
  const section = addSection(timeline, "Text", 0);
  section.prompt = "@image1:character and @image2:character walking forward";

  const validation = validateVideoTimeline(timeline);
  const warningCodes = validation.warnings.map((entry) => entry.code);

  assert.equal(validation.errors.length, 0);
  assert.equal(warningCodes.filter((code) => code === "PROMPT_REFERENCE_DISABLED").length, 2);
  assert.equal(warningCodes.includes("PROMPT_REFERENCE_UNKNOWN"), false);
}

testDefaultTimelineHasEmptyCharacterReferences();
testAddRemoveReferenceDoesNotCreateTimelineMedia();
testReferenceTagParsingAndCompletions();
testGlobalReferenceToggleDisablesCompletions();
testPromptReferenceIntellisenseHelpers();
testReferenceValidation();
testPromptReferenceWarnings();
testGlobalReferenceToggleWarningsAreDisabled();

console.log("phase17 character reference tests passed");
