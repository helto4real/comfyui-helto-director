// Managed D6 browser adapter. Shared policy owns association authorization,
// one-shot claim, typed safe-payload validation, and opaque-reference lifetime.

export const DIRECTOR_TAKE_OPERATION_RESOURCE_ID = "director-take-operations";
export const DIRECTOR_CAPTURE_TAKE_OPERATION_ID = "capture-take";
export const DIRECTOR_ASSOCIATE_CAPTURE_OPERATION_ID = "associate-captured-take";
export const DIRECTOR_CAPTURE_ASSET_KIND = "captured-asset";
export const DIRECTOR_CAPTURE_TAKE_KIND = "captured-take";

const ASSOCIATION_ID = /^hp-assoc-[A-Za-z0-9_-]{32}$/;
const REFERENCE_ID = /^hp-ref-[A-Za-z0-9_-]{32}$/;
const SAFE_KEYS = Object.freeze([
  "accepted", "asset_count", "duration_seconds", "has_preview",
  "has_sidecar", "ok", "status", "take_count",
]);
const COMMIT_KEYS = Object.freeze([
  "accepted", "asset_count", "has_preview", "has_sidecar", "ok", "take_count",
]);

function fail() {
  throw new Error("PRIVACY_DIRECTOR_TAKE_INVALID");
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

function safePayload(value) {
  if (!exactKeys(value, SAFE_KEYS)) fail();
  for (const name of ["accepted", "has_preview", "has_sidecar", "ok"]) {
    if (typeof value[name] !== "boolean") fail();
  }
  for (const name of ["asset_count", "take_count"]) {
    if (!Number.isInteger(value[name]) || value[name] < 0 || value[name] > 2_147_483_647) fail();
  }
  if (
    typeof value.duration_seconds !== "number"
    || !Number.isFinite(value.duration_seconds)
    || value.duration_seconds < 0
    || value.duration_seconds > 31_536_000
    || typeof value.status !== "string"
    || !/^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$/.test(value.status)
    || value.status.includes("..")
  ) fail();
  return Object.freeze({ ...value });
}

function clone(value) {
  return value == null ? value : structuredClone(value);
}

function committedResult(value) {
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
    || value.references.length !== 0
    || !exactKeys(value.data, COMMIT_KEYS)
  ) fail();
  for (const name of ["accepted", "has_preview", "has_sidecar", "ok"]) {
    if (typeof value.data[name] !== "boolean") fail();
  }
  for (const name of ["asset_count", "take_count"]) {
    if (!Number.isSafeInteger(value.data[name]) || value.data[name] < 0) fail();
  }
  return Object.freeze(clone(value.data));
}

export function createDirectorManagedTakeBrowser({ pack } = {}) {
  const handle = pack?.operations?.(DIRECTOR_TAKE_OPERATION_RESOURCE_ID);
  if (!handle?.claim || !handle?.invokeExternal || !handle?.revoke) fail();
  return Object.freeze({
    async claimCapture(associationId) {
      if (typeof associationId !== "string" || !ASSOCIATION_ID.test(associationId)) fail();
      const result = await handle.claim(DIRECTOR_CAPTURE_TAKE_OPERATION_ID, associationId);
      if (
        !exactKeys(result, [
          "association", "correlationId", "data", "lease", "ok", "private",
          "references", "safePayload",
        ])
        || result.ok !== true
        || typeof result.private !== "boolean"
        || result.association !== null
        || result.lease !== null
        || !Array.isArray(result.references)
        || result.references.length !== 2
      ) fail();
      const safe = safePayload(result.safePayload);
      const asset = reference(result.references[0], DIRECTOR_CAPTURE_ASSET_KIND);
      const take = reference(result.references[1], DIRECTOR_CAPTURE_TAKE_KIND);
      return Object.freeze({
        ui: Object.freeze({ ...safe, private: result.private }),
        asset,
        take,
      });
    },

    async revokeCapture(...references) {
      if (!references.length || references.length > 2) fail();
      const ids = references.map((value) => {
        if (
          !value
          || ![DIRECTOR_CAPTURE_ASSET_KIND, DIRECTOR_CAPTURE_TAKE_KIND].includes(value.kind)
        ) fail();
        return reference(value, value.kind).id;
      });
      await handle.revoke(ids);
      return true;
    },

    async associateCapture(owner, timeline, assetValue, takeValue) {
      const asset = reference(assetValue, DIRECTOR_CAPTURE_ASSET_KIND);
      const take = reference(takeValue, DIRECTOR_CAPTURE_TAKE_KIND);
      return committedResult(await handle.invokeExternal(
        DIRECTOR_ASSOCIATE_CAPTURE_OPERATION_ID,
        owner,
        clone({ timeline }),
        { asset: asset.id, take: take.id },
      ));
    },
  });
}
