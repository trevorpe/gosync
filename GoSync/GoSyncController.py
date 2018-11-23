# gosync is an open source Google Drive(TM) sync application for Linux
#
# Copyright (C) 2015 Himanshu Chauhan
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

import math
import os
import sys

from defines import *
from GoSyncEvents import *
from GoSyncModel import ClientSecretsNotFound, GoSyncModel

HERE = os.path.abspath(os.path.dirname(__file__))


class GoSyncController(object):
    def __init__(self):
        try:
            self.sync_model = GoSyncModel()
        except ClientSecretsNotFound as e:
            print(e)
            sys.exit(1)
        except:
            raise

        self.aboutdrive = self.sync_model.get_drive_info()

        event_controller = GoSyncEventController()

        # event_controller.register_listener(GOSYNC_EVENT_SYNC_STARTED,
        #                                    self.OnSyncStarted)
        # event_controller.register_listener(GOSYNC_EVENT_SYNC_UPDATE,
        #                                    self.OnSyncUpdate)
        # event_controller.register_listener(GOSYNC_EVENT_SYNC_DONE,
        #                                    self.OnSyncDone)
        # event_controller.register_listener(GOSYNC_EVENT_SYNC_TIMER,
        #                                    self.OnSyncTimer)
        # event_controller.register_listener(GOSYNC_EVENT_SYNC_INV_FOLDER,
        #                                    self.OnSyncInvalidFolder)

        self.sync_model.start()

    def OnSyncInvalidFolder(self, event):
        print(
            'Some of the folders to be sync\'ed were not found on '
            'remote server.\nPlease check.\n'
        )

    def OnSyncTimer(self, event):
        unicode_string = event.data.pop()
        self.sb.SetStatusText(unicode_string.encode('ascii', 'ignore'))

    def OnSyncStarted(self, event):
        self.sb.SetStatusText("Sync started...")

    def OnSyncUpdate(self, event):
        unicode_string = event.data.pop()
        self.sb.SetStatusText(unicode_string.encode('ascii', 'ignore'))

    def OnSyncDone(self, event):
        if not event.data:
            self.sb.SetStatusText("Sync completed.")
        else:
            self.sb.SetStatusText("Sync failed. Please check the logs.")

    def FileSizeHumanize(self, size):
        size = abs(size)
        if (size == 0):
            return "0B"
        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
        p = math.floor(math.log(size, 2) / 10)
        return "%.3f%s" % (size / math.pow(1024, p), units[int(p)])

    def OnExit(self, event):
        dial = wx.MessageDialog(
            None,
            'GoSync will stop syncing files until restarted.\n'
            'Are you sure to quit?\n',
            'Question', wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION
        )
        res = dial.ShowModal()
        if res == wx.ID_YES:
            wx.CallAfter(self.Destroy)

    def OnToggleSync(self, evt):
        if self.sync_model.is_sync_enabled():
            self.sync_model.stop_sync()
            self.sb.SetStatusText("Paused", 1)
        else:
            self.sync_model.start_sync()
            self.sb.SetStatusText("Running", 1)
