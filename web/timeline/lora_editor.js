import {
  MODEL_LORA_MODEL_LTX_2_3,
  MODEL_LORA_MODEL_WAN_2_2,
  MODEL_LORA_TARGET_HIGH_NOISE,
  MODEL_LORA_TARGET_LOW_NOISE,
  MODEL_LORA_TARGET_MAIN,
  createDefaultLoraStack,
} from "./schema.js";

const STYLE_ID = "helto-director-lora-editor-style";
const EDITOR_CLASS = "htd-lora-editor-dialog";
const INFO_CLASS = "htd-lora-info-dialog";
const LORA_LIST_ROUTE = "/helto_director/api/loras?format=details";
const LORA_INFO_ROUTE = "/helto_director/api/loras/info";
const LORA_INFO_REFRESH_ROUTE = "/helto_director/api/loras/info/refresh";

let loraListPromise = null;
let dialogCounter = 0;

export function loraEditorProfileForTarget(modelKey, targetKey) {
  if (modelKey === MODEL_LORA_MODEL_LTX_2_3 && targetKey === MODEL_LORA_TARGET_MAIN) {
    return {
      id: "ltx-main",
      modelKey,
      targetKey,
      label: "LTX Main",
      allowClipStrength: true,
      showStrengths: "single",
    };
  }
  if (modelKey === MODEL_LORA_MODEL_WAN_2_2 && targetKey === MODEL_LORA_TARGET_HIGH_NOISE) {
    return {
      id: "wan-high-noise",
      modelKey,
      targetKey,
      label: "WAN High",
      allowClipStrength: false,
      showStrengths: "single",
    };
  }
  if (modelKey === MODEL_LORA_MODEL_WAN_2_2 && targetKey === MODEL_LORA_TARGET_LOW_NOISE) {
    return {
      id: "wan-low-noise",
      modelKey,
      targetKey,
      label: "WAN Low",
      allowClipStrength: false,
      showStrengths: "single",
    };
  }
  return {
    id: "generic",
    modelKey,
    targetKey,
    label: "LoRA Stack",
    allowClipStrength: false,
    showStrengths: "single",
  };
}

export function normalizeLoraEditorStack(stack, profile = {}) {
  const source = stack && typeof stack === "object" && !Array.isArray(stack) ? stack : createDefaultLoraStack();
  const ui = source.ui && typeof source.ui === "object" && !Array.isArray(source.ui) ? source.ui : {};
  const allowClipStrength = Boolean(profile.allowClipStrength);
  const showStrengths = allowClipStrength
    ? String(source.show_strengths || ui.show_strengths || profile.showStrengths || "single")
    : "single";
  const loras = Array.isArray(source.loras)
    ? source.loras
      .filter((row) => row && typeof row === "object" && !Array.isArray(row) && row.enabled !== false && row.name)
      .map((row) => normalizeLoraEditorRow(row, allowClipStrength, showStrengths))
      .filter(Boolean)
    : [];
  return {
    version: 1,
    loras,
    ui: {
      show_strengths: showStrengths === "separate" ? "separate" : "single",
      match: String(source.match || ui.match || ""),
    },
  };
}

export function normalizeLoraEditorRow(row, allowClipStrength = true, showStrengths = "single") {
  const name = String(row?.name ?? row?.lora ?? "").trim();
  if (!name) return null;
  const strengthModel = finiteNumber(row.strength_model ?? row.strength, 1);
  const strengthClip = allowClipStrength && showStrengths === "separate"
    ? finiteNumber(row.strength_clip ?? row.strengthTwo ?? strengthModel, strengthModel)
    : strengthModel;
  if (strengthModel === 0 && strengthClip === 0) return null;
  return {
    enabled: true,
    name,
    strength_model: strengthModel,
    strength_clip: strengthClip,
  };
}

export function loraEditorFilteredChoices(loras, match = "") {
  const source = Array.isArray(loras) ? loras.map((item) => String(item ?? "")).filter(Boolean) : [];
  const needle = String(match ?? "");
  if (!needle) return source;
  try {
    const regex = new RegExp(needle);
    return source.filter((item) => regex.test(item));
  } catch {
    return source;
  }
}

