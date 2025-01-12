# -----------------------------------------------------------------------------
# Copyright (c) 2015-2021, NeXpy Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING, distributed with this software.
# -----------------------------------------------------------------------------

import numpy as np
import pyFAI
from nexpy.gui.datadialogs import GridParameters, NXDialog
from nexpy.gui.plotview import NXPlotView, plotviews
from nexpy.gui.utils import confirm_action, load_image, report_error
from nexusformat.nexus import (NeXusError, NXcollection, NXdata, NXfield,
                               NXprocess)
from pyFAI.azimuthalIntegrator import AzimuthalIntegrator
from pyFAI.calibrant import ALL_CALIBRANTS
from pyFAI.geometryRefinement import GeometryRefinement
from pyFAI.massif import Massif


def show_dialog():
    try:
        dialog = CalibrateDialog()
        dialog.show()
    except NeXusError as error:
        report_error("Calibrating Powder", error)


class CalibrateDialog(NXDialog):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.plotview = None
        self.data = None
        self.counts = None
        self.points = []
        self.pattern_geometry = None
        self.cake_geometry = None
        self.polarization = None
        self.is_calibrated = False
        self.phi_max = -np.pi

        cstr = str(ALL_CALIBRANTS)
        calibrants = sorted(cstr[cstr.index(':')+2:].split(', '))
        self.parameters = GridParameters()
        self.parameters.add('calibrant', calibrants, 'Calibrant')
        self.parameters['calibrant'].value = 'CeO2'
        self.parameters.add('wavelength', 0.5, 'Wavelength (Ang)', False)
        self.parameters.add('distance', 100.0, 'Detector Distance (mm)', True)
        self.parameters.add('xc', 512, 'Beam Center - x', True)
        self.parameters.add('yc', 512, 'Beam Center - y', True)
        self.parameters.add('yaw', 0.0, 'Yaw (degrees)', True)
        self.parameters.add('pitch', 0.0, 'Pitch (degrees)', True)
        self.parameters.add('roll', 0.0, 'Roll (degrees)', True)
        self.parameters.add('search_size', 10, 'Search Size (pixels)')
        self.rings_box = self.select_box([f'Ring{i}' for i in range(1, 21)])
        self.set_layout(self.select_entry(self.choose_entry),
                        self.progress_layout(close=True))
        self.set_title('Calibrating Powder')

    def choose_file(self):
        super().choose_file()
        powder_file = self.get_filename()
        if powder_file:
            self.data = load_image(powder_file)
            self.counts = self.data.nxsignal.nxvalue
            self.plot_data()

    def choose_entry(self):
        if self.layout.count() == 2:
            self.insert_layout(
                1, self.filebox('Choose Powder Calibration File'))
            self.insert_layout(2, self.parameters.grid(header=False))
            self.insert_layout(
                3, self.action_buttons(('Select Points', self.select),
                                       ('Autogenerate Rings', self.auto),
                                       ('Clear Points', self.clear_points)))
            self.insert_layout(4, self.make_layout(self.rings_box))
            self.insert_layout(
                5, self.action_buttons(('Calibrate', self.calibrate),
                                       ('Plot Cake', self.plot_cake),
                                       ('Restore', self.restore_parameters),
                                       ('Save', self.save_parameters)))
        self.parameters['wavelength'].value = (
            self.entry['instrument/monochromator/wavelength'])
        detector = self.entry['instrument/detector']
        self.parameters['distance'].value = detector['distance']
        self.parameters['yaw'].value = detector['yaw']
        self.parameters['pitch'].value = detector['pitch']
        self.parameters['roll'].value = detector['roll']
        if 'beam_center_x' in detector:
            self.parameters['xc'].value = detector['beam_center_x']
        if 'beam_center_y' in detector:
            self.parameters['yc'].value = detector['beam_center_y']
        self.pixel_size = (
            self.entry['instrument/detector/pixel_size'].nxvalue * 1e-3)
        self.pixel_mask = self.entry['instrument/detector/pixel_mask'].nxvalue
        self.ring = self.selected_ring
        if 'calibration' in self.entry['instrument']:
            self.data = self.entry['instrument/calibration']
            self.counts = self.data.nxsignal.nxvalue
            self.plot_data()
        else:
            self.close_plots()

    @property
    def search_size(self):
        return int(self.parameters['search_size'].value)

    @property
    def selected_ring(self):
        return int(self.rings_box.currentText()[4:]) - 1

    @property
    def ring_color(self):
        colors = ['r', 'b', 'g', 'c', 'm'] * 4
        return colors[self.ring]

    def plot_data(self):
        if self.plotview is None:
            if 'Powder Calibration' in plotviews:
                self.plotview = plotviews['Powder Calibration']
            else:
                self.plotview = NXPlotView('Powder Calibration')
        self.plotview.plot(self.data, log=True)
        self.plotview.aspect = 'equal'
        self.plotview.ytab.flipped = True
        self.clear_points()

    def on_button_press(self, event):
        self.plotview.make_active()
        if event.inaxes:
            self.xp, self.yp = event.x, event.y
        else:
            self.xp, self.yp = 0, 0

    def on_button_release(self, event):
        self.ring = self.selected_ring
        if event.inaxes:
            if abs(event.x - self.xp) > 5 or abs(event.y - self.yp) > 5:
                return
            x, y = self.plotview.inverse_transform(event.xdata, event.ydata)
            for i, point in enumerate(self.points):
                circle = point[0]
                if circle.shape.contains_point(
                        self.plotview.ax.transData.transform((x, y))):
                    circle.remove()
                    for circle in point[2]:
                        circle.remove()
                    del self.points[i]
                    return
            self.add_points(x, y)

    def circle(self, idx, idy, alpha=1.0):
        return self.plotview.circle(idx, idy, self.search_size,
                                    facecolor=self.ring_color, edgecolor='k',
                                    alpha=alpha)

    def select(self):
        self.plotview.cidpress = self.plotview.mpl_connect(
            'button_press_event', self.on_button_press)
        self.plotview.cidrelease = self.plotview.mpl_connect(
            'button_release_event', self.on_button_release)

    def auto(self):
        xc, yc = self.parameters['xc'].value, self.parameters['yc'].value
        wavelength = self.parameters['wavelength'].value
        distance = self.parameters['distance'].value * 1e-3
        self.start_progress((0, self.selected_ring+1))
        for ring in range(self.selected_ring+1):
            self.update_progress(ring)
            if len([p for p in self.points if p[3] == ring]) > 0:
                continue
            self.ring = ring
            theta = 2 * np.arcsin(wavelength /
                                  (2*self.calibrant.dSpacing[ring]))
            r = distance * np.tan(theta) / self.pixel_size
            phi = self.phi_max = -np.pi
            while phi < np.pi:
                x, y = np.int(xc + r*np.cos(phi)), np.int(yc + r*np.sin(phi))
                if ((x > 0 and x < self.data.x.max()) and
                    (y > 0 and y < self.data.y.max()) and
                        not self.pixel_mask[y, x]):
                    self.add_points(x, y, phi)
                    phi = self.phi_max + 0.2
                else:
                    phi = phi + 0.2
        self.stop_progress()

    def add_points(self, x, y, phi=0.0):
        xc, yc = self.parameters['xc'].value, self.parameters['yc'].value
        idx, idy = self.find_peak(x, y)
        points = [(idy, idx)]
        circles = []
        massif = Massif(self.counts)
        extra_points = massif.find_peaks((idy, idx))
        for point in extra_points:
            points.append(point)
            circles.append(self.circle(point[1], point[0], alpha=0.3))
        phis = np.array([np.arctan2(p[0]-yc, p[1]-xc) for p in points])
        if phi < -0.5*np.pi:
            phis[np.where(phis > 0.0)] -= 2 * np.pi
        self.phi_max = max(*phis, self.phi_max)
        self.points.append([self.circle(idx, idy), points, circles, self.ring])

    def find_peak(self, x, y):
        s = self.search_size
        left = int(np.round(x - s * 0.5))
        if left < 0:
            left = 0
        top = int(np.round(y - s * 0.5))
        if top < 0:
            top = 0
        region = self.counts[top:(top+s), left:(left+s)]
        idy, idx = np.where(region == region.max())
        idx = left + idx[0]
        idy = top + idy[0]
        return idx, idy

    def clear_points(self):
        for i, point in enumerate(self.points):
            circle = point[0]
            circle.remove()
            for circle in point[2]:
                circle.remove()
        self.points = []

    @property
    def calibrant(self):
        return ALL_CALIBRANTS[self.parameters['calibrant'].value]

    @property
    def point_array(self):
        points = []
        for point in self.points:
            for p in point[1]:
                points.append((p[0], p[1], point[3]))
        return np.array(points)

    def prepare_parameters(self):
        self.parameters.set_parameters()
        self.wavelength = self.parameters['wavelength'].value * 1e-10
        self.distance = self.parameters['distance'].value * 1e-3
        self.yaw = np.radians(self.parameters['yaw'].value)
        self.pitch = np.radians(self.parameters['pitch'].value)
        self.roll = np.radians(self.parameters['roll'].value)
        self.xc = self.parameters['xc'].value
        self.yc = self.parameters['yc'].value

    def calibrate(self):
        self.prepare_parameters()
        self.orig_pixel1 = self.pixel_size
        self.orig_pixel2 = self.pixel_size
        self.pattern_geometry = GeometryRefinement(self.point_array,
                                                   dist=self.distance,
                                                   wavelength=self.wavelength,
                                                   pixel1=self.pixel_size,
                                                   pixel2=self.pixel_size,
                                                   calibrant=self.calibrant)
        self.refine()
        self.create_cake_geometry()
        self.pattern_geometry.reset()

    def refine(self):
        self.pattern_geometry.data = self.point_array

        if self.parameters['wavelength'].vary:
            self.pattern_geometry.refine2()
            fix = []
        else:
            fix = ['wavelength']
        if not self.parameters['distance'].vary:
            fix.append('dist')
        self.pattern_geometry.refine2_wavelength(fix=fix)
        self.read_parameters()
        self.is_calibrated = True
        self.create_cake_geometry()
        self.pattern_geometry.reset()

    def create_cake_geometry(self):
        self.cake_geometry = AzimuthalIntegrator()
        pyFAI_parameter = self.pattern_geometry.getPyFAI()
        pyFAI_parameter['wavelength'] = self.pattern_geometry.wavelength
        self.cake_geometry.setPyFAI(dist=pyFAI_parameter['dist'],
                                    poni1=pyFAI_parameter['poni1'],
                                    poni2=pyFAI_parameter['poni2'],
                                    rot1=pyFAI_parameter['rot1'],
                                    rot2=pyFAI_parameter['rot2'],
                                    rot3=pyFAI_parameter['rot3'],
                                    pixel1=pyFAI_parameter['pixel1'],
                                    pixel2=pyFAI_parameter['pixel2'])
        self.cake_geometry.wavelength = pyFAI_parameter['wavelength']

    def plot_cake(self):
        if 'Cake Plot' in plotviews:
            plotview = plotviews['Cake Plot']
        else:
            plotview = NXPlotView('Cake Plot')
        if not self.is_calibrated:
            raise NeXusError('No refinement performed')
        res = self.cake_geometry.integrate2d(self.counts,
                                             1024, 1024,
                                             method='csr',
                                             unit='2th_deg',
                                             correctSolidAngle=True)
        self.cake_data = NXdata(res[0],
                                (NXfield(res[2], name='azimumthal_angle'),
                                 NXfield(res[1], name='polar_angle')))
        self.cake_data['title'] = 'Cake Plot'
        plotview.plot(self.cake_data, log=True)
        wavelength = self.parameters['wavelength'].value
        polar_angles = [2 * np.degrees(np.arcsin(wavelength/(2*d)))
                        for d in self.calibrant.dSpacing]
        plotview.vlines([polar_angle for polar_angle in polar_angles
                         if polar_angle < plotview.xaxis.max],
                        linestyle=':', color='r')

    def read_parameters(self):
        pyFAI = self.pattern_geometry.getPyFAI()
        fit2d = self.pattern_geometry.getFit2D()
        self.parameters['wavelength'].value = (
            self.pattern_geometry.wavelength * 1e10)
        self.parameters['distance'].value = pyFAI['dist'] * 1e3
        self.parameters['yaw'].value = np.degrees(pyFAI['rot1'])
        self.parameters['pitch'].value = np.degrees(pyFAI['rot2'])
        self.parameters['roll'].value = np.degrees(pyFAI['rot3'])
        self.parameters['xc'].value = fit2d['centerX']
        self.parameters['yc'].value = fit2d['centerY']

    def restore_parameters(self):
        self.parameters.restore_parameters()

    def save_parameters(self):
        if not self.is_calibrated:
            raise NeXusError('No refinement performed')
        elif 'calibration' in self.entry['instrument']:
            if confirm_action(
                    "Do you want to overwrite existing calibration data?"):
                del self.entry['instrument/calibration']
            else:
                return
        self.entry['instrument/calibration'] = self.data
        if 'refinement' in self.entry['instrument/calibration']:
            if confirm_action('Overwrite previous refinement?'):
                del self.entry['instrument/calibration/refinement']
            else:
                return
        self.entry['instrument/calibration/calibrant'] = (
            self.parameters['calibrant'].value)
        process = NXprocess()
        process.program = 'pyFAI'
        process.version = pyFAI.version
        process.parameters = NXcollection()
        process.parameters['Detector'] = (
            self.entry['instrument/detector/description'])
        pyFAI_parameter = self.pattern_geometry.getPyFAI()
        process.parameters['PixelSize1'] = pyFAI_parameter['pixel1']
        process.parameters['PixelSize2'] = pyFAI_parameter['pixel2']
        process.parameters['Distance'] = pyFAI_parameter['dist']
        process.parameters['Poni1'] = pyFAI_parameter['poni1']
        process.parameters['Poni2'] = pyFAI_parameter['poni2']
        process.parameters['Rot1'] = pyFAI_parameter['rot1']
        process.parameters['Rot2'] = pyFAI_parameter['rot2']
        process.parameters['Rot3'] = pyFAI_parameter['rot3']
        process.parameters['Wavelength'] = pyFAI_parameter['wavelength']
        self.entry['instrument/calibration/refinement'] = process
        self.entry['instrument/monochromator/wavelength'] = (
            self.parameters['wavelength'].value)
        self.entry['instrument/monochromator/energy'] = (
            12.398419739640717 / self.parameters['wavelength'].value)
        detector = self.entry['instrument/detector']
        detector['distance'] = self.parameters['distance'].value
        detector['yaw'] = self.parameters['yaw'].value
        detector['pitch'] = self.parameters['pitch'].value
        detector['roll'] = self.parameters['roll'].value
        detector['beam_center_x'] = self.parameters['xc'].value
        detector['beam_center_y'] = self.parameters['yc'].value
        try:
            detector['polarization'] = self.pattern_geometry.polarization(
                factor=0.99, shape=detector['mask'].shape)
        except Exception:
            pass

    def close_plots(self):
        if 'Powder Calibration' in plotviews:
            plotviews['Powder Calibration'].close()
        if 'Cake Plot' in plotviews:
            plotviews['Cake Plot'].close()

    def closeEvent(self, event):
        self.close_plots()
        event.accept()

    def accept(self):
        super().accept()
        self.close_plots()

    def reject(self):
        super().reject()
        self.close_plots()
