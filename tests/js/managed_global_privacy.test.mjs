import assert from "node:assert/strict";

import {
  DIRECTOR_GLOBAL_BROWSER_ADAPTER_ID,
  DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID,
  createDirectorGlobalPrivacyBrowserAdapters,
} from "../../web/timeline/managed_global_privacy.js";

const adapters = createDirectorGlobalPrivacyBrowserAdapters({
  workflowHandle: { markEdited() {}, app: {} },
});

assert.deepEqual(Object.keys(adapters).sort(), [
  DIRECTOR_GLOBAL_BROWSER_ADAPTER_ID,
  DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID,
].sort());

const contracts = {
  [DIRECTOR_GLOBAL_BROWSER_ADAPTER_ID]: [
    "onPrivacySessionChange", "readDeclaredMode", "reconcileNode",
    "reconcileNodeDefinition", "writeDeclaredMode",
  ],
  [DIRECTOR_TIMELINE_BROWSER_ADAPTER_ID]: [
    "apply", "applyExternalOperation", "applyModeTransitionOwnerExact", "clear",
    "extractDetachedModeTransitionOwnerExact", "identifyExternalOperationOwner",
    "inventoryModeTransitionOwners", "normalize", "onPrivacySessionChange",
    "readExternalOperationExact", "readModeTransitionOwnerExact", "readProtected",
    "reconcileExternalOperationRuntime", "reconcileModeTransitionRuntime",
    "reconcileNode", "reconcileNodeDefinition", "reloadExternalOperationRuntime",
    "reloadModeTransitionRuntime", "resolveExternalOperationOwner",
    "restoreExternalOperationExact", "restoreModeTransitionOwnerExact",
    "settleExternalOperation", "settleModeTransition", "writeProtected",
  ],
};

for (const [adapterId, methods] of Object.entries(contracts)) {
  assert.equal(Object.isFrozen(adapters[adapterId]), false);
  for (const method of methods) {
    assert.equal(typeof adapters[adapterId][method], "function", `${adapterId}.${method}`);
  }
}

assert.equal(Object.isFrozen(adapters), true);
