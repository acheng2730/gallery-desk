"""Run on an isolated session bus; never send playback commands to real Spotify."""

import os
import time
import unittest

from gi.repository import Gio, GLib

from media import OBJECT_PATH, PLAYER_INTERFACE, Spotify


XML = """<node><interface name="org.mpris.MediaPlayer2.Player">
<method name="PlayPause"/><method name="Previous"/><method name="Next"/>
<method name="SetPosition"><arg type="o" direction="in"/><arg type="x" direction="in"/></method>
<property name="Metadata" type="a{sv}" access="read"/>
<property name="Position" type="x" access="read"/>
<property name="PlaybackStatus" type="s" access="read"/>
<property name="Rate" type="d" access="read"/>
<property name="CanPlay" type="b" access="read"/>
<property name="CanPause" type="b" access="read"/>
<property name="CanGoNext" type="b" access="read"/>
<property name="CanGoPrevious" type="b" access="read"/>
<property name="CanSeek" type="b" access="read"/>
<signal name="Seeked"><arg type="x"/></signal>
</interface></node>"""


class NoArtwork:
    def artwork(self, uri, callback):
        raise AssertionError("The test service does not publish artwork")


@unittest.skipUnless(os.environ.get("GALLERY_TEST_BUS") == "1", "Use dbus-run-session with GALLERY_TEST_BUS=1")
class MprisTests(unittest.TestCase):
    def setUp(self):
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.name = "org.mpris.MediaPlayer2.gallery_test"
        self.calls = []
        self.track = "/test/track_one"
        self.position = 30_000_000
        self.registration = self.connection.register_object(OBJECT_PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
                                                             self.call, self.get_property, None)
        self.owner = Gio.bus_own_name_on_connection(self.connection, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        self.client = Spotify(NoArtwork(), lambda: None, self.name)
        self.until(lambda: self.client.connected and self.client.track_id == self.track)

    def until(self, predicate):
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 3
        while not predicate():
            while context.pending():
                context.iteration(False)
            if time.monotonic() > deadline:
                self.fail("The isolated MPRIS service did not deliver the expected event")
            time.sleep(.002)

    def get_property(self, connection, sender, path, interface, name):
        if name == "Metadata":
            return GLib.Variant("a{sv}", {"mpris:trackid": GLib.Variant("o", self.track),
                                         "mpris:length": GLib.Variant("x", 240_000_000),
                                         "xesam:title": GLib.Variant("s", "Test record"),
                                         "xesam:artist": GLib.Variant("as", ["Test artist"])})
        if name == "Position":
            return GLib.Variant("x", self.position)
        if name == "PlaybackStatus":
            return GLib.Variant("s", "Paused")
        if name == "Rate":
            return GLib.Variant("d", 1.)
        return GLib.Variant("b", True)

    def call(self, connection, sender, path, interface, method, parameters, invocation):
        self.calls.append((method, parameters.unpack()))
        if method == "SetPosition":
            self.position = parameters.unpack()[1]
            self.connection.emit_signal(None, OBJECT_PATH, PLAYER_INTERFACE, "Seeked", GLib.Variant("(x)", (self.position,)))
        invocation.return_value(None)

    def tearDown(self):
        self.client.close()
        Gio.bus_unown_name(self.owner)
        self.connection.unregister_object(self.registration)
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def test_metadata_and_initial_position_are_read_from_player(self):
        self.until(lambda: self.client.clock.position == self.position)
        self.assertEqual(self.client.title, "Test record")
        self.assertEqual(self.client.artist, "Test artist")
        self.assertFalse(self.client.clock.playing)

    def test_transport_and_seek_reach_the_service_with_microseconds(self):
        for command in ("Previous", "PlayPause", "Next"):
            self.client.command(command)
        self.assertTrue(self.client.seek(.25, self.track))
        self.until(lambda: len(self.calls) == 4)
        self.assertEqual(self.calls, [("Previous", ()), ("PlayPause", ()), ("Next", ()),
                                      ("SetPosition", (self.track, 60_000_000))])
        self.until(lambda: self.client.clock.position == 60_000_000)

    def test_seek_started_on_an_old_track_is_rejected(self):
        self.assertFalse(self.client.seek(.5, "/test/old_track"))
        self.assertEqual(self.calls, [])

    def test_disconnection_clears_metadata_and_reconnection_recovers(self):
        Gio.bus_unown_name(self.owner)
        self.until(lambda: not self.client.connected)
        self.assertEqual(self.client.metadata, {})
        self.owner = Gio.bus_own_name_on_connection(self.connection, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        self.until(lambda: self.client.connected and self.client.title == "Test record")
