import {
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  PROJECT_STORAGE_SCHEMA_VERSION,
  createDefaultProjectIdentity,
  deepClone,
  projectDirectoryName,
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
import { showMediaPreview } from "./media_preview.js";

export const ROUTE_PREFIX = "/helto_director/library";
export const PROJECT_REPLACE_CONFIRMATION = "Replace current project?\n\nThis will replace all current sections, audio tracks, settings and references. Media files are referenced by path and are not copied.";
export const PROJECT_UPDATE_CONFIRMATION = "Update saved project with current timeline?\n\nThis will overwrite the linked Director Library project. Save as a new project if you want to keep the existing saved version.";

const TAB_PROJECTS = "projects";
const TAB_CHARACTERS = "characters";
const PROJECT_LIBRARY_ITEM_ID_KEY = "library_item_id";
const SORT_OPTIONS = [
  { value: "newest", label: "Recently Updated" },
  { value: "oldest", label: "Oldest First" },
  { value: "name-asc", label: "Name A-Z" },
  { value: "name-desc", label: "Name Z-A" },
];

export async function showDirectorLibrary(options = {}) {
  const documentRef = options.documentRef ?? globalThis.document;
  installDirectorLibraryStyles(documentRef);
  closeDirectorLibrary(documentRef);

  const timeline = options.timeline ?? null;
  const privacyMode = Boolean(options.privacyMode);
  const overlay = documentRef.createElement("div");
  overlay.className = libraryDialogClassName(privacyMode);
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Director Library");

  const panel = el(documentRef, "div", "htd-library-panel");
  const header = el(documentRef, "div", "htd-library-header");
  const title = el(documentRef, "div", "htd-library-title");
  title.textContent = "Director Library";
  const closeButton = iconButton(documentRef, "close", "Close Director Library", () => finish());
  header.append(title, closeButton);

  const controls = el(documentRef, "div", "htd-library-controls");
  const searchWrap = el(documentRef, "label", "htd-library-search-wrap");
  searchWrap.append(iconSvg(documentRef, "search"));
  const search = el(documentRef, "input", "htd-library-search");
  search.type = "search";
  search.placeholder = "Search projects...";
  search.title = "Search library";
  searchWrap.append(search);
  const sort = el(documentRef, "select", "htd-library-sort");
  sort.title = "Sort Library";
  for (const option of SORT_OPTIONS) {
    const entry = documentRef.createElement("option");
    entry.value = option.value;
    entry.textContent = option.label;
    sort.append(entry);
  }
  const saveButton = iconButton(documentRef, "plus", "Save Project", async (event) => {
    if (state.tab === TAB_CHARACTERS) {
      await saveCurrentCharacters({ timeline, documentRef, setStatus, privacyMode });
      await refreshLibrary();
      return;
    }
    const linkedId = linkedProjectLibraryItemId(timeline);
    if (linkedId) {
      showProjectSaveChoicePopup(documentRef, saveButton, {
        update: async () => {
          await updateCurrentProjectLibraryItem({
            timeline,
            itemId: linkedId,
            documentRef,
            setStatus,
            privacyMode,
            callbacks: options,
          });
          await refreshLibrary();
        },
        saveAsNew: async () => {
          await saveCurrentProjectAsNew({
            timeline,
            documentRef,
            setStatus,
            privacyMode,
            saveProject: options.onSaveProject,
            callbacks: options,
            forkProject: true,
          });
          await refreshLibrary();
        },
      });
    } else {
      await saveCurrentProjectAsNew({
        timeline,
        documentRef,
        setStatus,
        privacyMode,
        saveProject: options.onSaveProject,
        callbacks: options,
      });
      await refreshLibrary();
    }
  });
  saveButton.classList.add("htd-library-primary");
  controls.append(searchWrap, sort, saveButton);

  const tabs = el(documentRef, "div", "htd-library-tabs");
  const projectsTab = tabButton(documentRef, "Projects", true);
  const charactersTab = tabButton(documentRef, "Characters", false);
  tabs.append(projectsTab, charactersTab);

  const body = el(documentRef, "div", "htd-library-body");
  const sidebar = el(documentRef, "div", "htd-library-sidebar");
  const grid = el(documentRef, "div", "htd-library-grid");
  const details = el(documentRef, "div", "htd-library-details");
  body.append(sidebar, grid, details);

  const status = el(documentRef, "div", "htd-library-status");
  const actions = el(documentRef, "div", "htd-library-actions");
  panel.append(header, controls, tabs, body, status, actions);
  overlay.append(panel);
  documentRef.body.append(overlay);

  const state = {
    tab: TAB_PROJECTS,
    search: "",
    sort: "newest",
    tag: "",
    filters: {},
    menuKey: "",
    renamingKey: "",
    projects: [],
    characters: [],
    selectedId: "",
  };
  const previewRequests = new Map();

  const setStatus = (message) => {
    status.textContent = message || "";
  };

  const finish = () => {
    overlay.remove();
    options.onClose?.();
  };

  const visibleItems = () => sortedLibraryItems(
    filterLibraryItems(
      state.tab === TAB_PROJECTS ? state.projects : state.characters,
      state.search,
      state.tag,
      state.filters,
      state.tab,
      timeline,
    ),
    state.sort,
  );

  const selectedItem = () => {
    const items = visibleItems();
    return items.find((item) => item.id === state.selectedId) ?? items[0] ?? null;
  };

  const mergeProjectPreview = (itemId, payload) => {
    const index = state.projects.findIndex((item) => item.id === itemId);
    if (index < 0) return;
    state.projects[index] = applyProjectPreviewPayload(state.projects[index], payload);
  };

  const mergeCharacterPreview = (itemId, payload) => {
    const index = state.characters.findIndex((item) => item.id === itemId);
    if (index < 0) return;
    state.characters[index] = applyCharacterPreviewPayload(state.characters[index], payload);
  };

  const requestPrivateProjectPreview = (item) => {
    if (!shouldRequestPrivateProjectPreview(item, privacyMode) || previewRequests.has(item.id)) return null;
    const request = fetchProjectPreview(item)
      .then((payload) => {
        mergeProjectPreview(item.id, payload);
        render();
      })
      .catch((error) => {
        mergeProjectPreview(item.id, { preview_assets: [], error });
        console.warn("Helto Director private project preview failed", error);
      })
      .finally(() => {
        previewRequests.delete(item.id);
      });
    previewRequests.set(item.id, request);
    return request;
  };

  const requestPrivateCharacterPreview = (item) => {
    if (!shouldRequestPrivateCharacterPreview(item, privacyMode) || previewRequests.has(item.id)) return null;
    const request = fetchCharacterPreview(item)
      .then((payload) => {
        mergeCharacterPreview(item.id, payload);
        render();
      })
      .catch((error) => {
        mergeCharacterPreview(item.id, { character: null, error });
        console.warn("Helto Director private character preview failed", error);
      })
      .finally(() => {
        previewRequests.delete(item.id);
      });
    previewRequests.set(item.id, request);
    return request;
  };

  const requestPrivatePreview = (item) => {
    requestPrivateProjectPreview(item);
    requestPrivateCharacterPreview(item);
  };

  const render = () => {
    const currentSelected = selectedItem();
    requestPrivatePreview(currentSelected);
    const linkedId = linkedProjectLibraryItemId(timeline);
    const saveTitle = state.tab === TAB_CHARACTERS
      ? "Add Current Character References to Library"
      : linkedId
        ? "Update Project"
        : "Save Project";
    saveButton.replaceChildren(iconSvg(documentRef, state.tab === TAB_CHARACTERS ? "character-plus" : linkedId ? "save" : "plus"));
    saveButton.title = saveTitle;
    saveButton.setAttribute("aria-label", saveButton.title);
    projectsTab.classList.toggle("is-active", state.tab === TAB_PROJECTS);
    charactersTab.classList.toggle("is-active", state.tab === TAB_CHARACTERS);
    projectsTab.setAttribute("aria-selected", state.tab === TAB_PROJECTS ? "true" : "false");
    charactersTab.setAttribute("aria-selected", state.tab === TAB_CHARACTERS ? "true" : "false");
    search.placeholder = state.tab === TAB_CHARACTERS ? "Search characters..." : "Search projects...";
    search.value = state.search;
    sort.value = state.sort;
    renderSidebar(documentRef, sidebar, {
      tab: state.tab,
      items: state.tab === TAB_PROJECTS ? state.projects : state.characters,
      tags: visibleTags(state.tab === TAB_PROJECTS ? state.projects : state.characters),
      activeTag: state.tag,
      filters: state.filters,
      timeline,
      onSelectTag: (tag) => {
        state.tag = tag;
        state.selectedId = "";
        render();
      },
      onToggleFilter: (key) => {
        state.filters = { ...state.filters, [key]: !state.filters[key] };
        state.selectedId = "";
        render();
      },
    });
    renderGrid(documentRef, grid, {
      items: visibleItems(),
      selected: currentSelected,
      tab: state.tab,
      timeline,
      privacyMode,
      select: (item) => {
        state.selectedId = item.id;
        requestPrivateProjectPreview(item);
        render();
      },
      reveal: requestPrivatePreview,
      context: {
        timeline,
        documentRef,
        setStatus,
        callbacks: options,
        close: finish,
        refresh: refreshLibrary,
        state,
        render,
      },
    });
    renderDetails(documentRef, details, currentSelected, state.tab, timeline, privacyMode, {
      timeline,
      documentRef,
      setStatus,
      callbacks: options,
      close: finish,
      refresh: refreshLibrary,
      state,
      render,
    });
    renderActions(documentRef, actions, {
      item: currentSelected,
      tab: state.tab,
      timeline,
      documentRef,
      setStatus,
      callbacks: options,
      close: finish,
      refresh: refreshLibrary,
      state,
      render,
    });
  };

  projectsTab.addEventListener("click", () => {
    state.tab = TAB_PROJECTS;
    state.tag = "";
    state.filters = {};
    state.selectedId = "";
    render();
  });
  charactersTab.addEventListener("click", () => {
    state.tab = TAB_CHARACTERS;
    state.tag = "";
    state.filters = {};
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
    state.projects = data.projects.map(normalizeLibraryProjectItem).filter(Boolean);
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
  for (const element of root.querySelectorAll?.(".htd-library-preview img, .htd-library-strip-thumb img, .htd-library-description, .htd-library-detail-description") ?? []) {
    if ("src" in element && element.tagName?.toLowerCase?.() === "img") element.removeAttribute("src");
    element.textContent = "";
  }
  return true;
}

export function normalizeLibraryProjectItem(item) {
  const hasSnapshot = Boolean(item?.project ?? item?.snapshot ?? item?.video_timeline ?? item?.payload);
  const snapshot = hasSnapshot ? normalizeVideoTimeline(item?.project ?? item?.snapshot ?? item?.video_timeline ?? item?.payload) : null;
  const summary = item?.summary && typeof item.summary === "object" && !Array.isArray(item.summary) ? item.summary : {};
  const isPrivate = Boolean(item?.is_private ?? item?.private);
  const previewAssets = isPrivate ? [] : normalizePreviewAssets(item?.preview_assets ?? item?.previewAssets);
  const previewAsset = snapshot ? firstTimelinePreviewAsset(snapshot) : previewAssets[0] ?? null;
  return {
    id: String(item?.id ?? item?.library_id ?? stableHash(JSON.stringify(snapshot))),
    kind: TAB_PROJECTS,
    title: String(item?.title ?? item?.name ?? projectName(snapshot)),
    description: String(item?.description ?? summaryText(summary)),
    tags: normalizeTags(item?.tags ?? snapshot?.project?.metadata?.tags),
    updatedAt: timestampValue(item?.updated_at ?? item?.mtime ?? item?.created_at),
    summary,
    isPrivate,
    timeline: snapshot,
    previewAssets,
    previewAsset,
    previewHydrated: Boolean(snapshot || previewAsset || previewAssets.length || !isPrivate),
    source: item,
  };
}

export function applyProjectPreviewPayload(item, payload) {
  const previewAssets = normalizePreviewAssets(payload?.preview_assets ?? payload?.previewAssets ?? payload?.item?.preview_assets ?? payload?.item?.previewAssets);
  const routeItem = payload?.item && typeof payload.item === "object" && !Array.isArray(payload.item) ? payload.item : null;
  return {
    ...item,
    title: routeItem?.name ? String(routeItem.name) : item?.title,
    previewAssets,
    previewAsset: previewAssets[0] ?? item?.previewAsset ?? null,
    previewHydrated: true,
    source: routeItem ? { ...(item?.source ?? {}), ...routeItem } : item?.source,
  };
}

export function shouldRequestPrivateProjectPreview(item, privacyMode) {
  return Boolean(
    privacyMode &&
    item?.kind === TAB_PROJECTS &&
    item?.isPrivate &&
    !item.previewHydrated,
  );
}

export function shouldRequestPrivateCharacterPreview(item, privacyMode) {
  return Boolean(
    privacyMode &&
    item?.kind === TAB_CHARACTERS &&
    item?.isPrivate &&
    !item.previewHydrated,
  );
}

export function normalizeLibraryCharacterItem(item) {
  const source = item?.character ?? item?.payload ?? item;
  const image = createReferenceImageFromLibraryItem(source) ?? null;
  const previewAsset = normalizeCharacterPreviewAsset(item, source, image);
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
    previewAsset,
    previewHydrated: Boolean(image?.path || previewAsset?.path || !Boolean(item?.is_private ?? item?.private)),
    source,
  };
}

export function applyCharacterPreviewPayload(item, payload) {
  const character = payload?.character ?? payload?.item?.character ?? payload?.item?.payload;
  const merged = normalizeLibraryCharacterItem({
    ...(item?.source ?? item ?? {}),
    ...(payload?.item && typeof payload.item === "object" && !Array.isArray(payload.item) ? payload.item : {}),
    character: character ?? item?.source,
    id: item?.id ?? payload?.item?.id,
    name: item?.title ?? payload?.item?.name,
    title: item?.title ?? payload?.item?.title,
    tags: item?.tags ?? payload?.item?.tags,
    is_private: item?.isPrivate ?? payload?.item?.is_private,
    summary: item?.summary ?? payload?.item?.summary,
  });
  return {
    ...item,
    ...merged,
    source: character ?? merged.source ?? item?.source,
    previewHydrated: true,
  };
}

function renderSidebar(documentRef, sidebar, context) {
  const { tab, items, tags, activeTag, filters, timeline, onSelectTag, onToggleFilter } = context;
  sidebar.replaceChildren();
  const filterTitle = el(documentRef, "div", "htd-library-sidebar-title");
  filterTitle.textContent = "Filters";
  sidebar.append(filterTitle);
  for (const filter of libraryFilters(tab, items, timeline)) {
    sidebar.append(toggleFilterButton(documentRef, filter, Boolean(filters[filter.key]), () => onToggleFilter(filter.key)));
  }
  const tagsHeader = el(documentRef, "div", "htd-library-sidebar-section");
  const tagsTitle = el(documentRef, "div", "htd-library-sidebar-title");
  tagsTitle.textContent = "Tags";
  tagsHeader.append(tagsTitle, iconSvg(documentRef, "chevron"));
  const all = tagFilterButton(documentRef, "All", items.length, activeTag === "", () => onSelectTag(""));
  sidebar.append(tagsHeader, all);
  for (const tag of tags) {
    sidebar.append(tagFilterButton(
      documentRef,
      tag,
      items.filter((item) => item.tags.includes(tag)).length,
      activeTag === tag,
      () => onSelectTag(tag),
    ));
  }
  const manage = textButton(documentRef, "Manage Tags", "Manage Tags", () => {});
  manage.classList.add("htd-library-manage-tags");
  manage.prepend(iconSvg(documentRef, "settings"));
  sidebar.append(manage);
}

function renderGrid(documentRef, grid, options) {
  const { items, selected, tab, timeline, privacyMode, select, reveal, context } = options;
  grid.replaceChildren();
  if (!items.length) {
    const empty = el(documentRef, "div", "htd-library-empty");
    empty.textContent = "No library items.";
    grid.append(empty);
    return;
  }
  for (const item of items) {
    const card = el(documentRef, "article", `htd-library-card htd-library-${tab}-card${selected?.id === item.id ? " is-selected" : ""}`);
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.title = item.title;
    card.setAttribute("aria-label", item.title);
    card.append(selectionBadge(documentRef, selected?.id === item.id));
    if (tab === TAB_PROJECTS) {
      card.append(renderProjectCardContent(documentRef, item, privacyMode, context));
    } else {
      card.append(renderCharacterCardContent(documentRef, item, timeline, privacyMode, context));
    }
    const revealPreview = () => reveal?.(item);
    card.addEventListener("pointerenter", revealPreview);
    card.addEventListener("focus", revealPreview);
    card.addEventListener("click", () => {
      revealPreview();
      select(item);
    });
    card.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      revealPreview();
      select(item);
    });
    grid.append(card);
  }
}

