import {
  ASSET_TYPE_IMAGE,
  ASSET_TYPE_VIDEO,
  SECTION_TYPE_TEXT,
} from "./schema.js";
import { resolveMediaReference } from "./media.js";
import { mediaViewUrl, thumbnailUrl } from "./media_cache.js";
import {
  closeMediaPreview,
  showMediaPreview,
} from "./media_preview.js";
import { htdScrollbarBlock, htdTokenBlock } from "./design_tokens.js";
import { setupOverlayDialog } from "./dialog.js";

const ROUTE_PREFIX = "/helto_director/prompt_optimizer";

export function showPromptOptimizer(options) {
  const documentRef = options.documentRef ?? globalThis.document;
  installPromptOptimizerStyles(documentRef);
  closePromptOptimizer(documentRef);

  const timelineRows = promptOptimizerRows(options.timeline, Boolean(options.privacyMode));
  const overlay = documentRef.createElement("div");
  overlay.className = `htd-prompt-optimizer-dialog${options.privacyMode ? " privacy-mode" : ""}`;
  overlay.innerHTML = `
    <div class="htd-prompt-optimizer-panel">
      <h3>LTX Prompt Optimizer</h3>
      <div class="htd-prompt-optimizer-controls">
        <select class="model" title="Local caption/optimizer model"></select>
        <div class="mode" role="group" aria-label="Prompt mode">
          <button class="active" type="button" data-mode="sfw">SFW</button>
          <button type="button" data-mode="nsfw">NSFW</button>
        </div>
        <button class="edit-ollama icon" type="button" title="Edit Ollama settings" aria-label="Edit Ollama settings">${ICONS.settings}</button>
        <button class="edit-template icon" type="button" title="Edit prompt template" aria-label="Edit prompt template">${ICONS.text}</button>
        <button class="generate icon" type="button" title="Generate timeline prompts" aria-label="Generate timeline prompts">${ICONS.timeline}</button>
      </div>
      <div class="htd-prompt-auth-row">
        <span class="auth-status">HF token: checking...</span>
        <input class="hf-token" type="password" autocomplete="off" placeholder="hf_... access token">
        <button class="save-token icon" type="button" title="Save Hugging Face token" aria-label="Save Hugging Face token">${ICONS.key}</button>
        <button class="clear-token icon" type="button" title="Clear Hugging Face token" aria-label="Clear Hugging Face token">${ICONS.clear}</button>
      </div>
      <div class="htd-ollama-settings-editor">
        <div class="htd-ollama-settings-grid">
          <label class="ollama-field">
            <span>Model</span>
            <select class="ollama-model" title="Installed Ollama model"></select>
          </label>
          <label class="ollama-field">
            <span>Base URL</span>
            <input class="ollama-base-url" type="url" spellcheck="false" autocomplete="off" title="Ollama API base URL">
          </label>
          <label class="ollama-field">
            <span>Keep loaded</span>
            <input class="ollama-keep-alive" type="number" min="0" step="1" title="Seconds to keep the Ollama model in memory after this optimizer session; 0 releases it after the final generated prompt">
          </label>
          <label class="ollama-field">
            <span>Max tokens</span>
            <input class="ollama-num-predict" type="number" min="1" step="1" title="Ollama num_predict">
          </label>
          <label class="ollama-field">
            <span>Context</span>
            <input class="ollama-num-ctx" type="number" min="0" step="1" title="Ollama num_ctx">
          </label>
          <label class="ollama-field">
            <span>Temperature</span>
            <input class="ollama-temperature" type="number" min="0" max="2" step="0.05" title="Ollama temperature">
          </label>
          <label class="ollama-field">
            <span>Top P</span>
            <input class="ollama-top-p" type="number" min="0" max="1" step="0.01" title="Ollama top_p">
          </label>
          <label class="ollama-field">
            <span>Top K</span>
            <input class="ollama-top-k" type="number" min="0" step="1" title="Ollama top_k">
          </label>
          <label class="ollama-field">
            <span>Repeat</span>
            <input class="ollama-repeat-penalty" type="number" min="0" step="0.01" title="Ollama repeat_penalty">
          </label>
        </div>
        <div class="htd-ollama-settings-toolbar">
          <span class="ollama-status">Ollama: checking...</span>
          <button class="save-ollama icon" type="button" title="Save Ollama settings" aria-label="Save Ollama settings">${ICONS.save}</button>
          <button class="reset-ollama icon" type="button" title="Reset Ollama settings" aria-label="Reset Ollama settings">${ICONS.reset}</button>
        </div>
      </div>
      <div class="htd-prompt-template-editor">
        <textarea class="prompt-template" spellcheck="false"></textarea>
        <div class="htd-prompt-template-toolbar">
          <span class="prompt-template-status">Default prompt template.</span>
          <button class="save-template icon" type="button" title="Save prompt template" aria-label="Save prompt template">${ICONS.save}</button>
          <button class="reset-template icon" type="button" title="Reset default prompt template" aria-label="Reset default prompt template">${ICONS.reset}</button>
        </div>
      </div>
      <div class="status"></div>
      <div class="progress" aria-hidden="true">
        <div class="progress-track"><div class="progress-bar"></div></div>
        <div class="progress-text"></div>
      </div>
      <div class="grid"></div>
      <div class="actions">
        <button class="cancel" type="button">Cancel</button>
        <button class="apply" type="button">Apply</button>
      </div>
    </div>`;

  const panel = overlay.querySelector(".htd-prompt-optimizer-panel");
  const modelSelect = overlay.querySelector(".model");
  const modeButtons = [...overlay.querySelectorAll(".mode button")];
  const editOllamaBtn = overlay.querySelector(".edit-ollama");
  const ollamaSettingsEditor = overlay.querySelector(".htd-ollama-settings-editor");
  const ollamaModelSelect = overlay.querySelector(".ollama-model");
  const ollamaBaseUrlInput = overlay.querySelector(".ollama-base-url");
  const ollamaKeepAliveInput = overlay.querySelector(".ollama-keep-alive");
  const ollamaNumPredictInput = overlay.querySelector(".ollama-num-predict");
  const ollamaNumCtxInput = overlay.querySelector(".ollama-num-ctx");
  const ollamaTemperatureInput = overlay.querySelector(".ollama-temperature");
  const ollamaTopPInput = overlay.querySelector(".ollama-top-p");
  const ollamaTopKInput = overlay.querySelector(".ollama-top-k");
  const ollamaRepeatPenaltyInput = overlay.querySelector(".ollama-repeat-penalty");
  const ollamaStatusEl = overlay.querySelector(".ollama-status");
  const saveOllamaBtn = overlay.querySelector(".save-ollama");
  const resetOllamaBtn = overlay.querySelector(".reset-ollama");
  const editTemplateBtn = overlay.querySelector(".edit-template");
  const promptTemplateEditor = overlay.querySelector(".htd-prompt-template-editor");
  const promptTemplateInput = overlay.querySelector(".prompt-template");
  const promptTemplateStatus = overlay.querySelector(".prompt-template-status");
  const saveTemplateBtn = overlay.querySelector(".save-template");
  const resetTemplateBtn = overlay.querySelector(".reset-template");
  const authStatusEl = overlay.querySelector(".auth-status");
  const hfTokenInput = overlay.querySelector(".hf-token");
  const saveTokenBtn = overlay.querySelector(".save-token");
  const clearTokenBtn = overlay.querySelector(".clear-token");
  const generateBtn = overlay.querySelector(".generate");
  const statusEl = overlay.querySelector(".status");
  const progressWrap = overlay.querySelector(".progress");
  const progressBar = overlay.querySelector(".progress-bar");
  const progressText = overlay.querySelector(".progress-text");
  const grid = overlay.querySelector(".grid");
  const cancelBtn = overlay.querySelector(".cancel");
  const applyBtn = overlay.querySelector(".apply");
  const rowState = new Map();
  const abortController = new AbortController();
  let mode = "sfw";
  let busy = false;
  let closed = false;
  let loadedModelAlias = "";
  let loadedModelBackend = "";
  let modelStatuses = [];

  const optimizerFetchOptions = (next = {}) => ({ ...next, signal: abortController.signal });
  const ollamaInputs = [
    ollamaModelSelect,
    ollamaBaseUrlInput,
    ollamaKeepAliveInput,
    ollamaNumPredictInput,
    ollamaNumCtxInput,
    ollamaTemperatureInput,
    ollamaTopPInput,
    ollamaTopKInput,
    ollamaRepeatPenaltyInput,
    saveOllamaBtn,
    resetOllamaBtn,
  ];
  const isClosed = () => closed || abortController.signal.aborted || !overlay.isConnected;
  const setStatus = (message) => {
    statusEl.textContent = message || "";
  };
  const selectedModelStatus = () => modelStatuses.find((model) => model.alias === modelSelect.value) || null;
  const selectedModelBackend = () => selectedModelStatus()?.backend || "";
  const setBusy = (value) => {
    busy = Boolean(value);
    for (const input of [
      modelSelect,
      ...modeButtons,
      editOllamaBtn,
      ...ollamaInputs,
      editTemplateBtn,
      promptTemplateInput,
      saveTemplateBtn,
      resetTemplateBtn,
      hfTokenInput,
      saveTokenBtn,
      clearTokenBtn,
      generateBtn,
      applyBtn,
    ]) {
      input.disabled = busy;
    }
    generateBtn.disabled = busy || !timelineRows.length;
    syncOllamaVisibility();
  };
  const close = () => {
    closePromptOptimizer(documentRef);
    dialog.restoreFocus();
  };
  const dialog = setupOverlayDialog(overlay, {
    documentRef,
    label: "Prompt optimizer",
    onRequestClose: () => close(),
  });
  overlay._htdPromptOptimizerCleanup = () => {
    if (closed) return;
    closed = true;
    abortController.abort();
    if (loadedModelBackend !== "ollama") unloadPromptOptimizerModel(loadedModelAlias);
    options.onClose?.();
  };

  const syncOllamaVisibility = () => {
    const isOllama = selectedModelBackend() === "ollama";
    editOllamaBtn.hidden = !isOllama;
    editOllamaBtn.disabled = busy || !isOllama;
    ollamaSettingsEditor.classList.toggle("is-provider-active", isOllama);
    if (!isOllama) ollamaSettingsEditor.classList.remove("is-open");
  };

  const populateOllamaSettings = (settings = {}, status = {}) => {
    const ollama = settings || {};
    const models = Array.isArray(status.models) ? status.models : [];
    const configured = String(ollama.model || status.configured_model || "").trim();
    const active = configured || (models.length === 1 ? models[0] : "");
    ollamaModelSelect.innerHTML = "";
    if (models.length) {
      if (!configured && models.length > 1) {
        const placeholder = documentRef.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Choose model";
        ollamaModelSelect.append(placeholder);
      }
      for (const name of models) {
        const option = documentRef.createElement("option");
        option.value = name;
        option.textContent = name;
        ollamaModelSelect.append(option);
      }
    } else {
      const option = documentRef.createElement("option");
      option.value = configured;
      option.textContent = configured || "No local models";
      ollamaModelSelect.append(option);
    }
    ollamaModelSelect.value = [...ollamaModelSelect.options].some((option) => option.value === active)
      ? active
      : "";
    ollamaBaseUrlInput.value = ollama.base_url || "http://127.0.0.1:11434";
    ollamaKeepAliveInput.value = numberInputValue(ollama.keep_alive_seconds, 0);
    ollamaNumPredictInput.value = numberInputValue(ollama.num_predict, 2048);
    ollamaNumCtxInput.value = numberInputValue(ollama.num_ctx, 4096);
    ollamaTemperatureInput.value = numberInputValue(ollama.temperature, 0.2);
    ollamaTopPInput.value = numberInputValue(ollama.top_p, 0.95);
    ollamaTopKInput.value = numberInputValue(ollama.top_k, 40);
    ollamaRepeatPenaltyInput.value = numberInputValue(ollama.repeat_penalty, 1.05);
    const state = String(status.status || "").replace(/_/g, " ");
    const suffix = status.active_model ? `: ${status.active_model}` : (state ? `: ${state}` : "");
    const vision = ollamaVisionLabel(status);
    ollamaStatusEl.textContent = `Ollama${suffix}${status.active_model && vision ? ` (${vision})` : ""}`;
    ollamaStatusEl.title = ollamaVisionTitle(status) || status.error || status.base_url || "";
  };

  const currentOllamaSettingsPayload = () => ({
    base_url: ollamaBaseUrlInput.value || "",
    model: ollamaModelSelect.value || "",
    keep_alive_seconds: ollamaKeepAliveInput.value || 0,
    num_predict: ollamaNumPredictInput.value || 2048,
    num_ctx: ollamaNumCtxInput.value || 4096,
    temperature: ollamaTemperatureInput.value || 0.2,
    top_p: ollamaTopPInput.value || 0.95,
    top_k: ollamaTopKInput.value || 40,
    repeat_penalty: ollamaRepeatPenaltyInput.value || 1.05,
  });

  const updateProgressBar = (progress = {}, visible = true) => {
    const percentValue = Number(progress.percent);
    const percent = Number.isFinite(percentValue) ? Math.max(0, Math.min(100, percentValue)) : 0;
    progressWrap.classList.toggle("visible", visible);
    progressWrap.setAttribute("aria-hidden", visible ? "false" : "true");
    progressBar.style.width = `${percent}%`;
    const parts = [];
    if (Number.isFinite(percentValue)) parts.push(`${Math.round(percent)}%`);
    const eta = formatEta(progress.eta_seconds);
    if (eta) parts.push(eta);
    if (progress.estimated && progress.phase === "generating") parts.push("estimated");
    if (progress.phase === "downloading") {
      const currentBytes = formatBytes(progress.download_current_bytes);
      const totalBytes = formatBytes(progress.download_total_bytes);
      if (currentBytes && totalBytes) parts.push(`${currentBytes} / ${totalBytes}`);
      if (progress.download_file_index && progress.download_file_total) {
        parts.push(`file ${progress.download_file_index}/${progress.download_file_total}`);
      }
    }
    progressText.textContent = parts.join(" · ");
  };

  const renderRows = () => {
    grid.innerHTML = "";
    rowState.clear();
    if (!timelineRows.length) {
      const empty = documentRef.createElement("div");
      empty.className = "empty";
      empty.textContent = "No timeline segments are available to optimize.";
      grid.append(empty);
      return;
    }
    for (const item of timelineRows) {
      const row = documentRef.createElement("div");
      row.className = "row";
      row.dataset.itemId = item.id;

      const check = documentRef.createElement("input");
      check.type = "checkbox";
      check.checked = true;
      check.title = "Optimize this segment";

      const thumb = documentRef.createElement("div");
      thumb.className = "thumb";
      thumb.title = item.mediaPreviewUrl ? "Ctrl-click for large preview" : "";
      if (item.thumbnailUrl) {
        const img = documentRef.createElement("img");
        img.src = item.thumbnailUrl;
        img.alt = "";
        thumb.append(img);
      } else {
        thumb.textContent = item.type === SECTION_TYPE_TEXT ? "Text" : item.type;
      }
      if (item.mediaPreviewUrl) {
        thumb.addEventListener("click", (event) => {
          if (!event.ctrlKey) return;
          event.preventDefault();
          event.stopPropagation();
          if (options.privacyMode && !panel.matches(":hover")) return;
          showMediaPreview(documentRef, {
            type: item.type,
            url: item.mediaPreviewUrl,
            caption: item.mediaPreviewCaption,
          });
        });
      }

      const direction = textarea(documentRef, item.prompt, "Direction or existing segment prompt...");
      const generated = textarea(documentRef, item.prompt, "Generated prompt will appear here...");
      const directionWrap = field(documentRef, `${item.order + 1}. ${item.label}`, direction);
      const generatedWrap = field(documentRef, "Optimized LTX prompt", generated);

      row.append(check, thumb, directionWrap, generatedWrap);
      grid.append(row);
      rowState.set(item.id, { item, check, direction, generated });
    }
  };

  const selectedPayloadRows = () => timelineRows.map((item) => {
    const state = rowState.get(item.id);
    return {
      id: item.id,
      order: item.order,
      type: item.type,
      start: item.start,
      length: item.length,
      selected: Boolean(state?.check.checked),
      direction: state?.direction.value || "",
      prompt: state?.direction.value || "",
      label: item.label || "",
      mediaPath: item.mediaPath || "",
      path: item.mediaPath || "",
      imageFile: item.mediaFile || "",
      imageFolderAlias: item.mediaFolderAlias || "",
      mediaFile: item.mediaFile || "",
      mediaFolderAlias: item.mediaFolderAlias || "",
    };
  });

  const refreshOptimizerStatus = async () => {
    const settings = await fetchJson(`${ROUTE_PREFIX}/settings`, optimizerFetchOptions());
    hfTokenInput.value = "";
    if (settings.tokenConfigured) {
      authStatusEl.textContent = "HF token: saved locally.";
    } else if (settings.envTokenAvailable) {
      authStatusEl.textContent = "HF token: using environment token.";
    } else {
      authStatusEl.textContent = "HF token: missing, gated models may fail.";
    }
    authStatusEl.title = settings.configPath || "";
    promptTemplateInput.value = settings.promptTemplate || settings.defaultPromptTemplate || "";
    promptTemplateStatus.textContent = settings.promptTemplateConfigured
      ? "Custom prompt template saved locally."
      : "Using the default motion-only prompt template.";
    const modelData = await loadPromptOptimizerModels(
      options.node,
      modelSelect,
      statusEl,
      optimizerFetchOptions(),
      () => syncOllamaVisibility(),
    );
    modelStatuses = modelData.models || [];
    const ollamaModel = modelStatuses.find((model) => model.backend === "ollama");
    populateOllamaSettings(settings.ollamaSettings, ollamaModel?.ollama || {});
    syncOllamaVisibility();
  };

  const runGenerate = async () => {
    if (isClosed()) return;
    if (await fetchComfyQueueRunning(optimizerFetchOptions())) {
      setStatus("Workflow is running; prompt generation is paused.");
      return;
    }
    setBusy(true);
    setStatus("Preparing selected items...");
    updateProgressBar({ percent: 0 }, true);
    try {
      const segments = selectedPayloadRows();
      if (!segments.some((segment) => segment.selected)) {
        setStatus("Select at least one item to optimize.");
        return;
      }
      setStatus("Starting prompt optimization...");
      loadedModelAlias = modelSelect.value;
      loadedModelBackend = selectedModelBackend();
      if (loadedModelBackend === "ollama") {
        await postOptimizerSettings({ ollama: currentOllamaSettingsPayload() });
      }
      const started = await fetchJson(`${ROUTE_PREFIX}/optimize/start`, optimizerFetchOptions({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: modelSelect.value,
          mode,
          duration_frames: durationFrames(options.timeline),
          frame_rate: frameRate(options.timeline),
          segments,
          references: [],
        }),
      }));
      let data = null;
      while (!isClosed()) {
        data = await fetchJson(`${ROUTE_PREFIX}/optimize/status?job_id=${encodeURIComponent(started.job_id)}`, optimizerFetchOptions());
        const progress = data.progress || {};
        const suffix = progress.current != null && progress.total != null ? ` (${progress.current} / ${progress.total})` : "";
        setStatus(`${data.message || "Working..."}${suffix}`);
        updateProgressBar(progress, true);
        if (data.state === "completed") break;
        if (data.state === "failed") throw new Error(data.error || data.message || "Prompt optimization failed.");
        await sleep(750, abortController.signal);
      }
      for (const result of data?.results || []) {
        const state = rowState.get(result.id);
        if (state) state.generated.value = result.prompt || "";
      }
      setStatus(`Generated ${data?.results?.length || 0} item${(data?.results?.length || 0) === 1 ? "" : "s"}.`);
      updateProgressBar({ ...(data?.progress || {}), percent: 100, eta_seconds: 0, estimated: false }, true);
    } catch (error) {
      if (error?.name !== "AbortError") setStatus(error.message);
    } finally {
      if (!isClosed()) setBusy(false);
    }
  };

  modeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      mode = button.dataset.mode || "sfw";
      modeButtons.forEach((other) => other.classList.toggle("active", other === button));
    });
  });
  editTemplateBtn.addEventListener("click", () => promptTemplateEditor.classList.toggle("is-open"));
  editOllamaBtn.addEventListener("click", () => ollamaSettingsEditor.classList.toggle("is-open"));
  saveTokenBtn.addEventListener("click", () => saveSettings({ hf_token: hfTokenInput.value || "" }));
  clearTokenBtn.addEventListener("click", () => saveSettings({ clear: true }));
  saveOllamaBtn.addEventListener("click", () => saveSettings({ ollama: currentOllamaSettingsPayload() }));
  resetOllamaBtn.addEventListener("click", () => saveSettings({ reset_ollama_settings: true }));
  saveTemplateBtn.addEventListener("click", () => saveSettings({ prompt_template: promptTemplateInput.value || "" }));
  resetTemplateBtn.addEventListener("click", () => saveSettings({ reset_prompt_template: true }));
  generateBtn.addEventListener("click", runGenerate);
  applyBtn.addEventListener("click", () => {
    const updates = {};
    for (const state of rowState.values()) updates[state.item.id] = state.generated.value || "";
    options.onApply?.(updates);
    close();
  });
  cancelBtn.addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  panel.addEventListener("pointerdown", (event) => event.stopPropagation());
  panel.addEventListener("click", (event) => event.stopPropagation());
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
    event.stopPropagation();
  });
  documentRef.body.append(overlay);
  dialog.focusInitial(".model");
  renderRows();
  setBusy(false);
  refreshOptimizerStatus().catch((error) => setStatus(error.message));

  async function saveSettings(payload) {
    setBusy(true);
    try {
      await postOptimizerSettings(payload);
      await refreshOptimizerStatus();
    } catch (error) {
      setStatus(error.message);
    } finally {
      if (!isClosed()) setBusy(false);
    }
  }

  async function postOptimizerSettings(payload) {
    return fetchJson(`${ROUTE_PREFIX}/settings`, optimizerFetchOptions({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }));
  }
}

