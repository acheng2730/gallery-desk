"""Spotify's local MPRIS connection; all bus and artwork requests are asynchronous."""

import logging
import time

from gi.repository import Gio, GLib

from assets import pixbuf
from core import PlaybackClock, seek_arguments


BUS_NAME = "org.mpris.MediaPlayer2.spotify"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"


class Spotify:
    def __init__(self, loader, changed, bus_name=BUS_NAME):
        self.loader = loader
        self.changed = changed
        self.bus_name = bus_name
        self.proxy = None
        self.clock = PlaybackClock()
        self.properties = {}
        self.metadata = {}
        self.art = None
        self.art_uri = ""
        self.connected = False
        self.generation = 0
        self.poll_pending = False
        self.closed = False
        self.error = ""
        self.watcher = Gio.bus_watch_name(Gio.BusType.SESSION, bus_name,
                                         Gio.BusNameWatcherFlags.NONE,
                                         self._appeared, self._vanished)
        self.poll_timer = GLib.timeout_add_seconds(5, self.poll_position)

    @property
    def track_id(self):
        return self.metadata.get("mpris:trackid", "")

    @property
    def title(self):
        return self.metadata.get("xesam:title", "Ready when you are" if self.connected else "Your listening corner")

    @property
    def artist(self):
        value = self.metadata.get("xesam:artist", [])
        return ", ".join(value) if isinstance(value, list) else str(value)

    def _appeared(self, connection, name, owner):
        self.generation += 1
        generation = self.generation

        def ready(source, result):
            try:
                proxy = Gio.DBusProxy.new_finish(result)
            except GLib.Error as error:
                logging.warning("Spotify connection: %s", error.message)
                return
            if self.closed or generation != self.generation:
                return
            self.proxy = proxy
            self.connected = True
            proxy.connect("g-properties-changed", self._properties_changed)
            proxy.connect("g-signal", self._signal)
            values = {key: proxy.get_cached_property(key).unpack()
                      for key in proxy.get_cached_property_names() or []}
            self._apply(values)
            self.poll_position()

        Gio.DBusProxy.new(connection, Gio.DBusProxyFlags.NONE, None, owner,
                         OBJECT_PATH, PLAYER_INTERFACE, None, ready)

    def _vanished(self, connection, name):
        self.generation += 1
        self.proxy = None
        self.connected = False
        self.poll_pending = False
        self.properties = {}
        self.metadata = {}
        self.art = None
        self.art_uri = ""
        self.clock.update(time.monotonic(), position=0, length=0, playing=False)
        if not self.closed:
            self.changed()

    def _properties_changed(self, proxy, changed, invalidated):
        if proxy != self.proxy:
            return
        self._apply(changed.unpack())
        if invalidated:
            self.poll_position()

    def _apply(self, values):
        old_track = self.track_id
        self.properties.update(values)
        self.metadata = self.properties.get("Metadata", {})
        track_changed = old_track != self.track_id
        position = values.get("Position", 0 if track_changed else None)
        self.clock.update(time.monotonic(), position=position,
                          length=self.metadata.get("mpris:length", 0),
                          playing=self.properties.get("PlaybackStatus") == "Playing",
                          rate=self.properties.get("Rate", 1))
        uri = self.metadata.get("mpris:artUrl", "")
        if uri != self.art_uri:
            self.art_uri = uri
            self.art = None
            generation = self.generation

            def loaded(path, error):
                if self.closed or uri != self.art_uri or generation != self.generation:
                    return
                if error:
                    logging.warning("Album artwork: %s", error)
                else:
                    try:
                        self.art = pixbuf(path)
                    except GLib.Error as exception:
                        logging.warning("Album artwork decode: %s", exception.message)
                self.changed()

            if uri:
                self.loader.artwork(uri, loaded)
        self.error = ""
        self.changed()
        if track_changed and "Position" not in values:
            self.poll_position()

    def _signal(self, proxy, sender, signal, parameters):
        if proxy == self.proxy and signal == "Seeked":
            self.clock.update(time.monotonic(), position=parameters.unpack()[0])
            self.changed()

    def poll_position(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if self.proxy is None or self.poll_pending:
            return GLib.SOURCE_CONTINUE
        proxy, generation, track_id = self.proxy, self.generation, self.track_id
        self.poll_pending = True

        def received(source, result):
            try:
                response = source.call_finish(result)
                position = response.unpack()[0]
            except GLib.Error as error:
                logging.debug("Spotify position unavailable: %s", error.message)
                position = None
            if generation != self.generation or self.closed:
                return
            self.poll_pending = False
            if position is not None and track_id == self.track_id:
                self.clock.update(time.monotonic(), position=position)
                self.changed()

        proxy.get_connection().call(proxy.get_name(), OBJECT_PATH,
                                    "org.freedesktop.DBus.Properties", "Get",
                                    GLib.Variant("(ss)", (PLAYER_INTERFACE, "Position")),
                                    GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE,
                                    3000, None, received)
        return GLib.SOURCE_CONTINUE

    def command(self, method):
        permission = {"Previous": "CanGoPrevious", "Next": "CanGoNext",
                      "PlayPause": "CanPause" if self.clock.playing else "CanPlay"}.get(method)
        if permission and self.properties.get(permission, False):
            self._call(method, None)

    def seek(self, fraction, track_id):
        if track_id != self.track_id:
            return False
        arguments = seek_arguments(track_id, fraction, self.clock.length,
                                   self.properties.get("CanSeek", False))
        if arguments is None or self.proxy is None:
            return False
        self._call("SetPosition", GLib.Variant("(ox)", arguments))
        return True

    def _call(self, method, parameters):
        if self.proxy is None:
            return
        proxy, generation = self.proxy, self.generation

        def finished(source, result):
            try:
                source.call_finish(result)
            except GLib.Error as error:
                if generation == self.generation and not self.closed:
                    self.error = "Spotify could not complete that action"
                    logging.warning("Spotify %s: %s", method, error.message)
                    self.changed()
                return
            if generation == self.generation and not self.closed:
                self.poll_position()

        proxy.call(method, parameters, Gio.DBusCallFlags.NONE, 3000, None, finished)

    def open_spotify(self):
        try:
            Gio.AppInfo.launch_default_for_uri("spotify:", None)
        except GLib.Error as error:
            self.error = "Open Spotify from your applications"
            logging.warning("Opening Spotify: %s", error.message)
            self.changed()

    def close(self):
        self.closed = True
        self.generation += 1
        Gio.bus_unwatch_name(self.watcher)
        GLib.source_remove(self.poll_timer)
        self.proxy = None
