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

import sys
import os
import wx
import math
import webbrowser

from DriveUsageBox import DriveUsageBox
from GoSyncEvents import *
from GoSyncModel import ClientSecretsNotFound
from GoSyncModel import GoSyncModel
from GoSyncSettingsPage import SettingsPage
from defines import *

ID_SYNC_TOGGLE = wx.NewId()

mainWindowStyle = wx.DEFAULT_FRAME_STYLE & (~wx.CLOSE_BOX) & (~wx.MAXIMIZE_BOX)
HERE = os.path.abspath(os.path.dirname(__file__))


class PageAccount(wx.Panel):
    def __init__(self, parent, sync_model):
        wx.Panel.__init__(self, parent, size=parent.GetSize())

        self.sync_model = sync_model
        self.totalFiles = 0

        aboutdrive = sync_model.get_drive_info()
        self.driveUsageBar = DriveUsageBox(self,
                                           long(aboutdrive['quotaBytesTotal']),
                                           -1)
        self.driveUsageBar.SetStatusMessage("Calculating your categorical "
                                            "Google Drive usage. Please wait.")
        self.driveUsageBar.SetMoviesUsage(0)
        self.driveUsageBar.SetDocumentUsage(0)
        self.driveUsageBar.SetOthersUsage(0)
        self.driveUsageBar.SetAudioUsage(0)
        self.driveUsageBar.SetPhotoUsage(0)
        self.driveUsageBar.RePaint()

        mainsizer = wx.BoxSizer(wx.VERTICAL)

        self.SetSizerAndFit(mainsizer)

        GoSyncEventController().BindEvent(self,
                                          GOSYNC_EVENT_CALCULATE_USAGE_STARTED,
                                          self.OnUsageCalculationStarted)
        GoSyncEventController().BindEvent(self,
                                          GOSYNC_EVENT_CALCULATE_USAGE_DONE,
                                          self.OnUsageCalculationDone)
        GoSyncEventController().BindEvent(self,
                                          GOSYNC_EVENT_CALCULATE_USAGE_UPDATE,
                                          self.OnUsageCalculationUpdate)

    def OnUsageCalculationDone(self, event):
        if not event.data:
            self.driveUsageBar.SetStatusMessage(
                "Your Google Drive usage is shown below:"
            )
            self.driveUsageBar.SetMoviesUsage(self.sync_model.movies_usage)
            self.driveUsageBar.SetDocumentUsage(self.sync_model.document_usage)
            self.driveUsageBar.SetOthersUsage(self.sync_model.others_usage)
            self.driveUsageBar.SetAudioUsage(self.sync_model.audio_usage)
            self.driveUsageBar.SetPhotoUsage(self.sync_model.photo_usage)
            self.driveUsageBar.RePaint()
        else:
            self.driveUsageBar.SetStatusMessage(
                "Sorry, could not calculate your Google Drive usage."
            )

    def OnUsageCalculationUpdate(self, event):
        percent = (event.data * 100) / self.totalFiles
        self.driveUsageBar.SetStatusMessage(
            "Calculating your categorical usage... (%d%%)\n" % percent
        )

    def OnUsageCalculationStarted(self, event):
        self.totalFiles = event.data
        self.driveUsageBar.SetStatusMessage(
            "Calculating your categorical Google Drive usage. Please wait."
        )


