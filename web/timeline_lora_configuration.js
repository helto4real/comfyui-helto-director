import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";
import { applyHtdNodeTheme, HTD, htdScrollbarBlock, htdTokenBlock } from "./timeline/design_tokens.js";
import { setupOverlayDialog } from "./timeline/dialog.js";
import { buildLoraInfoDialogMarkup } from "./timeline/lora_info_markup.js";

const NODE_NAME = "HeltoTimelineLoraConfiguration";
const NODE_DISPLAY_NAME = "Timeline LoRA Configuration";
const ROW_PREFIX = "lora_";
const HEADER_NAME = "helto_lora_header";
const ADD_BUTTON_LABEL = "+ Add LoRA";
const MIN_NODE_WIDTH = 560;
const LORA_HEADER_TOOLTIP = "Toggle every configured LoRA row on or off.";
const LORA_ROW_TOOLTIP = "LoRA row: choose a LoRA, toggle it, inspect metadata, and adjust strength.";
const ADD_LORA_TOOLTIP = "Add a LoRA row filtered by the match field.";

const DEFAULT_ROW = {
  on: true,
  lora: null,
  strength: 1,
  strengthTwo: null,
};

const NUMBER_WIDTH_TOTAL = 9 + 3 + 32 + 3 + 9;

let loraListPromise = null;

function widgetValue(node, name, fallback = "") {
  const widget = node.widgets?.find((item) => item.name === name);
  return widget?.value ?? fallback;
}

function isAioLoraNodeData(nodeData) {
  return nodeData?.name === NODE_NAME || nodeData?.display_name === NODE_DISPLAY_NAME;
}

function isAioLoraNode(node) {
  return (
    node?.type === NODE_NAME ||
    node?.comfyClass === NODE_NAME ||
    node?.constructor?.type === NODE_NAME ||
    node?.constructor?.comfyClass === NODE_NAME ||
    node?.title === NODE_DISPLAY_NAME
  );
}

function showSeparateStrengths(node) {
  return widgetValue(node, "show_strengths", "single") === "separate";
}

function dynamicWidgets(node) {
  return (node.widgets || []).filter(
    (widget) =>
      String(widget.name).startsWith(ROW_PREFIX) ||
      widget.name === HEADER_NAME ||
      widget._heltoLoraAddButton === true,
  );
}

function rowWidgets(node) {
  return (node.widgets || []).filter((widget) => String(widget.name).startsWith(ROW_PREFIX));
}

function moveArrayItem(array, item, index) {
  const current = array.indexOf(item);
  if (current < 0 || index < 0 || index >= array.length) {
    return;
  }
  array.splice(current, 1);
  array.splice(index, 0, item);
}

function removeArrayItem(array, item) {
  const index = array.indexOf(item);
  if (index >= 0) {
    array.splice(index, 1);
  }
}

function nextRowName(node) {
  let max = 0;
  for (const widget of node.widgets || []) {
    const match = String(widget.name || "").match(/^lora_(\d+)$/);
    if (match) {
      max = Math.max(max, Number(match[1]));
    }
  }
  return `${ROW_PREFIX}${max + 1}`;
}

function fitString(ctx, str, maxWidth) {
  str = String(str);
  if (ctx.measureText(str).width <= maxWidth) {
    return str;
  }
  const ellipsis = "...";
  let low = 0;
  let high = str.length;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (ctx.measureText(str.slice(0, mid) + ellipsis).width <= maxWidth) {
      low = mid;
    } else {
      high = mid - 1;
    }
  }
  return str.slice(0, low) + ellipsis;
}

function isLowQuality() {
  return (app.canvas?.ds?.scale || 1) <= 0.5;
}

function drawRoundedRectangle(ctx, { pos, size, borderRadius = null }) {
  const radius = isLowQuality() ? 0 : borderRadius ?? size[1] * 0.5;
  ctx.save();
  ctx.strokeStyle = LiteGraph.WIDGET_OUTLINE_COLOR;
  ctx.fillStyle = LiteGraph.WIDGET_BGCOLOR;
  ctx.beginPath();
  ctx.roundRect(pos[0], pos[1], size[0], size[1], [radius]);
  ctx.fill();
  if (!isLowQuality()) {
    ctx.stroke();
  }
  ctx.restore();
}

