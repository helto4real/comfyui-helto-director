// Canonical Helto design tokens. Source of truth for values:
// helto-ai-agent-skills/UI/helto-design-system/assets/tokens.css.
// Keep --helto-* as the canonical names and --htd-* as this repo's local
// aliases. Every Director surface pulls its token block from here; body-mounted
// overlays scope it to their root dialog selector because node-scoped tokens
// don't reach document.body.
export const HTD_TOKEN_DECLARATIONS = `
      --helto-bg: #181825; --helto-surface: #1e1e2e; --helto-surface-2: #313244; --helto-surface-3: #45475a; --helto-surface-hover: #585b70;
      --helto-border: #313244; --helto-border-strong: #45475a; --helto-border-hover: #6c7086;
      --helto-text: #cdd6f4; --helto-text-dim: #a6adc8; --helto-text-faint: #7f849c;
      --helto-accent: #fab387; --helto-accent-strong: #fddcc4; --helto-accent-border: #93664a; --helto-accent-bg: #46301f;
      --helto-focus: #89b4fa; --helto-focus-ring: 0 0 0 3px rgba(137, 180, 250, 0.28);
      --helto-danger: #f38ba8; --helto-danger-border: #96526a; --helto-ok: #a6e3a1; --helto-warn: #f9e2af; --helto-info: #74c7ec;
      --helto-radius-sm: 5px; --helto-radius: 6px; --helto-radius-lg: 10px;
      --helto-shadow: 0 1px 2px rgba(0, 0, 0, 0.35); --helto-shadow-pop: 0 12px 32px rgba(0, 0, 0, 0.5);
      --helto-shadow-glow: 0 0 0 1px rgba(250, 179, 135, 0.35), 0 0 12px rgba(250, 179, 135, 0.22);
      --helto-transition: 0.12s ease; --helto-ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
      --helto-font-sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; --helto-font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --helto-font-size: 12px; --helto-line: 1.4;
      --htd-bg: var(--helto-bg); --htd-surface: var(--helto-surface); --htd-surface-2: var(--helto-surface-2); --htd-surface-3: var(--helto-surface-3); --htd-surface-hover: var(--helto-surface-hover);
      --htd-border: var(--helto-border); --htd-border-strong: var(--helto-border-strong); --htd-border-hover: var(--helto-border-hover);
      --htd-text: var(--helto-text); --htd-text-dim: var(--helto-text-dim); --htd-text-faint: var(--helto-text-faint);
      --htd-accent: var(--helto-accent); --htd-accent-strong: var(--helto-accent-strong); --htd-accent-bg: var(--helto-accent-bg); --htd-accent-border: var(--helto-accent-border);
      --htd-focus: var(--helto-focus); --htd-ring: var(--helto-focus-ring);
      --htd-danger: var(--helto-danger); --htd-danger-bg: #3a2130; --htd-danger-border: var(--helto-danger-border); --htd-danger-text: #f9d4e0; --htd-danger-text-strong: #fdeef4;
      --htd-ok: var(--helto-ok); --htd-ok-bg: #223423; --htd-ok-border: #4f7050;
      --htd-warn: var(--helto-warn); --htd-warn-bg: #363019; --htd-warn-border: #7d7147;
      --htd-info: var(--helto-info); --htd-info-bg: #16303d; --htd-info-border: #3e6478;
      --htd-radius: var(--helto-radius); --htd-radius-sm: var(--helto-radius-sm); --htd-radius-lg: var(--helto-radius-lg);
      --htd-shadow: var(--helto-shadow); --htd-shadow-pop: var(--helto-shadow-pop); --htd-shadow-glow: var(--helto-shadow-glow);
      --htd-transition: var(--helto-transition); --htd-ease-spring: var(--helto-ease-spring);
      --htd-font-sans: var(--helto-font-sans); --htd-font-mono: var(--helto-font-mono); --htd-font-size: var(--helto-font-size); --htd-line: var(--helto-line);
      --htd-overlay: rgba(17, 17, 27, 0.72); --htd-overlay-strong: rgba(17, 17, 27, 0.82);
      --htd-modal-from: rgba(49, 50, 68, 0.92); --htd-modal-to: rgba(24, 24, 37, 0.96); --htd-inset-highlight: rgba(255, 255, 255, 0.02);
      --htd-accent-hairline: rgba(250, 179, 135, 0.18);
      --htd-privacy-status-border: #7d5a41; --htd-privacy-status-bg: #30231a; --htd-privacy-status-text: #f8d0ae;
      --htd-media-bg: #11111b; --htd-privacy-cover: #060a10; --htd-privacy-cover-strong: #050505;
      --htd-media-text: var(--htd-info); --htd-media-image: var(--htd-ok); --htd-media-video: var(--htd-warn); --htd-media-audio: #cba6f7;
`;

