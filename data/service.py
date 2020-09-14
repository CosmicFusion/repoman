#!/usr/bin/python3
'''
   Copyright 2017 Ian Santopietro (ian@system76.com)

   This file is part of Repoman.

    Repoman is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Repoman is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Repoman.  If not, see <http://www.gnu.org/licenses/>.
'''

import gi
from gi.repository import GObject, GLib

import apt
import dbus
import dbus.service
import dbus.mainloop.glib
import logging
import sys
import time
import os

from softwareproperties.SoftwareProperties import SoftwareProperties
from aptsources.sourceslist import SourceEntry

import repolib

class RepomanException(dbus.DBusException):
    _dbus_error_name = 'org.pop_os.repoman.RepomanException'

class PermissionDeniedByPolicy(dbus.DBusException):
    _dbus_error_name = 'org.pop_os.repoman.PermissionDeniedByPolicy'

class AptException(Exception):
    pass

class PPA(dbus.service.Object):
    def __init__(self, conn=None, object_path=None, bus_name=None):
        dbus.service.Object.__init__(self, conn, object_path, bus_name)

        # These are used by PolKit to check privileges
        self.dbus_info = None
        self.polkit = None
        self.enforce_polkit = True

        try:
            self.system_repo = repolib.SystemSource()
        except:
            self.system_repo = None
        self.sp = SoftwareProperties()
        self.cache = apt.Cache()

    @dbus.service.method(
        "org.pop_os.repoman.Interface",
        in_signature='', out_signature='',
        sender_keyword='sender', connection_keyword='conn'
    )
    def exit(self, sender=None, conn=None):
        mainloop.quit()
    
    @dbus.service.method(
        "org.pop_os.repoman.Interface",
        in_signature='sb', out_signature='b',
        sender_keyword='sender', connection_keyword='conn'
    )
    def set_system_comp_enabled(self, comp, enable, sender=None, conn=None):
        """ Enable or disable a component in the system source. 
        
        Arguments:
            comp (str): the component to set
            enable (bool): The new state to set, True = Enabled.
        """
        if self.system_repo:
            self.system_repo.load_from_file()
            self.system_repo.set_component_enabled(component=comp, enabled=enable)
            self.system_repo.save_to_disk()
            return True
        return False

    ## TODO: These are old SoftwareProperties Methods, and need to be replaced.
    
    @dbus.service.method(
        'org.pop_os.repoman.Interface',
        in_signature='s', out_signature='bs',
        sender_keyword='sender', connection_keyword='conn'
    )
    def add_repo(self, line, sender=None, conn=None):
        self._check_polkit_privilege(
            sender, conn, 'org.pop_os.repoman.modifysources'
        )

        try:
            self.sp.add_source_from_line(line)
            self.sp.sourceslist.save()
            self.cache.open()
            self.cache.update()
            self.cache.open(None)
            self.sp.reload_sourceslist()
            return [True, '']
        except Exception as e:
            print(str(e))
            return [False, str(e)]
    
    @dbus.service.method(
        'org.pop_os.repoman.Interface',
        in_signature='s', out_signature='bs',
        sender_keyword='sender', connection_keyword='conn'
    )
    def delete_repo(self, repo, sender=None, conn=None):
        self._check_polkit_privilege(
            sender, conn, 'org.pop_os.repoman.modifysources'
        )
        
        try:
            self.sp.remove_source(repo, remove_source_code=True)
            self.sp.sourceslist.save()
            self.cache.open()
            self.cache.update()
            self.cache.open(None)
            self.sp.reload_sourceslist()
            return [True, '']
        except Exception as e:
            print(str(e))
            return [False, str(e)]

    @dbus.service.method(
        'org.pop_os.repoman.Interface',
        in_signature='ss', out_signature='bs',
        sender_keyword='sender', connection_keyword='conn'
    )
    def modify_repo(self, old_repo, new_repo, sender=None, conn=None):
        self._check_polkit_privilege(
            sender, conn, 'org.pop_os.repoman.modifysources'
        )

        try:
            old_source = self._find_source_from_string(old_repo)
            index = self.sp.sourceslist.list.index(old_source)
            file = self.sp.sourceslist.list[index].file
            new_source_entry = SourceEntry(new_repo, file)
            self.sp.sourceslist.list[index] = new_source_entry
            self.sp.sourceslist.save()
            self.cache.open()
            self.cache.update()
            self.cache.open(None)
            self.sp.reload_sourceslist()
            return [True, '']
        except Exception as e:
            print(str(e))
            return [False, str(e)]
    
    @dbus.service.method(
        'org.pop_os.repoman.Interface',
        in_signature='b', out_signature='i',
        sender_keyword='sender', connection_keyword='conn'
    )
    def set_source_code_enabled(self, enabled, sender=None, conn=None):
        self._check_polkit_privilege(
            sender, conn, 'org.pop_os.repoman.modifysources'
        )

        if enabled:
            self.sp.enable_source_code_sources()
        else:
            self.sp.disable_source_code_sources()
        return 0
    
    @dbus.service.method(
        'org.pop_os.repoman.Interface',
        in_signature='sb', out_signature='i',
        sender_keyword='sender', connection_keyword='conn'
    )
    def set_child_enabled(self, child, enabled, sender=None, conn=None):
        self._check_polkit_privilege(
            sender, conn, 'org.pop_os.repoman.modifysources'
        )

        if enabled:
            self.sp.enable_child_source(child)
        else:
            self.sp.disable_child_source(child)
        return 0
    
    @dbus.service.method(
        'org.pop_os.repoman.Interface',
        in_signature='sb', out_signature='i',
        sender_keyword='sender', connection_keyword='conn'
    )
    def set_comp_enabled(self, comp, enabled, sender=None, conn=None):
        self._check_polkit_privilege(
            sender, conn, 'org.pop_os.repoman.modifysources'
        )

        if enabled:
            self.sp.enable_component(comp)
        else:
            self.sp.disable_component(comp)
        return 0

    def _find_source_from_string(self, line):
        # ensure that we have a current list, it might have been changed underneath
        # us
        self.sp.reload_sourceslist()

        for source in self.sp.sourceslist.list:
            if str(source) == line:
                return source
        return None

    @classmethod
    def _log_in_file(klass, filename, string):
        date = time.asctime(time.localtime())
        ff = open(filename, "a")
        ff.write("%s : %s\n" %(date,str(string)))
        ff.close()
    
    @classmethod
    def _strip_source_line(self, source):
        source = source.replace("#", "# ")
        source = source.replace("[", "")
        source = source.replace("]", "")
        source = source.replace("'", "")
        source = source.replace("  ", " ")
        return source

    def _check_polkit_privilege(self, sender, conn, privilege):
        # from jockey
        '''Verify that sender has a given PolicyKit privilege.
        sender is the sender's (private) D-BUS name, such as ":1:42"
        (sender_keyword in @dbus.service.methods). conn is
        the dbus.Connection object (connection_keyword in
        @dbus.service.methods). privilege is the PolicyKit privilege string.
        This method returns if the caller is privileged, and otherwise throws a
        PermissionDeniedByPolicy exception.
        '''
        if sender is None and conn is None:
            # called locally, not through D-BUS
            return
        if not self.enforce_polkit:
            # that happens for testing purposes when running on the session
            # bus, and it does not make sense to restrict operations here
            return

        # get peer PID
        if self.dbus_info is None:
            self.dbus_info = dbus.Interface(conn.get_object('org.freedesktop.DBus',
                '/org/freedesktop/DBus/Bus', False), 'org.freedesktop.DBus')
        pid = self.dbus_info.GetConnectionUnixProcessID(sender)
        
        # query PolicyKit
        if self.polkit is None:
            self.polkit = dbus.Interface(dbus.SystemBus().get_object(
                'org.freedesktop.PolicyKit1',
                '/org/freedesktop/PolicyKit1/Authority', False),
                'org.freedesktop.PolicyKit1.Authority')
        try:
            # we don't need is_challenge return here, since we call with AllowUserInteraction
            (is_auth, _, details) = self.polkit.CheckAuthorization(
                    ('unix-process', {'pid': dbus.UInt32(pid, variant_level=1),
                    'start-time': dbus.UInt64(0, variant_level=1)}), 
                    privilege, {'': ''}, dbus.UInt32(1), '', timeout=600)
        except dbus.DBusException as e:
            if e._dbus_error_name == 'org.freedesktop.DBus.Error.ServiceUnknown':
                # polkitd timed out, connect again
                self.polkit = None
                return self._check_polkit_privilege(sender, conn, privilege)
            else:
                raise

        if not is_auth:
            PPA._log_in_file('/tmp/repoman.log','_check_polkit_privilege: sender %s on connection %s pid %i is not authorized for %s: %s' %
                    (sender, conn, pid, privilege, str(details)))
            raise PermissionDeniedByPolicy(privilege)

if __name__ == '__main__':
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    
    bus = dbus.SystemBus()
    name = dbus.service.BusName("org.pop_os.repoman", bus)
    object = PPA(bus, '/PPA')

    mainloop = GLib.MainLoop()
    mainloop.run()