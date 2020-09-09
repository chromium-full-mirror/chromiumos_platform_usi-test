#!/usr/bin/env python3
# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import array
import fcntl
import hidtools.hidraw
import sys
import time

# Definitions from USI HID descriptor spec v1.0
FIELD_BITS = {
    # Get/Set features:
    'color': 8,
    'width': 8,
    # Diagnostic command feature:
    'preamble': 3,
    'index': 3,
    'command_id': 6,
    'command_data': 16,
    'crc': 5
}

# Get/Set feature options
STYLES = ['ink', 'pencil', 'highlighter', 'marker', 'brush', 'none']
ALL_BUTTONS = ['unimplemented', 'barrel', 'secondary', 'eraser', 'disabled']
REAL_BUTTONS = ALL_BUTTONS[1:-1]
ASSIGNABLE_BUTTONS = ALL_BUTTONS[1:]

# Diagnostic command constants
DEFAULT_PREAMBLE = 0x4
CRC_POLYNOMIAL = 0b111001
CRC_POLYNOMIAL_LENGTH = 6

# These usages only appear in a single feature report as defined
# by the USI HID descriptor spec v1.0, so we can use them to
# look for the feature reports.
UNIQUE_REPORT_USAGES = {
    'color': [
        0x5c,  # Preferred color
        0x5d   # Preferred color is locked
    ],
    'width': [
        0x5e,  # Preferred line width
        0x5f   # Preferred line width is locked
    ],
    'style': [
        0x70,  # Preferred line style
        0x71,  # Preferred line style is locked
        0x72,  # Ink
        0x73,  # Pencil
        0x74,  # Highlighter
        0x75,  # Chisel Marker
        0x76,  # Brush
        0x77   # No preferred line style
    ],
    'buttons': [
        0xa4,  # Switch unimplemented
        0x44,  # Barrel switch
        0x5a,  # Secondary barrel switch
        0x45,  # Eraser
        0xa3   # Switch disabled
    ],
    'firmware': [
        0x90,  # Transducer software info
        0x91,  # Transducer vendor ID
        0x92,  # Transducer product ID
        0x2a   # Software version
    ],
    'usi_version': [0x2b],  # Protocol version
    'diagnostic': [0x80],  # Digitizer Diagnostic
    'index': [0xa6]  # Transducer index selector
}

# ioctl definitions from <ioctl.h>
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(dir, type, nr, size):
    return ((dir << _IOC_DIRSHIFT) | (ord(type) << _IOC_TYPESHIFT) |
            (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT))


def HIDIOCSFEATURE(size):
    """ set report feature """
    return _IOC(_IOC_WRITE | _IOC_READ, 'H', 0x06, size)


def HIDIOCGFEATURE(size):
    """ get report feature """
    return _IOC(_IOC_WRITE | _IOC_READ, 'H', 0x07, size)


class FeatureNotFoundError(ValueError):
    pass


def log(message, end='\n'):
    print(message, end=end, file=sys.stderr)
    sys.stderr.flush()


def field_max(field_name):
    return 2**FIELD_BITS[field_name] - 1


def check_in_range(field_name, value):
    min_val = 0
    max_val = field_max(field_name)
    if value < 0 or value > max_val:
        raise ValueError('{} must be between {} and {}, inclusive'.format(
            field_name, min_val, max_val))


def find_feature_report_id(rdesc, feature_name):
    """Find the correct report ID for the feature.

    Search the report descriptor for a usage unique to the desired feature
    report.
    """
    report_id = None
    usage_found = False
    for item in rdesc.rdesc_items:
        if item.item == 'Report ID':
            report_id = item.value
        elif item.item == 'Usage' and item.value in UNIQUE_REPORT_USAGES[
                feature_name]:
            usage_found = True
        elif item.item == 'Feature':
            if usage_found and report_id is not None:
                return report_id
        elif item.item == 'Input' or item.item == 'Output':
            usage_found = False

    raise FeatureNotFoundError(
        'Feature report not found for {}'.format(feature_name))


def set_stylus_index(device, stylus_index):
    """Send a report to set the stylus index.

    Tell the controller which stylus index the next 'get feature' command should
    return information for.
    """
    try:
        report_id = find_feature_report_id(device.report_descriptor, 'index')
    except FeatureNotFoundError as e:
        if stylus_index != 1:
            raise e
        # Some devices only support one stylus and don't have a feature to set
        # the stylus index, so continue with a warning if the desired stylus
        # index is 1.
        log('WARNING: {}. Proceeding regardless.'.format(str(e)))
        return
    buf = array.array('B', [report_id, stylus_index])
    fcntl.ioctl(device.device.fileno(), HIDIOCSFEATURE(len(buf)), buf)


def get_ioctl(device, buf):
    """Send a 'get feature' ioctl to the device and return the results.

    Repeatedly poll until the device gets the information from the stylus,
    so the user can run this before pairing the stylus.
    """
    while fcntl.ioctl(device.device.fileno(), HIDIOCGFEATURE(len(buf)),
                      buf) == 0:
        log('.', end='')
        time.sleep(0.1)
    log('')
    return (buf)


