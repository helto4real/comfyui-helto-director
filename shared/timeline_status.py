from __future__ import annotations

import importlib
from typing import Any, Callable


STATUS_OPTIONAL_KEYS = (
    "segment_index",
    "segment_count",
    "frame_count",
    "encrypted_spill",
)
HELTO_PROGRESS_DETAIL_KEYS = (
    "stage",
    "model",
    "segment_index",
    "segment_count",
    "frame_count",
    "encrypted_spill",
    "current",
    "total",
)


class TimelineStatusReporter:
    def __init__(
        self,
        *,
        model: str,
        node_id: str | None = None,
        total: int = 1,
        emit_ui: bool | None = None,
        progress_bar_factory: Callable[..., Any] | None = None,
        text_sender: Callable[[str, str], Any] | None = None,
        event_sender: Callable[[dict[str, Any]], Any] | None = None,
        helto_progress_sender: Any | None = None,
    ) -> None:
        self.model = str(model or "timeline")
        self.node_id = str(node_id) if node_id is not None else None
        self.total = max(1, int(total or 1))
        self.current = 0
        self.events: list[dict[str, Any]] = []
        self._emit_ui = bool(self.node_id) if emit_ui is None else bool(emit_ui)
        self._progress_bar_factory = progress_bar_factory
        self._text_sender = text_sender
        self._event_sender = event_sender
        self._progress_bar = None
        self._helto_progress_sender = helto_progress_sender
        self._emit_helto_progress = bool(self.node_id) if helto_progress_sender is None else True

    def set_total(self, total: int) -> None:
        self.total = max(1, int(total or 1))
        if self._progress_bar is not None:
            self._safe_progress(self.current)

    def report(self, stage: str, label: str, **details: Any) -> dict[str, Any]:
        return self._record(
            stage,
            label,
            "start" if self.current <= 0 else "update",
            increment=True,
            **details,
        )

    def done(self, label: str | None = None) -> dict[str, Any]:
        self.current = max(self.current, self.total - 1)
        return self._record(
            "timeline.done",
            label or "Timeline Executor: done",
            "done",
            increment=True,
        )

    def error(self, label: str | None = None, stage: str = "timeline.error", **details: Any) -> dict[str, Any]:
        return self._record(
            stage,
            label or "Timeline Executor: failed",
            "error",
            increment=False,
            **details,
        )

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self.events]

    def _record(
        self,
        stage: str,
        label: str,
        progress_event: str,
        *,
        increment: bool,
        **details: Any,
    ) -> dict[str, Any]:
        if increment:
            self.current = min(self.current + 1, self.total)
        event = {
            "stage": str(stage),
            "label": str(label),
            "current": int(self.current),
            "total": int(self.total),
            "model": self.model,
        }
        for key in STATUS_OPTIONAL_KEYS:
            if key in details and details[key] is not None:
                event[key] = _safe_status_value(details[key])
        self.events.append(event)
        self._safe_event(event)
        self._safe_text(event["label"])
        self._safe_progress(self.current)
        self._safe_helto_progress(progress_event, event)
        return event

    def _safe_progress(self, value: int) -> None:
        if not self._emit_ui:
            return
        try:
            progress_bar = self._get_progress_bar()
            if progress_bar is not None:
                progress_bar.update_absolute(int(value), int(self.total))
        except Exception:
            return

    def _safe_text(self, label: str) -> None:
        if not self._emit_ui or not self.node_id:
            return
        try:
            sender = self._text_sender or _default_text_sender()
            if sender is not None:
                sender(label, self.node_id)
        except Exception:
            return

    def _safe_event(self, event: dict[str, Any]) -> None:
        if not self._emit_ui or not self.node_id:
            return
        try:
            sender = self._event_sender or _default_event_sender()
            if sender is not None:
                payload = {"node_id": self.node_id, **event}
                sender(payload)
        except Exception:
            return

    def _safe_helto_progress(self, progress_event: str, event: dict[str, Any]) -> None:
        if not self._emit_helto_progress:
            return
        try:
            sender = self._helto_progress_sender or _default_helto_progress_sender()
            reporter = getattr(sender, str(progress_event), None) if sender is not None else None
            if callable(reporter):
                reporter(
                    event["label"],
                    phase=event["stage"],
                    value=event["current"],
                    total=event["total"],
                    node_id=self.node_id,
                    detail=_helto_progress_detail(event),
                )
        except Exception:
            return

    def _get_progress_bar(self):
        if self._progress_bar is not None:
            return self._progress_bar
        try:
            factory = self._progress_bar_factory or _default_progress_bar_factory()
            self._progress_bar = factory(int(self.total), node_id=self.node_id) if factory is not None else None
        except Exception:
            self._progress_bar = None
        return self._progress_bar


def ensure_timeline_status_reporter(
    status_reporter: TimelineStatusReporter | None,
    *,
    model: str,
    total: int = 1,
) -> TimelineStatusReporter:
    if isinstance(status_reporter, TimelineStatusReporter):
        if status_reporter.current == 0:
            status_reporter.set_total(total)
        return status_reporter
    return TimelineStatusReporter(model=model, total=total, emit_ui=False)


def _default_progress_bar_factory():
    try:
        import comfy.utils

        return comfy.utils.ProgressBar
    except Exception:
        return None


def _default_text_sender():
    try:
        from server import PromptServer

        instance = getattr(PromptServer, "instance", None)
        if instance is None or not hasattr(instance, "send_progress_text"):
            return None
        return instance.send_progress_text
    except Exception:
        return None


def _default_event_sender():
    try:
        from server import PromptServer

        instance = getattr(PromptServer, "instance", None)
        if instance is None or not hasattr(instance, "send_sync"):
            return None

        def send_event(payload: dict[str, Any]) -> None:
            instance.send_sync("helto_timeline_status", payload, getattr(instance, "client_id", None))

        return send_event
    except Exception:
        return None


def _default_helto_progress_sender():
    try:
        return importlib.import_module("helto_progress")
    except Exception:
        return None


def _safe_status_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if value is None:
        return None
    return str(value)


def _helto_progress_detail(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _safe_status_value(event[key])
        for key in HELTO_PROGRESS_DETAIL_KEYS
        if key in event and event[key] is not None
    }
