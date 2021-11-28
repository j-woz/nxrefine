import numpy as np
from nexusformat.nexus import *

class NXSymmetry(object):

    def __init__(self, data, laue_group='-1'):
        laue_functions = {'-1': self.triclinic,
                          '2/m': self.monoclinic,
                          'mmm': self.orthorhombic,
                          '4/m': self.tetragonal1,
                          '4/mmm': self.tetragonal2,
                          '-3': self.triclinic,
                          '-3m': self.triclinic,
                          '6/m': self.hexagonal,
                          '6/mmm': self.hexagonal,
                          'm-3': self.cubic,
                          'm-3m': self.cubic}
        if laue_group in laue_functions:
            self._function = laue_functions[laue_group]
        else:
            self._function = self.triclinic
        if isinstance(data, NXdata):
            self._data = data
            signal = data.nxsignal.nxvalue
            weights = data.nxweights.nxvalue
        else:
            self._data = None
            if isinstance(data, NXfield):
                signal = data.nxvalue
            else:
                signal = data
            weights = None
        self._signal = np.nan_to_num(signal)
        if weights is None:
            self._wts = np.zeros(self._signal.shape, dtype=self._signal.dtype)
            self._wts[np.where(self._signal>0)] = 1
        else:
            self._wts = np.nan_to_num(weights, nan=1.0)

    def triclinic(self, data):
        """Laue group: -1"""
        outarr = data
        outarr += np.flip(outarr)
        return outarr

    def monoclinic(self, data):
        """Laue group: 2/m"""
        outarr = data
        outarr += np.rot90(outarr, 2, (0,2))
        outarr += np.flip(outarr, 0)
        return outarr

    def orthorhombic(self, data):
        """Laue group: mmm"""
        outarr = data
        outarr += np.flip(outarr, 0)
        outarr += np.flip(outarr, 1)
        outarr += np.flip(outarr, 2)
        return outarr

    def tetragonal1(self, data):
        """Laue group: 4/m"""
        outarr = data
        outarr += np.rot90(outarr, 1, (1,2))
        outarr += np.rot90(outarr, 2, (1,2))
        outarr += np.flip(outarr, 0)
        return outarr

    def tetragonal2(self, data):
        """Laue group: 4/mmm"""
        outarr = data
        outarr += np.rot90(outarr, 1, (1,2))
        outarr += np.rot90(outarr, 2, (1,2))
        outarr += np.rot90(outarr, 2, (0,1))
        outarr += np.flip(outarr, 0)
        return outarr

    def hexagonal(self, data):
        """Laue group: 6/m, 6/mmm (modeled as 2/m along the c-axis)"""
        outarr = data
        outarr += np.rot90(outarr, 2, (1,2))
        outarr += np.flip(outarr, 0)
        return outarr

    def cubic(self, data):
        """Laue group: m-3 or m-3m"""
        outarr = data
        outarr += np.transpose(outarr,axes=(1,2,0))+np.transpose(outarr,axes=(2,0,1))
        outarr += np.transpose(outarr,axes=(0,2,1))
        outarr += np.flip(outarr, 0)
        outarr += np.flip(outarr, 1)
        outarr += np.flip(outarr, 2)
        return outarr

    def symmetrize(self):
        signal = np.nan_to_num(self._function(self._signal))
        weights = np.nan_to_num(self._function(self._wts))
        with np.errstate(divide='ignore'):
            result = np.where(weights>0, signal/weights, 0.0)
        if self._data:
            return NXdata(NXfield(result, name=self._data.nxsignal.nxname), 
                          self._data.nxaxes)
        else:
            return result

