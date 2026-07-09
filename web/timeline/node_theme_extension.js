import { applyHtdNodeTheme } from "./design_tokens.js";

function nodeTypeCandidates(node) {
  return [
    node?.type,
    node?.comfyClass,
    node?.class_type,
    node?.constructor?.type,
    node?.constructor?.comfyClass,
    node?.title,
  ].map((value) => String(value || "")).filter(Boolean);
}

export function createHtdNodeThemeLifecycle({
  appRef,
  nodeTypes,
  patchKey,
  scheduleFrame = (callback) => globalThis.requestAnimationFrame(callback),
}) {
  const themedNodeTypes = new Set(nodeTypes || []);
  const patchMarker = Symbol.for(`helto.director.nodeTheme.${String(patchKey || "default")}`);

  function matchesNodeData(nodeData) {
    return (
      themedNodeTypes.has(String(nodeData?.name || "")) ||
      themedNodeTypes.has(String(nodeData?.display_name || ""))
    );
  }

  function matchesNode(node) {
    return nodeTypeCandidates(node).some((candidate) => themedNodeTypes.has(candidate));
  }

  function applyNodeTheme(node) {
    if (!matchesNode(node)) {
      return false;
    }
    return applyHtdNodeTheme(node, { appRef });
  }

  function patchNodeType(nodeType) {
    const prototype = nodeType?.prototype;
    if (!prototype || prototype[patchMarker]) {
      return false;
    }
    prototype[patchMarker] = true;

    const originalCreated = prototype.onNodeCreated;
    prototype.onNodeCreated = function () {
      const result = originalCreated?.apply(this, arguments);
      applyNodeTheme(this);
      return result;
    };

    const originalConfigure = prototype.configure;
    prototype.configure = function () {
      const result = originalConfigure?.apply(this, arguments);
      applyNodeTheme(this);
      return result;
    };

    const originalOnConfigure = prototype.onConfigure;
    prototype.onConfigure = function () {
      const result = originalOnConfigure?.apply(this, arguments);
      applyNodeTheme(this);
      return result;
    };
    return true;
  }

  return Object.freeze({
    setup() {
      scheduleFrame(() => {
        for (const node of appRef?.graph?._nodes || []) {
          applyNodeTheme(node);
        }
      });
    },

    beforeRegisterNodeDef(nodeType, nodeData) {
      if (matchesNodeData(nodeData)) {
        patchNodeType(nodeType);
      }
    },

    nodeCreated(node) {
      applyNodeTheme(node);
    },

    loadedGraphNode(node) {
      applyNodeTheme(node);
    },
  });
}
