import {
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  deepClone,
} from "./schema.js";
import { normalizeVideoTimeline } from "./migration.js";
import {
  addCharacterLibraryItemToTimeline,
  createCharacterReferenceFromLibraryItem,
  createReferenceImageFromLibraryItem,
  findLoadedCharacterReferenceForLibraryItem,
  formatCharacterReferenceTag,
  getCharacterReferences,
  replaceTimelineCharacterReferenceFromLibraryItem,
} from "./references.js";
import {
  mediaViewUrl,
  thumbnailUrl,
} from "./media_cache.js";

export const ROUTE_PREFIX = "/helto_director/library";
export const TIMELINE_REPLACE_CONFIRMATION = "Replace current timeline?\n\nThis will replace all current sections, audio tracks, settings and references. Media files are referenced by path and are not copied.";

const TAB_TIMELINES = "timelines";
const TAB_CHARACTERS = "characters";
const SORT_OPTIONS = [
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
  { value: "name-asc", label: "Name A-Z" },
  { value: "name-desc", label: "Name Z-A" },
];

export async function showDirectorLibrary(options = {}) {
  const documentRef = options.documentRef ?? globalThis.document;
  installDirectorLibraryStyles(documentRef);
  closeDirectorLibrary(documentRef);

  const timeline = options.timeline ?? null;
  const privacyMode = Boolean(options.privacyMode ?? timeline?.project?.privacy?.mode);
  const overlay = documentRef.createElement("div");
  overlay.className = libraryDialogClassName(privacyMode);
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Director Library");

  const panel = el(documentRef, "div", "htd-library-panel");
  const header = el(documentRef, "div", "htd-library-header");
  const title = el(documentRef, "div", "htd-library-title");
  title.textContent = "Director Library";
  const headerActions = el(documentRef, "div", "htd-library-header-actions");
  const saveButton = textButton(documentRef, "Save Current", "Save Current Timeline", async () => {
    if (state.tab === TAB_CHARACTERS) {
      await saveCurrentCharacters({ timeline, documentRef, setStatus, privacyMode });
    } else {
      await saveCurrentTimeline({
        timeline,
        documentRef,
        setStatus,
        privacyMode,
        saveTimeline: options.onSaveTimeline,
      });
    }
    await refreshLibrary();
  });
  const closeButton = iconButton(documentRef, "close", "Close Director Library", () => finish());
  headerActions.append(saveButton, closeButton);
  header.append(title, headerActions);

  const controls = el(documentRef, "div", "htd-library-controls");
  const search = el(documentRef, "input", "htd-library-search");
  search.type = "search";
  search.placeholder = "Search library...";
  search.title = "Search library";
  const sort = el(documentRef, "select", "htd-library-sort");
  sort.title = "Sort Library";
  for (const option of SORT_OPTIONS) {
    const entry = documentRef.createElement("option");
    entry.value = option.value;
    entry.textContent = option.label;
    sort.append(entry);
  }
  const tabs = el(documentRef, "div", "htd-library-tabs");
  const timelinesTab = tabButton(documentRef, "Timelines", true);
  const charactersTab = tabButton(documentRef, "Characters", false);
  tabs.append(timelinesTab, charactersTab);
  controls.append(search, sort, tabs);

  const body = el(documentRef, "div", "htd-library-body");
  const sidebar = el(documentRef, "div", "htd-library-sidebar");
  const grid = el(documentRef, "div", "htd-library-grid");
  const details = el(documentRef, "div", "htd-library-details");
  body.append(sidebar, grid, details);

  const status = el(documentRef, "div", "htd-library-status");
  const actions = el(documentRef, "div", "htd-library-actions");
  panel.append(header, controls, body, status, actions);
  overlay.append(panel);
  documentRef.body.append(overlay);

  const state = {
    tab: TAB_TIMELINES,
    search: "",
    sort: "newest",
    tag: "",
    timelines: [],
    characters: [],
    selectedId: "",
  };

  const setStatus = (message) => {
    status.textContent = message || "";
  };

  const finish = () => {
    overlay.remove();
    options.onClose?.();
  };

  const visibleItems = () => sortedLibraryItems(
    filterLibraryItems(state.tab === TAB_TIMELINES ? state.timelines : state.characters, state.search, state.tag),
    state.sort,
  );

  const selectedItem = () => {
    const items = visibleItems();
    return items.find((item) => item.id === state.selectedId) ?? items[0] ?? null;
  };

  const render = () => {
    saveButton.textContent = state.tab === TAB_CHARACTERS ? "Save Character" : "Save Current";
    saveButton.title = state.tab === TAB_CHARACTERS ? "Save Current Character References" : "Save Current Timeline";
    saveButton.setAttribute("aria-label", saveButton.title);
    timelinesTab.classList.toggle("is-active", state.tab === TAB_TIMELINES);
    charactersTab.classList.toggle("is-active", state.tab === TAB_CHARACTERS);
    timelinesTab.setAttribute("aria-selected", state.tab === TAB_TIMELINES ? "true" : "false");
    charactersTab.setAttribute("aria-selected", state.tab === TAB_CHARACTERS ? "true" : "false");
    search.value = state.search;
    sort.value = state.sort;
    renderSidebar(documentRef, sidebar, visibleTags(state.tab === TAB_TIMELINES ? state.timelines : state.characters), state.tag, (tag) => {
      state.tag = tag;
      state.selectedId = "";
      render();
    });
    renderGrid(documentRef, grid, visibleItems(), selectedItem(), privacyMode, (item) => {
      state.selectedId = item.id;
      render();
    });
    renderDetails(documentRef, details, selectedItem(), state.tab, timeline, privacyMode);
    renderActions(documentRef, actions, {
      item: selectedItem(),
      tab: state.tab,
      timeline,
      documentRef,
      setStatus,
      callbacks: options,
      close: finish,
      refresh: refreshLibrary,
    });
  };

  timelinesTab.addEventListener("click", () => {
    state.tab = TAB_TIMELINES;
    state.tag = "";
    state.selectedId = "";
    render();
  });
  charactersTab.addEventListener("click", () => {
    state.tab = TAB_CHARACTERS;
    state.tag = "";
    state.selectedId = "";
    render();
  });
  search.addEventListener("input", () => {
    state.search = search.value;
    state.selectedId = "";
    render();
  });
  sort.addEventListener("change", () => {
    state.sort = sort.value;
    render();
  });
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) finish();
  });
  overlay.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    finish();
  });

  const refreshLibrary = async () => {
    const data = await fetchLibraryItems();
    state.timelines = data.timelines.map(normalizeLibraryTimelineItem).filter(Boolean);
    state.characters = data.characters.map(normalizeLibraryCharacterItem).filter(Boolean);
    if (state.selectedId && !visibleItems().some((item) => item.id === state.selectedId)) {
      state.selectedId = "";
    }
    render();
  };

  setStatus("Loading library...");
  render();
  try {
    await refreshLibrary();
    setStatus("");
    search.focus?.();
  } catch (error) {
    setStatus(error.message || "Could not load Director Library.");
  }

  return {
    close: finish,
    element: overlay,
  };
}

