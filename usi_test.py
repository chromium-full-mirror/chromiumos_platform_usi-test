#!/usr/bin/env python3
# Copyright 2020 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import array
import collections
import fcntl
import hidtools.hidraw
from hidtools.hut import HUT
import sys
import time

# Note: someday it might be useful to convert this code into using the
# hid-tools ability to parse descriptors, pack and unpack records. However
# for the moment with hid-tools 0.2, the HID parser does not represent the
# 'buttons' feature well enough to be usable, as the 'buttons' turn into
# three indistinguishable fields named after 'Switch Unimplemented', instead
# of a 'Barrel Switch', 'Secondary Barrel Switch', and 'Eraser'.

# Fill in usage names that hid-tools may not have, yet.

RECENT_DIGITIZER_USAGES = {
    # Not actually standardized, yet.
    0xA6: "Transducer Index Selector",
    # Only ratified in HUT 1.3.
    0x6E: "Transducer Serial Number Part 2",
    0x6F: "No preferred Color",
}
DIGITIZER_USAGE_PAGE = 0x0D

for usage, description in RECENT_DIGITIZER_USAGES.items():
    if usage not in HUT[DIGITIZER_USAGE_PAGE]:
        HUT[DIGITIZER_USAGE_PAGE][usage] = description

# Definitions from USI HID descriptor spec v1.0 & v2.0
FIELD_BITS = {
    # Get/Set features:
    'color': 8,
    'color24': 24,
    'width': 8,
    # Diagnostic command feature:
    'preamble': 3,
    'index': 3,
    'command_id': 6,
    'command_data': 16,
    'crc': 5,
}

# Expected report lengths for either USI HID spec v1.0 or v2.0.
EXPECTED_FEATURE_REPORT_LENGTHS = {
    'color': [4],
    'color24': [6],
    'width': [4],
    'style': [4],
    'buttons': [5],
    'firmware': [14, 20], # Different length for USI 1.0 vs 2.0.
    'usi_version': [4],
}
DIAGNOSTIC_FEATURE_REPORT_LENGTH = 9

# Meanings of EIA bit for Diagnostic HID feature
DIAGNOSTIC_ERROR_INFORMATION_AVAILABLE = {
    0: "No error: ignore error code",
    1: "Error: error code contains REJECT reason",
}

# Defined error codes for Diagnostic HID feature
DIAGNOSTIC_ERROR_CODES = {
    0: "Unknown Error",
    1: "Unsupported Command/SubCommand",
    2: "Undefined Command-ID/SubCommand-ID",
    3: "Invalid Parameters",
    4: "Unable to respond due to no assigned data slots",
    5: "Soft Reset",
}
DEFAULT_DIAGNOSTIC_ERROR_CODE = "Reserved"

# Get/Set feature options
STYLES = {
    'ink': 1,
    'pencil': 2,
    'highlighter': 3,
    'marker': 4,
    'brush': 5,
    'none': 6,
}

# Ordered to prefer 'tail' over 'eraser', as USI 2.0 does.
PHYSICAL_BUTTONS = collections.OrderedDict(
    barrel=1,
    secondary=2,
    tail=3,
    eraser=3,
)

# These are the functions that can actually be assigned to buttons; for the
# most part the names are the same, but the numerical values and meanings
# are different enough that we will keep them separate.
BUTTON_ASSIGNMENTS = {
    'unimplemented': 1,
    'barrel': 2,
    'secondary': 3,
    'eraser': 4,
    'disabled': 5,
}

# Diagnostic command constants
DEFAULT_PREAMBLE = 0x4
DEFAULT_PREAMBLE_FLEX = 0x1  # For USI 2.0 flex beacons
DEFAULT_DELAY_MS = 0
DEFAULT_DELAY_FLEX_MS = 200  # delay for diagnostics
CRC_POLYNOMIAL = 0b111001
CRC_POLYNOMIAL_LENGTH = 6

