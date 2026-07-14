import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  DIRECTOR_MEDIA_FOLDER_KIND,
  DIRECTOR_MEDIA_OPERATION_RESOURCE_ID,
  DIRECTOR_MEDIA_SOURCE_KIND,
  DIRECTOR_PROJECT_TAKE_KIND,
  createDirectorManagedMediaBrowser,
} from "../../web/timeline/managed_media_privacy.js";

const folder = { id: `hp-ref-${"F".repeat(32)}`, kind: DIRECTOR_MEDIA_FOLDER_KIND };
const source = { id: `hp-ref-${"S".repeat(32)}`, kind: DIRECTOR_MEDIA_SOURCE_KIND };
const take = { id: `hp-ref-${"T".repeat(32)}`, kind: DIRECTOR_PROJECT_TAKE_KIND };
const calls = [];
const externalCalls = [];

function response(data, references = [], isPrivate = true) {
  return {
    ok: true,
    data,
    references,
    private: isPrivate,
    correlationId: "hp-operation-synthetic1234",
    safePayload: null,
    lease: null,
    association: null,
  };
}

function leaseResponse() {
  return {
    ...response({ ready: true }),
    lease: {
      url: `/helto_privacy/artifacts/hp-lease-${"L".repeat(32)}`,
      expiresInSeconds: 30,
    },
  };
}

const handle = {
  async invoke(operation, input, references) {
    calls.push([operation, input, references]);
    if (operation === "media-folders-list") {
      return response({ enabled_count: 1, existing_count: 1, folder_count: 1 }, [folder]);
    }
    if (operation === "media-folders-add") {
      return response({ ok: true, folder_count: 2 }, [folder]);
    }
    if (operation === "media-items-list") {
      return response({ item_count: 1 }, [source]);
    }
    if (operation === "media-source-resolve") {
      return response({ ready: true }, [source]);
    }
    if (operation === "project-takes-list") {
      return response({ capture_count: 1 }, [source, take]);
    }
    if (operation === "media-folders-remove") {
      return response({ ok: true, folder_count: 1 });
    }
    if (operation === "media-source-view" || operation === "media-source-preview") {
      return leaseResponse();
    }
    return response({ ok: true, deleted: true, files_deleted: 2, media_missing: false });
  },
  async invokeExternal(operation, owner, input, references) {
    externalCalls.push([operation, owner, input, references]);
    return response({ ok: true });
  },
};
const browser = createDirectorManagedMediaBrowser({
  pack: {
    operations(resourceId) {
      assert.equal(resourceId, DIRECTOR_MEDIA_OPERATION_RESOURCE_ID);
      return handle;
    },
  },
});

assert.throws(
  () => createDirectorManagedMediaBrowser({
    pack: { operations: () => ({}) },
  }),
  /PRIVACY_DIRECTOR_MEDIA_INVALID/,
);

const folders = await browser.listFolders({ media_type: "image" });
assert.deepEqual(folders.data, { enabled_count: 1, existing_count: 1, folder_count: 1 });
assert.deepEqual(folders.references.folders, [folder]);
assert.equal(Object.isFrozen(folders.references.folders), true);
await browser.addFolder({ directory: "SYNTHETIC_AUTHORIZED_INPUT" });
await browser.removeFolder(folder);
assert.deepEqual(calls.at(-1), [
  "media-folders-remove",
  {},
  { folder: folder.id },
]);

const items = await browser.listItems(folder, { recursive: true });
assert.deepEqual(items.references.sources, [source]);
assert.deepEqual(calls.at(-1), [
  "media-items-list",
  { recursive: true },
  { folder: folder.id },
]);

const takes = await browser.listProjectTakes({
  project_record_id: `hp-rec-${"P".repeat(32)}`,
  shot_id: "shot_001",
});
assert.deepEqual(takes.references.sources, [source]);
assert.deepEqual(takes.references.takes, [take]);
await browser.deleteProjectTake(take);
assert.deepEqual(calls.at(-1), [
  "project-takes-delete",
  {},
  { take: take.id },
]);

