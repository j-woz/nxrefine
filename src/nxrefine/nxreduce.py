import errno
import logging
import logging.handlers
import operator
import os
import platform
import shutil
import subprocess
import sys
import time
import timeit
from copy import deepcopy
from datetime import datetime

import h5py as h5
import numpy as np
from h5py import is_hdf5
from nexpy.gui.utils import clamp, timestamp
from nexusformat.nexus import (NeXusError, NXattenuator, NXcollection, NXdata,
                               NXentry, NXfield, NXinstrument, NXlink, NXLock,
                               NXmonitor, NXnote, NXprocess, NXreflections,
                               NXroot, NXsource, nxgetmemory, nxload,
                               nxsetlock, nxsetmemory)
from qtpy import QtCore

from . import __version__, blobcorrector
from .connectedpixels import blob_moments
from .labelimage import flip1, labelimage
from .nxdatabase import NXDatabase
from .nxrefine import NXPeak, NXRefine
from .nxserver import NXServer
from .nxsymmetry import NXSymmetry


class NXReduce(QtCore.QObject):
    """Data reduction workflow for single crystal diffuse x-ray scattering.

    All the components of the workflow required to reduce data from single
    crystals measured with high-energy synchrotron x-rays on a fast-area
    detector are defined as separate functions. The class is instantiated 
    by the entry in the experimental NeXus file corresponding to a single
    360° rotation of the crystal. 

        Parameters
        ----------
        entry : NXentry or str, optional
            Entry containing the rotation scan, by default None
        directory : str, optional
            Path to the directory containing the raw data, by default None
        parent : str, optional
            File path to the parent NeXus file, by default None
        entries : list of str, optional
            List of all the rotation scan entries in the file, by default None
        data : str, optional
            Path to the data field in the entry, by default 'data/data'
        extension : str, optional
            Extension of the raw data file, by default '.h5'
        path : str, optional
            [description], by default '/entry/data/data'
        threshold : [type], optional
            [description], by default None
        first : [type], optional
            [description], by default None
        last : [type], optional
            [description], by default None
        radius : [type], optional
            [description], by default None
        width : [type], optional
            [description], by default None
        monitor : [type], optional
            [description], by default None
        norm : [type], optional
            [description], by default None
        Qh : [type], optional
            [description], by default None
        Qk : [type], optional
            [description], by default None
        Ql : [type], optional
            [description], by default None
        link : bool, optional
            [description], by default False
        maxcount : bool, optional
            [description], by default False
        find : bool, optional
            [description], by default False
        copy : bool, optional
            [description], by default False
        refine : bool, optional
            [description], by default False
        lattice : bool, optional
            [description], by default False
        transform : bool, optional
            [description], by default False
        prepare : bool, optional
            [description], by default False
        mask : bool, optional
            [description], by default False
        overwrite : bool, optional
            [description], by default False
        gui : bool, optional
            [description], by default False
        """                 

    def __init__(self, entry=None, directory=None, parent=None, entries=None,
                 data='data/data', extension='.h5', path='/entry/data/data',
                 threshold=None, first=None, last=None, radius=None, width=None,
                 monitor=None, norm=None, Qh=None, Qk=None, Ql=None, 
                 link=False, maxcount=False, find=False, copy=False,
                 refine=False, lattice=False, transform=False, prepare=False, mask=False,
                 overwrite=False, gui=False):
 
        super(NXReduce, self).__init__()

        if isinstance(entry, NXentry):
            self.entry_name = entry.nxname
            self.wrapper_file = entry.nxfilename
            self.sample = os.path.basename(
                            os.path.dirname(
                              os.path.dirname(self.wrapper_file)))
            self.label = os.path.basename(os.path.dirname(self.wrapper_file))
            base_name = os.path.basename(os.path.splitext(self.wrapper_file)[0])
            self.scan = base_name.replace(self.sample+'_', '')
            self.directory = os.path.realpath(
                               os.path.join(
                                 os.path.dirname(self.wrapper_file), self.scan))
            self.root_directory = os.path.realpath(
                                    os.path.dirname(
                                      os.path.dirname(
                                        os.path.dirname(self.directory))))
            self._root = entry.nxroot
        elif directory is None:
            raise NeXusError('Directory not specified')
        else:
            self.directory = os.path.realpath(directory.rstrip('/'))
            self.root_directory = os.path.dirname(
                                      os.path.dirname(
                                        os.path.dirname(self.directory)))
            self.sample = os.path.basename(
                            os.path.dirname(
                              os.path.dirname(self.directory)))
            self.label = os.path.basename(os.path.dirname(self.directory))
            self.scan = os.path.basename(self.directory)
            self.wrapper_file = os.path.join(self.root_directory,
                                             self.sample, self.label,
                                             '%s_%s.nxs' %
                                             (self.sample, self.scan))
            self.entry_name = entry
            self._root = None
        self.base_directory = os.path.dirname(self.wrapper_file)
        if parent is None:
            self.parent_file = os.path.join(self.base_directory,
                                            self.sample+'_parent.nxs')
        else:
            self.parent_file = os.path.realpath(parent)
        
        self.mask_file = os.path.join(self.directory,
                                      self.entry_name+'_mask.nxs')
        self.transform_file = os.path.join(self.directory,
                                           self.entry_name+'_transform.nxs')
        self.masked_transform_file = os.path.join(self.directory,
                                        self.entry_name+'_masked_transform.nxs')
        self.settings_file = os.path.join(self.directory,
                                           self.entry_name+'_transform.pars')

        self._data = data
        self._field_root = None
        self._field = None
        self._shape = None
        self._mask_root = None
        self._pixel_mask = None
        self._parent_root = None
        self._parent = parent
        self._entries = entries

        if extension.startswith('.'):
            self.extension = extension
        else:
            self.extension = '.' + extension
        self.path = path

        self._threshold = threshold
        self._maximum = None
        self.summed_data = None
        self._first = first
        self._last = last
        self._monitor = monitor
        self._norm = norm
        self._radius = radius
        self.Qh = Qh
        self.Qk = Qk
        self.Ql = Ql

        self.link = link
        self.maxcount = maxcount
        self.find = find
        self.copy = copy
        self.refine = refine
        self.lattice = lattice
        self.transform = transform
        self.prepare = prepare
        self.mask = mask
        self.overwrite = overwrite
        self.gui = gui

        self._stopped = False

        self._server = None
        self._db = None
        self._logger = None

        nxsetlock(1800)

    start = QtCore.Signal(object)
    update = QtCore.Signal(object)
    result = QtCore.Signal(object)
    stop = QtCore.Signal()

    def __repr__(self):
        return "NXReduce('{}_{}/{}')".format(self.sample, self.scan, 
                                             self.entry_name)

    @property
    def task_directory(self):
        _directory = os.path.join(self.root_directory, 'tasks')
        if not os.path.exists(_directory):
            os.mkdir(_directory)
        return _directory

    @property
    def logger(self):
        if self._logger is None:
            self._logger = logging.getLogger("%s/%s_%s['%s']"
                  % (self.label, self.sample, self.scan, self.entry_name))
            self._logger.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                            '%(asctime)s %(name)-12s: %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
            for handler in self._logger.handlers:
                self._logger.removeHandler(handler)
            if os.path.exists(os.path.join(self.task_directory, 'nxlogger.pid')):
                socketHandler = logging.handlers.SocketHandler('localhost',
                                    logging.handlers.DEFAULT_TCP_LOGGING_PORT)
                self._logger.addHandler(socketHandler)
            else:
                fileHandler = logging.FileHandler(os.path.join(
                                                    self.task_directory, 
                                                    'nxlogger.log'))
                fileHandler.setFormatter(formatter)
                self._logger.addHandler(fileHandler)
            if not self.gui:
                streamHandler = logging.StreamHandler()
                self._logger.addHandler(streamHandler)
        return self._logger

    @property
    def server(self):
        if self._server is None:
            try:
                self._server = NXServer()
            except Exception as error:
                self.logger.info(str(error))
        return self._server

    @property
    def db(self):
        if self._db is None:
            try:
                self._db = NXDatabase(os.path.join(self.task_directory, 
                                                   'nxdatabase.db'))
            except Exception as error:
                self.logger.info(str(error))
        return self._db

    @property
    def root(self):
        if self._root is None:
            self._root = nxload(self.wrapper_file, 'rw')
        return self._root

    @property
    def entry(self):
        if self.entry_name in self.root:
            return self.root[self.entry_name]
        else:
            return None

    @property
    def entries(self):
        if self._entries:
            return self._entries
        else:
            return [entry for entry in self.root.entries if entry != 'entry']

    @property
    def first_entry(self):
        if self.entries:
            return self.entry_name == self.entries[0]
        else:
            return None

    @property
    def data(self):
        if 'data' in self.entry:
            return self.entry['data']
        else:
            return None

    @property
    def field(self):
        if self._field is None:
            self._field = self.data.nxsignal
            self._shape = self._field.shape
        return self._field

    @property
    def shape(self):
        if self._shape is None:
            self._shape = self.field.shape
        return self._shape

    @property
    def data_file(self):
        return self.entry[self._data].nxfilename

    def data_exists(self):
        return is_hdf5(self.data_file)

    @property
    def mask_root(self):
        if self._mask_root is None:
            self._mask_root = nxload(self.mask_file, 'a')
            if 'entry' not in self.mask_root:
                self.mask_root['entry'] = NXentry()
        return self._mask_root

    @property
    def pixel_mask(self):
        if self._pixel_mask is None:
            try:
                self._pixel_mask = self.entry['instrument/detector/pixel_mask'].nxvalue
            except Exception as error:
                pass
        return self._pixel_mask

    @pixel_mask.setter
    def pixel_mask(self, mask):
        with self.entry.nxfile:
            self.entry['instrument/detector/pixel_mask'] = mask

    @property
    def parent_root(self):
        if self._parent_root is None:
            self._parent_root = nxload(self.parent_file, 'r')
        return self._parent_root

    @property
    def parent(self):
        if self._parent is None:
            if not self.is_parent() and os.path.exists(self.parent_file):
                self._parent = os.path.realpath(self.parent_file)
        return self._parent

    def is_parent(self):
        if (os.path.exists(self.parent_file) and
            os.path.realpath(self.parent_file) == self.wrapper_file):
            return True
        else:
            return False

    def make_parent(self):
        if self.is_parent():
            self.logger.info(f"'{self.wrapper_file}' already set as parent")
            return
        elif os.path.exists(self.parent_file):
            if self.overwrite:
                os.remove(self.parent_file)
            else:
                raise NeXusError("'%s' already set as parent"
                                 % os.path.realpath(self.parent_file))
        self.record_start('nxcopy')
        os.symlink(os.path.basename(self.wrapper_file), self.parent_file)
        self.record('nxcopy', parent=self.wrapper_file)
        self.record_end('nxcopy')
        self._parent = None
        self.logger.info(
            f"'{os.path.realpath(self.parent_file)}' set as parent")

    @property
    def first(self):
        _first = self._first
        if _first is None:
            if 'nxreduce' in self.root['entry']:
                _first = self.root['entry/nxreduce/first_frame']
            elif 'peaks' in self.entry and 'first' in self.entry['peaks'].attrs:
                _first = self.entry['peaks'].attrs['first']
            elif 'data' in self.entry and 'first' in self.entry['data'].attrs:
                _first = self.entry['data'].attrs['first']
            elif self.parent:
                root = self.parent_root
                entry = root[self.entry_name]
                if 'nxreduce' in root['entry']:
                    _first = root['entry/nxreduce/first_frame']
                elif 'peaks' in entry and 'first' in entry['peaks'].attrs:
                    _first = entry['peaks'].attrs['first']
                elif 'first' in entry['data'].attrs:
                    _first = entry['data'].attrs['first']
        try:
            self._first = int(_first)
        except Exception as error:
            self._first = None
        return self._first

    @first.setter
    def first(self, value):
        try:
            self._first = np.int(value)
        except ValueError:
            pass

    @property
    def last(self):
        _last = self._last
        if _last is None:
            if 'nxreduce' in self.root['entry']:
                _last = self.root['entry/nxreduce/last_frame']
            elif 'peaks' in self.entry and 'last' in self.entry['peaks'].attrs:
                _last = self.entry['peaks'].attrs['last']
            elif 'data' in self.entry and 'last' in self.entry['data'].attrs:
                _last = self.entry['data'].attrs['last']
            elif self.parent:
                root = self.parent_root
                entry = root[self.entry_name]
                if 'nxreduce' in root['entry']:
                    _last = root['entry/nxreduce/last_frame']
                elif 'peaks' in entry and 'last' in entry['peaks'].attrs:
                    _last = entry['peaks'].attrs['last']
                elif 'last' in root[self.entry_name]['data'].attrs:
                    _last = entry['data'].attrs['last']
        try:
            self._last = int(_last)
        except Exception as error:
            self._last = None
        return self._last

    @last.setter
    def last(self, value):
        try:
            self._last = np.int(value)
        except ValueError:
            pass

    @property
    def threshold(self):
        _threshold = self._threshold
        if _threshold is None:
            if 'nxreduce' in self.root['entry']:
                _threshold = self.root['entry/nxreduce/threshold']
            elif ('peaks' in self.entry and 
                  'threshold' in self.entry['peaks'].attrs):
                _threshold = self.entry['peaks'].attrs['threshold']
            elif self.parent:
                root = self.parent_root
                entry = root[self.entry_name]
                if 'nxreduce' in root['entry']:
                    _threshold = root['entry/nxreduce/threshold']
                elif ('peaks' in entry and 'threshold' in entry['peaks'].attrs):
                    _threshold = entry['peaks'].attrs['threshold']
        if _threshold is None:
            if self.maximum is not None:
                _threshold = self.maximum / 10
        try:
            self._threshold = float(_threshold)
            if self._threshold <= 0.0:
                self._threshold = None
        except:
            self._threshold = None
        return self._threshold

    @threshold.setter
    def threshold(self, value):
        self._threshold = value

    @property
    def radius(self):
        _radius = self._radius
        if _radius is None:
            if ('nxreduce' in self.root['entry'] and 
                'radius' in self.root['entry/nxreduce']):
                _radius = self.root['entry/nxreduce/radius']
            elif self.parent:
                root = self.parent_root
                if ('nxreduce' in root['entry'] and 
                    'radius' in root['entry/nxreduce']):
                    _radius = root['entry/nxreduce/radius']
        if _radius is None:
            _radius = 0.2
        try:
            self._radius = float(_radius)
        except:
            self._radius = 0.2  
        return self._radius

    @radius.setter
    def radius(self, value):
        self._radius = value

    @property
    def norm(self):
        _norm = self._norm
        if _norm is None:
            if 'nxreduce' in self.root['entry']:
                _norm = self.root['entry/nxreduce/norm']
            elif 'peaks' in self.entry and 'norm' in self.entry['peaks'].attrs:
                _norm = self.entry['peaks'].attrs['norm']
            elif self.parent:
                root = self.parent_root
                entry = root[self.entry_name]
                if 'nxreduce' in root['entry']:
                    _norm = root['entry/nxreduce/norm']
                elif 'peaks' in entry and 'norm' in entry['peaks'].attrs:
                    _norm = entry['peaks'].attrs['norm']
        try:
            self._norm = float(_norm)
            if self._norm <= 0:
                self._norm = None
        except:
            self._norm = None
        return self._norm

    @norm.setter
    def norm(self, value):
        self._norm = value

    @property
    def monitor(self):
        _monitor = self._monitor
        if _monitor is None:
            if 'nxreduce' in self.root['entry']:
                _monitor = self.root['entry/nxreduce/monitor']
            elif self.parent:
                root = self.parent_root
                if 'nxreduce' in root['entry']:
                    _monitor = root['entry/nxreduce/monitor']
                else:
                    _monitor = 'monitor1'
            else:
                _monitor = 'monitor1'
        self._monitor = str(_monitor)
        return self._monitor

    @monitor.setter
    def monitor(self, value):
        self._monitor = value

    @property
    def maximum(self):
        if self._maximum is None:
            if 'data' in self.entry and 'maximum' in self.entry['data'].attrs:
                self._maximum = self.entry['data'].attrs['maximum']
        return self._maximum

    def complete(self, program):
        if program == 'nxcombine':
            return program in self.root['entry']
        else:
            return program in self.entry

    def all_complete(self, program):
        """ Check that all entries for this temperature are done """
        complete = True
        for entry in self.entries:
            if program not in self.root[entry]:
                complete = False
        return complete

    def not_complete(self, program):
        return program not in self.entry or self.overwrite

    def start_progress(self, start, stop):
        self._start = start
        if self.gui:
            self._step = (stop - start) / 100
            self._value = int(start)
            self.start.emit((0, 100))
        else:
            print('Frame', end='')
        self.stopped = False
        return timeit.default_timer()

    def update_progress(self, i):
        if self.gui:
            _value = int(i/self._step)
            if  _value > self._value:
                self.update.emit(_value)
                self._value = _value
        elif (i - self._start) % 100 == 0:
            print('\rFrame %d' % i, end='')

    def stop_progress(self):
        if not self.gui:
            print('')
        self.stopped = True
        return timeit.default_timer()

    @property
    def stopped(self):
        return self._stopped

    @stopped.setter
    def stopped(self, value):
        self._stopped = value

    def record(self, program, **kwargs):
        """ Record that a task has finished. Update NeXus file and database """
        process = kwargs.pop('process', program)
        parameters = '\n'.join(
            [('%s: %s' % (k, v)).replace('_', ' ').capitalize()
             for (k,v) in kwargs.items()])
        note = NXnote(process, ('Current machine: %s\n' % platform.node() +
                                'Current directory: %s\n' % self.directory +
                                parameters))
        with self.root.nxfile:
            if process in self.entry:
                del self.entry[process]
            self.entry[process] = NXprocess(program='%s' % process,
                                            sequence_index=len(self.entry.NXprocess)+1,
                                            version='nxrefine v'+__version__,
                                            note=note)

    def record_start(self, program):
        """ Record that a task has started. Update database """
        try:
            self.db.start_task(self.wrapper_file, program, self.entry_name)
        except Exception as error:
            self.logger.info(str(error))

    def record_end(self, program):
        """ Record that a task has ended. Update database """
        try:
            self.db.end_task(self.wrapper_file, program, self.entry_name)
        except Exception as error:
            self.logger.info(str(error))

    def record_fail(self, program):
        """ Record that a task has failed. Update database """
        try:
            self.db.fail_task(self.wrapper_file, program, self.entry_name)
        except Exception as error:
            self.logger.info(str(error))

    def nxlink(self):
        if self.not_complete('nxlink') and self.link:
            if not self.data_exists():
                self.logger.info('Data file not available')                
                return
            self.record_start('nxlink')
            self.link_data()
            logs = self.read_logs()
            if logs:
                self.transfer_logs(logs)
                self.record('nxlink', logs='Transferred')
                self.record_end('nxlink')
                self.logger.info('Entry linked to raw data')
            else:
                self.record_fail('nxlink')
        elif self.link:
            self.logger.info('Data already linked')

    def link_data(self):
        if self.field:
            with self.root.nxfile:
                frames = np.arange(self.shape[0], dtype=np.int32)
                if 'instrument/detector/frame_time' in self.entry:
                    frame_time = self.entry['instrument/detector/frame_time']
                else:
                    frame_time = 0.1
                if 'data' not in self.entry:
                    self.entry['data'] = NXdata()
                    self.entry['data/x_pixel'] = np.arange(
                        self.shape[2], dtype=np.int32)
                    self.entry['data/y_pixel'] = np.arange(
                        self.shape[1], dtype=np.int32)
                    self.entry['data/frame_number'] = frames
                    self.entry['data/frame_time'] = frame_time * frames
                    self.entry['data/frame_time'].attrs['units'] = 's'
                    data_file = os.path.relpath(
                        self.data_file, os.path.dirname(self.wrapper_file))
                    self.entry['data/data'] = NXlink(self.path, data_file)
                    self.entry['data'].nxsignal = self.entry['data/data']
                    self.logger.info(
                        'Data group created and linked to external data')
                else:
                    if self.entry['data/frame_number'].shape != self.shape[0]:
                        del self.entry['data/frame_number']
                        self.entry['data/frame_number'] = frames
                        if 'frame_time' in self.entry['data']:
                            del self.entry['data/frame_time']
                        self.logger.info('Fixed frame number axis')
                    if 'data/frame_time' not in self.entry:
                        self.entry['data/frame_time'] = frame_time * frames
                        self.entry['data/frame_time'].attrs['units'] = 's'
                self.entry['data'].nxaxes = [self.entry['data/frame_number'],
                                             self.entry['data/y_pixel'],
                                             self.entry['data/x_pixel']]
                with self.field.nxfile as f:
                    time_path = (
                        'entry/instrument/NDAttributes/NDArrayTimeStamp')
                    if time_path in f:
                        start = datetime.fromtimestamp(f[time_path][0])
                        #In EPICS, the epoch started in 1990, not 1970
                        start_time = start.replace(
                            year=start.year+20).isoformat()
                        self.entry['start_time'] = start_time
                        self.entry['data/frame_time'].attrs['start'] = start_time
        else:
            self.logger.info('No raw data loaded')

    def read_logs(self):
        head_file = os.path.join(self.directory, self.entry_name+'_head.txt')
        meta_file = os.path.join(self.directory, self.entry_name+'_meta.txt')
        if os.path.exists(head_file) and os.path.exists(meta_file):
            logs = NXcollection()
        else:
            if not os.path.exists(head_file):
                self.logger.info(
                    f"'{self.entry_name}_head.txt' does not exist")
            if not os.path.exists(meta_file):
                self.logger.info(
                    f"'{self.entry_name}_meta.txt' does not exist")
            return None
        with open(head_file) as f:
            lines = f.readlines()
        for line in lines:
            key, value = line.split(', ')
            value = value.strip('\n')
            try:
               value = np.float(value)
            except:
                pass
            logs[key] = value
        meta_input = np.genfromtxt(meta_file, delimiter=',', names=True)
        for i, key in enumerate(meta_input.dtype.names):
            logs[key] = [array[i] for array in meta_input]
        return logs

    def transfer_logs(self, logs):
        with self.root.nxfile:
            if 'instrument' not in self.entry:
                self.entry['instrument'] = NXinstrument()
            if 'logs' in self.entry['instrument']:
                del self.entry['instrument/logs']
            self.entry['instrument/logs'] = logs
            frame_number = self.entry['data/frame_number']
            frames = frame_number.size
            if 'MCS1' in logs:
                if 'monitor1' in self.entry:
                    del self.entry['monitor1']
                data = logs['MCS1'][:frames]
                #Remove outliers at beginning and end of frames
                data[0] = data[1]
                data[-1] = data[-2]
                self.entry['monitor1'] = NXmonitor(NXfield(data, name='MCS1'),
                                                   frame_number)
                if 'data/frame_time' in self.entry:
                    self.entry['monitor1/frame_time'] = (
                        self.entry['data/frame_time'])
            if 'MCS2' in logs:
                if 'monitor2' in self.entry:
                    del self.entry['monitor2']
                data = logs['MCS2'][:frames]
                #Remove outliers at beginning and end of frames
                data[0] = data[1]
                data[-1] = data[-2]
                self.entry['monitor2'] = NXmonitor(NXfield(data, name='MCS2'),
                                                   frame_number)
                if 'data/frame_time' in self.entry:
                    self.entry['monitor2/frame_time'] = (
                        self.entry['data/frame_time'])
            if 'source' not in self.entry['instrument']:
                self.entry['instrument/source'] = NXsource()
            self.entry['instrument/source/name'] = 'Advanced Photon Source'
            self.entry['instrument/source/type'] = 'Synchrotron X-ray Source'
            self.entry['instrument/source/probe'] = 'x-ray'
            if 'Storage_Ring_Current' in logs:
                self.entry['instrument/source/current'] = (
                    logs['Storage_Ring_Current'])
            if 'SCU_Current' in logs:
                self.entry['instrument/source/undulator_current'] = (
                    logs['SCU_Current'])
            if 'UndulatorA_gap' in logs:
                self.entry['instrument/source/undulator_gap'] = (
                    logs['UndulatorA_gap'])
            if 'Calculated_filter_transmission' in logs:
                if 'attenuator' not in self.entry['instrument']:
                    self.entry['instrument/attenuator'] = NXattenuator()
                self.entry['instrument/attenuator/attenuator_transmission'] = (
                    logs['Calculated_filter_transmission'])

    def nxmax(self):
        if self.not_complete('nxmax') and self.maxcount:
            if not self.data_exists():
                self.logger.info('Data file not available')
                return
            self.record_start('nxmax')
            maximum = self.find_maximum()
            if self.gui:
                if maximum:
                    self.result.emit(maximum)
                self.stop.emit()
            else:
                self.write_maximum(maximum)
                self.record('nxmax', maximum=maximum,
                            first_frame=self.first, last_frame=self.last)
                self.record_end('nxmax')
        elif self.maxcount:
            self.logger.info('Maximum counts already found')

    def find_maximum(self):
        self.logger.info('Finding maximum counts')
        with self.field.nxfile:
            maximum = 0.0
            nframes = self.shape[0]
            chunk_size = self.field.chunks[0]
            if chunk_size < 20:
                chunk_size = 50
            if self.first == None:
                self.first = 0
            if self.last == None:
                self.last = nframes
            data = self.field.nxfile[self.path]
            fsum = np.zeros(nframes, dtype=np.float64)
            pixel_mask = self.pixel_mask
            #Add constantly firing pixels to the mask
            pixel_max = np.zeros((self.shape[1], self.shape[2]))
            v = data[0:10,:,:]
            for i in range(10):
                pixel_max = np.maximum(v[i,:,:], pixel_max)
            pixel_mean=v.sum(0) / 10.
            mask = np.zeros((self.shape[1], self.shape[2]), dtype=np.int8)
            mask[np.where(pixel_max == pixel_mean)] = 1
            mask[np.where(pixel_mean < 100)] = 0
            pixel_mask = pixel_mask | mask
            self.pixel_mask = pixel_mask
            #Start looping over the data
            tic = self.start_progress(self.first, self.last)
            for i in range(self.first, self.last, chunk_size):
                if self.stopped:
                    return None
                self.update_progress(i)
                try:
                    v = data[i:i+chunk_size,:,:]
                except IndexError as error:
                    pass
                if i == self.first:
                    vsum = v.sum(0)
                else:
                    vsum += v.sum(0)
                if pixel_mask is not None:
                    v = np.ma.masked_array(v)
                    v.mask = pixel_mask
                fsum[i:i+chunk_size] = v.sum((1,2))
                if maximum < v.max():
                    maximum = v.max()
                del v
        if pixel_mask is not None:
            vsum = np.ma.masked_array(vsum)
            vsum.mask = pixel_mask
        self.summed_data = NXfield(vsum, name='summed_data')
        self.summed_frames = NXfield(fsum, name='summed_frames')
        toc = self.stop_progress()
        self.logger.info(f'Maximum counts: {maximum} ({(toc-tic):g} seconds)')
        return maximum

    def write_maximum(self, maximum):
        with self.root.nxfile:
            self.entry['data'].attrs['maximum'] = maximum
            self.entry['data'].attrs['first'] = self.first
            self.entry['data'].attrs['last'] = self.last
            if 'summed_data' in self.entry:
                del self.entry['summed_data']
            self.entry['summed_data'] = NXdata(self.summed_data,
                                               self.entry['data'].nxaxes[-2:])
            if 'summed_frames' in self.entry:
                del self.entry['summed_frames']
            self.entry['summed_frames'] = NXdata(self.summed_frames,
                                                 self.entry['data'].nxaxes[0])
            calculations = self.calculate_radial_sums()
            if calculations:
                polar_angle, intensity, polarization = calculations
                if 'radial_sum' in self.entry:
                    del self.entry['radial_sum']
                self.entry['radial_sum'] = NXdata(
                    NXfield(intensity, name='radial_sum'),
                    NXfield(polar_angle, name='polar_angle'))
                if 'polarization' not in self.entry['instrument/detector']:
                    self.entry['instrument/detector/polarization'] = polarization

    def calculate_radial_sums(self):
        try:
            from pyFAI.azimuthalIntegrator import AzimuthalIntegrator
            parameters = self.entry['instrument/calibration/refinement/parameters']
            ai = AzimuthalIntegrator(
                dist=parameters['Distance'].nxvalue,
                detector=parameters['Detector'].nxvalue,
                poni1=parameters['Poni1'].nxvalue,
                poni2=parameters['Poni2'].nxvalue,
                rot1=parameters['Rot1'].nxvalue,
                rot2=parameters['Rot2'].nxvalue,
                rot3=parameters['Rot3'].nxvalue,
                pixel1=parameters['PixelSize1'].nxvalue,
                pixel2=parameters['PixelSize2'].nxvalue,
                wavelength=parameters['Wavelength'].nxvalue)
            polarization = ai.polarization(factor=0.99)
            counts = self.summed_data.nxvalue / polarization
            polar_angle, intensity = ai.integrate1d(counts, 
                                                    2048,
                                                    unit='2th_deg',
                                                    mask=self.pixel_mask,
                                                    correctSolidAngle=True)
            return polar_angle, intensity, polarization
        except Exception as error:
            self.logger.info('Unable to create radial sum')
            self.logger.info(str(error))
            return None

    def nxfind(self):
        if self.not_complete('nxfind') and self.find:
            if not self.data_exists():
                self.logger.info('Data file not available')
                return
            self.record_start('nxfind')
            peaks = self.find_peaks()
            if self.gui:
                if peaks:
                    self.result.emit(peaks)
                self.stop.emit()
            elif peaks:
                self.write_peaks(peaks)
                self.record('nxfind', threshold=self.threshold,
                            first_frame=self.first, last_frame=self.last,
                            peak_number=len(peaks))
                self.record_end('nxfind')
            else:
                self.record_fail('nxfind')
        elif self.find:
            self.logger.info('Peaks already found')

    def find_peaks(self):
        self.logger.info("Finding peaks")
        with self.root.nxfile:
            self._threshold, self._maximum = self.threshold, self.maximum

        if self.threshold is None:
            if self.maximum is None:
                self.maxcount = True
                self.nxmax()
            self.threshold = self.maximum / 10

        with self.field.nxfile:
            if self.first == None:
                self.first = 0
            if self.last == None:
                self.last = self.shape[0]
            z_min, z_max = self.first, self.last

            tic = self.start_progress(z_min, z_max)

            lio = labelimage(self.shape[-2:], flipper=flip1)
            allpeaks = []
            if len(self.shape) == 2:
                res = None
            else:
                chunk_size = self.field.chunks[0]
                pixel_tolerance = 50
                frame_tolerance = 10
                nframes = z_max
                data = self.field.nxfile[self.path]
                for i in range(0, nframes, chunk_size):
                    if self.stopped:
                        return None
                    try:
                        if i + chunk_size > z_min and i < z_max:
                            self.update_progress(i)
                            v = data[i:i+chunk_size,:,:]
                            for j in range(chunk_size):
                                if i+j >= z_min and i+j <= z_max:
                                    omega = np.float32(i+j)
                                    lio.peaksearch(v[j], self.threshold, omega)
                                    if lio.res is not None:
                                        blob_moments(lio.res)
                                        for k in range(lio.res.shape[0]):
                                            res = lio.res[k]
                                            peak = NXBlob(res[0], res[22],
                                                res[23], res[24], omega,
                                                res[27], res[26], res[29],
                                                self.threshold,
                                                pixel_tolerance,
                                                frame_tolerance)
                                            if peak.isvalid(self.pixel_mask):
                                                allpeaks.append(peak)
                    except IndexError as error:
                        pass

        if not allpeaks:
            toc = self.stop_progress()
            self.logger.info('No peaks found (%g seconds)' % (toc-tic))
            return None

        allpeaks = sorted(allpeaks)

        self.start_progress(z_min, z_max)

        merged_peaks = []
        for z in range(z_min, z_max+1):
            if self.stopped:
                return None
            self.update_progress(z)
            frame = [peak for peak in allpeaks if peak.z == z]
            if not merged_peaks:
                merged_peaks.extend(frame)
            else:
                for peak1 in frame:
                    combined = False
                    for peak2 in last_frame:
                        if peak1 == peak2:
                            for idx in range(len(merged_peaks)):
                                if peak1 == merged_peaks[idx]:
                                    break
                            peak1.combine(merged_peaks[idx])
                            merged_peaks[idx] = peak1
                            combined = True
                            break
                    if not combined:
                        reversed_peaks = [p for p in reversed(merged_peaks)
                                          if p.z >= peak1.z - frame_tolerance]
                        for peak2 in reversed_peaks:
                            if peak1 == peak2:
                                for idx in range(len(merged_peaks)):
                                    if peak1 == merged_peaks[idx]:
                                        break
                                peak1.combine(merged_peaks[idx])
                                merged_peaks[idx] = peak1
                                combined = True
                                break
                        if not combined:
                            merged_peaks.append(peak1)

            if frame:
                last_frame = frame

        merged_peaks = sorted(merged_peaks)
        for peak in merged_peaks:
            peak.merge()

        merged_peaks = sorted(merged_peaks)
        peaks = merged_peaks
        toc = self.stop_progress()
        self.logger.info('%s peaks found (%g seconds)' % (len(peaks), toc-tic))
        return peaks

    def write_peaks(self, peaks):
        group = NXreflections()
        shape = (len(peaks),)
        group['npixels'] = NXfield([peak.np for peak in peaks], dtype=float)
        group['intensity'] = NXfield([peak.intensity for peak in peaks],
                                     dtype=float)
        group['x'] = NXfield([peak.x for peak in peaks], dtype=float)
        group['y'] = NXfield([peak.y for peak in peaks], dtype=float)
        group['z'] = NXfield([peak.z for peak in peaks], dtype=float)
        group['sigx'] = NXfield([peak.sigx for peak in peaks], dtype=float)
        group['sigy'] = NXfield([peak.sigy for peak in peaks], dtype=float)
        group['covxy'] = NXfield([peak.covxy for peak in peaks], 
                                 dtype=float)
        group.attrs['first'] = self.first
        group.attrs['last'] = self.last
        group.attrs['threshold'] = self.threshold
        with self.root.nxfile:
            if 'peaks' in self.entry:
                del self.entry['peaks']
            self.entry['peaks'] = group
            refine = NXRefine(self.entry)
            polar_angles, azimuthal_angles = refine.calculate_angles(refine.xp,
                                                                     refine.yp)
            refine.write_angles(polar_angles, azimuthal_angles)

    def nxcopy(self):
        if not self.copy:
            return
        elif self.is_parent():
            self.logger.info('Set as parent; no parameters copied')
        elif self.not_complete('nxcopy'):
            self.record_start('nxcopy')
            if self.parent:
                self.copy_parameters()
                self.record('nxcopy', parent=self.parent)
                self.record_end('nxcopy')
            else:
                self.logger.info('No parent defined')
                self.record_fail('nxcopy')
        else:
            self.logger.info('Parameters already copied')

    def copy_parameters(self):
        with self.parent_root.nxfile:
            input = self.parent_root
            input_ref = NXRefine(input[self.entry_name])
            with self.root.nxfile:
                output_ref = NXRefine(self.entry)
                input_ref.copy_parameters(output_ref, sample=True, 
                                          instrument=True)
        self.logger.info("Parameters copied from '%s'" %
                         os.path.basename(os.path.realpath(self.parent)))

    def nxrefine(self):
        if self.not_complete('nxrefine') and self.refine:
            if not self.complete('nxfind'):
                self.logger.info('Cannot refine until peak search is completed')
                return
            self.record_start('nxrefine')
            self.logger.info('Refining orientation')
            if self.lattice or self.first_entry:
                lattice = True
            else:
                lattice = False
            result = self.refine_parameters(lattice=lattice)
            if result:
                if not self.gui:
                    self.write_refinement(result)
                self.record('nxrefine', fit_report=result.fit_report)
                self.record_end('nxrefine')
            else:
                self.record_fail('nxrefine')
        elif self.refine:
            self.logger.info('HKL values already refined')

    def refine_parameters(self, lattice=False):
        with self.root.nxfile:
            refine = NXRefine(self.entry)
            refine.refine_hkls(lattice=lattice, chi=True, omega=True)
            fit_report=refine.fit_report
            refine.refine_hkls(chi=True, omega=True)
            fit_report = fit_report + '\n' + refine.fit_report
            refine.refine_orientation_matrix()
            fit_report = fit_report + '\n' + refine.fit_report
            if refine.result.success:
                refine.fit_report = fit_report
                self.logger.info('Refined HKL values')
                return refine
            else:
                self.logger.info('HKL refinement not successful')
                return None

    def write_refinement(self, refine):
        with self.root.nxfile:
            refine.write_parameters()

    def nxtransform(self):
        if self.not_complete('nxtransform') and self.transform:
            if not self.complete('nxrefine'):
                self.logger.info(
                    'Cannot transform until the orientation is complete')
                return
            self.record_start('nxtransform')
            cctw_command = self.prepare_transform()
            if cctw_command:
                self.logger.info('Transform process launched')
                tic = timeit.default_timer()
                with self.field.nxfile:
                    with NXLock(self.transform_file):
                        process = subprocess.run(cctw_command, shell=True,
                                                 stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE)
                toc = timeit.default_timer()
                if process.returncode == 0:
                    self.logger.info('Transform completed (%g seconds)'
                                     % (toc-tic))
                    self.record('nxtransform', norm=self.norm,
                                command=cctw_command,
                                output=process.stdout.decode(),
                                errors=process.stderr.decode())
                    self.record_end('nxtransform')
                else:
                    self.logger.info(
                        'Transform completed - errors reported (%g seconds)'
                        % (toc-tic))
                    self.record_fail('nxtransform')
            else:
                self.logger.info('CCTW command invalid')
                self.record_fail('nxtransform')
        elif self.transform:
            self.logger.info('Data already transformed')

    def get_transform_grid(self):
        if self.Qh and self.Qk and self.Ql:
            try:
                self.Qh = [np.float32(v) for v in self.Qh]
                self.Qk = [np.float32(v) for v in self.Qk]
                self.Ql = [np.float32(v) for v in self.Ql]
            except Exception:
                self.Qh = self.Qk = self.Ql = None
        else:
            if 'transform' in self.entry:
                transform = self.entry['transform']
            elif 'masked_transform' in self.entry:
                transform = self.entry['masked_transform']
            elif self.parent:
                root = self.parent_root
                if 'transform' in root[self.entry_name]:
                    transform = root[self.entry_name]['transform']
                elif 'masked_transform' in root[self.entry_name]:
                    transform = root[self.entry_name]['masked_transform']
            try:
                Qh, Qk, Ql = (transform['Qh'].nxvalue,
                              transform['Qk'].nxvalue,
                              transform['Ql'].nxvalue)
                self.Qh = Qh[0], Qh[1]-Qh[0], Qh[-1]
                self.Qk = Qk[0], Qk[1]-Qk[0], Qk[-1]
                self.Ql = Ql[0], Ql[1]-Ql[0], Ql[-1]
            except Exception:
                self.Qh = self.Qk = self.Ql = None

    def get_normalization(self):
        from scipy.signal import savgol_filter
        with self.root.nxfile:
            if self.norm and self.monitor in self.entry:
                monitor_signal = self.entry[self.monitor].nxsignal / self.norm
                monitor_signal[0] = monitor_signal[1]
                monitor_signal[-1] = monitor_signal[-2]
                self.data['monitor_weight'] = savgol_filter(monitor_signal, 
                                                            501, 2)
                self.data['monitor_weight'].attrs['axes'] = 'frame_number'
                self.data['monitor_weight'][0] = self.data['monitor_weight'][1]
                self.data['monitor_weight'][-1] = self.data['monitor_weight'][-2]

    def prepare_transform(self, mask=False):
        if mask:
            transform_file = self.masked_transform_file
        else:
            transform_file = self.transform_file
        with self.root.nxfile:
            self.get_transform_grid()
            if self.norm:
                self.get_normalization()
            if self.Qh and self.Qk and self.Ql:
                refine = NXRefine(self.entry)
                refine.read_parameters()
                refine.h_start, refine.h_step, refine.h_stop = self.Qh
                refine.k_start, refine.k_step, refine.k_stop = self.Qk
                refine.l_start, refine.l_step, refine.l_stop = self.Ql
                refine.define_grid()
                refine.prepare_transform(transform_file, mask=mask)
                refine.write_settings(self.settings_file)
                command = refine.cctw_command(mask)
                if command and os.path.exists(transform_file):
                    with NXLock(transform_file):
                        os.remove(transform_file)
                return command
            else:
                self.logger.info('Invalid HKL grid')
                return None

    def nxprepare(self):
        if self.not_complete('nxprepare_mask') and self.prepare:
            if not self.complete('nxrefine'):
                self.logger.info('Cannot prepare mask until the orientation is complete')
                return
            self.record_start('nxprepare')
            self.logger.info('Preparing 3D mask')
            tic = timeit.default_timer()
            self.prepare_mask()
            self.link_mask()
            self.record('nxprepare', masked_file=self.mask_file, 
                        process='nxprepare_mask')
            self.record_end('nxprepare')
            toc = timeit.default_timer()
            self.logger.info("3D Mask stored in '%s' (%g seconds)"
                             % (self.mask_file, toc-tic))
        elif self.prepare:
            self.logger.info('3D Mask already prepared')

    def prepare_mask(self):
        self.logger.info("Calculating peaks to be masked")
        with self.root.nxfile:
            refine = NXRefine(self.entry)
        peaks = refine.get_xyzs()
        self.logger.info("Optimizing peak frames")
        for peak in peaks:
            self.get_xyz_frame(peak)
        self.write_xyz_peaks(peaks)
        self.logger.info("Determining 3D mask radii")
        masks = self.prepare_xyz_masks(peaks)
        self.logger.info("Writing 3D peak mask parameters")
        self.write_xyz_masks(masks)
        self.logger.info("Writing 3D edge mask parameters")
        self.write_xyz_edges()
        self.logger.info("Masked frames stored in %s" % self.mask_file)

    def link_mask(self):
        with self.root.nxfile:
            mask_file = os.path.relpath(self.mask_file, 
                                        os.path.dirname(self.wrapper_file))
            if 'data_mask' in self.data:
                del self.data['data_mask']
            self.data['data_mask'] = NXlink('entry/mask', mask_file)

    def get_xyz_frame(self, peak):
        slab = self.get_xyz_slab(peak)
        if slab.nxsignal.min() < 0: #Slab includes gaps in the detector
            slab = self.get_xyz_slab(peak, width=30)
        cut = slab.sum((1,2))
        x, y = cut.nxaxes[0], cut.nxsignal
        try:
            slope = (y[-1]-y[0]) / (x[-1]-x[0])
            constant = y[0] - slope * x[0]
            z = (cut - constant - slope*x).moment().nxvalue
        except Exception:
            pass
        if z > x[0] and z < x[-1]:
            peak.z = z
        peak.x, peak.y, peak.z = (clamp(peak.x, 0, self.shape[2]-1), 
                                  clamp(peak.y, 0, self.shape[1]-1), 
                                  clamp(peak.z, 0, self.shape[0]-1))
        peak.pixel_count = self.data[peak.z, peak.y, peak.x].nxsignal.nxvalue
        return slab

    def get_xyz_slab(self, peak, width=10):
        xmin, xmax = max(peak.x-width, 0), min(peak.x+width+1, self.shape[1]-1)
        ymin, ymax = max(peak.y-width, 0), min(peak.y+width+1, self.shape[0]-1)
        zmin, zmax = max(peak.z-10, 0), min(peak.z+11, self.shape[0])
        return self.data[zmin:zmax, ymin:ymax, xmin:xmax]

    def write_xyz_peaks(self, peaks):
        extra_peaks = []
        for peak in [p for p in peaks if p.z >= 3600]:
            extra_peak = deepcopy(peak)
            extra_peak.z = peak.z - 3600
            extra_peaks.append(extra_peak)
        for peak in [p for p in peaks if p.z < 50]:
            extra_peak = deepcopy(peak)
            extra_peak.z = peak.z + 3600
            extra_peaks.append(extra_peak)
        peaks.extend(extra_peaks)            
        peaks = sorted(peaks, key=operator.attrgetter('z'))
        peak_array = np.array(list(zip(*[(peak.x, peak.y, peak.z, peak.pixel_count, 
                                          peak.H, peak.K, peak.L) for peak in peaks])))
        collection = NXcollection()
        collection['x'] = peak_array[0]
        collection['y'] = peak_array[1]
        collection['z'] = peak_array[2]
        collection['pixel_count'] = peak_array[3]
        collection['H'] = peak_array[4]
        collection['K'] = peak_array[5]
        collection['L'] = peak_array[6]
        with self.mask_root.nxfile:
            entry = self.mask_root['entry']
            if 'peaks_inferred' in entry:
                del entry['peaks_inferred']
            entry['peaks_inferred'] = collection

    def prepare_xyz_masks(self, peaks):
        with self.root.nxfile:
            masks = []
            peaks = sorted(peaks, key=operator.attrgetter('z'))
            for p in peaks:
                if p.pixel_count >= 0:
                    masks.extend(self.determine_mask(p))
        return masks

    def determine_mask(self, peak):
        slab = self.get_xyz_slab(peak)
        s = slab.nxsignal.nxdata
        slab_axis = slab.nxaxes[0].nxdata
        frames = np.array([np.average(np.ma.masked_where(s[i]<0,s[i]))*np.prod(s[i].shape) 
                           for i in range(s.shape[0])])
        masked_frames = np.ma.masked_where(frames<350000, frames)
        masked_peaks = []
        mask = masked_frames.mask
        if mask.size == 1 and mask == True:
            return []
        elif mask.size == 1 and mask == False:
            for f, z in zip(masked_frames, slab_axis):
                masked_peaks.append(NXPeak(peak.x, peak.y, z, 
                                           H=peak.H, K=peak.K, L=peak.L, 
                                           pixel_count=peak.pixel_count, 
                                           radius=mask_size(f)))
        else:
            for f, z in zip(masked_frames[~mask], slab_axis[~mask]):
                masked_peaks.append(NXPeak(peak.x, peak.y, z, 
                                           H=peak.H, K=peak.K, L=peak.L, 
                                           pixel_count=peak.pixel_count, 
                                           radius=mask_size(f)))
        return masked_peaks

    def write_xyz_masks(self, peaks):
        peaks = sorted(peaks, key=operator.attrgetter('z'))
        peak_array = np.array(list(zip(*[(peak.x, peak.y, peak.z, 
                                          peak.H, peak.K, peak.L,
                                          peak.radius, peak.pixel_count) 
                                         for peak in peaks])))
        collection = NXcollection()
        collection['x'] = peak_array[0]
        collection['y'] = peak_array[1]
        collection['z'] = peak_array[2]
        collection['H'] = peak_array[3]
        collection['K'] = peak_array[4]
        collection['L'] = peak_array[5]
        collection['radius'] = peak_array[6]
        collection['pixel_count'] = peak_array[7]
        with self.mask_root.nxfile:
            entry = self.mask_root['entry']
            if 'mask_xyz' in entry:
                del entry['mask_xyz']
            entry['mask_xyz'] = collection
    
    def write_xyz_edges(self):
        from .mask_functions import mask_edges
        edges_array = mask_edges(self.entry)
        collection = NXcollection()
        collection['x'] = edges_array[:,0]
        collection['y'] = edges_array[:,1]
        collection['z'] = edges_array[:,2]
        collection['radius'] = edges_array[:,3]
        collection['H'] = np.zeros(edges_array[:,0].shape[0])
        collection['K'] = np.zeros(edges_array[:,0].shape[0])
        collection['L'] = np.zeros(edges_array[:,0].shape[0])
        collection['pixel_count'] = np.zeros(edges_array[:,0].shape[0])
        with self.mask_root.nxfile:
            entry = self.mask_root['entry']
            if 'mask_xyz_edges' in entry:
                del entry['mask_xyz_edges']
            entry['mask_xyz_edges'] = collection

    def write_xyz_extras(self, peaks):
        peaks = sorted(peaks, key=operator.attrgetter('z'))
        peak_array = np.array(list(zip(*[(peak.x, peak.y, peak.z, 
                                          peak.H, peak.K, peak.L,
                                          peak.radius, peak.pixel_count) 
                                         for peak in peaks])))
        collection = NXcollection()
        collection['x'] = peak_array[0]
        collection['y'] = peak_array[1]
        collection['z'] = peak_array[2]
        collection['H'] = peak_array[3]
        collection['K'] = peak_array[4]
        collection['L'] = peak_array[5]
        collection['radius'] = peak_array[6]
        collection['pixel_count'] = peak_array[7]
        with self.mask_root.nxfile:
            entry = self.mask_root['entry']
            if 'mask_xyz_extras' in entry:
                del entry['mask_xyz_extras']
            entry['mask_xyz_extras'] = collection

    def read_xyz_peaks(self):
        return self.read_peaks('peaks_inferred')

    def read_xyz_masks(self):
        return self.read_peaks('mask_xyz')

    def read_xyz_extras(self):
        return self.read_peaks('mask_xyz_extras')

    def read_xyz_edges(self):
        return self.read_peaks('mask_xyz_edges')

    def read_peaks(self, peak_group):
        with self.mask_root.nxfile:
            if peak_group not in self.mask_root['entry']:
                return []
            else:
                pg = deepcopy(self.mask_root['entry'][peak_group])
        if 'intensity' not in pg:
            pg.intensity = np.zeros(len(pg.x))
        if 'radius' not in pg:
            pg.radius = np.zeros(len(pg.x))
        peaks = [NXPeak(*args) for args in 
                 list(zip(pg.x, pg.y, pg.z, pg.intensity, pg.pixel_count, 
                          pg.H, pg.K, pg.L, pg.radius))]
        return sorted(peaks, key=operator.attrgetter('z'))

    def nxmasked_transform(self):
        if self.not_complete('nxmasked_transform') and self.transform and self.mask:
            self.record_start('nxmasked_transform')
            if not self.all_complete('nxprepare_mask'):
                self.logger.info('Cannot perform masked transform until the 3D mask ' + 
                                 'is prepared for all entries')
                self.record_fail('nxmasked_transform')
                return
            self.logger.info("Completing and writing 3D mask")
            self.complete_xyz_mask()
            self.logger.info("3D mask written")
            cctw_command = self.prepare_transform(mask=True)
            if cctw_command:
                self.logger.info('Masked transform launched')
                tic = timeit.default_timer()
                with self.field.nxfile:
                    with NXLock(self.masked_transform_file):
                        process = subprocess.run(cctw_command, shell=True,
                                                 stdout=subprocess.PIPE,
                                                 stderr=subprocess.PIPE)
                toc = timeit.default_timer()
                if process.returncode == 0:
                    self.logger.info('Masked transform completed (%g seconds)'
                                     % (toc-tic))
                    self.record('nxmasked_transform', mask=self.mask_file,
                                norm=self.norm,
                                command=cctw_command,
                                output=process.stdout.decode(),
                                errors=process.stderr.decode())
                    self.record_end('nxmasked_transform')
                else:
                    self.logger.info(
                        'Masked transform completed - errors reported (%g seconds)'
                        % (toc-tic))
                    self.record_fail('nxmasked_transform')
            else:
                self.logger.info('CCTW command invalid')
        elif self.transform and self.mask:
            self.logger.info('Masked data already transformed')

    def complete_xyz_mask(self):
        with self.mask_root.nxfile:
            if 'mask' in self.mask_root['entry']:
                if self.overwrite:
                    del self.mask_root['entry/mask']
                else:
                    self.logger.info('Mask already completed')
                    return
        peaks = {}
        masks = {}
        reduce = {}
        for entry in self.entries:
            if entry == self.entry_name:
                reduce[entry] = self
            else:
                reduce[entry] = NXReduce(self.root[entry])
            peaks[entry] = reduce[entry].read_xyz_peaks()
            masks[entry] = reduce[entry].read_xyz_masks()
        extra_masks = []
        for p in [p for p in peaks[self.entry_name] if p.pixel_count < 0]:
            radius = 0
            width = 0
            for e in [e for e in self.entries if e is not self.entry_name]:
                other_masks = [om for om in masks[e] if om.H == p.H and
                                                        om.K == p.K and 
                                                        om.L == p.L]
                for om in other_masks:
                    radius = max(radius, om.radius)
                width = max(width, len(other_masks))
            if radius > 0:
                radius += 20.
                width = int((width + 2) / 2)
                p.z = int(np.rint(p.z))
                for z in [z for z in range(p.z-width, p.z+width+1)]:
                    extra_masks.append(NXPeak(p.x, p.y, z, 
                                              H=p.H, K=p.K, L=p.L, 
                                              pixel_count=p.pixel_count,
                                              radius=radius))
        if extra_masks:
            self.write_xyz_extras(extra_masks)
        self.write_mask()

    def write_mask(self, peaks=None):
        with self.mask_root.nxfile:
            if peaks is None:
                peaks = self.read_xyz_masks()
                peaks.extend(self.read_xyz_extras())
                peaks.extend(self.read_xyz_edges())
            peaks = sorted(peaks, key=operator.attrgetter('z'))
            entry = self.mask_root['entry']
            entry['mask'] = NXfield(shape=self.shape, dtype=np.int8, fillvalue=0)
            mask = entry['mask']
            x, y = np.arange(self.shape[2]), np.arange(self.shape[1])
            frames = self.shape[0]
            chunk_size = mask.chunks[0]
            for frame in range(0, frames, chunk_size):
                mask_chunk = np.zeros(shape=(chunk_size, self.shape[1], self.shape[2]),
                                      dtype=np.int8)
                for peak in [p for p in peaks if p.z >= frame and p.z < frame+chunk_size]:
                    xp, yp, zp, radius = int(peak.x), int(peak.y), int(peak.z), peak.radius
                    inside = np.array(((x[np.newaxis,:]-xp)**2 + (y[:,np.newaxis]-yp)**2 
                                        < radius**2), dtype=np.int8)
                    mask_chunk[zp-frame] = mask_chunk[zp-frame] | inside
                try:
                    mask[frame:frame+chunk_size] = (mask[frame:frame+chunk_size].nxvalue | 
                                                    mask_chunk)
                except ValueError as error:
                    i, j, k= frame, frames, frames-frame
                    mask[i:j] = mask[i:j].nxvalue | mask_chunk[:k]

    def nxsum(self, scan_list, update=False):
        if os.path.exists(self.data_file) and not (self.overwrite or update):
            self.logger.info('Data already summed')
        elif not os.path.exists(self.directory):
            self.logger.info('Sum directory not created')
        else:
            self.record_start('nxsum')
            self.logger.info('Sum files launched')
            tic = timeit.default_timer()
            if not self.check_files(scan_list):
                self.record_fail('nxsum')
            else:
                self.logger.info('All files and metadata have been checked')
                if not update:
                    self.sum_files(scan_list)
                self.sum_monitors(scan_list)
                toc = timeit.default_timer()
                self.logger.info('Sum completed (%g seconds)' % (toc-tic))
                self.record('nxsum', scans=','.join(scan_list))
                self.record_end('nxsum')

    def check_sum_files(self, scan_list):
        status = True
        for i, scan in enumerate(scan_list):
            reduce = NXReduce(self.entry_name, 
                              os.path.join(self.base_directory, scan))
            if not os.path.exists(reduce.data_file):
                self.logger.info("'%s' does not exist" % reduce.data_file)
                status = False
            elif 'monitor1' not in reduce.entry:
                self.logger.info("Monitor1 not present in %s" 
                                 % reduce.wrapper_file)
                status = False
        return status

    def sum_files(self, scan_list):
    
        nframes = 3650
        chunk_size = 500
        for i, scan in enumerate(scan_list):
            reduce = NXReduce(self.entry_name, 
                              os.path.join(self.base_directory, scan))
            self.logger.info("Summing %s in '%s'" % (self.entry_name,
                                                     reduce.data_file))
            if i == 0:
                shutil.copyfile(reduce.data_file, self.data_file)
                new_file = h5.File(self.data_file, 'r+')
                new_field = new_file[self.path]
            else:
                scan_file = h5.File(reduce.data_file, 'r')
                scan_field = scan_file[self.path]
                for i in range(0, nframes, chunk_size):
                    new_slab = new_field[i:i+chunk_size,:,:]
                    scan_slab = scan_field[i:i+chunk_size,:,:]
                    new_field[i:i+chunk_size,:,:] = new_slab + scan_slab
        self.logger.info("Raw data files summed")

    def sum_monitors(self, scan_list, update=False):

        for i, scan in enumerate(scan_list):
            reduce = NXReduce(self.entry_name, 
                              os.path.join(self.base_directory, scan))
            self.logger.info("Adding %s monitors in '%s'" % (self.entry_name,
                                                             reduce.wrapper_file))
            if i == 0:
                monitor1 = reduce.entry['monitor1/MCS1'].nxvalue
                monitor2 = reduce.entry['monitor2/MCS2'].nxvalue
                if 'monitor_weight' not in reduce.entry['data']:
                    reduce.get_normalization()
                monitor_weight = reduce.entry['data/monitor_weight'].nxvalue
                if os.path.exists(reduce.mask_file):
                    shutil.copyfile(reduce.mask_file, self.mask_file)
            else:
                monitor1 += reduce.entry['monitor1/MCS1'].nxvalue
                monitor2 += reduce.entry['monitor2/MCS2'].nxvalue
                if 'monitor_weight' not in reduce.entry['data']:
                    reduce.get_normalization()
                monitor_weight += reduce.entry['data/monitor_weight'].nxvalue
        with self.root.nxfile:
            self.entry['monitor1/MCS1'] = monitor1
            self.entry['monitor2/MCS2'] = monitor2
            self.entry['data/monitor_weight'] = monitor_weight

    def nxreduce(self):
        self.nxlink()
        self.nxmax()
        self.nxfind()
        self.nxcopy()
        if self.complete('nxfind') and self.complete('nxcopy'):
            self.nxrefine()
        if self.complete('nxrefine'):
            self.nxprepare()
            if self.mask:
                self.nxmasked_transform()
            else:
                self.nxtransform()
        elif self.transform:
            self.logger.info('Orientation has not been refined')
            self.record_fail('nxtransform')
            self.record_fail('nxmasked_transform')

    def command(self, parent=False):
        switches = ['-d %s' % self.directory, '-e %s' % self.entry_name]
        if parent:
            command = 'nxparent '
            if self.first is not None:
                switches.append('-f %s' % self.first)
            if self.last is not None:
                switches.append('-l %s' % self.last)
            if self.threshold is not None:
                switches.append('-t %s' % self.threshold)
            if self.norm is not None:
                switches.append('-n %s' % self.norm)
            if self.radius is not None:
                switches.append('-r %s' % self.radius)
            switches.append('-s')
        else:
            command = 'nxreduce '
            if self.link:
                switches.append('-l')
            if self.maxcount:
                switches.append('-m')
            if self.find:
                switches.append('-f')
            if self.copy:
                switches.append('-c')
            if self.refine:
                switches.append('-r')
            if self.prepare:
                switches.append('-p')
            if self.transform:
                switches.append('-t')
            if self.mask:
                switches.append('-M')
            if len(switches) == 2:
                return None
        if self.overwrite:
            switches.append('-o')

        return command+' '.join(switches)

    def queue(self, parent=False):
        """ Add tasks to the server's fifo, and log this in the database """
        command = self.command(parent)
        if command:
            self.server.add_task(command)
            if self.link:
                self.db.queue_task(self.wrapper_file, 'nxlink', self.entry_name)
            if self.maxcount:
                self.db.queue_task(self.wrapper_file, 'nxmax', self.entry_name)
            if self.find:
                self.db.queue_task(self.wrapper_file, 'nxfind', self.entry_name)
            if self.copy:
                self.db.queue_task(self.wrapper_file, 'nxcopy', self.entry_name)
            if self.refine:
                self.db.queue_task(self.wrapper_file, 'nxrefine', self.entry_name)
            if self.prepare:
                self.db.queue_task(self.wrapper_file, 'nxprepare', self.entry_name)
            if self.transform:
                if self.mask:
                    self.db.queue_task(self.wrapper_file, 'nxmasked_transform', 
                                       self.entry_name)
                    self.db.queue_task(self.wrapper_file, 'nxmasked_combine', 
                                       'entry')
                else:
                    self.db.queue_task(self.wrapper_file, 'nxtransform', 
                                       self.entry_name)
                    self.db.queue_task(self.wrapper_file, 'nxcombine', 'entry')


