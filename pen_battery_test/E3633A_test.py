# Copyright 2022 The ChromiumOS Authors.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

""" E3633A class to handle all connections,
    communication, test execution, and results output
    for testing the reported stylus battery capacity across
    an applied voltage range.
"""

import pyvisa
import subprocess
import re
import numpy as np
from time import sleep
from datetime import datetime


class E3633ATest:
    """Class to handle connection to E3633A source meter,
       run test, and output results.

       Attributes:
         num_steps: number of voltage steps during test.
         vstep: voltage step value, based on num_steps and V range tested.
         test_voltages: array of voltages used for test, descending order.
         battery_dir: string for directory containing stylus battery info.
         instrument: object that handles direct messaging to E3633A.
         compliance_current: string containing value for E3633A current limit.
         current_prot: string containing value for E3633A current protection level.
         currents_avg: list to hold average current measurements.
         currents_std: list to hold stdev of current measurements.
         battery_percents: list to hold battery percent readings reported by OS.
         usi_bat_percents_avg: list to hold average battery percent reported from usi-test.
         usi_bat_percents_std: list to hold stdev  of battery percent reported from usi-test.
         usi_bat_errors: list of ints to hold number of errors per test voltage from usi-test.
         stylus_name: string, name of stylus being tested.
         test_datetime: datetime object of date/time test was started.
         path: string of absolute path to stylus for usi-test tool.
         event: string of numeric value of stylus event, used by evtest command.
    """

    def __init__(self, max_voltage, min_voltage, num_steps, name, path, event):
        """Initializes E3633ATest attributes, connects to
           E3633A source meter and initializes settings, and
           searches for stylus battery location in /sys/class/power_supply.

           Args:
             max_voltage: a float to set the highest stylus test voltage.
             min_voltage: a float to set the lowest stylus test voltage.
             num_steps: an integer that sets the total number of voltage
               steps to test.
             name: a string, name of stylus being tested.
             path: string of absolute path to stylus.
             event: string of numeric value of evtest stylus event.
        """

        # create source voltage values
        self.num_steps = num_steps

        # create array of source voltages and save voltage step value if needed for plot
        self.test_voltages, self.vstep = np.linspace(min_voltage, max_voltage, num=self.num_steps, endpoint=True, retstep=True)
        self.test_voltages = np.flipud(self.test_voltages) # reverse to descending order

        self.compliance_current = "0.01" # 10mA

        self.current_prot = "0.1" # 100mA

        # get stylus path
        re_stylus = re.compile("hid\S*battery") # create RE to parse out styluses from list of power supplies
        cmd_result = subprocess.run(["ls", "/sys/class/power_supply"],
                                    stdout = subprocess.PIPE,
                                    stderr = subprocess.STDOUT)
        cmd_response = cmd_result.stdout.decode("UTF-8")
        cmd_response = cmd_response.split()

        # parse out valid stylus battery source from cmd_response
        self.battery_dir = None
        for r in cmd_response:
            if re_stylus.match(r) != None:
                self.battery_dir = r

        # Prompt user for power on sequence
        user_prompt = "\
        \nWith E3633A power OFF, connect stylus to (+) and (-) output terminals.\
        \nPlace stylus in operation range of DUT.\
        \nPower on E3633A.\
        \nPress enter when done."
        input(user_prompt)

        # connect to E3633A
        resource_manager = pyvisa.ResourceManager("@py") # use pyvisa-py
        self.instrument = resource_manager.open_resource("ASRL/dev/ttyUSB0::INSTR")

        # set E3633A command termination characters
        self.instrument.read_termination = "\n"
        self.instrument.write_termination = "\n"
        self.instrument.timeout = 25000   # increase timeout from default to 25 seconds
        self.instrument.write("SYST:REM") # set remote mode
        self.instrument.write(":VOLT:RANG P8V") # SMU in 8V source range

        print("Connected to:")
        print(self.instrument.query("*IDN?"))

        # empty lists for raw data
        self.currents_avg = []         # list of numpy arrays
        self.currents_std = []         # list of numpy arrays
        self.battery_percents = []     # list of ints
        self.usi_bat_percents_avg = [] # numpy array
        self.usi_bat_percents_std = [] # numpy array
        self.usi_bat_percents_max = [] # numpy array
        self.usi_bat_errors = []       # list of ints

        self.stylus_name = name

        self.test_datetime = datetime.now()

        self.path = path
        self.event = event

    def init_test(self):
        """Initializes E3633A to test start conditions.
           Enables source output.
        """

        # set E3633A over current protection
        self.instrument.write(":CURR:PROT " + self.current_prot)
        sleep(1) # wait to avoid input buffer overflow

        # init source voltage/current limits and enable E3633A source output
        self.instrument.write(":CURR " + self.compliance_current)
        self.instrument.write(":VOLT " + str(self.test_voltages[0]))
        self.instrument.write("OUTP ON")

    def pause(self, seconds):
        """Sleeps for specified number of seconds.
           Displays timing bar to user in terminal.

           Args:
             seconds: int of seconds to sleep
        """

        for i in range(seconds):
            print("|{}{}|".format("/" * i, "." * (seconds - i - 1)), end="\r")
            sleep(1)

    def stylus_connected(self):
        """Checks up to 20 times if stylus is currently connected to DUT.

           Returns:
             boolean:
               True: connected
               False: not connected
        """
        stylus_event = "/dev/input/event{}".format(self.event)
        for x in range(20):
            connected = subprocess.run(["evtest", "--query", stylus_event, "EV_KEY", "320"],
                                       stdout = subprocess.PIPE,
                                       stderr = subprocess.STDOUT)
            if connected.returncode == 10: # a return code of 10 means stylus is in range and active
                return True

        return False

    def parse_usi_bat_percent(self, response):
        """Parses battery percent out of list from usi-test command response.

           Args:
             response: list of strings from usi-test command response.

           Returns:
             The integer value of the battery percent.
        """

        bat_percent_bit_mask = 0b1111111 # last 7 bits holds battery percent

        response_value_hex_str = response[-2][-6:]
        response_value_int = int(response_value_hex_str, 16)
        bat_percent = response_value_int & bat_percent_bit_mask

        return bat_percent

    def run(self):
        """Runs the test by applying the voltages in test_voltages to
           the stylus.
           At each voltage step:
             A check is made to confirm stylus is connected, exits
               test loop if not.
             Current is sampled 100 times and the average/stdev is
               calculated/saved.
             Stylus battery percent from usi-test is sampled 32
               times and the average/stdev/errors is saved.
             Stylus battery percent reported by OS is saved.
           After the test loop completes, test_voltages is truncated
           if necessary to match the length of battery_percents for
           plotting the data.
        """

        # used to prompt user when stylus disconnects
        disconnect_msg = "Stylus lost connection at {:.2f} volts, stopping measurements."

        self.init_test()
        print("Allowing time for stylus to power on...")
        self.pause(10) # allow time for initial stylus connection

        # loop through desired test voltages and read stylus battery level from OS
        for v in self.test_voltages:
            print("Testing {:.2f} V".format(v))
            self.instrument.write(":VOLT " + str(v)) # set voltage
            sleep(2)                                 # allow time for V change to settle

            if not self.stylus_connected():
                print(disconnect_msg.format(v))
                break # exit test loop since no more data can be gathered

            print("  Measuring current...")
            currents = []
            for i in range(100):
                c = self.instrument.query("MEAS:CURR?")
                currents.append(c)
                sleep(0.1)

            # poll stylus battery 32 times with usi-test tool
            print("  Polling stylus battery %...")
            usi_battery_data = np.array([])
            usi_error_count = 0
            for i in range(32):

                print("    {}/32...".format(i + 1), end="\r")
                err = True
                try:
                    usi_cmd_result = subprocess.run(["usi-test", "-p", self.path, "diagnostic", "63", "128"],
                                                    stdout=subprocess.PIPE,
                                                    stderr=subprocess.STDOUT,
                                                    timeout=15)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    usi_response = usi_cmd_result.stdout.decode("UTF-8") # convert bytes to string
                    usi_response = usi_response.split("\n")
                    err_code = usi_response[-3][-4:] # grab error value
                    if err_code == "0x00":
                        err = False

                if err:
                    usi_battery_data = np.append(usi_battery_data, np.NaN)
                    usi_error_count = usi_error_count + 1
                else:
                    # no error
                    percent = self.parse_usi_bat_percent(usi_response)
                    usi_battery_data = np.append(usi_battery_data, percent)

            print("    32/32   ")

            # poll stylus battery capacity from OS
            cmd_result = subprocess.run(["cat", "/sys/class/power_supply/" + self.battery_dir + "/capacity"],
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
            cap = cmd_result.stdout.decode("UTF-8").strip() # convert bytes to string

            if usi_error_count == 32:
                print(disconnect_msg.format(v))
                break # exit test loop since no more data can be gathered

            # save raw data
            currents = np.array(currents, dtype=float)
            self.currents_avg.append(currents.mean())
            self.currents_std.append(np.std(currents))
            self.battery_percents.append(int(cap))
            self.usi_bat_percents_avg.append(np.nanmean(usi_battery_data))
            self.usi_bat_percents_std.append(np.nanstd(usi_battery_data))
            self.usi_bat_percents_max.append(np.nanmax(usi_battery_data))
            self.usi_bat_errors.append(usi_error_count)

        # disable source output
        self.instrument.write("OUTP OFF")

        # convert battery percent lists to numpy arrays
        self.usi_bat_percents_avg = np.array(self.usi_bat_percents_avg, dtype=float)
        self.usi_bat_percents_std = np.array(self.usi_bat_percents_std, dtype=float)
        self.usi_bat_percents_max = np.array(self.usi_bat_percents_max, dtype=float)
        self.battery_percents = np.array(self.battery_percents, dtype=float)
        # resize test_voltages to match size of battery_percents
        # by removing untested voltages
        if self.test_voltages.size > self.battery_percents.size:
            self.test_voltages = self.test_voltages[:self.battery_percents.size - self.test_voltages.size]

    def create_file(self, file_name):
        """Creates new file with formatted naming convention.
           Prepends datetime and stylus name to specified file name.

           Args:
             file_name: a string.

           Returns: file object.
        """

        year = str(self.test_datetime.year)
        month = "{:02d}".format(self.test_datetime.month)
        day = "{:02d}".format(self.test_datetime.day)
        hour = "{:02d}".format(self.test_datetime.hour)
        minute = "{:02d}".format(self.test_datetime.minute)
        second = "{:02d}".format(self.test_datetime.second)
        file = open("{}{}{}{}{}{}_{}_{}".format(year, month, day, hour, minute, second, self.stylus_name, file_name), "w")
        file.truncate()

        return file

    def raw_data_to_csv(self):
        """Writes raw data to a csv file containing columns:
             V: voltage step tested.
             I avg: average current measured at step.
             I std: std of current at step.
             USI Battery %: stylus battery % avg reported from usi-test at step.
             USI Battery % std: stdev of battery % reported by usi-test at step.
             USI Battery % max: max battery % reported by usi-test at step.
             USI errors: number of errors out fo 32 readings from usi-test at step.
             OS Battery % : stylus battery percent reported in OS at step.
        """

        # check if any data was collected
        if len(self.battery_percents) == 0:
            print("Insufficient data, csv not created")
            return

        results = self.create_file("rawdata.csv")

        # column names
        results.write("V,I avg,I stdev,USI Battery %,USI Battery % std,USI Battery % max,")
        results.write("USI errors,OS Battery %\n")

        # write data to columns
        for i in range(0, self.test_voltages.size):

            results.write("{},{},{},{},{},{},{}/32,{}\n".format(self.test_voltages[i], self.currents_avg[i],
                                                                self.currents_std[i], self.usi_bat_percents_avg[i],
                                                                self.usi_bat_percents_std[i], self.usi_bat_percents_max[i],
                                                                self.usi_bat_errors[i], self.battery_percents[i]))

        results.close()

    def plot_data(self):
        """Creates an html file with two plots:
             Battery percent vs applied voltage.
               Calculates r squared based on ideal battery percentage
               drop over the voltage range the stylus was actually
               connected and measurements could be successfully taken.
             mWatts vs applied voltage.
        """

        # check if any data was collected
        if len(self.battery_percents) == 0:
            print("Insufficient data, plot not created")
            return

        plot = self.create_file("plot.html")

        # create header
        plot.write("<head>\n")
        plot.write("  <script src=\"https://cdn.plot.ly/plotly-2.6.3.min.js\"></script>\n")
        plot.write("</head>\n")

        # create div for plot
        plot.write("<div id=\"plot\" style=\"width:90%;height:600px;\"></div>\n")

        """ Percent vs Volts plot """

        plot.write("<script>\n\n")

        plot.write("  PLOT = document.getElementById(\"plot\");\n\n")

        plot.write("  var trace1 = {\n\n") # start trace1

        # x data
        plot.write("    x: [")
        plot.write("{}".format(self.test_voltages[0]))
        if self.test_voltages.size > 1:
            for x in self.test_voltages[1:]:
                plot.write(", {}".format(x))
        plot.write("],\n")

        # y data
        plot.write("    y: [")
        plot.write("{}".format(self.usi_bat_percents_avg[0]))
        if self.usi_bat_percents_avg.size > 1:
            for y in self.usi_bat_percents_avg[1:]:
                plot.write(", {}".format(y))
        plot.write("],\n")

        #error bars
        plot.write("    error_y: {\n")
        plot.write("      type: \"data\",\n")
        plot.write("      array: [")
        plot.write("{}".format(self.usi_bat_percents_std[0]))
        if self.usi_bat_percents_std.size > 1:
            for e in self.usi_bat_percents_std[1:]:
                plot.write(", {}".format(e))
        plot.write("],\n")
        plot.write("      visible: true\n")
        plot.write("    },\n")

        plot.write("    type: \"scatter\",\n")
        plot.write("    name: \"Reported Battery Percent\"\n")
        plot.write("  };\n\n") # end trace1

        if self.test_voltages.size > 1:
            # calc ideal percentage drop
            ixstop = self.test_voltages[-1]
            if self.num_steps > self.test_voltages.size:
                # if stylus lost connection before lowest test voltage
                # anchors battery at 0% to last attempted voltage
                ixstop = ixstop - self.vstep
            ix = np.array([self.test_voltages[0], ixstop], dtype=float)
            iy = np.array([100.0, 0.0], dtype=float)
            coef = np.polyfit(ix, iy, 1, full=True)[0]
            ideal_yfit = [coef[0] * x + coef[1] for x in self.test_voltages]

            plot.write("  var trace2 = {\n\n") # start trace2

            # x data
            plot.write("    x: [")
            plot.write("{}".format(self.test_voltages[0]))
            for x in self.test_voltages[1:]:
                plot.write(", {}".format(x))
            plot.write("],\n")

            # y data
            plot.write("    y: [")
            plot.write("{}".format(ideal_yfit[0]))
            for y in ideal_yfit[1:]:
                plot.write(", {}".format(y))
            plot.write("],\n")

            plot.write("    type: \"scatter\",\n")
            plot.write("    name: \"Ideal Fit\"\n")
            plot.write("  };\n\n") # end trace2

            # calc regression of usi_bat_percents_avg
            coef = np.polyfit(self.test_voltages, self.usi_bat_percents_avg, 1, full=True)[0]
            yfit = [coef[0] * x + coef[1] for x in self.test_voltages]

            plot.write("  var trace3 = {\n\n") # start trace3

            # x data
            plot.write("    x: [")
            plot.write("{}".format(self.test_voltages[0]))
            for x in self.test_voltages[1:]:
                plot.write(", {}".format(x))
            plot.write("],\n")

            # y data
            plot.write("    y: [")
            plot.write("{}".format(yfit[0]))
            for y in yfit[1:]:
                plot.write(", {}".format(y))
            plot.write("],\n")

            plot.write("    type: \"scatter\",\n")
            plot.write("    name: \"Data Fit\"\n")
            plot.write("  };\n\n") # end trace3

        plot.write("  var data = [trace1")
        if self.test_voltages.size > 1:
            plot.write(", trace2, trace3")
        plot.write("];\n\n")

        plot.write("  var layout = {\n\n") # start layout

        plot.write("    xaxis: {\n")
        plot.write("      tick0: 0.7,\n")
        plot.write("      dtick: 0.1,\n")
        plot.write("      title: \"Applied Voltage\"\n")
        plot.write("    },\n\n")

        plot.write("    yaxis: {\n")
        plot.write("      tick0: 0,\n")
        plot.write("      dtick: 8,\n")
        plot.write("      title: \"Battery Percentage\"\n")
        plot.write("    },\n\n")

        # Calculate ideal/relative R2
        if self.test_voltages.size > 2: # need at least 3 points to calculate r2

            # Ideal
            res = np.sum(np.square(self.usi_bat_percents_avg - ideal_yfit))
            ymean = self.usi_bat_percents_avg.mean()
            total_sum_of_squares = np.sum(np.square(self.usi_bat_percents_avg - ymean))
            ideal_r2 = 100 * (1 - (res / total_sum_of_squares))

            # Relative
            res = np.sum(np.square(self.usi_bat_percents_avg - yfit))
            ymean = self.usi_bat_percents_avg.mean()
            total_sum_of_squares = np.sum(np.square(self.usi_bat_percents_avg - ymean))
            relative_r2 = 100 * (1 - (res / total_sum_of_squares))

        plot.write("    legend: {\n")
        plot.write("      orientation: \"h\",\n")
        plot.write("      y: -0.2,\n")
        plot.write("      title: {\n")
        if self.test_voltages.size > 2:
            if 0 <= ideal_r2 <= 100:
                # more or less expected behavior
                plot.write("        text: \"R2 absolute = {:.2f}% - R2 relative = {:.2f}%\"\n".format(ideal_r2, relative_r2))
            else:
                # yikes, something went wrong
                plot.write("        text: \"R2 absolute = out of range - R2 relative = {:.2f}%\"\n".format(relative_r2))
        else:
            plot.write("        text: \"R2 = insufficient data\"\n")
        plot.write("      }\n")
        plot.write("    },\n")
        plot.write("    showlegend: true,\n")
        plot.write("    title: \"Reported Stylus Battery Percentage - {}\"\n\n".format(self.stylus_name))

        plot.write("  };\n\n") # end layout

        plot.write("  Plotly.newPlot(PLOT, data, layout);\n\n")

        plot.write("</script>")


        """ mWatts vs V plot """

        plot.write("<div id=\"plot2\" style=\"width:90%;height:600px;\"></div>\n")

        plot.write("<script>\n\n")

        plot.write("  PLOT = document.getElementById(\"plot2\");\n\n")

        plot.write("  var trace1 = {\n\n") # start trace1

        # x data
        plot.write("    x: [")
        plot.write("{}".format(self.test_voltages[0]))
        if self.test_voltages.size > 1:
            for x in self.test_voltages[1:]:
                plot.write(", {}".format(x))
        plot.write("],\n")

        # Calculate mWatts
        mWatts = self.test_voltages * self.currents_avg * 1000

        # y data
        plot.write("    y: [")
        plot.write("{}".format(mWatts[0]))
        if mWatts.size > 1:
            for y in mWatts[1:]:
                plot.write(", {}".format(y))
        plot.write("],\n")

        plot.write("    type: \"scatter\",\n")
        plot.write("    name: \"mWatts vs V\"\n")
        plot.write("  };\n\n") # end trace1

        plot.write("  var data = [trace1];\n\n")

        plot.write("  var layout = {\n\n") # start layout

        plot.write("    xaxis: {\n")
        plot.write("      tick0: 0.7,\n")
        plot.write("      dtick: 0.1,\n")
        plot.write("      title: \"Applied Voltage\"\n")
        plot.write("    },\n\n")

        plot.write("    yaxis: {\n")
        plot.write("      tick0: 0,\n")
        plot.write("      title: \"mWatt\"\n")
        plot.write("    },\n\n")

        plot.write("    showlegend: true,\n")
        plot.write("    title: \"mWatt vs V\"\n\n")

        plot.write("  };\n\n") # end layout

        plot.write("  Plotly.newPlot(PLOT, data, layout);\n\n")

        plot.write("</script>")

        plot.close()
