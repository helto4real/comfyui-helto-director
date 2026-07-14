// D5 browser facade. Transport, authorization, source-reference resolution,
// and lease validation stay inside the attested shared operation handle.

export const DIRECTOR_MEDIA_OPERATION_RESOURCE_ID = "director-media-operations";
export const DIRECTOR_MEDIA_FOLDER_KIND = "media-folder";
export const DIRECTOR_MEDIA_SOURCE_KIND = "media-source";
export const DIRECTOR_PROJECT_TAKE_KIND = "project-take";

const REFERENCE_ID = /^hp-ref-[A-Za-z0-9_-]{32}$/;
const LEASE_URL = /^\/helto_privacy\/artifacts\/hp-lease-[A-Za-z0-9_-]{32}$/;
const OPERATIONS = Object.freeze({
  folders: "media-folders-list",
  addFolder: "media-folders-add",
  removeFolder: "media-folders-remove",
  items: "media-items-list",
  view: "media-source-view",
  preview: "media-source-preview",
  resolve: "media-source-resolve",
  attach: "media-source-attach",
  takes: "project-takes-list",
  attachTake: "project-takes-attach",
  deleteTake: "project-takes-delete",
});
const PRIVATE_DATA_SCHEMAS = Object.freeze({
  [OPERATIONS.folders]: Object.freeze({
    enabled_count: "count",
    existing_count: "count",
    folder_count: "count",
  }),
  [OPERATIONS.addFolder]: Object.freeze({ folder_count: "count", ok: "boolean" }),
  [OPERATIONS.removeFolder]: Object.freeze({ folder_count: "count", ok: "boolean" }),
  [OPERATIONS.items]: Object.freeze({ item_count: "count" }),
  [OPERATIONS.takes]: Object.freeze({ capture_count: "count" }),
  [OPERATIONS.deleteTake]: Object.freeze({
    deleted: "boolean",
    files_deleted: "count",
    media_missing: "boolean",
    ok: "boolean",
  }),
  [OPERATIONS.attach]: Object.freeze({ ok: "boolean" }),
  [OPERATIONS.attachTake]: Object.freeze({ ok: "boolean" }),
  [OPERATIONS.resolve]: Object.freeze({ ready: "boolean" }),
});

function fail() {
  throw new Error("PRIVACY_DIRECTOR_MEDIA_INVALID");
}

function clone(value) {
  return value == null ? value : structuredClone(value);
}

