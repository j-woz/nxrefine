import logging
import logging.handlers
import os
import platform
import sys
import time
import timeit
import numpy as np

from nexusformat.nexus import *

from nexpy.gui.pyqt import QtCore

from .nxrefine import NXRefine
from .nxserver import NXServer
from . import blobcorrector, __version__
from .connectedpixels import blob_moments
from .labelimage import labelimage, flip1


class LockException(Exception):
    LOCK_FAILED = 1


class Lock(object):

    def __init__(self, filename, timeout=30, check_interval=1):
        self.filename = os.path.realpath(filename)
        self.lock_file = self.filename+'.lock'
        self.timeout = timeout
        self.check_interval = check_interval
    
    def acquire(self, timeout=None, check_interval=None):
        if timeout is None:
            timeout = self.timeout
        if timeout is None:
            timeout = 0

        if check_interval is None:
            check_interval = self.check_interval

        def _get_lock():
            if os.path.exists(self.lock_file):
                raise LockException("'%s' already locked" % self.filename)
            else:
                open(self.lock_file, 'w').write("%s" % os.getpid())
        try:
            _get_lock()
        except LockException as exception:
            timeoutend = timeit.default_timer() + timeout
            while timeoutend > timeit.default_timer():
                time.sleep(check_interval)
                try:
                    _get_lock()
                    break
                except LockException:
                    pass
            else:
                raise LockException("'%s' already locked" % self.filename)

    def release(self):
        if os.path.exists(self.lock_file):
            os.remove(self.lock_file)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, type_, value, tb):
        self.release()

    def __delete__(self, instance):
        instance.release()


