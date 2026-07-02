import { htdTokenBlock } from "./design_tokens.js";
import { setupOverlayDialog } from "./dialog.js";
import {
  changePrivacyKeystorePassword,
  initializePrivacyKeystore,
  unlockPrivacyKeystore,
} from "./privacy.js";

const DIALOG_CLASS = "htd-privacy-keystore-dialog";
const STYLE_ID = "helto-privacy-keystore-style";

const MODES = {
  unlock: {
    title: "Unlock Privacy Keystore",
    hint: "Enter your privacy password. It stays unlocked until this computer restarts or you lock it.",
    fields: [{ name: "password", label: "Privacy password" }],
    action: "Unlock",
    run: (values) => unlockPrivacyKeystore(values.password),
  },
  setup: {
    title: "Set Privacy Password",
    hint: "Creates a password-protected keystore shared by all Helto node packs. Your existing key is imported so saved workflows stay readable.",
    fields: [
      { name: "password", label: "New privacy password" },
      { name: "confirm", label: "Repeat password" },
    ],
    action: "Create keystore",
    run: (values) => {
      if (values.password !== values.confirm) throw new Error("Passwords do not match.");
      return initializePrivacyKeystore(values.password);
    },
  },
  change: {
    title: "Change Privacy Password",
    hint: "Re-wraps the keystore with a new password. Encrypted data is unaffected.",
    fields: [
      { name: "current", label: "Current password" },
      { name: "password", label: "New privacy password" },
      { name: "confirm", label: "Repeat new password" },
    ],
    action: "Change password",
    run: (values) => {
      if (values.password !== values.confirm) throw new Error("Passwords do not match.");
      return changePrivacyKeystorePassword(values.current, values.password);
    },
  },
};

export function closePrivacyKeystoreDialog(documentRef = globalThis.document) {
  for (const dialog of documentRef?.querySelectorAll?.(`.${DIALOG_CLASS}`) ?? []) dialog.remove();
}

export function isPrivacyKeystoreDialogOpen(documentRef = globalThis.document) {
  return Boolean(documentRef?.querySelector?.(`.${DIALOG_CLASS}`));
}

export function showPrivacyKeystoreDialog(mode = "unlock", { documentRef = globalThis.document } = {}) {
  const spec = MODES[mode] ?? MODES.unlock;
  if (!documentRef?.createElement || !documentRef.body) return Promise.resolve(null);
  installPrivacyKeystoreStyles(documentRef);
  closePrivacyKeystoreDialog(documentRef);

  return new Promise((resolve) => {
    const overlay = documentRef.createElement("div");
    overlay.className = DIALOG_CLASS;
    const finish = (result) => {
      overlay.remove();
      dialog.restoreFocus();
      resolve(result);
    };
    const dialog = setupOverlayDialog(overlay, {
      documentRef,
      label: spec.title,
      onRequestClose: () => finish(null),
    });

    const panel = documentRef.createElement("div");
    panel.className = "htd-privacy-keystore-panel";

    const title = documentRef.createElement("h3");
    title.textContent = spec.title;
    const hint = documentRef.createElement("p");
    hint.className = "htd-privacy-keystore-hint";
    hint.textContent = spec.hint;
    panel.append(title, hint);

    const inputs = new Map();
    for (const field of spec.fields) {
      const label = documentRef.createElement("label");
      label.className = "htd-privacy-keystore-field";
      const caption = documentRef.createElement("span");
      caption.textContent = field.label;
      const input = documentRef.createElement("input");
      input.type = "password";
      input.autocomplete = "off";
      input.spellcheck = false;
      label.append(caption, input);
      panel.append(label);
      inputs.set(field.name, input);
    }

    const status = documentRef.createElement("div");
    status.className = "htd-privacy-keystore-status";
    const actions = documentRef.createElement("div");
    actions.className = "htd-privacy-keystore-actions";
    const cancelButton = documentRef.createElement("button");
    cancelButton.type = "button";
    cancelButton.textContent = "Cancel";
    const submitButton = documentRef.createElement("button");
    submitButton.type = "button";
    submitButton.className = "primary";
    submitButton.textContent = spec.action;
    actions.append(cancelButton, submitButton);
    panel.append(status, actions);
    overlay.append(panel);

    const submit = async () => {
      const values = {};
      for (const [name, input] of inputs) values[name] = input.value || "";
      submitButton.disabled = true;
      status.textContent = "Working...";
      try {
        const result = await spec.run(values);
        finish(result);
      } catch (error) {
        status.textContent = error.message || String(error);
        submitButton.disabled = false;
      }
    };

    submitButton.addEventListener("click", submit);
    cancelButton.addEventListener("click", () => finish(null));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) finish(null);
    });
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target?.tagName === "INPUT") {
        event.preventDefault();
        submit();
      }
      event.stopPropagation();
    });

    documentRef.body.append(overlay);
    dialog.focusInitial("input");
  });
}

