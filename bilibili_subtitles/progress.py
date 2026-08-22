from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    phase: str
    message: str
    current: int | None = None
    total: int | None = None
    part_number: int | None = None


ProgressReporter = Callable[[ProgressEvent], None]


def emit_progress(
    reporter: ProgressReporter | None,
    phase: str,
    message: str,
    *,
    current: int | None = None,
    total: int | None = None,
    part_number: int | None = None,
) -> None:
    if reporter is None:
        return
    reporter(
        ProgressEvent(
            phase=phase,
            message=message,
            current=current,
            total=total,
            part_number=part_number,
        )
    )


def format_progress(event: ProgressEvent, *, elapsed_seconds: float) -> str:
    position = ""
    if event.current is not None and event.total is not None:
        position = f" [{event.current}/{event.total}]"
    part = f" P{event.part_number}" if event.part_number is not None else ""
    return (
        f"[progress {max(0.0, elapsed_seconds):.1f}s]{position}{part} "
        f"{event.message}"
    )