export async function fetchTimelineLoras({ force = false, fetcher = null } = {}) {
  if (force) loraListPromise = null;
  if (!loraListPromise) {
    const activeFetcher = fetcher ?? globalThis.fetch?.bind(globalThis);
    if (!activeFetcher) return [];
    loraListPromise = fetchJson(activeFetcher, LORA_LIST_ROUTE)
      .then((data) => Array.isArray(data) ? data.map((item) => String(item?.file ?? item ?? "")).filter(Boolean) : [])
      .catch(() => fetchJson(activeFetcher, "/object_info/LoraLoader")
        .then((data) => data?.LoraLoader?.input?.required?.lora_name?.[0] ?? [])
        .catch(() => []));
  }
  return loraListPromise;
}

export async function fetchTimelineLoraInfo(file, { refresh = false, light = false, fetcher = null } = {}) {
  const activeFetcher = fetcher ?? globalThis.fetch?.bind(globalThis);
  if (!activeFetcher || !file) return null;
  const params = new URLSearchParams({ files: file });
  if (light) params.set("light", "1");
  const endpoint = refresh ? LORA_INFO_REFRESH_ROUTE : LORA_INFO_ROUTE;
  const payload = await fetchJson(activeFetcher, `${endpoint}?${params.toString()}`);
  return payload?.data?.[0] ?? null;
}

export function closeTimelineLoraStackEditor(documentRef = globalThis.document) {
  for (const dialog of documentRef?.querySelectorAll?.(`.${EDITOR_CLASS}`) ?? []) {
    dialog.remove();
  }
}

