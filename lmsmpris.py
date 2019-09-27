#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Modul 9 <info@hifiberry.com>
# Based on mpDris2 by
#          Jean-Philippe Braun <eon@patapon.info>,
#          Mantas Mikulėnas <grawity@gmail.com>
# Based on mpDris by:
#          Erik Karlsson <pilo@ayeon.org>
# Some bits taken from quodlibet mpris plugin by:
#           <christoph.reiter@gmx.at>

#
# This creates an MPRIS service for LMS on the system bus
# Implements only a minimal MPRIS subset that is required by HiFiBerryOS
#


from __future__ import print_function

import sys
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
import logging
import time
import threading

from lms import LMS

try:
    from gi.repository import GLib
    using_gi_glib = True
except ImportError:
    import glib as GLib


identity = "LMS client"

# python dbus bindings don't include annotations and properties
MPRIS2_INTROSPECTION = """<node name="/org/mpris/MediaPlayer2">
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect">
      <arg direction="out" name="xml_data" type="s"/>
    </method>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get">
      <arg direction="in" name="interface_name" type="s"/>
      <arg direction="in" name="property_name" type="s"/>
      <arg direction="out" name="value" type="v"/>
    </method>
    <method name="GetAll">
      <arg direction="in" name="interface_name" type="s"/>
      <arg direction="out" name="properties" type="a{sv}"/>
    </method>
    <method name="Set">
      <arg direction="in" name="interface_name" type="s"/>
      <arg direction="in" name="property_name" type="s"/>
      <arg direction="in" name="value" type="v"/>
    </method>
    <signal name="PropertiesChanged">
      <arg name="interface_name" type="s"/>
      <arg name="changed_properties" type="a{sv}"/>
      <arg name="invalidated_properties" type="as"/>
    </signal>
  </interface>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="false"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek">
      <arg direction="in" name="Offset" type="x"/>
    </method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri">
      <arg direction="in" name="Uri" type="s"/>
    </method>
    <signal name="Seeked">
      <arg name="Position" type="x"/>
    </signal>
    <property name="PlaybackStatus" type="s" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="LoopStatus" type="s" access="readwrite">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="Rate" type="d" access="readwrite">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="Shuffle" type="b" access="readwrite">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="Metadata" type="a{sv}" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="Volume" type="d" access="readwrite">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="false"/>
    </property>
    <property name="Position" type="x" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="false"/>
    </property>
    <property name="MinimumRate" type="d" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="MaximumRate" type="d" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="CanGoNext" type="b" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="CanGoPrevious" type="b" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="CanPlay" type="b" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="CanPause" type="b" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="CanSeek" type="b" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="true"/>
    </property>
    <property name="CanControl" type="b" access="read">
      <annotation name="org.freedesktop.DBus.Property.EmitsChangedSignal" value="false"/>
    </property>
  </interface>
</node>"""