function drawTogglePart(ctx, { posX, posY, height, value }) {
  const lowQuality = isLowQuality();
  const toggleRadius = height * 0.36;
  const toggleBgWidth = height * 1.5;
  ctx.save();
  if (!lowQuality) {
    ctx.beginPath();
    ctx.roundRect(posX + 4, posY + 4, toggleBgWidth - 8, height - 8, [height * 0.5]);
    ctx.globalAlpha = app.canvas.editor_alpha * 0.25;
    ctx.fillStyle = HTD.controlWash;
    ctx.fill();
    ctx.globalAlpha = app.canvas.editor_alpha;
  }
  ctx.fillStyle = value === true ? HTD.accent : HTD.textFaint;
  const toggleX =
    lowQuality || value === false ? posX + height * 0.5 : value === true ? posX + height : posX + height * 0.75;
  ctx.beginPath();
  ctx.arc(toggleX, posY + height * 0.5, toggleRadius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
  return [posX, posY, toggleBgWidth, height];
}

function drawNumberWidgetPart(ctx, { posX, posY, height, value, direction = -1, textColor }) {
  const arrowWidth = 9;
  const arrowHeight = 10;
  const innerMargin = 3;
  const numberWidth = 32;
  let x = direction === -1 ? posX - NUMBER_WIDTH_TOTAL : posX;
  const midY = posY + height / 2;

  ctx.save();
  ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
  ctx.fill(new Path2D(`M ${x} ${midY} l ${arrowWidth} ${arrowHeight / 2} l 0 -${arrowHeight} L ${x} ${midY} z`));
  const left = [x, posY, arrowWidth, height];
  x += arrowWidth + innerMargin;

  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  if (textColor) {
    ctx.fillStyle = textColor;
  }
  ctx.fillText(fitString(ctx, Number(value ?? 1).toFixed(2), numberWidth), x + numberWidth / 2, midY);
  const text = [x, posY, numberWidth, height];
  x += numberWidth + innerMargin;

  ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
  ctx.fill(new Path2D(`M ${x} ${midY - arrowHeight / 2} l ${arrowWidth} ${arrowHeight / 2} l -${arrowWidth} ${arrowHeight / 2} v -${arrowHeight} z`));
  const right = [x, posY, arrowWidth, height];
  ctx.restore();
  return [left, text, right, [left[0], posY, right[0] + right[2] - left[0], height]];
}

function drawInfoIcon(ctx, x, y, size, treatment = "GRAYED") {
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(x, y, size, size, [size * 0.1]);
  ctx.fillStyle = treatment === "GRAYED" ? HTD.textFaint : HTD.accent;
  ctx.strokeStyle = ctx.fillStyle;
  if (treatment === "FILLED") {
    ctx.fill();
  } else {
    ctx.stroke();
  }
  ctx.strokeStyle = HTD.text;
  ctx.lineWidth = 2;
  const midX = x + size / 2;
  const serif = size * 0.175;
  ctx.stroke(
    new Path2D(`
      M ${midX} ${y + size * 0.15}
      v 2
      M ${midX - serif} ${y + size * 0.45}
      h ${serif}
      v ${size * 0.325}
      h ${serif}
      h -${serif * 2}
    `),
  );
  ctx.restore();
}

function inArea(pos, area) {
  return (
    area &&
    pos[0] >= area[0] &&
    pos[0] <= area[0] + area[2] &&
    pos[1] >= area[1] &&
    pos[1] <= area[1] + area[3]
  );
}

async function getLoras(force = false) {
  if (force) {
    loraListPromise = null;
  }
  if (!loraListPromise) {
    loraListPromise = api
      .fetchApi("/helto_director/api/loras?format=details", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("No Director loras route"))))
      .then((data) => data.map((item) => item.file ?? item))
      .catch(() =>
        api
          .fetchApi("/object_info/LoraLoader", { cache: "no-store" })
          .then((response) => response.json())
          .then((data) => data?.LoraLoader?.input?.required?.lora_name?.[0] || [])
          .catch(() => []),
      );
  }
  return loraListPromise;
}

async function fetchLoraInfo(file, { refresh = false, light = false } = {}) {
  const endpoint = refresh ? "/helto_director/api/loras/info/refresh" : "/helto_director/api/loras/info";
  const params = new URLSearchParams({ files: file });
  if (light) {
    params.set("light", "1");
  }
  const response = await api.fetchApi(`${endpoint}?${params.toString()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Director LoRA info request failed: ${response.status}`);
  }
  const payload = await response.json();
  return payload?.data?.[0] ?? null;
}

function filteredChooserData(node, loras) {
  let filtered = [...loras];
  let prefix = "";
  const match = String(widgetValue(node, "match", "") || "");
  if (match) {
    try {
      const regex = new RegExp(match);
      filtered = filtered.filter((lora) => regex.test(lora));
    } catch {
      filtered = [...loras];
    }
  }

  if (filtered.length > 0) {
    prefix = filtered[0];
    for (const lora of filtered) {
      let common = "";
      for (let index = 0; prefix[index] && prefix[index] === lora[index]; index++) {
        common += prefix[index];
      }
      prefix = common;
      if (!prefix) {
        break;
      }
    }
    if (prefix) {
      filtered = filtered.map((lora) => lora.replace(prefix, ""));
    }
  }

  return { prefix, choices: filtered };
}

async function showLoraChooser(event, node, onChoose) {
  const { prefix, choices } = filteredChooserData(node, await getLoras());
  new LiteGraph.ContextMenu(["None", ...choices], {
    event,
    title: "Choose LoRA",
    className: "dark",
    callback: (value) => {
      if (typeof value === "string" && value !== "None") {
        onChoose(prefix + value);
      }
      node.setDirtyCanvas(true, true);
    },
  });
}

function showFallbackInfo(file, error = null) {
  const message = error ? `Could not load LoRA/Civitai info for ${file}: ${error.message}` : `LoRA: ${file}`;
  if (app.extensionManager?.toast) {
    app.extensionManager.toast.add({ severity: error ? "warn" : "info", summary: "LoRA", detail: message });
    return;
  }
  console.info(`[Timeline LoRA Configuration] ${message}`);
}

async function saveLoraInfoPartial(file, partial) {
  const body = new FormData();
  body.append("json", JSON.stringify(partial));
  const response = await api.fetchApi(`/helto_director/api/loras/info?file=${encodeURIComponent(file)}`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
    throw new Error(`Save failed: ${response.status}`);
  }
  const payload = await response.json();
  return payload?.data ?? null;
}

function renderInfoDialogContent(container, info, file, isLoading = false) {
  container.innerHTML = buildLoraInfoDialogMarkup(info, file, { isLoading });
}

