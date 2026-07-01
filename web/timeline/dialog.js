// Shared behavior for Director dialogs mounted on <body>: ARIA dialog
// semantics, Escape-to-close, a Tab focus trap, initial focus, and focus
// restore to the trigger element when the dialog closes.

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

export function setupOverlayDialog(overlay, {
  documentRef = globalThis.document,
  label = "",
  onRequestClose = null,
} = {}) {
  if (!overlay?.setAttribute) return { focusInitial: () => {}, restoreFocus: () => {} };

  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  if (label && !overlay.getAttribute("aria-label")) overlay.setAttribute("aria-label", label);
  if (overlay.tabIndex === undefined || overlay.tabIndex < 0) overlay.tabIndex = -1;

  const previousFocus = documentRef?.activeElement ?? null;

  const focusables = () => {
    const elements = overlay.querySelectorAll?.(FOCUSABLE_SELECTOR);
    return elements ? [...elements].filter((element) => element.getAttribute?.("aria-hidden") !== "true") : [];
  };

  overlay.addEventListener?.("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      if (typeof onRequestClose === "function") {
        event.preventDefault();
        onRequestClose();
      }
      return;
    }
    if (event.key !== "Tab") return;
    const elements = focusables();
    if (!elements.length) {
      event.preventDefault();
      return;
    }
    const first = elements[0];
    const last = elements[elements.length - 1];
    const active = documentRef?.activeElement;
    if (event.shiftKey && (active === first || active === overlay)) {
      event.preventDefault();
      last.focus?.();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus?.();
    }
  });

  const focusInitial = (preferredSelector = null) => {
    const preferred = preferredSelector ? overlay.querySelector?.(preferredSelector) : null;
    (preferred ?? focusables()[0] ?? overlay).focus?.();
  };

  const restoreFocus = () => {
    if (previousFocus?.focus && previousFocus.isConnected !== false) previousFocus.focus();
  };

  return { focusInitial, restoreFocus };
}