class LMSWrapper(threading.Thread):
    """ Wrapper to handle all communications with LMS
    """

    def __init__(self):
        super().__init__()
        self.lms = LMS(find_my_server=True)
        self.playerid = None
        self.playback_status = "unknown"
        self.metadata = {}

        self.dbus_service = None

        self.bus = dbus.SessionBus()
        self.received_data = False

    def run(self):
        try:
            self.dbus_service = MPRISInterface()
            while True:
                try:
                    self.lms.connect()
                    me = self.lms.client()
                    if me is None:
                        logging.info(
                            "Could not find myself as a client, aborting")
                        self.lms.disconnect()
                        break

                    self.playerid = me["playerid"]
                    logging.info("%s, playerid=%s", self.lms, self.playerid)

                    # subscribe to player status updates
                    self.lms.add_status_listener(self)
                    self.lms.send(
                        "{} status - 1 tags:adKljJ subscribe:1".format(self.playerid))

                    while self.lms.is_connected():
                        self.received_data = False
                        time.sleep(10)
                        if not(self.received_data):
                            logging.warning(
                                "did not receive status updated from LMS, re-connecting")
                            break

                except Exception as e:
                    logging.warning("error communicating with LMS: %s", e)
                # Wait a bit before reconnecting
                time.sleep(30)
        except Exception as e:
            logging.error("LMSWrapper thread died: %s", e)
            sys.exit(1)

    def send_command(self, cmd):
        """
        send commands like play, pause, ...
        """

        commands = {
            "play": "{} play",
            "pause": "{} pause",
            "next": "{} playlist index +1",
            "previous": "{} playlist index +1"
        }

        if cmd in commands:
            lms_cmd = commands[cmd].format(self.playerid)
        else:
            logging. error("command %s not implemented", cmd)
            return

        self.lms.send(lms_cmd)

    def notify_status(self, playerid, lms_meta):
        """
        Translate metadata returned by MPD to the MPRIS v2 syntax.
        http://www.freedesktop.org/wiki/Specifications/mpris-spec/metadata
        """

        if playerid == self.playerid:
            logging.debug("Got status %s", lms_meta)
        else:
            # unexpected status update from another player
            return

        self.received_data = True
        self._metadata = {}

        if "artist" in lms_meta:
            self.metadata["xesam:artist"] = [lms_meta["artist"]]

        if "title" in lms_meta:
            if "xesam:title" not in self._metadata:
                self.metadata["xesam:title"] = lms_meta["title"]

        if "album" in lms_meta:
            if "xesam:album" not in self._metadata:
                self.metadata["xesam:album"] = lms_meta["album"]

        if "artwork_track_id" in lms_meta:
            self.metadata["mpris:artUrl"] = self.lms.cover_url(
                lms_meta["artwork_track_id"])

        if "mode" in lms_meta:
            self.playback_status = lms_meta["mode"]

        # TODO: Implement time and duration tags, repeat and shuffle

    def last_status(self):
        if time.time() - self._time >= 2:
            self.timer_callback()
        return self._status.copy()

    def _update_properties(self, force=False):
        old_status = self._status
        old_position = self._position
        old_time = self._time
        self._currentsong = self.currentsong()
        self._status = new_status = self.status()
        self._time = new_time = int(time.time())
        logging.debug("_update_properties: current song = %r" %
                      self._currentsong)
        logging.debug("_update_properties: current status = %r" % self._status)

        if not new_status:
            return

        if 'elapsed' in new_status:
            new_position = float(new_status['elapsed'])
        elif 'time' in new_status:
            new_position = int(new_status['time'].split(':')[0])
        else:
            new_position = 0

        self._position = new_position

        # "player" subsystem

        if old_status['state'] != new_status['state']:
            self._dbus_service.update_property('org.mpris.MediaPlayer2.Player',
                                               'PlaybackStatus')

        if not force:
            old_id = old_status.get('songid', None)
            new_id = new_status.get('songid', None)
            force = (old_id != new_id)

        if not force:
            if new_status['state'] == 'play':
                expected_position = old_position + (new_time - old_time)
            else:
                expected_position = old_position
            if abs(new_position - expected_position) > 0.6:
                logging.debug("Expected pos %r, actual %r, diff %r" % (
                    expected_position, new_position, new_position - expected_position))
                logging.debug("Old position was %r at %r (%r seconds ago)" % (
                    old_position, old_time, new_time - old_time))
                self._dbus_service.Seeked(new_position * 1000000)

        else:
            # Update current song metadata
            old_meta = self._metadata.copy()
            self.update_metadata()
            new_meta = self._dbus_service.update_property('org.mpris.MediaPlayer2.Player',
                                                          'Metadata')

            if self._params['notify'] and new_status['state'] != 'stop':
                if old_meta.get('xesam:artist', None) != new_meta.get('xesam:artist', None) \
                        or old_meta.get('xesam:album', None) != new_meta.get('xesam:album', None) \
                        or old_meta.get('xesam:title', None) != new_meta.get('xesam:title', None) \
                        or old_meta.get('xesam:url', None) != new_meta.get('xesam:url', None):
                    self.notify_about_track(new_meta, new_status['state'])

        # "options" subsystem
        # also triggered if consume, crossfade or ReplayGain are updated

        if old_status['random'] != new_status['random']:
            self._dbus_service.update_property('org.mpris.MediaPlayer2.Player',
                                               'Shuffle')

        if (old_status['repeat'] != new_status['repeat']
                or old_status.get('single', 0) != new_status.get('single', 0)):
            self._dbus_service.update_property('org.mpris.MediaPlayer2.Player',
                                               'LoopStatus')

        if ("nextsongid" in old_status) != ("nextsongid" in new_status):
            self._dbus_service.update_property('org.mpris.MediaPlayer2.Player',
                                               'CanGoNext')