function ensureDialogStyles() {
  if (document.getElementById("helto-lora-info-styles")) {
    return;
  }
  const style = document.createElement("style");
  style.id = "helto-lora-info-styles";
  style.textContent = `
    ${htdTokenBlock(".helto-lora-info-overlay")}
    .helto-lora-info-overlay {
      position: fixed;
      inset: 0;
      z-index: 10000;
      display: grid;
      place-items: center;
      background: var(--htd-overlay);
      backdrop-filter: blur(4px);
    }
    .rgthree-info-dialog {
      width: 90vw;
      max-width: 960px;
      max-height: calc(100vh - 48px);
      overflow: hidden;
      border: 1px solid var(--htd-border-strong);
      border-radius: var(--htd-radius-lg);
      background: linear-gradient(135deg, var(--htd-modal-from), var(--htd-modal-to));
      color: var(--htd-text);
      box-shadow: var(--htd-shadow-pop);
      font: 13px/1.4 var(--htd-font-sans);
      -webkit-font-smoothing: antialiased;
    }
    .aio-rgthree-dialog-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 46px;
      padding: 0 14px 0 18px;
      background: var(--htd-surface-2);
      border-bottom: 1px solid var(--htd-border);
      color: var(--htd-text);
      font-weight: 700;
    }
    .aio-rgthree-dialog-title h2 {
      margin: 0;
      font-size: 18px;
      line-height: 1.2;
      color: var(--htd-text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .helto-lora-close {
      border: 0;
      background: transparent;
      color: var(--htd-text-dim);
      font-size: 30px;
      line-height: 1;
      cursor: pointer;
      transition: color var(--htd-transition);
    }
    .helto-lora-close:hover {
      color: var(--htd-text);
    }
    .aio-rgthree-dialog-content {
      padding: 12px 16px 16px;
      max-height: calc(100vh - 96px);
      overflow: auto;
    }
    ${htdScrollbarBlock(".aio-rgthree-dialog-content, .rgthree-info-dialog .rgthree-info-table td > ul.rgthree-info-trained-words-list, .rgthree-info-dialog .rgthree-info-images")}
    .helto-lora-loading {
      padding: 8px 10px;
      margin-bottom: 10px;
      color: var(--htd-text-dim);
      background: var(--htd-surface-2);
      border-radius: var(--htd-radius-sm);
    }
    .rgthree-button,
    .rgthree-button-reset {
      font: inherit;
      color: inherit;
    }
    .rgthree-button {
      border: 1px solid var(--htd-border-strong);
      border-radius: var(--htd-radius-sm);
      background: linear-gradient(180deg, var(--htd-surface-3), var(--htd-surface-2));
      color: var(--htd-text);
      padding: 6px 16px;
      cursor: pointer;
      transition: background var(--htd-transition), border-color var(--htd-transition), color var(--htd-transition);
    }
    .rgthree-button:hover {
      background: linear-gradient(180deg, var(--htd-surface-hover), var(--htd-surface-3));
      border-color: var(--htd-border-hover);
      color: var(--htd-text);
    }
    .rgthree-button:focus-visible {
      outline: none;
      border-color: var(--htd-focus);
      box-shadow: var(--htd-ring);
    }
    .rgthree-button-reset {
      border: 0;
      padding: 0;
      background: transparent;
      cursor: pointer;
    }
    .rgthree-info-dialog .rgthree-info-area {
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      align-items: center;
    }
    .rgthree-info-dialog .rgthree-info-area > li {
      display: inline-flex;
      margin: 0;
      vertical-align: top;
    }
    .rgthree-info-dialog .rgthree-info-area > li + li {
      margin-left: 6px;
    }
    .rgthree-info-dialog .rgthree-info-area > li.rgthree-info-tag > * {
      min-height: 24px;
      border-radius: 999px;
      line-height: 1;
      color: var(--htd-text-dim);
      border: 1px solid var(--htd-border-strong);
      background: var(--htd-surface-2);
      font-size: 14px;
      font-weight: 600;
      text-decoration: none;
      display: flex;
      height: 1.6em;
      padding: 0 0.6em 0.1em;
      align-content: center;
      justify-content: center;
      align-items: center;
    }
    .rgthree-info-dialog .rgthree-info-area > li.rgthree-info-tag > *:empty {
      display: none;
    }
    .rgthree-info-dialog .rgthree-info-area > li.-type > * {
      border-color: var(--htd-info-border);
      background: var(--htd-info-bg);
      color: var(--htd-info);
    }
    .rgthree-info-dialog .rgthree-info-area > li.rgthree-info-menu {
      margin-left: auto;
    }
    .rgthree-info-dialog .rgthree-info-table {
      border-collapse: collapse;
      margin: 16px 0;
      width: 100%;
      font-size: 12px;
    }
    .rgthree-info-dialog .rgthree-info-table tr.editable button {
      display: flex;
      width: 28px;
      height: 28px;
      align-items: center;
      justify-content: center;
    }
    .rgthree-info-dialog .rgthree-info-table tr.editable button svg + svg {
      display: none;
    }
    .rgthree-info-dialog .rgthree-info-table tr.editable.-rgthree-editing button svg {
      display: none;
    }
    .rgthree-info-dialog .rgthree-info-table tr.editable.-rgthree-editing button svg + svg {
      display: inline-block;
    }
    .rgthree-info-dialog .rgthree-info-table td {
      position: relative;
      border: 1px solid var(--htd-border);
      padding: 0;
      vertical-align: top;
    }
    .rgthree-info-dialog .rgthree-info-table td:first-child {
      background: var(--htd-surface-2);
      width: 10px;
    }
    .rgthree-info-dialog .rgthree-info-table td:first-child > *:first-child {
      white-space: nowrap;
      padding-right: 32px;
    }
    .rgthree-info-dialog .rgthree-info-table td:first-child small {
      display: block;
      margin-top: 2px;
      opacity: 0.75;
    }
    .rgthree-info-dialog .rgthree-info-table td:first-child small > [data-action] {
      text-decoration: underline;
      cursor: pointer;
    }
    .rgthree-info-dialog .rgthree-info-table td:first-child small > [data-action]:hover {
      text-decoration: none;
    }
    .rgthree-info-dialog .rgthree-info-table td a,
    .rgthree-info-dialog .rgthree-info-table td a:hover,
    .rgthree-info-dialog .rgthree-info-table td a:visited {
      color: inherit;
    }
    .rgthree-info-dialog .rgthree-info-table td svg {
      width: 1.3333em;
      height: 1.3333em;
      vertical-align: -0.285em;
    }
    .rgthree-info-dialog .rgthree-info-table td svg.logo-civitai {
      margin-right: 0.3333em;
    }
    .rgthree-info-dialog .rgthree-info-table td > *:first-child {
      display: block;
      padding: 6px 10px;
    }
    .rgthree-info-dialog .rgthree-info-table td > input,
    .rgthree-info-dialog .rgthree-info-table td > textarea {
      padding: 5px 10px;
      border: 0;
      box-shadow: inset 0 0 0 1px var(--htd-border-strong);
      font: inherit;
      appearance: none;
      background: var(--htd-bg);
      color: var(--htd-text);
      resize: vertical;
    }
    .rgthree-info-dialog .rgthree-info-table td > input:focus-visible,
    .rgthree-info-dialog .rgthree-info-table td > textarea:focus-visible {
      outline: none;
      box-shadow: var(--htd-ring);
    }
    .rgthree-info-dialog .rgthree-info-table td > input:only-child,
    .rgthree-info-dialog .rgthree-info-table td > textarea:only-child {
      width: 100%;
      box-sizing: border-box;
    }
    .rgthree-info-dialog .rgthree-info-table td .-help {
      border: 1px solid currentColor;
      position: absolute;
      right: 5px;
      top: 6px;
      line-height: 1;
      font-size: 11px;
      width: 12px;
      height: 12px;
      border-radius: 8px;
      display: flex;
      align-content: center;
      justify-content: center;
      cursor: help;
    }
    .rgthree-info-dialog .rgthree-info-table td .-help::before {
      content: "?";
    }
    .rgthree-info-dialog .rgthree-info-table td > ul.rgthree-info-trained-words-list {
      list-style: none;
      padding: 2px 8px;
      margin: 0;
      display: flex;
      flex-direction: row;
      flex-wrap: wrap;
      max-height: 15vh;
      overflow: auto;
    }
    .rgthree-info-dialog .rgthree-info-table td > ul.rgthree-info-trained-words-list > li {
      display: inline-flex;
      margin: 2px;
      vertical-align: top;
      border-radius: var(--htd-radius-sm);
      line-height: 1;
      color: var(--htd-text-dim);
      background: var(--htd-surface-2);
      border: 1px solid var(--htd-border-strong);
      font-size: 1.2em;
      font-weight: 600;
      text-decoration: none;
      height: 1.6em;
      align-content: center;
      justify-content: center;
      align-items: center;
      cursor: pointer;
      white-space: nowrap;
      max-width: 183px;
      transition: background var(--htd-transition), border-color var(--htd-transition), color var(--htd-transition);
    }
    .rgthree-info-dialog .rgthree-info-table td > ul.rgthree-info-trained-words-list > li:hover {
      background: var(--htd-surface-hover);
      border-color: var(--htd-border-hover);
      color: var(--htd-text);
    }
    .rgthree-info-dialog .rgthree-info-table td > ul.rgthree-info-trained-words-list > li > svg {
      width: auto;
      height: 1.2em;
    }
    .rgthree-info-dialog .rgthree-info-table td > ul.rgthree-info-trained-words-list > li > span {
      padding-left: 0.5em;
      padding-right: 0.5em;
      padding-bottom: 0.1em;
      text-overflow: ellipsis;
      overflow: hidden;
    }
    .rgthree-info-dialog .rgthree-info-table td > ul.rgthree-info-trained-words-list > li > small {
      align-self: stretch;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0 0.5em;
      background: color-mix(in srgb, var(--htd-bg) 20%, transparent);
    }
    .rgthree-info-dialog .rgthree-info-table td > ul.rgthree-info-trained-words-list > li.-rgthree-is-selected {
      background: var(--htd-accent-bg);
      border-color: var(--htd-accent-border);
      color: var(--htd-accent-strong);
      box-shadow: var(--htd-shadow-glow);
    }
    .rgthree-info-dialog .rgthree-info-images {
      list-style: none;
      padding: 0;
      margin: 0;
      scroll-snap-type: x mandatory;
      display: flex;
      flex-direction: row;
      overflow: auto;
    }
    .rgthree-info-dialog .rgthree-info-images > li {
      scroll-snap-align: start;
      max-width: 90%;
      flex: 0 0 auto;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-direction: column;
      overflow: hidden;
      padding: 0;
      margin: 6px;
      font-size: 0;
      position: relative;
    }
    .rgthree-info-dialog .rgthree-info-images > li figure {
      margin: 0;
      position: static;
    }
    .rgthree-info-dialog .rgthree-info-images > li figure video,
    .rgthree-info-dialog .rgthree-info-images > li figure img {
      max-height: 45vh;
      max-width: 100%;
    }
    .rgthree-info-dialog .rgthree-info-images > li figure figcaption {
      position: absolute;
      left: 0;
      width: 100%;
      bottom: 0;
      padding: 12px;
      font-size: 12px;
      background: color-mix(in srgb, var(--htd-bg) 85%, transparent);
      opacity: 0;
      transform: translateY(50px);
      transition: all 0.25s ease-in-out;
      box-sizing: border-box;
    }
    .rgthree-info-dialog .rgthree-info-images > li figure figcaption > span {
      display: inline-block;
      padding: 2px 4px;
      margin: 2px;
      border-radius: 2px;
      border: 1px solid var(--htd-border-strong);
      word-break: break-word;
    }
    .rgthree-info-dialog .rgthree-info-images > li figure figcaption > span label {
      display: inline;
      padding: 0;
      margin: 0;
      opacity: 0.5;
      pointer-events: none;
      user-select: none;
    }
    .rgthree-info-dialog .rgthree-info-images > li figure figcaption > span a {
      color: inherit;
      text-decoration: underline;
    }
    .rgthree-info-dialog .rgthree-info-images > li figure figcaption:empty {
      text-align: center;
    }
    .rgthree-info-dialog .rgthree-info-images > li figure figcaption:empty::before {
      content: "No data.";
    }
    .rgthree-info-dialog .rgthree-info-images > li:hover figure figcaption {
      opacity: 1;
      transform: translateY(0);
    }
  `;
  document.head.appendChild(style);
}