function renderDetails(documentRef, details, item, tab, timeline, privacyMode, context = {}) {
  details.replaceChildren();
  const title = el(documentRef, "div", "htd-library-details-title");
  title.textContent = tab === TAB_CHARACTERS ? "Character Preview" : "Preview";
  details.append(title);
  if (!item) {
    const empty = el(documentRef, "div", "htd-library-empty");
    empty.textContent = "Select a library item.";
    details.append(empty);
    return;
  }
  if (tab === TAB_CHARACTERS) {
    renderCharacterDetails(documentRef, details, item, timeline, privacyMode, context);
  } else {
    renderProjectDetails(documentRef, details, item, privacyMode);
  }
}

function renderProjectCardContent(documentRef, item, privacyMode, context) {
  const fragment = documentRef.createDocumentFragment();
  fragment.append(renderProjectMediaStrip(documentRef, item, privacyMode));
  const title = renderEditableLibraryTitle(documentRef, item, TAB_PROJECTS, context);
  const meta = el(documentRef, "div", "htd-library-card-meta-line");
  meta.textContent = timelineMetaLine(item);
  const counts = el(documentRef, "div", "htd-library-card-counts");
  counts.textContent = timelineCountsLine(item);
  const status = statusPill(documentRef, timelineStatus(item));
  const actionRow = el(documentRef, "div", "htd-library-card-actions");
  actionRow.append(
    quickIconButton(documentRef, "load", "Load Project", () => loadProjectLibraryItem(item, context), "primary"),
    quickIconButton(documentRef, "overwrite", "Update Project", async () => {
      await updateCurrentProjectLibraryItem({
        timeline: context.timeline,
        itemId: item.id,
        documentRef,
        setStatus: context.setStatus,
        privacyMode,
        callbacks: context.callbacks,
      });
      await context.refresh?.();
    }, "positive"),
    renderLibraryActionMenu(documentRef, `${TAB_PROJECTS}:${item.id}`, "More Project Actions", [
      menuAction("edit", "Rename", () => beginLibraryRename(context, TAB_PROJECTS, item)),
      menuAction("copy", "Duplicate Project", async () => {
        await duplicateLibraryItem(TAB_PROJECTS, item.id);
        context.setStatus?.("Duplicated project.");
        await context.refresh?.();
      }),
      menuAction("download", "Export Project JSON", async () => {
        const full = await fetchProjectForUse(item);
        exportJson(documentRef, `${safeFilename(item.title)}.json`, full.timeline);
        context.setStatus?.("Exported project JSON.");
      }),
      menuAction("delete", "Delete Project", async () => {
        if (!confirmDelete(documentRef, `Delete "${item.title}"?`)) return;
        await deleteLibraryItem(TAB_PROJECTS, item.id);
        context.setStatus?.("Deleted project.");
        await context.refresh?.();
      }, "danger"),
    ], context),
  );
  fragment.append(title, meta, counts, status, actionRow);
  return fragment;
}