export function closePromptOptimizer(documentRef = globalThis.document) {
  const dialog = documentRef.querySelector(".htd-prompt-optimizer-dialog");
  dialog?._htdPromptOptimizerCleanup?.();
  dialog?.remove();
  closeMediaPreview(documentRef);
}

export function promptOptimizerRows(timeline, privacyMode = false) {
  const fps = frameRate(timeline);
  return [...(timeline?.director_track?.sections || [])]
    .filter((section) => section && section.type)
    .sort((a, b) => Number(a.start_time || 0) - Number(b.start_time || 0))
    .map((section, order) => {
      const asset = mediaAssetForSection(timeline, section);
      const start = Math.round(Number(section.start_time || 0) * fps);
      const end = Math.round(Number(section.end_time || section.start_time || 0) * fps);
      return {
        id: section.item_id,
        order,
        type: section.type,
        start,
        length: Math.max(1, end - start),
        prompt: section.prompt || "",
        mediaPath: asset?.path || asset?.file_path || "",
        mediaFile: asset?.metadata?.browser_filename || asset?.name || "",
        mediaFolderAlias: asset?.metadata?.browser_alias || "",
        thumbnailUrl: thumbnailUrlForAsset(asset, privacyMode),
        mediaPreviewUrl: mediaViewUrlForAsset(asset),
        mediaPreviewCaption: mediaCaptionForAsset(asset, section),
        label: asset?.name || asset?.path || (section.type === SECTION_TYPE_TEXT ? "Text segment" : `${section.type} segment`),
      };
    });
}

