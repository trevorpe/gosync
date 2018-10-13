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

import os


class DriveFolder(object):
    def __init__(self, parent, id, name, data=None):
        self.children = []
        self.id = id
        self.parent = parent
        self.data = data
        self.name = name

    def GetId(self):
        # TODO: remove
        return self.id

    def add_child(self, child):
        self.children.append(child)

    def delete_child(self, child):
        self.children.remove(child)

    def get_path(self):
        cpath = ''
        if self.parent is not None:
            cpath = self.parent.get_path()

        if self.parent is None:
            path = cpath
        else:
            path = os.path.join(cpath, self.name)

        return path
    
    def __iter__(self):
        return iter(self.children)
    
    def __contains__(self, id):
        try:
            return self[id] is not None
        except KeyError:
            return False
    
    def __getitem__(self, id):
        for f in self.children:
            if f.id == id:
                return f
            try:
                return f[id]
            except KeyError:
                pass
        raise KeyError
    
    def get(self, id, d=None):
        try:
            return self[id]
        except KeyError:
            return d


class GoogleDriveTree(object):
    def __init__(self):
        self.root_node = DriveFolder(None, 'root', 'Google Drive Root', None)

    def add_folder(self, parent_id, folder_id, folder_name, data):
        if not parent_id:
            raise TypeError('must supply the parent_id')

        if self.get(folder_id) is not None:
            return
        
        pnode = self[parent_id]
        cnode = DriveFolder(pnode, folder_id, folder_name, data)
        pnode.add_child(cnode)

    def delete_folder(self, folder_id):
        folder = self.get(folder_id)
        if folder:
            folder.parent.delete_child(folder)
    
    def __iter__(self):
        return iter(self.root_node)
    
    def __contains__(self, id):
        return id in self.root_node
    
    def __getitem__(self, id):
        if id == 'root':
            return self.root_node
        else:
            return self.root_node[id]
        
    def get(self, id, d=None):
        try:
            return self[id]
        except KeyError:
            return d