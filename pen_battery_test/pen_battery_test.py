# Copyright 2022 The ChromiumOS Authors.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

""" Stylus Battery Test

"""

import argparse
from E3633A_test import E3633ATest


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("name",
                        help="Name of stylus being tested")
    parser.add_argument("-p", "--path",
                        default="/dev/hidraw0",
                        help="Path to stylus used by usi-test tool")
    parser.add_argument("-e", "--event",
                        default="3",
                        help="Event number of stylus, /dev/input/eventX")
    parser.add_argument("-m", "--max_voltage",
                        default="1.5",
                        help="Maximum voltage to be tested")
    parser.add_argument("-n", "--min_voltage",
                        default="0.9",
                        help="Minimum voltage to be tested, stylus should lose power")
    parser.add_argument("-s", "--steps", type=int,
                        default=35,
                        help="Number of voltage steps to be tested")
    args = parser.parse_args()

    test = E3633ATest(float(args.max_voltage), float(args.min_voltage), args.steps, args.name, args.path, args.event)

    if test.battery_dir == None:
        print("Aborting: /sys/class/power_supply does not contain a directory for the stylus battery.")
        return

    test.run()

    test.raw_data_to_csv()

    test.plot_data()


if __name__ in "__main__":
    main()