export function closeDirectorLibrary(documentRef = globalThis.document) {
  documentRef?.querySelector?.(".htd-library-dialog")?.remove();
}

export function libraryDialogClassName(privacyMode = false) {
  return `htd-library-dialog${privacyMode ? " privacy-mode" : ""}`;
}

export function clearDirectorLibraryDisplay(root) {
  if (!root) return false;
  for (const element of root.querySelectorAll?.(".htd-library-preview img, .htd-library-description, .htd-library-detail-description") ?? []) {
    if ("src" in element && element.tagName?.toLowerCase?.() === "img") element.removeAttribute("src");
    element.textContent = "";
  }
  return true;
}

export function normalizeLibraryTimelineItem(item) {
  const hasSnapshot = Boolean(item?.timeline ?? item?.snapshot ?? item?.video_timeline ?? item?.payload);
  const snapshot = hasSnapshot ? normalizeVideoTimeline(item?.timeline ?? item?.snapshot ?? item?.video_timeline ?? item?.payload) : null;
  const summary = item?.summary && typeof item.summary === "object" && !Array.isArray(item.summary) ? item.summary : {};
  return {
    id: String(item?.id ?? item?.library_id ?? stableHash(JSON.stringify(snapshot))),
    kind: TAB_TIMELINES,
    title: String(item?.title ?? item?.name ?? snapshot?.project?.metadata?.title ?? "Untitled Timeline"),
    description: String(item?.description ?? summaryText(summary)),
    tags: normalizeTags(item?.tags ?? snapshot?.project?.metadata?.tags),
    updatedAt: timestampValue(item?.updated_at ?? item?.mtime ?? item?.created_at),
    summary,
    isPrivate: Boolean(item?.is_private ?? item?.private),
    timeline: snapshot,
    previewAsset: snapshot ? firstTimelinePreviewAsset(snapshot) : null,
    source: item,
  };
}