export function htdTokenBlock(scopeSelector) {
  return `${scopeSelector} {${HTD_TOKEN_DECLARATIONS}    }`;
}

// Raw token literal values, for LiteGraph canvas drawing where CSS variables
// cannot be resolved. Keep these values mirrored with HTD_TOKEN_DECLARATIONS.
export const HTD = {
  bg: "#181825",
  surface: "#1e1e2e",
  surface2: "#313244",
  surface3: "#45475a",
  surfaceHover: "#585b70",
  border: "#313244",
  borderStrong: "#45475a",
  borderHover: "#6c7086",
  text: "#cdd6f4",
  textDim: "#a6adc8",
  textFaint: "#7f849c",
  accent: "#fab387",
  accentStrong: "#fddcc4",
  accentBorder: "#93664a",
  accentBg: "#46301f",
  focus: "#89b4fa",
  danger: "#f38ba8",
  dangerBorder: "#96526a",
  warn: "#f9e2af",
  ok: "#a6e3a1",
  info: "#74c7ec",
  mediaBg: "#11111b",
  privacyCover: "#060a10",
  privacyCoverStrong: "#050505",
  controlWash: "rgba(205, 214, 244, 0.18)",
};

const HTD_LITEGRAPH_WIDGET_THEME = {
  WIDGET_BGCOLOR: HTD.bg,
  WIDGET_OUTLINE_COLOR: HTD.borderStrong,
  WIDGET_PROMOTED_OUTLINE_COLOR: HTD.accent,
  WIDGET_ADVANCED_OUTLINE_COLOR: HTD.focus,
  WIDGET_TEXT_COLOR: HTD.text,
  WIDGET_SECONDARY_TEXT_COLOR: HTD.textDim,
  WIDGET_DISABLED_TEXT_COLOR: HTD.textFaint,
};

const HTD_NODE_THEME_KEY = "__heltoDirectorNodeTheme";
const HTD_WIDGET_THEME_BRIDGE_KEY = "__heltoDirectorWidgetThemeBridgeInstalled";
const HTD_WIDGET_THEME_FALLBACK_KEY = "__heltoDirectorWidgetThemeFallbackInstalled";
const HTD_WIDGET_THEME_SNAPSHOT_KEY = "__heltoDirectorWidgetThemeSnapshot";

export function applyHtdLiteGraphWidgetTheme(liteGraph = globalThis.LiteGraph) {
  if (!liteGraph || typeof liteGraph !== "object") {
    return null;
  }
  const previous = {};
  for (const [key, value] of Object.entries(HTD_LITEGRAPH_WIDGET_THEME)) {
    if (key in liteGraph) {
      previous[key] = liteGraph[key];
      liteGraph[key] = value;
    }
  }
  return Object.keys(previous).length ? { liteGraph, previous } : null;
}

export function restoreHtdLiteGraphWidgetTheme(snapshot) {
  const { liteGraph, previous } = snapshot || {};
  if (!liteGraph || !previous) {
    return false;
  }
  for (const [key, value] of Object.entries(previous)) {
    liteGraph[key] = value;
  }
  return true;
}

export function withHtdLiteGraphWidgetTheme(callback, liteGraph = globalThis.LiteGraph) {
  const snapshot = applyHtdLiteGraphWidgetTheme(liteGraph);
  try {
    return callback?.();
  } finally {
    restoreHtdLiteGraphWidgetTheme(snapshot);
  }
}