function thumbnailUrlForAsset(asset, privacyMode = false) {
  if (!asset?.path || (asset.type !== ASSET_TYPE_IMAGE && asset.type !== ASSET_TYPE_VIDEO)) return "";
  return thumbnailUrl(asset, 320, privacyMode);
}

function mediaViewUrlForAsset(asset) {
  if (!asset?.path || (asset.type !== ASSET_TYPE_IMAGE && asset.type !== ASSET_TYPE_VIDEO)) return "";
  return mediaViewUrl(asset);
}

function mediaCaptionForAsset(asset, section) {
  return asset?.name || asset?.metadata?.browser_filename || asset?.path || `${section.type} segment`;
}

async function loadPromptOptimizerModels(node, selectEl, statusEl, fetchOptions = undefined, onModelChange = null) {
  const data = await fetchJson(`${ROUTE_PREFIX}/models`, fetchOptions);
  const preferred = node?.properties?.helto_prompt_optimizer_model || "qwen3_vl_4b_fast";
  selectEl.innerHTML = "";
  for (const model of data.models || []) {
    const option = document.createElement("option");
    option.value = model.alias;
    const state = model.status === "ready" || model.status === "downloaded" ? "ready" : String(model.status || "").replace(/_/g, " ");
    const label = model.display_name || model.alias;
    const ollamaVision = model.backend === "ollama" ? ollamaVisionLabel(model.ollama || {}) : "";
    const detail = model.backend === "ollama" && model.active_model
      ? [model.active_model, ollamaVision].filter(Boolean).join(", ")
      : state;
    option.textContent = `${label} (${detail})`;
    option.title = model.missing_dependencies?.length
      ? `Missing: ${model.missing_dependencies.join(", ")}`
      : (model.backend === "ollama" ? ollamaVisionTitle(model.ollama || {}) || model.ollama?.base_url || model.repo_id : model.repo_id);
    selectEl.append(option);
  }
  if ([...selectEl.options].some((option) => option.value === preferred)) selectEl.value = preferred;
  const updateStatus = () => {
    const model = (data.models || []).find((item) => item.alias === selectEl.value);
    if (!model) return;
    statusEl.title = "";
    if (model.missing_dependencies?.length) {
      statusEl.textContent = `Missing optional packages: ${model.missing_dependencies.join(", ")}`;
    } else if (model.backend === "ollama") {
      if (model.status === "ready") {
        if (model.ollama?.supports_vision === false) {
          statusEl.textContent = `Ollama ready: ${model.active_model}, but this model does not advertise vision support.`;
          statusEl.title = ollamaVisionTitle(model.ollama);
        } else if (model.ollama?.supports_vision === true) {
          statusEl.textContent = `Ollama ready: ${model.active_model} with vision support.`;
        } else {
          statusEl.textContent = `Ollama ready: ${model.active_model}; vision capability could not be checked.`;
          statusEl.title = ollamaVisionTitle(model.ollama || {});
        }
      } else if (model.status === "choose_model") {
        statusEl.textContent = "Choose an Ollama model in settings.";
      } else if (model.status === "unavailable") {
        statusEl.textContent = model.ollama?.error || "Ollama is unavailable.";
      } else {
        statusEl.textContent = `Ollama status: ${String(model.status || "unknown").replace(/_/g, " ")}.`;
      }
    } else if (model.downloaded || model.backend === "fallback") {
      statusEl.textContent = `${model.alias} is ready.`;
    } else {
      statusEl.textContent = `${model.alias} will auto-download on Generate.`;
    }
  };
  selectEl.onchange = () => {
    node.properties = node.properties || {};
    node.properties.helto_prompt_optimizer_model = selectEl.value;
    updateStatus();
    onModelChange?.((data.models || []).find((item) => item.alias === selectEl.value) || null);
  };
  updateStatus();
  onModelChange?.((data.models || []).find((item) => item.alias === selectEl.value) || null);
  return data;
}