# These usages only appear in a single feature report as defined by the USI
# HID descriptor specs v1.0 & v2.0, so we can use them to search for the
# appropriate feature reports. Each report has a list of relevant usages.
# When find_feature_report_id() searches the reports, if any feature field
# of that usage is found, that is considered a match. The usage page is 0x0D
# if not present, while the size is ignored unless specified.
# A match against a logical collection (instead of a feature field) is
# specified with 'collection'.

UNIQUE_REPORT_USAGES = {
    'color': [ # Classic 1.0 8-bit indexed color
        # Preferred color, must be 8-bits
        {'usage': 0x5c, 'size': 8},
    ],
    'color24': [ # Additional RGB report for for USI 2.0
        # Preferred color, must be 24-bits
        {'usage': 0x5c, 'size': 24},
    ],
    'width': [
        {'usage': 0x5e},  # Preferred line width
        {'usage': 0x5f},  # Preferred line width is locked
    ],
    'style': [
        {'usage': 0x70},  # Preferred line style
        {'usage': 0x71},  # Preferred line style is locked
        {'usage': 0x72},  # Ink
        {'usage': 0x73},  # Pencil
        {'usage': 0x74},  # Highlighter
        {'usage': 0x75},  # Chisel Marker
        {'usage': 0x76},  # Brush
        {'usage': 0x77},  # No preferred line style
    ],
    'buttons': [
        {'usage': 0xa4},  # Switch unimplemented
        {'usage': 0x44},  # Barrel switch
        {'usage': 0x5a},  # Secondary barrel switch
        {'usage': 0x45},  # Eraser
        {'usage': 0xa3},  # Switch disabled
    ],
    'firmware': [
        {'usage': 0x90},  # Transducer software info
        {'usage': 0x91},  # Transducer vendor ID
        {'usage': 0x92},  # Transducer product ID
        {'usage': 0x2a},  # Software version
    ],
    'usi_version': [
        # Protocol version, Generic Device Controls
        {'page': 0x06,
         'usage': 0x2b,
         'collection': True},
    ],
    'diagnostic': [{ 'usage': 0x80}],  # Digitizer Diagnostic
    'index-2': [{'usage': 0xa6}],  # Transducer index selector for USI 2.0
    'index-1': [{'usage': 0x38}],  # Transducer Index for USI 1.0; this usage
                                   # is not unique: see set_stylus_index below
                                   # for more info.
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

# global modified by arg parsing

verbose = False

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
    if value < min_val or value > max_val:
        raise ValueError('{} must be between {} and {}, inclusive'.format(
            field_name, min_val, max_val))

def dict_lookup(d, desired_value):
    for key, value in d.items():
        if value == desired_value:
            return key
    return None

def easy_HIDIOCSFEATURE(device, buf):
    result = fcntl.ioctl(device.device.fileno(), HIDIOCSFEATURE(len(buf)), buf)
    if verbose:
        print("HIDIOCSFEATURE(len {}: {}) = result {}".format(len(buf), format_bytes_ws(buf), result))

def easy_HIDIOCGFEATURE(device, buf):
    original_length = len(buf)
    result = fcntl.ioctl(device.device.fileno(), HIDIOCGFEATURE(len(buf)), buf)
    # Trim result to actual response length
    if result >= 0:
        buf = buf[0:result]
    else:
        buf = []
    if verbose:
        print("HIDIOCGFEATURE(len {}) = result {}: {}".format(original_length, result, format_bytes_ws(buf)))
    return (result, buf)

def match_unique_usage(rdesc_item, state, feature_name):
    for match in UNIQUE_REPORT_USAGES[feature_name]:
        if 'collection' not in match and rdesc_item.item != 'Feature':
            continue
        if 'collection' in match and rdesc_item.item != 'Collection':
            continue
        if match['usage'] != state['usage']:
            continue
        if 'page' not in match and state['page'] != DIGITIZER_USAGE_PAGE:
            continue
        if 'page' in match and state['page'] != match['page']:
            continue
        if 'size' in match and state['size'] != match['size']:
            continue
        return True
    return False

def find_feature_report_id(rdesc, feature_name, max_fields=None):
    """Find the correct report ID for the feature.

    Search the report descriptor for a usage unique to the desired feature
    report.

    If max_fields is set, only match a feature which has <= Usages
    than max_fields.
    """

    # Implement a very crude HID descriptor parser to look for usages in
    # reports; it does not understand collections at all. A sentinel None is
    # used to finish processing the last report.

    # TODO: Implement push/pop if we find a descriptor that needs them.

    # Prepare for first report
    state = {
        'id': None,
        'size': None,
        'count': 1,
        'usage': None,
        'page': None
    }
    field_count = None
    usage_found = False

    for item in rdesc.rdesc_items + [None]:
        if item == None or item.item == 'Report ID':
            # Check for match at end of each report and at the end of the descriptor.
            if (usage_found and state['id'] is not None
                and (max_fields == None or field_count <= max_fields)):
                return state['id']
            if item is None:
                break
            # Prepare for processing next report
            state['id'] = item.value
            field_count = 0
            usage_found = False
        elif item.item == 'Report Size':
            state['size'] = item.value
        elif item.item == 'Report Count':
            state['count'] = item.value
        elif item.item == 'Usage':
            state['usage'] = item.value
        elif item.item == 'Usage Page':
            state['page'] = item.value
        elif item.item == 'Feature':
            field_count = field_count + state['count']
            if match_unique_usage(item, state, feature_name):
                usage_found = True
        elif item.item == 'Collection':
            if match_unique_usage(item, state, feature_name):
                usage_found = True
        elif item.item == 'Input' or item.item == 'Output':
            # Not a report we are interested in.
            usage_found = False
            field_count = 0
            state['id'] = None

    raise FeatureNotFoundError(
        'Feature report not found for {}'.format(feature_name))


def set_stylus_index(device, stylus_index):
    """Send a report to set the stylus index.

    Tell the controller which stylus index the next 'get feature' command should
    return information for.
    """

    # There is a variation between USI 1.0 spec, USI 2.0 spec: USI 1.0
    # specifies 0x38 Usage(Transducer Index) for the SET_TRANSDUCER feature
    # report, while 2.0 specifies 0xA6 Usage(Transducer Index Selector). If
    # we cannot find the unique 0xA6 Usage, look up 0x38, with a report that
    # has a single field (all stylus features have 0x38 Usages, we need a
    # report that has only one field, Usage(0x38).)
    #
    # Note that 0xA6 Usage(Transducer Index Selector) was not a HID HUT
    # standard as of the time of writing this comment
    try:
        report_id = find_feature_report_id(device.report_descriptor, 'index-2')
    except FeatureNotFoundError as e:
        try:
            report_id = find_feature_report_id(device.report_descriptor, 'index-1', max_fields=1)
        except FeatureNotFoundError as e2:
            if stylus_index != 1:
                raise e
            # Some devices only support one stylus and don't have a feature
            # to set the stylus index, so continue with a warning if the
            # desired stylus index is 1.
            log('Note: {} and {}. Not setting stylus index; this is likely harmless.'
                .format(str(e), str(e2)))
            return

    buf = array.array('B', [report_id, stylus_index])
    easy_HIDIOCSFEATURE(device, buf)


def get_feature_ioctl(device, buf):
    """Send a 'get feature' ioctl to the device and return the results.

    Repeatedly poll until the device gets the information from the stylus,
    so the user can run this before pairing the stylus.
    """
    # TODO: Someday test if we get useful results with a non-one stylus
    # index and a pen that is paired with the screen after we start trying
    # to read the feature.

    original_length = len(buf)
    output_buf = None

    # Note: some firmware likes to cache values and will return features
    # immediately, even if there is no pen present, while others return a
    # zero-length report in that case. Consider some other technique (such
    # as looking for pen presence in input reports?) to implement the delay.

    while True:
        (result, output_buf) = easy_HIDIOCGFEATURE(device, buf)
        if result != 0:
            break
        log('.', end='')
        time.sleep(0.1)
    log('')
    return output_buf


def get_feature(device, stylus_index, feature_name):
    report_id = find_feature_report_id(device.report_descriptor, feature_name)
    set_stylus_index(device, stylus_index)

    # In theory we have enough info to only request the right amount of
    # data, but we prefer to ask for as much as possible to see the actual
    # returned length.
    buf = array.array('B', [0] * 64)
    buf[0] = report_id

    buf = get_feature_ioctl(device, buf)

    # Verify length
    if len(buf) not in EXPECTED_FEATURE_REPORT_LENGTHS[feature_name]:
        log('Warning: unexpected length for {} report, expected {} bytes, but got {}: {}'.format(
                feature_name,
                ' or '.join(str(x) for x in EXPECTED_FEATURE_REPORT_LENGTHS[feature_name]),
                len(buf),
                format_bytes_ws(buf)))
        min_len = min(EXPECTED_FEATURE_REPORT_LENGTHS[feature_name])
        # Extend buf to minimum to prevent crashes down the line
        if len(buf) < min_len:
            buf.extend([0] * (min_len - len(buf)))

    return buf

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

    if args.preamble is None:
        args.preamble = DEFAULT_PREAMBLE_FLEX if args.flex else DEFAULT_PREAMBLE

    if args.delay is None:
        args.delay = DEFAULT_DELAY_FLEX_MS if args.flex else DEFAULT_DELAY_MS

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
    easy_HIDIOCSFEATURE(device, buf)

    if args.delay > 0:
        time.sleep(args.delay / 1000)

    return get_feature_ioctl(device, buf)


def format_bytes(buf):
    """Format bytes for printing as a numeric hex value.

    Given a little-endian list of bytes, format them for printing as a single
    numeric hex value.
    """
    ret = '0x'
    for byte in reversed(buf):
        ret += '{:02x}'.format(byte)
    return ret

def format_bytes_ws(buf):
    """Format bytes for printing as a list of hex bytes.

    Given a  list of bytes, format them for printing as whitespace
    separated hex byte values.
    """
    ret = ' '.join(['{:02x}'.format(value) for value in buf])
    return ret

def print_diagnostic(buf):
    if len(buf) != DIAGNOSTIC_FEATURE_REPORT_LENGTH:
        log('Warning: unexpected length for diagnostic report, '
            'expected {} bytes, but got {}: {}'.format(
                DIAGNOSTIC_FEATURE_REPORT_LENGTH,
                len(buf),
                format_bytes_ws(buf)))
    eia = (buf[3] & 0x40) >> 6
    error_code = buf[3] & 0x3f
    print('full response: {}'.format(format_bytes_ws(buf[1:])))
    print('error information available? {} ({})'.format(eia,
              DIAGNOSTIC_ERROR_INFORMATION_AVAILABLE.get(eia)))
    print('error code: 0x{:02x} ({}, {})'.format(error_code, error_code,
              DIAGNOSTIC_ERROR_CODES.get(error_code, DEFAULT_DIAGNOSTIC_ERROR_CODE)))
    print('command response: {}'.format(format_bytes_ws(buf[1:3])))

def get_style_name(style):
    return (dict_lookup(STYLES, style)
            or "unknown ({})".format(style))

def print_feature(buf, index, feature_name):
    if index != buf[1]:
        log("Warning: result is for stylus {}, not requested stylus {}".format(
            buf[1], index))

    if feature_name == 'color':
        print('transducer color: stylus indexed color={}, locked={}'.format(
            buf[2], buf[3] & 1))
    elif feature_name == 'color24':
        print('transducer color24: stylus RGB={}, no preference={}, locked={}'.format(
              format_bytes(buf[2:5]), buf[5] & 1, buf[5] & 2))
    elif feature_name == 'width':
        print('transducer width: stylus width={}, locked={}'.format(
              buf[2], buf[3] & 1))
    elif feature_name == 'style':
        print('transducer style: stylus style={} locked={}'.format(
              get_style_name(buf[2]),
              buf[3] & 1))
    elif feature_name == 'buttons':
        base_offset = 2
        for physical_button_index in [1,2,3]:
            physical_button_name = dict_lookup(PHYSICAL_BUTTONS, physical_button_index)
            button_assignment_index = buf[base_offset + physical_button_index - 1]
            button_assignment_name = (dict_lookup(BUTTON_ASSIGNMENTS, button_assignment_index)
                                      or "unknown")
            print("Physical button '{}' maps to assignment '{}' ({})".format(
                  physical_button_name,
                  button_assignment_name,
                  button_assignment_index))
    elif feature_name == 'firmware':
        if len(buf) == 20: # Format for USI 2.0
            print('Transducer Serial Number (64-bit of SN): {}'.format(format_bytes(buf[2:10])))
            print('Transducer Serial Number Part 2 (redundant high 32-bit of SN): {}'.format(
                  format_bytes(buf[10:14])))
            print('Transducer Vendor ID: {}'.format(format_bytes(buf[14:16])))
            print('Transducer Product ID: {}'.format(format_bytes(buf[16:18])))
            print('Software Version (Major.Minor): {}.{}'.format(buf[18], buf[19]))
        elif len(buf) == 14: # Format for USI 1.0
            print('Transducer Vendor ID (high 12-bit of SN): {}'.format(format_bytes(buf[2:4])))
            print('Transducer Serial Number (low 52-bit of SN): {}'.format(format_bytes(buf[4:12])))
            print('Software Version (Major.Minor): {}.{}'.format(buf[12], buf[13]))
    elif feature_name == 'usi_version':
        print('USI version (Major.Minor): {}.{}'.format(buf[2], buf[3]))


def set_feature(device, stylus_index, feature_name, value):
    buf = get_feature(device, stylus_index, feature_name)
    buf[2] = value
    easy_HIDIOCSFEATURE(device, buf)

def set_number_feature(device, stylus_index, feature_name, value):
    try:
        check_in_range(feature_name, value)
    except ValueError as e:
        log('ERROR: {}. Proceeding with setting other features.'.format(str(e)))
        return
    set_feature(device, stylus_index, feature_name, value)


def set_style_feature(device, stylus_index, style_value):
    set_feature(device, stylus_index, 'style', style_value)

def set_buttons_feature(device, stylus_index, mappings):
    buf = get_feature(device, stylus_index, 'buttons')
    base_offset = 2
    for physical_button_value, button_assignment_value in mappings:
        # These values were already validated by the arg parser
        # to be ints in the valid ranges or text of the appropriate
        # choices.
        if physical_button_value in PHYSICAL_BUTTONS:
            physical_button_value = PHYSICAL_BUTTONS[physical_button_value]
        if button_assignment_value in BUTTON_ASSIGNMENTS:
            button_assignment_value = BUTTON_ASSIGNMENTS[button_assignment_value]

        # Goal is that a physical_button_value of 'barrel' should
        # become offset 2.
        offset = base_offset + physical_button_value -1
        buf[offset] = button_assignment_value
    easy_HIDIOCSFEATURE(device, buf)

def set_color24_feature(device, stylus_index, color24, no_preferred):
    # Note: while setting an RGB color with the no_preference flag set
    # should always leave the RGB color set to 0x000000 within the stylus,
    # we don't prevent choosing other colors, for the sake of testing the
    # HID interface.
    buf = get_feature(device, stylus_index, 'color24')
    buf[2] = (color24 >> 0) & 0xFF
    buf[3] = (color24 >> 8) & 0xFF
    buf[4] = (color24 >> 16) & 0xFF
    buf[5] = 1 if no_preferred else 0
    easy_HIDIOCSFEATURE(device, buf)

def set_features(device, args):
    if args.color is not None:
        set_number_feature(device, args.index, 'color', args.color)
    if args.color24 is not None:
        set_color24_feature(device, args.index, args.color24, args.color24_no_preference)
    if args.width is not None:
        set_number_feature(device, args.index, 'width', args.width)
    if args.style is not None:
        set_style_feature(device, args.index, args.style)
    if args.buttons is not None:
        set_buttons_feature(device, args.index, args.buttons)


def parse_arguments():

    # define a value usable for type= which will parse 0x and 0b integer
    # syntax, and produce sensible output in argparse error messages for failed
    # argument parsing.
    int_anybase = lambda x: int(x, 0)
    int_anybase.__name__ = "int (accepts 0x..., 0b... syntax)"

    parser = argparse.ArgumentParser(
        description='USI test tool',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '-p',
        '--path',
        default='/dev/hidraw0',
        help='the path of the USI device')
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='Show raw data for HID transactions')
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
            'color', 'color24', 'width', 'style', 'buttons', 'firmware', 'usi_version'
        ],
        help='which feature to read')
    parser_get.add_argument(
        '-i',
        '--index',
        default=1,
        type=int_anybase,
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
        type=int_anybase,
        help='which stylus to set values for, by index (default: %(default)s)')
    parser_set.add_argument(
        '-c',
        '--color',
        type=int_anybase,
        metavar='{}-{}'.format(0, field_max('color')),
        help='set the 8-bit indexed stylus color to the specified value '
        'in the range ({},{})'.format(0, field_max('color')))
    parser_set.add_argument(
        '-C',
        '--color24',
        type=int_anybase,
        metavar='{}-{}'.format(hex(0), hex(field_max('color24'))),
        help='set the 24-bit RGB stylus color to the specified value '
        'in the range ({},{})'.format(hex(0), hex(field_max('color24'))))
    parser_set.add_argument(
        '--color24-no-preference',
        action='store_true',
        help='indicate that a 24-bit color should be set as \'No preference\'; '
        'must be used in conjunction with -C or --color24.')
    parser_set.add_argument(
        '-w',
        '--width',
        type=int_anybase,
        metavar='{}-{}'.format(0, field_max('width')),
        help='set the stylus width to the specified value '
        'in the range ({},{})'.format(0, field_max('width')))
    parser_set.add_argument(
        '-s',
        '--style',
        # This is intentionally lax: for testing purposed, all numeric
        # values can actually be assigned.
        type=lambda value: STYLES[value] if value in STYLES
                        else int(value, 0) if 0 <= int(value,0) <= 255
                        else None,
        metavar='{%s}' % ','.join(STYLES.keys()),
        help='set the stylus style to the specified value. A numeric '
             'value may also be used.')
    parser_set.add_argument(
        '-b',
        '--button',
        dest='buttons',
        nargs=2,
        action='append',
        type=lambda value: value if value in PHYSICAL_BUTTONS
                                    or value in BUTTON_ASSIGNMENTS
                                else int(value, 0),
        metavar=('{%s}' % ','.join(PHYSICAL_BUTTONS),
                 '{%s}' % ','.join(BUTTON_ASSIGNMENTS)),
        help='Remap a physical button to have a particular function. '
        'For example: \'usi-test set -b barrel eraser\' will set the '
        'barrel button to have the eraser function. You may set multiple '
        'buttons at once by repeating this option. The assignment may '
        'also be a numeric value.')

    parser_diag = subparsers.add_parser(
        'diagnostic',
        help='send a diagnostic command',
        description='Send the specified diagnostic command to a stylus and '
        'read the response. You may set the preamble and cyclic redundancy '
        'check manually, but by default the tool will send the preamble and '
        'CRC defined by the USI spec.')
    parser_diag.add_argument(
        'command_id', type=int_anybase, help='which diagnostic command to send')
    parser_diag.add_argument(
        'data', type=int_anybase, help='the data to send with the diagnostic command')
    parser_diag.add_argument(
        '-i',
        '--index',
        default=1,
        type=int,
        help='which stylus to send command to, by index (default: %(default)s)')
    parser_diag.add_argument(
        '-d',
        '--delay',
        default=None,
        type=int,
        help='delay in ms to introduce between Set and Get for the dignostic feature '
             f'(default: {DEFAULT_DELAY_MS}, for flex: {DEFAULT_DELAY_FLEX_MS})')
    parser_diag.add_argument(
        '-c',
        '--crc',
        type=int_anybase,
        help='the custom cyclic redundancy check for the diagnostic command. '
        'By default this tool will send the appropriate crc calculated by the '
        'algorithm defined in the USI spec.')
    parser_diag.add_argument(
        '-p',
        '--preamble',
        type=int_anybase,
        default=None,
        help='the preamble for the diagnostic command. '
             'By default this tool will send the preamble defined in the USI spec '
             f'(default: {DEFAULT_PREAMBLE}, for flex: {DEFAULT_PREAMBLE_FLEX})')
    parser_diag.add_argument(
        '-f',
        '--flex',
        action='store_true',
        default=False,
        help='use parameters appropriate for USI 2.0 flex beacons, '
             'which will likely be needed for in-cell displays.')

    args = parser.parse_args()

    # With Python 3.7, this should just be required=True in add_subparsers()
    if args.command == None:
        parser.print_help()
        parser.exit()

    # Due to nargs=2, this is not easy to validate directly in --buttons.
    if args.command == 'set' and args.buttons is not None:
        valid = True
        for physical_button_value, button_assignment_value in args.buttons:
            if (physical_button_value not in PHYSICAL_BUTTONS):
                valid = False
            # The assigned value check is intentionally lax: for testing,
            # all values from 0-255 can actually be assigned.
            if (type(button_assignment_value) == int
                and not 0 <= button_assignment_value <= 255):
                valid = False
            if (type(button_assignment_value) != int
                and button_assignment_value not in BUTTON_ASSIGNMENTS):
                valid = False
        if not valid:
            parser_set.print_help()
            parser_set.exit()

    return args

