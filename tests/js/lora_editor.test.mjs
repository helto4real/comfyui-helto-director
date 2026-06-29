import assert from "node:assert/strict";
import {
  MODEL_LORA_MODEL_LTX_2_3,
  MODEL_LORA_MODEL_WAN_2_2,
  MODEL_LORA_TARGET_HIGH_NOISE,
  MODEL_LORA_TARGET_MAIN,
} from "../../web/timeline/schema.js";
import {
  fetchTimelineLoras,
  loraEditorFilteredChoices,
  loraEditorProfileForTarget,
  normalizeLoraEditorStack,
  showTimelineLoraStackEditor,
} from "../../web/timeline/lora_editor.js";

function testLtxSingleStrengthNormalizesModelAndClipTogether() {
  const profile = loraEditorProfileForTarget(MODEL_LORA_MODEL_LTX_2_3, MODEL_LORA_TARGET_MAIN);
  const stack = normalizeLoraEditorStack({
    loras: [{ enabled: true, name: "style.safetensors", strength_model: 0.75, strength_clip: 0.25 }],
    ui: { show_strengths: "single", match: "style" },
  }, profile);

  assert.equal(stack.ui.show_strengths, "single");
  assert.equal(stack.ui.match, "style");
  assert.equal(stack.loras[0].strength_model, 0.75);
  assert.equal(stack.loras[0].strength_clip, 0.75);
}

function testLtxSeparateStrengthPreservesClip() {
  const profile = loraEditorProfileForTarget(MODEL_LORA_MODEL_LTX_2_3, MODEL_LORA_TARGET_MAIN);
  const stack = normalizeLoraEditorStack({
    loras: [{ enabled: true, name: "style.safetensors", strength_model: 0.75, strength_clip: 0.25 }],
    ui: { show_strengths: "separate" },
  }, profile);

  assert.equal(stack.ui.show_strengths, "separate");
  assert.equal(stack.loras[0].strength_model, 0.75);
  assert.equal(stack.loras[0].strength_clip, 0.25);
}

function testWanModelOnlyStoresClipEqualToModel() {
  const profile = loraEditorProfileForTarget(MODEL_LORA_MODEL_WAN_2_2, MODEL_LORA_TARGET_HIGH_NOISE);
  const stack = normalizeLoraEditorStack({
    loras: [{ enabled: true, name: "wan.safetensors", strength_model: 0.6, strength_clip: 0.1 }],
    ui: { show_strengths: "separate" },
  }, profile);

  assert.equal(stack.ui.show_strengths, "single");
  assert.equal(stack.loras[0].strength_model, 0.6);
  assert.equal(stack.loras[0].strength_clip, 0.6);
}

function testFilteredChoicesUseRegexAndFallbackOnInvalidRegex() {
  const loras = ["style/detail.safetensors", "character/main.safetensors", "style/light.safetensors"];

  assert.deepEqual(loraEditorFilteredChoices(loras, "^style/"), [
    "style/detail.safetensors",
    "style/light.safetensors",
  ]);
  assert.deepEqual(loraEditorFilteredChoices(loras, "["), loras);
}

async function testFetchTimelineLorasUsesDirectorRoute() {
  const requested = [];
  const fetcher = async (url) => {
    requested.push(url);
    return {
      ok: true,
      async json() {
        return [{ file: "a.safetensors" }, { file: "b.safetensors" }];
      },
    };
  };

  const loras = await fetchTimelineLoras({ force: true, fetcher });
  assert.deepEqual(loras, ["a.safetensors", "b.safetensors"]);
  assert.equal(requested[0], "/helto_director/api/loras?format=details");
}

function testPrivacyLockedEditorDoesNotOpen() {
  assert.equal(showTimelineLoraStackEditor({ documentRef: null, privacyLocked: true }), null);
}

testLtxSingleStrengthNormalizesModelAndClipTogether();
testLtxSeparateStrengthPreservesClip();
testWanModelOnlyStoresClipEqualToModel();
testFilteredChoicesUseRegexAndFallbackOnInvalidRegex();
await testFetchTimelineLorasUsesDirectorRoute();
testPrivacyLockedEditorDoesNotOpen();

console.log("lora editor tests passed");