export function normalizeLibraryCharacterItem(item) {
  const source = item?.character ?? item?.payload ?? item;
  const image = createReferenceImageFromLibraryItem(source) ?? null;
  const summary = item?.summary && typeof item.summary === "object" && !Array.isArray(item.summary) ? item.summary : {};
  return {
    id: String(item?.id ?? item?.library_id ?? stableHash(image?.path ?? item?.name ?? item?.updated_at)),
    kind: TAB_CHARACTERS,
    title: String(item?.title ?? item?.name ?? image?.name ?? "Character"),
    description: String(item?.description ?? source?.description ?? ""),
    tags: normalizeTags(item?.tags),
    updatedAt: timestampValue(item?.updated_at ?? item?.mtime ?? item?.created_at),
    summary,
    isPrivate: Boolean(item?.is_private ?? item?.private),
    image,
    source,
  };
}

function renderSidebar(documentRef, sidebar, tags, activeTag, onSelect) {
  sidebar.replaceChildren();
  const title = el(documentRef, "div", "htd-library-sidebar-title");
  title.textContent = "Filters";
  const all = filterButton(documentRef, "All", activeTag === "", () => onSelect(""));
  sidebar.append(title, all);
  for (const tag of tags) {
    sidebar.append(filterButton(documentRef, tag, activeTag === tag, () => onSelect(tag)));
  }
}

function renderGrid(documentRef, grid, items, selected, privacyMode, onSelect) {
  grid.replaceChildren();
  if (!items.length) {
    const empty = el(documentRef, "div", "htd-library-empty");
    empty.textContent = "No library items.";
    grid.append(empty);
    return;
  }
  for (const item of items) {
    const card = el(documentRef, "button", `htd-library-card${selected?.id === item.id ? " is-selected" : ""}`);
    card.type = "button";
    card.title = item.title;
    card.append(renderPreview(documentRef, item, privacyMode, "htd-library-card-preview"));
    const meta = el(documentRef, "div", "htd-library-card-meta");
    const name = el(documentRef, "div", "htd-library-card-title");
    name.textContent = item.title;
    const description = el(documentRef, "div", "htd-library-description");
    description.textContent = item.description;
    meta.append(name, description, renderTags(documentRef, item.tags));
    card.append(meta);
    card.addEventListener("click", () => onSelect(item));
    grid.append(card);
  }
}

function renderDetails(documentRef, details, item, tab, timeline, privacyMode) {
  details.replaceChildren();
  const title = el(documentRef, "div", "htd-library-details-title");
  title.textContent = "Preview";
  details.append(title);
  if (!item) {
    const empty = el(documentRef, "div", "htd-library-empty");
    empty.textContent = "Select a library item.";
    details.append(empty);
    return;
  }
  details.append(renderPreview(documentRef, item, privacyMode, "htd-library-detail-preview"));
  const name = el(documentRef, "div", "htd-library-detail-name");
  name.textContent = item.title;
  const description = el(documentRef, "div", "htd-library-detail-description");
  description.textContent = item.description;
  details.append(name, description, renderTags(documentRef, item.tags));
  details.append(renderSummary(documentRef, item));
  if (tab === TAB_CHARACTERS) {
    const existing = findLoadedCharacterReferenceForLibraryItem(timeline, item.source);
    if (existing) {
      const loaded = el(documentRef, "div", "htd-library-loaded");
      loaded.textContent = `Already loaded as ${formatCharacterReferenceTag(existing)}`;
      details.append(loaded);
    }
  }
}

function renderSummary(documentRef, item) {
  const summary = item?.summary && typeof item.summary === "object" && !Array.isArray(item.summary) ? item.summary : {};
  const list = el(documentRef, "div", "htd-library-summary");
  const rows = item.kind === TAB_TIMELINES
    ? [
        ["Duration", formatSeconds(summary.duration_seconds)],
        ["Frame Rate", formatNumber(summary.frame_rate)],
        ["Aspect", summary.aspect_ratio],
        ["Sections", summary.section_count],
        ["References", summary.character_reference_count ?? summary.character_count],
      ]
    : [
        ["Default Tag", summary.label],
        ["Strength", formatNumber(summary.strength)],
        ["Image", summary.has_image ? "OK" : "Missing"],
      ];
  for (const [label, value] of rows) {
    if (value == null || value === "") continue;
    const row = el(documentRef, "div", "htd-library-summary-row");
    const labelEl = el(documentRef, "span", "htd-library-summary-label");
    labelEl.textContent = label;
    const valueEl = el(documentRef, "span", "htd-library-summary-value");
    valueEl.textContent = String(value);
    row.append(labelEl, valueEl);
    list.append(row);
  }
  return list;
}

