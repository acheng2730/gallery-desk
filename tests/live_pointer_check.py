"""Opt-in desktop check using real XTest pointer events, restoring the layout."""

import argparse
import ctypes
import json
from pathlib import Path
import time

from gi.repository import Gio, GLib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-desktop", action="store_true", required=True)
    parser.parse_args()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(method, signature=None, values=()):
        return bus.call_sync("org.alan.GalleryDesk", "/org/alan/GalleryDesk/Control",
                             "org.alan.GalleryDesk.Control", method,
                             GLib.Variant(signature, values) if signature else None,
                             None, Gio.DBusCallFlags.NONE, 5000, None)

    def state():
        return json.loads(call("GetState").unpack()[0])

    x11 = ctypes.CDLL("libX11.so.6")
    xtest = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                  ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                                  *([ctypes.POINTER(ctypes.c_int)] * 4), ctypes.POINTER(ctypes.c_uint)]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtest.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    xtest.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError("Cannot open the desktop display")
    root_return, child = ctypes.c_ulong(), ctypes.c_ulong()
    root_x, root_y, local_x, local_y = (ctypes.c_int() for _ in range(4))
    mask = ctypes.c_uint()
    x11.XQueryPointer(display, x11.XDefaultRootWindow(display), ctypes.byref(root_return), ctypes.byref(child),
                      ctypes.byref(root_x), ctypes.byref(root_y), ctypes.byref(local_x), ctypes.byref(local_y), ctypes.byref(mask))
    original_pointer = root_x.value, root_y.value
    initial = state()
    initial_widgets = [(frame["id"], frame, frame["size"], frame["size"]) for frame in initial["frames"]]
    player = initial["player"]
    initial_widgets.append(("player", player, player["width"], round(player["width"] * 350 / 640)))

    def move(x, y):
        xtest.XTestFakeMotionEvent(display, -1, round(x), round(y), 0)
        x11.XSync(display, False)

    def button(pressed):
        xtest.XTestFakeButtonEvent(display, 1, pressed, 0)
        x11.XSync(display, False)

    def drag(x, y, dx, dy):
        move(x, y)
        time.sleep(.15)
        button(True)
        time.sleep(.1)
        try:
            for step in range(1, 7):
                move(x + dx * step / 6, y + dy * step / 6)
                time.sleep(.05)
        finally:
            button(False)
        time.sleep(.15)

    def geometry(identifier):
        current = state()
        value = current["player"] if identifier == "player" else next(frame for frame in current["frames"] if frame["id"] == identifier)
        return value["x"], value["y"], value["width" if identifier == "player" else "size"]

    try:
        call("SetArrange", "(b)", (True,))
        time.sleep(.7)
        for identifier, layout, width, height in initial_widgets:
            drag(layout["x"] + width / 2, layout["y"] + height / 2, 48, 48)
            moved = geometry(identifier)
            assert moved[:2] != (layout["x"], layout["y"]), (identifier, "move", moved)
            if identifier != "graphs":
                drag(moved[0] + width - 12, moved[1] + height - 12, 48, 48)
                resized = geometry(identifier)
                assert resized[2] != moved[2], (identifier, "resize", resized)
            print(identifier, "received pointer drags and resized with snapping", flush=True)
            call("PlaceWidget", "(siii)", (identifier, layout["x"], layout["y"], width))
    finally:
        button(False)
        for identifier, layout, width, height in initial_widgets:
            call("PlaceWidget", "(siii)", (identifier, layout["x"], layout["y"], width))
        call("SetArrange", "(b)", (initial["arranging"],))
        move(*original_pointer)
        x11.XCloseDisplay(display)
    saved = json.loads((Path.home() / ".config/gallery-desk/settings.json").read_text())
    for frame in initial["frames"]:
        restored = next(item for item in saved["frames"] if item["id"] == frame["id"])
        for key in ("x", "y", "size"):
            assert restored[key] == frame[key]
    assert [saved["interval_min_seconds"], saved["interval_max_seconds"]] == initial["photo_interval_range"]
    print("All original positions, sizes, and the timing range restored.")


if __name__ == "__main__":
    main()
