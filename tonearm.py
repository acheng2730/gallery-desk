"""Stable per-track arm positions inside the turntable's available clearance."""

import math
import random


# With the current pivot and head, these angles keep the entire head between
# the 58 px album label and the 119 px vinyl edge, with room for antialiasing.
PLAYING_ANGLES = tuple(math.radians(degrees) for degrees in range(-11, 4))
PARKED_ANGLE = math.radians(-27)


class Tonearm:
    def __init__(self):
        self.track_id = None
        self.playing_angle = None

    def angle_for(self, track_id, playing):
        if not isinstance(track_id, str) or not track_id.startswith("/") or track_id == "/org/mpris/MediaPlayer2/TrackList/NoTrack":
            return PARKED_ANGLE
        if track_id != self.track_id:
            # Leave at least two degrees between successive choices so a new
            # track visibly moves the head. Pause/resume keeps that choice.
            choices = [angle for angle in PLAYING_ANGLES
                       if self.playing_angle is None or abs(angle - self.playing_angle) > math.radians(1.5)]
            self.playing_angle = random.choice(choices)
            self.track_id = track_id
        return self.playing_angle if playing else PARKED_ANGLE
