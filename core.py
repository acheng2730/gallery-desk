"""Settings, geometry, photo selection, and playback timing without a GUI."""

import copy
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile


DEFAULTS = {
    "version": 1,
    "photo_directory": str(Path.home() / "Pictures/Desktop_Photos/Desktop_Photos_Original"),
    "interval_min_seconds": 15.0,
    "interval_max_seconds": 30.0,
    "fade_seconds": 0.9,
    "corner_radius": 24.0,
    "snap_enabled": True,
    "snap_spacing": 24.0,
    "frames": [
        {"id": "photo-1", "name": "Photo 1", "x": 64, "y": 72, "size": 440},
        {"id": "photo-2", "name": "Photo 2", "x": 114, "y": 590, "size": 340},
        {"id": "photo-3", "name": "Photo 3", "x": 2076, "y": 72, "size": 420},
        {"id": "photo-4", "name": "Photo 4", "x": 2106, "y": 592, "size": 360},
    ],
    "player": {"x": 64, "y": 1110, "width": 640},
    "visualizer": {"x": 728, "y": 1300, "width": 640, "enabled": True},
    "graphs": {"x": 2157, "y": 1098},
}


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number.")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g}.")
    return value


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object.")
    result = copy.deepcopy(DEFAULTS)
    result.update(copy.deepcopy(data))
    directory = result["photo_directory"]
    if not isinstance(directory, str) or not directory or "\0" in directory or len(directory) > 4096:
        raise ValueError("Choose a valid photo folder.")
    result["photo_directory"] = str(Path(directory).expanduser().absolute())
    # Replace old shared/per-frame delays with the requested random range.
    result.pop("interval_seconds", None)
    minimum = number(result["interval_min_seconds"], 2, 3600, "Minimum photo interval")
    maximum = number(result["interval_max_seconds"], 2, 3600, "Maximum photo interval")
    if minimum > maximum:
        raise ValueError("The minimum photo interval must not exceed the maximum.")
    number(result["fade_seconds"], .1, 3, "Fade duration")
    number(result["corner_radius"], 0, 80, "Corner radius")
    if not isinstance(result["snap_enabled"], bool):
        raise ValueError("Snap to grid must be true or false.")
    number(result["snap_spacing"], 4, 200, "Grid spacing")
    frames = result["frames"]
    if not isinstance(frames, list) or len(frames) > 12:
        raise ValueError("Use up to 12 photo frames.")
    identifiers = set()
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("Each photo frame must be a settings object.")
        identifier = frame.get("id", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", identifier):
            raise ValueError("Each frame needs a valid identifier.")
        if identifier in identifiers:
            raise ValueError("Frame identifiers must be unique.")
        identifiers.add(identifier)
        if not isinstance(frame.get("name"), str) or len(frame["name"]) > 80:
            raise ValueError("Frame names must have at most 80 characters.")
        number(frame.get("size"), 160, 1000, "Frame size")
        frame.pop("interval_seconds", None)
        for axis in ("x", "y"):
            number(frame.get(axis), -20000, 20000, "Frame position")
    for section in ("player", "graphs", "visualizer"):
        if not isinstance(result[section], dict):
            raise ValueError(f"{section.title()} settings must be an object.")
        for axis in ("x", "y"):
            number(result[section].get(axis), -20000, 20000, "Widget position")
    number(result["player"].get("width"), 480, 1000, "Turntable width")
    number(result["visualizer"].get("width"), 400, 1000, "Visualizer width")
    if not isinstance(result["visualizer"].get("enabled"), bool):
        raise ValueError("Visualizer visibility must be true or false.")
    return result


def load_config(path):
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    return validate_config(json.loads(path.read_text()))


def save_config(path, data):
    validated = validate_config(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(validated, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def contain_rect(image_width, image_height, width, height=None):
    height = width if height is None else height
    if min(image_width, image_height, width, height) <= 0:
        raise ValueError("Image and frame dimensions must be positive.")
    scale = min(width / image_width, height / image_height)
    drawn_width, drawn_height = image_width * scale, image_height * scale
    return ((width - drawn_width) / 2, (height - drawn_height) / 2, drawn_width, drawn_height)


def clamp_position(x, y, width, height, screen_width, screen_height):
    return (round(max(0, min(x, screen_width - width))),
            round(max(0, min(y, screen_height - height))))


def _crosses(start, length, other_start, other_length, tolerance):
    return min(start + length, other_start + other_length) - max(start, other_start) >= -tolerance


def snap_axis(position, length, cross_start, cross_length, others, origin=0,
              spacing=24, tolerance=None):
    """Snap one rectangle axis, giving equal gaps precedence over grid points."""
    spacing = max(1, float(spacing))
    tolerance = max(6, spacing * .65) if tolerance is None else max(0, float(tolerance))
    related = [(start, start + size) for start, size, other_cross, other_cross_size in others
               if _crosses(cross_start, cross_length, other_cross, other_cross_size, tolerance)]
    candidates = []

    def add(value, priority):
        distance = abs(value - position)
        if priority < 2 and distance > tolerance:
            return
        candidates.append((priority, distance, value))

    ordered = sorted(related)
    for index, (left, left_end) in enumerate(ordered):
        for right, right_end in ordered[index + 1:]:
            gap = right - left_end
            if gap < 0:
                continue
            # Put the moving rectangle between this pair with identical gaps.
            available = gap - length
            if available >= 0:
                add(left_end + available / 2, 0)
            # Continue the existing rhythm to either side of the pair.
            add(right_end + gap, 0)
            add(left - gap - length, 0)

    for other_start, other_end in related:
        add(other_start, 1)
        add(other_end - length, 1)
        add(other_start + (other_end - other_start - length) / 2, 1)

    grid = origin + round((position - origin) / spacing) * spacing
    add(grid, 2)
    return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]


def conky_gap(position, dpi=96, border=13):
    """Invert Conky 1.19's text-origin coordinates and Xft DPI rounding."""
    scale = max(1, dpi) / 96
    border_pixels = math.floor(border * scale + .5)
    estimate = round((position + border_pixels) / scale)
    return min(range(estimate - 1, estimate + 2),
               key=lambda gap: abs(math.floor(gap * scale + .5) - border_pixels - position))


def fade_fraction(elapsed, duration):
    fraction = max(0.0, min(1.0, elapsed / max(.001, duration)))
    return fraction * fraction * (3 - 2 * fraction)


def format_time(microseconds):
    seconds = max(0, int(microseconds // 1_000_000))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PhotoDeck:
    """Reserve outgoing and incoming photos until fades finish in every frame."""

    def __init__(self, files=(), seed=None):
        self.random = random.Random(seed)
        self.files = list(dict.fromkeys(map(str, files)))
        self.queue = []
        self.leases = {}
        self.failed = set()

    def replace_files(self, files):
        self.files = list(dict.fromkeys(map(str, files)))
        self.failed.intersection_update(self.files)
        self.queue = []

    def reserve_next(self, frame_id):
        occupied = set().union(*self.leases.values()) if self.leases else set()
        candidates = set(self.files) - occupied - self.failed
        if not candidates:
            return None
        choices = [path for path in self.queue if path in candidates]
        if not choices:
            self.queue = self.files.copy()
            self.random.shuffle(self.queue)
            choices = [path for path in self.queue if path in candidates]
        selected = choices[0]
        self.queue.remove(selected)
        self.leases.setdefault(frame_id, set()).add(selected)
        return selected

    def settle(self, frame_id, current):
        self.leases[frame_id] = {current} if current else set()

    def reject(self, frame_id, path):
        self.failed.add(path)
        self.leases.get(frame_id, set()).discard(path)

    def release(self, frame_id):
        self.leases.pop(frame_id, None)


class PlaybackClock:
    def __init__(self):
        self.position = 0
        self.length = 0
        self.updated_at = 0.0
        self.playing = False
        self.rate = 1.0

    def at(self, now):
        elapsed = max(0.0, now - self.updated_at) if self.playing else 0.0
        position = max(0, round(self.position + elapsed * self.rate * 1_000_000))
        return min(position, self.length) if self.length > 0 else 0

    def update(self, now, position=None, playing=None, length=None, rate=None):
        previous_position = self.at(now)
        if length is not None:
            self.length = max(0, int(length))
        self.position = previous_position if position is None else max(0, int(position))
        self.updated_at = now
        if playing is not None:
            self.playing = bool(playing)
        if rate is not None:
            self.rate = max(0.0, float(rate))


def seek_arguments(track_id, fraction, length, can_seek):
    if not can_seek or not track_id or track_id == "/org/mpris/MediaPlayer2/TrackList/NoTrack":
        return None
    if not track_id.startswith("/") or length <= 0 or not math.isfinite(fraction):
        return None
    return track_id, round(max(0, min(1, fraction)) * length)