function ollamaVisionLabel(status = {}) {
  if (status.supports_vision === true) return "vision";
  if (status.supports_vision === false) return "no vision";
  if (status.active_model || status.capability_error) return "vision unknown";
  return "";
}

function ollamaVisionTitle(status = {}) {
  if (status.supports_vision === false) {
    return "This Ollama model does not advertise vision support; choose a model whose capabilities include 'vision' for image and video timeline sections.";
  }
  if (status.capability_error) return `Could not check Ollama vision capability: ${status.capability_error}`;
  return "";
}

function numberInputValue(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? String(value) : String(fallback);
}

function unloadPromptOptimizerModel(alias) {
  if (!alias) return;
  fetch(`${ROUTE_PREFIX}/models/unload`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: alias }),
  }).catch(() => {});
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
  if (!response.ok || data.ok === false || data.error) {
    throw new Error(data.error || response.statusText || `HTTP ${response.status}`);
  }
  return data;
}

async function fetchComfyQueueRunning(options) {
  try {
    const response = await fetch("/queue", options);
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    const running = Array.isArray(data.queue_running) ? data.queue_running : [];
    return running.length > 0;
  } catch {
    return false;
  }
}

function mediaAssetForSection(timeline, section) {
  if (section.type === ASSET_TYPE_IMAGE) return resolveMediaReference(timeline, section.image);
  if (section.type === ASSET_TYPE_VIDEO) return resolveMediaReference(timeline, section.video);
  return null;
}