class NXMultiReduce(NXReduce):

    def __init__(self, directory, entries=None, 
                 combine=False, pdf=False, mask=False, laue='-1', radius=None,
                 overwrite=False):
        if isinstance(directory, NXroot):
            entry = directory['entry']
        else:
            entry = 'entry'
        super(NXMultiReduce, self).__init__(entry=entry, directory=directory,
                                            entries=entries, overwrite=overwrite)
        self.refine = NXRefine(self.root[self.entries[0]])
        if laue:
            if laue in self.refine.laue_groups:
                self.refine.laue_group = laue
            else:
                raise NeXusError('Invalid Laue group specified')
        self.combine = combine
        self.pdf = pdf
        self.mask = mask
        if self.mask:
            self.transform_file = os.path.join(self.directory,
                                               'masked_transform.nxs')
            self.symm_file = os.path.join(self.directory, 
                                          'symm_masked_transform.nxs')
            self.symm_transform = 'symm_masked_transform'
            self.pdf_file = os.path.join(self.directory, 'masked_pdf.nxs')
        else:
            self.transform_file = os.path.join(self.directory, 'transform.nxs')
            self.symm_file = os.path.join(self.directory, 'symm_transform.nxs')
            self.symm_transform = 'symm_transform'
            self.pdf_file = os.path.join(self.directory, 'pdf.nxs')
        self.total_pdf_file = os.path.join(self.directory, 'total_pdf.nxs')
        self.julia = None

    def __repr__(self):
        return "NXMultiReduce('{}_{}')".format(self.sample, self.scan)

    def complete(self, program):
        complete = True
        if program == 'nxcombine' or program == 'nxmasked_combine':
            if program not in self.entry:
                complete = False
        elif program == 'nxtransform' or program == 'nxmasked_transform':
            for entry in self.entries:
                if program not in self.root[entry]:
                    complete = False
            if not complete and program == 'nxmasked_transform':
                complete = True
                for entry in self.entries:
                    if 'nxmask' not in self.root[entry]:
                        complete = False                        
        return complete

    def nxcombine(self):
        if self.mask:
            task = 'nxmasked_combine'
            title = 'Masked combine'
        else:
            task = 'nxcombine'
            title = 'Combine'
        if self.not_complete(task) and self.combine:
            if self.mask:
                if not self.complete('nxmasked_transform'):
                    self.logger.info('Cannot combine until masked transforms complete')
                    return
            elif not self.complete('nxtransform'):
                self.logger.info('Cannot combine until transforms complete')
                return
            self.record_start(task)
            cctw_command = self.prepare_combine()
            if cctw_command:
                if self.mask:
                    self.logger.info('Combining masked transforms (%s)'
                                     % ', '.join(self.entries))
                    transform_path = 'masked_transform/data'
                else:
                    self.logger.info('Combining transforms (%s)'
                                     % ', '.join(self.entries))
                    transform_path = 'transform/data'
                tic = timeit.default_timer()
                with NXLock(self.transform_file):
                    if os.path.exists(self.transform_file):
                        os.remove(self.transform_file)
                    data_lock = {}
                    for entry in self.entries:
                        data_lock[entry] = NXLock(
                                    self.root[entry][transform_path].nxfilename)
                        data_lock[entry].acquire()
                    process = subprocess.run(cctw_command, shell=True,
                                             stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE)
                    for entry in self.entries:
                        data_lock[entry].release()
                toc = timeit.default_timer()
                if process.returncode == 0:
                    self.logger.info('%s (%s) completed (%g seconds)'
                        % (title, ', '.join(self.entries), toc-tic))
                    self.record(task, command=cctw_command,
                                output=process.stdout.decode(),
                                errors=process.stderr.decode())
                    self.record_end(task)
                else:
                    self.logger.info(
                        '%s (%s) completed - errors reported (%g seconds)'
                        % (title, ', '.join(self.entries), toc-tic))
                    self.record_fail('nxcombine')
            else:
                self.logger.info('CCTW command invalid')
        else:
            self.logger.info('Data already combined')

    def prepare_combine(self):
        if self.mask:
            transform = 'masked_transform'
        else:
            transform = 'transform'
        try:
            with self.root.nxfile:
                entry = self.entries[0]
                Qh, Qk, Ql = (self.root[entry][transform]['Qh'],
                              self.root[entry][transform]['Qk'],
                              self.root[entry][transform]['Ql'])
                data = NXlink('/entry/data/v',
                              file=os.path.join(self.scan, transform+'.nxs'), 
                              name='data')
                if transform in self.entry:
                    del self.entry[transform]
                self.entry[transform] = NXdata(data, [Ql,Qk,Qh])
                self.entry[transform].attrs['angles'] = (
                    self.root[entry][transform].attrs['angles'])
                self.entry[transform].set_default(over=True)
        except Exception as error:
            self.logger.info('Unable to initialize transform group')
            self.logger.info(str(error))
            return None
        input = ' '.join([os.path.join(self.directory,
                                       f'{entry}_{transform}.nxs\#/entry/data')
                          for entry in self.entries])
        output = os.path.join(self.directory, transform+'.nxs\#/entry/data/v')
        return 'cctw merge %s -o %s' % (input, output)

    def nxpdf(self):
        if self.mask:
            task = 'nxmasked_pdf'
            title = 'Masked PDF'
        else:
            task = 'nxpdf'
            title = 'PDF'
        if self.not_complete(task) and self.pdf:
            if self.mask:
                if not self.complete('nxmasked_combine'):
                    self.logger.info('Cannot calculate PDF until the masked transforms are combined')
                    return
            elif not self.complete('nxcombine'):
                self.logger.info('Cannot calculate PDF until the transforms are combined')
                return
            elif self.refine.laue_group not in self.refine.laue_groups:
                self.logger.info('Need to define a valid Laue group before PDF calculation')
                return
            self.record_start('nxpdf')
            self.set_memory()
            self.symmetrize_transform()
            self.total_pdf()
            self.punch_holes()
            self.punch_and_fill()
            self.delta_pdf()
            self.record(task, laue=self.refine.laue_group, radius=self.radius)
            self.record_end(task)
        else:
            self.logger.info('PDF already calculated')

    def set_memory(self):
        if self.mask:
            transform = 'masked_transform'
        else:
            transform = 'transform'
        signal = self.entry[transform].nxsignal
        total_size = np.prod(signal.shape) * np.dtype(signal.dtype).itemsize / 1e6
        if total_size > nxgetmemory():
            nxsetmemory(total_size + 1000)

    def symmetrize_transform(self):
        if self.mask:
            transform = 'masked_transform'
        else:
            transform = 'transform'
        if os.path.exists(self.symm_file):
            if self.overwrite:
                os.remove(self.symm_file)
            else:
                self.logger.info('Symmetrized data already exists')
                return
        self.logger.info('Transform being symmetrized')
        tic = timeit.default_timer()
        for i, entry in enumerate(self.entries):
            r = NXReduce(self.root[entry])
            if i == 0:
                summed_data = r.entry[transform].nxsignal.nxvalue
                summed_weights = r.entry[transform].nxweights.nxvalue
                summed_axes = r.entry[transform].nxaxes
            else:
                summed_data += r.entry[transform].nxsignal.nxvalue
                summed_weights += r.entry[transform].nxweights.nxvalue
        summed_transforms = NXdata(NXfield(summed_data, name='data'),
                                   summed_axes, weights=summed_weights)
        symmetry = NXSymmetry(summed_transforms, 
                              laue_group=self.refine.laue_group)
        root = nxload(self.symm_file, 'a')
        root['entry'] = NXentry()
        root['entry/data'] = symmetry.symmetrize()
        root['entry/data'].nxweights = self.fft_weights(root['entry/data'].shape)
        if self.symm_transform in self.entry:
            del self.entry[self.symm_transform]
        symm_data = NXlink('/entry/data/data', file=self.symm_file, name='data')
        self.entry[self.symm_transform] = NXdata(symm_data, 
                                                 self.entry[transform].nxaxes)
        self.entry[self.symm_transform]['data_weights'] = NXlink(
                                '/entry/data/data_weights', file=self.symm_file)
        self.logger.info("'{}' added to entry".format(self.symm_transform))
        toc = timeit.default_timer()
        self.logger.info('Symmetrization completed (%g seconds)' % (toc-tic))

    def fft_weights(self, shape, alpha=0.5):
        from scipy.signal import tukey
        x = tukey(shape[2], alpha=alpha)
        y = tukey(shape[1], alpha=alpha)
        z = tukey(shape[0], alpha=alpha)
        return np.einsum('i,j,k->ijk', 1.0/np.where(z>0, z, z[1]/2), 
                                       1.0/np.where(y>0, y, y[1]/2),
                                       1.0/np.where(x>0, x, x[1]/2))

    def fft_taper(self, shape, alpha=0.5):
        from scipy.signal import tukey
        x = tukey(shape[2], alpha=alpha)
        y = tukey(shape[1], alpha=alpha)
        z = tukey(shape[0], alpha=alpha)
        return np.einsum('i,j,k->ijk', z, y, x)

    def total_pdf(self):
        self.logger.info('Calculating total PDF')
        if os.path.exists(self.total_pdf_file):
            if self.overwrite:
                os.remove(self.total_pdf_file)
            else:
                self.logger.info('Total PDF file already exists')
                return
        tic = timeit.default_timer()
        symm_data = self.entry[self.symm_transform].nxsignal[:-1,:-1,:-1].nxvalue
        symm_data *= self.fft_taper(symm_data.shape)
        fft = np.real(np.fft.fftshift(np.fft.fftn(np.fft.fftshift(symm_data))))
        fft *= (1.0 / np.prod(fft.shape))
        
        root = nxload(self.total_pdf_file, 'a')
        root['entry'] = NXentry()
        root['entry/pdf'] = NXdata(NXfield(fft, name='pdf'))

        if 'total_pdf' in self.entry:
            del self.entry['total_pdf']
        pdf = NXlink('/entry/pdf/pdf', file=self.total_pdf_file, name='pdf')
        
        dl, dk, dh = [(ax[1]-ax[0]).nxvalue 
                      for ax in self.entry[self.symm_transform].nxaxes]
        x = NXfield(np.fft.fftshift(np.fft.fftfreq(fft.shape[2], dh)), name='x',
                    scaling_factor=self.refine.a)
        y = NXfield(np.fft.fftshift(np.fft.fftfreq(fft.shape[1], dk)), name='y',
                    scaling_factor=self.refine.b)
        z = NXfield(np.fft.fftshift(np.fft.fftfreq(fft.shape[0], dl)), name='z',
                    scaling_factor=self.refine.c)
        self.entry['total_pdf'] = NXdata(pdf, (z, y, x))
        self.entry['total_pdf'].attrs['angles'] = self.refine.lattice_parameters[3:]
        self.logger.info("'{}' added to entry".format('total_pdf'))
        toc = timeit.default_timer()
        self.logger.info('Total PDF calculated (%g seconds)' % (toc-tic))

    def hole_mask(self):
        symm_group = self.entry[self.symm_transform]
        Qh, Qk, Ql = (symm_group['Qh'], symm_group['Qk'], symm_group['Ql'])
        dl, dk, dh = [(ax[1]-ax[0]).nxvalue for ax in symm_group.nxaxes]
        dhp = np.rint(self.radius / (dh * self.refine.astar))
        dkp = np.rint(self.radius / (dk * self.refine.bstar))
        dlp = np.rint(self.radius / (dl * self.refine.cstar))
        ml, mk, mh = np.ogrid[0:4*int(dlp)+1, 0:4*int(dkp)+1, 0:4*int(dhp)+1]
        mask = ((((ml-2*dlp)/dlp)**2+((mk-2*dkp)/dkp)**2+((mh-2*dhp)/dhp)**2) <= 1)
        mask_array = np.where(mask==0, 0, 1)
        mask_indices = [list(idx) for idx in list(np.argwhere(mask==1))]
        return mask_array, mask_indices

    @property
    def indices(self):
        self.refine.polar_max = self.refine.two_theta_max()
        if self.refine.laue_group in ['-3', '-3m', '6/m', '6/mmm']:
            _indices = []
            for idx in self.refine.indices:
                _indices += self.refine.indices_hkl(*idx)
            return _indices
        else:
            return self.refine.indices

    def symmetrize(self, data):
        if self.refine.laue_group in ['-3', '-3m', '6/m', '6/mmm']:
            return data
        else:
            symmetry = NXSymmetry(data, laue_group=self.refine.laue_group)
            return symmetry.symmetrize()

    def punch_holes(self):
        self.logger.info('Punching holes')
        if (self.symm_transform in self.entry and
            'punched_data' in self.entry[self.symm_transform]):
            if self.overwrite:
                del self.entry[self.symm_transform]['punched_data']
            else:
                self.logger.info('Punched holes already exists')
                return
        tic = timeit.default_timer()
        symm_group = self.entry[self.symm_transform]
        Qh, Qk, Ql = (symm_group['Qh'], symm_group['Qk'], symm_group['Ql'])

        root = nxload(self.symm_file, 'rw')
        entry = root['entry']
        
        mask, _ = self.hole_mask()
        ml = int((mask.shape[0]-1)/2)
        mk = int((mask.shape[1]-1)/2)
        mh = int((mask.shape[2]-1)/2)
        symm_data = entry['data/data'].nxdata
        punch_data = np.zeros(shape=symm_data.shape, dtype=symm_data.dtype)
        for h, k, l in self.indices:
            try:
                ih = np.argwhere(np.isclose(Qh, h))[0][0]
                ik = np.argwhere(np.isclose(Qk, k))[0][0]
                il = np.argwhere(np.isclose(Ql, l))[0][0]
                lslice = slice(il-ml, il+ml+1)
                kslice = slice(ik-mk, ik+mk+1)
                hslice = slice(ih-mh, ih+mh+1)
                punch_data[(lslice, kslice, hslice)] = mask
            except Exception as error:
                pass
        punch_data = self.symmetrize(punch_data)
        changed_idx = np.where(punch_data>0)
        symm_data[changed_idx] *= 0

        if 'punch' in entry['data']:
            del entry['data/punch']
        entry['data/punch'] = symm_data
        self.entry[self.symm_transform]['punched_data'] = NXlink(
                                    '/entry/data/punch', file=self.symm_file)
        self.logger.info("'punched_data' added to '{}'".format(
                                                          self.symm_transform))

        toc = timeit.default_timer()
        self.logger.info('Punches completed (%g seconds)' % (toc-tic))

    def init_julia(self):
        if self.julia is None:
            try:
                from julia import Julia
                self.julia = Julia(compiled_modules=False)
                import pkg_resources

                from julia import Main
                Main.include(pkg_resources.resource_filename('nxrefine', 
                                            'julia/LaplaceInterpolation.jl'))
            except Exception as error:
                raise NeXusError(str(error))

    def punch_and_fill(self):
        self.logger.info('Performing punch-and-fill')
        if (self.symm_transform in self.entry and
            'filled_data' in self.entry[self.symm_transform]):
            if self.overwrite:
                del self.entry[self.symm_transform]['filled_data']
            else:
                self.logger.info('Data already punched-and-filled')
                return

        self.init_julia()
        from julia import Main
        LaplaceInterpolation = Main.LaplaceInterpolation

        m = 1
        epsilon = 0
        tic = timeit.default_timer()
        symm_group = self.entry[self.symm_transform]
        Qh, Qk, Ql = (symm_group['Qh'], symm_group['Qk'], symm_group['Ql'])

        root = nxload(self.symm_file, 'rw')
        entry = root['entry']
        
        mask, mask_indices = self.hole_mask()
        idx = [Main.CartesianIndex(int(i[0]+1),int(i[1]+1),int(i[2]+1)) 
               for i in mask_indices]
        ml = int((mask.shape[0]-1)/2)
        mk = int((mask.shape[1]-1)/2)
        mh = int((mask.shape[2]-1)/2)
        symm_data = entry['data/data'].nxdata
        fill_data = np.zeros(shape=symm_data.shape, dtype=symm_data.dtype)
        self.refine.polar_max = self.refine.two_theta_max()
        for h, k, l in self.indices:
            try:
                ih = np.argwhere(np.isclose(Qh, h))[0][0]
                ik = np.argwhere(np.isclose(Qk, k))[0][0]
                il = np.argwhere(np.isclose(Ql, l))[0][0]
                lslice = slice(il-ml, il+ml+1)
                kslice = slice(ik-mk, ik+mk+1)
                hslice = slice(ih-mh, ih+mh+1)
                v = symm_data[(lslice, kslice, hslice)]
                if v.max() > 0.0:
                    w = LaplaceInterpolation.matern_3d_grid(v, idx)
                    fill_data[(lslice, kslice, hslice)] = w
            except Exception as error:
                pass
        fill_data = self.symmetrize(fill_data)
        changed_idx = np.where(fill_data>0)
        symm_data[changed_idx] = fill_data[changed_idx]

        if 'fill' in entry['data']:
            del entry['data/fill']        
        entry['data/fill'] = symm_data
        self.entry[self.symm_transform]['filled_data'] = NXlink(
                                    '/entry/data/fill', file=self.symm_file)
        self.logger.info("'filled_data' added to '{}'".format(
                                                          self.symm_transform))

        toc = timeit.default_timer()
        self.logger.info('Punch-and-fill completed (%g seconds)' % (toc-tic))

    def delta_pdf(self):
        self.logger.info('Calculating Delta-PDF')
        if os.path.exists(self.pdf_file):
            if self.overwrite:
                os.remove(self.pdf_file)
            else:
                self.logger.info('Delta-PDF file already exists')
                return
        tic = timeit.default_timer()
        symm_data = self.entry[self.symm_transform]['filled_data'][:-1,:-1,:-1].nxvalue
        symm_data *= self.fft_taper(symm_data.shape)
        fft = np.real(np.fft.fftshift(np.fft.fftn(np.fft.fftshift(symm_data))))
        fft *= (1.0 / np.prod(fft.shape))
        
        root = nxload(self.pdf_file, 'a')
        root['entry'] = NXentry()
        root['entry/pdf'] = NXdata(NXfield(fft, name='pdf'))

        if 'pdf' in self.entry:
            del self.entry['pdf']
        pdf = NXlink('/entry/pdf/pdf', file=self.pdf_file, name='pdf')
        
        dl, dk, dh = [(ax[1]-ax[0]).nxvalue 
                      for ax in self.entry[self.symm_transform].nxaxes]
        x = NXfield(np.fft.fftshift(np.fft.fftfreq(fft.shape[2], dh)), name='x',
                    scaling_factor=self.refine.a)
        y = NXfield(np.fft.fftshift(np.fft.fftfreq(fft.shape[1], dk)), name='y',
                    scaling_factor=self.refine.b)
        z = NXfield(np.fft.fftshift(np.fft.fftfreq(fft.shape[0], dl)), name='z',
                    scaling_factor=self.refine.c)
        self.entry['pdf'] = NXdata(pdf, (z, y, x))
        self.entry['pdf'].attrs['angles'] = self.refine.lattice_parameters[3:]
        self.logger.info("'{}' added to entry".format('pdf'))
        toc = timeit.default_timer()
        self.logger.info('Delta-PDF calculated (%g seconds)' % (toc-tic))

    def nxsum(self, scan_list):
        if not os.path.exists(self.wrapper_file) or self.overwrite:
            for e in self.entries:
                reduce = NXReduce(self.root[e])
                status = reduce.check_sum_files(scan_list)
                if not status:
                    return status
            if not os.path.exists(self.directory):
                os.mkdir(self.directory)
            self.logger.info('Creating sum file')
            self.configure_sum_file(scan_list)
            self.logger.info('Sum file created')
        else:
            self.logger.info('Sum file already exists')

    def configure_sum_file(self, scan_list):
        shutil.copyfile(os.path.join(self.base_directory, 
                                     self.sample+'_'+scan_list[0]+'.nxs'),
                        self.wrapper_file)
        with self.root.nxfile:
            if 'nxcombine' in self.root['entry']:
                del self.root['entry/nxcombine']
            if 'nxmasked_combine' in self.root['entry']:
                del self.root['entry/nxmasked_combine']
            for e in self.entries:
                entry = self.root[e]
                if 'data' in entry:
                    if 'data' in entry['data']:
                        del entry['data/data']
                    entry['data/data'] = NXlink('/entry/data/data', 
                            os.path.join(self.directory, entry.nxname+'.h5'))
                    if 'data_mask' in entry['data']:
                        mask_file = os.path.join(self.directory, 
                                                 entry.nxname+'_mask.nxs')
                        del entry['data/data_mask']
                        entry['data/data_mask'] = NXlink('/entry/mask', 
                                                         mask_file)
                if 'nxtransform' in entry:
                    del entry['nxtransform']
                if 'nxmasked_transform' in entry:
                    del entry['nxmasked_transform']
        self.db.update_file(self.wrapper_file)

    def nxreduce(self):
        self.nxcombine()
        self.nxpdf()

    def command(self):
        command = 'nxreduce '
        switches = ['-d %s' %  self.directory]
        if self.combine:
            switches.append('--combine')
        if self.pdf:
            switches.append('--pdf')
        if self.mask:
            switches.append('--mask')
        if self.overwrite:
            switches.append('--overwrite')
        return command+' '.join(switches)

    def queue(self):
        if self.server is None:
            raise NeXusError("NXServer not running")
        self.server.add_task(self.command())
        
        if self.combine:
            if self.mask:
                self.db.queue_task(self.wrapper_file, 'nxmasked_combine', 'entry')
            else:
                self.db.queue_task(self.wrapper_file, 'nxcombine', 'entry')
        if self.pdf:
            if self.mask:
                self.db.queue_task(self.wrapper_file, 'nxmasked_pdf', 'entry')
            else:
                self.db.queue_task(self.wrapper_file, 'nxpdf', 'entry')