def get_feature(device, stylus_index, feature_name):
    report_id = find_feature_report_id(device.report_descriptor, feature_name)
    set_stylus_index(device, stylus_index)

    buf = array.array('B', [0] * 64)
    buf[0] = report_id

    return get_ioctl(device, buf)


def calculate_crc(data):
    """Calculate the cyclic redundancy check.

    Calculate the cyclic redundancy check for diagnostic commands according to
    the USI spec.
    """
    data_len = (
        FIELD_BITS['command_data'] +
        FIELD_BITS['command_id'] +
        FIELD_BITS['index'])
    crc_polynomial = CRC_POLYNOMIAL
    msb_mask = 1 << (data_len - 1)
    crc_polynomial = crc_polynomial << (data_len - CRC_POLYNOMIAL_LENGTH)
    for _ in range(data_len):
        if data & msb_mask:
            data = data ^ crc_polynomial
        data = data << 1
    return data >> (data_len - (CRC_POLYNOMIAL_LENGTH - 1))


def diagnostic(device, args):
    check_in_range('command_id', args.command_id)
    check_in_range('command_data', args.data)
    check_in_range('index', args.index)
    check_in_range('preamble', args.preamble)

    command = args.data
    command = args.command_id | (command << FIELD_BITS['command_id'])
    command = args.index | (command << FIELD_BITS['index'])

    if args.crc is None:
        crc = calculate_crc(command)
    else:
        crc = args.crc
        check_in_range('crc', args.crc)

    full_command = crc
    full_command = command | (
        full_command << (FIELD_BITS['command_data'] +
                         FIELD_BITS['command_id'] +
                         FIELD_BITS['index']))
    full_command = args.preamble | (full_command << FIELD_BITS['preamble'])
    command_bytes = full_command.to_bytes(8, byteorder='little')

    report_id = find_feature_report_id(device.report_descriptor, 'diagnostic')
    buf = array.array('B', [report_id] + list(command_bytes))

    # Get a feature from the stylus to make sure it is paired.
    get_feature(device, args.index, 'color')
    fcntl.ioctl(device.device.fileno(), HIDIOCSFEATURE(len(buf)), buf)

    return get_ioctl(device, buf)


def format_bytes(buf):
    """Format bytes for printing.

    Given a little-endian list of bytes, format them for printing as a single
    hex value.
    """
    ret = '0x'
    for byte in reversed(buf):
        ret += '{:02x}'.format(byte)
    return ret


def print_diagnostic(buf):
    error_bit = (buf[3] & 0x40) >> 6
    error_code = buf[3] & 0x3f
    print('full response: {}'.format(format_bytes(buf[1:])))
    print('error information available? {}'.format(error_bit))
    print('error code: 0x{:02x}'.format(error_code))
    print('command response: {}'.format(format_bytes(buf[1:3])))


def print_feature(buf, feature_name):
    # The first two elements of buf hold the report ID and stylus index,
    # which do not need to be printed
    if feature_name == 'color':
        print('transducer color: {}'.format(buf[2]))
    elif feature_name == 'width':
        print('transducer width: {}'.format(buf[2]))
    elif feature_name == 'style':
        style = buf[2]
        style_index = style - 1
        if 0 <= style_index < len(STYLES):
            style_name = STYLES[style_index]
        else:
            style_name = str(style)
        print('transducer style: {}'.format(style_name))
    elif feature_name == 'buttons':
        for real_button_index in range(len(REAL_BUTTONS)):
            real_button_name = REAL_BUTTONS[real_button_index]
            virtual_button = buf[2 + real_button_index]
            virtual_button_index = virtual_button - 1
            if 0 <= virtual_button_index < len(ALL_BUTTONS):
                virtual_button_name = ALL_BUTTONS[virtual_button_index]
            else:
                virtual_button_name = str(virtual_button)
            print("'{}' maps to '{}'".format(real_button_name,
                                             virtual_button_name))
    elif feature_name == 'firmware':
        print('vendor ID: {}'.format(format_bytes(buf[2:4])))
        print('product ID: {}'.format(format_bytes(buf[4:12])))
        print('firmware version: {}.{}'.format(buf[12], buf[13]))
    elif feature_name == 'usi_version':
        print('USI version: {}.{}'.format(buf[2], buf[3]))


def set_feature(device, stylus_index, feature_name, value):
    buf = get_feature(device, stylus_index, feature_name)
    buf[2] = value
    fcntl.ioctl(device.device.fileno(), HIDIOCSFEATURE(len(buf)), buf)


def set_number_feature(device, stylus_index, feature_name, value):
    try:
        check_in_range(feature_name, value)
    except ValueError as e:
        log('ERROR: {}. Proceeding with setting other features.'.format(str(e)))
        return
    set_feature(device, stylus_index, feature_name, value)


def set_style_feature(device, stylus_index, style_name):
    style = STYLES.index(style_name) + 1
    set_feature(device, stylus_index, 'style', style)