export function showTimelineLoraStackEditor(options = {}) {
  const documentRef = options.documentRef ?? globalThis.document;
  if (!documentRef?.createElement || !documentRef.body) return null;
  if (options.privacyLocked) return null;
  installLoraEditorStyles(documentRef);
  closeTimelineLoraStackEditor(documentRef);

  const profile = options.profile ?? {};
  const state = {
    stack: normalizeLoraEditorStack(options.stack, profile),
    rows: normalizeLoraEditorStack(options.stack, profile).loras.map((row) => ({ ...row })),
    loras: Array.isArray(options.loras) ? options.loras.map(String) : [],
    loading: false,
    error: "",
    addValue: "",
  };
  const fetcher = options.fetcher ?? documentRef.defaultView?.fetch?.bind(documentRef.defaultView) ?? globalThis.fetch?.bind(globalThis);
  const dialogId = `htd-lora-editor-${++dialogCounter}`;

  const overlay = documentRef.createElement("div");
  overlay.className = EDITOR_CLASS;
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", options.title || profile.label || "LoRA Stack");

  const panel = documentRef.createElement("div");
  panel.className = "htd-lora-editor-panel";
  const render = () => {
    panel.replaceChildren();
    const header = div(documentRef, "htd-lora-editor-header");
    const title = div(documentRef, "htd-lora-editor-title");
    title.textContent = options.title || profile.label || "LoRA Stack";
    const close = editorButton(documentRef, "x", "Close LoRA editor", () => overlay.remove());
    header.append(title, close);

    const toolbar = div(documentRef, "htd-lora-editor-toolbar");
    const filter = documentRef.createElement("input");
    filter.className = "htd-lora-editor-filter";
    filter.type = "text";
    filter.value = state.stack.ui.match;
    filter.placeholder = "Filter LoRAs";
    filter.title = "LoRA filter regex";
    filter.addEventListener("change", () => {
      state.stack.ui.match = filter.value;
      render();
    });
    toolbar.append(labelWrap(documentRef, "Filter", filter));

    if (profile.allowClipStrength) {
      const mode = documentRef.createElement("select");
      mode.className = "htd-lora-editor-mode";
      for (const value of ["single", "separate"]) {
        const option = documentRef.createElement("option");
        option.value = value;
        option.textContent = value === "separate" ? "Model + CLIP" : "Single";
        mode.append(option);
      }
      mode.value = state.stack.ui.show_strengths === "separate" ? "separate" : "single";
      mode.title = "Strength display mode";
      mode.addEventListener("change", () => {
        state.stack.ui.show_strengths = mode.value === "separate" ? "separate" : "single";
        state.rows = normalizeLoraEditorStack({ ...state.stack, loras: state.rows }, profile).loras;
        render();
      });
      toolbar.append(labelWrap(documentRef, "Strengths", mode));
    }

    const choices = loraEditorFilteredChoices(state.loras, state.stack.ui.match);
    const datalistId = `${dialogId}-choices`;
    const datalist = documentRef.createElement("datalist");
    datalist.id = datalistId;
    for (const choice of choices) {
      const option = documentRef.createElement("option");
      option.value = choice;
      datalist.append(option);
    }

    const addInput = documentRef.createElement("input");
    addInput.className = "htd-lora-editor-add-input";
    addInput.setAttribute("list", datalistId);
    addInput.value = state.addValue;
    addInput.placeholder = choices[0] || "LoRA name";
    addInput.title = "Choose or type a LoRA name";
    addInput.addEventListener("input", () => {
      state.addValue = addInput.value;
    });
    const add = editorButton(documentRef, "Add", "Add LoRA", () => {
      const name = String(state.addValue || choices[0] || "").trim();
      state.rows.push({
        enabled: true,
        name,
        strength_model: 1,
        strength_clip: 1,
      });
      state.addValue = "";
      render();
    });
    const refresh = editorButton(documentRef, "Refresh", "Refresh LoRA List", async () => {
      await loadLoras(true);
    });
    toolbar.append(labelWrap(documentRef, "Add", addInput), add, refresh, datalist);

    const body = div(documentRef, "htd-lora-editor-body");
    if (state.loading) {
      const loading = div(documentRef, "htd-lora-editor-empty");
      loading.textContent = "Loading LoRAs...";
      body.append(loading);
    }
    if (state.error) {
      const error = div(documentRef, "htd-lora-editor-error");
      error.textContent = state.error;
      body.append(error);
    }
    if (!state.rows.length) {
      const empty = div(documentRef, "htd-lora-editor-empty");
      empty.textContent = "No LoRAs in this stack.";
      body.append(empty);
    } else {
      for (const [index, row] of state.rows.entries()) {
        body.append(renderLoraRow(documentRef, row, index, profile, state, render, datalistId, fetcher));
      }
    }

    const footer = div(documentRef, "htd-lora-editor-footer");
    footer.append(
      editorButton(documentRef, "Clear", "Clear all LoRAs from this stack", () => {
        state.rows = [];
        render();
      }),
      editorButton(documentRef, "Cancel", "Cancel LoRA changes", () => overlay.remove()),
      editorButton(documentRef, "Save", "Save LoRA stack", () => {
        const nextStack = normalizeLoraEditorStack({
          ...state.stack,
          loras: state.rows,
        }, profile);
        options.onSave?.(nextStack);
        overlay.remove();
      }, "is-primary"),
    );
    panel.append(header, toolbar, body, footer);
  };

  async function loadLoras(force = false) {
    if (!fetcher) return;
    state.loading = true;
    state.error = "";
    render();
    try {
      state.loras = await fetchTimelineLoras({ force, fetcher });
    } catch (error) {
      state.error = error?.message || "Could not load LoRA list.";
    } finally {
      state.loading = false;
      render();
    }
  }

  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) overlay.remove();
  });
  overlay.append(panel);
  documentRef.body.append(overlay);
  render();
  if (!state.loras.length) loadLoras(false);
  return overlay;
}