function renderCharacterCardContent(documentRef, item, timeline, privacyMode, context) {
  const fragment = documentRef.createDocumentFragment();
  fragment.append(renderPreview(documentRef, item, privacyMode, "htd-library-card-preview"));
  const title = renderEditableLibraryTitle(documentRef, item, TAB_CHARACTERS, context);
  const description = el(documentRef, "div", "htd-library-description");
  description.textContent = item.description;
  const loaded = findLoadedCharacterReferenceForLibraryItem(timeline, item.source);
  const defaultRow = el(documentRef, "div", "htd-library-character-defaults");
  defaultRow.textContent = characterDefaultsLine(item);
  const actionRow = el(documentRef, "div", "htd-library-card-actions");
  actionRow.append(
    quickIconButton(documentRef, "insert", "Add Character to Timeline", async () => {
      const full = await fetchCharacterForUse(item);
      const reference = addCharacterFromLibrary(context.callbacks, full.character, item, false);
      if (reference) context.setStatus?.(`Added ${formatCharacterReferenceTag(reference)}.`);
    }, "primary"),
    quickIconButton(documentRef, "replace", "Replace Character Reference", async () => {
      const references = getCharacterReferences(context.timeline);
      if (!references.length) return context.setStatus?.("No loaded character reference to replace.");
      const full = await fetchCharacterForUse(item);
      const reference = replaceCharacterFromLibrary(context.callbacks, references[0].id, full.character, item);
      if (reference) context.setStatus?.(`Replaced ${formatCharacterReferenceTag(reference)}.`);
    }),
    renderLibraryActionMenu(documentRef, `${TAB_CHARACTERS}:${item.id}`, "More Character Actions", [
      menuAction("insert", "Add and Insert Reference Tag", async () => {
        const full = await fetchCharacterForUse(item);
        const reference = addCharacterFromLibrary(context.callbacks, full.character, item, true);
        if (reference) context.setStatus?.(`Inserted ${formatCharacterReferenceTag(reference)}.`);
      }),
      menuAction("copy", "Copy Reference Tag", async () => {
        const tag = tagForCharacterItem(timeline, item.source);
        await copyTextWithPromptFallback(documentRef, tag, "Character reference tag");
        context.setStatus?.(`Copied ${tag}.`);
      }),
      menuAction("edit", "Rename", () => beginLibraryRename(context, TAB_CHARACTERS, item)),
      menuAction("duplicate", "Duplicate Character", async () => {
        await duplicateLibraryItem(TAB_CHARACTERS, item.id);
        context.setStatus?.("Duplicated character.");
        await context.refresh?.();
      }),
      menuAction("download", "Export Character JSON", async () => {
        const full = await fetchCharacterForUse(item);
        exportJson(documentRef, `${safeFilename(item.title)}.json`, full.character);
        context.setStatus?.("Exported character JSON.");
      }),
      menuAction("delete", "Delete Character", async () => {
        if (!confirmDelete(documentRef, `Delete "${item.title}"?`)) return;
        await deleteLibraryItem(TAB_CHARACTERS, item.id);
        context.setStatus?.("Deleted character.");
        await context.refresh?.();
      }, "danger"),
    ], context),
  );
  fragment.append(title, description, renderTags(documentRef, item.tags), defaultRow);
  if (loaded) fragment.append(statusPill(documentRef, { label: "Loaded", tone: "loaded" }));
  fragment.append(actionRow);
  return fragment;
}

function renderProjectDetails(documentRef, details, item, privacyMode) {
  const name = el(documentRef, "div", "htd-library-detail-name");
  name.textContent = item.title;
  const meta = el(documentRef, "div", "htd-library-detail-meta");
  meta.textContent = timelineMetaLine(item);
  details.append(name, meta, renderPreview(documentRef, item, privacyMode, "htd-library-detail-preview"));
  details.append(renderTimelineSegmentBar(documentRef, item));
  details.append(renderInfoSection(documentRef, "Summary", summaryRows(item)));
  details.append(renderInfoSection(documentRef, "Sections", sectionRows(item)));
  details.append(renderInfoSection(documentRef, "Characters", characterRows(item)));
  details.append(renderInfoSection(documentRef, "Media Health", mediaHealthRows(item)));
}

function renderCharacterDetails(documentRef, details, item, timeline, privacyMode, context) {
  details.append(renderPreview(documentRef, item, privacyMode, "htd-library-detail-preview"));
  const existing = findLoadedCharacterReferenceForLibraryItem(timeline, item.source);
  if (existing) {
    const loaded = el(documentRef, "div", "htd-library-loaded");
    loaded.append(iconSvg(documentRef, "check"), documentRef.createTextNode(`Already loaded as ${formatCharacterReferenceTag(existing)}`));
    details.append(loaded);
  }
  const name = el(documentRef, "div", "htd-library-detail-name");
  name.textContent = item.title;
  const description = el(documentRef, "div", "htd-library-detail-description");
  description.textContent = item.description;
  details.append(name, description);
  details.append(renderInfoSection(documentRef, "Details", characterRowsForDetails(item, existing)));
  const stack = el(documentRef, "div", "htd-library-inspector-actions");
  stack.append(
    quickIconButton(documentRef, "insert", "Add Character to Timeline", async () => {
      const full = await fetchCharacterForUse(item);
      const reference = addCharacterFromLibrary(context.callbacks, full.character, item, false);
      if (reference) context.setStatus?.(`Added ${formatCharacterReferenceTag(reference)}.`);
    }, "primary"),
    quickIconButton(documentRef, "plus", "Add and Insert Reference Tag", async () => {
      const full = await fetchCharacterForUse(item);
      const reference = addCharacterFromLibrary(context.callbacks, full.character, item, true);
      if (reference) context.setStatus?.(`Inserted ${formatCharacterReferenceTag(reference)}.`);
    }, "positive"),
    quickIconButton(documentRef, "copy", "Copy Reference Tag", async () => {
      const tag = tagForCharacterItem(timeline, item.source);
      await copyTextWithPromptFallback(documentRef, tag, "Character reference tag");
      context.setStatus?.(`Copied ${tag}.`);
    }),
  );
  details.append(stack);
}

function renderProjectMediaStrip(documentRef, item, privacyMode) {
  const strip = el(documentRef, "div", "htd-library-media-strip");
  const assets = timelinePreviewAssets(item).slice(0, 3);
  if (!assets.length) {
    const empty = el(documentRef, "div", "htd-library-strip-empty");
    empty.append(iconSvg(documentRef, "timeline"));
    strip.append(empty);
    return strip;
  }
  for (const asset of assets) {
    strip.append(renderAssetThumb(documentRef, asset, item.title, privacyMode));
  }
  strip.append(renderTimelineSegmentBar(documentRef, item, true));
  return strip;
}

function renderAssetThumb(documentRef, asset, title, privacyMode) {
  const thumb = el(documentRef, "button", "htd-library-strip-thumb");
  thumb.type = "button";
  thumb.title = "Open Preview";
  if (asset?.path) {
    const img = documentRef.createElement("img");
    img.alt = title;
    img.src = thumbnailUrl(asset, 220, privacyMode);
    thumb.append(img);
    thumb.addEventListener("click", (event) => {
      event.stopPropagation();
      openLibraryMediaPreview(documentRef, asset, title);
    });
  }
  return thumb;
}

function renderTimelineSegmentBar(documentRef, item, compact = false) {
  const wrap = el(documentRef, "div", compact ? "htd-library-segment-bar is-compact" : "htd-library-segment-preview");
  const bar = el(documentRef, "div", "htd-library-segment-bar-track");
  const sections = timelineSections(item);
  if (!sections.length) {
    const segment = el(documentRef, "span", "htd-library-segment is-empty");
    segment.style.flexGrow = "1";
    bar.append(segment);
  } else {
    for (const section of sections.slice(0, 8)) {
      const segment = el(documentRef, "span", `htd-library-segment is-${String(section.type || "text").toLowerCase()}`);
      const duration = Math.max(0.1, Number(section.end_time) - Number(section.start_time));
      segment.style.flexGrow = String(Number.isFinite(duration) ? duration : 1);
      bar.append(segment);
    }
  }
  wrap.append(bar);
  if (!compact) {
    const time = el(documentRef, "div", "htd-library-segment-times");
    time.append(span(documentRef, "0s"), span(documentRef, formatSeconds(timelineDuration(item))));
    wrap.append(time);
  }
  return wrap;
}

function renderInfoSection(documentRef, title, rows) {
  const section = el(documentRef, "section", "htd-library-info-section");
  const header = el(documentRef, "div", "htd-library-info-title");
  header.textContent = title;
  section.append(header);
  const list = el(documentRef, "div", "htd-library-summary");
  for (const [label, value, tone] of rows) {
    if (value == null || value === "") continue;
    const row = el(documentRef, "div", `htd-library-summary-row${tone ? ` is-${tone}` : ""}`);
    const labelEl = el(documentRef, "span", "htd-library-summary-label");
    labelEl.textContent = label;
    const valueEl = el(documentRef, "span", "htd-library-summary-value");
    valueEl.textContent = String(value);
    row.append(labelEl, valueEl);
    list.append(row);
  }
  if (!list.children.length) {
    const empty = el(documentRef, "div", "htd-library-muted");
    empty.textContent = "No details.";
    list.append(empty);
  }
  section.append(list);
  return section;
}

