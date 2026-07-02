export const PRIVACY_SCHEMA = "helto.timeline-director";
const ROUTE_PREFIX = "/helto_director/privacy";
const PRIVACY_TOKEN_HEADER = "X-Helto-Privacy-Token";
const PRIVACY_TOKEN_STORAGE_KEY = "helto_privacy_token";
const PRIVACY_LOCKED_CODES = ["PRIVACY_LOCKED", "PRIVACY_TOKEN_REQUIRED"];

export function getStoredPrivacyToken() {
  try {
    return globalThis.localStorage?.getItem(PRIVACY_TOKEN_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function storePrivacyToken(token) {
  try {
    if (token) globalThis.localStorage?.setItem(PRIVACY_TOKEN_STORAGE_KEY, String(token));
    else globalThis.localStorage?.removeItem(PRIVACY_TOKEN_STORAGE_KEY);
  } catch {
    /* localStorage unavailable (tests, embedded webviews) — token stays per-request. */
  }
  writePrivacyTokenCookie(token);
}

export function hasStoredPrivacyToken() {
  return Boolean(getStoredPrivacyToken());
}

export function hasPrivacyTokenCookie(documentRef = globalThis.document) {
  try {
    const prefix = `${PRIVACY_TOKEN_STORAGE_KEY}=`;
    return String(documentRef?.cookie || "")
      .split(";")
      .map((part) => part.trim())
      .some((part) => part.startsWith(prefix) && part.length > prefix.length);
  } catch {
    return false;
  }
}

export function ensureStoredPrivacyTokenCookie(documentRef = globalThis.document) {
  const token = getStoredPrivacyToken();
  if (!token) return false;
  writePrivacyTokenCookie(token, documentRef);
  return true;
}

function writePrivacyTokenCookie(token, documentRef = globalThis.document) {
  // Image/media elements cannot send custom headers, so privacy-mode
  // thumbnails and waveforms authenticate with this cookie instead.
  try {
    if (!documentRef) return;
    documentRef.cookie = token
      ? `${PRIVACY_TOKEN_STORAGE_KEY}=${encodeURIComponent(String(token))}; path=/; SameSite=Lax`
      : `${PRIVACY_TOKEN_STORAGE_KEY}=; path=/; SameSite=Lax; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
  } catch {
    /* cookies unavailable — header-based callers still work. */
  }
}

export function isPrivacyLockedError(error) {
  const message = String(error?.message ?? error ?? "");
  return PRIVACY_LOCKED_CODES.some((code) => message.includes(code));
}

export function isEncryptedPrivacyPayload(value) {
  const parsed = parsePrivacyPayload(value);
  return Boolean(parsed?.encrypted === true && parsed.schema === PRIVACY_SCHEMA && parsed.algorithm === "AES-256-GCM");
}

export function parsePrivacyPayload(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

export async function fetchPrivacyJson(endpoint, payload = null) {
  const headers = { "Content-Type": "application/json" };
  const token = getStoredPrivacyToken();
  if (token) headers[PRIVACY_TOKEN_HEADER] = token;
  const options = payload
    ? { method: "POST", headers, body: JSON.stringify(payload) }
    : undefined;
  const response = await fetch(`${ROUTE_PREFIX}/${endpoint}`, options);
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(text || response.statusText || `HTTP ${response.status}`);
  }
  if (!response.ok || data.ok === false || data.error) throw new Error(data.error || response.statusText);
  return data;
}

export function encryptTimelineSync(timeline) {
  if (typeof XMLHttpRequest !== "function") {
    throw new Error("Synchronous privacy encryption is unavailable in this environment.");
  }
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `${ROUTE_PREFIX}/encrypt`, false);
  xhr.setRequestHeader("Content-Type", "application/json");
  const token = getStoredPrivacyToken();
  if (token) xhr.setRequestHeader(PRIVACY_TOKEN_HEADER, token);
  xhr.send(JSON.stringify({ state: { timeline } }));
  let data = {};
  try {
    data = xhr.responseText ? JSON.parse(xhr.responseText) : {};
  } catch {
    throw new Error(xhr.responseText || xhr.statusText || `HTTP ${xhr.status}`);
  }
  if (xhr.status < 200 || xhr.status >= 300 || data.ok === false || data.error) {
    throw new Error(data.error || xhr.statusText || `HTTP ${xhr.status}`);
  }
  return data.envelope;
}

export async function fetchPrivacyStatus() {
  return fetchPrivacyJson("status");
}

export async function initializePrivacyKeystore(password) {
  const result = await fetchPrivacyJson("keystore/init", { password });
  storePrivacyToken(result.token || "");
  return result;
}

export async function unlockPrivacyKeystore(password) {
  const result = await fetchPrivacyJson("unlock", { password });
  storePrivacyToken(result.token || "");
  return result;
}

export async function lockPrivacyKeystore() {
  const result = await fetchPrivacyJson("lock", {});
  storePrivacyToken("");
  return result;
}

export async function changePrivacyKeystorePassword(currentPassword, newPassword) {
  const result = await fetchPrivacyJson("keystore/change_password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
  storePrivacyToken(result.token || "");
  return result;
}