class NXReduce(QtCore.QObject):

    def __init__(self, entry='f1', directory=None, data='data/data', parent=None,
                 extension='.h5', path='/entry/data/data',
                 threshold=None, first=None, last=None, radius=200, width=3,
                 refine=False, transform=False, mask3D=False, 
                 overwrite=False, gui=False):

        super(NXReduce, self).__init__()

        if isinstance(entry, NXentry):
            self._entry = entry.nxname
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
        elif directory is None:
            raise NeXusError('Directory not specified')
        else:
            self.directory = directory.rstrip('/')
            self.root_directory = os.path.realpath(
                                    os.path.dirname(
                                      os.path.dirname(
                                        os.path.dirname(self.directory))))
            self.sample = os.path.basename(
                            os.path.dirname(
                              os.path.dirname(self.directory)))   
            self.label = os.path.basename(os.path.dirname(self.directory))
            self.scan = os.path.basename(self.directory)
            self.wrapper_file = os.path.join(self.root_directory, 
                                             self.sample, self.label, 
                                             '%s_%s.nxs' % 
                                             (self.sample, self.scan))
            self._entry = entry
        self.base_directory = os.path.dirname(self.wrapper_file)
        self.task_directory = os.path.join(self.root_directory, 'tasks')
        if 'tasks' not in os.listdir(self.root_directory):
            os.mkdir(self.task_directory)
        self.log_file = os.path.join(self.task_directory, 'nxlogger.log')
        
        self._root = None 
        self._data = data
        self._field = None
        self._mask = None
        self._parent = parent
        
        self.extension = extension
        self.path = path

        self.threshold = threshold
        self._maximum = None
        self.first = first
        self.last = last
        self.radius = 200
        self.width = 3
        self.refine = refine
        self.transform = transform
        self.mask3D = mask3D
        self.overwrite = overwrite
        self.gui = gui
        
        self._stopped = False
        
        self.logger = logging.getLogger("%s_%s['%s']" 
                                        % (self.sample, self.scan, self._entry))
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
                        '%(asctime)s %(name)-12s: %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')
        for handler in self.logger.handlers:
            self.logger.removeHandler(handler)
        if os.path.exists(os.path.join(self.task_directory, 'nxlogger.pid')):
            socketHandler = logging.handlers.SocketHandler('localhost',
                                logging.handlers.DEFAULT_TCP_LOGGING_PORT)
            self.logger.addHandler(socketHandler)
        else:
            fileHandler = logging.FileHandler(self.log_file)
            fileHandler.setFormatter(formatter)
            self.logger.addHandler(fileHandler)
        if not self.gui:
            streamHandler = logging.StreamHandler()
            self.logger.addHandler(streamHandler)

    start = QtCore.Signal(object)
    update = QtCore.Signal(object)
    result = QtCore.Signal(object)
    stop = QtCore.Signal()            

    @property
    def command(self):
        switches = '-d %s -e %s' % (self.directory, self._entry)
        if self.refine:
            switches += ' -r'
        if self.overwrite:
            switches += ' -o'
        return 'nxreduce ' + switches

    @property
    def root(self):
        if self._root is None:
            with Lock(self.wrapper_file):
                self._root = nxload(self.wrapper_file, 'r+')
        return self._root

    @property
    def entry(self):
        return self.root[self._entry]

    @property
    def data(self):
        return self.entry['data']

    @property
    def field(self):
        if self._field is None:
            self._field = nxload(self.data_file, 'r')[self.data_target]
        return self._field

    @property
    def data_file(self):
        return self.entry[self._data].nxfilename

    @property
    def data_target(self):
        return self.entry[self._data].nxtarget

    @property
    def mask(self):
        if self._mask is None:
            try:
                self._mask = self.entry['instrument/detector/pixel_mask'].nxvalue
            except Exception as error:
                pass
        return self._mask

    @property
    def parent(self):
        if (self._parent is None and 
            os.path.exists(os.path.join(self.base_directory, 
                                        self.sample+'_parent.nxs'))):
            _parent = os.path.join(self.base_directory, 
                                   self.sample+'_parent.nxs')
            if os.path.realpath(_parent) != self.wrapper_file:
                self._parent = _parent
        return self._parent

    def make_parent(self):
        _parent = os.path.join(self.base_directory, self.sample+'_parent.nxs')
        if os.path.exists(_parent):
            if self.overwrite:
                os.remove(_parent)
            else:
                raise NeXusError("'%s' already set as parent" 
                                 % os.path.realpath(_parent))
        os.symlink(self.wrapper_file, _parent)
        self._parent = None
        self.logger.info("'%s' set as parent" % os.path.realpath(_parent))

    @property
    def maximum(self):
        if self._maximum is None:
            if 'maximum' in self.entry['data'].attrs:
                self._maximum = self.entry['data'].attrs['maximum']
            elif 'maximum' in self.entry['peaks'].attrs:
                self._maximum = self.entry['peaks'].attrs['maximum']
        return self._maximum

    def not_complete(self, program):
        return program not in self.entry or self.overwrite

    def start_progress(self, start, stop):
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
        else:
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

    def record(self, program, **kwds):
        parameters = '\n'.join(
            [('%s: %s' % (k, v)).replace('_', ' ').capitalize()
             for (k,v) in kwds.items()])
        note = NXnote(program, ('Current machine: %s\n' % platform.node() + 
                                'Current directory: %s\n' % self.directory +
                                parameters))
        if program in self.entry:
            del self.entry[program]
        self.entry[program] = NXprocess(program='%s' % program, 
                                sequence_index=len(self.entry.NXprocess)+1, 
                                version='nxrefine v'+__version__, 
                                note=note)

    def nxlink(self):
        if self.not_complete('nxlink'):
            with Lock(self.wrapper_file):
                self.link_data()
                logs = self.read_logs()
                if logs:
                    if 'logs' in self.entry:
                        del self.entry['logs']
                    self.entry['logs'] = logs
                    self.transfer_logs()
                    self.record('nxlink', logs='Transferred')
                else:
                    self.record('nxlink')
        else:
            self.logger.info('Data already linked')             

    def link_data(self):
        if self.field:
            shape = self.field.shape
            if 'data' not in self.entry:
                self.entry['data'] = NXdata()
                self.entry['data/x_pixel'] = np.arange(shape[2], dtype=np.int32)
                self.entry['data/y_pixel'] = np.arange(shape[1], dtype=np.int32)
                self.entry['data/frame_number'] = np.arange(shape[0], dtype=np.int32)
                self.entry['data/data'] = NXlink(self.data_target, self.data_file)
                self.logger.info('Data group created and linked to external data')
            else:
                if self.entry['data/frame_number'].shape != shape[0]:
                    del self.entry['data/frame_number']
                    self.entry['data/frame_number'] = np.arange(shape[0], dtype=np.int32)
                    self.logger.info('Fixed frame number axis')
                if ('data' in entry['data'] and 
                    entry['data/data']._filename != data_file):
                    del entry['data/data']
                    entry['data/data'] = NXlink(data_target, data_file)
                    self.logger.info('Fixed path to external data')
            self.entry['data'].nxsignal = self.entry['data/data']
            self.entry['data'].nxaxes = [self.entry['data/frame_number'], 
                                         self.entry['data/y_pixel'], 
                                         self.entry['data/x_pixel']]
        else:
            self.logger.info('No raw data loaded')

    def read_logs(self):
        head_file = os.path.join(self.directory, entry+'_head.txt')
        meta_file = os.path.join(self.directory, entry+'_meta.txt')
        if os.path.exists(head_file) or os.path.exists(meta_file):
            logs = NXcollection()
        else:
            self.logger.info('No metadata files found')
            return None
        if os.path.exists(head_file):
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
        if os.path.exists(meta_file):
            meta_input = np.genfromtxt(meta_file, delimiter=',', names=True)
            for i, key in enumerate(meta_input.dtype.names):
                logs[key] = [array[i] for array in meta_input]
        return logs

    def transfer_logs(self):
        logs = self.entry['instrument/logs']
        frames = self.entry['data/frame_number'].size
        if 'MCS1' in logs:
            if 'monitor1' in self.entry:
                del self.entry['monitor1']
            data = logs['MCS1'][:frames]
            self.entry['monitor1'] = NXmonitor(NXfield(data, name='MCS1'),
                                               NXfield(np.arange(frames, 
                                                                 dtype=np.int32), 
                                                       name='frame_number'))
        if 'MCS2' in logs:
            if 'monitor2' in self.entry:
                del self.entry['monitor2']
            data = logs['MCS2'][:frames]
            self.entry['monitor2'] = NXmonitor(NXfield(data, name='MCS2'),
                                               NXfield(np.arange(frames, 
                                                                 dtype=np.int32), 
                                                       name='frame_number'))
        if 'source' not in self.entry['instrument']:
            self.entry['instrument/source'] = NXsource()
        self.entry['instrument/source/name'] = 'Advanced Photon Source'
        self.entry['instrument/source/type'] = 'Synchrotron X-ray Source'
        self.entry['instrument/source/probe'] = 'x-ray'
        if 'Storage_Ring_Current' in logs:
            self.entry['instrument/source/current'] = logs['Storage_Ring_Current']
        if 'UndulatorA_gap' in logs:
            self.entry['instrument/source/undulator_gap'] = logs['UndulatorA_gap']
        if 'Calculated_filter_transmission' in logs:
            if 'attenuator' not in self.entry['instrument']:
                self.entry['instrument/attenuator'] = NXattenuator()
            self.entry['instrument/attenuator/attenuator_transmission'] = logs['Calculated_filter_transmission']

    def nxmax(self):
        if self.not_complete('nxmax'):
            with Lock(self.data_file):
                maximum = self.find_maximum()
            if self.gui:
                if maximum:
                    self.result.emit(maximum)
                self.stop.emit()
            else:
                with Lock(self.wrapper_file):
                    self.write_maximum(maximum)
        else:
            self.logger.info('Maximum counts already found')             

    def find_maximum(self):
        self.logger.info('Finding maximum counts')
        maximum = 0.0
        nframes = self.field.shape[0]
        chunk_size = self.field.chunks[0]
        if chunk_size < 20:
            chunk_size = 50
        data = self.field.nxfile[self.path]
        tic = self.start_progress(0, nframes)
        for i in range(0, nframes, chunk_size):
            if self.stopped:
                return None
            self.update_progress(i)
            try:
                v = data[i:i+chunk_size,:,:]
            except IndexError as error:
                pass
            if self.mask is not None:
                v = np.ma.masked_array(v)
                v.mask = self.mask
            if maximum < v.max():
                maximum = v.max()
            del v
        toc = self.stop_progress()
        self.logger.info('Maximum counts: %s (%g seconds)' % (maximum, toc-tic))
        return maximum

    def write_maximum(self, maximum):
        self.entry['data'].attrs['maximum'] = maximum
        self.record('nxmax', maximum=maximum)

    def nxfind(self):
        if self.not_complete('nxfind'):
            with Lock(self.data_file):
                peaks = self.find_peaks()
            if self.gui:
                if peaks:
                    self.result.emit(peaks)
                self.stop.emit()
            else:
                with Lock(self.wrapper_file):
                    self.write_peaks(peaks)
        else:
            self.logger.info('Peaks already found')             

    def find_peaks(self):
        self.logger.info("Finding peaks")
        if self.threshold is None:
            if self.maximum is None:
                self.nxmax()     
            self.threshold = self.maximum / 10

        if self.first == None:
            self.first = 0
        if self.last == None:
            self.last = self.field.shape[0]
        z_min, z_max = self.first, self.last
        
        tic = self.start_progress(z_min, z_max)

        lio = labelimage(self.field.shape[-2:], flipper=flip1)
        allpeaks = []
        if len(self.field.shape) == 2:
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
                                        peak = NXpeak(res[0], res[22],
                                            res[23], res[24], omega,
                                            res[27], res[26], res[29],
                                            self.threshold,
                                            pixel_tolerance,
                                            frame_tolerance)
                                        if peak.isvalid(self.mask):
                                            allpeaks.append(peak)
                except IndexError as error:
                    pass

        if not allpeaks:
            toc = self.stop_progress()
            self.logger.info('No peaks found (%g seconds)' % (toc-tic))
            return None

        allpeaks = sorted(allpeaks)

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
        group['npixels'] = NXfield([peak.np for peak in peaks], dtype=np.float32)
        group['intensity'] = NXfield([peak.intensity for peak in peaks], 
                                        dtype=np.float32)
        group['x'] = NXfield([peak.x for peak in peaks], dtype=np.float32)
        group['y'] = NXfield([peak.y for peak in peaks], dtype=np.float32)
        group['z'] = NXfield([peak.z for peak in peaks], dtype=np.float32)
        group['sigx'] = NXfield([peak.sigx for peak in peaks], dtype=np.float32)
        group['sigy'] = NXfield([peak.sigy for peak in peaks], dtype=np.float32)
        group['covxy'] = NXfield([peak.covxy for peak in peaks], dtype=np.float32)
        if 'peaks' in self.entry:
            del self.entry['peaks']
        self.entry['peaks'] = group
        self.record('nxfind', threshold=self.threshold,
                    first_frame=self.first, last_frame=self.last, 
                    peak_number=len(peaks))                    

    def nxmask(self):
        if self.not_complete('nxmask'):
            with Lock(self.wrapper_file):
                mask = self.calculate_mask()
                if self.gui:
                    if mask:
                        self.result.emit(mask)
                    self.stop.emit()
                else:
                    self.write_mask(mask)
        else:
            self.logger.info('Mask already produced')             

    def calculate_mask(self):
        self.logger.info("Calculating 3D mask")
        data_shape = self.entry['data/data'].shape
        mask = NXfield(shape=data_shape, dtype=np.int8, fillvalue=0)
        x, y = np.arange(data_shape[2]), np.arange(data_shape[1])
        xp, yp, zp = self.entry['peaks/x'], self.entry['peaks/y'], self.entry['peaks/z']
        tic = self.start_progress(0, len(xp))    
        for i in range(len(xp)):
            if self.stopped:
                return None
            self.update_progress(int(zp[i]))
            inside = (x[None,:]-int(xp[i]))**2+(y[:,None]-int(yp[i]))**2 < self.radius**2
            frame = int(zp[i])
            if self.width == 3:
                mask[frame-1:frame+2] = mask[frame-1:frame+2] | inside
            else:
                mask[frame] = mask[frame] | inside
        toc = self.stop_progress()
        self.logger.info('3D Mask calculated (%g seconds)' % (toc-tic))
        return mask
 
    def write_mask(self, mask):
        if 'data_mask' in entry['data']:
            del self.entry['data/data_mask']
        self.entry['data/data_mask'] = mask
        self.record('nxmask', radius=self.radius, width=self.width)                    

    def nxcopy(self):
        if self.not_complete('nxcopy'):
            if self.parent:
                self.copy()
                self.record('nxcopy', parent=self.parent)
            else:
                self.logger.info('No parent defined')               
        else:
            self.logger.info('Data already copied')             

    def copy(self):
        with Lock(self.parent):
            input = nxload(self.parent)
            input_ref = NXRefine(input[self._entry])
        with Lock(self.wrapper_file):
            output_ref = NXRefine(self.entry)
            input_ref.copy_parameters(output_ref, instrument=True)
        self.logger.info("Parameters copied from '%s'", self.parent)

    def nxrefine(self):
        if self.not_complete('nxrefine') and self.refine:
            with Lock(self.wrapper_file):
                result = self.refine()
                if not self.gui:
                    self.write_refinement(result)
        else:
            self.logger.info('HKL values already refined')             

    def refine(self):
        refine = NXRefine(self.entry)
        if i == 0:
            refine.refine_hkl_parameters(chi=True,omega=True)
            fit_report=refine.fit_report
            refine.refine_hkl_parameters(chi=True, omega=True, gonpitch=True)                
        else:
            refine.refine_hkl_parameters(lattice=False, chi=True, omega=True)
            fit_report=refine.fit_report
            refine.refine_hkl_parameters(
                lattice=False, chi=True, omega=True, gonpitch=True)
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
        refine.write_parameters()
        self.record('nxrefine', fit_report=refine.fit_report)

    def transform(self):
        pass

    def run(self):
        self.nxlink()
        self.nxmax()
        self.nxfind()
        self.nxmask()
        self.nxcopy()
        self.nxrefine()
        self.nxtransform()


class NXMultiReduce(object):

    def __init__(self, directory, entries=['f1', 'f2', 'f3'], *kwds):

        self.directory = directory.rstrip('/')
        self.entries = entries   
        self.kwds = kwds
        self.server = NXServer()

    def reduce(self):
        for entry in self.entries:
            reduce = NXReduce(self.directory, entry, *self.kwds)
            self.server.add_task(reduce.command())


class NXpeak(object):

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
        return "Peak x=%f y=%f z=%f np=%i avg=%f" % (self.x, self.y, self.z, self.np, self.average)

    def __repr__(self):
        return "Peak x=%f y=%f z=%f np=%i avg=%f" % (self.x, self.y, self.z, self.np, self.average)

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