class MPRISInterface(dbus.service.Object):
    ''' The base object of an MPRIS player '''

    PATH = "/org/mpris/MediaPlayer2"
    INTROSPECT_INTERFACE = "org.freedesktop.DBus.Introspectable"
    PROP_INTERFACE = dbus.PROPERTIES_IFACE

    def __init__(self):
        dbus.service.Object.__init__(self, dbus.SystemBus(),
                                     MPRISInterface.PATH)
        self.name = "org.mpris.MediaPlayer2.lms"
        self.bus = dbus.SystemBus()
        self.uname = self.bus.get_unique_name()
        self.dbus_obj = self.bus.get_object("org.freedesktop.DBus",
                                            "/org/freedesktop/DBus")
        self.dbus_obj.connect_to_signal("NameOwnerChanged",
                                        self.name_owner_changed_callback,
                                        arg0=self.name)

        self.acquire_name()
        logging.info("name on DBus aqcuired")

    def name_owner_changed_callback(self, name, old_owner, new_owner):
        if name == self.name and old_owner == self.uname and new_owner != "":
            try:
                pid = self._dbus_obj.GetConnectionUnixProcessID(new_owner)
            except:
                pid = None
            logging.info("Replaced by %s (PID %s)" %
                         (new_owner, pid or "unknown"))
            loop.quit()

    def acquire_name(self):
        self.bus_name = dbus.service.BusName(self.name,
                                             bus=self.bus,
                                             allow_replacement=True,
                                             replace_existing=True)

    def release_name(self):
        if hasattr(self, "_bus_name"):
            del self.bus_name

    ROOT_INTERFACE = "org.mpris.MediaPlayer2"
    ROOT_PROPS = {
        "CanQuit": (False, None),
        "CanRaise": (False, None),
        "DesktopEntry": ("lmsmpris", None),
        "HasTrackList": (False, None),
        "Identity": (identity, None),
        "SupportedUriSchemes": (dbus.Array(signature="s"), None),
        "SupportedMimeTypes": (dbus.Array(signature="s"), None)
    }

    @dbus.service.method(INTROSPECT_INTERFACE)
    def Introspect(self):
        return MPRIS2_INTROSPECTION

    def get_playback_status():
        status = lms_wrapper.playback_status
        return {'play': 'Playing',
                'pause': 'Paused',
                'stop': 'Stopped',
                'unknown': 'Unknown'}[status]

    def get_metadata():
        return dbus.Dictionary(lms_wrapper.metadata, signature='sv')