class GoSyncController(wx.Frame):
    def __init__(self):
        wx.Frame.__init__(self,
                          None,
                          title="GoSync",
                          size=(520, 400),
                          style=mainWindowStyle)

        try:
            self.sync_model = GoSyncModel()
        except ClientSecretsNotFound:
            dial = wx.MessageDialog(None,
                                    'Client secret file was not found!'
                                    '\n\nDo you want to know how to '
                                    'create one?\nError',
                                    wx.YES_NO | wx.ICON_EXCLAMATION)
            res = dial.ShowModal()

            if res == wx.ID_YES:
                webbrowser.open(CLIENT_SECRET_HELP_SITE, new=1, autoraise=True)

            sys.exit(1)
        except:
            dial = wx.MessageDialog(None, 'GoSync failed to initialize\n',
                                    'Error', wx.OK | wx.ICON_EXCLAMATION)
            res = dial.ShowModal()
            raise

        self.aboutdrive = self.sync_model.get_drive_info()

        title_string = "GoSync --%s (%s used of %s)" % (
            self.aboutdrive['name'],
            self.FileSizeHumanize(long(self.aboutdrive['quotaBytesUsed'])),
            self.FileSizeHumanize(long(self.aboutdrive['quotaBytesTotal']))
        )
        self.SetTitle(title_string)
        appIcon = wx.Icon(APP_ICON, wx.BITMAP_TYPE_PNG)
        self.SetIcon(appIcon)
        menuBar = wx.MenuBar()
        menu = wx.Menu()

        menu_txt = 'Pause/Resume Sync'

        self.CreateMenuItem(menu,
                            menu_txt,
                            self.OnToggleSync,
                            icon=os.path.join(HERE, 'resources/sync-menu.png'),
                            id=ID_SYNC_TOGGLE)

        menu.AppendSeparator()
        self.CreateMenuItem(menu, 'A&bout', self.OnAbout,
                            os.path.join(HERE, 'resources/info.png'))
        self.CreateMenuItem(menu, 'E&xit', self.OnExit,
                            os.path.join(HERE, 'resources/exit.png'))

        menuBar.Append(menu, '&File')

        self.SetMenuBar(menuBar)

        # Here we create a panel and a notebook on the panel
        p = wx.Panel(self, size=self.GetSize())
        nb = wx.Notebook(p)

        # create the page windows as children of the notebook
        accountPage = PageAccount(nb, self.sync_model)
        settingsPage = SettingsPage(nb, self.sync_model)

        # add the pages to the notebook with the label to show on the tab
        nb.AddPage(accountPage, "Account")
        nb.AddPage(settingsPage, "Settings")

        # finally, put the notebook in a sizer for the panel to manage
        # the layout
        sizer = wx.BoxSizer()
        sizer.Add(nb, 1, wx.EXPAND)
        p.SetSizer(sizer)

        self.sb = self.CreateStatusBar(2)
        self.sb.SetStatusWidths([-6, -1])

        if self.sync_model.is_sync_enabled():
            self.sb.SetStatusText("Running", 1)
        else:
            self.sb.SetStatusText("Paused", 1)

        GoSyncEventController().BindEvent(self, GOSYNC_EVENT_SYNC_STARTED,
                                          self.OnSyncStarted)
        GoSyncEventController().BindEvent(self, GOSYNC_EVENT_SYNC_UPDATE,
                                          self.OnSyncUpdate)
        GoSyncEventController().BindEvent(self, GOSYNC_EVENT_SYNC_DONE,
                                          self.OnSyncDone)
        GoSyncEventController().BindEvent(self, GOSYNC_EVENT_SYNC_TIMER,
                                          self.OnSyncTimer)
        GoSyncEventController().BindEvent(self, GOSYNC_EVENT_SYNC_INV_FOLDER,
                                          self.OnSyncInvalidFolder)

        self.sync_model.start()

    def OnSyncInvalidFolder(self, event):
        dial = wx.MessageDialog(
            None,
            'Some of the folders to be sync\'ed were not found on '
            'remote server.\nPlease check.\n',
            'Error', wx.OK | wx.ICON_EXCLAMATION
        )
        dial.ShowModal()

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

    def CreateMenuItem(self, menu, label, func, icon=None, id=None):
        if id:
            item = wx.MenuItem(menu, id, label)
        else:
            item = wx.MenuItem(menu, -1, label)

        if icon:
            item.SetBitmap(wx.Bitmap(icon))

        if id:
            self.Bind(wx.EVT_MENU, func, id=id)
        else:
            self.Bind(wx.EVT_MENU, func, id=item.GetId())

        menu.AppendItem(item)
        return item

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

    def OnAbout(self, evt):
        """About GoSync"""
        about = wx.AboutDialogInfo()
        about.SetIcon(wx.Icon(ABOUT_ICON, wx.BITMAP_TYPE_PNG))
        about.SetName(APP_NAME)
        about.SetVersion(APP_VERSION)
        about.SetDescription(APP_DESCRIPTION)
        about.SetCopyright(APP_COPYRIGHT)
        about.SetWebSite(APP_WEBSITE)
        about.SetLicense(APP_LICENSE)
        about.AddDeveloper(APP_DEVELOPER)
        about.AddArtist(APP_DEVELOPER)
        wx.AboutBox(about)