function field(documentRef, labelText, input) {
  const wrap = documentRef.createElement("label");
  wrap.className = "field";
  const label = documentRef.createElement("span");
  label.textContent = labelText;
  wrap.append(label, input);
  return wrap;
}

function textarea(documentRef, value, placeholder) {
  const input = documentRef.createElement("textarea");
  input.value = value || "";
  input.placeholder = placeholder;
  return input;
}

function durationFrames(timeline) {
  return Math.max(1, Math.round(Number(timeline?.project?.duration_seconds || 1) * frameRate(timeline)));
}

function frameRate(timeline) {
  return Math.max(1, Number(timeline?.project?.frame_rate || 24));
}

function formatEta(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0.5) return "";
  if (value < 60) return `about ${Math.ceil(value)}s left`;
  return `about ${Math.ceil(value / 60)}m left`;
}

function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let scaled = value;
  let unitIndex = 0;
  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024;
    unitIndex += 1;
  }
  const decimals = scaled >= 10 || unitIndex === 0 ? 0 : 1;
  return `${scaled.toFixed(decimals)} ${units[unitIndex]}`;
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const timeout = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timeout);
      reject(abortError());
    }, { once: true });
  });
}

function abortError() {
  const error = new Error("Prompt optimizer closed.");
  error.name = "AbortError";
  return error;
}

