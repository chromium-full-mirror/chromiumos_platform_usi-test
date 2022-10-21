# USI pen battery test tool

[TOC]

## Introduction

This repository contains a tool for testing battery performance and reporting
of a [Universal Stylus Initiative][usi](USI) stylus. It will output csv (raw data)
and html (analysis plots) files.


## Setting up

Make sure that your device has a test image. You can use [cros flash]
to flash a test image. Then you will need to enter the [command prompt].
The following Python modules need to be installed:
  * pyvisa
  * pyvisa-py

Finally, you can run the tool with the `python pen_battery_test.py` command.

## Commands

`pen_battery_test.py` has five arguments: `name`, `--path`, `--event`, `--max_voltage`, and
`--steps`.

### name

This argument is positional and required. It is used for results and output
file naming.

example:
```sh
$ python pen_battery_test.py name_of_stylus
```

### --path (or -p)

This argument sets the path used by the use-test tool to communicate with the
stylus. Default is /dev/hidraw0.

example:
```sh
$ python pen_battery_test.py name_of_stylus -p /dev/hidraw0
```

### --event (or -e)

This argument sets the input event number (X) used by evtest in the form
/dev/input/eventX. Default is 3.

example:
```sh
$ python pen_battery_test.py name_of_stylus -e 3
```

### --max_voltage (or -m)

This argument sets the max voltage to be applied to the stylus being tested
and takes a limited number of values: [1.5, 2.7. 3.7]. Default is 1.5.

example:
```sh
$ python pen_battery_test.py name_of_stylus -m 3.7
```

### --steps (or -s)

This argument sets the number of voltage steps to be tested in descending
order from the value set with the --max_voltage argument to 0.9 volts.
Default is 35.

examples:
```sh
$ python pen_battery_test.py name_of_stylus -s 20
```

[command prompt]: https://chromium.googlesource.com/chromiumos/docs/+/HEAD/developer_mode.md#shell
[cros flash]: https://chromium.googlesource.com/chromiumos/docs/+/HEAD/cros_flash.md
[usi]: https://universalstylus.org/