function installPrivacyKeystoreStyles(documentRef) {
  if (!documentRef || documentRef.getElementById?.(STYLE_ID)) return;
  const style = documentRef.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    ${htdTokenBlock(`.${DIALOG_CLASS}`)}
    .${DIALOG_CLASS} { position: fixed; inset: 0; z-index: 10090; display: flex; align-items: center; justify-content: center; background: rgba(6,9,15,0.72); backdrop-filter: blur(4px); color: var(--htd-text-dim); font: 12px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; }
    .htd-privacy-keystore-panel { width: min(380px, calc(100vw - 28px)); display: flex; flex-direction: column; gap: 10px; background: linear-gradient(135deg, rgba(27,35,51,0.92), rgba(13,19,32,0.96)); border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-lg); box-shadow: var(--htd-shadow-pop); padding: 16px; box-sizing: border-box; }
    .htd-privacy-keystore-panel h3 { margin: 0; font-size: 15px; font-weight: 700; color: var(--htd-text); }
    .htd-privacy-keystore-hint { margin: 0; color: var(--htd-text-dim); }
    .htd-privacy-keystore-field { display: grid; gap: 4px; color: var(--htd-text-faint); }
    .htd-privacy-keystore-field input { height: 30px; box-sizing: border-box; padding: 0 8px; background: var(--htd-bg); color: var(--htd-text); border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); font: inherit; transition: border-color .12s ease, box-shadow .12s ease; }
    .htd-privacy-keystore-field input:focus-visible { outline: none; border-color: var(--htd-focus); box-shadow: var(--htd-ring); }
    .htd-privacy-keystore-status { min-height: 16px; color: var(--htd-danger); }
    .htd-privacy-keystore-actions { display: flex; justify-content: flex-end; gap: 8px; }
    .htd-privacy-keystore-actions button { min-width: 88px; padding: 7px 14px; cursor: pointer; background: linear-gradient(180deg, var(--htd-surface-3), var(--htd-surface-2)); color: var(--htd-text); border: 1px solid var(--htd-border-strong); border-radius: var(--htd-radius-sm); font: inherit; transition: background .12s ease, border-color .12s ease, color .12s ease; }
    .htd-privacy-keystore-actions button:hover:not(:disabled) { background: linear-gradient(180deg, var(--htd-surface-hover), var(--htd-surface-3)); border-color: var(--htd-border-hover); color: #fff; }
    .htd-privacy-keystore-actions button:focus-visible { outline: none; border-color: var(--htd-focus); box-shadow: var(--htd-ring); }
    .htd-privacy-keystore-actions button:disabled { opacity: .48; cursor: not-allowed; }
    .htd-privacy-keystore-actions button.primary { border-color: var(--htd-accent-border); background: linear-gradient(180deg, #4f4322, #3c3318); color: var(--htd-accent-strong); }
    .htd-privacy-keystore-actions button.primary:hover:not(:disabled) { background: linear-gradient(180deg, #5b4d27, #46391b); color: #fff3cf; }
  `;
  documentRef.head?.append(style);
}