function selectionBadge(documentRef, selected) {
  const badge = el(documentRef, "span", "htd-library-selected-badge");
  badge.append(iconSvg(documentRef, selected ? "check" : "blank"));
  return badge;
}

function statusPill(documentRef, status) {
  const pill = el(documentRef, "span", `htd-library-status-pill is-${status.tone}`);
  pill.textContent = status.label;
  return pill;
}

function renderEditableLibraryTitle(documentRef, item, tab, context) {
  const key = libraryUiKey(tab, item);
  if (context.state?.renamingKey !== key) {
    const title = el(documentRef, "div", "htd-library-card-title");
    title.textContent = item.title;
    title.title = "Double-click to rename";
    title.addEventListener("dblclick", (event) => {
      event.preventDefault();
      event.stopPropagation();
      beginLibraryRename(context, tab, item);
    });
    return title;
  }

  const editor = el(documentRef, "div", "htd-library-title-editor");
  const input = el(documentRef, "input", "htd-library-title-input");
  input.type = "text";
  input.value = item.title;
  input.setAttribute("aria-label", "Library item name");
  const confirm = iconButton(documentRef, "check", "Save Name", async (event) => {
    event.stopPropagation();
    const name = input.value.trim();
    if (!name || name === item.title) {
      endLibraryRename(context);
      return;
    }
    await patchLibraryItem(tab, item.id, { name });
    context.setStatus?.("Renamed library item.");
    endLibraryRename(context);
    await context.refresh?.();
  });
  confirm.classList.add("htd-library-title-confirm", "is-positive");
  const cancel = iconButton(documentRef, "cancel", "Cancel Rename", (event) => {
    event.stopPropagation();
    endLibraryRename(context);
  });
  cancel.classList.add("htd-library-title-cancel");
  input.addEventListener("click", (event) => event.stopPropagation());
  input.addEventListener("dblclick", (event) => event.stopPropagation());
  input.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Enter") {
      event.preventDefault();
      confirm.click();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancel.click();
    }
  });
  editor.addEventListener("click", (event) => event.stopPropagation());
  editor.append(input, confirm, cancel);
  setTimeout(() => input.focus?.(), 0);
  return editor;
}

function beginLibraryRename(context, tab, item) {
  if (!context.state) return;
  context.state.renamingKey = libraryUiKey(tab, item);
  context.state.menuKey = "";
  context.render?.();
}

function endLibraryRename(context) {
  if (!context.state) return;
  context.state.renamingKey = "";
  context.render?.();
}

function libraryUiKey(tab, item) {
  return `${tab}:${item?.id ?? ""}`;
}

function quickIconButton(documentRef, iconName, title, onClick, variant = "") {
  const control = iconButton(documentRef, iconName, title, async (event) => {
    event?.stopPropagation?.();
    await onClick?.(event);
  });
  control.classList.add("htd-library-quick-action");
  if (variant) control.classList.add(`is-${variant}`);
  control.addEventListener("keydown", (event) => event.stopPropagation?.());
  return control;
}

function menuAction(icon, label, run, tone = "") {
  return { icon, label, run, tone };
}

function renderLibraryActionMenu(documentRef, key, title, items, context) {
  const wrap = el(documentRef, "div", "htd-library-action-menu");
  const control = quickIconButton(documentRef, "more", title, (event) => {
    event?.stopPropagation?.();
    if (!context.state) return;
    context.state.menuKey = context.state.menuKey === key ? "" : key;
    context.render?.();
  }, "neutral");
  wrap.append(control);
  if (context.state?.menuKey === key) {
    const menu = el(documentRef, "div", "htd-library-menu");
    menu.setAttribute("role", "menu");
    for (const item of items) {
      const option = textButton(documentRef, "", item.label, async (event) => {
        event.stopPropagation();
        context.state.menuKey = "";
        context.render?.();
        await item.run?.();
      });
      option.classList.add("htd-library-menu-item");
      if (item.tone) option.classList.add(`is-${item.tone}`);
      option.setAttribute("role", "menuitem");
      option.append(iconSvg(documentRef, item.icon), span(documentRef, item.label));
      menu.append(option);
    }
    wrap.append(menu);
  }
  return wrap;
}

function toggleFilterButton(documentRef, filter, active, onClick) {
  const control = textButton(documentRef, "", filter.label, onClick);
  control.classList.add("htd-library-filter-toggle");
  control.classList.toggle("is-active", active);
  control.append(iconSvg(documentRef, filter.icon), span(documentRef, filter.label), toggleDot(documentRef), countBadge(documentRef, filter.count));
  return control;
}

function tagFilterButton(documentRef, label, count, active, onClick) {
  const control = textButton(documentRef, "", label, onClick);
  control.classList.add("htd-library-tag-filter");
  control.classList.toggle("is-active", active);
  control.append(span(documentRef, label), countBadge(documentRef, count));
  return control;
}

function countBadge(documentRef, value) {
  const badge = el(documentRef, "span", "htd-library-count");
  badge.textContent = String(value ?? 0);
  return badge;
}

function toggleDot(documentRef) {
  return el(documentRef, "span", "htd-library-toggle-dot");
}

function span(documentRef, text, className = "") {
  const element = el(documentRef, "span", className);
  element.textContent = text;
  return element;
}

function libraryFilters(tab, items, timeline) {
  const definitions = tab === TAB_PROJECTS
    ? [
        { key: "hasReferences", label: "Has References", icon: "image" },
        { key: "hasAudio", label: "Has Audio", icon: "music" },
        { key: "missingMedia", label: "Missing Media", icon: "warning" },
        { key: "private", label: "Private", icon: "lock" },
      ]
    : [
        { key: "loaded", label: "Loaded in Timeline", icon: "timeline" },
        { key: "missingFile", label: "Missing File", icon: "warning" },
        { key: "hasTags", label: "Has Tags", icon: "tag" },
        { key: "private", label: "Private", icon: "lock" },
      ];
  return definitions.map((filter) => ({
    ...filter,
    count: items.filter((item) => filterMatches(item, filter.key, tab, timeline)).length,
  }));
}

function filterMatches(item, key, tab, timeline) {
  if (tab === TAB_PROJECTS) {
    if (key === "hasReferences") return timelineReferenceCount(item) > 0;
    if (key === "hasAudio") return timelineAudioCount(item) > 0;
    if (key === "missingMedia") return timelineHasMissingMedia(item);
    if (key === "private") return item.isPrivate;
    return true;
  }
  if (key === "loaded") return Boolean(findLoadedCharacterReferenceForLibraryItem(timeline, item.source));
  if (key === "missingFile") return !item.image?.path || item.summary?.has_image === false;
  if (key === "hasTags") return item.tags.length > 0;
  if (key === "private") return item.isPrivate;
  return true;
}

function timelinePreviewAssets(item) {
  const assets = timelineSectionPreviewAssets(item.timeline);
  if (assets.length) return assets;
  if (Array.isArray(item.previewAssets) && item.previewAssets.length) return item.previewAssets;
  return item.previewAsset ? [item.previewAsset] : [];
}

function timelineSections(item) {
  return Array.isArray(item.timeline?.director_track?.sections) ? item.timeline.director_track.sections : [];
}

function timelineDuration(item) {
  return item.summary?.duration_seconds ?? item.timeline?.project?.duration_seconds ?? 0;
}

function timelineFrameRate(item) {
  return item.summary?.frame_rate ?? item.timeline?.project?.frame_rate ?? null;
}

function timelineAspect(item) {
  return item.summary?.aspect_ratio ?? item.timeline?.project?.aspect_ratio ?? "";
}

function timelineSectionCount(item) {
  return item.summary?.section_count ?? timelineSections(item).length;
}

function timelineReferenceCount(item) {
  return item.summary?.character_reference_count ?? item.summary?.character_count ?? getCharacterReferences(item.timeline).length;
}

function timelineAudioCount(item) {
  return item.summary?.audio_count ?? item.summary?.audio_track_count ?? item.timeline?.audio_tracks?.length ?? 0;
}

function timelineHasMissingMedia(item) {
  if (Number(item.summary?.missing_media_count) > 0 || item.summary?.has_missing_media === true) return true;
  return (item.timeline?.assets ?? []).some((asset) => asset.missing || asset.status === "missing" || asset.exists === false);
}

function timelineStatus(item) {
  if (timelineHasMissingMedia(item)) return { label: "Missing Media", tone: "warning" };
  if (item.isPrivate) return { label: "Private", tone: "private" };
  return { label: "OK", tone: "ok" };
}

function timelineMetaLine(item) {
  return [formatSeconds(timelineDuration(item)), timelineFrameRate(item) ? `${formatNumber(timelineFrameRate(item))} fps` : "", timelineAspect(item)]
    .filter(Boolean)
    .join(" · ");
}

function timelineCountsLine(item) {
  return [
    `${timelineSectionCount(item)} sections`,
    `${timelineReferenceCount(item)} refs`,
    `${timelineAudioCount(item)} audio`,
  ].join(" · ");
}

function characterDefaultsLine(item) {
  const strength = item.summary?.strength ?? item.source?.strength;
  const age = ageText(item.updatedAt);
  return [`Default: ${formatNumber(strength) || "0.90"}`, age].filter(Boolean).join(" · ");
}

function summaryRows(item) {
  return [
    ["Duration", formatSeconds(timelineDuration(item))],
    ["Frame Rate", timelineFrameRate(item) ? `${formatNumber(timelineFrameRate(item))} fps` : ""],
    ["Aspect", timelineAspect(item)],
    ["Status", timelineStatus(item).label, timelineStatus(item).tone],
  ];
}