function exactKeys(value, keys) {
  return value
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

function reference(value, kind) {
  if (
    !exactKeys(value, ["id", "kind"])
    || value.kind !== kind
    || typeof value.id !== "string"
    || !REFERENCE_ID.test(value.id)
  ) fail();
  return Object.freeze({ id: value.id, kind });
}

function references(value, groups) {
  if (!Array.isArray(value)) fail();
  let offset = 0;
  const result = {};
  for (const [name, kind, maximum] of groups) {
    const group = [];
    while (offset < value.length && value[offset]?.kind === kind) {
      if (group.length >= maximum) fail();
      group.push(reference(value[offset], kind));
      offset += 1;
    }
    result[name] = Object.freeze(group);
  }
  if (offset !== value.length) fail();
  return Object.freeze(result);
}

function privateData(value, operation) {
  const schema = PRIVATE_DATA_SCHEMAS[operation];
  if (!schema || !exactKeys(value, Object.keys(schema))) fail();
  for (const [name, kind] of Object.entries(schema)) {
    if (kind === "boolean" && typeof value[name] !== "boolean") fail();
    if (
      kind === "count"
      && (!Number.isSafeInteger(value[name]) || value[name] < 0)
    ) fail();
  }
  return value;
}

function result(value, operation, groups = []) {
  if (
    !exactKeys(value, [
      "association", "correlationId", "data", "lease", "ok", "private",
      "references", "safePayload",
    ])
    || value.ok !== true
    || typeof value.private !== "boolean"
    || value.association !== null
    || value.lease !== null
    || value.safePayload !== null
    || !Array.isArray(value.references)
  ) fail();
  const data = value.data;
  if (!data || typeof data !== "object" || Array.isArray(data)) fail();
  if (value.private) privateData(data, operation);
  return Object.freeze({
    data: Object.freeze(clone(data)),
    references: references(value.references, groups),
  });
}

function lease(value) {
  if (
    !exactKeys(value, [
      "association", "correlationId", "data", "lease", "ok", "private",
      "references", "safePayload",
    ])
    || value.ok !== true
    || typeof value.private !== "boolean"
    || value.association !== null
    || value.safePayload !== null
    || !Array.isArray(value.references)
    || value.references.length !== 0
    || !exactKeys(value.data, ["ready"])
    || typeof value.data.ready !== "boolean"
  ) fail();
  const candidate = value.lease;
  if (
    !exactKeys(candidate, ["expiresInSeconds", "url"])
    || typeof candidate.url !== "string"
    || !LEASE_URL.test(candidate.url)
    || !Number.isInteger(candidate.expiresInSeconds)
    || candidate.expiresInSeconds < 1
  ) fail();
  return Object.freeze({
    url: candidate.url,
    expiresInSeconds: candidate.expiresInSeconds,
  });
}

function operationHandle(pack) {
  const handle = pack?.operations?.(DIRECTOR_MEDIA_OPERATION_RESOURCE_ID);
  if (!handle?.invoke || !handle?.invokeExternal) fail();
  return handle;
}

export function createDirectorManagedMediaBrowser({ pack } = {}) {
  const handle = operationHandle(pack);

  return Object.freeze({
    async listFolders(input) {
      return result(
        await handle.invoke(OPERATIONS.folders, clone(input), {}),
        OPERATIONS.folders,
        [["folders", DIRECTOR_MEDIA_FOLDER_KIND, 256]],
      );
    },

    async addFolder(input) {
      return result(
        await handle.invoke(OPERATIONS.addFolder, clone(input), {}),
        OPERATIONS.addFolder,
        [["folders", DIRECTOR_MEDIA_FOLDER_KIND, 1]],
      );
    },

    async removeFolder(folder) {
      const shell = reference(folder, DIRECTOR_MEDIA_FOLDER_KIND);
      return result(
        await handle.invoke(OPERATIONS.removeFolder, {}, { folder: shell.id }),
        OPERATIONS.removeFolder,
      );
    },

    async listItems(folder, input = {}) {
      const shell = reference(folder, DIRECTOR_MEDIA_FOLDER_KIND);
      return result(
        await handle.invoke(OPERATIONS.items, clone(input), { folder: shell.id }),
        OPERATIONS.items,
        [["sources", DIRECTOR_MEDIA_SOURCE_KIND, 256]],
      );
    },

    async viewSource(source) {
      const shell = reference(source, DIRECTOR_MEDIA_SOURCE_KIND);
      return lease(await handle.invoke(OPERATIONS.view, {}, { source: shell.id }));
    },

    async previewSource(source, options = {}) {
      const shell = reference(source, DIRECTOR_MEDIA_SOURCE_KIND);
      if (!exactKeys(options, Object.keys(options))) fail();
      const input = {};
      if (Object.prototype.hasOwnProperty.call(options, "maxSize")) {
        if (!Number.isInteger(options.maxSize) || options.maxSize < 32 || options.maxSize > 2048) fail();
        input.max_size = options.maxSize;
      }
      if (Object.prototype.hasOwnProperty.call(options, "peaks")) {
        if (!Number.isInteger(options.peaks) || options.peaks < 16 || options.peaks > 512) fail();
        input.peaks = options.peaks;
      }
      if (Object.keys(options).some((key) => !["maxSize", "peaks"].includes(key))) fail();
      return lease(await handle.invoke(OPERATIONS.preview, input, { source: shell.id }));
    },

    async resolveSource({ assetType, path, sourceType = "" } = {}) {
      const mediaType = { Image: "image", Video: "video", Audio: "audio" }[assetType];
      if (
        !mediaType
        || typeof path !== "string"
        || !path.trim()
        || typeof sourceType !== "string"
      ) fail();
      const input = { media_type: mediaType, path };
      if (sourceType.trim()) input.source_type = sourceType.trim();
      const resolved = result(
        await handle.invoke(
          OPERATIONS.resolve,
          input,
          {},
        ),
        OPERATIONS.resolve,
        [["sources", DIRECTOR_MEDIA_SOURCE_KIND, 1]],
      );
      if (resolved.references.sources.length !== 1) fail();
      return resolved.references.sources[0];
    },

    async attachSource(owner, timeline, source, { assetType, itemId } = {}) {
      const shell = reference(source, DIRECTOR_MEDIA_SOURCE_KIND);
      if (
        !["Image", "Video", "Audio"].includes(assetType)
        || typeof itemId !== "string"
        || !itemId.trim()
      ) fail();
      return result(
        await handle.invokeExternal(
          OPERATIONS.attach,
          owner,
          clone({ asset_type: assetType, item_id: itemId, timeline }),
          { source: shell.id },
        ),
        OPERATIONS.attach,
      ).data;
    },

    async listProjectTakes(input) {
      return result(
        await handle.invoke(OPERATIONS.takes, clone(input), {}),
        OPERATIONS.takes,
        [
          ["sources", DIRECTOR_MEDIA_SOURCE_KIND, 128],
          ["takes", DIRECTOR_PROJECT_TAKE_KIND, 128],
        ],
      );
    },

    async attachProjectTake(owner, timeline, sourceValue, takeValue, {
      accept = false,
      projectRecordId,
      shotId,
    } = {}) {
      const source = reference(sourceValue, DIRECTOR_MEDIA_SOURCE_KIND);
      const take = reference(takeValue, DIRECTOR_PROJECT_TAKE_KIND);
      if (
        typeof projectRecordId !== "string"
        || !/^hp-rec-[A-Za-z0-9_-]{32}$/.test(projectRecordId)
        || typeof shotId !== "string"
        || !shotId.trim()
      ) fail();
      return result(
        await handle.invokeExternal(
          OPERATIONS.attachTake,
          owner,
          clone({
            accept: accept === true,
            project_record_id: projectRecordId,
            shot_id: shotId,
            timeline,
          }),
          { source: source.id, take: take.id },
        ),
        OPERATIONS.attachTake,
      ).data;
    },

    async deleteProjectTake(take) {
      const shell = reference(take, DIRECTOR_PROJECT_TAKE_KIND);
      return result(
        await handle.invoke(OPERATIONS.deleteTake, {}, { take: shell.id }),
        OPERATIONS.deleteTake,
      );
    },
  });
}