function renderActions(documentRef, actions, context) {
  const { item, tab, timeline, callbacks, setStatus, close, refresh } = context;
  actions.replaceChildren();
  if (!item) return;
  if (tab === TAB_TIMELINES) {
    actions.append(textButton(documentRef, "Replace Current Timeline", "Replace Current Timeline", async () => {
      const confirmFn = documentRef.defaultView?.confirm ?? globalThis.confirm;
      if (confirmFn && !confirmFn(TIMELINE_REPLACE_CONFIRMATION)) return;
      const full = await fetchTimelineForUse(item);
      replaceTimelineFromLibrary(callbacks, deepClone(full.timeline), full);
      close();
    }));
    actions.append(textButton(documentRef, "Overwrite", "Overwrite Saved Timeline", async () => {
      await updateTimelineLibraryItem(item.id, timeline);
      setStatus("Overwrote saved timeline.");
      await refresh?.();
    }));
    actions.append(textButton(documentRef, "Duplicate", "Duplicate Saved Timeline", async () => {
      await duplicateLibraryItem(TAB_TIMELINES, item.id);
      setStatus("Duplicated timeline.");
      await refresh?.();
    }));
    actions.append(textButton(documentRef, "Rename", "Rename Saved Timeline", async () => {
      const name = promptForText(documentRef, "Timeline name", item.title);
      if (!name) return;
      await patchLibraryItem(TAB_TIMELINES, item.id, { name });
      setStatus("Renamed timeline.");
      await refresh?.();
    }));
    actions.append(textButton(documentRef, "Delete", "Delete Saved Timeline", async () => {
      if (!confirmDelete(documentRef, `Delete "${item.title}"?`)) return;
      await deleteLibraryItem(TAB_TIMELINES, item.id);
      setStatus("Deleted timeline.");
      await refresh?.();
    }));
    actions.append(textButton(documentRef, "Export JSON", "Export Timeline JSON", async () => {
      const full = await fetchTimelineForUse(item);
      exportJson(documentRef, `${safeFilename(item.title)}.json`, full.timeline);
      setStatus("Exported timeline JSON.");
    }));
    return;
  }

  actions.append(
    textButton(documentRef, "Add to Timeline", "Add to Timeline", async () => {
      const full = await fetchCharacterForUse(item);
      const reference = addCharacterFromLibrary(callbacks, full.character, item, false);
      if (reference) setStatus(`Added ${formatCharacterReferenceTag(reference)}.`);
    }),
    textButton(documentRef, "Add + Insert Tag", "Add and Insert Reference Tag", async () => {
      const full = await fetchCharacterForUse(item);
      const reference = addCharacterFromLibrary(callbacks, full.character, item, true);
      if (reference) setStatus(`Inserted ${formatCharacterReferenceTag(reference)}.`);
    }),
    textButton(documentRef, "Copy Tag", "Copy Reference Tag", async () => {
      const tag = tagForCharacterItem(timeline, item.source);
      await copyTextWithPromptFallback(documentRef, tag, "Character reference tag");
      setStatus(`Copied ${tag}.`);
    }),
  );

  const references = getCharacterReferences(timeline);
  const replaceSelect = el(documentRef, "select", "htd-library-replace-select");
  replaceSelect.title = "Reference to Replace";
  for (const reference of references) {
    const option = documentRef.createElement("option");
    option.value = reference.id;
    option.textContent = formatCharacterReferenceTag(reference);
    replaceSelect.append(option);
  }
  const replaceButton = textButton(documentRef, "Replace", "Replace preserving label", () => {
    fetchCharacterForUse(item).then((full) => {
      const reference = replaceCharacterFromLibrary(callbacks, replaceSelect.value, full.character, item);
      if (reference) setStatus(`Replaced ${formatCharacterReferenceTag(reference)}.`);
    }).catch((error) => setStatus(error.message));
  });
  replaceButton.disabled = references.length === 0;
  replaceSelect.disabled = references.length === 0;
  actions.append(replaceSelect, replaceButton);
  actions.append(
    textButton(documentRef, "Duplicate", "Duplicate Character", async () => {
      await duplicateLibraryItem(TAB_CHARACTERS, item.id);
      setStatus("Duplicated character.");
      await context.refresh?.();
    }),
    textButton(documentRef, "Delete", "Delete Character", async () => {
      if (!confirmDelete(documentRef, `Delete "${item.title}"?`)) return;
      await deleteLibraryItem(TAB_CHARACTERS, item.id);
      setStatus("Deleted character.");
      await context.refresh?.();
    }),
    textButton(documentRef, "Export", "Export Character JSON", async () => {
      const full = await fetchCharacterForUse(item);
      exportJson(documentRef, `${safeFilename(item.title)}.json`, full.character);
      setStatus("Exported character JSON.");
    }),
  );
}