function sectionRows(item) {
  const sections = timelineSections(item);
  if (!sections.length) return [["Sections", timelineSectionCount(item)]];
  return sections.slice(0, 4).map((section, index) => [
    section.type || `Section ${index + 1}`,
    `${formatSeconds(section.start_time)}-${formatSeconds(section.end_time)}${section.prompt ? `: ${truncateText(section.prompt, 42)}` : ""}`,
  ]);
}

function characterRows(item) {
  const references = getCharacterReferences(item.timeline);
  if (!references.length) return [["References", timelineReferenceCount(item)]];
  return references.slice(0, 4).map((reference) => [
    formatCharacterReferenceTag(reference),
    reference.description || reference.image?.name || "Character reference",
  ]);
}

function mediaHealthRows(item) {
  const assets = item.timeline?.assets ?? [];
  if (!assets.length) return [["Media", timelineHasMissingMedia(item) ? "Missing" : "OK", timelineStatus(item).tone]];
  return assets.slice(0, 5).map((asset) => [
    asset.name || asset.path || "Media",
    asset.missing || asset.status === "missing" || asset.exists === false ? "Missing" : "OK",
    asset.missing || asset.status === "missing" || asset.exists === false ? "warning" : "ok",
  ]);
}

function timelineSectionPreviewAssets(timeline) {
  if (!timeline) return [];
  const assetsById = new Map((timeline.assets ?? []).map((asset) => [String(asset?.asset_id ?? ""), asset]));
  const previews = [];
  for (const section of timeline.director_track?.sections ?? []) {
    if (section?.type !== ASSET_TYPE_IMAGE && section?.type !== ASSET_TYPE_VIDEO) continue;
    const sectionType = section.type;
    const reference = sectionType === ASSET_TYPE_VIDEO ? section.video : section.image;
    const preview = timelinePreviewAssetFromReference(reference, assetsById, sectionType);
    if (preview) previews.push(preview);
  }
  return previews;
}

function timelinePreviewAssetFromReference(reference, assetsById, fallbackType = ASSET_TYPE_IMAGE) {
  if (!reference) return null;
  if (typeof reference === "string") {
    return normalizePreviewAsset({ type: fallbackType, path: reference });
  }
  if (reference.asset_id) {
    const asset = assetsById.get(String(reference.asset_id));
    if (asset?.type === ASSET_TYPE_IMAGE || asset?.type === ASSET_TYPE_VIDEO) {
      return normalizePreviewAsset(asset);
    }
  }
  if (reference.path || reference.file_path) {
    return normalizePreviewAsset({
      ...reference,
      type: reference.type === ASSET_TYPE_VIDEO ? ASSET_TYPE_VIDEO : fallbackType,
      path: reference.path ?? reference.file_path,
    });
  }
  return null;
}

function characterRowsForDetails(item, existing) {
  return [
    ["Default Tag", item.summary?.label || (existing ? formatCharacterReferenceTag(existing) : "@image1:character")],
    ["Default Strength", formatNumber(item.summary?.strength ?? item.source?.strength) || "0.90"],
    ["Usage", existing ? `Loaded as ${formatCharacterReferenceTag(existing)}` : "Not loaded in current timeline"],
    ["Tags", item.tags.join(", ")],
  ];
}