function showInfoToast(message, severity = "info") {
  if (app.extensionManager?.toast) {
    app.extensionManager.toast.add({ severity, summary: "LoRA", detail: message });
    return;
  }
  console.info(`[Timeline LoRA Configuration] ${message}`);
}

function selectedWordElements(tr) {
  return Array.from(tr?.querySelectorAll(".-rgthree-is-selected") || []);
}

function updateSelectedWordsSummary(tr) {
  const labelSpan = tr?.querySelector("td:first-child > *");
  if (!labelSpan) {
    return;
  }
  let small = labelSpan.querySelector("small");
  if (!small) {
    small = document.createElement("small");
    labelSpan.appendChild(small);
  }
  const count = selectedWordElements(tr).length;
  small.innerHTML = count
    ? `${count} selected | <span role="button" data-action="copy-trained-words">Copy</span>`
    : "";
}

async function copySelectedWords(target) {
  const tr = target.closest("tr");
  const words = selectedWordElements(tr).map((el) => el.getAttribute("data-word")).filter(Boolean);
  await navigator.clipboard.writeText(words.join(", "));
  showInfoToast(`Successfully copied ${words.length} key word${words.length === 1 ? "" : "s"}.`, "success");
}

async function saveEditableRow(info, file, tr, saving = true) {
  const fieldName = tr?.dataset?.fieldName;
  const td = tr?.querySelector("td:nth-child(2)");
  const input = td?.querySelector("input,textarea");
  if (!fieldName || !td) {
    return false;
  }

  let newValue = info?.[fieldName] ?? "";
  let modified = false;
  if (saving && input) {
    newValue = input.value;
    if (fieldName.startsWith("strength")) {
      if (Number.isNaN(Number(newValue))) {
        alert(`You must enter a number into the ${fieldName} field.`);
        return false;
      }
      newValue = (Math.round(Number(newValue) * 100) / 100).toFixed(2);
    }
    const saved = await saveLoraInfoPartial(file, { [fieldName]: newValue });
    Object.assign(info, saved || { [fieldName]: newValue });
    modified = true;
  }

  tr.classList.remove("-rgthree-editing");
  td.replaceChildren();
  const span = document.createElement("span");
  span.textContent = newValue;
  td.appendChild(span);
  return modified;
}