function installPromptOptimizerStyles(documentRef) {
  if (documentRef.getElementById("helto-director-prompt-optimizer-styles")) return;
  const style = documentRef.createElement("style");
  style.id = "helto-director-prompt-optimizer-styles";
  style.textContent = `
    ${htdTokenBlock(".htd-prompt-optimizer-dialog")}
    .htd-prompt-optimizer-dialog { position: fixed; inset: 0; z-index: 10000; display: flex; align-items: center; justify-content: center; background: var(--htd-overlay); backdrop-filter: blur(4px); color: var(--htd-text-dim); font: var(--htd-font-size) / var(--htd-line) var(--htd-font-sans); -webkit-font-smoothing: antialiased; }
    .htd-prompt-optimizer-panel { width: min(980px, calc(100vw - 28px)); max-height: min(760px, calc(100vh - 28px)); display: flex; flex-direction: column; gap: 8px; background: linear-gradient(135deg, var(--htd-modal-from), var(--htd-modal-to)); border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-lg); padding: 10px; box-shadow: var(--htd-shadow-pop); box-sizing: border-box; }
    .htd-prompt-optimizer-panel h3 { margin: 0; font-size: 14px; font-weight: 700; color: var(--htd-text); }
    .htd-prompt-optimizer-controls { display: grid; grid-template-columns: minmax(180px, 1fr) 150px repeat(3, 32px); gap: 8px; align-items: center; }
    .htd-prompt-optimizer-panel button, .htd-prompt-optimizer-panel select, .htd-prompt-optimizer-panel input, .htd-prompt-optimizer-panel textarea { background: var(--htd-surface-2); color: var(--htd-text); border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); box-sizing: border-box; font: inherit; }
    .htd-prompt-optimizer-panel button { cursor: pointer; background: linear-gradient(180deg, var(--htd-surface-3), var(--htd-surface-2)); transition: background var(--htd-transition), border-color var(--htd-transition), color var(--htd-transition); }
    .htd-prompt-optimizer-panel button:hover { background: linear-gradient(180deg, var(--htd-surface-hover), var(--htd-surface-3)); border-color: var(--htd-border-hover); color: var(--htd-text); }
    .htd-prompt-optimizer-panel button:focus-visible, .htd-prompt-optimizer-panel select:focus-visible, .htd-prompt-optimizer-panel input:focus-visible, .htd-prompt-optimizer-panel textarea:focus-visible { outline: none; border-color: var(--htd-focus); box-shadow: var(--htd-ring); }
    .htd-prompt-optimizer-panel button:disabled, .htd-prompt-optimizer-panel select:disabled, .htd-prompt-optimizer-panel input:disabled, .htd-prompt-optimizer-panel textarea:disabled { opacity: .48; cursor: not-allowed; }
    .htd-prompt-optimizer-panel select, .htd-prompt-optimizer-panel input { height: 32px; min-width: 0; padding: 0 8px; }
    .htd-prompt-optimizer-panel .icon { width: 32px; height: 32px; padding: 6px; display: inline-flex; align-items: center; justify-content: center; }
    .htd-prompt-optimizer-panel .icon svg { width: 17px; height: 17px; stroke: currentColor; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .htd-prompt-optimizer-panel .mode { display: flex; padding: 2px; background: var(--htd-bg); border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); }
    .htd-prompt-optimizer-panel .mode button { flex: 1; height: 26px; border: 0; background: transparent; color: var(--htd-text-dim); border-radius: 3px; }
    .htd-prompt-optimizer-panel .mode button:hover { background: transparent; color: var(--htd-text); }
    .htd-prompt-optimizer-panel .mode button.active { background: linear-gradient(180deg, #4f3a2a, #3d2d20); color: var(--htd-accent-strong); box-shadow: inset 0 0 0 1px var(--htd-accent-hairline); }
    .htd-prompt-auth-row { display: grid; grid-template-columns: auto minmax(170px, 1fr) auto auto; gap: 8px; align-items: center; padding: 7px; background: var(--htd-surface-2); border: 1px solid var(--htd-border); border-radius: var(--htd-radius-sm); }
    .htd-prompt-auth-row span, .htd-prompt-template-toolbar span, .htd-ollama-settings-toolbar span, .htd-prompt-optimizer-panel .status, .htd-prompt-optimizer-panel .progress-text { color: var(--htd-text-dim); font-size: 11px; }
    .htd-ollama-settings-editor { display: none; padding: 8px; background: var(--htd-surface-2); border: 1px solid var(--htd-border); border-radius: var(--htd-radius-sm); }
    .htd-ollama-settings-editor.is-provider-active.is-open { display: grid; gap: 8px; }
    .htd-ollama-settings-grid { display: grid; grid-template-columns: repeat(3, minmax(120px, 1fr)); gap: 8px; }
    .htd-ollama-settings-editor .ollama-field { display: grid; gap: 4px; min-width: 0; }
    .htd-ollama-settings-editor .ollama-field span { color: var(--htd-text-faint); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }
    .htd-ollama-settings-editor .ollama-field input, .htd-ollama-settings-editor .ollama-field select { width: 100%; height: 28px; min-width: 0; font-variant-numeric: tabular-nums; }
    .htd-ollama-settings-toolbar { display: flex; gap: 8px; align-items: center; }
    .htd-ollama-settings-toolbar span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .htd-prompt-template-editor { display: none; padding: 8px; background: var(--htd-surface-2); border: 1px solid var(--htd-border); border-radius: var(--htd-radius-sm); }
    .htd-prompt-template-editor.is-open { display: block; }
    .htd-prompt-template-editor textarea { width: 100%; height: 150px; min-height: 110px; resize: vertical; padding: 7px; }
    .htd-prompt-template-toolbar { display: flex; gap: 8px; align-items: center; margin-top: 7px; }
    .htd-prompt-template-toolbar span { flex: 1; min-width: 0; }
    .htd-prompt-optimizer-panel .progress { display: none; gap: 4px; }
    .htd-prompt-optimizer-panel .progress.visible { display: grid; }
    .htd-prompt-optimizer-panel .progress-track { height: 5px; overflow: hidden; border-radius: 999px; background: var(--htd-bg); }
    .htd-prompt-optimizer-panel .progress-bar { width: 0%; height: 100%; background: var(--htd-accent); transition: width .18s ease; }
    .htd-prompt-optimizer-panel .grid { min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 8px; padding: 2px; }
    ${htdScrollbarBlock(".htd-prompt-optimizer-panel .grid")}
    .htd-prompt-optimizer-panel .row { display: grid; grid-template-columns: 28px 96px minmax(210px, 1fr) minmax(260px, 1.25fr); gap: 8px; align-items: start; padding: 8px; background: var(--htd-surface-2); border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); }
    .htd-prompt-optimizer-panel .thumb { width: 96px; height: 96px; min-width: 96px; align-self: center; display: flex; align-items: center; justify-content: center; border: 1px solid var(--htd-border); border-radius: var(--htd-radius-sm); color: var(--htd-text-dim); background: var(--htd-media-bg); overflow: hidden; }
    .htd-prompt-optimizer-panel .thumb img { width: 100%; height: 100%; object-fit: contain; display: block; }
    .htd-prompt-optimizer-panel .field { display: grid; gap: 4px; min-width: 0; }
    .htd-prompt-optimizer-panel .field span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--htd-text-dim); font-size: 11px; }
    .htd-prompt-optimizer-panel .field textarea { width: 100%; height: 96px; min-height: 72px; resize: vertical; padding: 7px; line-height: 1.35; }
    /* Privacy mask: keep the thumb cover opaque/dark and field text unreadable until the panel is hovered. */
    .htd-prompt-optimizer-dialog.privacy-mode .thumb { color: transparent; background: var(--htd-media-bg); }
    .htd-prompt-optimizer-dialog.privacy-mode .thumb img { opacity: 0; }
    .htd-prompt-optimizer-dialog.privacy-mode .grid .field span,
    .htd-prompt-optimizer-dialog.privacy-mode .grid .field textarea,
    .htd-prompt-optimizer-dialog.privacy-mode .status,
    .htd-prompt-optimizer-dialog.privacy-mode .progress-text { color: transparent; -webkit-text-fill-color: transparent; text-shadow: none; }
    .htd-prompt-optimizer-dialog.privacy-mode .grid .field textarea::placeholder { color: transparent; }
    .htd-prompt-optimizer-dialog.privacy-mode .htd-prompt-optimizer-panel:hover .thumb { color: var(--htd-text-dim); }
    .htd-prompt-optimizer-dialog.privacy-mode .htd-prompt-optimizer-panel:hover .thumb img { opacity: 1; }
    .htd-prompt-optimizer-dialog.privacy-mode .htd-prompt-optimizer-panel:hover .grid .field span { color: var(--htd-text-dim); -webkit-text-fill-color: currentColor; }
    .htd-prompt-optimizer-dialog.privacy-mode .htd-prompt-optimizer-panel:hover .grid .field textarea { color: var(--htd-text); -webkit-text-fill-color: currentColor; }
    .htd-prompt-optimizer-dialog.privacy-mode .htd-prompt-optimizer-panel:hover .status,
    .htd-prompt-optimizer-dialog.privacy-mode .htd-prompt-optimizer-panel:hover .progress-text { color: var(--htd-text-dim); -webkit-text-fill-color: currentColor; }
    .htd-prompt-optimizer-dialog.privacy-mode .htd-prompt-optimizer-panel:hover .grid .field textarea::placeholder { color: var(--htd-text-faint); }
    .htd-prompt-optimizer-panel .empty { padding: 12px; color: var(--htd-text-dim); background: var(--htd-surface-2); border: 1px solid var(--htd-border); border-radius: var(--htd-radius-sm); }
    .htd-prompt-optimizer-panel .actions { display: flex; justify-content: flex-end; gap: 8px; }
    .htd-prompt-optimizer-panel .actions button { min-width: 82px; height: 32px; padding: 0 12px; }
    .htd-prompt-optimizer-panel .actions button.apply { border-color: var(--htd-accent-border); background: linear-gradient(180deg, #4f3a2a, #3d2d20); color: var(--htd-accent-strong); }
    .htd-prompt-optimizer-panel .actions button.apply:hover { background: linear-gradient(180deg, #5d4531, #493626); color: var(--htd-accent-strong); }
  `;
  documentRef.head.append(style);
}

const ICONS = {
  settings: `<svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 0 1 4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.3 7A2 2 0 0 1 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 0 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.1a2 2 0 0 1 0 4H21a1.7 1.7 0 0 0-1.6 1z"/></svg>`,
  text: `<svg viewBox="0 0 24 24"><path d="M5 6h14M12 6v12M8 18h8"/></svg>`,
  timeline: `<svg viewBox="0 0 24 24"><path d="M4 6h16M4 18h16"/><rect x="6" y="9" width="5" height="6" rx="1"/><rect x="13" y="9" width="5" height="6" rx="1"/></svg>`,
  key: `<svg viewBox="0 0 24 24"><circle cx="7.5" cy="15.5" r="5.5"/><path d="M12 12l8-8"/><path d="M15 7l2 2"/><path d="M17 5l2 2"/></svg>`,
  clear: `<svg viewBox="0 0 24 24"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`,
  save: `<svg viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/></svg>`,
  reset: `<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 3v5h5"/></svg>`,
};
