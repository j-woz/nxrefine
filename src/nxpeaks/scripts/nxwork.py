
'''
This script should be run with PWD as the top-level data directory
'''

import argparse, os, subprocess
import numpy as np

from nexusformat.tools.stack import nxstack

def crash(msg):
    print msg
    exit(1)

def main():

    print "nxwork..."

    parser = argparse.ArgumentParser(
        description="Perform workflow for scan")
    parser.add_argument('-s', '--sample', help='sample name')
    parser.add_argument('-l', '--label', default='', help='sample label')
    parser.add_argument('-d', '--directory', default='', help='scan directory')
    parser.add_argument('-t', '--temperature', help='temperature of scan')
    parser.add_argument('-f', '--filenames', default=['f1', 'f2', 'f3'],
        nargs='+', help='names of NeXus files to be linked to this file')
    parser.add_argument('-m', '--maskfile', default='pilatus_mask.nxs',
        help='name of the pixel mask file')
    parser.add_argument('-p', '--parent', help='file name of file to copy from')

    args = parser.parse_args()

    sample = args.sample
    label = args.label
    directory = args.directory
    temperature = np.float32(args.temperature)
    files = args.filenames
    parent = args.parent

    label_path = '%s/%s' % (sample, label)
    wrapper_file = '%s/%s_%s.nxs' % (label_path, sample, directory)
    print "wrapper file:", wrapper_file

    if sample == None:
        crash('Requires sample!')
    if not os.path.exists(label_path):
        crash("Label does not exist: "+label_path)

    if not os.path.exists(wrapper_file):
        setup_command = 'nxsetup -s %s -l %s -d %s -t %s -f %s' \
                        % (sample, label, directory, temperature, ' '.join(files))

    for f in files:
        path = '%s/%s/%s/%s' % (sample, label, directory, f)
        print "calling nxstack"
        nxstack(directory, prefixes=['scan'], extension='cbf',
                output='%s.nxs'%output,
        print "calling nxlink"
        subprocess.call('nxlink -s %s -l %s -d %s -f %s -m pilatus_mask.nxs'
                        % (sample, label, directory, f))
        subprocess.call('nxmax -d %s -f %s -p %s/data'
                        % (label_path, wrapper_file, f))
        subprocess.call('nxfind -d %s -f %s -p %s/data -s 500 -e 1000'
                        % (label_path, wrapper_file, f))

    if parent:
        subprocess.call('nxcopy -f %s/%s -o %s'
                        % (label_path, parent, wrapper_file))

if __name__=="__main__":
    main()