function beginEditableRow(info, file, tr) {
  const fieldName = tr?.dataset?.fieldName;
  const td = tr?.querySelector("td:nth-child(2)");
  if (!fieldName || !td) {
    return;
  }
  tr.classList.add("-rgthree-editing");
  const isTextarea = fieldName === "userNote";
  const input = document.createElement(isTextarea ? "textarea" : "input");
  if (!isTextarea) {
    input.type = "text";
  }
  input.value = td.textContent || info?.[fieldName] || "";
  input.addEventListener("keydown", async (event) => {
    if (!isTextarea && event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      await saveEditableRow(info, file, tr, true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      await saveEditableRow(info, file, tr, false);
    }
  });
  td.replaceChildren(input);
  input.focus();
}

async function showLoraInfoDialog(file, row = null) {
  ensureDialogStyles();
  const overlay = document.createElement("div");
  overlay.className = "helto-lora-info-overlay";

  const close = () => {
    overlay.remove();
    dialog.restoreFocus();
  };
  const dialog = setupOverlayDialog(overlay, {
    documentRef: document,
    label: `LoRA info: ${file}`,
    onRequestClose: close,
  });
  document.body.appendChild(overlay);

  overlay.addEventListener("click", (event) => {
    if (event.target === overlay || event.target.closest(".helto-lora-close")) {
      close();
    }
  });

  let info = null;
  try {
    renderInfoDialogContent(overlay, null, file, true);
    dialog.focusInitial(".helto-lora-close");
    info = await fetchLoraInfo(file);
    renderInfoDialogContent(overlay, info, file, false);
    row?.setLoraInfo?.(info);
  } catch (error) {
    close();
    showFallbackInfo(file, error);
    return;
  }

  overlay.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-action]");
    const action = target?.getAttribute("data-action");
    if (!target || !action) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();

    if (action === "fetch-civitai") {
      renderInfoDialogContent(overlay, info, file, true);
      try {
        info = await fetchLoraInfo(file, { refresh: true });
        renderInfoDialogContent(overlay, info, file, false);
        row?.setLoraInfo?.(info);
      } catch (error) {
        renderInfoDialogContent(overlay, info, file, false);
        showFallbackInfo(file, error);
      }
    } else if (action === "toggle-trained-word") {
      target.classList.toggle("-rgthree-is-selected");
      updateSelectedWordsSummary(target.closest("tr"));
    } else if (action === "copy-trained-words") {
      await copySelectedWords(target);
    } else if (action === "edit-row") {
      const tr = target.closest("tr");
      if (tr?.querySelector("input,textarea")) {
        await saveEditableRow(info, file, tr, true);
        row?.setLoraInfo?.(info);
      } else {
        beginEditableRow(info, file, tr);
      }
    }
  });
}

class LoraHeaderWidget {
  constructor() {
    this.name = HEADER_NAME;
    this.type = "custom";
    this.value = { type: HEADER_NAME };
    this.tooltip = LORA_HEADER_TOOLTIP;
    this.last_y = 0;
    this.hitAreas = {};
  }

  computeSize(width) {
    return [width, LiteGraph.NODE_WIDGET_HEIGHT];
  }

  serializeValue() {
    return this.value;
  }

  draw(ctx, node, width, posY, height) {
    const nodeWidth = node.size?.[0] ?? width;
    this.last_y = posY;
    if (!rowWidgets(node).length) {
      return;
    }
    const margin = 10;
    const innerMargin = margin * 0.33;
    const midY = posY + height / 2;
    let posX = margin;
    const separate = showSeparateStrengths(node);

    ctx.save();
    this.hitAreas.toggle = drawTogglePart(ctx, {
      posX,
      posY: posY + 2,
      height,
      value: allLorasState(node),
    });
    if (!isLowQuality()) {
      posX += this.hitAreas.toggle[2] + innerMargin;
      ctx.globalAlpha = app.canvas.editor_alpha * 0.55;
      ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText("Toggle All", posX, midY);

      let rightX = nodeWidth - margin - innerMargin - innerMargin;
      ctx.textAlign = "center";
      ctx.fillText(separate ? "Clip" : "Strength", rightX - NUMBER_WIDTH_TOTAL / 2, midY);
      if (separate) {
        rightX = rightX - NUMBER_WIDTH_TOTAL - innerMargin * 2;
        ctx.fillText("Model", rightX - NUMBER_WIDTH_TOTAL / 2, midY);
      }
    }
    ctx.restore();
  }

