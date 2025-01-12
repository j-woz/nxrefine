# -----------------------------------------------------------------------------
# Copyright (c) 2015-2021, NeXpy Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING, distributed with this software.
# -----------------------------------------------------------------------------

import os

from nexpy.gui.datadialogs import GridParameters, NXDialog
from nexpy.gui.utils import report_error
from nexusformat.nexus import NeXusError
from nxrefine.nxsettings import NXSettings


def show_dialog():
    try:
        dialog = SettingsDialog()
        dialog.show()
    except NeXusError as error:
        report_error("Defining New Settings", error)


class SettingsDialog(NXDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            self.settings = NXSettings()
            default_directory = self.settings.directory
        except NeXusError:
            self.settings = None
            default_directory = ""
        self.set_layout(
            self.directorybox('Choose Settings Directory',
                              suggestion=default_directory),
            self.close_layout(save=True))
        if self.settings:
            self.define_parameters()
        self.set_title('New Settings')

    def choose_directory(self):
        super().choose_directory()
        directory = self.get_directory()
        self.settings = NXSettings(directory=directory)
        self.define_parameters()

    def define_parameters(self):
        self.refine_parameters = GridParameters()
        defaults = self.settings.settings['nxrefine']
        for p in defaults:
            self.refine_parameters.add(p, defaults[p], p)
        self.reduce_parameters = GridParameters()
        defaults = self.settings.settings['nxreduce']
        for p in defaults:
            self.reduce_parameters.add(p, defaults[p], p)
        if self.layout.count() == 2:
            self.layout.insertLayout(
                1, self.refine_parameters.grid(header=False, title='NXRefine'))
            self.layout.insertLayout(
                2, self.reduce_parameters.grid(header=False, title='NXReduce'))

    def accept(self):
        try:
            for p in self.refine_parameters:
                self.settings.set('nxrefine', p,
                                  self.refine_parameters[p].value)
            for p in self.reduce_parameters:
                self.settings.set('nxreduce', p,
                                  self.reduce_parameters[p].value)
            self.settings.save()
            super().accept()
        except Exception as error:
            report_error("Defining New Settings", error)
