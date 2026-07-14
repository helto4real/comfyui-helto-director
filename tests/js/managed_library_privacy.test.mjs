import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  DIRECTOR_CHARACTER_RECORD_KIND,
  DIRECTOR_CHARACTER_REFERENCE_MIGRATION_ID,
  DIRECTOR_CHARACTER_RESOURCE_ID,
  DIRECTOR_PROJECT_RECORD_KIND,
  DIRECTOR_PROJECT_REFERENCE_MIGRATION_ID,
  DIRECTOR_PROJECT_RESOURCE_ID,
  createDirectorManagedLibrary,
} from "../../web/timeline/managed_library_privacy.js";

const PROJECT_ID = `hp-rec-${"P".repeat(32)}`;
const CHARACTER_ID = `hp-rec-${"C".repeat(32)}`;

function fakeHandle(kind, recordId, useField) {
  const calls = [];
  const handle = {
    async list(recordKind) {
      calls.push(["list", recordKind]);
      return [{ id: recordId, kind, private: true, label: "Private record" }];
    },
    async reveal(recordKind, id, operation) {
      calls.push(["reveal", recordKind, id, operation]);
      const fields = {
        details: { metadata: { name: "SYNTHETIC_AUTHORIZED_NAME" } },
        preview: { preview: { count: 1 } },
        use: { [useField]: { type: `SYNTHETIC_${useField.toUpperCase()}` } },
      };
      return { value: fields[operation] };
    },
    async create(recordKind, value) {
      calls.push(["create", recordKind, value]);
      return {
        ok: true,
        recordId,
        kind,
        operation: "create",
        correlationId: "hp-record-synthetic-create",
      };
    },
    async mutate(recordKind, id, operation, value) {
      calls.push(["mutate", recordKind, id, operation, value]);
      return {
        ok: true,
        recordId: id,
        kind,
        operation,
        correlationId: `hp-record-synthetic-${operation}`,
      };
    },
    async delete(recordKind, id) {
      calls.push(["delete", recordKind, id]);
      return {
        ok: true,
        operation: "delete",
        correlationId: "hp-record-synthetic-delete",
      };
    },
    async migrateLegacyReference(recordKind, migrationId, reference) {
      calls.push(["migrateLegacyReference", recordKind, migrationId, reference]);
      return {
        ok: true,
        recordId,
        disposition: "migrated",
        correlationId: "hp-relocation-synthetic-migrate",
      };
    },
    async resolveLegacyReference(recordKind, migrationId, reference) {
      calls.push(["resolveLegacyReference", recordKind, migrationId, reference]);
      return {
        ok: true,
        recordId,
        correlationId: "hp-relocation-synthetic-resolve",
      };
    },
  };
  return { calls, handle };
}

const project = fakeHandle(DIRECTOR_PROJECT_RECORD_KIND, PROJECT_ID, "project");
const character = fakeHandle(
  DIRECTOR_CHARACTER_RECORD_KIND,
  CHARACTER_ID,
  "character",
);
const resources = new Map([
  [DIRECTOR_PROJECT_RESOURCE_ID, project.handle],
  [DIRECTOR_CHARACTER_RESOURCE_ID, character.handle],
]);
const library = createDirectorManagedLibrary({
  pack: { records: (resourceId) => resources.get(resourceId) },
});

assert.deepEqual(await library.projects.list(), [{
  id: PROJECT_ID,
  kind: DIRECTOR_PROJECT_RECORD_KIND,
  private: true,
  label: "Private record",
}]);
assert.equal(
  JSON.stringify(await library.projects.list()).includes("SYNTHETIC_AUTHORIZED_NAME"),
  false,
);
assert.deepEqual(await library.projects.details(PROJECT_ID), {
  name: "SYNTHETIC_AUTHORIZED_NAME",
});
assert.deepEqual(await library.projects.preview(PROJECT_ID), { count: 1 });
assert.deepEqual(await library.projects.use(PROJECT_ID), { type: "SYNTHETIC_PROJECT" });
assert.deepEqual(await library.characters.use(CHARACTER_ID), {
  type: "SYNTHETIC_CHARACTER",
});

const payload = { type: "VIDEO_TIMELINE", prompt: "SYNTHETIC_PRIVATE_PROMPT" };
const createdReceipt = await library.projects.create(payload, { name: "SYNTHETIC_NAME" });
assert.equal(createdReceipt.operation, "create");
assert.equal(Object.isFrozen(createdReceipt), true);
assert.equal((await library.projects.replace(PROJECT_ID, payload)).operation, "replace");
assert.equal((await library.projects.patch(PROJECT_ID, {
  metadata: { description: "SYNTHETIC_DESCRIPTION" },
})).operation, "patch");
assert.equal((await library.projects.duplicate(PROJECT_ID)).operation, "duplicate");
assert.deepEqual(await library.projects.delete(PROJECT_ID), {
  operation: "delete",
  correlationId: "hp-record-synthetic-delete",
});
assert.deepEqual(project.calls.at(-1), [
  "delete",
  DIRECTOR_PROJECT_RECORD_KIND,
  PROJECT_ID,
]);
const oldProjectId = "old project/id with spaces";
const migrationReceipt = await library.projects.migrateLegacyReference(oldProjectId);
assert.equal(Object.isFrozen(migrationReceipt), true);
assert.deepEqual(migrationReceipt, {
  recordId: PROJECT_ID,
  disposition: "migrated",
  correlationId: "hp-relocation-synthetic-migrate",
});
assert.deepEqual(project.calls.at(-1), [
  "migrateLegacyReference",
  DIRECTOR_PROJECT_RECORD_KIND,
  DIRECTOR_PROJECT_REFERENCE_MIGRATION_ID,
  oldProjectId,
]);
assert.deepEqual(await library.projects.resolveLegacyReference(oldProjectId), {
  recordId: PROJECT_ID,
  correlationId: "hp-relocation-synthetic-resolve",
});
assert.deepEqual(project.calls.at(-1), [
  "resolveLegacyReference",
  DIRECTOR_PROJECT_RECORD_KIND,
  DIRECTOR_PROJECT_REFERENCE_MIGRATION_ID,
  oldProjectId,
]);
await library.characters.migrateLegacyReference("old-character");
assert.deepEqual(character.calls.at(-1), [
  "migrateLegacyReference",
  DIRECTOR_CHARACTER_RECORD_KIND,
  DIRECTOR_CHARACTER_REFERENCE_MIGRATION_ID,
  "old-character",
]);
assert(project.calls.some((call) => call[0] === "reveal" && call[3] === "details"));
assert(project.calls.some((call) => call[0] === "reveal" && call[3] === "preview"));
assert(project.calls.some((call) => call[0] === "reveal" && call[3] === "use"));