function ageText(timestamp) {
  const then = Number(timestamp);
  if (!Number.isFinite(then) || then <= 0) return "";
  const seconds = Math.max(1, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function truncateText(value, length) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > length ? `${text.slice(0, Math.max(0, length - 1))}...` : text;
}

function renderActions(documentRef, actions, context) {
  actions.replaceChildren();
}

async function loadProjectLibraryItem(item, context) {
  const confirmFn = context.documentRef.defaultView?.confirm ?? globalThis.confirm;
  if (confirmFn && !confirmFn(PROJECT_REPLACE_CONFIRMATION)) return;
  const full = await fetchProjectForUse(item);
  replaceProjectFromLibrary(context.callbacks, stampProjectLibraryItemId(deepClone(full.timeline), item.id), full);
  context.close?.();
}

function replaceProjectFromLibrary(options, nextTimeline, item) {
  if (typeof options.onReplaceTimeline === "function") {
    return options.onReplaceTimeline(nextTimeline, item);
  }
  return options.controller?.replaceTimelineFromLibrary?.(nextTimeline, "replace project from library") ?? null;
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
  const asset = libraryPreviewAssetForItem(item);
  if (asset?.path) {
    const img = documentRef.createElement("img");
    img.alt = item.title;
    img.src = thumbnailUrl(asset, 320, privacyMode);
    preview.append(img);
    preview.addEventListener("click", (event) => {
      event.stopPropagation();
      openLibraryMediaPreview(documentRef, asset, item.title);
    });
  } else {
    preview.append(iconSvg(documentRef, item.kind === TAB_PROJECTS ? "timeline" : "character"));
  }
  return preview;
}

export function libraryPreviewAssetForItem(item) {
  if (!item) return null;
  return item.kind === TAB_CHARACTERS ? item.previewAsset ?? item.image ?? null : item.previewAsset ?? null;
}

export function linkedProjectLibraryItemId(timeline) {
  return String(timeline?.project?.metadata?.[PROJECT_LIBRARY_ITEM_ID_KEY] ?? "").trim();
}

export function stampProjectLibraryItemId(timeline, itemId) {
  if (!timeline || typeof timeline !== "object") return timeline;
  timeline.project ??= {};
  timeline.project.metadata = timeline.project.metadata && typeof timeline.project.metadata === "object" && !Array.isArray(timeline.project.metadata)
    ? timeline.project.metadata
    : {};
  const id = String(itemId ?? "").trim();
  if (id) {
    timeline.project.metadata[PROJECT_LIBRARY_ITEM_ID_KEY] = id;
  } else {
    delete timeline.project.metadata[PROJECT_LIBRARY_ITEM_ID_KEY];
  }
  return timeline;
}

export function clearProjectLibraryItemId(timeline) {
  return stampProjectLibraryItemId(timeline, "");
}

export function stampProjectName(timeline, name) {
  if (!timeline || typeof timeline !== "object") return timeline;
  timeline.project ??= {};
  timeline.project.identity = timeline.project.identity && typeof timeline.project.identity === "object" && !Array.isArray(timeline.project.identity)
    ? timeline.project.identity
    : {};
  timeline.project.identity.name = normalizedProjectName(name);
  return timeline;
}

export function cloneProjectForDirectorLibrary(timeline, itemId = "", name = null, options = {}) {
  const clone = deepClone(timeline ?? {});
  pruneUnreferencedTimelineAssets(clone);
  if (options.forkProjectIdentity) forkProjectIdentity(clone, name);
  if (name != null) stampProjectName(clone, name);
  return stampProjectLibraryItemId(clone, itemId);
}

export function forkProjectIdentity(timeline, name = null) {
  if (!timeline || typeof timeline !== "object") return timeline;
  timeline.project ??= {};
  const currentStorage = timeline.project.storage && typeof timeline.project.storage === "object" && !Array.isArray(timeline.project.storage)
    ? timeline.project.storage
    : {};
  const identity = createDefaultProjectIdentity();
  identity.name = normalizedProjectName(name ?? timeline.project.identity?.name);
  timeline.project.identity = identity;
  timeline.project.storage = {
    ...currentStorage,
    schema_version: PROJECT_STORAGE_SCHEMA_VERSION,
    project_directory_name: projectDirectoryName(identity.name, identity.project_id),
  };
  return timeline;
}

function normalizedProjectName(name) {
  return String(name ?? "").trim() || "Untitled Project";
}

function projectName(timeline) {
  return normalizedProjectName(timeline?.project?.identity?.name ?? timeline?.project?.metadata?.title ?? timeline?.project?.metadata?.name);
}

function promptForProjectName(documentRef, timeline) {
  const promptFn = documentRef?.defaultView?.prompt ?? globalThis.prompt;
  if (typeof promptFn !== "function") return projectName(timeline);
  const value = promptFn("Project name", projectName(timeline));
  if (value == null) return "";
  return normalizedProjectName(value);
}

function pruneUnreferencedTimelineAssets(timeline) {
  if (!Array.isArray(timeline?.assets)) return timeline;
  const referencedAssetIds = collectTimelineAssetReferences(timeline);
  timeline.assets = timeline.assets.filter((asset) => referencedAssetIds.has(String(asset?.asset_id ?? "")));
  return timeline;
}

function collectTimelineAssetReferences(timeline) {
  const ids = new Set();
  const addAssetId = (reference) => {
    const assetId = String(reference?.asset_id ?? "").trim();
    if (assetId) ids.add(assetId);
  };
  for (const section of timeline?.director_track?.sections ?? []) {
    if (section?.type === ASSET_TYPE_IMAGE) addAssetId(section.image);
    if (section?.type === ASSET_TYPE_VIDEO) addAssetId(section.video);
  }
  for (const track of timeline?.audio_tracks ?? []) {
    for (const clip of track?.clips ?? []) addAssetId(clip?.audio);
  }
  for (const reference of getCharacterReferences(timeline)) {
    addAssetId(reference?.image);
  }
  const shots = timeline?.sequence?.shots;
  for (const shot of Array.isArray(shots) ? shots : []) {
    if (!shot || typeof shot !== "object") continue;
    addAssetId(shot.clip_instance);
    const takes = shot.takes;
    for (const take of Array.isArray(takes) ? takes : []) {
      if (!take || typeof take !== "object") continue;
      addAssetId(take);
      addAssetId(take.clip_instance);
    }
  }
  return ids;
}

function stampCurrentProjectLibraryItemId(callbacks, timeline, itemId, name = null) {
  if (callbacks?.controller?.updateTimeline) {
    callbacks.controller.updateTimeline((current) => {
      if (name != null) stampProjectName(current, name);
      stampProjectLibraryItemId(current, itemId);
    }, "link library project", { rerender: false });
    return;
  }
  if (name != null) stampProjectName(timeline, name);
  stampProjectLibraryItemId(timeline, itemId);
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

async function saveCurrentProjectAsNew({ timeline, documentRef, setStatus, privacyMode, saveProject, callbacks, forkProject = false }) {
  if (!timeline) return;
  try {
    const name = promptForProjectName(documentRef, timeline);
    if (!name) {
      setStatus("Project save canceled.");
      return;
    }
    if (typeof saveProject === "function") {
      await saveProject(cloneProjectForDirectorLibrary(timeline, "", name, { forkProjectIdentity: Boolean(forkProject) }));
    } else {
      const projectPayload = cloneProjectForDirectorLibrary(timeline, "", name, { forkProjectIdentity: Boolean(forkProject) });
      const data = await fetchLibraryJson(`${ROUTE_PREFIX}/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          private: Boolean(privacyMode),
          project: projectPayload,
        }),
      });
      const itemId = data?.item?.id;
      if (itemId) {
        stampCurrentProjectLibraryItemId(callbacks, timeline, itemId, name);
      }
    }
    setStatus("Saved project.");
  } catch (error) {
    const alertFn = documentRef.defaultView?.alert ?? globalThis.alert;
    alertFn?.(error.message);
    setStatus(error.message || "Could not save project.");
  }
}

async function updateCurrentProjectLibraryItem({ timeline, itemId, documentRef, setStatus, privacyMode = false, callbacks }) {
  if (!timeline || !itemId) return;
  if (!confirmProjectUpdate(documentRef)) {
    setStatus?.("Project update canceled.");
    return;
  }
  try {
    await updateProjectLibraryItem(itemId, timeline, privacyMode);
    stampCurrentProjectLibraryItemId(callbacks, timeline, itemId);
    setStatus?.("Updated project.");
  } catch (error) {
    const alertFn = documentRef.defaultView?.alert ?? globalThis.alert;
    alertFn?.(error.message);
    setStatus?.(error.message || "Could not update project.");
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

function showProjectSaveChoicePopup(documentRef, anchor, actions) {
  documentRef.querySelector?.(".htd-library-save-popup")?.remove();
  const popup = el(documentRef, "div", "htd-library-save-popup");
  popup.setAttribute("role", "menu");
  const update = textButton(documentRef, "", "Update Current Project", async (event) => {
    event.stopPropagation();
    popup.remove();
    await actions.update?.();
  });
  update.classList.add("htd-library-menu-item", "is-positive");
  update.setAttribute("role", "menuitem");
  update.append(iconSvg(documentRef, "save"), span(documentRef, "Update Current Project"));
  const saveAsNew = textButton(documentRef, "", "Save Project As New", async (event) => {
    event.stopPropagation();
    popup.remove();
    await actions.saveAsNew?.();
  });
  saveAsNew.classList.add("htd-library-menu-item");
  saveAsNew.setAttribute("role", "menuitem");
  saveAsNew.append(iconSvg(documentRef, "plus"), span(documentRef, "Save Project As New"));
  const cancel = textButton(documentRef, "", "Cancel", (event) => {
    event.stopPropagation();
    popup.remove();
  });
  cancel.classList.add("htd-library-menu-item");
  cancel.setAttribute("role", "menuitem");
  cancel.append(iconSvg(documentRef, "cancel"), span(documentRef, "Cancel"));
  popup.append(update, saveAsNew, cancel);
  anchor.closest?.(".htd-library-controls")?.append(popup);
}

async function fetchLibraryItems() {
  const data = await fetchLibraryJson(`${ROUTE_PREFIX}/items`);
  return {
    projects: Array.isArray(data.projects) ? data.projects : [],
    characters: Array.isArray(data.characters) ? data.characters : [],
  };
}

async function fetchProjectForUse(item) {
  if (item?.timeline) return item;
  const data = await fetchLibraryJson(`${ROUTE_PREFIX}/projects/${encodeURIComponent(item.id)}/use`, { method: "POST" });
  const timeline = data.project ?? data.item?.project ?? data.item?.payload;
  return {
    ...item,
    timeline: normalizeVideoTimeline(timeline),
  };
}

async function fetchProjectPreview(item) {
  return fetchLibraryJson(`${ROUTE_PREFIX}/projects/${encodeURIComponent(item.id)}/preview`, { method: "POST" });
}

async function fetchCharacterPreview(item) {
  return fetchLibraryJson(`${ROUTE_PREFIX}/characters/${encodeURIComponent(item.id)}/preview`, { method: "POST" });
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

async function updateProjectLibraryItem(itemId, timeline, privacyMode = false) {
  const timelinePayload = cloneProjectForDirectorLibrary(timeline, itemId);
  return fetchLibraryJson(`${ROUTE_PREFIX}/projects/${encodeURIComponent(itemId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: projectName(timelinePayload),
      private: Boolean(privacyMode),
      project: timelinePayload,
    }),
  });
}

async function duplicateLibraryItem(tab, itemId) {
  const route = tab === TAB_CHARACTERS ? "characters" : "projects";
  return fetchLibraryJson(`${ROUTE_PREFIX}/${route}/${encodeURIComponent(itemId)}/duplicate`, { method: "POST" });
}

async function patchLibraryItem(tab, itemId, metadata) {
  const route = tab === TAB_CHARACTERS ? "characters" : "projects";
  return fetchLibraryJson(`${ROUTE_PREFIX}/${route}/${encodeURIComponent(itemId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(metadata),
  });
}

async function deleteLibraryItem(tab, itemId) {
  const route = tab === TAB_CHARACTERS ? "characters" : "projects";
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

function filterLibraryItems(items, query, tag, filters = {}, tab = TAB_PROJECTS, timeline = null) {
  const needle = String(query ?? "").trim().toLowerCase();
  return items.filter((item) => {
    const matchesTag = !tag || item.tags.includes(tag);
    if (!matchesTag) return false;
    for (const [key, active] of Object.entries(filters)) {
      if (active && !filterMatches(item, key, tab, timeline)) return false;
    }
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
  return timelineSectionPreviewAssets(timeline)[0] ?? null;
}

function normalizePreviewAssets(value) {
  if (!Array.isArray(value)) return [];
  return value
    .map((asset) => normalizePreviewAsset(asset))
    .filter(Boolean)
    .slice(0, 3);
}

function normalizePreviewAsset(asset) {
  if (!asset || typeof asset !== "object" || Array.isArray(asset)) return null;
  if (asset.type !== ASSET_TYPE_IMAGE && asset.type !== ASSET_TYPE_VIDEO) return null;
  const path = safePreviewText(asset.path ?? asset.file_path);
  if (!path) return null;
  const normalized = {};
  for (const key of [
    "asset_id",
    "duration_seconds",
    "file_path",
    "frame_rate",
    "height",
    "id",
    "media_type",
    "mime_type",
    "name",
    "path",
    "size_bytes",
    "source_kind",
    "source_type",
    "type",
    "width",
  ]) {
    const value = asset[key];
    if (typeof value === "string") {
      const text = safePreviewText(value);
      if (text) normalized[key] = text;
    } else if (value == null || typeof value === "boolean" || typeof value === "number") {
      normalized[key] = value;
    }
  }
  normalized.type = asset.type;
  normalized.path = path;
  return normalized;
}

function normalizeCharacterPreviewAsset(item, source, image) {
  const asset = normalizePreviewAsset({
    ...source,
    ...(source?.image && typeof source.image === "object" && !Array.isArray(source.image) ? source.image : {}),
    ...image,
    source_kind: image?.source_kind ?? source?.source_kind ?? source?.image?.source_kind,
    source_type: image?.source_type ?? image?.metadata?.source_type ?? image?.metadata?.browser_alias ?? source?.source_type ?? source?.image?.source_type ?? source?.metadata?.source_type ?? source?.metadata?.browser_alias ?? item?.source_type,
    type: ASSET_TYPE_IMAGE,
  });
  return asset;
}

function safePreviewText(value) {
  const text = String(value ?? "").trim();
  if (!text || text.startsWith("data:") || text.startsWith("blob:")) return "";
  if (text.length >= 256 && /^[A-Za-z0-9+/]+={0,2}$/.test(text)) return "";
  return text;
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

function confirmDelete(documentRef, message) {
  const confirmFn = documentRef.defaultView?.confirm ?? globalThis.confirm;
  return !confirmFn || confirmFn(message);
}

export function confirmProjectUpdate(documentRef) {
  const confirmFn = documentRef.defaultView?.confirm ?? globalThis.confirm;
  return !confirmFn || confirmFn(PROJECT_UPDATE_CONFIRMATION);
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

function openLibraryMediaPreview(documentRef, asset, caption) {
  const url = mediaViewUrl(asset);
  if (!url) return null;
  return showMediaPreview(documentRef, {
    type: asset?.type,
    url,
    caption: caption || asset?.name || asset?.path,
  });
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
  cancel: `<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>`,
  search: `<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m16 16 4 4"/></svg>`,
  plus: `<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>`,
  "character-plus": `<svg viewBox="0 0 24 24"><path d="M7 19a5 5 0 0 1 10 0"/><circle cx="12" cy="9" r="3"/><path d="M19 6v6M16 9h6"/></svg>`,
  chevron: `<svg viewBox="0 0 24 24"><path d="m8 10 4 4 4-4"/></svg>`,
  settings: `<svg viewBox="0 0 24 24"><path d="M12 8v8M8 12h8"/><circle cx="12" cy="12" r="9"/></svg>`,
  image: `<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m7 17 4-4 3 3 2-2 3 3"/></svg>`,
  music: `<svg viewBox="0 0 24 24"><path d="M9 18V5l10-2v13"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="16" r="2"/></svg>`,
  warning: `<svg viewBox="0 0 24 24"><path d="m12 4 9 16H3L12 4Z"/><path d="M12 9v4M12 17h.01"/></svg>`,
  lock: `<svg viewBox="0 0 24 24"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>`,
  tag: `<svg viewBox="0 0 24 24"><path d="M20 13 13 20 4 11V4h7l9 9Z"/><circle cx="8.5" cy="8.5" r="1.5"/></svg>`,
  check: `<svg viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>`,
  save: `<svg viewBox="0 0 24 24"><path d="M5 4h12l2 2v14H5z"/><path d="M8 4v6h8V4M8 20v-6h8v6"/></svg>`,
  load: `<svg viewBox="0 0 24 24"><path d="M5 4h14v6H5z"/><path d="M12 20V9M7 15l5 5 5-5"/></svg>`,
  overwrite: `<svg viewBox="0 0 24 24"><path d="M5 4h12l2 2v14H5z"/><path d="M8 4v6h8V4"/><path d="m8 16 3 3 5-6"/></svg>`,
  more: `<svg viewBox="0 0 24 24"><circle cx="6" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18" cy="12" r="1.5"/></svg>`,
  edit: `<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/></svg>`,
  duplicate: `<svg viewBox="0 0 24 24"><rect x="8" y="8" width="10" height="10" rx="2"/><path d="M6 14H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v1"/></svg>`,
  copy: `<svg viewBox="0 0 24 24"><rect x="8" y="8" width="10" height="10" rx="2"/><path d="M6 14H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v1"/></svg>`,
  insert: `<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/><path d="M4 5h5M4 19h5M15 5h5M15 19h5"/></svg>`,
  replace: `<svg viewBox="0 0 24 24"><path d="M7 7h10l-3-3M17 17H7l3 3"/><path d="M17 7a5 5 0 0 1 0 10M7 17a5 5 0 0 1 0-10"/></svg>`,
  delete: `<svg viewBox="0 0 24 24"><path d="M6 7h12M10 7V5h4v2M9 10v7M15 10v7M8 7l1 12h6l1-12"/></svg>`,
  download: `<svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>`,
  blank: `<svg viewBox="0 0 24 24"></svg>`,
};

function installDirectorLibraryStyles(documentRef) {
  if (!documentRef || documentRef.getElementById("helto-director-library-style")) return;
  const style = documentRef.createElement("style");
  style.id = "helto-director-library-style";
  style.textContent = `
    /* Helto design tokens (keep in sync with helto-designsystem/reference/tokens.css). Scoped to the library overlay appended to <body>. */
    .htd-library-dialog {
      --htd-bg:#0d1320; --htd-surface:#151c2a; --htd-surface-2:#1b2333; --htd-surface-3:#232d3f; --htd-surface-hover:#2c3850;
      --htd-border:#2a3346; --htd-border-strong:#3a465c; --htd-border-hover:#4c5970; --htd-text:#e7ebf3; --htd-text-dim:#9aa6bd; --htd-text-faint:#6f7c95;
      --htd-accent:#f1c75c; --htd-accent-strong:#ffd873; --htd-accent-bg:rgba(241,199,92,0.16); --htd-accent-border:rgba(241,199,92,0.55);
      --htd-focus:#5e9bff; --htd-ring:0 0 0 2px rgba(94,155,255,0.5); --htd-danger:#ec5a6b; --htd-danger-border:#8f3a44;
      --htd-radius:6px; --htd-radius-sm:5px; --htd-radius-lg:10px; --htd-shadow:0 1px 2px rgba(0,0,0,0.35); --htd-shadow-pop:0 14px 36px rgba(0,0,0,0.55); --htd-shadow-glow:0 0 10px rgba(241,199,92,0.35); }
    .htd-library-dialog { position: fixed; inset: 0; z-index: 10020; display: flex; align-items: center; justify-content: center; padding: 28px; box-sizing: border-box; background: rgba(6,9,15,0.72); backdrop-filter: blur(4px); color: var(--htd-text-dim); font: 13px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; }
    .htd-library-panel { width: min(1120px, calc(100vw - 72px)); height: min(760px, calc(100vh - 72px)); min-height: 560px; display: grid; grid-template-rows: auto auto auto minmax(0, 1fr) auto auto; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-lg); background: linear-gradient(135deg, rgba(27,35,51,0.92), rgba(13,19,32,0.96)); box-shadow: var(--htd-shadow-pop), inset 0 1px rgba(255,255,255,0.04); overflow: hidden; }
    .htd-library-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 22px 24px 8px; }
    .htd-library-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 22px; font-weight: 700; color: var(--htd-text); }
    .htd-library-controls { position: relative; min-width: 0; display: grid; grid-template-columns: minmax(220px, 360px) 184px auto 1fr; gap: 12px; align-items: center; padding: 8px 24px 10px; }
    .htd-library-search-wrap { min-width: 0; height: 38px; display: grid; grid-template-columns: 22px minmax(0, 1fr); align-items: center; gap: 4px; padding: 0 12px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-bg); box-sizing: border-box; color: var(--htd-text-dim); transition: border-color .12s ease, box-shadow .12s ease; }
    .htd-library-search-wrap:focus-within { border-color: var(--htd-focus); box-shadow: var(--htd-ring); }
    .htd-library-search { min-width: 0; height: 34px; border: 0; outline: 0; background: transparent; color: var(--htd-text); font: inherit; }
    .htd-library-search::placeholder { color: var(--htd-text-faint); }
    .htd-library-sort, .htd-library-replace-select { min-width: 0; height: 38px; box-sizing: border-box; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-surface-2); color: var(--htd-text); padding: 0 12px; font: inherit; }
    .htd-library-sort:focus-visible, .htd-library-replace-select:focus-visible, .htd-library-search:focus-visible, .htd-library-button:focus-visible, .htd-library-icon-button:focus-visible, .htd-library-title-input:focus-visible { outline: none; border-color: var(--htd-focus); box-shadow: var(--htd-ring); }
    .htd-library-tabs { display: inline-flex; align-items: end; gap: 18px; padding: 0 24px; border-bottom: 1px solid var(--htd-border); }
    .htd-library-tab { height: 36px; padding: 0 0 9px; border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: var(--htd-text-dim); cursor: pointer; font: inherit; }
    .htd-library-tab:hover { color: var(--htd-text); }
    .htd-library-tab.is-active { border-color: var(--htd-accent); background: transparent; color: var(--htd-accent-strong); }
    .htd-library-body { min-height: 0; display: grid; grid-template-columns: 190px minmax(360px, 1fr) 348px; overflow: hidden; }
    .htd-library-sidebar, .htd-library-grid, .htd-library-details { min-height: 0; overflow: auto; }
    .htd-library-sidebar { display: flex; flex-direction: column; gap: 8px; padding: 18px 14px 18px 20px; border-right: 1px solid var(--htd-border); }
    .htd-library-sidebar-title, .htd-library-details-title { color: var(--htd-text); font-weight: 700; }
    .htd-library-sidebar-section { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; }
    .htd-library-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(236px, 1fr)); grid-auto-rows: min-content; gap: 14px; padding: 18px 16px; }
    .htd-library-details { display: flex; flex-direction: column; gap: 14px; padding: 18px 20px; border-left: 1px solid var(--htd-border); }
    .htd-library-button { min-width: 32px; min-height: 32px; padding: 0 12px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: linear-gradient(180deg, var(--htd-surface-3), var(--htd-surface-2)); color: var(--htd-text); cursor: pointer; white-space: nowrap; display: inline-flex; align-items: center; justify-content: center; gap: 7px; font: inherit; transition: background .12s ease, border-color .12s ease, color .12s ease; }
    .htd-library-button:hover:not(:disabled) { background: linear-gradient(180deg, var(--htd-surface-hover), var(--htd-surface-3)); border-color: var(--htd-border-hover); color: #fff; }
    .htd-library-primary, .htd-library-quick-action.is-primary, .htd-library-quick-action.is-positive, .htd-library-button.is-positive { border-color: var(--htd-accent-border); background: linear-gradient(180deg, #4f4322, #3c3318); color: var(--htd-accent-strong); }
    .htd-library-primary:hover:not(:disabled), .htd-library-quick-action.is-primary:hover:not(:disabled), .htd-library-quick-action.is-positive:hover:not(:disabled), .htd-library-button.is-positive:hover:not(:disabled) { background: linear-gradient(180deg, #5b4d27, #46391b); color: #fff3cf; }
    .htd-library-quick-action.is-neutral { border-color: var(--htd-border-strong); background: linear-gradient(180deg, var(--htd-surface-3), var(--htd-surface-2)); }
    .htd-library-icon-button { width: 34px; min-width: 34px; padding: 0; border: 0; background: transparent; color: var(--htd-text); cursor: pointer; }
    .htd-library-icon-button.htd-library-primary { border: 1px solid var(--htd-accent-border); background: linear-gradient(180deg, #4f4322, #3c3318); color: var(--htd-accent-strong); }
    .htd-library-button:disabled, .htd-library-replace-select:disabled { opacity: 0.44; cursor: not-allowed; }
    .htd-library-icon { width: 16px; height: 16px; flex: 0 0 16px; display: inline-flex; align-items: center; justify-content: center; }
    .htd-library-icon svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .htd-library-filter-toggle, .htd-library-tag-filter { width: 100%; justify-content: start; text-align: left; overflow: hidden; }
    .htd-library-filter-toggle { display: grid; align-items: center; }
    .htd-library-filter-toggle { grid-template-columns: 16px minmax(0, 1fr) 28px auto; }
    .htd-library-tag-filter { justify-content: space-between; }
    .htd-library-filter-toggle > span:not(.htd-library-icon):not(.htd-library-toggle-dot):not(.htd-library-count), .htd-library-tag-filter > span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .htd-library-filter-toggle.is-active, .htd-library-tag-filter.is-active { border-color: var(--htd-accent-border); background: var(--htd-accent-bg); color: var(--htd-accent-strong); }
    .htd-library-toggle-dot { width: 22px; height: 13px; border-radius: 99px; border: 1px solid var(--htd-border-strong); background: var(--htd-surface-2); position: relative; box-sizing: border-box; }
    .htd-library-toggle-dot::after { content: ""; position: absolute; top: 2px; left: 2px; width: 7px; height: 7px; border-radius: 50%; background: var(--htd-text-dim); transition: left .2s ease, background .2s ease; }
    .htd-library-filter-toggle.is-active .htd-library-toggle-dot { background: var(--htd-accent-bg); border-color: var(--htd-accent-border); }
    .htd-library-filter-toggle.is-active .htd-library-toggle-dot::after { left: 12px; background: var(--htd-accent); }
    .htd-library-count { margin-left: auto; color: var(--htd-text-dim); }
    .htd-library-manage-tags { margin-top: 8px; width: 100%; justify-content: start; color: var(--htd-text-dim); }
    .htd-library-card { position: relative; min-width: 0; display: flex; flex-direction: column; gap: 8px; padding: 10px; border: 2px solid transparent; border-radius: var(--htd-radius); background: var(--htd-surface-2); color: var(--htd-text-dim); text-align: left; cursor: pointer; box-sizing: border-box; transition: border-color .12s ease, box-shadow .12s ease, background .12s ease; }
    .htd-library-card:hover { border-color: var(--htd-border-strong); background: var(--htd-surface-3); }
    .htd-library-card.is-selected { border-color: var(--htd-accent); box-shadow: var(--htd-shadow-glow); }
    .htd-library-selected-badge { position: absolute; top: 8px; right: 8px; z-index: 2; width: 26px; height: 26px; display: none; align-items: center; justify-content: center; border-radius: 50%; background: var(--htd-accent); color: var(--htd-bg); }
    .htd-library-card.is-selected .htd-library-selected-badge { display: inline-flex; }
    .htd-library-preview, .htd-library-strip-thumb, .htd-library-strip-empty { width: 100%; min-width: 0; padding: 0; border: 1px solid var(--htd-border); border-radius: var(--htd-radius-sm); background: #0a0e16; color: var(--htd-text-dim); display: flex; align-items: center; justify-content: center; overflow: hidden; }
    .htd-library-preview img, .htd-library-strip-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .htd-library-card-preview { height: 112px; flex: 0 0 112px; }
    .htd-library-media-strip { height: 68px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-template-rows: 54px 4px; gap: 0 1px; overflow: hidden; border-radius: 5px; }
    .htd-library-strip-thumb, .htd-library-strip-empty { height: 54px; border-radius: 0; border-width: 0; }
    .htd-library-strip-empty { grid-column: 1 / -1; }
    .htd-library-segment-bar.is-compact { grid-column: 1 / -1; }
    .htd-library-segment-preview { display: grid; gap: 5px; }
    .htd-library-segment-bar-track { height: 4px; display: flex; gap: 3px; overflow: hidden; border-radius: 99px; background: var(--htd-bg); }
    .htd-library-segment { min-width: 10px; background: var(--htd-text-faint); }
    .htd-library-segment.is-image { background: #69c16f; } /* semantic media-type indicator */
    .htd-library-segment.is-video { background: #a866e8; } /* semantic media-type indicator */
    .htd-library-segment.is-empty { background: var(--htd-border-strong); }
    .htd-library-segment-times { display: flex; justify-content: space-between; color: var(--htd-text-dim); font-size: 11px; }
    .htd-library-card-title, .htd-library-detail-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text); font-weight: 700; font-size: 14px; }
    .htd-library-title-editor { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) 30px 30px; gap: 5px; align-items: center; }
    .htd-library-title-input { min-width: 0; height: 30px; box-sizing: border-box; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); background: var(--htd-bg); color: var(--htd-text); padding: 0 8px; font: inherit; font-weight: 700; }
    .htd-library-title-confirm, .htd-library-title-cancel { width: 30px; min-width: 30px; height: 30px; min-height: 30px; border: 1px solid var(--htd-border-strong); background: linear-gradient(180deg, var(--htd-surface-3), var(--htd-surface-2)); }
    .htd-library-detail-name { font-size: 18px; }
    .htd-library-card-meta-line, .htd-library-card-counts, .htd-library-character-defaults, .htd-library-detail-meta { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text-dim); }
    .htd-library-description, .htd-library-detail-description { min-width: 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; color: var(--htd-text-dim); }
    .htd-library-detail-preview { height: 180px; flex: 0 0 180px; }
    .htd-library-tags { min-width: 0; display: flex; flex-wrap: wrap; gap: 5px; }
    .htd-library-tag { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 2px 7px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); color: var(--htd-text-dim); background: var(--htd-surface-2); font-size: 11px; }
    .htd-library-status-pill { align-self: start; padding: 3px 7px; border-radius: 999px; font-size: 11px; }
    .htd-library-status-pill.is-ok { color: var(--htd-ok, #baf0c8); border: 1px solid #436854; background: #173024; }
    .htd-library-status-pill.is-warning { color: var(--htd-warn, #ffe3a3); border: 1px solid #7a5e28; background: #332711; }
    .htd-library-status-pill.is-private { color: var(--htd-text-dim); border: 1px solid var(--htd-border-strong); background: var(--htd-surface-2); }
    .htd-library-status-pill.is-loaded, .htd-library-loaded { color: var(--htd-ok, #baf0c8); background: #173024; border: 1px solid #436854; }
    .htd-library-loaded { min-height: 32px; display: flex; align-items: center; gap: 8px; padding: 0 10px; border-radius: var(--htd-radius-sm); }
    .htd-library-card-actions, .htd-library-inspector-actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; margin-top: auto; }
    .htd-library-quick-action { width: 34px; min-width: 34px; height: 32px; min-height: 32px; padding: 0; border: 1px solid var(--htd-border-strong); background: linear-gradient(180deg, var(--htd-surface-3), var(--htd-surface-2)); }
    .htd-library-action-menu { position: relative; display: inline-flex; }
    .htd-library-menu, .htd-library-save-popup { position: absolute; z-index: 20; min-width: 190px; display: grid; gap: 4px; padding: 6px; border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius); background: var(--htd-surface); box-shadow: var(--htd-shadow-pop); }
    .htd-library-menu { right: 0; bottom: calc(100% + 6px); }
    .htd-library-save-popup { top: 48px; left: 432px; }
    .htd-library-menu-item { width: 100%; min-height: 30px; justify-content: start; border: 0; border-radius: var(--htd-radius-sm); background: transparent; color: var(--htd-text); cursor: pointer; }
    .htd-library-menu-item:hover { background: var(--htd-surface-hover); color: #fff; }
    .htd-library-menu-item.is-positive { color: var(--htd-accent-strong); }
    .htd-library-menu-item.is-danger { color: #ffd6dc; }
    .htd-library-menu-item.is-danger:hover { background: linear-gradient(180deg, #5a2330, #471b25); color: #fff3f5; }
    .htd-library-info-section { display: grid; gap: 8px; padding-top: 12px; border-top: 1px solid var(--htd-border); }
    .htd-library-info-title { color: var(--htd-text); font-weight: 700; }
    .htd-library-summary { display: grid; gap: 8px; }
    .htd-library-summary-row { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 12px; align-items: start; color: var(--htd-text-dim); }
    .htd-library-summary-label { color: var(--htd-text-faint); }
    .htd-library-summary-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .htd-library-summary-row.is-ok .htd-library-summary-value { color: var(--htd-ok, #baf0c8); }
    .htd-library-summary-row.is-warning .htd-library-summary-value { color: var(--htd-warn, #ffe3a3); }
    .htd-library-muted { color: var(--htd-text-faint); }
    .htd-library-inspector-actions { padding: 0 12px 12px; }
    .htd-library-status { min-height: 18px; padding: 0 24px 6px; color: var(--htd-text-faint); }
    .htd-library-actions { display: none; }
    .htd-library-empty { grid-column: 1 / -1; padding: 28px 8px; text-align: center; color: var(--htd-text-faint); }
    .htd-library-dialog.privacy-mode .htd-library-preview img,
    .htd-library-dialog.privacy-mode .htd-library-strip-thumb img,
    .htd-library-dialog.privacy-mode .htd-library-description,
    .htd-library-dialog.privacy-mode .htd-library-detail-description { opacity: 0; }
    .htd-library-dialog.privacy-mode .htd-library-card:hover .htd-library-preview img,
    .htd-library-dialog.privacy-mode .htd-library-card:hover .htd-library-strip-thumb img,
    .htd-library-dialog.privacy-mode .htd-library-card:hover .htd-library-description,
    .htd-library-dialog.privacy-mode .htd-library-details:hover .htd-library-preview img,
    .htd-library-dialog.privacy-mode .htd-library-details:hover .htd-library-detail-description { opacity: 1; }
    @media (max-width: 980px) {
      .htd-library-panel { width: calc(100vw - 28px); height: calc(100vh - 28px); min-height: 0; }
      .htd-library-controls { grid-template-columns: minmax(0, 1fr); }
      .htd-library-body { grid-template-columns: 150px minmax(260px, 1fr); }
      .htd-library-details { display: none; }
      .htd-library-actions { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  `;
  documentRef.head.append(style);
}
