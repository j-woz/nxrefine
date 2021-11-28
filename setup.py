#!/usr/bin/env python
#-----------------------------------------------------------------------------
# Copyright (c) 2013-2017, NeXpy Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING, distributed with this software.
#-----------------------------------------------------------------------------

#from distutils.core import setup, Extension
from setuptools import setup, find_packages, Extension

import numpy
import os
import sys
import versioneer

sys.path.insert(0, os.path.join('src', ))
import nxrefine
import nxrefine.requires

verbose=1

setup (name = nxrefine.__package_name__, 
       version=versioneer.get_version(),
       cmdclass=versioneer.get_cmdclass(),
       license = nxrefine.__license__,
       description = nxrefine.__description__,
       long_description = nxrefine.__long_description__,
       author=nxrefine.__author_name__,
       author_email=nxrefine.__author_email__,
       platforms='any',
       install_requires = nxrefine.requires.pkg_requirements,
       package_dir = {'': 'src'},
       packages = find_packages('src'),
       include_package_data = True,
       package_data = {'nxrefine' : ['julia/*.jl']},
       ext_modules=[Extension('nxrefine.connectedpixels', 
                        ['src/nxrefine/connectedpixels.c',
                         'src/nxrefine/blobs.c'],
                        depends=['src/nxrefine/blobs.h'],
                        include_dirs=[numpy.get_include()]),
                    Extension("nxrefine.closest", 
                        ['src/nxrefine/closest.c'], 
                        include_dirs=[numpy.get_include()])],
       entry_points={
            # create & install scripts in <python>/bin
            'console_scripts': ['nxparent=nxrefine.scripts.nxparent:main',
                                'nxcopy=nxrefine.scripts.nxcopy:main',
                                'nxlink=nxrefine.scripts.nxlink:main',
                                'nxfind=nxrefine.scripts.nxfind:main',
                                'nxmax=nxrefine.scripts.nxmax:main',
                                'nxrefine=nxrefine.scripts.nxrefine:main',
                                'nxprepare=nxrefine.scripts.nxprepare:main',
                                'nxtransform=nxrefine.scripts.nxtransform:main',
                                'nxcombine=nxrefine.scripts.nxcombine:main',
                                'nxpdf=nxrefine.scripts.nxpdf:main',
                                'nxreduce=nxrefine.scripts.nxreduce:main',
                                'nxqueue=nxrefine.scripts.nxqueue:main',
                                'nxserver=nxrefine.scripts.nxserver:main',
                                'nxlogger=nxrefine.scripts.nxlogger:main',
                                'nxwatcher=nxrefine.scripts.nxwatcher:main',
                                'nxsummary=nxrefine.scripts.nxsummary:main',
                                'nxdatabase=nxrefine.scripts.nxdatabase:main',
                                'nxsum=nxrefine.scripts.nxsum:main',
                                'nxshrink=nxrefine.scripts.nxshrink:main',
                                'nxrestore=nxrefine.scripts.nxrestore:main'],
       },
       classifiers= ['Development Status :: 4 - Beta',
                     'Intended Audience :: Science/Research',
                     'License :: OSI Approved :: BSD License',
                     'Programming Language :: Python',
                     'Programming Language :: Python :: 2',
                     'Programming Language :: Python :: 2.7',
                     'Programming Language :: Python :: 3',
                     'Programming Language :: Python :: 3.5',
                     'Programming Language :: Python :: 3.6',
                     'Programming Language :: Python :: 3.7',
                     'Topic :: Scientific/Engineering']
      )

