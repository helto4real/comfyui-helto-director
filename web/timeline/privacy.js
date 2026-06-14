export const PRIVACY_SCHEMA = "helto.timeline-director";
const ROUTE_PREFIX = "/helto_director/privacy";

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
  const options = payload
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }
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
