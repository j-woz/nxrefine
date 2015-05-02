import argparse
import os
import re
import numpy as np
from nexusformat.nexus import *


def make_nexus_file(sample_name, sample_label, scan_directory, temperature, 
                    filenames, maskfiles):    
    root = NXroot()
    sample = NXsample()
    sample.name = sample_name
    if sample_label:
        sample.label = sample_label
    sample['temperature'] = temperature
    sample['temperature'].attrs['units'] = 'K'
    root.entry = NXentry(sample)
    for (f, m) in zip(filenames, maskfiles):
        try:
            mask = nxload(m+'.nxs')['entry/mask']
        except Exception:
            mask = None
        root[f] = make_entry(mask)
        root[f].makelink(root.entry.sample)
    return root
    

def make_entry(mask=None):
    entry = NXentry()
    entry.instrument = NXinstrument()
    entry.instrument.detector = NXdetector()
    if mask is not None:
        entry.instrument.detector.pixel_mask = mask
        entry.instrument.detector.pixel_mask_applied = False
    return entry


def main():

    parser = argparse.ArgumentParser(
        description="Make NeXus file and directories for new scan")
    parser.add_argument('-s', '--sample', help='sample name')
    parser.add_argument('-l', '--label', default='', help='sample label')
    parser.add_argument('-d', '--directory', default='', help='scan directory')
    parser.add_argument('-t', '--temperature', help='temperature of scan')
    parser.add_argument('-f', '--filenames', default=['f1', 'f2', 'f3'], 
        nargs='+', help='names of NeXus files to be linked to this file')
    parser.add_argument('-m', '--maskfiles', nargs='+',
        help='name of the pixel mask files')
    
    args = parser.parse_args()

    sample = args.sample
    label = args.label
    directory = args.directory.rstrip('/')
    if sample is None and label == '':
        sample = os.path.basename(os.path.dirname(os.path.dirname(directory)))   
        label = os.path.basename(os.path.dirname(directory))
        directory = os.path.basename(directory)
    temperature = np.float32(args.temperature)
    filenames = args.filenames
    maskfiles = args.maskfiles
    if maskfiles and len(maskfiles) < len(filenames):
        if len(maskfiles) == 1:
            maskfiles = [maskfiles] * len(filenames)
        else:
            raise NeXusError('No. of maskfiles must same as no. of filenames or 1')
    elif maskfiles is None:
        maskfiles = [None] * len(filenames)

    scan_directory = os.path.join(sample, label, directory)
    try: 
        os.makedirs(scan_directory)
        for f in filenames:
            os.makedirs(os.path.join(scan_directory, f))
    except Exception:
        pass

    if directory:
        nexus_file = os.path.join(sample, label, sample+'_'+directory+'.nxs')
    else:
        nexus_file = os.path.join(sample, label, sample+'.nxs')
    root = make_nexus_file(sample, label, scan_directory, temperature, 
                           filenames, maskfiles)
    root.save(nexus_file, 'w')
    print 'Saving ', nexus_file
    

if __name__=="__main__":
    main()