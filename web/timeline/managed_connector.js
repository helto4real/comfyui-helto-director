import { app } from "../../../scripts/app.js";

import { createDirectorManagedLibrary } from "./managed_library_privacy.js";
import { createDirectorManagedMediaBrowser } from "./managed_media_privacy.js";
import {
  DIRECTOR_GLOBAL_BROWSER_ADAPTER_ID,
  DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID,
} from "./managed_global_privacy.js";
import {
  createDirectorGlobalModeBrowserAdapter,
  createDirectorTimelineBrowserAdapter,
} from "./managed_privacy.js";
import { createDirectorManagedTakeBrowser } from "./managed_take_privacy.js";

export const DIRECTOR_PROFILE_ID = "helto.director";
export const DIRECTOR_PROFILE_FINGERPRINT = "948ad2440e27b7fdba7e40ac1928424afae3b8a19c27d859ee61ce25f42ab835";

const GLOBAL_MODE_RESOURCE_ID = "director-global-mode";
const GLOBAL_SCOPE_ID = "director-global";
const TIMELINE_RESOURCE_ID = "timeline";
const TIMELINE_EXECUTION_RESOURCE_ID = "timeline-render";
const INSTALLATION_BLOCKED = "PRIVACY_DIRECTOR_INSTALLATION_BLOCKED";

let connectionPromise = null;

function blocked() {
  throw new Error(INSTALLATION_BLOCKED);
}

async function connect() {
  const response = await fetch("/helto_privacy/status", { cache: "no-store" });
  if (!response.ok) blocked();
  const suite = await response.json();
  if (
    suite?.ok !== true
    || suite?.suiteStatus !== "active"
    || !/^[0-9a-f]{64}$/.test(String(suite?.suiteManifestDigest || ""))
  ) blocked();
  const runtime = await import(
    `/helto_privacy/ui/privacy_profile/${suite.suiteManifestDigest}.js`
  );
  if (typeof runtime.connectPrivacyPack !== "function" || !runtime.PRIVACY_CONTRACT_V3) {
    blocked();
  }
  const browserAdapters = {};
  const pack = await runtime.connectPrivacyPack({
    app,
    packId: DIRECTOR_PROFILE_ID,
    contract: runtime.PRIVACY_CONTRACT_V3,
    profileFingerprint: DIRECTOR_PROFILE_FINGERPRINT,
    suiteManifestDigest: suite.suiteManifestDigest,
    adapterFactories: {
      [DIRECTOR_GLOBAL_BROWSER_ADAPTER_ID]: () => {
        const adapter = createDirectorGlobalModeBrowserAdapter();
        browserAdapters[DIRECTOR_GLOBAL_BROWSER_ADAPTER_ID] = adapter;
        return adapter;
      },
      [DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID]: ({ handle }) => {
        const adapter = createDirectorTimelineBrowserAdapter({
          workflowHandle: handle,
          app,
        });
        browserAdapters[DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID] = adapter;
        return adapter;
      },
    },
  });
  const mode = pack.mode(GLOBAL_MODE_RESOURCE_ID);
  const workflow = pack.workflow(TIMELINE_RESOURCE_ID);
  const execution = pack.execution(TIMELINE_EXECUTION_RESOURCE_ID);
  return Object.freeze({
    pack,
    mode,
    workflow,
    execution,
    scopeId: GLOBAL_SCOPE_ID,
    library: createDirectorManagedLibrary({ pack }),
    media: createDirectorManagedMediaBrowser({ pack }),
    takes: createDirectorManagedTakeBrowser({ pack }),
    browserAdapters: Object.freeze(browserAdapters),
  });
}

export function directorManagedPrivacy() {
  connectionPromise ??= connect().catch((error) => {
    connectionPromise = null;
    throw error;
  });
  return connectionPromise;
}

export async function bindDirectorManagedPrivacy(node, controller) {
  const connection = await directorManagedPrivacy();
  const adapter = connection.browserAdapters[DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID];
  if (!adapter?.reconcileNode || !controller?.bindManagedPrivacy) blocked();
  controller.bindManagedPrivacy(connection);
  node?._timelineMediaCache?.bindManagedMedia?.(connection.media);
  node?._timelineMediaCache?.refresh?.(controller.timeline, controller.globalSettings);
  adapter.reconcileNode(node);
  return connection;
}
