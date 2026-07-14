// Product-facing Director Library facade over shared private-record handles.

export const DIRECTOR_PROJECT_RESOURCE_ID = "director-projects";
export const DIRECTOR_CHARACTER_RESOURCE_ID = "director-characters";
export const DIRECTOR_PROJECT_RECORD_KIND = "director-project";
export const DIRECTOR_CHARACTER_RECORD_KIND = "director-character";
export const DIRECTOR_PROJECT_REFERENCE_MIGRATION_ID = "director-project-library-v1-relocation";
export const DIRECTOR_CHARACTER_REFERENCE_MIGRATION_ID = "director-character-library-v1-relocation";

const RECORD_ID = /^hp-rec-[A-Za-z0-9_-]{32}$/;
const CORRELATION_ID = /^hp-(?:record|relocation)-[A-Za-z0-9_-]{12,64}$/;
const SHELL_KEYS = "id,kind,label,private";

function fail() {
  throw new Error("PRIVACY_DIRECTOR_LIBRARY_INVALID");
}

function clone(value) {
  return value == null ? value : structuredClone(value);
}

function recordHandle(pack, resourceId) {
  const handle = pack?.records?.(resourceId);
  if (!handle) fail();
  return handle;
}

function metadata(value) {
  if (value == null) return {};
  if (typeof value !== "object" || Array.isArray(value)) fail();
  const allowed = new Set(["description", "name", "tags"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) fail();
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, clone(item)]),
  );
}

function request(payload, metadataValue, { payloadRequired = false } = {}) {
  if (payloadRequired && (!payload || typeof payload !== "object" || Array.isArray(payload))) {
    fail();
  }
  const value = { metadata: metadata(metadataValue) };
  if (payload !== undefined) value.payload = clone(payload);
  return value;
}

function exactKeys(value, keys) {
  return value
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}

function mutationReceipt(value, kind, operation) {
  if (
    !exactKeys(value, ["correlationId", "kind", "ok", "operation", "recordId"])
    || value.ok !== true
    || value.kind !== kind
    || value.operation !== operation
    || typeof value.recordId !== "string"
    || !RECORD_ID.test(value.recordId)
    || typeof value.correlationId !== "string"
    || !CORRELATION_ID.test(value.correlationId)
  ) fail();
  return Object.freeze({
    recordId: value.recordId,
    kind,
    operation,
    correlationId: value.correlationId,
  });
}

function deleteReceipt(value) {
  if (
    !exactKeys(value, ["correlationId", "ok", "operation"])
    || value.ok !== true
    || value.operation !== "delete"
    || typeof value.correlationId !== "string"
    || !CORRELATION_ID.test(value.correlationId)
  ) fail();
  return Object.freeze({
    operation: "delete",
    correlationId: value.correlationId,
  });
}

function referenceReceipt(value, operation) {
  const migrate = operation === "migrate";
  const keys = migrate
    ? ["correlationId", "disposition", "ok", "recordId"]
    : ["correlationId", "ok", "recordId"];
  if (
    !exactKeys(value, keys)
    || value.ok !== true
    || typeof value.recordId !== "string"
    || !RECORD_ID.test(value.recordId)
    || typeof value.correlationId !== "string"
    || !CORRELATION_ID.test(value.correlationId)
    || (migrate && value.disposition !== "migrated")
  ) fail();
  return Object.freeze({
    recordId: value.recordId,
    ...(migrate ? { disposition: "migrated" } : {}),
    correlationId: value.correlationId,
  });
}

function legacyReference(value) {
  if (typeof value !== "string" || value.length === 0) fail();
  return value;
}

function shell(value, kind) {
  if (
    !value
    || Object.keys(value).sort().join(",") !== SHELL_KEYS
    || value.kind !== kind
    || value.private !== true
    || value.label !== "Private record"
    || typeof value.id !== "string"
    || !RECORD_ID.test(value.id)
  ) fail();
  return Object.freeze({ ...value });
}

function revealed(response, field) {
  const value = response?.value?.[field];
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  return Object.freeze(clone(value));
}

function facade(handle, kind, useField, migrationId) {
  return Object.freeze({
    async list() {
      const values = await handle.list(kind);
      if (!Array.isArray(values)) fail();
      return Object.freeze(values.map((value) => shell(value, kind)));
    },
    details(recordId) {
      return handle.reveal(kind, recordId, "details")
        .then((value) => revealed(value, "metadata"));
    },
    preview(recordId) {
      return handle.reveal(kind, recordId, "preview")
        .then((value) => revealed(value, "preview"));
    },
    use(recordId) {
      return handle.reveal(kind, recordId, "use")
        .then((value) => revealed(value, useField));
    },
    async create(payload, metadataValue = {}) {
      return mutationReceipt(
        await handle.create(kind, request(payload, metadataValue, { payloadRequired: true })),
        kind,
        "create",
      );
    },
    async replace(recordId, payload, metadataValue = {}) {
      return mutationReceipt(
        await handle.mutate(
          kind,
          recordId,
          "replace",
          request(payload, metadataValue, { payloadRequired: true }),
        ),
        kind,
        "replace",
      );
    },
    async patch(recordId, { payload, metadata: metadataValue } = {}) {
      return mutationReceipt(
        await handle.mutate(kind, recordId, "patch", request(payload, metadataValue)),
        kind,
        "patch",
      );
    },
    async duplicate(recordId, metadataValue = {}) {
      return mutationReceipt(
        await handle.mutate(
          kind,
          recordId,
          "duplicate",
          request(undefined, metadataValue),
        ),
        kind,
        "duplicate",
      );
    },
    async delete(recordId) {
      const value = await handle.delete(kind, recordId);
      return value === null ? null : deleteReceipt(value);
    },
    async migrateLegacyReference(reference) {
      return referenceReceipt(
        await handle.migrateLegacyReference(
          kind,
          migrationId,
          legacyReference(reference),
        ),
        "migrate",
      );
    },
    async resolveLegacyReference(reference) {
      return referenceReceipt(
        await handle.resolveLegacyReference(
          kind,
          migrationId,
          legacyReference(reference),
        ),
        "resolve",
      );
    },
  });
}

export function createDirectorManagedLibrary({ pack } = {}) {
  const projects = facade(
    recordHandle(pack, DIRECTOR_PROJECT_RESOURCE_ID),
    DIRECTOR_PROJECT_RECORD_KIND,
    "project",
    DIRECTOR_PROJECT_REFERENCE_MIGRATION_ID,
  );
  const characters = facade(
    recordHandle(pack, DIRECTOR_CHARACTER_RESOURCE_ID),
    DIRECTOR_CHARACTER_RECORD_KIND,
    "character",
    DIRECTOR_CHARACTER_REFERENCE_MIGRATION_ID,
  );
  return Object.freeze({ projects, characters });
}
