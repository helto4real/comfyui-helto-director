// Complete browser-adapter assembly for the managed D1-D6 Director profile.

import {
  createDirectorGlobalModeBrowserAdapter,
  createDirectorTimelineBrowserAdapter,
} from "./managed_privacy.js";

export const DIRECTOR_GLOBAL_BROWSER_ADAPTER_ID = "director-global-mode-browser";
export const DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID = "director-timeline-browser";

export function createDirectorGlobalPrivacyBrowserAdapters({ workflowHandle, app } = {}) {
  return Object.freeze({
    [DIRECTOR_GLOBAL_BROWSER_ADAPTER_ID]: createDirectorGlobalModeBrowserAdapter(),
    [DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID]: createDirectorTimelineBrowserAdapter({
      workflowHandle,
      app,
    }),
  });
}