export function isHtdThemedNode(node) {
  return Boolean(node?.[HTD_NODE_THEME_KEY]);
}

function liteGraphCanvasPrototype(appRef = null) {
  return [
    globalThis.LGraphCanvas?.prototype,
    globalThis.LiteGraph?.LGraphCanvas?.prototype,
    appRef?.canvas?.constructor?.prototype,
  ].find((prototype) => typeof prototype?.drawNodeWidgets === "function") || null;
}

export function installHtdWidgetThemeBridge(appRef = null) {
  const prototype = liteGraphCanvasPrototype(appRef);
  if (!prototype) {
    return false;
  }
  if (prototype[HTD_WIDGET_THEME_BRIDGE_KEY]) {
    return true;
  }
  const originalDrawNodeWidgets = prototype.drawNodeWidgets;
  prototype[HTD_WIDGET_THEME_BRIDGE_KEY] = true;
  prototype.drawNodeWidgets = function (node) {
    if (isHtdThemedNode(node)) {
      return withHtdLiteGraphWidgetTheme(() => originalDrawNodeWidgets.apply(this, arguments));
    }
    return originalDrawNodeWidgets.apply(this, arguments);
  };
  return true;
}

function ensureHtdWidgetThemeFallback(node) {
  if (!node || node[HTD_WIDGET_THEME_FALLBACK_KEY]) {
    return;
  }
  node[HTD_WIDGET_THEME_FALLBACK_KEY] = true;

  const originalDrawBackground = node.onDrawBackground;
  node.onDrawBackground = function () {
    restoreHtdLiteGraphWidgetTheme(this[HTD_WIDGET_THEME_SNAPSHOT_KEY]);
    this[HTD_WIDGET_THEME_SNAPSHOT_KEY] = applyHtdLiteGraphWidgetTheme();
    try {
      return originalDrawBackground?.apply(this, arguments);
    } catch (error) {
      restoreHtdLiteGraphWidgetTheme(this[HTD_WIDGET_THEME_SNAPSHOT_KEY]);
      this[HTD_WIDGET_THEME_SNAPSHOT_KEY] = null;
      throw error;
    }
  };

  const originalDrawForeground = node.onDrawForeground;
  node.onDrawForeground = function () {
    try {
      return originalDrawForeground?.apply(this, arguments);
    } finally {
      restoreHtdLiteGraphWidgetTheme(this[HTD_WIDGET_THEME_SNAPSHOT_KEY]);
      this[HTD_WIDGET_THEME_SNAPSHOT_KEY] = null;
    }
  };
}

export function applyHtdNodeTheme(node, { appRef = null } = {}) {
  if (!node || typeof node !== "object") {
    return false;
  }
  node[HTD_NODE_THEME_KEY] = true;
  if (!installHtdWidgetThemeBridge(appRef)) {
    ensureHtdWidgetThemeFallback(node);
  }
  node.color = HTD.surface3;
  node.bgcolor = HTD.surface;
  node.setDirtyCanvas?.(true, true);
  node.graph?.setDirtyCanvas?.(true, true);
  return true;
}

// Shared scrollbar treatment for Director overlay scroll areas. Focus blue is
// a documented exception here: the thin scrollbar thumb reuses --htd-focus so
// it stays visible against gold selection highlights.
export function htdScrollbarBlock(scopeSelector) {
  const scopes = String(scopeSelector || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  const base = scopes.join(", ");
  const webkit = (suffix) => scopes.map((scope) => `${scope}::-webkit-scrollbar${suffix}`).join(", ");
  return `
    ${base} { scrollbar-width: thin; scrollbar-color: color-mix(in srgb, var(--htd-focus) 45%, transparent) transparent; }
    ${webkit("")} { width: 8px; height: 8px; }
    ${webkit("-track")} { background: transparent; }
    ${webkit("-thumb")} { background: color-mix(in srgb, var(--htd-focus) 45%, transparent); border-radius: 999px; border: 2px solid transparent; background-clip: padding-box; }
    ${webkit("-thumb:hover")} { background: color-mix(in srgb, var(--htd-focus) 70%, transparent); background-clip: padding-box; }
  `;
}
