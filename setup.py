# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from setuptools import setup

setup(name='usi-test',
      version='1.0',
      description='USI Device Testing Tool',
      long_description='This contains tools for testing USI devices.',
      py_modules=['usi_test'],
      author='Sean O\'Brien',
      author_email='seobrien@chromium.org',
      license='BSD-Google',
      entry_points={
          'console_scripts': [
              'usi-test = usi_test:main'
          ]
      },
      python_requires='>=3.6',
      install_requires=['hid-tools==0.2']
)

