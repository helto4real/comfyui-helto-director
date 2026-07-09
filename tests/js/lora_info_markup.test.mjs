import assert from "node:assert/strict";
import {
  buildLoraInfoDialogMarkup,
  escapeLoraInfoHtml,
  loraInfoEditActionLabel,
  sanitizeLoraInfoUrl,
} from "../../web/timeline/lora_info_markup.js";

function testEscapesAllUntrustedMetadata() {
  const markup = buildLoraInfoDialogMarkup({
    file: `bad\"><img src=x onerror="file()">.safetensors`,
    name: `<script>name()</script>`,
    type: `LoRA\" onmouseover=\"type()`,
    baseModel: `<img src=x onerror=base()>`,
    baseModelFile: `<svg onload=model()>`,
    sha256: `<b>hash</b>`,
    userNote: `<textarea autofocus onfocus=note()>`,
    strengthMin: `<img src=x onerror=min()>`,
    strengthMax: `\"><img src=x onerror=max()>`,
    trainedWords: [{ word: `\"><img src=x onerror=word()>`, count: `<svg onload=count()>`, civitai: true }],
    raw: {
      metadata: {
        ss_output_name: `<img src=x onerror=metadata()>`,
        ss_clip_skip: `<img src=x onerror=clip()>`,
      },
      civitai: { error: `<img src=x onerror=error()>` },
    },
    images: [{
      url: "https://images.example.test/preview.png",
      seed: `<img src=x onerror=seed()>`,
      positive: `<script>prompt()</script>`,
      negative: `\"><svg onload=negative()>`,
      sampler: `<b>sampler</b>`,
      model: `<img src=x onerror=model()>`,
    }],
  }, `<img src=x onerror=fallback()>`);

  assert.ok(markup.includes("&lt;script&gt;name()&lt;/script&gt;"));
  assert.ok(markup.includes("&lt;textarea autofocus onfocus=note()&gt;"));
  assert.ok(markup.includes("&lt;script&gt;prompt()&lt;/script&gt;"));
  assert.ok(markup.includes("&lt;img src=x onerror=word()&gt;"));
  assert.ok(markup.includes('<svg class="logo-civitai"'));
  assert.equal(markup.includes("<script>"), false);
  assert.equal(markup.includes("<textarea autofocus"), false);
  assert.equal(markup.includes("<img src=x"), false);
  assert.equal(markup.includes("<svg onload"), false);
  assert.equal(markup.includes('onmouseover="type()'), false);
  assert.ok(markup.includes("-type-lora-onmouseover-type"));
}

function testRejectsUnsafeLinksAndMediaSources() {
  const markup = buildLoraInfoDialogMarkup({
    links: [
      "javascript:alert('link')",
      "https://evil.example/?next=civitai.com/models/123",
    ],
    images: [
      { url: "javascript:alert('image')", civitaiUrl: "javascript:alert('caption')" },
      { url: "data:text/html,<script>alert('data')</script>" },
      { url: "/helto_director/not-a-media-route?file=x" },
      { url: "//evil.example/preview.png" },
    ],
    raw: { civitai: {} },
  }, "unsafe.safetensors");

  assert.equal(markup.includes("javascript:"), false);
  assert.equal(markup.includes("data:text/html"), false);
  assert.equal(markup.includes("evil.example"), false);
  assert.equal(markup.includes("rgthree-info-images"), false);
  assert.equal(markup.includes("View on Civitai"), false);
}