  mouse(event, pos, node) {
    if (event.type === "pointerdown" && inArea(pos, this.hitAreas.toggle)) {
      toggleAll(node);
      return true;
    }
    return false;
  }
}

class LoraRowWidget {
  constructor(name, value = null) {
    this.name = name;
    this.type = "custom";
    this.value = { ...DEFAULT_ROW, ...(value || {}) };
    this.tooltip = LORA_ROW_TOOLTIP;
    this.last_y = 0;
    this.hitAreas = {};
    this.showModelAndClip = null;
    this.haveMouseMovedStrength = false;
    this.activeStrengthKey = null;
    this.loraInfo = null;
    this.loraInfoPromise = null;
    this.getLoraInfo();
  }

  computeSize(width) {
    return [width, LiteGraph.NODE_WIDGET_HEIGHT];
  }

  serializeValue(node) {
    const value = { ...this.value };
    if (!showSeparateStrengths(node)) {
      delete value.strengthTwo;
    } else {
      value.strengthTwo = value.strengthTwo ?? value.strength ?? 1;
    }
    return value;
  }

  setLora(lora) {
    this.value.lora = lora;
    this.loraInfo = null;
    this.loraInfoPromise = null;
    this.getLoraInfo(true);
  }

  setLoraInfo(info) {
    this.loraInfo = info;
    this.loraInfoPromise = Promise.resolve(info);
  }

  draw(ctx, node, width, posY, height) {
    const nodeWidth = node.size?.[0] ?? width;
    this.last_y = posY;
    const currentShowModelAndClip = showSeparateStrengths(node);
    if (this.showModelAndClip !== currentShowModelAndClip) {
      const oldShowModelAndClip = this.showModelAndClip;
      this.showModelAndClip = currentShowModelAndClip;
      if (this.showModelAndClip) {
        if (oldShowModelAndClip != null) {
          this.value.strengthTwo = this.value.strength ?? 1;
        }
      } else {
        this.value.strengthTwo = null;
      }
    }

    const margin = 10;
    const innerMargin = margin * 0.33;
    const midY = posY + height / 2;
    let posX = margin;

    ctx.save();
    drawRoundedRectangle(ctx, {
      pos: [posX, posY],
      size: [nodeWidth - margin * 2, height],
      borderRadius: height * 0.5,
    });
    this.hitAreas.toggle = drawTogglePart(ctx, { posX, posY, height, value: this.value.on });
    posX += this.hitAreas.toggle[2] + innerMargin;

    if (isLowQuality()) {
      ctx.restore();
      return;
    }

    if (!this.value.on) {
      ctx.globalAlpha = app.canvas.editor_alpha * 0.4;
    }

    let rightX = nodeWidth - margin - innerMargin - innerMargin;
    const clipStrength = this.showModelAndClip ? this.value.strengthTwo ?? 1 : this.value.strength ?? 1;
    const clipParts = drawNumberWidgetPart(ctx, {
      posX: rightX,
      posY,
      height,
      value: clipStrength,
      direction: -1,
      textColor: this.strengthTextColor(clipStrength),
    });
    this.hitAreas.strengthTwoDec = this.showModelAndClip ? clipParts[0] : null;
    this.hitAreas.strengthTwoVal = this.showModelAndClip ? clipParts[1] : null;
    this.hitAreas.strengthTwoInc = this.showModelAndClip ? clipParts[2] : null;
    this.hitAreas.strengthTwoAny = this.showModelAndClip ? clipParts[3] : null;
    this.hitAreas.strengthDec = this.showModelAndClip ? null : clipParts[0];
    this.hitAreas.strengthVal = this.showModelAndClip ? null : clipParts[1];
    this.hitAreas.strengthInc = this.showModelAndClip ? null : clipParts[2];
    this.hitAreas.strengthAny = this.showModelAndClip ? null : clipParts[3];
    rightX = clipParts[0][0] - innerMargin;

    if (this.showModelAndClip) {
      rightX -= innerMargin;
      const modelStrength = this.value.strength ?? 1;
      const modelParts = drawNumberWidgetPart(ctx, {
        posX: rightX,
        posY,
        height,
        value: modelStrength,
        direction: -1,
        textColor: this.strengthTextColor(modelStrength),
      });
      this.hitAreas.strengthDec = modelParts[0];
      this.hitAreas.strengthVal = modelParts[1];
      this.hitAreas.strengthInc = modelParts[2];
      this.hitAreas.strengthAny = modelParts[3];
      rightX = modelParts[0][0] - innerMargin;
    }

    const infoSize = height * 0.66;
    const infoWidth = infoSize + innerMargin + innerMargin;
    if (this.value.lora) {
      rightX -= innerMargin;
      drawInfoIcon(ctx, rightX - infoSize, posY + (height - infoSize) / 2, infoSize, this.infoTreatment());
      this.hitAreas.info = [rightX - infoSize, posY, infoWidth, height];
      rightX = rightX - infoSize - innerMargin;
    } else {
      this.hitAreas.info = null;
    }

    const loraWidth = rightX - posX;
    this.hitAreas.lora = [posX, posY, loraWidth, height];
    ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(fitString(ctx, this.value.lora || "None", loraWidth), posX, midY);
    ctx.restore();
  }

