from __future__ import annotations


def seconds_to_frame(seconds: float, frame_rate: float) -> int:
    _validate_frame_rate(frame_rate)
    return max(0, int(round(float(seconds) * float(frame_rate))))


def frame_to_seconds(frame: int, frame_rate: float) -> float:
    _validate_frame_rate(frame_rate)
    return max(0, int(frame)) / float(frame_rate)


def time_range_to_frames(
    start_time: float,
    end_time: float,
    frame_rate: float,
) -> dict:
    start_frame = seconds_to_frame(start_time, frame_rate)
    end_frame_exclusive = seconds_to_frame(end_time, frame_rate)
    end_frame_exclusive = max(start_frame, end_frame_exclusive)
    return {
        "start_frame": start_frame,
        "end_frame_exclusive": end_frame_exclusive,
        "frame_count": end_frame_exclusive - start_frame,
    }


def _validate_frame_rate(frame_rate: float) -> None:
    if float(frame_rate) <= 0:
        raise ValueError("frame_rate must be greater than zero.")
