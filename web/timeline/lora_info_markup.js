const TRUSTED_MARKUP = Symbol("trusted-lora-info-markup");
const LOCAL_URL_ORIGIN = "http://helto-director.local";
const DIRECTOR_MEDIA_ROUTES = new Set([
  "/helto_director/api/loras/img",
]);

const CIVITAI_LOGO = `<svg class="logo-civitai" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7.2 3.8 12 1l4.8 2.8v5.5l4.8 2.7v5.6L12 23l-9.6-5.4V12l4.8-2.7V3.8Zm1.6 1v5.4L4 13v3.6l8 4.5 8-4.5V13l-4.8-2.8V4.8L12 3 8.8 4.8Zm1.6 7.3L12 11l1.6 1.1v2.1L12 15.2l-1.6-1v-2.1Z"/></svg>`;
const EXTERNAL_ICON = `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M10 2h4v4h-1.5V4.6L7 10.1 5.9 9 11.4 3.5H10V2ZM3.5 4h4v1.5h-4v7h7v-4H12v4.5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h.5Z"/></svg>`;
const EDIT_ICON = `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M11.9 1.7 14.3 4 5.5 12.8 2.6 13.4l.6-2.9 8.7-8.8Zm-.9 2.1-6.4 6.4-.2.9.9-.2 6.4-6.4-.7-.7Z"/></svg>`;
const SAVE_ICON = `<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M2 2h10.5L14 3.5V14H2V2Zm2 1.5v3h7v-3H4Zm0 9h8v-4H4v4Z"/></svg>`;

export function escapeLoraInfoHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function sanitizeLoraInfoUrl(value, { allowDirectorRoutes = false } = {}) {
  const rawInput = typeof value === "string" ? value : "";
  if (!rawInput || /[\u0000-\u001f\u007f]/.test(rawInput)) {
    return "";
  }
  const input = rawInput.trim();
  if (!input) {
    return "";
  }

  if (input.startsWith("/") && !input.startsWith("//")) {
    if (!allowDirectorRoutes) {
      return "";
    }
    try {
      const parsed = new URL(input, LOCAL_URL_ORIGIN);
      if (parsed.origin !== LOCAL_URL_ORIGIN || !DIRECTOR_MEDIA_ROUTES.has(parsed.pathname)) {
        return "";
      }
      return `${parsed.pathname}${parsed.search}`;
    } catch {
      return "";
    }
  }

  try {
    const parsed = new URL(input);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "";
  } catch {
    return "";
  }
}

function trustedMarkup(value) {
  return Object.freeze({ [TRUSTED_MARKUP]: String(value ?? "") });
}

function trustedMarkupValue(value) {
  return value?.[TRUSTED_MARKUP];
}

