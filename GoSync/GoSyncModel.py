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

import copy
import hashlib
import json
import logging
import ntpath
import os
import pickle
import random
import threading
import time

from GoSyncDriveTree import GoogleDriveTree
from GoSyncEvents import *
from apiclient import errors
from apiclient.errors import HttpError
from defines import *
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer


class ClientSecretsNotFound(RuntimeError):
    """Client secrets file was not found"""


class FileNotFound(RuntimeError):
    """File was not found on google drive"""


class FolderNotFound(RuntimeError):
    """Folder on Google Drive was not found"""


class UnknownError(RuntimeError):
    """Unknown/Unexpected error happened"""


class MD5ChecksumCalculationFailed(RuntimeError):
    """Calculation of MD5 checksum on a given file failed"""


class RegularFileUploadFailed(RuntimeError):
    """Upload of a regular file failed"""


class RegularFileTrashFailed(RuntimeError):
    """Could not move file to trash"""


class FileListQueryFailed(RuntimeError):
    """The query of file list failed"""


class ConfigLoadFailed(RuntimeError):
    """Failed to load the GoSync configuration file"""


class FileMoveFailed(RuntimeError):
    """Failed to move a file"""


audio_file_mimelist = ['audio/mpeg', 'audio/x-mpeg-3', 'audio/mpeg3',
                       'audio/aiff', 'audio/x-aiff']
movie_file_mimelist = ['video/mp4', 'video/x-msvideo', 'video/mpeg',
                       'video/flv', 'video/quicktime']
image_file_mimelist = ['image/png', 'image/jpeg', 'image/jpg', 'image/tiff']
document_file_mimelist = ['application/powerpoint', 'applciation/mspowerpoint',
                          'application/x-mspowerpoint', 'application/pdf',
                          'application/x-dvi']
google_folder_mime = 'application/vnd.google-apps.folder'
google_docs_mimelist = ['application/vnd.google-apps.spreadsheet',
                        'application/vnd.google-apps.sites',
                        'application/vnd.google-apps.script',
                        'application/vnd.google-apps.presentation',
                        'application/vnd.google-apps.fusiontable',
                        'application/vnd.google-apps.form',
                        'application/vnd.google-apps.drawing',
                        'application/vnd.google-apps.document',
                        'application/vnd.google-apps.map']


