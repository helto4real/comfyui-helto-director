import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createDefaultVideoTimeline } from "../../web/timeline/schema.js";
import { addSection } from "../../web/timeline/operations.js";
import { promptOptimizerRows } from "../../web/timeline/prompt_optimizer.js";

function testPromptOptimizerRowsUseTimelineSectionsAndAssets() {
  const timeline = createDefaultVideoTimeline();
  timeline.project.frame_rate = 24;
  timeline.assets.push({
    asset_id: "image_001",
    type: "Image",
    source_kind: "FilePath",
    path: "/tmp/guide.png",
    name: "guide.png",
    metadata: { browser_alias: "input", browser_filename: "guide.png" },
  });
  const image = addSection(timeline, "Image", 0, 1);
  image.item_id = "section_image";
  image.prompt = "look left";
  image.image = { asset_id: "image_001" };
  const text = addSection(timeline, "Text", 1, 2);
  text.item_id = "section_text";
  text.prompt = "walk forward";

  const rows = promptOptimizerRows(timeline);

  assert.equal(rows.length, 2);
  assert.equal(rows[0].id, "section_image");
  assert.equal(rows[0].start, 0);
  assert.equal(rows[0].length, 24);
  assert.equal(rows[0].mediaPath, "/tmp/guide.png");
  assert.equal(rows[0].mediaFolderAlias, "input");
  assert.equal(rows[0].mediaFile, "guide.png");
  assert.equal(rows[0].thumbnailUrl.includes("/helto_director/media/thumbnail?"), true);
  assert.equal(rows[0].thumbnailUrl.includes("path=%2Ftmp%2Fguide.png"), true);
  assert.equal(rows[1].id, "section_text");
  assert.equal(rows[1].prompt, "walk forward");
}

function testPromptOptimizerUsesModernRouteAndApplyMutation() {
  const optimizerSource = readFileSync(new URL("../../web/timeline/prompt_optimizer.js", import.meta.url), "utf8");
  const rendererSource = readFileSync(new URL("../../web/timeline/renderer.js", import.meta.url), "utf8");

  assert.equal(optimizerSource.includes('const ROUTE_PREFIX = "/helto_director/prompt_optimizer";'), true);
  assert.equal(optimizerSource.includes('body: JSON.stringify({'), true);
  assert.equal(optimizerSource.includes("segments,"), true);
  assert.equal(optimizerSource.includes("references: []"), true);
  assert.equal(optimizerSource.includes("mediaPath: item.mediaPath ||"), true);
  assert.equal(optimizerSource.includes("img.src = item.thumbnailUrl"), true);
  assert.equal(optimizerSource.includes("width: 96px; height: 96px; min-width: 96px"), true);
  assert.equal(optimizerSource.includes("object-fit: contain"), true);
  assert.equal(optimizerSource.includes("object-fit: cover"), false);
  assert.equal(rendererSource.includes('this.commitMutation((timeline) => {'), true);
  assert.equal(rendererSource.includes('}, "prompt optimizer apply");'), true);
}

testPromptOptimizerRowsUseTimelineSectionsAndAssets();
testPromptOptimizerUsesModernRouteAndApplyMutation();

console.log("phase10 prompt optimizer tests passed");
