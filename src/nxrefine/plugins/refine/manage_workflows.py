import os
import subprocess

from nexusformat.nexus import *
from nexpy.gui.pyqt import QtCore, QtWidgets
from nexpy.gui.datadialogs import NXWidget, NXDialog, GridParameters
from nexpy.gui.utils import report_error, natural_sort, format_mtime, human_size
from nexpy.gui.widgets import NXLabel, NXScrollArea, NXPlainTextEdit, NXPushButton

from nxrefine.nxreduce import NXReduce, NXMultiReduce
from nxrefine.nxdatabase import NXDatabase
from nxrefine.nxserver import NXServer

def show_dialog():
    try:
        dialog = WorkflowDialog()
        dialog.show()
    except NeXusError as error:
        report_error("Managing Workflows", error)


class WorkflowDialog(NXDialog):

    def __init__(self, parent=None):
        super(WorkflowDialog, self).__init__(parent)

        self.set_layout(self.directorybox('Choose Sample Directory', default=False),
                        self.filebox('Choose Parent File'),
                        self.action_buttons(('Update Status', self.update),
                                            ('Add to Queue', self.add_tasks),
                                            ('View Logs', self.view_logs),
                                            ('Sync Database', self.sync_db)),
                        self.progress_layout(close=True))
        self.progress_bar.setVisible(False)
        self.set_title('Manage Workflows')
        self.grid = None
        self.scroll_area = None
        self.sample_directory = None
        self.entries = ['f1', 'f2', 'f3']

    def choose_directory(self):
        super(WorkflowDialog, self).choose_directory()
        if self.scroll_area is None:
            self.add_grid_headers()
        self.sample_directory = self.get_directory()
        self.sample = os.path.basename(os.path.dirname(self.sample_directory))
        self.label = os.path.join(os.path.basename(self.sample_directory))
        parent_file = os.path.join(self.sample_directory,
                                   self.sample+'_parent.nxs')
        if os.path.exists(parent_file):
            self.parent_file = os.path.realpath(parent_file)
            self.filename.setText(os.path.basename(self.parent_file))
        else:
            self.parent_file = None
            self.filename.setText('')
        self.root_directory = os.path.dirname(
                                  os.path.dirname(self.sample_directory))
        self.mainwindow.default_directory = self.sample_directory
        self.task_directory = os.path.join(self.root_directory, 'tasks')
        if not os.path.exists(self.task_directory):
            os.mkdir(self.task_directory)
        db_file = os.path.join(self.task_directory, 'nxdatabase.db')
        self.db = NXDatabase(db_file)
        self.server = NXServer()
        self.update()

    def add_grid_headers(self):
        header_grid = QtWidgets.QGridLayout()
        header_widget = NXWidget()
        header_widget.set_layout(header_grid, 'stretch')

        header_grid.setSpacing(1)
        row = 0
        columns = ['Scan', 'data', 'link', 'max', 'find', 'copy', 'refine', 
                   'prepare', 'transform', 'masked_transform', 'combine', 
                   'masked_combine', 'pdf', 'masked_pdf', 'overwrite', 'sync']
        header = {}
        for col, column in enumerate(columns):
            header[column] = NXLabel(column, bold=True, width=75, align='center')
            if column == 'transform' or column == 'combine' or column == 'pdf':
                header_grid.addWidget(header[column], row, col, 1, 2,
                                      QtCore.Qt.AlignHCenter)
            elif 'masked' not in column:
                header_grid.addWidget(header[column], row, col)
                header[column].setAlignment(QtCore.Qt.AlignHCenter)
        row = 1
        columns = 3 * ['regular', 'masked']
        for col, column in enumerate(columns):
            header[column] = NXLabel(column, width=75, align='center')
            header_grid.addWidget(header[column], row, col+8)
        header_widget.setStyleSheet("border:0; margin:0")
        header_widget.setFixedHeight(50)
        self.insert_layout(2, header_widget)

    def choose_file(self):
        super(WorkflowDialog, self).choose_file()
        self.make_parent()

    def get_scan(self, filename):
        _base = os.path.basename(os.path.splitext(filename)[0])
        _scan = _base.replace(self.sample+'_', '')
        return os.path.join(self.sample_directory, _scan)

    def get_scan_file(self, scan):
        return os.path.join(self.sample_directory, 
                            self.sample+'_'+os.path.basename(scan)+'.nxs')

    def make_parent(self):
        reduce = NXMultiReduce(directory=self.get_scan(self.get_filename()),
                               overwrite=True)
        reduce.make_parent()
        self.db.update_file(reduce.wrapper_file)
        if self.parent_file:
            self.db.update_file(self.parent_file)
        self.parent_file = reduce.wrapper_file
        self.filename.setText(os.path.basename(self.parent_file))
        self.update()

    def is_valid(self, wrapper_file):
        if not wrapper_file.endswith('.nxs'):
            return False
        elif not os.path.basename(wrapper_file).startswith(self.sample):
            return False
        elif '_parent' in wrapper_file or '_mask' in wrapper_file:
            return False
        else:
            return True

    def update(self):
        if not self.sample_directory:
            raise NeXusError("No sample directory declared")

        if self.grid:
            self.delete_grid(self.grid)
            del self.grid_widget

        if self.scroll_area:
            self.scroll_area.close()
            self.scroll_area.deleteLater()

        # Map from wrapper files to scan directories
        wrapper_files = { w : self.get_scan(w) for w in sorted( [
                            os.path.join(self.sample_directory, filename)
                            for filename in os.listdir(self.sample_directory)
                            if self.is_valid(filename)] , key=natural_sort) }
        self.grid = QtWidgets.QGridLayout()
        self.grid_widget = NXWidget()
        self.grid_widget.set_layout(self.grid, 'stretch')
        self.scroll_area = NXScrollArea(self.grid_widget)
        self.scroll_area.setMinimumSize(1250, 300)
        self.insert_layout(3, self.scroll_area)
        self.grid.setSpacing(1)

        self.scans = {}
        self.scans_backup = {}

        row = 0
        # Create (unchecked) checkboxes
        for wrapper_file, scan in wrapper_files.items():
            scan_label = os.path.basename(scan)
            status = {}
            status['scan'] = NXLabel(scan_label)
            if self.parent_file == wrapper_file:
                status['scan'].setStyleSheet('font-weight:bold')
            status['entries'] = []
            status['data'] = self.new_checkbox()
            status['link'] = self.new_checkbox()
            status['max'] = self.new_checkbox()
            status['find'] = self.new_checkbox()
            status['copy'] = self.new_checkbox()
            status['refine'] = self.new_checkbox()
            status['prepare'] = self.new_checkbox()
            status['transform'] = self.new_checkbox()
            status['masked_transform'] = self.new_checkbox()
            status['combine'] = self.new_checkbox()
            status['masked_combine'] = self.new_checkbox()
            status['pdf'] = self.new_checkbox()
            status['masked_pdf'] = self.new_checkbox()
            status['overwrite'] = self.new_checkbox(self.select_scans)
            status['sync'] = self.new_checkbox()
            self.grid.addWidget(status['scan'], row, 0, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['data'], row, 1, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['link'], row, 2, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['max'], row, 3, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['find'], row, 4, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['copy'], row, 5, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['refine'], row, 6, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['prepare'], row, 7, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['transform'], row, 8, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['masked_transform'], row, 9, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['combine'], row, 10, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['masked_combine'], row, 11, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['pdf'], row, 12, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['masked_pdf'], row, 13, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['overwrite'], row, 14, QtCore.Qt.AlignCenter)
            self.grid.addWidget(status['sync'], row, 15, QtCore.Qt.AlignCenter)
            self.scans[scan] = status
            row += 1
        self.grid.addWidget(NXLabel('All'), row, 0, QtCore.Qt.AlignCenter)
        all_boxes = {}
        all_boxes['link'] = self.new_checkbox(lambda:self.select_status('link'))
        all_boxes['max'] = self.new_checkbox(lambda:self.select_status('max'))
        all_boxes['find'] = self.new_checkbox(lambda:self.select_status('find'))
        all_boxes['copy'] = self.new_checkbox(lambda:self.select_status('copy'))
        all_boxes['refine'] = self.new_checkbox(lambda:self.select_status('refine'))
        all_boxes['prepare'] = self.new_checkbox(lambda:self.select_status('prepare'))
        all_boxes['transform'] = self.new_checkbox(lambda:self.select_status('transform'))
        all_boxes['masked_transform'] = self.new_checkbox(lambda:self.select_status('masked_transform'))
        all_boxes['combine'] = self.new_checkbox(lambda:self.select_status('combine'))
        all_boxes['masked_combine'] = self.new_checkbox(lambda:self.select_status('masked_combine'))
        all_boxes['pdf'] = self.new_checkbox(lambda:self.select_status('pdf'))
        all_boxes['masked_pdf'] = self.new_checkbox(lambda:self.select_status('masked_pdf'))
        all_boxes['overwrite'] = self.new_checkbox(self.select_all)
        all_boxes['sync'] = self.new_checkbox(self.select_all)
        self.grid.addWidget(all_boxes['link'], row, 2, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['max'], row, 3, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['find'], row, 4, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['copy'], row, 5, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['refine'], row, 6, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['prepare'], row, 7, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['transform'], row, 8, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['masked_transform'], row, 9, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['combine'], row, 10, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['masked_combine'], row, 11, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['pdf'], row, 12, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['masked_pdf'], row, 13, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['overwrite'], row, 14, QtCore.Qt.AlignCenter)
        self.grid.addWidget(all_boxes['sync'], row, 15, QtCore.Qt.AlignCenter)
        self.all_scans = all_boxes
        self.start_progress((0, len(wrapper_files)))

        # Populate the checkboxes based on the entries in self.db.File
        for i, (wrapper, scan) in enumerate(wrapper_files.items()):
            status = self.scans[scan]
            status['data'].setEnabled(False)
            f = self.db.get_file(wrapper)
            status['entries'] = f.get_entries()
            for task_name in self.db.task_names:
                # Database columns use nx* names while columns don't
                if task_name.startswith('nx'):
                    col_name = task_name[2:]
                else:
                    col_name = task_name
                checkbox = status[col_name]
                file_status = getattr(f, task_name)
                if file_status == self.db.DONE:
                    checkbox.setCheckState(QtCore.Qt.Checked)
                    checkbox.setEnabled(False)
                elif file_status == self.db.IN_PROGRESS:
                    checkbox.setCheckState(QtCore.Qt.PartiallyChecked)
                    checkbox.setEnabled(True)
                    checkbox.setStyleSheet("color: green")
                elif file_status == self.db.QUEUED:
                    checkbox.setCheckState(QtCore.Qt.PartiallyChecked)
                    checkbox.setEnabled(True)
                    checkbox.setStyleSheet("color: blue")
                elif file_status == self.db.FAILED:
                    checkbox.setCheckState(QtCore.Qt.PartiallyChecked)
                    checkbox.setEnabled(True)
                    checkbox.setStyleSheet("color: red")
            if status['data'].checkState() == QtCore.Qt.Unchecked:
                for program in ['link', 'max', 'find', 'copy', 'refine', 
                                'prepare', 'transform', 'masked_transform']:
                    status[program].setEnabled(False)
            self.update_progress(i)

        self.stop_progress()
        self.backup_scans()
        return self.grid

    def sync_db(self):
        for scan in self.scans:
            if self.sync_selected(scan):
                self.db.update_file(self.get_scan_file(scan))
        self.update()

    def new_checkbox(self, slot=None):
        checkbox = QtWidgets.QCheckBox()
        checkbox.setCheckState(QtCore.Qt.Unchecked)
        checkbox.setEnabled(True)
        if slot:
            checkbox.stateChanged.connect(slot)
        return checkbox

    def update_checkbox(self, checkbox, idx, status):
        if status and idx == 0:
            checkbox.setCheckState(QtCore.Qt.Checked)
            checkbox.setEnabled(False)
        elif ((status and checkbox.checkState() == QtCore.Qt.Unchecked) or
              (not status and checkbox.checkState() == QtCore.Qt.Checked)):
            checkbox.setCheckState(QtCore.Qt.PartiallyChecked)
            checkbox.setEnabled(True)

    def backup_scans(self):
        for scan in self.scans:
            self.scans_backup[scan] = []
            for status in self.programs:
                self.scans_backup[scan].append(
                    (status,
                     self.scans[scan][status].isEnabled(),
                     self.scans[scan][status].checkState()))

    @property
    def programs(self):
        return ['link', 'max', 'find', 'copy', 'refine', 'prepare', 
                'transform', 'masked_transform', 'combine', 'masked_combine', 
                'pdf', 'masked_pdf']

    @property
    def enabled_scans(self):
        return self.scans

    def overwrite_selected(self, scan):
        return self.scans[scan]['overwrite'].isChecked()

    def sync_selected(self, scan):
        return self.scans[scan]['sync'].isChecked()

    def restore_scan(self, scan):
        for backup in self.scans_backup[scan]:
            status, enabled, checked = backup
            self.scans[scan][status].setEnabled(enabled)
            self.scans[scan][status].setCheckState(checked)

    def select_programs(self, scan):
        if self.overwrite_selected(scan):
            for status in self.programs:
                self.scans[scan][status].setEnabled(True)
        else:
            self.restore_scan(scan)
        if self.overwrite_selected(scan):
            for status in self.programs:
                if self.scans[scan][status].isEnabled():
                    self.scans[scan][status].setChecked(
                        self.all_scans[status].isChecked())
        else:
            self.restore_scan(scan)

    def select_scans(self):
        for scan in self.enabled_scans:
            self.select_programs(scan)

    def select_all(self):
        for scan in self.enabled_scans:
            self.scans[scan]['overwrite'].blockSignals(True)
            self.scans[scan]['overwrite'].setCheckState(
                self.all_scans['overwrite'].checkState())
            self.scans[scan]['overwrite'].blockSignals(False)
        for scan in self.scans:
            self.scans[scan]['sync'].setCheckState(self.all_scans['sync'].checkState())
        for scan in self.enabled_scans:
            self.select_programs(scan)

    def select_status(self, status):
        for scan in self.enabled_scans:
            if self.scans[scan][status].isEnabled():
                self.scans[scan][status].setCheckState(
                    self.all_scans[status].checkState())

    def deselect_all(self):
        for scan in self.enabled_scans:
            self.scans[scan]['overwrite'].blockSignals(True)
            self.scans[scan]['overwrite'].setCheckState(False)
            self.scans[scan]['overwrite'].blockSignals(False)
        for scan in self.scans:
            self.scans[scan]['sync'].setCheckState(False)
        self.all_scans['overwrite'].blockSignals(True)
        self.all_scans['overwrite'].setChecked(False)
        self.all_scans['overwrite'].blockSignals(False)
        self.backup_scans()

    def selected(self, scan, command):
        return (self.scans[scan][command].isEnabled() and
                self.scans[scan][command].checkState()==QtCore.Qt.Checked)

    def queued(self, scan, program):
        self.scans[scan][program].setCheckState(QtCore.Qt.PartiallyChecked)
        self.scans[scan][program].setStyleSheet("")
        self.scans[scan][program].setEnabled(False)

    def add_tasks(self):
        if self.grid is None:
            raise NeXusError('Need to update status')
        for scan in self.enabled_scans:
            for entry in self.enabled_scans[scan]['entries']:
                reduce = NXReduce(entry, scan)
                if self.selected(scan, 'link'):
                    reduce.link = True
                if self.selected(scan, 'max'):
                    reduce.maxcount = True
                if self.selected(scan, 'find'):
                    reduce.find = True
                if self.selected(scan, 'copy'):
                    reduce.copy = True
                if self.selected(scan, 'refine'):
                    reduce.refine = True
                if self.selected(scan, 'prepare'):
                    reduce.prepare = True
                if self.selected(scan, 'transform'):
                    reduce.transform = True
                elif self.selected(scan, 'masked_transform'):
                    reduce.transform = True
                    reduce.mask = True
                if self.selected(scan, 'overwrite'):
                    reduce.overwrite = True
                reduce.queue()
            if self.selected(scan, 'link'):
                self.queued(scan, 'link')
            if self.selected(scan, 'max'):
                self.queued(scan, 'max')
            if self.selected(scan, 'find'):
                self.queued(scan, 'find')
            if self.selected(scan, 'copy'):
                self.queued(scan, 'copy')
            if self.selected(scan, 'refine'):
                self.queued(scan, 'refine')
            if self.selected(scan, 'prepare'):
                self.queued(scan, 'prepare')
            if self.selected(scan, 'transform'):
                self.queued(scan, 'transform')
            if self.selected(scan, 'masked_transform'):
                self.queued(scan, 'masked_transform')
            if self.selected(scan, 'combine') or self.selected(scan, 'pdf'):
                multi_reduce = NXMultiReduce(scan)
                if self.selected(scan, 'combine'):
                    multi_reduce.combine = True
                    self.queued(scan, 'combine')
                if self.selected(scan, 'pdf'):
                    multi_reduce.pdf = True
                    self.queued(scan, 'pdf')
                if self.selected(scan, 'overwrite'):
                    multi_reduce.overwrite = True
                multi_reduce.queue()
            if self.selected(scan, 'masked_combine') or self.selected(scan, 'masked_pdf'):
                multi_reduce = NXMultiReduce(scan)
                multi_reduce.mask = True
                if self.selected(scan, 'masked_combine'):
                    multi_reduce.combine = True
                    self.queued(scan, 'masked_combine')
                if self.selected(scan, 'masked_pdf'):
                    multi_reduce.pdf = True
                    self.queued(scan, 'masked_pdf')
                if self.selected(scan, 'overwrite'):
                    multi_reduce.overwrite = True
                multi_reduce.queue()
        self.deselect_all()

    def view_logs(self):
        if self.grid is None:
            raise NeXusError('Need to update status')
        dialog = NXDialog(parent=self)
        dialog.setMinimumWidth(800)
        dialog.setMinimumHeight(600)
        scans = [os.path.basename(scan) for scan in self.scans]
        self.scan_combo = dialog.select_box(scans, slot=self.choose_scan)
        self.entry_combo = dialog.select_box(self.entries, 
                                             slot=self.refreshview)
        self.program_combo = dialog.select_box(self.programs, 
                                               slot=self.refreshview)
        self.defaultview = None
        self.output_box = NXPlainTextEdit(wrap=False)
        cpu_process_button = NXPushButton('View CPU Processes', self.procview)
        cpu_log_button = NXPushButton('View CPU Log', self.cpuview)
        self.cpu_combo = dialog.select_box(['nxserver'] + self.server.cpus,
                                           slot=self.cpuview)
        close_layout = self.make_layout(cpu_process_button, cpu_log_button,
                                        self.cpu_combo, 'stretch', 
                                        dialog.close_buttons(close=True),
                                        align='justified')
        dialog.set_layout(
            dialog.make_layout(self.scan_combo, self.entry_combo, 
                               self.program_combo),
            self.output_box,
            dialog.action_buttons(('View Data Directory', self.dataview),
                                  ('View Server Logs', self.serverview),
                                  ('View Workflow Logs', self.logview),
                                  ('View Workflow Output', self.outview),
                                  ('View Database', self.databaseview)),
            close_layout)
        scans = os.path.join(self.label, self.sample)
        dialog.setWindowTitle("'%s' Logs" % scans)
        self.view_dialog = dialog
        self.view_dialog.show()

    def choose_scan(self):
        scan = os.path.join(self.sample_directory, self.scan_combo.selected)
        current_entry = self.entry_combo.selected
        self.entry_combo.clear()
        self.entry_combo.add(*self.scans[scan]['entries'])
        if current_entry in self.entry_combo:
            self.entry_combo.select(current_entry)
        else:
            self.entry_combo.select(self.scans[scan]['entries'][0])
        self.refreshview()

    def dataview(self):
        self.defaultview = self.dataview
        scan = self.scan_combo.currentText()
        scan_directory = os.path.join(self.sample_directory, scan)
        if not os.path.exists(scan_directory):
            self.output_box.setPlainText('Directory has not been created')
            return
        text = []
        def _getmtime(entry):
            return entry.stat().st_mtime
        for f in sorted(os.scandir(scan_directory), key=_getmtime):
            text.append('{0}   {1}   {2}'.format(
                                        format_mtime(f.stat().st_mtime),
                                        human_size(f.stat().st_size, width=6), 
                                        f.name))
        if text:
            self.output_box.setPlainText('\n'.join(text))
        else:
            self.output_box.setPlainText('No Files')

    def serverview(self):
        self.defaultview = self.serverview
        scan = os.path.join(self.sample, self.label, 
                            self.scan_combo.currentText())
        with open(self.server.log_file) as f:
            lines = f.readlines()
        text = [line for line in lines if scan in line]
        if text:
            self.output_box.setPlainText(''.join(text))
            self.output_box.verticalScrollBar().setValue(
                self.output_box.verticalScrollBar().maximum())
        else:
            self.output_box.setPlainText('No Logs')

    def logview(self):
        self.defaultview = self.logview
        scan = os.path.join(self.label, 
                            self.sample + '_' + self.scan_combo.currentText())
        entry = self.entry_combo.currentText()
        prefix = scan + "['" + entry + "']: "
        alternate_prefix = scan + "['entry']: "
        with open(os.path.join(self.task_directory, 'nxlogger.log')) as f:
            lines = f.readlines()
        text = [line.replace(prefix, '').replace(alternate_prefix, '') 
                for line in lines if scan in line 
                if (entry in line or 'entry' in line)]
        if text:
            self.output_box.setPlainText(''.join(text))
            self.output_box.verticalScrollBar().setValue(
                self.output_box.verticalScrollBar().maximum())
        else:
            self.output_box.setPlainText('No Logs')

    def outview(self):
        self.defaultview = self.outview
        scan = self.sample + '_' + self.scan_combo.currentText()
        entry = self.entry_combo.currentText()
        program = 'nx' + self.program_combo.currentText()
        if (program == 'nxcombine' or program == 'nxmasked_combine' or
            program == 'nxpdf'):
            entry = 'entry'
        wrapper_file = os.path.join(self.sample_directory, scan+'.nxs')
        root = nxload(wrapper_file)
        if program in root[entry]:
            text = 'Date: ' + root[entry][program]['date'].nxvalue + '\n'
            text = text + root[entry][program]['note/data'].nxvalue
            self.output_box.setPlainText(text)
        else:
            self.output_box.setPlainText('No output for %s' % program)

    def databaseview(self):
        self.defaultview = self.databaseview
        scan = self.sample + '_' + self.scan_combo.currentText()
        entry = self.entry_combo.currentText()
        program = 'nx' + self.program_combo.currentText()
        if (program == 'nxcombine' or program == 'nxmasked_combine' or
            program == 'nxpdf'):
            entry = 'entry'
        wrapper_file = os.path.join(self.sample_directory, scan+'.nxs')
        f = self.db.get_file(wrapper_file)
        text = [' '.join([t.name, str(t.entry), str(t.status), 
                          str(t.queue_time), str(t.start_time), 
                          str(t.end_time)]) 
                for t in f.tasks if t.name == program]
        if text:
            self.output_box.setPlainText('\n'.join(text))
        else:
            self.output_box.setPlainText('No Entries')

    def procview(self):
        patterns = ['nxreduce', 'nxcombine', 'nxpdf', 'nxsum']
        if self.server.server_type == 'multicore':
            command = "ps -auxww | grep -e {}".format(" -e ".join(patterns))
        else:
            command = "pdsh -w {} 'ps -f' | grep -e {}".format(
                ",".join(self.server.cpus), " -e ".join(patterns))
        process = subprocess.run(command, shell=True, stdout=subprocess.PIPE,
                                                      stderr=subprocess.PIPE)
        if process.returncode == 0:
            lines = [l for l in sorted(process.stdout.decode().split('\n')) if l]
            lines = [l[l.index('nx'):] for l in lines if 'grep' not in l]
            self.output_box.setPlainText('\n'.join(lines))
        else:
            self.output_box.setPlainText(process.stderr.decode())

    def cpuview(self):
        cpu = self.cpu_combo.selected
        cpu_log = os.path.join(self.server.directory, '{}.log'.format(cpu))
        if os.path.exists(cpu_log):
            with open(cpu_log) as f:
                lines = f.readlines()
            self.output_box.setPlainText(''.join(lines))
            self.output_box.verticalScrollBar().setValue(
                self.output_box.verticalScrollBar().maximum())
        else:
            self.output_box.setPlainText('No Logs')

    def refreshview(self):
        if self.defaultview:
            self.defaultview()