class NXBlob(object):

    def __init__(self, np, average, x, y, z, sigx, sigy, covxy, threshold,
                 pixel_tolerance, frame_tolerance):
        self.np = np
        self.average = average
        self.intensity = np * average
        self.x = x
        self.y = y
        self.z = z
        self.sigx = sigx
        self.sigy = sigy
        self.covxy = covxy
        self.threshold = threshold
        self.peaks = [self]
        self.pixel_tolerance = pixel_tolerance**2
        self.frame_tolerance = frame_tolerance
        self.combined = False

    def __str__(self):
        return "NXBlob x=%f y=%f z=%f np=%i avg=%f" % (self.x, self.y, self.z, self.np, self.average)

    def __repr__(self):
        return "NXBlob x=%f y=%f z=%f np=%i avg=%f" % (self.x, self.y, self.z, self.np, self.average)

    def __lt__(self, other):
        return self.z < other.z

    def __eq__(self, other):
        if abs(self.z - other.z) <= self.frame_tolerance:
            if (self.x - other.x)**2 + (self.y - other.y)**2 <= self.pixel_tolerance:
                return True
            else:
                return False
        else:
            return False

    def __ne__(self, other):
        if abs(self.z - other.z) > self.frame_tolerance:
            if (self.x - other.x)**2 + (self.y - other.y)**2 > self.pixel_tolerance:
                return True
            else:
                return False
        else:
            return False

    def combine(self, other):
        self.peaks.extend(other.peaks)
        self.combined = True
        other.combined = False

    def merge(self):
        np = sum([p.np for p in self.peaks])
        intensity = sum([p.intensity for p in self.peaks])
        self.x = sum([p.x * p.intensity for p in self.peaks]) / intensity
        self.y = sum([p.y * p.intensity for p in self.peaks]) /intensity
        self.z = sum([p.z * p.intensity for p in self.peaks]) / intensity
        self.sigx = sum([p.sigx * p.intensity for p in self.peaks]) / intensity
        self.sigy = sum([p.sigy * p.intensity for p in self.peaks]) / intensity
        self.covxy = sum([p.covxy * p.intensity for p in self.peaks]) / intensity
        self.np = np
        self.intensity = intensity
        self.average = self.intensity / self.np

    def isvalid(self, mask):
        if mask is not None:
            clip = mask[int(self.y),int(self.x)]
            if clip:
                return False
        if np.isclose(self.average, 0.0) or np.isnan(self.average) or self.np < 5:
            return False
        else:
            return True

def mask_size(intensity):
    a = 1.3858
    b = 0.330556764635949
    c = -134.21 + 40 #radius_add
    try:
        if len(intensity) > 1:
            pass
    except Exception:
        pass
    if (intensity<1):
        return 0
    else:
        radius = np.real(c + a * (intensity**b))
        return max(1,np.int(radius))