def set_buttons_feature(device, stylus_index, mappings):
    buf = get_feature(device, stylus_index, 'buttons')
    base_offset = 2
    for real_button_name, virtual_button_name in mappings:
        offset = base_offset + REAL_BUTTONS.index(real_button_name)
        virtual_button = ALL_BUTTONS.index(virtual_button_name) + 1
        buf[offset] = virtual_button
    fcntl.ioctl(device.device.fileno(), HIDIOCSFEATURE(len(buf)), buf)


def set_features(device, args):
    if args.color is not None:
        set_number_feature(device, args.index, 'color', args.color)
    if args.width is not None:
        set_number_feature(device, args.index, 'width', args.width)
    if args.style is not None:
        set_style_feature(device, args.index, args.style)
    if args.buttons is not None:
        set_buttons_feature(device, args.index, args.buttons)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description='USI test tool',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '-p',
        '--path',
        default='/dev/hidraw0',
        help='the path of the USI device')
    subparsers = parser.add_subparsers(dest='command')

    descriptor_help = 'print the report descriptor in human-readable format'
    subparsers.add_parser(
        'descriptor', help=descriptor_help, description=descriptor_help)

    data_help = 'print incoming data reports in human-readable format'
    subparsers.add_parser('data', help=data_help, description=data_help)

    parser_get = subparsers.add_parser(
        'get',
        help='read a feature from a stylus',
        description='Send a HID feature request to the device and print the '
        'response. Will wait for the specified stylus index to pair first.')
    parser_get.add_argument(
        'feature',
        choices=[
            'color', 'width', 'style', 'buttons', 'firmware', 'usi_version'
        ],
        help='which feature to read')
    parser_get.add_argument(
        '-i',
        '--index',
        default=1,
        type=int,
        help='which stylus to read values for, by index '
        '(default: %(default)s)')

    parser_set = subparsers.add_parser(
        'set',
        help='change stylus features',
        description='Set a stylus feature via HID feature report. Will wait '
        'for the specified stylus index to pair first.')
    parser_set.add_argument(
        '-i',
        '--index',
        default=1,
        type=int,
        help='which stylus to set values for, by index (default: %(default)s)')
    parser_set.add_argument(
        '-c',
        '--color',
        type=int,
        metavar='[{},{}]'.format(0, field_max('color')),
        help='set the stylus color to the specified value '
        'in the range ({},{})'.format(0, field_max('color')))
    parser_set.add_argument(
        '-w',
        '--width',
        type=int,
        metavar='[{},{}]'.format(0, field_max('width')),
        help='set the stylus width to the specified value '
        'in the range ({},{})'.format(0, field_max('width')))
    parser_set.add_argument(
        '-s',
        '--style',
        choices=STYLES,
        help='set the stylus style to the specified value')
    parser_set.add_argument(
        '-b',
        '--button',
        dest='buttons',
        nargs=2,
        action='append',
        metavar=('{%s}' % ','.join(REAL_BUTTONS),
                 '{%s}' % ','.join(ASSIGNABLE_BUTTONS)),
        help='Remap a button to emulate another. '
        'For example: \'usi-test set -b barrel eraser\' will set the '
        'barrel button to act as an eraser button. You may set multiple '
        'buttons by setting this option multiple times.')

    parser_diag = subparsers.add_parser(
        'diagnostic',
        help='send a diagnostic command',
        description='Send the specified diagnostic command to a stylus and '
        'read the response. You may set the preamble and cyclic redundancy '
        'check manually, but by default the tool will send the preamble and '
        'CRC defined by the USI spec.')
    parser_diag.add_argument(
        'command_id', type=int, help='which diagnostic command to send')
    parser_diag.add_argument(
        'data', type=int, help='the data to send with the diagnostic command')
    parser_diag.add_argument(
        '-i',
        '--index',
        default=1,
        type=int,
        help='which stylus to send command to, by index (default: %(default)s)')
    parser_diag.add_argument(
        '-c',
        '--crc',
        type=int,
        help='the custom cyclic redundancy check for the diagnostic command. '
        'By default this tool will send the appropriate crc calculated by the '
        'algorithm defined in the USI spec.')
    parser_diag.add_argument(
        '-p',
        '--preamble',
        type=int,
        default=DEFAULT_PREAMBLE,
        help='the preamble for the diagnostic command. '
        'By default this tool will send the preamble defined in the USI spec')

    args = parser.parse_args()

    return args


def main():
    args = parse_arguments()

    fd = open(args.path, 'rb+')
    device = hidtools.hidraw.HidrawDevice(fd)

    if args.command == 'descriptor':
        device.dump(sys.stdout)
    elif args.command == 'data':
        while True:
            device.dump(sys.stdout)
            device.read_events()
    elif args.command == 'get':
        buf = get_feature(device, args.index, args.feature)
        print_feature(buf, args.feature)
    elif args.command == 'set':
        set_features(device, args)
    elif args.command == 'diagnostic':
        buf = diagnostic(device, args)
        print_diagnostic(buf)


if __name__ == '__main__':
    main()