function replaceTimelineFromLibrary(options, nextTimeline, item) {
  if (typeof options.onReplaceTimeline === "function") {
    return options.onReplaceTimeline(nextTimeline, item);
  }
  return options.controller?.replaceTimelineFromLibrary?.(nextTimeline, "replace timeline from library") ?? null;
}

function addCharacterFromLibrary(options, item, libraryItem, insertTag) {
  const callback = insertTag ? options.onAddCharacterAndInsertTag : options.onAddCharacter;
  if (typeof callback === "function") return callback(item, libraryItem);
  let reference = null;
  options.controller?.updateTimeline?.((timeline) => {
    reference = addCharacterLibraryItemToTimeline(timeline, item, insertTag ? { insertTag: true } : {});
  }, insertTag ? "add library character tag" : "add library character");
  return reference;
}

function replaceCharacterFromLibrary(options, referenceId, item, libraryItem) {
  if (typeof options.onReplaceCharacter === "function") return options.onReplaceCharacter(referenceId, item, libraryItem);
  let reference = null;
  options.controller?.updateTimeline?.((timeline) => {
    reference = replaceTimelineCharacterReferenceFromLibraryItem(timeline, referenceId, item);
  }, "replace library character");
  return reference;
}

function renderPreview(documentRef, item, privacyMode, className) {
  const preview = el(documentRef, "button", `htd-library-preview ${className}`);
  preview.type = "button";
  preview.title = "Open Preview";
  const asset = item.kind === TAB_CHARACTERS ? item.image : item.previewAsset;
  if (asset?.path) {
    const img = documentRef.createElement("img");
    img.alt = item.title;
    img.src = thumbnailUrl(asset, 320, privacyMode);
    preview.append(img);
    preview.addEventListener("click", (event) => {
      event.stopPropagation();
      const url = mediaViewUrl(asset);
      if (url) windowOpen(documentRef, url);
    });
  } else {
    preview.append(iconSvg(documentRef, item.kind === TAB_TIMELINES ? "timeline" : "character"));
  }
  return preview;
}

function renderTags(documentRef, tags) {
  const row = el(documentRef, "div", "htd-library-tags");
  for (const tag of tags.slice(0, 4)) {
    const chip = el(documentRef, "span", "htd-library-tag");
    chip.textContent = tag;
    row.append(chip);
  }
  return row;
}

async function saveCurrentTimeline({ timeline, documentRef, setStatus, saveTimeline }) {
  if (!timeline) return;
  try {
    if (typeof saveTimeline === "function") {
      await saveTimeline(deepClone(timeline));
    } else {
      await fetchLibraryJson(`${ROUTE_PREFIX}/timelines`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: timelineName(timeline),
          private: Boolean(timeline?.project?.privacy?.mode),
          timeline,
        }),
      });
    }
    setStatus("Saved current timeline.");
  } catch (error) {
    const alertFn = documentRef.defaultView?.alert ?? globalThis.alert;
    alertFn?.(error.message);
    setStatus(error.message || "Could not save current timeline.");
  }
}

async function saveCurrentCharacters({ timeline, documentRef, setStatus, privacyMode }) {
  const references = getCharacterReferences(timeline);
  if (!references.length) {
    const alertFn = documentRef.defaultView?.alert ?? globalThis.alert;
    alertFn?.("No character references in the current timeline.");
    setStatus("No character references in the current timeline.");
    return;
  }
  for (const reference of references) {
    await fetchLibraryJson(`${ROUTE_PREFIX}/characters`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: reference.description || formatCharacterReferenceTag(reference),
        description: reference.description || "",
        private: privacyMode,
        character: reference,
      }),
    });
  }
  setStatus(references.length === 1 ? "Saved character." : `Saved ${references.length} characters.`);
}

function tagForCharacterItem(timeline, item) {
  const existing = findLoadedCharacterReferenceForLibraryItem(timeline, item);
  if (existing) return formatCharacterReferenceTag(existing);
  const reference = createCharacterReferenceFromLibraryItem(timeline, item);
  return reference ? formatCharacterReferenceTag(reference) : "@image1:character";
}

