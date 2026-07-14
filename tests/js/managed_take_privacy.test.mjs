import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  DIRECTOR_ASSOCIATE_CAPTURE_OPERATION_ID,
  DIRECTOR_CAPTURE_ASSET_KIND,
  DIRECTOR_CAPTURE_TAKE_KIND,
  DIRECTOR_TAKE_OPERATION_RESOURCE_ID,
  createDirectorManagedTakeBrowser,
} from "../../web/timeline/managed_take_privacy.js";

const association = `hp-assoc-${"A".repeat(32)}`;
const asset = { id: `hp-ref-${"B".repeat(32)}`, kind: DIRECTOR_CAPTURE_ASSET_KIND };
const take = { id: `hp-ref-${"C".repeat(32)}`, kind: DIRECTOR_CAPTURE_TAKE_KIND };
const safePayload = {
  accepted: true,
  asset_count: 1,
  duration_seconds: 1.25,
  has_preview: true,
  has_sidecar: true,
  ok: true,
  status: "Candidate",
  take_count: 1,
};
let response = {
  ok: true,
  data: {},
  safePayload,
  references: [asset, take],
  lease: null,
  association: null,
  private: true,
  correlationId: "hp-operation-abcdefghijklmnop",
};
const claims = [];
const invocations = [];
const revocations = [];
const browser = createDirectorManagedTakeBrowser({
  pack: {
    operations(resourceId) {
      assert.equal(resourceId, DIRECTOR_TAKE_OPERATION_RESOURCE_ID);
      return {
        async claim(operationId, associationId) {
          claims.push([operationId, associationId]);
          return response;
        },
        async revoke(ids) {
          revocations.push(ids);
        },
        async invokeExternal(operationId, owner, input, references) {
          invocations.push([operationId, owner, input, references]);
          return {
            ok: true,
            data: {
              accepted: true,
              asset_count: 1,
              has_preview: true,
              has_sidecar: true,
              ok: true,
              take_count: 1,
            },
            safePayload: null,
            references: [],
            lease: null,
            association: null,
            private: true,
            correlationId: "hp-operation-abcdefghijklmnop",
          };
        },
      };
    },
  },
});

const claimed = await browser.claimCapture(association);
assert.deepEqual(claimed.ui, { ...safePayload, private: true });
assert.deepEqual(claimed.asset, asset);
assert.deepEqual(claimed.take, take);
assert.deepEqual(claims, [["capture-take", association]]);
assert.equal(await browser.revokeCapture(claimed.asset, claimed.take), true);
assert.deepEqual(revocations, [[asset.id, take.id]]);
const timeline = { project: { title: "SYNTHETIC_PRIVATE_CANARY" } };
const owner = { id: 7 };
assert.deepEqual(
  await browser.associateCapture(owner, timeline, claimed.asset, claimed.take),
  {
    accepted: true,
    asset_count: 1,
    has_preview: true,
    has_sidecar: true,
    ok: true,
    take_count: 1,
  },
);
assert.deepEqual(invocations, [[
  DIRECTOR_ASSOCIATE_CAPTURE_OPERATION_ID,
  owner,
  { timeline },
  { asset: asset.id, take: take.id },
]]);

for (const invalid of [
  { ...response, safePayload: { ...safePayload, status: "/private/path" } },
  { ...response, safePayload: { ...safePayload, duration_seconds: Infinity } },
  { ...response, safePayload: { ...safePayload, asset_count: true } },
  { ...response, safePayload: { ...safePayload, private_path: "/private" } },
  { ...response, references: [{ ...asset, name: "SYNTHETIC_PRIVATE_CANARY" }, take] },
  { ...response, references: [take, asset] },
]) {
  response = invalid;
  await assert.rejects(
    browser.claimCapture(association),
    /PRIVACY_DIRECTOR_TAKE_INVALID/,
  );
}

await assert.rejects(
  browser.claimCapture("/private/association"),
  /PRIVACY_DIRECTOR_TAKE_INVALID/,
);

const source = readFileSync(
  new URL("../../web/timeline/managed_take_privacy.js", import.meta.url),
  "utf8",
);
for (const forbidden of [
  "fetch(",
  "X-Helto-Privacy-Token",
  "file://",
  "asset_id",
  "take_id",
  "media.path",
]) {
  assert.equal(source.includes(forbidden), false, forbidden);
}