  mouse(event, pos, node) {
    if (event.type === "pointerdown") {
      if (event.button === 2) {
        this.showMenu(event, node);
        return true;
      }
      if (inArea(pos, this.hitAreas.toggle)) {
        this.value.on = !this.value.on;
        node.setDirtyCanvas(true, true);
        return true;
      }
      if (inArea(pos, this.hitAreas.info)) {
        this.showLoraInfoDialog();
        return true;
      }
      if (inArea(pos, this.hitAreas.lora)) {
        showLoraChooser(event, node, (value) => this.setLora(value));
        return true;
      }
      if (this.handleNumberPointerDown(event, pos, node)) {
        return true;
      }
    }

    if (event.type === "pointermove" && this.activeStrengthKey) {
      const delta = event.deltaX ?? event.movementX ?? 0;
      if (delta) {
        this.haveMouseMovedStrength = true;
        this.value[this.activeStrengthKey] = (this.value[this.activeStrengthKey] ?? 1) + delta * 0.05;
        node.setDirtyCanvas(true, true);
      }
      return true;
    }

    if (event.type === "pointerup" && this.activeStrengthKey) {
      if (!this.haveMouseMovedStrength) {
        this.promptStrength(event, this.activeStrengthKey);
      }
      this.haveMouseMovedStrength = false;
      this.activeStrengthKey = null;
      return true;
    }

    return false;
  }

  handleNumberPointerDown(event, pos, node) {
    const specs = [
      ["strength", this.hitAreas.strengthDec, -1],
      ["strength", this.hitAreas.strengthInc, 1],
      ["strengthTwo", this.hitAreas.strengthTwoDec, -1],
      ["strengthTwo", this.hitAreas.strengthTwoInc, 1],
    ];
    for (const [key, area, direction] of specs) {
      if (inArea(pos, area)) {
        this.stepStrength(key, direction);
        node.setDirtyCanvas(true, true);
        return true;
      }
    }

    if (inArea(pos, this.hitAreas.strengthAny)) {
      this.activeStrengthKey = "strength";
      this.haveMouseMovedStrength = false;
      return true;
    }
    if (inArea(pos, this.hitAreas.strengthTwoAny)) {
      this.activeStrengthKey = "strengthTwo";
      this.haveMouseMovedStrength = false;
      return true;
    }
    return false;
  }

  stepStrength(key, direction) {
    const current = this.value[key] ?? 1;
    this.value[key] = Math.round((current + 0.05 * direction) * 100) / 100;
  }

  promptStrength(event, key) {
    app.canvas.prompt(
      "Value",
      this.value[key] ?? 1,
      (value) => {
        const parsed = Number(value);
        if (!Number.isNaN(parsed)) {
          this.value[key] = parsed;
        }
      },
      event,
    );
  }

  strengthTextColor(value) {
    if (this.loraInfo?.strengthMax != null && value > this.loraInfo.strengthMax) {
      return HTD.danger;
    }
    if (this.loraInfo?.strengthMin != null && value < this.loraInfo.strengthMin) {
      return HTD.danger;
    }
    return undefined;
  }

  infoTreatment() {
    if (this.loraInfo?.raw?.civitai) {
      return "FILLED";
    }
    if (this.loraInfo?.hasInfoFile) {
      return "OUTLINED";
    }
    return "GRAYED";
  }

  async getLoraInfo(force = false) {
    if (!this.value.lora || this.value.lora === "None") {
      this.loraInfo = null;
      return null;
    }
    if (!this.loraInfoPromise || force) {
      this.loraInfoPromise = fetchLoraInfo(this.value.lora, { refresh: force, light: true })
        .then((info) => (this.loraInfo = info))
        .catch(() => null);
    }
    return this.loraInfoPromise;
  }

  async showLoraInfoDialog() {
    if (!this.value.lora || this.value.lora === "None") {
      return;
    }
    await showLoraInfoDialog(this.value.lora, this);
  }

  showMenu(event, node) {
    new LiteGraph.ContextMenu(rowMenuItems(node, this), {
      event,
      title: "LoRA",
      className: "dark",
    });
  }
}

function rowMenuItems(node, row) {
  const rows = rowWidgets(node);
  const index = rows.indexOf(row);
  return [
    { content: "Show Info", callback: () => row.showLoraInfoDialog() },
    null,
    {
      content: row.value.on ? "Toggle Off" : "Toggle On",
      callback: () => {
        row.value.on = !row.value.on;
        node.setDirtyCanvas(true, true);
      },
    },
    {
      content: "Move Up",
      disabled: index <= 0,
      callback: () => moveRow(node, row, -1),
    },
    {
      content: "Move Down",
      disabled: index < 0 || index >= rows.length - 1,
      callback: () => moveRow(node, row, 1),
    },
    {
      content: "Remove",
      callback: () => removeRow(node, row),
    },
  ];
}

function allLorasState(node) {
  const rows = rowWidgets(node);
  if (!rows.length) {
    return false;
  }
  const allOn = rows.every((row) => row.value.on === true);
  const allOff = rows.every((row) => row.value.on === false);
  if (!allOn && !allOff) {
    return null;
  }
  return allOn;
}

function toggleAll(node) {
  const rows = rowWidgets(node);
  const toggledTo = !allLorasState(node);
  for (const row of rows) {
    row.value.on = toggledTo;
  }
  node.setDirtyCanvas(true, true);
}

function removeDynamicWidgets(node) {
  node.widgets = (node.widgets || []).filter((widget) => !dynamicWidgets(node).includes(widget));
}

function addHeader(node) {
  const header = new LoraHeaderWidget();
  node.addCustomWidget(header);
  return header;
}

function addControls(node) {
  removeArrayItem(node.widgets, node.widgets.find((widget) => widget._heltoLoraAddButton === true));
  const button = node.addWidget("button", ADD_BUTTON_LABEL, null, async (...args) => {
    const event = args.find((arg) => arg instanceof Event) || window.event;
    await showLoraChooser(event, node, (value) => addRow(node, value));
  });
  button._heltoLoraAddButton = true;
  button.tooltip = ADD_LORA_TOOLTIP;
  button.options ||= {};
  button.options.tooltip = ADD_LORA_TOOLTIP;
}