async function copyTextWithPromptFallback(documentRef, value, label) {
  try {
    const clipboard = documentRef.defaultView?.navigator?.clipboard;
    if (typeof clipboard?.writeText !== "function") throw new Error("Clipboard unavailable");
    await clipboard.writeText(value);
  } catch (_error) {
    const promptFn = documentRef.defaultView?.prompt ?? globalThis.prompt;
    promptFn?.(label, value);
  }
}

async function fetchLibraryItems() {
  const data = await fetchLibraryJson(`${ROUTE_PREFIX}/items`);
  return {
    timelines: Array.isArray(data.timelines) ? data.timelines : [],
    characters: Array.isArray(data.characters) ? data.characters : [],
  };
}

async function fetchTimelineForUse(item) {
  if (item?.timeline) return item;
  const data = await fetchLibraryJson(`${ROUTE_PREFIX}/timelines/${encodeURIComponent(item.id)}/use`, { method: "POST" });
  const timeline = data.timeline ?? data.item?.timeline ?? data.item?.payload;
  return {
    ...item,
    timeline: normalizeVideoTimeline(timeline),
  };
}

async function fetchCharacterForUse(item) {
  if (item?.source?.image?.path) return { ...item, character: item.source };
  const data = await fetchLibraryJson(`${ROUTE_PREFIX}/characters/${encodeURIComponent(item.id)}/use`, { method: "POST" });
  const character = data.character ?? data.item?.character ?? data.item?.payload;
  return {
    ...item,
    character,
  };
}

async function updateTimelineLibraryItem(itemId, timeline) {
  return fetchLibraryJson(`${ROUTE_PREFIX}/timelines/${encodeURIComponent(itemId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: timelineName(timeline),
      private: Boolean(timeline?.project?.privacy?.mode),
      timeline,
    }),
  });
}

async function duplicateLibraryItem(tab, itemId) {
  const route = tab === TAB_CHARACTERS ? "characters" : "timelines";
  return fetchLibraryJson(`${ROUTE_PREFIX}/${route}/${encodeURIComponent(itemId)}/duplicate`, { method: "POST" });
}

async function patchLibraryItem(tab, itemId, metadata) {
  const route = tab === TAB_CHARACTERS ? "characters" : "timelines";
  return fetchLibraryJson(`${ROUTE_PREFIX}/${route}/${encodeURIComponent(itemId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(metadata),
  });
}

async function deleteLibraryItem(tab, itemId) {
  const route = tab === TAB_CHARACTERS ? "characters" : "timelines";
  return fetchLibraryJson(`${ROUTE_PREFIX}/${route}/${encodeURIComponent(itemId)}`, { method: "DELETE" });
}

async function fetchLibraryJson(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(text || response.statusText || `HTTP ${response.status}`);
  }
  if (!response.ok || data.error) throw new Error(data.error || response.statusText || `HTTP ${response.status}`);
  return data;
}

function filterLibraryItems(items, query, tag) {
  const needle = String(query ?? "").trim().toLowerCase();
  return items.filter((item) => {
    const matchesTag = !tag || item.tags.includes(tag);
    if (!matchesTag) return false;
    if (!needle) return true;
    return [item.title, item.description, ...item.tags].some((value) => String(value).toLowerCase().includes(needle));
  });
}

function sortedLibraryItems(items, sortMode) {
  return [...items].sort((a, b) => {
    if (sortMode === "oldest") return a.updatedAt - b.updatedAt || a.title.localeCompare(b.title);
    if (sortMode === "name-asc") return a.title.localeCompare(b.title);
    if (sortMode === "name-desc") return b.title.localeCompare(a.title);
    return b.updatedAt - a.updatedAt || a.title.localeCompare(b.title);
  });
}

function visibleTags(items) {
  return Array.from(new Set(items.flatMap((item) => item.tags))).sort((a, b) => a.localeCompare(b));
}

function firstTimelinePreviewAsset(timeline) {
  return (timeline?.assets ?? []).find((asset) => (
    (asset.type === ASSET_TYPE_IMAGE || asset.type === ASSET_TYPE_VIDEO) &&
    asset.path
  )) ?? null;
}

function normalizeTags(value) {
  if (!Array.isArray(value)) return [];
  return value.map((tag) => String(tag ?? "").trim()).filter(Boolean);
}