function renderLoraRow(documentRef, row, index, profile, state, render, datalistId, fetcher) {
  const showSeparate = Boolean(profile.allowClipStrength) && state.stack.ui.show_strengths === "separate";
  const item = div(documentRef, "htd-lora-editor-row");
  item.classList.toggle("has-clip-strength", showSeparate);
  const enabled = documentRef.createElement("input");
  enabled.type = "checkbox";
  enabled.checked = row.enabled !== false;
  enabled.title = "Enable LoRA";
  enabled.addEventListener("change", () => {
    row.enabled = enabled.checked;
  });

  const name = documentRef.createElement("input");
  name.className = "htd-lora-editor-name";
  name.type = "text";
  name.setAttribute("list", datalistId);
  name.value = row.name ?? "";
  name.title = "LoRA name";
  name.addEventListener("change", () => {
    row.name = name.value;
  });

  const model = strengthInput(documentRef, row.strength_model, "Model strength", (value) => {
    row.strength_model = value;
    if (!showSeparate) row.strength_clip = value;
  });
  const controls = [enabled, name, model];
  if (showSeparate) {
    controls.push(strengthInput(documentRef, row.strength_clip ?? row.strength_model, "CLIP strength", (value) => {
      row.strength_clip = value;
    }));
  }
  controls.push(
    editorButton(documentRef, "Info", "Show LoRA info", () => showTimelineLoraInfoDialog(documentRef, row.name, { fetcher })),
    editorButton(documentRef, "Up", "Move LoRA up", () => {
      if (index <= 0) return;
      const [removed] = state.rows.splice(index, 1);
      state.rows.splice(index - 1, 0, removed);
      render();
    }),
    editorButton(documentRef, "Down", "Move LoRA down", () => {
      if (index >= state.rows.length - 1) return;
      const [removed] = state.rows.splice(index, 1);
      state.rows.splice(index + 1, 0, removed);
      render();
    }),
    editorButton(documentRef, "Remove", "Remove LoRA", () => {
      state.rows.splice(index, 1);
      render();
    }, "is-danger"),
  );
  item.append(...controls);
  return item;
}

export function showTimelineLoraInfoDialog(documentRef = globalThis.document, file, { fetcher = null } = {}) {
  if (!documentRef?.createElement || !documentRef.body || !file) return null;
  installLoraEditorStyles(documentRef);
  for (const dialog of documentRef.querySelectorAll?.(`.${INFO_CLASS}`) ?? []) dialog.remove();
  const overlay = documentRef.createElement("div");
  overlay.className = INFO_CLASS;
  const panel = div(documentRef, "htd-lora-info-panel");
  const title = div(documentRef, "htd-lora-info-title");
  title.textContent = file;
  const body = div(documentRef, "htd-lora-info-body");
  body.textContent = "Loading...";
  const close = editorButton(documentRef, "Close", "Close LoRA info", () => overlay.remove());
  panel.append(title, body, close);
  overlay.append(panel);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) overlay.remove();
  });
  documentRef.body.append(overlay);
  fetchTimelineLoraInfo(file, { light: false, fetcher })
    .then((info) => {
      body.replaceChildren(...loraInfoElements(documentRef, info));
    })
    .catch((error) => {
      body.textContent = error?.message || "Could not load LoRA info.";
    });
  return overlay;
}

function loraInfoElements(documentRef, info) {
  if (!info || typeof info !== "object") {
    const empty = div(documentRef, "htd-lora-info-empty");
    empty.textContent = "No LoRA metadata found.";
    return [empty];
  }
  const entries = [
    ["Name", info.name || info.file],
    ["Base Model", info.baseModel || info.base_model],
    ["Type", info.modelType || info.type],
    ["Hash", info.sha256 || info.hash],
    ["Triggers", Array.isArray(info.trainedWords) ? info.trainedWords.join(", ") : info.trainedWords],
  ].filter(([, value]) => value != null && value !== "");
  if (!entries.length) {
    const empty = div(documentRef, "htd-lora-info-empty");
    empty.textContent = "No LoRA metadata found.";
    return [empty];
  }
  return entries.map(([label, value]) => {
    const row = div(documentRef, "htd-lora-info-row");
    const key = div(documentRef, "htd-lora-info-key");
    const val = div(documentRef, "htd-lora-info-value");
    key.textContent = label;
    val.textContent = String(value);
    row.append(key, val);
    return row;
  });
}

function strengthInput(documentRef, value, title, onChange) {
  const input = documentRef.createElement("input");
  input.className = "htd-lora-editor-strength";
  input.type = "number";
  input.step = "0.05";
  input.value = String(finiteNumber(value, 1));
  input.title = title;
  input.addEventListener("change", () => onChange(finiteNumber(input.value, 1)));
  return input;
}

function labelWrap(documentRef, text, control) {
  const label = documentRef.createElement("label");
  label.className = "htd-lora-editor-field";
  const span = documentRef.createElement("span");
  span.textContent = text;
  label.append(span, control);
  return label;
}

