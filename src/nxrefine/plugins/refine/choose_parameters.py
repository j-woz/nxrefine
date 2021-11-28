from nexpy.gui.datadialogs import GridParameters, NXDialog
from nexpy.gui.utils import report_error
from nexusformat.nexus import NeXusError, NXparameters
from nxrefine.nxreduce import NXReduce


def show_dialog():
    try:
        dialog = ParametersDialog()
        dialog.show()
    except NeXusError as error:
        report_error("Choosing Parameters", error)


class ParametersDialog(NXDialog):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.select_root(self.choose_root)

        self.parameters = GridParameters()
        self.parameters.add('threshold', 50000.0, 'Peak Threshold')
        self.parameters.add('first', 0, 'First Frame')
        self.parameters.add('last', 3650, 'Last Frame')
        self.parameters.add('monitor', ['monitor1', 'monitor2'], 
                            'Normalization Monitor')
        self.parameters['monitor'].value = 'monitor2'
        self.parameters.add('norm', 30000.0, 'Normalization Value')
        self.parameters.add('radius', 0.2, 'Punch Radius (Å)')

        self.set_layout(self.root_layout,
                        self.close_buttons(save=True))
        self.set_title('Choose Parameters')

    def choose_root(self):
        self.entries = [self.root[entry] 
                        for entry in self.root if entry != 'entry']
        if self.layout.count() == 2:
            self.layout.insertLayout(1, self.parameters.grid(header=False))
        self.read_parameters()

    def read_parameters(self):
        if 'nxreduce' in self.root['entry']:
            reduce = self.root['entry/nxreduce']
            if 'threshold' in reduce:
                self.parameters['threshold'].value = reduce['threshold']
            if 'first_frame' in reduce:
                self.parameters['first'].value = reduce['first_frame']
            if 'last_frame' in reduce:
                self.parameters['last'].value = reduce['last_frame']
            if 'monitor' in reduce:
                self.parameters['monitor'].value = reduce['monitor']
            if 'norm' in reduce:
                self.parameters['norm'].value = reduce['norm']
            if 'radius' in reduce:
                self.parameters['radius'].value = reduce['radius']
        else:
            try:
                reduce = NXReduce(self.entries[0])
                if reduce.first:
                    self.parameters['first'].value = reduce.first
                if reduce.last:
                    self.parameters['last'].value = reduce.last
                if reduce.threshold:
                    self.parameters['threshold'].value = reduce.threshold
                if reduce.monitor:
                    self.parameters['monitor'].value = reduce.monitor
                if reduce.norm:
                    self.parameters['norm'].value = reduce.norm
                if reduce.radius:
                    self.parameters['radius'].value = reduce.radius
            except Exception:
                pass

    def write_parameters(self):
        if 'nxreduce' not in self.root['entry']:
            self.root['entry/nxreduce'] = NXparameters()
        self.root['entry/nxreduce/threshold'] = self.threshold
        self.root['entry/nxreduce/first_frame'] = self.first
        self.root['entry/nxreduce/last_frame'] = self.last
        self.root['entry/nxreduce/monitor'] = self.monitor
        self.root['entry/nxreduce/norm'] = self.norm
        self.root['entry/nxreduce/radius'] = self.radius
#        self.remove_parameters()

    def remove_parameters(self):
        for entry in self.entries:
            if 'peaks' in entry:
                if 'threshold' in entry['peaks'].attrs:
                    del entry['peaks'].attrs['threshold']
                if 'first' in entry['peaks'].attrs:
                    del entry['peaks'].attrs['first']
                if 'last' in entry['peaks'].attrs:
                    del entry['peaks'].attrs['last']
                if 'norm' in entry['peaks'].attrs:
                    del entry['peaks'].attrs['norm']

    @property
    def threshold(self):
        return float(self.parameters['threshold'].value)

    @property
    def first(self):
        return int(self.parameters['first'].value)

    @property
    def last(self):
        return int(self.parameters['last'].value)

    @property
    def monitor(self):
        return self.parameters['monitor'].value

    @property
    def norm(self):
        return float(self.parameters['norm'].value)

    @property
    def radius(self):
        return float(self.parameters['radius'].value)

    def accept(self):
        try:
            self.write_parameters()
            super().accept()
        except NeXusError as error:
            report_error(error)