await assert.rejects(
  createDirectorManagedLibrary({
    pack: {
      records: () => ({
        list: async () => [{
          id: PROJECT_ID,
          kind: DIRECTOR_PROJECT_RECORD_KIND,
          private: true,
          label: "Private record",
          name: "SYNTHETIC_LEAKED_NAME",
        }],
      }),
    },
  }).projects.list(),
  /PRIVACY_DIRECTOR_LIBRARY_INVALID/,
);

await assert.rejects(
  library.projects.create(payload, { private: false }),
  /PRIVACY_DIRECTOR_LIBRARY_INVALID/,
);

for (const [label, override, invoke] of [
  ["create", {
    create: async () => ({
      ok: true,
      recordId: PROJECT_ID,
      kind: DIRECTOR_PROJECT_RECORD_KIND,
      operation: "create",
      correlationId: "hp-record-synthetic-create",
      SYNTHETIC_RESPONSE_CANARY: "blocked",
    }),
  }, (value) => value.create(payload)],
  ["replace", {
    mutate: async (_kind, id, operation) => ({
      ok: true,
      recordId: id,
      kind: DIRECTOR_PROJECT_RECORD_KIND,
      operation,
      correlationId: `hp-record-synthetic-${operation}`,
      SYNTHETIC_RESPONSE_CANARY: "blocked",
    }),
  }, (value) => value.replace(PROJECT_ID, payload)],
  ["patch", {
    mutate: async (_kind, id, operation) => ({
      ok: true,
      recordId: id,
      kind: DIRECTOR_PROJECT_RECORD_KIND,
      operation,
      correlationId: `hp-record-synthetic-${operation}`,
      SYNTHETIC_RESPONSE_CANARY: "blocked",
    }),
  }, (value) => value.patch(PROJECT_ID)],
  ["duplicate", {
    mutate: async (_kind, id, operation) => ({
      ok: true,
      recordId: id,
      kind: DIRECTOR_PROJECT_RECORD_KIND,
      operation,
      correlationId: `hp-record-synthetic-${operation}`,
      SYNTHETIC_RESPONSE_CANARY: "blocked",
    }),
  }, (value) => value.duplicate(PROJECT_ID)],
  ["delete", {
    delete: async () => ({
      ok: true,
      operation: "delete",
      correlationId: "hp-record-synthetic-delete",
      SYNTHETIC_RESPONSE_CANARY: "blocked",
    }),
  }, (value) => value.delete(PROJECT_ID)],
  ["migrate", {
    migrateLegacyReference: async () => ({
      ok: true,
      recordId: PROJECT_ID,
      disposition: "migrated",
      correlationId: "hp-relocation-synthetic-migrate",
      SYNTHETIC_RESPONSE_CANARY: "blocked",
    }),
  }, (value) => value.migrateLegacyReference("old-id")],
  ["resolve", {
    resolveLegacyReference: async () => ({
      ok: true,
      recordId: PROJECT_ID,
      correlationId: "hp-relocation-synthetic-resolve",
      SYNTHETIC_RESPONSE_CANARY: "blocked",
    }),
  }, (value) => value.resolveLegacyReference("old-id")],
]) {
  const leaky = fakeHandle(DIRECTOR_PROJECT_RECORD_KIND, PROJECT_ID, "project");
  Object.assign(leaky.handle, override);
  const strict = createDirectorManagedLibrary({
    pack: { records: () => leaky.handle },
  });
  await assert.rejects(
    invoke(strict.projects),
    /PRIVACY_DIRECTOR_LIBRARY_INVALID/,
    label,
  );
}

await assert.rejects(
  library.projects.migrateLegacyReference(""),
  /PRIVACY_DIRECTOR_LIBRARY_INVALID/,
);

const cancelledDelete = fakeHandle(
  DIRECTOR_PROJECT_RECORD_KIND,
  PROJECT_ID,
  "project",
);
cancelledDelete.handle.delete = async () => null;
const cancellationLibrary = createDirectorManagedLibrary({
  pack: { records: () => cancelledDelete.handle },
});
assert.equal(await cancellationLibrary.projects.delete(PROJECT_ID), null);

const source = readFileSync(
  new URL("../../web/timeline/managed_library_privacy.js", import.meta.url),
  "utf8",
);
for (const forbidden of [
  "/helto_director/library",
  "fetch(",
  "privacy_mode",
  "encrypted_payload",
  "X-Helto-Privacy-Token",
  "localStorage",
]) {
  assert.equal(source.includes(forbidden), false, forbidden);
}

console.log("managed library privacy tests passed");
