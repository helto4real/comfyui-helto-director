import {
  ASSET_SOURCE_FILE_PATH,
  ASSET_TYPE_IMAGE,
  deepClone,
} from "./schema.js";

export const REFERENCE_KIND_CHARACTER = "character";
export const PROMPT_REFERENCE_TRIGGER = "@";

const REFERENCE_TAG_RE = /@(?<label>image[1-9]\d*):(?<kind>[A-Za-z][A-Za-z0-9_-]*)(?:\[(?<strength>[^\]]*)\])?/g;

export function getCharacterReferences(timeline) {
  const references = timeline?.project?.metadata?.character_references;
  return Array.isArray(references) ? references : [];
}

export function activeCharacterReferences(timeline) {
  return getCharacterReferences(timeline).filter((reference) => reference.enabled !== false);
}

export function getReferencePromptCompletions(timeline) {
  return activeCharacterReferences(timeline).map((reference) => ({
    id: reference.id,
    label: reference.label,
    tag: formatCharacterReferenceTag(reference),
    description: reference.description || "",
    trigger: PROMPT_REFERENCE_TRIGGER,
  }));
}

export function formatCharacterReferenceTag(referenceOrLabel, strengthOverride = null) {
  const label = normalizeReferenceLabel(
    typeof referenceOrLabel === "string" ? referenceOrLabel : referenceOrLabel?.label,
  );
  const strength = finiteNumberOrNull(strengthOverride);
  return strength == null
    ? `@${label}:${REFERENCE_KIND_CHARACTER}`
    : `@${label}:${REFERENCE_KIND_CHARACTER}[${strength}]`;
}

export function parseReferenceTags(prompt) {
  const tags = [];
  const text = String(prompt ?? "");
  for (const match of text.matchAll(REFERENCE_TAG_RE)) {
    const label = normalizeReferenceLabel(match.groups?.label);
    const kind = String(match.groups?.kind ?? "").toLowerCase();
    const strengthOverride = finiteNumberOrNull(match.groups?.strength);
    tags.push({
      label,
      kind,
      token: match[0],
      supported: kind === REFERENCE_KIND_CHARACTER,
      strength_override: strengthOverride,
    });
  }
  return tags;
}

export function normalizeReferenceLabel(value, fallbackIndex = 0) {
  const label = String(value ?? "").trim().toLowerCase();
  return /^image[1-9]\d*$/.test(label) ? label : `image${Number(fallbackIndex) + 1}`;
}

export function normalizeCharacterReferences(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((reference) => reference && typeof reference === "object" && !Array.isArray(reference))
    .map((reference, index) => {
      const normalized = deepClone(reference);
      normalized.label = normalizeReferenceLabel(normalized.label, index);
      normalized.id = String(normalized.id || normalized.label);
      normalized.kind = REFERENCE_KIND_CHARACTER;
      normalized.enabled = normalized.enabled !== false;
      normalized.description = String(normalized.description ?? "");
      normalized.strength = clampStrength(normalized.strength);
      normalized.image = normalizeReferenceImage(normalized.image);
      return normalized;
    });
}

export function ensureCharacterReferences(timeline) {
  timeline.project ??= {};
  timeline.project.metadata = timeline.project.metadata && typeof timeline.project.metadata === "object" && !Array.isArray(timeline.project.metadata)
    ? timeline.project.metadata
    : {};
  timeline.project.metadata.character_references = normalizeCharacterReferences(timeline.project.metadata.character_references);
  return timeline.project.metadata.character_references;
}

export function createCharacterReferenceFromPickedItem(timeline, item) {
  const image = createReferenceImageFromPickedItem(item);
  if (!image) return null;
  const references = getCharacterReferences(timeline);
  const label = nextReferenceLabel(references);
  return {
    id: makeReferenceId(label, image.path || image.name || ""),
    label,
    kind: REFERENCE_KIND_CHARACTER,
    enabled: true,
    description: "",
    strength: 1.0,
    image,
  };
}

export function addCharacterReference(timeline, item) {
  const references = ensureCharacterReferences(timeline);
  const reference = createCharacterReferenceFromPickedItem(timeline, item);
  if (!reference) return null;
  references.push(reference);
  return reference;
}

export function removeCharacterReference(timeline, referenceId) {
  const references = ensureCharacterReferences(timeline);
  const next = references.filter((reference) => reference.id !== referenceId);
  timeline.project.metadata.character_references = next;
  return next.length !== references.length;
}

export function createReferenceImageFromPickedItem(item) {
  const path = String(item?.path ?? "").trim();
  if (!path) return null;
  return normalizeReferenceImage({
    type: ASSET_TYPE_IMAGE,
    source_kind: ASSET_SOURCE_FILE_PATH,
    path,
    name: item.name ?? basename(item.filename ?? path),
    mime_type: item.mime_type ?? "",
    size_bytes: Number.isFinite(item.size) ? item.size : null,
    metadata: {
      browser_alias: item.folder_alias ?? null,
      browser_filename: item.filename ?? null,
      mtime: Number.isFinite(item.mtime) ? item.mtime : null,
      width: Number.isFinite(item.width) ? item.width : null,
      height: Number.isFinite(item.height) ? item.height : null,
    },
  });
}

export function normalizeReferenceImage(image) {
  if (!image || typeof image !== "object" || Array.isArray(image)) return null;
  const path = String(image.path ?? image.file_path ?? "").trim();
  const normalized = deepClone(image);
  normalized.type = ASSET_TYPE_IMAGE;
  normalized.source_kind = normalized.source_kind || ASSET_SOURCE_FILE_PATH;
  normalized.path = path || null;
  normalized.name = String(normalized.name ?? basename(path) ?? "");
  normalized.mime_type = normalized.mime_type ?? "";
  normalized.size_bytes = Number.isFinite(normalized.size_bytes) ? normalized.size_bytes : null;
  normalized.metadata = normalized.metadata && typeof normalized.metadata === "object" && !Array.isArray(normalized.metadata)
    ? normalized.metadata
    : {};
  delete normalized.asset_id;
  return normalized;
}

function nextReferenceLabel(references) {
  const used = new Set(references.map((reference, index) => normalizeReferenceLabel(reference?.label, index)));
  let index = 1;
  while (used.has(`image${index}`)) index += 1;
  return `image${index}`;
}

function clampStrength(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 1.0;
  return Math.max(0, Math.min(1, numeric));
}

function finiteNumberOrNull(value) {
  if (value == null || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function makeReferenceId(label, seed) {
  return `reference_${label}_${stableHash(seed).toString(36)}_${Date.now().toString(36)}`;
}

function stableHash(value) {
  let hash = 2166136261;
  for (let index = 0; index < String(value).length; index += 1) {
    hash ^= String(value).charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function basename(path) {
  return String(path ?? "").split(/[\\/]/).filter(Boolean).pop() ?? "";
}