#     def __get_position():
#         status = lms_wrapper.last_status()
#         if 'time' in status:
#             current, end = status['time'].split(':')
#             return dbus.Int64((int(current) * 1000000))
#         else:
#             return dbus.Int64(0)

    PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
    PLAYER_PROPS = {
        "PlaybackStatus": (get_playback_status, None),
        "Rate": (1.0, None),
        "Metadata": (get_metadata, None),
        #        "Position": (__get_position, None),
        "MinimumRate": (1.0, None),
        "MaximumRate": (1.0, None),
        "CanGoNext": (True, None),
        "CanGoPrevious": (True, None),
        "CanPlay": (True, None),
        "CanPause": (True, None),
        "CanSeek": (False, None),
        "CanControl": (False, None),
    }

    PROP_MAPPING = {
        PLAYER_INTERFACE: PLAYER_PROPS,
        ROOT_INTERFACE: ROOT_PROPS,
    }

    @dbus.service.signal(PROP_INTERFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed_properties,
                          invalidated_properties):
        pass

    @dbus.service.method(PROP_INTERFACE,
                         in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        getter, _setter = self.PROP_MAPPING[interface][prop]
        if callable(getter):
            return getter()
        return getter

    @dbus.service.method(PROP_INTERFACE,
                         in_signature="ssv", out_signature="")
    def Set(self, interface, prop, value):
        _getter, setter = self.PROP_MAPPING[interface][prop]
        if setter is not None:
            setter(value)

    @dbus.service.method(PROP_INTERFACE,
                         in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        read_props = {}
        props = self.PROP_MAPPING[interface]
        for key, (getter, _setter) in props.items():
            if callable(getter):
                getter = getter()
            read_props[key] = getter
        return read_props

    def update_property(self, interface, prop):
        getter, _setter = self.__prop_mapping[interface][prop]
        if callable(getter):
            value = getter()
        else:
            value = getter
        logging.debug('Updated property: %s = %s' % (prop, value))
        self.PropertiesChanged(interface, {prop: value}, [])
        return value

    # Player methods
    @dbus.service.method(PLAYER_INTERFACE, in_signature='', out_signature='')
    def Next(self):
        logging.debug("received DBUS next")
        lms_wrapper.send_command("next")
        return

    @dbus.service.method(PLAYER_INTERFACE, in_signature='', out_signature='')
    def Previous(self):
        logging.debug("received DBUS previous")
        lms_wrapper.send_command("previous")
        return

    @dbus.service.method(PLAYER_INTERFACE, in_signature='', out_signature='')
    def Pause(self):
        logging.debug("received DBUS pause")
        lms_wrapper.send_command("pause")
        return

    @dbus.service.method(PLAYER_INTERFACE, in_signature='', out_signature='')
    def PlayPause(self):
        logging.debug("received DBUS play/pause")
        status = lms_wrapper.status()
        if status['state'] == 'play':
            lms_wrapper.send_command("pause")
        else:
            lms_wrapper.send_command("play")
        return

    @dbus.service.method(PLAYER_INTERFACE, in_signature='', out_signature='')
    def Stop(self):
        logging.debug("received DBUS stop")
        lms_wrapper.send_command("stop")
        return

    @dbus.service.method(PLAYER_INTERFACE, in_signature='', out_signature='')
    def Play(self):
        lms_wrapper.send_command("play")
        return

#     @dbus.service.method(__player_interface, in_signature='x', out_signature='')
#     def Seek(self, offset):
#         status = lms_wrapper.status()
#         current, end = status['time'].split(':')
#         current = int(current)
#         end = int(end)
#         offset = int(offset) / 1000000
#         if current + offset <= end:
#             position = current + offset
#             if position < 0:
#                 position = 0
#             lms_wrapper.seekid(int(status['songid']), position)
#             self.Seeked(position * 1000000)
#         return
#
#     @dbus.service.method(__player_interface, in_signature='ox', out_signature='')
#     def SetPosition(self, trackid, position):
#         song = lms_wrapper.last_currentsong()
#         # FIXME: use real dbus objects
#         if str(trackid) != '/org/mpris/MediaPlayer2/Track/%s' % song['id']:
#             return
#         # Convert position to seconds
#         position = int(position) / 1000000
#         if position <= int(song['time']):
#             lms_wrapper.seekid(int(song['id']), position)
#             self.Seeked(position * 1000000)
#         return
#
#     @dbus.service.signal(__player_interface, signature='x')
#     def Seeked(self, position):
#         logging.debug("Seeked to %i" % position)
#         return float(position)


if __name__ == '__main__':
    DBusGMainLoop(set_as_default=True)

    if len(sys.argv) > 1:
        if "-v" in sys.argv:
            logging.basicConfig(format='%(levelname)s: %(name)s - %(message)s',
                                level=logging.DEBUG)
            logging.debug("enabled verbose logging")
    else:
        logging.basicConfig(format='%(levelname)s: %(name)s - %(message)s',
                            level=logging.INFO)

    # Set up the main loop
    loop = GLib.MainLoop()

    # Create wrapper to handle connection failures with MPD more gracefully
    try:
        lms_wrapper = LMSWrapper()
        lms_wrapper.start()
        logging.info("LMS poller thread started")
    except dbus.exceptions.DBusException as e:
        logging.error("DBUS error: %s", e)
        sys.exit(1)

    time.sleep(2)
    if not (lms_wrapper.is_alive()):
        logging.error("LMS connector thread died, exiting")
        sys.exit(1)

    # Run idle loop
    try:
        logging.info("main loop started")
        loop.run()
    except KeyboardInterrupt:
        logging.debug('Caught SIGINT, exiting.')