const view = await browser.viewSource(source);
const preview = await browser.previewSource(source);
const sizedPreview = await browser.previewSource(source, { maxSize: 256 });
const waveformPreview = await browser.previewSource(source, { peaks: 64 });
assert.deepEqual(view, {
  url: `/helto_privacy/artifacts/hp-lease-${"L".repeat(32)}`,
  expiresInSeconds: 30,
});
assert.deepEqual(preview, view);
assert.deepEqual(sizedPreview, view);
assert.deepEqual(waveformPreview, view);
assert.deepEqual(calls.slice(-4), [
  ["media-source-view", {}, { source: source.id }],
  ["media-source-preview", {}, { source: source.id }],
  ["media-source-preview", { max_size: 256 }, { source: source.id }],
  ["media-source-preview", { peaks: 64 }, { source: source.id }],
]);
assert.deepEqual(
  await browser.resolveSource({ assetType: "Video", path: "/synthetic/source.mp4" }),
  source,
);
assert.deepEqual(calls.at(-1), [
  "media-source-resolve",
  { media_type: "video", path: "/synthetic/source.mp4" },
  {},
]);
await browser.resolveSource({
  assetType: "Image",
  path: "references/source.png",
  sourceType: "input",
});
assert.deepEqual(calls.at(-1), [
  "media-source-resolve",
  { media_type: "image", path: "references/source.png", source_type: "input" },
  {},
]);

const timeline = { schema_version: 1 };
const owner = { node: { id: 7 } };
assert.deepEqual(
  await browser.attachSource(owner, timeline, source, {
    assetType: "Video",
    itemId: "section_001",
  }),
  { ok: true },
);
assert.deepEqual(externalCalls, [[
  "media-source-attach",
  owner,
  { asset_type: "Video", item_id: "section_001", timeline },
  { source: source.id },
]]);
const projectRecordId = `hp-rec-${"P".repeat(32)}`;
assert.deepEqual(
  await browser.attachProjectTake(owner, timeline, source, take, {
    accept: true,
    projectRecordId,
    shotId: "shot_001",
  }),
  { ok: true },
);
assert.deepEqual(externalCalls.at(-1), [
  "project-takes-attach",
  owner,
  {
    accept: true,
    project_record_id: projectRecordId,
    shot_id: "shot_001",
    timeline,
  },
  { source: source.id, take: take.id },
]);

for (const invalid of [
  { ...source, id: "/private/source.mp4" },
  { ...source, id: `hp-ref-${"S".repeat(32)}`, extra: "SYNTHETIC_LEAK" },
  { ...source, kind: DIRECTOR_MEDIA_FOLDER_KIND },
]) {
  await assert.rejects(browser.viewSource(invalid), /PRIVACY_DIRECTOR_MEDIA_INVALID/);
}

const leakyBrowser = createDirectorManagedMediaBrowser({
  pack: {
    operations: () => ({
      invoke: async () => response(
        { item_count: 1 },
        [{ ...source, name: "SYNTHETIC_LEAKED_NAME" }],
      ),
      invokeExternal: async () => response({ ok: true }),
    }),
  },
});
await assert.rejects(
  leakyBrowser.listItems(folder),
  /PRIVACY_DIRECTOR_MEDIA_INVALID/,
);

for (const invalidData of [
  { item_count: 1, path: "/private/source.mp4" },
  { item_count: 1, name: "SYNTHETIC_LEAKED_NAME" },
  { item_count: 1, extra: false },
  { item_count: true },
  { item_count: -1 },
]) {
  const invalidResultBrowser = createDirectorManagedMediaBrowser({
    pack: { operations: () => ({
      invoke: async () => response(invalidData, [source]),
      invokeExternal: async () => response({ ok: true }),
    }) },
  });
  await assert.rejects(
    invalidResultBrowser.listItems(folder),
    /PRIVACY_DIRECTOR_MEDIA_INVALID/,
  );
}

const publicBrowser = createDirectorManagedMediaBrowser({
  pack: {
    operations: () => ({
      invoke: async () => response(
        { item_count: 1, path: "/declared/product/path", name: "capture.mp4" },
        [source],
        false,
      ),
      invokeExternal: async () => response({ ok: true }, [], false),
    }),
  },
});
assert.deepEqual((await publicBrowser.listItems(folder)).data, {
  item_count: 1,
  path: "/declared/product/path",
  name: "capture.mp4",
});
await assert.rejects(
  leakyBrowser.viewSource(source),
  /PRIVACY_DIRECTOR_MEDIA_INVALID/,
);

const moduleSource = readFileSync(
  new URL("../../web/timeline/managed_media_privacy.js", import.meta.url),
  "utf8",
);
for (const forbidden of [
  "fetch(",
  "privacy_mode",
  "X-Helto-Privacy-Token",
  "view_url",
  "thumb_url",
  "URLSearchParams",
  "/helto_director/media",
]) {
  assert.equal(moduleSource.includes(forbidden), false, forbidden);
}
