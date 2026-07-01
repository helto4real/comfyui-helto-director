// Canonical Helto design tokens. Source of truth for values:
// helto-designsystem/reference/tokens.css (--helto-* is the canonical prefix,
// --htd-* is this repo's equivalent). Every Director surface pulls its token
// block from here; body-mounted overlays scope it to their root dialog
// selector because node-scoped tokens don't reach document.body.
export const HTD_TOKEN_DECLARATIONS = `
      --htd-bg: #0d1320; --htd-surface: #151c2a; --htd-surface-2: #1b2333; --htd-surface-3: #232d3f; --htd-surface-hover: #2c3850;
      --htd-border: #2a3346; --htd-border-strong: #3a465c; --htd-border-hover: #4c5970; --htd-text: #e7ebf3; --htd-text-dim: #9aa6bd; --htd-text-faint: #6f7c95;
      --htd-accent: #f1c75c; --htd-accent-strong: #ffd873; --htd-accent-bg: rgba(241,199,92,0.16); --htd-accent-border: rgba(241,199,92,0.55);
      --htd-focus: #5e9bff; --htd-ring: 0 0 0 2px rgba(94,155,255,0.5); --htd-danger: #ec5a6b; --htd-danger-bg: #3a1a22; --htd-danger-border: #8f3a44;
      --htd-radius: 6px; --htd-radius-sm: 5px; --htd-radius-lg: 10px; --htd-shadow: 0 1px 2px rgba(0,0,0,0.35); --htd-shadow-pop: 0 14px 36px rgba(0,0,0,0.55); --htd-shadow-glow: 0 0 10px rgba(241,199,92,0.35);
`;

export function htdTokenBlock(scopeSelector) {
  return `${scopeSelector} {${HTD_TOKEN_DECLARATIONS}    }`;
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