class GoSyncModel(object):
    def __init__(self):
        # Initialize attributes
        self.calculatingDriveUsage = False
        self.fcount = 0
        self.updates_done = 0
        self.authToken = None
        self.drive = None
        self.config = None

        # Paths
        self.config_path = None
        self.credential_file = None
        self.settings_file = None
        self.client_secret_file = None

        self.sync_selection = []
        self.user_config = {}
        self.account_dict = {}
        self.drive_usage_dict = {}

        # Setup logger
        self.logger = logging.getLogger(APP_NAME)
        self.logger.setLevel(logging.DEBUG)
        fh = logging.FileHandler(os.path.join(os.environ['HOME'], 'GoSync.log'))
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        # Set paths and create directories
        self.configure_authentication_files()

        # Authenticate
        self.observer = Observer()
        self.do_authenticate(self.settings_file)

        self.about_drive = self.drive.GetAbout()
        self.user_email = self.about_drive['user']['emailAddress']

        # Load GoSync configuration
        self.config_file = os.path.join(self.config_path, 'config.json')
        if not os.path.exists(self.config_file):
            self.create_default_config()

        # Load the configuration settings
        self.load_config(self.config_file)

        self.base_mirror_directory = self.config['base_mirror_directory']
        if not os.path.exists(self.base_mirror_directory):
            os.mkdir(self.base_mirror_directory, 0755)

        self.mirror_directory = self.user_config['mirror_directory']
        if not os.path.exists(self.mirror_directory):
            os.mkdir(self.mirror_directory, 0755)

        self.iobserv_handle = self.observer.schedule(
            FileModificationNotifyHandler(self),
            self.mirror_directory,
            recursive=True
        )

        # Setup thread objects
        self.sync_lock = threading.Lock()
        self.syncRunning = threading.Event()
        self.syncRunning.set()
        self.usageCalculateEvent = threading.Event()
        self.usageCalculateEvent.set()

        self.sync_thread = threading.Thread(target=self.run)
        self.sync_thread.daemon = True
        self.usage_calc_thread = threading.Thread(target=self.calculate_usage)
        self.usage_calc_thread.daemon = True

        # Load the drive tree
        self.tree_pickle_file = os.path.join(self.config_path, 'gtree-%s.pkl' % self.user_email)
        if not os.path.exists(self.tree_pickle_file):
            self.driveTree = GoogleDriveTree()
        else:
            with open(self.tree_pickle_file, "rb") as tree_file:
                self.driveTree = pickle.load(tree_file)

    def start(self):
        self.sync_thread.start()
        self.usage_calc_thread.start()
        self.observer.start()

    @property
    def number_of_files(self):
        return self.drive_usage_dict['total_files']

    @number_of_files.setter
    def number_of_files(self, value):
        self.drive_usage_dict['total_files'] = value

    @property
    def total_file_size(self):
        return self.drive_usage_dict['total_size']

    @total_file_size.setter
    def total_file_size(self, value):
        self.drive_usage_dict['total_size'] = value

    @property
    def audio_usage(self):
        return self.drive_usage_dict['audio_size']

    @audio_usage.setter
    def audio_usage(self, value):
        self.drive_usage_dict['audio_size'] = value

    @property
    def movies_usage(self):
        return self.drive_usage_dict['movies_size']

    @movies_usage.setter
    def movies_usage(self, value):
        self.drive_usage_dict['movies_size'] = value

    @property
    def document_usage(self):
        return self.drive_usage_dict['document_size']

    @document_usage.setter
    def document_usage(self, value):
        self.drive_usage_dict['document_size'] = value

    @property
    def photo_usage(self):
        return self.drive_usage_dict['photo_size']

    @photo_usage.setter
    def photo_usage(self, value):
        self.drive_usage_dict['photo_size'] = value

    @property
    def others_usage(self):
        return self.drive_usage_dict['others_size']

    @others_usage.setter
    def others_usage(self, value):
        self.drive_usage_dict['others_size'] = value

    def configure_authentication_files(self):
        self.logger.debug('configure_authentication_files()')
        self.config_path = os.path.join(os.environ['HOME'], ".gosync")
        if not os.path.exists(self.config_path):
            self.logger.info('Creating configuration path at "%s"', self.config_path)
            os.mkdir(self.config_path, 0755)
            self.logger.error('no client secrets')
            raise ClientSecretsNotFound()

        self.client_secret_file = os.path.join(self.config_path,
                                               'client_secrets.json')
        if not os.path.exists(self.client_secret_file):
            self.logger.error('no client secrets')
            raise ClientSecretsNotFound()

        self.credential_file = os.path.join(self.config_path, "credentials.json")
        self.settings_file = os.path.join(self.config_path, "settings.yaml")
        if not os.path.isfile(self.settings_file):
            self.logger.info('Creating default settings file')
            self.create_default_settings_file()

    def is_user_logged_in(self):
        return self.is_logged_in

    def hash_of_file(self, abs_filepath):
        with open(abs_filepath, "r") as f:
            data = f.read()
        return hashlib.md5(data).hexdigest()

    def create_default_settings_file(self):
        with open(self.settings_file, 'w') as sfile:
            sfile.write("save_credentials: False\n")
            sfile.write(
                "save_credentials_file: %s\n" % self.credential_file
            )
            sfile.write(
                'client_config_file: %s\n' % self.client_secret_file
            )
            sfile.write("save_credentials_backend: file\n")

    def create_default_config(self):
        base_mirror_directory = os.path.join(os.environ['HOME'],
                                             'Google Drive')
        DEFAULT_CONFIG = {
            'sync_selection': [['root', '']],
            'mirror_directory': os.path.join(base_mirror_directory,
                                             self.user_email)
        }

        with open(self.config_file, 'w') as f:
            account_dict = {
                'base_mirror_directory': base_mirror_directory,
                self.user_email: DEFAULT_CONFIG
            }
            json.dump(account_dict, f)

    def load_config(self, config_file):
        with open(config_file, 'r') as f:
            try:
                self.config = json.load(f)
                try:
                    self.user_config = self.config[self.user_email]
                    self.sync_selection = self.user_config['sync_selection']
                    print self.user_config['drive_usage']
                    try:
                        self.drive_usage_dict = self.user_config['drive_usage']
                    except KeyError:
                        pass
                except KeyError:
                    pass
            except:
                raise ConfigLoadFailed()

    def save_config(self, config_file):
        with open(config_file, 'w') as f:
            f.truncate()
            if not self.sync_selection:
                self.user_config['sync_selection'] = [['root', '']]

            json.dump(self.config, f)

    def do_authenticate(self, settings_file):
        self.logger.info('Authenticating...')
        try:
            self.authToken = GoogleAuth(settings_file)
            self.authToken.LocalWebserverAuth()
            self.drive = GoogleDrive(self.authToken)
            self.is_logged_in = True
            self.logger.info('Authentication successful.')
        except:
            self.logger.error('Authentication rejected!')
            self.is_logged_in = False
            raise

    def do_unauthenticate(self):
            self.do_sync = False
            self.observer.unschedule(self.iobserv_handle)
            self.iobserv_handle = None
            os.remove(self.credential_file)
            self.is_logged_in = False

    def get_drive_info(self):
        return self.about_drive

    def path_leaf(self, path):
        head, tail = ntpath.split(path)
        return tail or ntpath.basename(head)

    def get_folder_on_drive(self, folder_name, parent='root'):
        """
        Return the folder with name in "folder_name" in the parent folder
        mentioned in parent.
        """
        self.logger.debug("get_folder_on_drive: searching %s on %s... " % (folder_name, parent))
        file_list = self.drive.ListFile(
            {'q': "'%s' in parents and trashed=false" % parent}
        ).GetList()
        for f in file_list:
            if f['title'] == folder_name and f['mimeType'] == google_folder_mime:
                self.logger.debug("Found!\n")
                return f

        return None

    def locate_folder_on_drive(self, folder_path):
        """
        Locate and return the directory in the path. The complete path
        is walked and the last directory is returned. An exception is raised
        if the path walking fails at any stage.
        """
        dir_list = folder_path.split(os.sep)
        croot = 'root'
        for dir1 in dir_list:
            folder = self.get_folder_on_drive(dir1, croot)
            if not folder:
                raise FolderNotFound()

            croot = folder['id']

        return folder

    def locate_file_in_folder(self, filename, parent='root'):
        try:
            file_list = self.make_file_list_query(
                {'q': "'%s' in parents and trashed=false" % parent}
            )
            for f in file_list:
                if f['title'] == filename:
                    return f

            raise FileNotFound()
        except:
            raise FileNotFound()

    def locate_file_on_drive(self, abs_filepath):
        dirpath = os.path.dirname(abs_filepath)
        filename = self.path_leaf(abs_filepath)

        if dirpath != '':
            try:
                f = self.locate_folder_on_drive(dirpath)
                try:
                    fil = self.locate_file_in_folder(filename, f['id'])
                    return fil
                except FileNotFound:
                    self.logger.debug("locate_file_on_drive: File not found.\n")
                    raise
                except FileListQueryFailed:
                    self.logger.debug("locate_file_on_drive: File list query "
                                      "failed\n")
                    raise
            except FolderNotFound:
                self.logger.debug("locate_file_on_drive: Folder not found\n")
                raise
            except FileListQueryFailed:
                self.logger.debug("locate_file_on_drive:  %s folder not found\n"
                                  "" % dirpath)
                raise
        else:
            try:
                fil = self.locate_file_in_folder(filename)
                return fil
            except FileNotFound:
                self.logger.debug("locate_file_on_drive: File not found.\n")
                raise
            except FileListQueryFailed:
                self.logger.debug("locate_file_on_drive: File list query failed.\n")
                raise
            except:
                self.logger.error("locate_file_on_drive: Unknown error in "
                                  "locating file in drive\n")
                raise

    def create_dir_in_parent(self, dirname, parent_id='root'):
        upfile = self.drive.CreateFile({
            'title': dirname,
            'mimeType': google_folder_mime,
            'parents': [{'kind': 'drive#fileLink', 'id': parent_id}]
        })
        upfile.Upload()

    def create_dir_by_path(self, dirpath):
        self.logger.debug('create directory: %s\n' % dirpath)
        drivepath = dirpath.split(self.mirror_directory + '/')[1]
        basepath = os.path.dirname(drivepath)
        dirname = self.path_leaf(dirpath)

        try:
            self.locate_folder_on_drive(drivepath)
            return
        except FolderNotFound:
            if basepath == '':
                self.create_dir_in_parent(dirname)
            else:
                try:
                    parent_folder = self.locate_folder_on_drive(basepath)
                    self.create_dir_in_parent(dirname, parent_folder['id'])
                except:
                    errorMsg = ("Failed to locate directory path %s on drive."
                                "\n" % basepath)
                    self.logger.error(errorMsg)
                    return
        except FileListQueryFailed:
            errorMsg = "Server Query Failed!\n"
            self.logger.error(errorMsg)
            return

    def create_regular_file(self, file_path, parent='root', uploaded=False):
        self.logger.debug("Create file %s\n" % file_path)
        filename = self.path_leaf(file_path)
        upfile = self.drive.CreateFile({
            'title': filename,
            "parents": [{"kind": "drive#fileLink", "id": parent}]
        })
        upfile.SetContentFile(file_path)
        upfile.Upload()

    def upload_file(self, file_path):
        if os.path.isfile(file_path):
            drivepath = file_path.split(self.mirror_directory + '/')[1]
            self.logger.debug("file: %s drivepath is %s\n" % (file_path,
                                                              drivepath))
            try:
                f = self.locate_file_on_drive(drivepath)
                self.logger.debug('Found file %s on remote (dpath: %s)'
                                  '\n' % (f['title'], drivepath))
                newfile = False
                self.logger.debug('Checking if they are same... ')
                if f['md5Checksum'] == self.hash_of_file(file_path):
                    self.logger.debug('yes\n')
                    return
                else:
                    self.logger.debug('no\n')
            except (FileNotFound, FolderNotFound):
                self.logger.debug("A new file!\n")
                newfile = True

            dirpath = os.path.dirname(drivepath)
            if dirpath == '':
                self.logger.debug('Creating %s file in root\n' % file_path)
                self.create_regular_file(file_path, 'root', newfile)
            else:
                try:
                    f = self.locate_folder_on_drive(dirpath)
                    self.create_regular_file(file_path, f['id'], newfile)
                except FolderNotFound:
                    # We are coming from premise that upload comes as part
                    # of observer. So before notification of this file's
                    # creation happens, a notification of its parent directory
                    # must have come first.
                    # So,
                    # Folder not found? That cannot happen. Can it?
                    raise RegularFileUploadFailed()
        else:
            self.create_dir_by_path(file_path)

    def upload_observed_file(self, file_path):
        self.sync_lock.acquire()
        self.upload_file(file_path)
        self.sync_lock.release()

    def rename_file(self, file_object, new_title):
        try:
            file = {'title': new_title}

            updated_file = self.authToken.service.files().patch(
                fileId=file_object['id'],
                body=file,
                fields='title'
            ).execute()
            return updated_file
        except errors.HttpError, error:
            self.logger.error('An error occurred while renaming file: %s' % error)
            return None
        except:
            self.logger.exception('An unknown error occurred file renaming file\n')
            return None

    def rename_observed_file(self, file_path, new_name):
        self.sync_lock.acquire()
        drive_path = file_path.split(self.mirror_directory + '/')[1]
        self.logger.debug("rename_observed_file: Rename %s to new name %s\n"
                          % (file_path, new_name))
        try:
            ftd = self.locate_file_on_drive(drive_path)
            nftd = self.rename_file(ftd, new_name)
            if not nftd:
                self.logger.error("File rename failed\n")
        except:
            self.logger.exception("Could not locate file on drive.\n")

        self.sync_lock.release()

    def trash_file(self, file_object):
        try:
            self.authToken.service.files().trash(fileId=file_object['id']).execute()
            self.logger.info({"TRASH_FILE: File %s deleted successfully.\n" % file_object['title']})
        except errors.HttpError:
            self.logger.error("TRASH_FILE: HTTP Error\n")
            raise RegularFileTrashFailed()

    def trash_observed_file(self, file_path):
        self.sync_lock.acquire()
        drive_path = file_path.split(self.mirror_directory + '/')[1]
        self.logger.debug({"TRASH_FILE: dirpath to delete: %s\n" % drive_path})
        try:
            ftd = self.locate_file_on_drive(drive_path)
            try:
                self.trash_file(ftd)
            except RegularFileTrashFailed:
                self.logger.error({"TRASH_FILE: Failed to move file %s to "
                                   "trash\n" % drive_path})
                raise
            except:
                raise
        except (FileNotFound, FileListQueryFailed, FolderNotFound):
            self.logger.error({"TRASH_FILE: Failed to locate %s file on drive"
                               "\n" % drive_path})
            pass

        self.sync_lock.release()

    def move_file(self, src_file, dst_folder='root', src_folder='root'):
        try:
            if dst_folder != 'root':
                did = dst_folder['id']
            else:
                did = 'root'

            if src_folder != 'root':
                sid = src_folder['id']
            else:
                sid = 'root'

            self.authToken.service.files().patch(
                fileId=src_file['id'],
                body=src_file,
                addParents=did,
                removeParents=sid
            ).execute()
        except:
            self.logger.exception("move failed\n")

    def move_observed_file(self, src_path, dest_path):
        from_drive_path = src_path.split(self.mirror_directory + '/')[1]
        to_drive_path = os.path.dirname(
            dest_path.split(self.mirror_directory + '/')[1]
        )

        self.logger.debug("Moving file %s to %s\n" % (from_drive_path,
                                                      to_drive_path))

        try:
            ftm = self.locate_file_on_drive(from_drive_path)
            self.logger.debug("move_observed_file: Found source file on drive\n")
            if os.path.dirname(from_drive_path) == '':
                sf = 'root'
            else:
                sf = self.locate_folder_on_drive(os.path.dirname(from_drive_path))
            self.logger.debug("move_observed_file: Found source folder on drive\n")
            try:
                if to_drive_path == '':
                    df = 'root'
                else:
                    df = self.locate_folder_on_drive(to_drive_path)
                self.logger.debug("move_observed_file: Found destination folder on drive\n")
                try:
                    self.logger.debug("MovingFile() ")
                    self.move_file(ftm, df, sf)
                    self.logger.debug("done\n")
                except (UnknownError, FileMoveFailed):
                    self.logger.error("MovedObservedFile: Failed\n")
                    return
                except:
                    self.logger.error("?????\n")
                    return
            except FolderNotFound:
                self.logger.error("move_observed_file: Couldn't locate destination folder on drive.\n")
                return
            except:
                self.logger.error("move_observed_file: Unknown error while locating destination folder on drive.\n")
                return
        except FileNotFound:
                self.logger.error("move_observed_file: Couldn't locate file on drive.\n")
                return
        except FileListQueryFailed:
            self.logger.error("move_observed_file: File Query failed. aborting.\n")
            return
        except FolderNotFound:
            self.logger.error("move_observed_file: Folder not found\n")
            return
        except:
            self.logger.error("move_observed_file: Unknown error while moving file.\n")
            return

    def handle_moved_file(self, src_path, dest_path):
        drive_path1 = os.path.dirname(src_path.split(self.mirror_directory + '/')[1])
        drive_path2 = os.path.dirname(dest_path.split(self.mirror_directory + '/')[1])

        if drive_path1 == drive_path2:
            self.logger.debug("Rename file\n")
            self.rename_observed_file(src_path, self.path_leaf(dest_path))
        else:
            self.logger.debug("Move file\n")
            self.move_observed_file(src_path, dest_path)

    # DOWNLOAD SECTION
    def make_file_list_query(self, query):
        # Retry 5 times to get the query
        for n in range(0, 5):
            try:
                return self.drive.ListFile(query).GetList()
            except HttpError as error:
                if error.resp.reason in ['userRateLimitExceeded', 'quotaExceeded']:
                    self.logger.error("user rate limit/quota exceeded. Will try later\n")
                    time.sleep((2**n) + random.random())
            except:
                self.logger.error("make_file_list_query: failed with reason %s\n" % error.resp.reason)
                time.sleep((2**n) + random.random())

        self.logger.error("Can't get the connection back after many retries. Bailing out\n")
        raise FileListQueryFailed

    def get_total_files_in_folder(self, parent='root'):
        file_count = 0
        try:
            file_list = self.make_file_list_query({'q': "'%s' in parents and trashed=false" % parent})
            for f in file_list:
                if f['mimeType'] == google_folder_mime:
                    file_count += self.get_total_files_in_folder(f['id'])
                    file_count += 1
                else:
                    file_count += 1

            return file_count
        except:
            raise

    def is_google_document(self, f):
        if any(f['mimeType'] in s for s in google_docs_mimelist):
            return True
        else:
            return False

    def get_total_files_in_drive(self):
        return self.get_total_files_in_folder()

    def download_file_by_object(self, file_obj, download_path):
        dfile = self.drive.CreateFile({'id': file_obj['id']})
        abs_filepath = os.path.join(download_path, file_obj['title'])
        if os.path.exists(abs_filepath):
            if self.hash_of_file(abs_filepath) == file_obj['md5Checksum']:
                self.logger.debug('%s file is same as local. not downloading\n' % abs_filepath)
                return
            else:
                self.logger.debug("download_file_by_object: Local and remote "
                                  "file with same name but different content. "
                                  "Skipping. (local file: %s)\n" % abs_filepath)
        else:
            self.logger.info('Downloading %s ' % abs_filepath)
            fd = abs_filepath.split(self.mirror_directory + '/')[1]
            GoSyncEventController().notify_listeners(GOSYNC_EVENT_SYNC_UPDATE,
                                              {'Downloading %s' % fd})
            dfile.GetContentFile(abs_filepath)
            self.updates_done = 1
            self.logger.info('Done\n')

    def sync_remote_directory(self, parent, pwd, recursive=True):
        if not self.syncRunning.is_set():
            self.logger.debug("sync_remote_directory: Sync has been paused. "
                              "Aborting.\n")
            return

        if not os.path.exists(os.path.join(self.mirror_directory, pwd)):
            os.makedirs(os.path.join(self.mirror_directory, pwd))

        try:
            file_list = self.make_file_list_query(
                {'q': "'%s' in parents and trashed=false" % parent}
            )
            for f in file_list:
                if not self.syncRunning.is_set():
                    self.logger.debug("sync_remote_directory: Sync has been "
                                      "paused. Aborting.\n")
                    return

                if f['mimeType'] == google_folder_mime:
                    if not recursive:
                        continue

                    abs_dirpath = os.path.join(self.mirror_directory,
                                               pwd,
                                               f['title'])
                    self.logger.debug("Checking directory %s\n" % f['title'])
                    if not os.path.exists(abs_dirpath):
                        self.logger.debug("creating directory %s " % abs_dirpath)
                        os.makedirs(abs_dirpath)
                        self.logger.debug("done\n")
                    self.logger.debug('syncing directory %s\n' % f['title'])
                    self.sync_remote_directory(f['id'], os.path.join(pwd, f['title']))
                    if not self.syncRunning.is_set():
                        self.logger.debug("sync_remote_directory: Sync has been "
                                          "paused. Aborting.\n")
                        return
                else:
                    self.logger.debug("Checking file %s\n" % f['title'])
                    if not self.is_google_document(f):
                        self.download_file_by_object(
                            f,
                            os.path.join(self.mirror_directory, pwd)
                        )
                    else:
                        self.logger.info("%s is a google document\n" % f['title'])
        except:
            self.logger.error("Failed to sync directory\n")
            raise

    def sync_local_directory(self):
        for root, dirs, files in os.walk(self.mirror_directory):
            for names in files:
                try:
                    dirpath = os.path.join(root, names)
                    drivepath = dirpath.split(self.mirror_directory + '/')[1]
                    self.locate_file_on_drive(drivepath)
                except FileListQueryFailed:
                    # if the file list query failed, we can't delete the local file even if
                    # its gone in remote drive. Let the next sync come and take care of this
                    # Log the event though
                    self.logger.info("File check on remote directory has "
                                     "failed. Aborting local sync.\n")
                    return
                except:
                    if os.path.exists(dirpath) and os.path.isfile(dirpath):
                        self.logger.info("%s has been removed from drive. "
                                         "Deleting local copy\n" % dirpath)
                        os.remove(dirpath)

            for names in dirs:
                try:
                    dirpath = os.path.join(root, names)
                    drivepath = dirpath.split(self.mirror_directory + '/')[1]
                    self.locate_file_on_drive(drivepath)
                except FileListQueryFailed:
                    # if the file list query failed, we can't delete the local file even if
                    # its gone in remote drive. Let the next sync come and take care of this
                    # Log the event though
                    self.logger.info("Folder check on remote directory has "
                                     "failed. Aborting local sync.\n")
                    return
                except:
                    if os.path.exists(dirpath) and os.path.isdir(dirpath):
                        self.logger.info("%s folder has been removed from "
                                         "drive. Deleting local copy\n" % dirpath)
                        os.remove(dirpath)

    def validate_sync_settings(self):
        for path, id_ in self.sync_selection:
            if path != 'root':
                try:
                    f = self.locate_folder_on_drive(path)
                    if f['id'] != id_:
                        raise FolderNotFound()
                    break
                except FolderNotFound:
                    raise
                except:
                    raise FolderNotFound()
            else:
                if id_ != '':
                    raise FolderNotFound()

    def run(self):
        import pudb; pudb.set_trace()
        while True:
            self.syncRunning.wait()

            self.sync_lock.acquire()

            try:
                self.validate_sync_settings()
            except:
                GoSyncEventController().notify_listeners(GOSYNC_EVENT_SYNC_INV_FOLDER, 0)
                self.syncRunning.clear()
                self.sync_lock.release()
                continue

            try:
                GoSyncEventController().notify_listeners(GOSYNC_EVENT_SYNC_STARTED, None)
                for path, id_ in self.sync_selection:
                    self.logger.info("Syncing remote (%s)... " % path)
                    if path != 'root':
                        # Root folder files are always synced
                        self.sync_remote_directory('root', '', False)
                        self.sync_remote_directory(id_, path)
                    else:
                        self.sync_remote_directory('root', '')
                    self.logger.info("done\n")
                self.logger.info("Syncing local...")
                self.sync_local_directory()
                self.logger.info("done\n")
                if self.updates_done:
                    self.usageCalculateEvent.set()
                GoSyncEventController().notify_listeners(GOSYNC_EVENT_SYNC_DONE, 0)
            except:
                GoSyncEventController().notify_listeners(GOSYNC_EVENT_SYNC_DONE, -1)

            self.sync_lock.release()

            time_left = 600

            while (time_left):
                GoSyncEventController().notify_listeners(
                    GOSYNC_EVENT_SYNC_TIMER,
                    {'Sync starts in %02dm:%02ds' % ((time_left / 60),
                                                     (time_left % 60))}
                )
                time_left -= 1
                self.syncRunning.wait()
                time.sleep(1)

    def get_file_size(self, f):
        try:
            size = f['fileSize']
            return long(size)
        except:
            self.logger.error("Failed to get size of file %s (mime: %s)"
                              "\n" % (f['title'], f['mimeType']))
            return 0

    def calculate_usage_of_folder(self, folder_id):
        try:
            file_list = self.make_file_list_query(
                {'q': "'%s' in parents and trashed=false" % folder_id}
            )
            for f in file_list:
                self.fcount += 1
                GoSyncEventController().notify_listeners(GOSYNC_EVENT_CALCULATE_USAGE_UPDATE, self.fcount)
                if f['mimeType'] == google_folder_mime:
                    self.driveTree.add_folder(folder_id, f['id'], f['title'], f)
                    self.calculate_usage_of_folder(f['id'])
                else:
                    if not self.is_google_document(f):
                        if any(f['mimeType'] in s for s in audio_file_mimelist):
                            self.audio_usage += self.get_file_size(f)
                        elif any(f['mimeType'] in s for s in image_file_mimelist):
                            self.photo_usage += self.get_file_size(f)
                        elif any(f['mimeType'] in s for s in movie_file_mimelist):
                            self.movies_usage += self.get_file_size(f)
                        elif any(f['mimeType'] in s for s in document_file_mimelist):
                            self.document_usage += self.get_file_size(f)
                        else:
                            self.others_usage += self.get_file_size(f)

        except:
            raise

    def calculate_usage(self):
        while True:
            self.usageCalculateEvent.wait()
            self.usageCalculateEvent.clear()

            self.sync_lock.acquire()
            if self.drive_usage_dict and not self.updates_done:
                GoSyncEventController().notify_listeners(GOSYNC_EVENT_CALCULATE_USAGE_DONE, 0)
                self.sync_lock.release()
                continue

            self.updates_done = 0
            self.calculatingDriveUsage = True
            self.audio_usage = 0
            self.movies_usage = 0
            self.document_usage = 0
            self.photo_usage = 0
            self.others_usage = 0
            self.fcount = 0
            try:
                self.number_of_files = self.get_total_files_in_drive()
                self.logger.info("Total files to check %d\n" % self.number_of_files)
                GoSyncEventController().notify_listeners(GOSYNC_EVENT_CALCULATE_USAGE_STARTED,
                                                  self.number_of_files)
                try:
                    self.calculate_usage_of_folder('root')
                    GoSyncEventController().notify_listeners(GOSYNC_EVENT_CALCULATE_USAGE_DONE, 0)
                    self.total_size = long(self.about_drive['quotaBytesTotal'])
                    with open(self.tree_pickle_file, "wb") as tree_file:
                        pickle.dump(self.driveTree, tree_file)
                    self.user_config['drive_usage'] = self.drive_usage_dict
                    self.save_config(self.config_file)
                except:
                    self.audio_usage = 0
                    self.movies_usage = 0
                    self.document_usage = 0
                    self.photo_usage = 0
                    self.others_usage = 0
                    GoSyncEventController().notify_listeners(GOSYNC_EVENT_CALCULATE_USAGE_DONE, -1)
            except:
                GoSyncEventController().notify_listeners(GOSYNC_EVENT_CALCULATE_USAGE_DONE, -1)
                self.logger.error("Failed to get the total number of files in drive\n")

            self.calculatingDriveUsage = False
            self.sync_lock.release()

    def get_drive_directory_tree(self):
        self.sync_lock.acquire()
        ref_tree = copy.deepcopy(self.driveTree)
        self.sync_lock.release()
        return ref_tree

    def is_calculating_drive_usage(self):
        return self.calculatingDriveUsage

    def start_sync(self):
        self.syncRunning.set()

    def stop_sync(self):
        self.syncRunning.clear()

    def is_sync_enabled(self):
        return self.syncRunning.is_set()

    def set_sync_selection(self, folder):
        if folder == 'root':
            self.sync_selection = [['root', '']]
        else:
            for path, id_ in self.sync_selection:
                if path == 'root':
                    self.sync_selection = []
            for path, id_ in self.sync_selection:
                if path == folder.get_path() and id_ == folder.id:
                    return
            self.sync_selection.append([folder.get_path(), folder.id])
        self.user_config['sync_selection'] = self.sync_selection
        self.save_config(self.config_file)

    def get_sync_list(self):
        return copy.deepcopy(self.sync_selection)


class FileModificationNotifyHandler(PatternMatchingEventHandler):
    patterns = ["*"]

    def __init__(self, sync_handler):
        super(FileModificationNotifyHandler, self).__init__()
        self.sync_handler = sync_handler

    def on_created(self, evt):
        self.sync_handler.logger.debug("Observer: %s created\n" % evt.src_path)
        self.sync_handler.upload_observed_file(evt.src_path)

    def on_moved(self, evt):
        self.sync_handler.logger.info("Observer: file %s moved to %s: Not supported yet!\n" % (evt.src_path, evt.dest_path))
        self.sync_handler.handle_moved_file(evt.src_path, evt.dest_path)

    def on_deleted(self, evt):
        self.sync_handler.logger.info("Observer: file %s deleted on drive.\n" % evt.src_path)
        self.sync_handler.trash_observed_file(evt.src_path)
