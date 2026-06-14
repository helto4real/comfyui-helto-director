import {
  ASSET_TYPE_AUDIO,
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
} from "./schema.js";


const ROUTE_PREFIX = "/helto_director/media_browser";
const COLUMN_STORAGE_KEY = "helto_director_media_picker_columns";
const COLUMN_DEFAULT = 4;
const COLUMN_MIN = 2;
const COLUMN_MAX = 8;

const PLAY_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>`;
const PAUSE_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7z"/><path d="M13 5h4v14h-4z"/></svg>`;


export async function showMediaPicker(options) {
  const documentRef = options.documentRef ?? globalThis.document;
  installMediaPickerStyles(documentRef);
  if (options.assetType === ASSET_TYPE_AUDIO) return showAudioPicker({ ...options, documentRef });
  if (options.assetType === ASSET_TYPE_IMAGE || options.assetType === ASSET_TYPE_VIDEO) {
    return showVisualPicker({ ...options, documentRef });
  }
  return null;
}

export function closeMediaPicker(documentRef = globalThis.document) {
  documentRef.querySelector(".pr-image-browser-dialog")?.remove();
  documentRef.querySelector(".pr-image-large-preview")?.remove();
}

async function showVisualPicker({ assetType, node, documentRef, mode = "add", privacyMode = false }) {
  closeMediaPicker(documentRef);
  const mediaType = mediaTypeForAsset(assetType);
  const title = `${mode === "replace" ? "Replace" : "Add"} Timeline ${assetType}`;
  const okLabel = `${mode === "replace" ? "Replace" : "Add"} ${assetType}`;
  const noun = mediaType === "video" ? "videos" : "images";

  return new Promise((resolve) => {
    const overlay = documentRef.createElement("div");
    overlay.className = `pr-image-browser-dialog${privacyMode ? " privacy-mode" : ""}`;
    overlay.innerHTML = `
      <div class="pr-image-browser-panel">
        <h3>${escapeHtml(title)}</h3>
        <div class="pr-image-browser-controls">
          <select class="folder" title="Choose configured ${mediaType} folder"></select>
          <input class="search" type="search" placeholder="Search ${noun}..." title="Search loaded filenames and relative paths">
          <div class="pr-image-sort-wrap">
            <button class="sort pr-image-sort-btn" type="button" title="Sort ${noun}" aria-haspopup="true" aria-expanded="false">
              <span>Newest</span>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
            </button>
            <div class="pr-image-sort-menu" role="menu"></div>
          </div>
          <button class="scope pr-image-icon-btn" type="button" title="Recursive folder view" aria-label="Recursive folder view"></button>
          <button class="folder-add pr-image-icon-btn" type="button" title="Add configured ${mediaType} folder" aria-label="Add configured ${mediaType} folder">+</button>
          <button class="folder-remove pr-image-icon-btn" type="button" title="Remove configured ${mediaType} folder" aria-label="Remove configured ${mediaType} folder">-</button>
          <label class="pr-image-columns-control" title="Thumbnail columns per row">
            <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
            <input class="columns" type="range" min="${COLUMN_MIN}" max="${COLUMN_MAX}" step="1" value="${COLUMN_DEFAULT}">
            <span class="columns-value">${COLUMN_DEFAULT}</span>
          </label>
        </div>
        <span class="pr-image-browser-meta"></span>
        <div class="pr-image-browser-grid"></div>
        <div class="pr-image-browser-actions">
          <button class="cancel" type="button">Cancel</button>
          <button class="ok" type="button">${escapeHtml(okLabel)}</button>
        </div>
      </div>`;

    const folderSelect = overlay.querySelector(".folder");
    const searchInput = overlay.querySelector(".search");
    const sortButton = overlay.querySelector(".sort");
    const sortButtonLabel = sortButton.querySelector("span");
    const sortMenu = overlay.querySelector(".pr-image-sort-menu");
    const scopeButton = overlay.querySelector(".scope");
    const folderAddButton = overlay.querySelector(".folder-add");
    const folderRemoveButton = overlay.querySelector(".folder-remove");
    const columnsInput = overlay.querySelector(".columns");
    const columnsValue = overlay.querySelector(".columns-value");
    const grid = overlay.querySelector(".pr-image-browser-grid");
    const meta = overlay.querySelector(".pr-image-browser-meta");

    let availableItems = [];
    let selectedItem = null;
    let recursive = true;
    let sortMode = "newest";
    columnsInput.value = String(getStoredColumns());
    const sortOptions = [
      { value: "newest", label: "Newest" },
      { value: "oldest", label: "Oldest" },
      { value: "name-asc", label: "Name A-Z" },
      { value: "name-desc", label: "Name Z-A" },
    ];

    const finish = (value) => {
      overlay.remove();
      documentRef.querySelector(".pr-image-large-preview")?.remove();
      resolve(value);
    };

    const syncScopeButton = () => {
      scopeButton.title = recursive ? `Show ${noun} recursively from subfolders` : `Show only ${noun} directly in this folder`;
      scopeButton.innerHTML = recursive
        ? `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h6l2 2h9a2 2 0 0 1 2 2v2"/><path d="M6 12v6a2 2 0 0 0 2 2h5"/><path d="M10 15h4l1.5 1.5H21v3.5H10z"/></svg>`
        : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`;
    };

    const syncGridVisibility = () => {
      grid.classList.toggle("hide-images", privacyMode);
      grid.classList.toggle("show-images", !privacyMode);
    };

    const syncColumns = () => {
      const columns = setStoredColumns(columnsInput.value);
      columnsInput.value = String(columns);
      grid.style.setProperty("--pr-image-columns", String(columns));
      columnsValue.textContent = String(columns);
    };

    const syncSortMenu = () => {
      const active = sortOptions.find((option) => option.value === sortMode) ?? sortOptions[0];
      sortButtonLabel.textContent = active.label;
      for (const button of sortMenu.querySelectorAll(".pr-image-sort-option")) {
        button.classList.toggle("active", button.dataset.sortMode === sortMode);
      }
    };

    const sortedItems = () => availableItems
      .map((item, index) => ({ item, index }))
      .sort((a, b) => compareBySortMode(a, b, sortMode))
      .map((entry) => entry.item);

    const renderGrid = () => {
      grid.innerHTML = "";
      const query = searchInput.value.trim().toLowerCase();
      const items = sortedItems();
      const visibleItems = query
        ? items.filter((item) => String(item.filename || "").toLowerCase().includes(query))
        : items;

      if (selectedItem && !visibleItems.some((item) => item.filename === selectedItem.filename)) {
        selectedItem = null;
      }

      for (const item of visibleItems) {
        const tile = documentRef.createElement("button");
        tile.type = "button";
        tile.className = `pr-image-tile${selectedItem?.filename === item.filename ? " selected" : ""}`;
        tile.title = `${item.filename}\nClick to select. Ctrl-click for large preview.`;
        tile.innerHTML = `<img src="${escapeHtml(item.thumb_url)}" alt="">`;
        tile.addEventListener("click", (event) => {
          if (event.ctrlKey) {
            showLargePreview(documentRef, item.view_url, itemCaption(item));
            return;
          }
          selectedItem = { ...item, folder_alias: folderSelect.value };
          for (const other of grid.querySelectorAll(".pr-image-tile")) other.classList.remove("selected");
          tile.classList.add("selected");
          meta.textContent = itemCaption(item);
        });
        grid.append(tile);
      }

      if (!availableItems.length) {
        meta.textContent = `No ${noun} found.`;
      } else if (!visibleItems.length) {
        meta.textContent = `No ${noun} match "${searchInput.value.trim()}".`;
      } else if (query) {
        meta.textContent = `${visibleItems.length} of ${availableItems.length} ${noun} match. Select one to add.`;
      } else if (!selectedItem) {
        meta.textContent = `${availableItems.length} ${noun}. Select one to add.`;
      }
      syncGridVisibility();
    };

    const loadFolders = async (preferredAlias = null) => {
      const data = await fetchJson(`${ROUTE_PREFIX}/${mediaType}/folders`);
      folderSelect.innerHTML = data.folders.map((folder) => (
        `<option value="${escapeHtml(folder.alias)}">${escapeHtml(folder.alias)}${folder.exists ? "" : " (missing)"}</option>`
      )).join("");
      const propertyName = `helto_director_last_${mediaType}_folder_alias`;
      const lastAlias = preferredAlias || node?.properties?.[propertyName];
      if (lastAlias && data.folders.some((folder) => folder.alias === lastAlias)) {
        folderSelect.value = lastAlias;
      }
    };

    const loadItems = async () => {
      node.properties = node.properties || {};
      node.properties[`helto_director_last_${mediaType}_folder_alias`] = folderSelect.value;
      const params = new URLSearchParams({
        alias: folderSelect.value,
        recursive: recursive ? "1" : "0",
      });
      if (privacyMode) params.set("privacy", "1");
      const data = await fetchJson(`${ROUTE_PREFIX}/${mediaType}/items?${params.toString()}`);
      availableItems = data[mediaType === "image" ? "images" : "videos"] ?? [];
      selectedItem = null;
      renderGrid();
    };

    folderSelect.addEventListener("change", loadItems);
    scopeButton.addEventListener("click", async () => {
      recursive = !recursive;
      syncScopeButton();
      await loadItems();
    });
    folderAddButton.addEventListener("click", async () => {
      try {
        const alias = promptInDocument(documentRef, "Folder alias");
        if (!alias) return;
        const path = promptInDocument(documentRef, "Folder path");
        if (!path) return;
        await fetchJson(`${ROUTE_PREFIX}/${mediaType}/folders`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ alias, path }),
        });
        await loadFolders(alias);
        await loadItems();
      } catch (error) {
        alertInDocument(documentRef, error.message);
      }
    });
    folderRemoveButton.addEventListener("click", async () => {
      try {
        const data = await fetchJson(`${ROUTE_PREFIX}/${mediaType}/folders`);
        const removable = data.folders.filter((folder) => folder.alias !== "input");
        if (!removable.length) {
          alertInDocument(documentRef, "No custom folders to remove.");
          return;
        }
        const alias = promptInDocument(documentRef, `Folder alias to remove:\n${removable.map((folder) => folder.alias).join("\n")}`);
        if (!alias) return;
        await fetchJson(`${ROUTE_PREFIX}/${mediaType}/folders?alias=${encodeURIComponent(alias)}`, { method: "DELETE" });
        await loadFolders("input");
        await loadItems();
      } catch (error) {
        alertInDocument(documentRef, error.message);
      }
    });
    searchInput.addEventListener("input", renderGrid);
    columnsInput.addEventListener("input", syncColumns);
    sortButton.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = !sortMenu.classList.contains("is-open");
      sortMenu.classList.toggle("is-open", open);
      sortButton.setAttribute("aria-expanded", open ? "true" : "false");
    });
    sortMenu.innerHTML = sortOptions.map((option) => (
      `<button class="pr-image-sort-option${option.value === sortMode ? " active" : ""}" type="button" role="menuitem" data-sort-mode="${escapeHtml(option.value)}">${escapeHtml(option.label)}</button>`
    )).join("");
    sortMenu.addEventListener("click", (event) => {
      const option = event.target.closest(".pr-image-sort-option");
      if (!option) return;
      sortMode = option.dataset.sortMode || "newest";
      syncSortMenu();
      sortMenu.classList.remove("is-open");
      sortButton.setAttribute("aria-expanded", "false");
      renderGrid();
    });
    overlay.addEventListener("click", (event) => {
      if (!event.target.closest(".pr-image-sort-wrap")) {
        sortMenu.classList.remove("is-open");
        sortButton.setAttribute("aria-expanded", "false");
      }
      if (event.target === overlay) finish(null);
    });
    overlay.querySelector(".cancel").addEventListener("click", () => finish(null));
    overlay.querySelector(".ok").addEventListener("click", () => {
      if (!selectedItem) {
        alertInDocument(documentRef, `Select a ${mediaType} first.`);
        return;
      }
      finish(selectedItem);
    });

    documentRef.body.append(overlay);
    syncColumns();
    syncScopeButton();
    syncSortMenu();
    syncGridVisibility();
    loadFolders()
      .then(loadItems)
      .catch((error) => { meta.textContent = error.message; });
  });
}

async function showAudioPicker({ node, documentRef, privacyMode = false }) {
  closeMediaPicker(documentRef);
  return new Promise((resolve) => {
    const overlay = documentRef.createElement("div");
    overlay.className = `pr-image-browser-dialog pr-audio-browser-dialog${privacyMode ? " privacy-mode" : ""}`;
    overlay.innerHTML = `
      <div class="pr-image-browser-panel">
        <h3>Add Timeline Audio</h3>
        <div class="pr-image-browser-controls" style="grid-template-columns: 1fr minmax(150px, 1fr) auto auto auto;">
          <select class="folder" title="Choose configured audio folder"></select>
          <input class="search" type="search" placeholder="Search audio..." title="Search loaded audio filenames and relative paths">
          <button class="scope pr-image-icon-btn" type="button" title="Recursive folder view" aria-label="Recursive folder view"></button>
          <button class="folder-add pr-image-icon-btn" type="button" title="Add configured audio folder" aria-label="Add configured audio folder">+</button>
          <button class="folder-remove pr-image-icon-btn" type="button" title="Remove configured audio folder" aria-label="Remove configured audio folder">-</button>
        </div>
        <span class="pr-image-browser-meta"></span>
        <div class="pr-audio-browser-list"></div>
        <div class="pr-image-browser-actions">
          <button class="cancel" type="button">Cancel</button>
          <button class="ok" type="button">Add Audio</button>
        </div>
      </div>`;

    const folderSelect = overlay.querySelector(".folder");
    const searchInput = overlay.querySelector(".search");
    const scopeButton = overlay.querySelector(".scope");
    const folderAddButton = overlay.querySelector(".folder-add");
    const folderRemoveButton = overlay.querySelector(".folder-remove");
    const list = overlay.querySelector(".pr-audio-browser-list");
    const meta = overlay.querySelector(".pr-image-browser-meta");
    const previewAudio = new Audio();

    let availableAudios = [];
    let selectedAudio = null;
    let recursive = true;
    let playingFilename = null;

    const finish = (value) => {
      previewAudio.pause();
      overlay.remove();
      resolve(value);
    };

    const syncScopeButton = () => {
      scopeButton.title = recursive ? "Show audio recursively from subfolders" : "Show only audio directly in this folder";
      scopeButton.innerHTML = recursive
        ? `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h6l2 2h9a2 2 0 0 1 2 2v2"/><path d="M6 12v6a2 2 0 0 0 2 2h5"/><path d="M10 15h4l1.5 1.5H21v3.5H10z"/></svg>`
        : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h6l2 2h10v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`;
    };

    const syncPreviewButtons = () => {
      for (const button of list.querySelectorAll(".pr-audio-play")) {
        const isPlaying = button.dataset.filename === playingFilename && !previewAudio.paused;
        button.innerHTML = isPlaying ? PAUSE_ICON : PLAY_ICON;
        button.title = isPlaying ? "Pause preview" : "Play preview";
        button.setAttribute("aria-label", button.title);
      }
    };

    const selectAudio = (audio, row) => {
      selectedAudio = { ...audio, folder_alias: folderSelect.value };
      for (const other of list.querySelectorAll(".pr-audio-row")) other.classList.remove("selected");
      row.classList.add("selected");
      meta.textContent = audio.filename;
    };

    const togglePreview = async (audio) => {
      if (playingFilename === audio.filename && !previewAudio.paused) {
        previewAudio.pause();
        syncPreviewButtons();
        return;
      }
      if (playingFilename !== audio.filename) {
        previewAudio.pause();
        previewAudio.src = audio.view_url;
        playingFilename = audio.filename;
      }
      try {
        await previewAudio.play();
      } catch {
        playingFilename = null;
        meta.textContent = `Could not preview ${audio.filename}.`;
      }
      syncPreviewButtons();
    };

    const renderAudioList = () => {
      list.innerHTML = "";
      const query = searchInput.value.trim().toLowerCase();
      const visibleAudios = query
        ? availableAudios.filter((audio) => String(audio.filename || "").toLowerCase().includes(query))
        : availableAudios;

      if (selectedAudio && !visibleAudios.some((audio) => audio.filename === selectedAudio.filename)) {
        selectedAudio = null;
      }

      for (const audio of visibleAudios) {
        const row = documentRef.createElement("div");
        row.className = `pr-audio-row${selectedAudio?.filename === audio.filename ? " selected" : ""}`;
        row.title = `${audio.filename}\nClick to select.`;
        row.tabIndex = 0;

        const playButton = documentRef.createElement("button");
        playButton.type = "button";
        playButton.className = "pr-audio-play";
        playButton.dataset.filename = audio.filename;
        playButton.innerHTML = PLAY_ICON;
        playButton.addEventListener("click", async (event) => {
          event.stopPropagation();
          selectAudio(audio, row);
          await togglePreview(audio);
        });

        const details = documentRef.createElement("div");
        details.className = "pr-audio-details";
        const metaText = [formatFileSize(audio.size), formatDuration(audio.duration_seconds)].filter(Boolean).join(" - ");
        details.innerHTML = `
          <div class="pr-audio-name">${escapeHtml(audio.filename)}</div>
          <div class="pr-audio-size">${escapeHtml(metaText)}</div>`;

        row.append(playButton, details);
        row.addEventListener("click", () => selectAudio(audio, row));
        row.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            selectAudio(audio, row);
          }
        });
        list.append(row);
      }

      if (!availableAudios.length) {
        meta.textContent = "No audio clips found.";
      } else if (!visibleAudios.length) {
        meta.textContent = `No audio clips match "${searchInput.value.trim()}".`;
      } else if (query) {
        meta.textContent = `${visibleAudios.length} of ${availableAudios.length} audio clips match. Select one to add.`;
      } else if (!selectedAudio) {
        meta.textContent = `${availableAudios.length} audio clips. Select one to add.`;
      }
      syncPreviewButtons();
    };

    const loadFolders = async (preferredAlias = null) => {
      const data = await fetchJson(`${ROUTE_PREFIX}/audio/folders`);
      folderSelect.innerHTML = data.folders.map((folder) => (
        `<option value="${escapeHtml(folder.alias)}">${escapeHtml(folder.alias)}${folder.exists ? "" : " (missing)"}</option>`
      )).join("");
      const lastAlias = preferredAlias || node?.properties?.helto_director_last_audio_folder_alias;
      if (lastAlias && data.folders.some((folder) => folder.alias === lastAlias)) {
        folderSelect.value = lastAlias;
      }
    };

    const loadAudios = async () => {
      node.properties = node.properties || {};
      node.properties.helto_director_last_audio_folder_alias = folderSelect.value;
      previewAudio.pause();
      playingFilename = null;
      const params = new URLSearchParams({
        alias: folderSelect.value,
        recursive: recursive ? "1" : "0",
      });
      const data = await fetchJson(`${ROUTE_PREFIX}/audio/items?${params.toString()}`);
      availableAudios = data.audios ?? [];
      selectedAudio = null;
      renderAudioList();
    };

    previewAudio.addEventListener("pause", syncPreviewButtons);
    previewAudio.addEventListener("ended", () => {
      playingFilename = null;
      syncPreviewButtons();
    });
    previewAudio.addEventListener("error", () => {
      playingFilename = null;
      syncPreviewButtons();
    });
    folderSelect.addEventListener("change", loadAudios);
    scopeButton.addEventListener("click", async () => {
      recursive = !recursive;
      syncScopeButton();
      await loadAudios();
    });
    folderAddButton.addEventListener("click", async () => {
      try {
        const alias = promptInDocument(documentRef, "Folder alias");
        if (!alias) return;
        const path = promptInDocument(documentRef, "Folder path");
        if (!path) return;
        await fetchJson(`${ROUTE_PREFIX}/audio/folders`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ alias, path }),
        });
        await loadFolders(alias);
        await loadAudios();
      } catch (error) {
        alertInDocument(documentRef, error.message);
      }
    });
    folderRemoveButton.addEventListener("click", async () => {
      try {
        const data = await fetchJson(`${ROUTE_PREFIX}/audio/folders`);
        const removable = data.folders.filter((folder) => folder.alias !== "input");
        if (!removable.length) {
          alertInDocument(documentRef, "No custom folders to remove.");
          return;
        }
        const alias = promptInDocument(documentRef, `Folder alias to remove:\n${removable.map((folder) => folder.alias).join("\n")}`);
        if (!alias) return;
        await fetchJson(`${ROUTE_PREFIX}/audio/folders?alias=${encodeURIComponent(alias)}`, { method: "DELETE" });
        await loadFolders("input");
        await loadAudios();
      } catch (error) {
        alertInDocument(documentRef, error.message);
      }
    });
    searchInput.addEventListener("input", renderAudioList);
    overlay.querySelector(".cancel").addEventListener("click", () => finish(null));
    overlay.querySelector(".ok").addEventListener("click", () => {
      if (!selectedAudio) {
        alertInDocument(documentRef, "Select an audio clip first.");
        return;
      }
      finish(selectedAudio);
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) finish(null);
    });

    documentRef.body.append(overlay);
    syncScopeButton();
    loadFolders()
      .then(loadAudios)
      .catch((error) => { meta.textContent = error.message; });
  });
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(text || response.statusText || `HTTP ${response.status}`);
  }
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

function showLargePreview(documentRef, imageUrl, caption = "") {
  documentRef.querySelector(".pr-image-large-preview")?.remove();
  const overlay = documentRef.createElement("div");
  overlay.className = "pr-image-large-preview";
  overlay.innerHTML = `
    <div class="pr-image-large-preview-panel">
      <button class="pr-image-large-preview-close" type="button" title="Close preview" aria-label="Close preview">x</button>
      <img src="${escapeHtml(imageUrl)}" alt="">
      ${caption ? `<div class="pr-image-large-preview-caption">${escapeHtml(caption)}</div>` : ""}
    </div>`;
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay || event.target.closest(".pr-image-large-preview-close")) overlay.remove();
  });
  documentRef.body.append(overlay);
}

function compareBySortMode(a, b, sortMode) {
  let cmp = 0;
  if (sortMode === "newest") {
    cmp = Number(b.item.mtime || 0) - Number(a.item.mtime || 0);
  } else if (sortMode === "oldest") {
    cmp = Number(a.item.mtime || 0) - Number(b.item.mtime || 0);
  } else if (sortMode === "name-desc") {
    cmp = compareNames(b.item, a.item);
  } else {
    cmp = compareNames(a.item, b.item);
  }
  if (cmp !== 0) return cmp;
  cmp = compareNames(a.item, b.item);
  return cmp !== 0 ? cmp : a.index - b.index;
}

function compareNames(a, b) {
  return String(a.filename || "").localeCompare(String(b.filename || ""), undefined, { sensitivity: "base" });
}

function itemCaption(item) {
  const size = item.width || item.height ? ` (${item.width || "?"}x${item.height || "?"})` : "";
  return `${item.filename}${size}`;
}

function mediaTypeForAsset(assetType) {
  if (assetType === ASSET_TYPE_IMAGE) return "image";
  if (assetType === ASSET_TYPE_VIDEO) return "video";
  if (assetType === ASSET_TYPE_AUDIO) return "audio";
  return "";
}

function getStoredColumns() {
  try {
    return normalizeColumns(window.localStorage?.getItem(COLUMN_STORAGE_KEY));
  } catch {
    return COLUMN_DEFAULT;
  }
}

function setStoredColumns(value) {
  const columns = normalizeColumns(value);
  try {
    window.localStorage?.setItem(COLUMN_STORAGE_KEY, String(columns));
  } catch { }
  return columns;
}

function normalizeColumns(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return COLUMN_DEFAULT;
  return Math.max(COLUMN_MIN, Math.min(COLUMN_MAX, parsed));
}

function formatFileSize(bytes) {
  const value = Number(bytes || 0);
  if (!Number.isFinite(value) || value <= 0) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "";
  const totalSeconds = Math.max(0, Math.round(value));
  if (totalSeconds < 60) return `${totalSeconds} s`;
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function promptInDocument(documentRef, message) {
  return documentRef.defaultView?.prompt?.(message) ?? globalThis.prompt?.(message);
}

function alertInDocument(documentRef, message) {
  (documentRef.defaultView?.alert ?? globalThis.alert)?.(message);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  })[char]);
}

function installMediaPickerStyles(documentRef) {
  if (!documentRef || documentRef.getElementById("helto-media-picker-style")) return;
  const style = documentRef.createElement("style");
  style.id = "helto-media-picker-style";
  style.textContent = `
    .pr-image-browser-dialog { position: fixed; inset: 0; z-index: 10001; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; color: #ddd; font: 12px Arial, sans-serif; }
    .pr-image-browser-panel { width: 720px; max-width: 92vw; max-height: 86vh; overflow: auto; background: #222; border: 1px solid #555; border-radius: 6px; box-shadow: 0 12px 44px rgba(0,0,0,0.55); padding: 14px; box-sizing: border-box; }
    .pr-image-browser-panel h3 { margin: 0 0 10px; font-size: 15px; color: #f1f1f1; }
    .pr-image-browser-controls { display: grid; grid-template-columns: 1fr minmax(150px, 1fr) minmax(108px, 130px) auto auto auto minmax(130px, 180px); gap: 8px; align-items: center; margin-bottom: 8px; }
    .pr-image-browser-controls select, .pr-image-browser-controls input { min-width: 0; background: #151515; color: #ddd; border: 1px solid #555; border-radius: 4px; padding: 6px; box-sizing: border-box; }
    .pr-image-browser-controls button, .pr-image-browser-actions button { background: #333; color: #ddd; border: 1px solid #555; border-radius: 4px; padding: 6px 10px; cursor: pointer; }
    .pr-image-browser-controls button:hover, .pr-image-browser-actions button:hover { background: #444; }
    .pr-image-icon-btn { width: 32px; height: 32px; padding: 4px !important; display: inline-flex; align-items: center; justify-content: center; }
    .pr-image-icon-btn svg, .pr-image-sort-btn svg, .pr-audio-play svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .pr-audio-play svg { fill: currentColor; stroke: none; }
    .pr-image-sort-wrap { position: relative; min-width: 0; }
    .pr-image-sort-btn { width: 100%; min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; text-align: left; }
    .pr-image-sort-menu { display: none; position: absolute; left: 0; top: calc(100% + 4px); z-index: 10002; background: #1d1c25; border: 1px solid #3d3c4a; border-radius: 6px; padding: 6px; min-width: 144px; box-shadow: 0 8px 22px rgba(0,0,0,0.55); }
    .pr-image-sort-menu.is-open { display: flex; flex-direction: column; gap: 2px; }
    .pr-image-sort-option { background: transparent !important; border: none !important; color: #aaa; border-radius: 4px !important; padding: 7px 10px !important; font-size: 12px; font-weight: 600; text-align: left; cursor: pointer; }
    .pr-image-sort-option:hover { background: #2b2a34 !important; color: #ddd; }
    .pr-image-sort-option.active { color: #f2f2f4; }
    .pr-image-columns-control { display: grid; grid-template-columns: 22px 1fr 18px; gap: 6px; align-items: center; color: #ddd; }
    .pr-image-columns-control svg { width: 18px; height: 18px; stroke: currentColor; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .pr-image-columns-control input { width: 100%; min-width: 0; }
    .pr-image-browser-grid { --pr-image-columns: 4; display: grid; grid-template-columns: repeat(var(--pr-image-columns), minmax(0, 1fr)); gap: 8px; max-height: 52vh; overflow: auto; padding: 2px; }
    .pr-image-browser-dialog.privacy-mode .pr-image-browser-grid.hide-images .pr-image-tile img { opacity: 0; }
    .pr-image-browser-dialog.privacy-mode .pr-image-browser-panel:hover .pr-image-browser-grid.hide-images .pr-image-tile img, .pr-image-browser-grid.show-images .pr-image-tile img { opacity: 1; }
    .pr-image-tile { min-width: 0; background: #181818; border: 1px solid #444; border-radius: 5px; padding: 5px; color: #ddd; cursor: pointer; text-align: left; }
    .pr-image-tile.selected { border-color: #8ab4f8; background: #202a36; box-shadow: none; }
    .pr-image-tile img { display: block; width: 100%; aspect-ratio: 1 / 1; object-fit: contain; background: #101010; border: 1px solid #2d2d2d; border-radius: 3px; transition: opacity .12s ease; box-sizing: border-box; }
    .pr-audio-browser-list { max-height: 52vh; overflow: auto; display: flex; flex-direction: column; gap: 6px; border: 1px solid #333; border-radius: 6px; padding: 6px; background: #141414; }
    .pr-audio-row { display: grid; grid-template-columns: 34px 1fr; gap: 8px; align-items: center; background: #202020; border: 1px solid #333; border-radius: 6px; padding: 7px; cursor: pointer; }
    .pr-audio-row.selected { border-color: #f0cc64; background: #2c2717; }
    .pr-audio-play { width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; background: #151515; color: #eee; border: 1px solid #444; border-radius: 4px; cursor: pointer; }
    .pr-audio-details { min-width: 0; }
    .pr-audio-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #eee; font-size: 12px; }
    .pr-audio-size { color: #aaa; font-size: 11px; margin-top: 2px; }
    .pr-audio-browser-dialog.privacy-mode .pr-audio-name, .pr-audio-browser-dialog.privacy-mode .pr-audio-size, .pr-audio-browser-dialog.privacy-mode .pr-image-browser-meta { color: transparent; text-shadow: none; }
    .pr-audio-browser-dialog.privacy-mode .pr-image-browser-panel:hover .pr-audio-name { color: #eee; }
    .pr-audio-browser-dialog.privacy-mode .pr-image-browser-panel:hover .pr-audio-size, .pr-audio-browser-dialog.privacy-mode .pr-image-browser-panel:hover .pr-image-browser-meta { color: #aaa; }
    .pr-image-browser-meta { min-height: 18px; color: #aaa; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pr-image-browser-actions { display: flex; justify-content: flex-end; gap: 8px; }
    .pr-image-large-preview { position: fixed; inset: 0; z-index: 10000; background: rgba(0,0,0,0.78); display: flex; align-items: center; justify-content: center; }
    .pr-image-large-preview-panel { position: relative; max-width: calc(100vw - 64px); max-height: calc(100vh - 64px); background: #111; border: 1px solid #444; border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
    .pr-image-large-preview-panel img { max-width: 100%; max-height: calc(100vh - 132px); object-fit: contain; }
    .pr-image-large-preview-close { position: absolute; top: 8px; right: 8px; width: 26px; height: 26px; border-radius: 50%; border: 1px solid #555; background: #1e1e1e; color: #eee; cursor: pointer; }
    .pr-image-large-preview-caption { color: #ddd; font-size: 12px; text-align: center; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  `;
  documentRef.head.append(style);
}
