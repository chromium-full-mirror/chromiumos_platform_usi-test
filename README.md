# USI Test Tool

[TOC]

## Introduction

This repository contains a tool for testing a [Universal Stylus Initiative][usi]
(USI) digitizer and stylus at the HID protocol level.


## Setting up

Make sure that your device has a dev or test image. You can use [cros flash]
to flash a test image. Then you will need to enter the [command prompt].
Finally, you can run the tool with the `usi-test` command.

## Commands

`usi-test` has five subcommands: `descriptor`, `data`, `get`, `set`, and
`diagnostic`.

For any of these commands, you can set the path to the device with the `-p` or
`--path` option. The default device path is `/dev/hidraw0`.

### descriptor

This command simply prints out the report descriptor in human-readable format.

example:
```sh
$ usi-test --path /dev/hidraw0 descriptor
```

### data

This command prints incoming events from the device in human-readable format.
You can quit with `Ctl-C`.

example:
```sh
$ usi-test -p /dev/hidraw0 data
```

### get

This command sends a HID feature request to the device and prints the response.
All of these commands query a stylus paired with the device, which can be
specified with the `-i` or `--index` option. This is the temporary index
assigned to the stylus by the digitizer when it pairs. The default index is 1.

This command will wait for the specified stylus to pair with the digitizer, so
you can send the command without having the stylus pressed to the digitizer.

The values you can query are:
* `color`: The current 8-bit indexed color of the stylus stroke
* `color24': The current 24-bit color of the stylus stroke
* `width`: The current width of the stylus stroke
* `style`: The current style of the stylus stroke (ink, pencil, etc.)
* `buttons`: The mapping from physical stylus buttons to the button types they
  are set to be emulating.
* `firmware`: The vendor ID, product ID, serial number, and firmware version of the stylus.
* `usi_version`: The USI protocol version this pen is using.

examples:
```sh
$ usi-test -p /dev/hidraw0 get color
$ usi-test get width --index 1
$ usi-test get buttons -i 2
```

### set

This command sets the specified values for the stylus using a HID feature
report. The stylus index is specified the same as with the `get` command.

This command will wait for the specified stylus to pair with the digitizer, so
you can send the command without having the stylus pressed to the digitizer.

The values you can set are:
* `--color`, `-c`: Values between 0 and 255. See the USI spec for color mapping.
  (255 specifies no preference.)
* `--color24`, `-C`: Values between 0x000000 and 0xFFFFFF, as 0xRRGGBB.
* `--color24-no-preference`: Used in conjunction with `--color24`, indicates
  that there is no preferred color.
* `--width`, `-w`: Values between 0 and 255, in 0.1 mm increments. For example,
  A value of 25 gives a stroke width of 2.5 mm. (255 specifies no preference.)
* `--style`, `-s`: Choose from ink, pencil, highlighter, marker, brush, and none.
  ('none' indicates no preferred style)
* `--button`, `-b`: This option requires two arguments. It will set the first
  button argument to emulate the second button argument. The first (physical)
  button may be (barrel, secondary, eraser/tail), and the second (emulated) button
  may be (barrel, secondary, eraser, disabled)

You may set one or all of the values in the same command. The button option
can be set once for each physical button.

examples:
```sh
$ usi-test set --color 50
$ usi-test set -c 50
$ usi-test set --style pencil --index 2
$ usi-test set -c 50 -w 20 -s pencil
  # set the barrel button to act as the secondary button:
$ usi-test set --button barrel secondary
$ usi-test set --button barrel secondary --button secondary eraser
```

### diagnostic

This command sends a diagnostic command to the stylus and reads the response. If
you want you can set the preamble and cyclic redundancy check (CRC) manually,
but by default this tool will calculate and send the preamble and CRC defined by
the USI spec. The stylus index can be set the same as with the `get` command.

This command expects the command ID as the first argument, and the data as the
second argument.

It will print the full response from the stylus, and break the response into the
error bit, error code, and command response.

If used with a display that requires USI 2.0 flex beacons, add the `--flex` argument.

examples:
```sh
  # send the echo command, with value 8
$ usi-test diagnostic 52 8
full response: 0x0000000000000008
error information available? 0
error code: 0x00
command response: 0x0008
$ usi-test diagnostic 52 7 --preamble 2 --crc 3
```

[command prompt]: https://chromium.googlesource.com/chromiumos/docs/+/HEAD/developer_mode.md#shell
[cros flash]: https://chromium.googlesource.com/chromiumos/docs/+/HEAD/cros_flash.md
[usi]: https://universalstylus.org/