function safeClassToken(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function infoTableRow(label, value, help = "", editableFieldName = "") {
  const richValue = trustedMarkupValue(value);
  const rawValue = richValue ?? value;
  if (rawValue == null || rawValue === "") {
    return "";
  }
  const valueMarkup = richValue == null ? `<span>${escapeLoraInfoHtml(value)}</span>` : richValue;
  return `
    <tr class="${editableFieldName ? "editable" : ""}" ${editableFieldName ? `data-field-name="${escapeLoraInfoHtml(editableFieldName)}"` : ""}>
      <td><span>${escapeLoraInfoHtml(label)} ${help ? `<span class="-help" title="${escapeLoraInfoHtml(help)}"></span>` : ""}</span></td>
      <td ${editableFieldName ? "" : 'colspan="2"'}>${valueMarkup}</td>
      ${
        editableFieldName
          ? `<td style="width: 24px;"><button class="rgthree-button-reset rgthree-button-edit" data-action="edit-row">${EDIT_ICON}${SAVE_ICON}</button></td>`
          : ""
      }
    </tr>`;
}

function trainedWordsMarkup(words) {
  if (!Array.isArray(words) || words.length === 0) {
    return "";
  }
  return `<ul class="rgthree-info-trained-words-list">${words
    .map((item) => {
      const word = item && typeof item === "object" ? item.word : item;
      const civitai = Boolean(item && typeof item === "object" && item.civitai);
      const count = item && typeof item === "object" ? item.count : null;
      return `<li title="${escapeLoraInfoHtml(word)}" data-word="${escapeLoraInfoHtml(word)}" class="rgthree-info-trained-words-list-item" data-action="toggle-trained-word">
        <span>${escapeLoraInfoHtml(word)}</span>
        ${civitai ? CIVITAI_LOGO : ""}
        ${count != null ? `<small>${escapeLoraInfoHtml(count)}</small>` : ""}
      </li>`;
    })
    .join("")}</ul>`;
}

function imageInfoTextField(label, value) {
  return value != null
    ? `<span>${label ? `<label>${escapeLoraInfoHtml(label)} </label>` : ""}${escapeLoraInfoHtml(value)}</span>`
    : "";
}

function imageInfoLinkField(url) {
  const href = sanitizeLoraInfoUrl(url);
  if (!href) {
    return "";
  }
  return `<span><a href="${escapeLoraInfoHtml(href)}" target="_blank" rel="noopener noreferrer">civitai${EXTERNAL_ICON}</a></span>`;
}

function imagesMarkup(images) {
  if (!Array.isArray(images) || images.length === 0) {
    return "";
  }
  const items = images.flatMap((image) => {
    if (!image || typeof image !== "object") {
      return [];
    }
    const src = sanitizeLoraInfoUrl(image.url, { allowDirectorRoutes: true });
    if (!src) {
      return [];
    }
    const media = image.type === "video"
      ? `<video src="${escapeLoraInfoHtml(src)}" autoplay loop muted></video>`
      : `<img src="${escapeLoraInfoHtml(src)}" alt="">`;
    return [`<li><figure>${media}<figcaption>
      ${imageInfoLinkField(image.civitaiUrl)}
      ${imageInfoTextField("seed", image.seed)}
      ${imageInfoTextField("steps", image.steps)}
      ${imageInfoTextField("cfg", image.cfg)}
      ${imageInfoTextField("sampler", image.sampler)}
      ${imageInfoTextField("model", image.model)}
      ${imageInfoTextField("positive", image.positive)}
      ${imageInfoTextField("negative", image.negative)}
    </figcaption></figure></li>`];
  });
  return items.length ? `<ul class="rgthree-info-images">${items.join("")}</ul>` : "";
}

function civitaiModelLink(links) {
  if (!Array.isArray(links)) {
    return "";
  }
  for (const value of links) {
    const href = sanitizeLoraInfoUrl(value);
    if (!href) {
      continue;
    }
    const parsed = new URL(href);
    const hostname = parsed.hostname.toLowerCase();
    if ((hostname === "civitai.com" || hostname.endsWith(".civitai.com")) && parsed.pathname.startsWith("/models/")) {
      return href;
    }
  }
  return "";
}

export function buildLoraInfoDialogMarkup(info, file, { isLoading = false } = {}) {
  const civitaiLink = civitaiModelLink(info?.links);
  const civitaiError = info?.raw?.civitai?.error;
  const civitaiValue = civitaiLink
    ? trustedMarkup(`<a href="${escapeLoraInfoHtml(civitaiLink)}" target="_blank" rel="noopener noreferrer">${CIVITAI_LOGO}View on Civitai</a>`)
    : civitaiError
      ? String(civitaiError) === "Model not found"
        ? trustedMarkup(`<i>Model not found</i> <span class="-help" title="The model was not found on civitai with the sha256 hash. It is possible the model was removed, re-uploaded, or was never on civitai to begin with."></span>`)
        : civitaiError
      : !info?.raw?.civitai
        ? trustedMarkup(`<button type="button" class="rgthree-button" data-action="fetch-civitai">Fetch info from civitai</button>`)
        : "";
  const trainedWords = trainedWordsMarkup(info?.trainedWords);
  const metadata = info?.raw?.metadata && typeof info.raw.metadata === "object" ? info.raw.metadata : {};
  const title = info?.name || info?.file || file || "Unknown";
  const type = info?.type || "";
  const baseModel = info?.baseModel || "";

  return `
    <div class="rgthree-info-dialog">
      <div class="aio-rgthree-dialog-title">
        <h2>${escapeLoraInfoHtml(title)}</h2>
        <button type="button" class="helto-lora-close" aria-label="Close">x</button>
      </div>
      <div class="aio-rgthree-dialog-content">
        ${isLoading ? `<div class="helto-lora-loading">Loading...</div>` : ""}
        <ul class="rgthree-info-area">
          <li title="Type" class="rgthree-info-tag -type -type-${safeClassToken(type)}"><span>${escapeLoraInfoHtml(type)}</span></li>
          <li title="Base Model" class="rgthree-info-tag -basemodel -basemodel-${safeClassToken(baseModel)}"><span>${escapeLoraInfoHtml(baseModel)}</span></li>
          <li class="rgthree-info-menu"></li>
        </ul>
        <table class="rgthree-info-table">
          ${infoTableRow("File", info?.file || file)}
          ${infoTableRow("Hash (sha256)", info?.sha256)}
          ${infoTableRow("Civitai", civitaiValue)}
          ${infoTableRow("Name", info?.name || metadata.ss_output_name || "", "The name for display.", "name")}
          ${!info?.baseModelFile && !baseModel ? "" : infoTableRow("Base Model", `${baseModel}${info?.baseModelFile ? ` (${info.baseModelFile})` : ""}`)}
          ${trainedWords ? infoTableRow("Trained Words", trustedMarkup(trainedWords), "Trained words from the metadata and/or civitai. Click to select for copy.") : ""}
          ${!metadata.ss_clip_skip || metadata.ss_clip_skip === "None" ? "" : infoTableRow("Clip Skip", metadata.ss_clip_skip)}
          ${infoTableRow("Strength Min", info?.strengthMin ?? "", "The recommended minimum strength. In the Power Lora Loader node, strength will signal when it is below this threshold.", "strengthMin")}
          ${infoTableRow("Strength Max", info?.strengthMax ?? "", "The recommended maximum strength. In the Power Lora Loader node, strength will signal when it is above this threshold.", "strengthMax")}
          ${infoTableRow("Additional Notes", info?.userNote ?? "", "Additional notes you'd like to keep and reference in the info dialog.", "userNote")}
        </table>
        ${imagesMarkup(info?.images)}
      </div>
    </div>
  `;
}