function timestampValue(value) {
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric;
  const parsed = Date.parse(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function summaryText(summary) {
  if (!summary || typeof summary !== "object" || Array.isArray(summary)) return "";
  const parts = [];
  if (summary.duration_seconds != null) parts.push(`${formatSeconds(summary.duration_seconds)}`);
  if (summary.frame_rate != null) parts.push(`${formatNumber(summary.frame_rate)} fps`);
  if (summary.aspect_ratio) parts.push(summary.aspect_ratio);
  if (summary.section_count != null) parts.push(`${summary.section_count} sections`);
  return parts.join(" · ");
}

function formatSeconds(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(number % 1 ? 1 : 0)}s` : "";
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? String(Number(number.toFixed(2))) : "";
}

function timelineName(timeline) {
  const metadata = timeline?.project?.metadata ?? {};
  return String(metadata.title || metadata.name || "Untitled Timeline");
}

function promptForText(documentRef, label, value) {
  const promptFn = documentRef.defaultView?.prompt ?? globalThis.prompt;
  return String(promptFn?.(label, value) ?? "").trim();
}

function confirmDelete(documentRef, message) {
  const confirmFn = documentRef.defaultView?.confirm ?? globalThis.confirm;
  return !confirmFn || confirmFn(message);
}

function exportJson(documentRef, filename, payload) {
  const win = documentRef.defaultView ?? globalThis.window;
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json" });
  const url = win.URL?.createObjectURL?.(blob);
  if (!url) return;
  const link = documentRef.createElement("a");
  link.href = url;
  link.download = filename;
  documentRef.body.append(link);
  link.click?.();
  link.remove();
  win.setTimeout?.(() => win.URL?.revokeObjectURL?.(url), 0);
}

function safeFilename(value) {
  return String(value || "director-library").replace(/[^A-Za-z0-9_. -]+/g, "_").trim().slice(0, 80) || "director-library";
}

function tabButton(documentRef, label, active) {
  const control = textButton(documentRef, label, label, () => {});
  control.classList.add("htd-library-tab");
  control.setAttribute("role", "tab");
  control.classList.toggle("is-active", active);
  return control;
}

function filterButton(documentRef, label, active, onClick) {
  const control = textButton(documentRef, label, label, onClick);
  control.classList.add("htd-library-filter");
  control.classList.toggle("is-active", active);
  return control;
}

function textButton(documentRef, text, title, onClick) {
  const control = el(documentRef, "button", "htd-library-button");
  control.type = "button";
  control.textContent = text;
  control.title = title;
  control.setAttribute("aria-label", title);
  control.addEventListener("click", onClick);
  return control;
}

function iconButton(documentRef, iconName, title, onClick) {
  const control = textButton(documentRef, "", title, onClick);
  control.classList.add("htd-library-icon-button");
  control.append(iconSvg(documentRef, iconName));
  return control;
}

function iconSvg(documentRef, name) {
  const span = el(documentRef, "span", "htd-library-icon");
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = ICONS[name] ?? ICONS.timeline;
  return span;
}

function windowOpen(documentRef, url) {
  const win = documentRef.defaultView ?? globalThis.window;
  win?.open?.(url, "_blank", "noopener");
}

function el(documentRef, tag, className) {
  const element = documentRef.createElement(tag);
  if (className) element.className = className;
  return element;
}

function stableHash(value) {
  let hash = 2166136261;
  const text = String(value ?? "");
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

const ICONS = {
  timeline: `<svg viewBox="0 0 24 24"><path d="M4 7h16M4 17h16M8 4v6M16 14v6"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></svg>`,
  character: `<svg viewBox="0 0 24 24"><path d="M7 19a5 5 0 0 1 10 0"/><circle cx="12" cy="9" r="3"/><path d="M4 5h4M16 5h4M4 5v4M20 5v4"/></svg>`,
  close: `<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>`,
};

function installDirectorLibraryStyles(documentRef) {
  if (!documentRef || documentRef.getElementById("helto-director-library-style")) return;
  const style = documentRef.createElement("style");
  style.id = "helto-director-library-style";
  style.textContent = `
    .htd-library-dialog { position: fixed; inset: 0; z-index: 10020; display: flex; align-items: stretch; justify-content: center; padding: 18px; box-sizing: border-box; background: rgba(8, 11, 17, 0.84); color: #d8dde8; font: 12px/1.3 system-ui, sans-serif; }
    .htd-library-panel { width: min(1080px, 100%); min-height: 0; display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto auto; border: 1px solid #465064; border-radius: 6px; background: #121925; box-shadow: 0 16px 38px rgba(0,0,0,0.48); overflow: hidden; }
    .htd-library-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px; border-bottom: 1px solid #30394c; }
    .htd-library-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; color: #eef2f7; }
    .htd-library-header-actions, .htd-library-tabs, .htd-library-actions { display: inline-flex; align-items: center; gap: 4px; }
    .htd-library-controls { min-width: 0; display: grid; grid-template-columns: minmax(180px, 1fr) 126px auto; gap: 6px; align-items: center; padding: 8px; border-bottom: 1px solid #30394c; }
    .htd-library-search, .htd-library-sort, .htd-library-replace-select { min-width: 0; height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 8px; }
    .htd-library-body { min-height: 0; display: grid; grid-template-columns: 148px minmax(260px, 1fr) 274px; gap: 8px; padding: 8px; overflow: hidden; }
    .htd-library-sidebar, .htd-library-grid, .htd-library-details { min-height: 0; overflow: auto; }
    .htd-library-sidebar { display: flex; flex-direction: column; gap: 4px; padding-right: 4px; border-right: 1px solid #30394c; }
    .htd-library-sidebar-title, .htd-library-details-title { color: #9ba8bd; font-weight: 600; }
    .htd-library-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(178px, 1fr)); grid-auto-rows: min-content; gap: 8px; }
    .htd-library-details { display: flex; flex-direction: column; gap: 8px; padding-left: 8px; border-left: 1px solid #30394c; }
    .htd-library-button { min-width: 28px; height: 24px; padding: 0 8px; border: 1px solid #4b5568; border-radius: 4px; background: #202633; color: #f2f5f8; cursor: pointer; white-space: nowrap; }
    .htd-library-icon-button { width: 28px; min-width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center; }
    .htd-library-button.is-active { border-color: #d6b65a; background: #4b3d1e; color: #fff1b8; }
    .htd-library-button:disabled, .htd-library-replace-select:disabled { opacity: 0.44; cursor: not-allowed; }
    .htd-library-icon { width: 16px; height: 16px; display: inline-flex; align-items: center; justify-content: center; }
    .htd-library-icon svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .htd-library-filter { width: 100%; justify-content: flex-start; text-align: left; overflow: hidden; text-overflow: ellipsis; }
    .htd-library-card { min-width: 0; display: grid; grid-template-rows: 104px minmax(0, auto); gap: 6px; padding: 6px; border: 1px solid #30394c; border-radius: 6px; background: rgba(17, 23, 34, 0.58); color: #d8dde8; text-align: left; cursor: pointer; }
    .htd-library-card.is-selected { outline: 2px solid #f2d16b; outline-offset: -2px; }
    .htd-library-preview { width: 100%; min-width: 0; height: 100%; min-height: 96px; padding: 0; border: 1px solid #3d4658; border-radius: 4px; background: #101722; color: #9ba8bd; display: flex; align-items: center; justify-content: center; overflow: hidden; }
    .htd-library-preview img { width: 100%; height: 100%; object-fit: contain; display: block; }
    .htd-library-card-meta { min-width: 0; display: grid; gap: 3px; }
    .htd-library-card-title, .htd-library-detail-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #eef2f7; font-weight: 600; }
    .htd-library-description, .htd-library-detail-description { min-width: 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; color: #9ba8bd; }
    .htd-library-detail-preview { height: 172px; flex: 0 0 172px; }
    .htd-library-tags { min-width: 0; display: flex; flex-wrap: wrap; gap: 3px; }
    .htd-library-tag { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 1px 5px; border: 1px solid #394255; border-radius: 999px; color: #c7d0df; background: #151c29; font-size: 10px; }
    .htd-library-loaded { padding: 6px; border: 1px solid #4b3d1e; border-radius: 4px; background: rgba(214, 182, 90, 0.12); color: #fff1b8; }
    .htd-library-status { min-height: 18px; padding: 0 8px 4px; color: #9ba8bd; }
    .htd-library-actions { justify-content: flex-end; padding: 8px; border-top: 1px solid #30394c; }
    .htd-library-empty { grid-column: 1 / -1; padding: 18px 8px; text-align: center; color: #9ba8bd; }
    .htd-library-dialog.privacy-mode .htd-library-preview img,
    .htd-library-dialog.privacy-mode .htd-library-description,
    .htd-library-dialog.privacy-mode .htd-library-detail-description { opacity: 0; }
    .htd-library-dialog.privacy-mode .htd-library-card:hover .htd-library-preview img,
    .htd-library-dialog.privacy-mode .htd-library-card:hover .htd-library-description,
    .htd-library-dialog.privacy-mode .htd-library-details:hover .htd-library-preview img,
    .htd-library-dialog.privacy-mode .htd-library-details:hover .htd-library-detail-description { opacity: 1; }
  `;
  documentRef.head.append(style);
}