function applyLoraNodeSize(node, mode = "restore", savedSize = null) {
  const currentSize = node.size || node.computeSize();
  const nextSize = [Math.max(Number(currentSize[0]) || 0, MIN_NODE_WIDTH), Number(currentSize[1]) || 0];

  if (Array.isArray(savedSize) && Number.isFinite(Number(savedSize[0]))) {
    nextSize[0] = Math.max(Number(savedSize[0]), MIN_NODE_WIDTH);
  }
  if (mode === "interactive") {
    nextSize[1] = Math.max(nextSize[1], node.computeSize()[1]);
  } else if (Array.isArray(savedSize) && Number.isFinite(Number(savedSize[1]))) {
    nextSize[1] = Number(savedSize[1]);
  }

  if (typeof node.setSize === "function") {
    node.setSize(nextSize);
  } else {
    node.size = nextSize;
  }
}

function scheduleLoraNodeSizeRestore(node, savedSize) {
  if (!Array.isArray(savedSize)) {
    return;
  }
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      applyLoraNodeSize(node, "restore", savedSize);
      node.setDirtyCanvas?.(true, true);
    });
  });
}

function addRow(node, lora = null, value = null, { resize = true, dirty = true } = {}) {
  const widget = new LoraRowWidget(nextRowName(node), value);
  if (lora) {
    widget.setLora(lora);
  }
  const buttonIndex = node.widgets.findIndex((item) => item._heltoLoraAddButton === true);
  if (buttonIndex >= 0) {
    node.widgets.splice(buttonIndex, 0, widget);
  } else {
    node.addCustomWidget(widget);
  }
  applyLoraNodeSize(node, resize ? "interactive" : "restore");
  if (dirty) {
    node.setDirtyCanvas(true, true);
  }
  return widget;
}

function moveRow(node, row, direction) {
  const rows = rowWidgets(node);
  const rowIndex = rows.indexOf(row);
  const sibling = rows[rowIndex + direction];
  if (!sibling) {
    return;
  }
  moveArrayItem(node.widgets, row, node.widgets.indexOf(sibling));
  node.setDirtyCanvas(true, true);
}

function removeRow(node, row) {
  removeArrayItem(node.widgets, row);
  node.setDirtyCanvas(true, true);
}

function restoreRows(node, info) {
  const values = (info?.widgets_values || []).filter((value) => value && typeof value.lora === "string");
  removeDynamicWidgets(node);
  addHeader(node);
  for (const value of values) {
    addRow(node, null, value, { resize: false, dirty: false });
  }
  addControls(node);
  applyLoraNodeSize(node, "restore", info?.size);
  scheduleLoraNodeSizeRestore(node, info?.size);
}

function ensureLoraUi(node) {
  if (!isAioLoraNode(node)) {
    return;
  }
  applyHtdNodeTheme(node, { appRef: app });
  node.serialize_widgets = true;
  const hasHeader = node.widgets?.some((widget) => widget.name === HEADER_NAME);
  const hasButton = node.widgets?.some((widget) => widget._heltoLoraAddButton === true);
  if (!hasHeader) {
    addHeader(node);
  }
  if (!hasButton) {
    addControls(node);
  }
  applyLoraNodeSize(node, "restore");
  node.setDirtyCanvas?.(true, true);
}

function patchLoraNodeType(nodeType) {
  if (nodeType.prototype.__heltoLoraConfigurationPatched) {
    return;
  }
  nodeType.prototype.__heltoLoraConfigurationPatched = true;

  const originalCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    originalCreated?.apply(this, arguments);
    applyHtdNodeTheme(this, { appRef: app });
    this.serialize_widgets = true;
    removeDynamicWidgets(this);
    addHeader(this);
    addControls(this);
    applyLoraNodeSize(this, "interactive");
    this.setDirtyCanvas(true, true);
  };

  const originalConfigure = nodeType.prototype.configure;
  nodeType.prototype.configure = function (info) {
    originalConfigure?.apply(this, arguments);
    applyHtdNodeTheme(this, { appRef: app });
    this.serialize_widgets = true;
    restoreRows(this, info);
  };

  const originalRefreshCombo = nodeType.prototype.refreshComboInNode;
  nodeType.prototype.refreshComboInNode = function () {
    loraListPromise = null;
    return originalRefreshCombo?.apply(this, arguments);
  };

  const originalMenu = nodeType.prototype.getExtraMenuOptions;
  nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
    originalMenu?.apply(this, arguments);
    options.push({
      content: "Toggle All LoRAs",
      callback: () => toggleAll(this),
    });
    options.push({
      content: "Refresh LoRA List",
      callback: () => getLoras(true),
    });
  };

  const originalGetSlot = nodeType.prototype.getSlotInPosition;
  nodeType.prototype.getSlotInPosition = function (canvasX, canvasY) {
    const slot = originalGetSlot?.apply(this, arguments);
    if (slot) {
      return slot;
    }
    const localY = canvasY - this.pos[1];
    for (const widget of this.widgets || []) {
      if (
        String(widget.name).startsWith(ROW_PREFIX) &&
        localY >= widget.last_y &&
        localY <= widget.last_y + LiteGraph.NODE_WIDGET_HEIGHT
      ) {
        return { widget, output: { type: "HELTO LORA ROW" } };
      }
    }
    return undefined;
  };

  const originalSlotMenu = nodeType.prototype.getSlotMenuOptions;
  nodeType.prototype.getSlotMenuOptions = function (slot) {
    if (String(slot?.widget?.name || "").startsWith(ROW_PREFIX)) {
      return rowMenuItems(this, slot.widget);
    }
    return originalSlotMenu?.apply(this, arguments);
  };
}

app.registerExtension({
  name: "helto.timeline.loraConfiguration",
  setup() {
    requestAnimationFrame(() => {
      for (const node of app.graph?._nodes || []) {
        ensureLoraUi(node);
      }
    });
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!isAioLoraNodeData(nodeData)) {
      return;
    }

    patchLoraNodeType(nodeType);
  },
  nodeCreated(node) {
    ensureLoraUi(node);
  },
  loadedGraphNode(node) {
    ensureLoraUi(node);
  },
});
