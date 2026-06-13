import { deepClone } from "./schema.js";

export class TimelineUndoStack {
  constructor(limit = 100) {
    this.limit = limit;
    this.undoStack = [];
    this.redoStack = [];
  }

  push(previousState) {
    this.undoStack.push(deepClone(previousState));
    if (this.undoStack.length > this.limit) this.undoStack.shift();
    this.redoStack = [];
  }

  canUndo() {
    return this.undoStack.length > 0;
  }

  canRedo() {
    return this.redoStack.length > 0;
  }

  undo(currentState) {
    if (!this.canUndo()) return null;
    const previous = this.undoStack.pop();
    this.redoStack.push(deepClone(currentState));
    return deepClone(previous);
  }

  redo(currentState) {
    if (!this.canRedo()) return null;
    const next = this.redoStack.pop();
    this.undoStack.push(deepClone(currentState));
    return deepClone(next);
  }
}