function editorButton(documentRef, text, title, onClick, className = "") {
  const button = documentRef.createElement("button");
  button.type = "button";
  button.className = `htd-lora-editor-button${className ? ` ${className}` : ""}`;
  button.textContent = text;
  button.title = title;
  button.setAttribute("aria-label", title);
  button.addEventListener("click", onClick);
  return button;
}

function div(documentRef, className) {
  const element = documentRef.createElement("div");
  element.className = className;
  return element;
}

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

async function fetchJson(fetcher, url) {
  const response = await fetcher(url, { cache: "no-store" });
  if (!response?.ok) throw new Error(`Request failed: ${response?.status ?? "unknown"}`);
  return response.json();
}

function installLoraEditorStyles(documentRef) {
  if (!documentRef || documentRef.getElementById?.(STYLE_ID)) return;
  const style = documentRef.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .htd-lora-editor-dialog, .htd-lora-info-dialog { position: fixed; inset: 0; z-index: 10080; display: flex; align-items: center; justify-content: center; padding: 18px; box-sizing: border-box; background: rgba(8, 11, 17, 0.82); color: #d8dde8; font: 12px/1.3 system-ui, sans-serif; }
    .htd-lora-editor-panel, .htd-lora-info-panel { width: min(920px, 100%); max-height: min(760px, 96vh); min-height: 0; display: flex; flex-direction: column; border: 1px solid #465064; border-radius: 6px; background: #121925; box-shadow: 0 18px 48px rgba(0,0,0,0.5); overflow: hidden; }
    .htd-lora-info-panel { width: min(560px, 100%); padding: 10px; gap: 8px; }
    .htd-lora-editor-header, .htd-lora-editor-footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px; border-bottom: 1px solid #30394c; }
    .htd-lora-editor-footer { justify-content: flex-end; border-top: 1px solid #30394c; border-bottom: 0; }
    .htd-lora-editor-title, .htd-lora-info-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #eef2f7; font-weight: 600; }
    .htd-lora-editor-toolbar { display: flex; flex-wrap: wrap; align-items: end; gap: 7px; padding: 8px; border-bottom: 1px solid #30394c; }
    .htd-lora-editor-field { min-width: 0; display: grid; gap: 3px; color: #9ba8bd; }
    .htd-lora-editor-field input, .htd-lora-editor-field select, .htd-lora-editor-name, .htd-lora-editor-strength { height: 26px; box-sizing: border-box; border: 1px solid #465064; border-radius: 4px; background: #151c29; color: #eef2f7; padding: 0 7px; }
    .htd-lora-editor-body { min-height: 120px; overflow: auto; padding: 8px; display: grid; gap: 6px; }
    .htd-lora-editor-row { min-width: 0; display: grid; grid-template-columns: 22px minmax(180px, 1fr) 74px auto auto auto auto; align-items: center; gap: 5px; }
    .htd-lora-editor-row.has-clip-strength { grid-template-columns: 22px minmax(180px, 1fr) 74px 74px auto auto auto auto; }
    .htd-lora-editor-name { width: 100%; min-width: 0; }
    .htd-lora-editor-strength { width: 74px; }
    .htd-lora-editor-button { min-width: 26px; height: 24px; padding: 0 7px; border: 1px solid #4b5568; border-radius: 4px; background: #202633; color: #f2f5f8; cursor: pointer; white-space: nowrap; }
    .htd-lora-editor-button.is-primary { border-color: #d6b65a; background: #4b3d1e; color: #fff1b8; }
    .htd-lora-editor-button.is-danger { border-color: #8f2f36; background: #552029; color: #ffd6dc; }
    .htd-lora-editor-button:disabled { opacity: 0.45; cursor: not-allowed; }
    .htd-lora-editor-empty, .htd-lora-editor-error, .htd-lora-info-empty { color: #9ba8bd; padding: 10px 2px; }
    .htd-lora-editor-error { color: #ffd8c2; }
    .htd-lora-info-body { display: grid; gap: 5px; }
    .htd-lora-info-row { min-width: 0; display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 8px; }
    .htd-lora-info-key { color: #9ba8bd; }
    .htd-lora-info-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; color: #eef2f7; }
  `;
  documentRef.head?.append(style);
}