function testEscapesSafetensorsFallbacksAndOmitsUnsafeCaptionLinks() {
  const markup = buildLoraInfoDialogMarkup({
    file: "metadata.safetensors",
    raw: {
      metadata: {
        ss_output_name: `<img src=x onerror=metadata()>`,
        ss_clip_skip: `<svg onload=clip()>`,
      },
    },
    images: [{
      url: "https://images.example.test/safe.png",
      civitaiUrl: "javascript:alert('caption')",
    }],
  }, "metadata.safetensors");

  assert.ok(markup.includes("&lt;img src=x onerror=metadata()&gt;"));
  assert.ok(markup.includes("&lt;svg onload=clip()&gt;"));
  assert.ok(markup.includes('src="https://images.example.test/safe.png"'));
  assert.equal(markup.includes("javascript:"), false);
  assert.equal(markup.includes('target="_blank"'), false);
  assert.equal(markup.includes("<img src=x"), false);
  assert.equal(markup.includes("<svg onload"), false);
}

function testAllowsHttpMediaAndKnownDirectorPreviewRoute() {
  const markup = buildLoraInfoDialogMarkup({
    links: ["https://civitai.com/models/123?modelVersionId=456"],
    images: [
      {
        url: "/helto_director/api/loras/img?file=folder%2Fstyle.safetensors",
        civitaiUrl: "https://civitai.com/images/123",
        positive: "safe prompt",
      },
      { url: "http://images.example.test/video.mp4", type: "video" },
    ],
  }, "safe.safetensors");

  assert.ok(markup.includes('href="https://civitai.com/models/123?modelVersionId=456"'));
  assert.ok(markup.includes('src="/helto_director/api/loras/img?file=folder%2Fstyle.safetensors"'));
  assert.ok(markup.includes('src="http://images.example.test/video.mp4"'));
  assert.equal((markup.match(/rel="noopener noreferrer"/g) || []).length, 2);
  assert.equal((markup.match(/target="_blank"/g) || []).length, 2);
}

function testUrlSanitizerHasExplicitPolicy() {
  assert.equal(sanitizeLoraInfoUrl("https://example.test/a.png"), "https://example.test/a.png");
  assert.equal(sanitizeLoraInfoUrl("http://example.test"), "http://example.test/");
  assert.equal(sanitizeLoraInfoUrl("javascript:alert(1)"), "");
  assert.equal(sanitizeLoraInfoUrl("data:image/png;base64,abc"), "");
  assert.equal(sanitizeLoraInfoUrl("/helto_director/api/loras/img?file=a"), "");
  assert.equal(
    sanitizeLoraInfoUrl("/helto_director/api/loras/img?file=a", { allowDirectorRoutes: true }),
    "/helto_director/api/loras/img?file=a",
  );
  assert.equal(sanitizeLoraInfoUrl("/helto_director/media/view?path=a", { allowDirectorRoutes: true }), "");
  assert.equal(sanitizeLoraInfoUrl("https://example.test/a.png\n"), "");
  assert.equal(escapeLoraInfoHtml(`<>&\"'`), "&lt;&gt;&amp;&quot;&#039;");
}

function testEditAndSaveButtonsHaveAccessibleNamesAndTooltips() {
  const markup = buildLoraInfoDialogMarkup({
    name: "Accessible LoRA",
    strengthMin: 0.2,
    strengthMax: 1,
    userNote: "Editable note",
  }, "accessible.safetensors");
  const editButtons = markup.match(/<button[^>]+data-action="edit-row"[^>]*>/g) || [];

  assert.equal(editButtons.length, 4);
  for (const button of editButtons) {
    assert.ok(button.includes('aria-label="Edit LoRA metadata"'));
    assert.ok(button.includes('title="Edit LoRA metadata"'));
  }
  assert.equal(loraInfoEditActionLabel(false), "Edit LoRA metadata");
  assert.equal(loraInfoEditActionLabel(true), "Save LoRA metadata");
}

testEscapesAllUntrustedMetadata();
testRejectsUnsafeLinksAndMediaSources();
testEscapesSafetensorsFallbacksAndOmitsUnsafeCaptionLinks();
testAllowsHttpMediaAndKnownDirectorPreviewRoute();
testUrlSanitizerHasExplicitPolicy();
testEditAndSaveButtonsHaveAccessibleNamesAndTooltips();

console.log("lora info markup tests passed");