def dump_input_reports(device):
    """Continually dump out HID Input reports until killed by interrupt.
    """

    # Look inside hidtools, see if it is one we know about
    known_hidtools = (hasattr(device, '_dump_offset')
                      and hasattr(device, 'events'))
    input_report_length = {}

    while True:
        device.dump(sys.stdout)

        # Workaround to avoid very slow memory leak. Issue filed:
        # https://gitlab.freedesktop.org/libevdev/hid-tools/-/issues/37
        if known_hidtools:
            device.events = []      # Empty perpetual list
            device._dump_offset = 0 # Reset index to top of list

        device.read_events()

        # Workaround to bug in hid-tools 0.2 that rejects reports
        # that are _longer_ than the size in the descriptor, fixed in
        # hid-tools commit 370454ba34724050a63b0bd97c5a82925b29c19a.
        if known_hidtools:
            for report in device.events:
                if len(report.bytes) > 0:
                    # Fetch first byte of report to get report ID.
                    id = report.bytes[0]
                    if id in input_report_length:
                        # Trim reports to exact length, so they aren't rejected for
                        # being wrong size.
                        report.bytes = report.bytes[0:input_report_length[id]]
                    else:
                        input_report_length[id] = device.report_descriptor.input_reports[id].size

def main():
    global verbose

    args = parse_arguments()

    fd = open(args.path, 'rb+')
    device = hidtools.hidraw.HidrawDevice(fd)
    verbose = args.verbose

    try:

        if args.command == 'descriptor':
            device.dump(sys.stdout)
        elif args.command == 'data':
            dump_input_reports(device)
        elif args.command == 'get':
            buf = get_feature(device, args.index, args.feature)
            print_feature(buf, args.index, args.feature)
        elif args.command == 'set':
            set_features(device, args)
        elif args.command == 'diagnostic':
            buf = diagnostic(device, args)
            print_diagnostic(buf)

    except FeatureNotFoundError as e:
        print("Unable to complete operation due to incompatible HID descriptor for {}: {}".format(args.path, *e.args))

if __name__ == '__main__':
    main()
