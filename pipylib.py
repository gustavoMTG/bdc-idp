"""
Required to set API_ENDPOINT with url as environment variable.
This library is intended to make requests to PIWebAPI easier.

Talking about signals is the same as a list of PIPoint objects.

////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
//////////////////////////////////////////////// OVERVIEW //////////////////////////////////////////////////////////

The core component of this library is the PIPoint class. This class will store data as attributes for each signal and
also has methods to request data for each signal. Inside It there are two methods, get_recorded_data to request a list
of points, each with value, name and timestamp, and get_end_value which doesn't require arguments since it will only
retrieve as the name implies, the last value.

get_converted_alarms returns a list of PIPoint signals that match the name_filter argument, name_filter takes string
arguments the same way we query PIVision e.g. CAZ_RODUR*.VALOR.

Once we have a list of PIPoint signals, we can ask for it's data by two approaches:
- We can loop the list and use get_recorded_data or get_end_value to get the data. Both methods return None, the
retrieved data is stored in the data attribute of each signal.
- Second approach is to use the batch controller, but it requires to first segment the signals with
batch_signals_segmentation().
Batch controller can make multiple requests at once, minimizing the amount of requests sent to the server.

generate table takes a list of signals and will return a styled pandas dataframe with the signals we are interested
highlighted.
"""

import requests
import urllib3
from datetime import datetime, timedelta
import pandas as pd
import os
import math

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Set the API endpoint as environment variables or replace these two lines with the routes as strings
API_ENDPOINT = os.environ["API_ENDPOINT"]
BATCH_ENDPOINT = os.environ["BATCH_ENDPOINT"]


def batch_signals_segmentation(signals: list, max_batch=999) -> list:
    """
    Since PIWebAPI batch controller is limited to 999 elements, this method segments the list of signals to query into
    lists of a maximum length of 999 elements each.
    :param signals:
    :return:
    """
    length = len(signals)
    if length > max_batch:
        required_divisons = math.ceil(length / max_batch)
        segmented_signals = []
        for division in range(1, required_divisons + 1):
            index_start = max_batch * (division - 1)
            index_end = max_batch * division
            if division != required_divisons:
                segmented_signals.append(signals[index_start:index_end])
            else:
                segmented_signals.append(signals[index_start:])
        return segmented_signals
    else:
        return [signals]


def pi_sync_get_alarms_list(name_filter, max_count=3000):
    """
    API request from PI database, returns a list of alarms that matches with the nameFilter.
    Each point of the list as a JSON.
    """

    parameters = {
        "nameFilter": name_filter,
        "maxCount": max_count
    }
    try:
        response = requests.get(url=API_ENDPOINT, params=parameters, verify=False)
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        print(f"Error ocurred: {error}")
        alarms = []
    else:
        alarms = response.json()
        alarms = alarms["Items"]
    finally:
        return alarms


def format_timestamp(timestamp):
    """Internal function"""
    # Remove the "Z" at the end and one decimal digit because timestamp only handles microseconds (6 decimals)
    if "." in timestamp:
        after_comma_length = len(timestamp.split(".")[1])
        if after_comma_length > 3:
            timestamp = timestamp[:-2]
        else:
            timestamp = timestamp[:-1]
    else:
        timestamp = timestamp[:-1] + ".0"
    timestamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%f")
    local_date_time = timestamp - timedelta(hours=3)
    return local_date_time


def find_voltage(pi_query):
    """Internal function"""
    try:
        kv_index = pi_query["Descriptor"].split(" ")
        kv_index = kv_index.index("kV")
        return pi_query["Descriptor"].split(" ")[kv_index - 1]
    except ValueError:
        return "Not defined"


def find_equipment_type(pi_query):
    """Internal function"""
    try:
        descriptor = pi_query["Descriptor"].split("|")[1]
    except IndexError:
        descriptor = pi_query["Descriptor"].split("|")[0]

    equipments_dict = {
        "línea": ["línea", "linea"],
        "cable": ["cable"],
        "trafo": ["trafo"],
        "radial": ["radial", "celda", "salida"],
        "barra": ["barra"]
    }

    found = False
    for equipment, values_list in equipments_dict.items():
        for value in values_list:
            if value in descriptor.lower():
                found = True
                return equipment
    if not found:
        return "Not defined"


def get_converted_alarms(name_filter, max_count=3000, exclude_borr=True):
    """This function returns a list of alarms already converted to PI_point objects"""
    alarms = pi_sync_get_alarms_list(name_filter=name_filter, max_count=max_count)
    alarms = [PIPoint(alarm) for alarm in alarms]
    if exclude_borr:
        alarms = [alarm for alarm in alarms if ".borr" not in alarm.path.lower()]
    return alarms


def generate_table(signals: list, start_time="*-3d", end_time="*-0d", style=True, batch=False, max_batch=999):
    """
    Generates a dataframe ordered by timestamps and colors it according to triggers, reclosure commands and breakers positions.
    :param signals: A list of the signals that will be taken into account.
    :param start_time: Starting date since it will be retrieved.
    :param end_time: Ending date since it will be retrieved.
    Examples for days_back
            "*" (now)
            "*-8h" (8 hours ago)
            "01" (first of current month)
            "01/01" (first of current year)
            "Monday+8h"
            "Sat, 01 Nov 2008 19:35:00 GMT + 2y+5d-12h+30.55s"
            "Today" (Today at 00:00)
            "T-3d"
            "Yesterday + 03:45:30.25"
    """

    # Styling row according to values
    def highlight_row(row):
        if row['Name'] == "ON":
            if "recierre" in row["Descriptor"].lower():
                return ['background-color: darkorange'] * len(row)
            elif "disp." in row["Descriptor"].lower():
                return ['background-color: yellow'] * len(row)
            else:
                return [''] * len(row)
        elif "estado del interruptor" in row["Descriptor"].lower():
            if row['Name'] == "DSD":
                return ['color: red'] * len(row)
            elif row['Name'] == "Abierto":
                return ['background-color: red'] * len(row)
            elif row['Name'] == "Cerrado":
                return ['background-color: skyblue'] * len(row)
            else:
                return [''] * len(row)
        else:
            return [''] * len(row)

    def df_treatment(dataframe, station, descriptor, source):
        # Add some info to the table
        dataframe["Descriptor"] = "|".join(descriptor.split("|")[:-1])
        dataframe["Station"] = station
        dataframe["Source"] = source
        return dataframe

    # Only keeps the values that are not measurements (voltages, currents...)
    signals = [signal for signal in signals if signal.point_type.lower() == "digital"]

    # This portion of the code could improve with async requests
    # This if statement keeps backward compatibility with the previous request method get_recorded
    if batch:
        segmented_signals = batch_signals_segmentation(signals, max_batch)
        for signals_ in segmented_signals:
            print(f"Batch segmentation: {len(signals_)}")
            batch_request(signals_, start_time, end_time)
    else:
        for signal in signals:
            signal.get_recorded_data(start_time, end_time)
    dfs_list = [df_treatment(
        dataframe=pd.DataFrame(signal.data),
        station=signal.station,
        descriptor=signal.descriptor,
        source=signal.source) for signal in signals]

    dfs_list = [df_ for df_ in dfs_list if "Name" in list(df_.columns)]

    styled_df = pd.DataFrame()  # Assign an initial value to styled_df

    try:
        # Keep only the shifts
        dfs_list = [item[item['Name'].ne(item['Name'].shift())] for item in dfs_list]

    except KeyError:
        pass

    else:
        styled_df = pd.concat(dfs_list)
        styled_df = styled_df.sort_values("Timestamp")
        styled_df = styled_df.reset_index()
        styled_df = styled_df.drop("index", axis=1)

        # Apply the styling to the dataframe
        styled_df = styled_df.style.apply(highlight_row, axis=1) if style else styled_df

    finally:
        return styled_df


def batch_request(signals, start_time="*-3d", end_time="*-0d", end_value=False, max_count=3000):
    """
    This method handles the PI Web API batch controller. By passing a signals list of PIPoint objects
    it will perform the requests in a single large request and store it in the data attribute of each signal.
    :param end_value:
    :param signals: List of signals with PIPoint objects
    :param start_time: Date like string e.g. 12/09/2023
    :param end_time: As start_time
    :param max_count: Maximum number of data to be returned by each signal
    :return: Nothing
    """
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "application/json"
    }
    raw_body = {}
    for signal in signals:
        if end_value:
            raw_body[signal.path] = {
                "Method": "GET",
                "Resource": signal.end_value
            }
        else:
            raw_body[signal.path] = {
                "Method": "GET",
                "Resource": signal.recorded_data
                            + "?" + f"startTime={start_time}"
                            + "&" + f"endTime={end_time}"
                            + "&" + f"maxCount={max_count}",
                "Content": f"{{'startTime': '{start_time}', 'endTime': '{end_time}', 'maxCount': '{max_count}'}}"
            }

    try:
        response = requests.post(url=BATCH_ENDPOINT, headers=headers, json=raw_body,
                                 verify=False)
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        print(f"Error ocurred: {error}")
    else:
        datas = response.json()
        for signal in signals:
            # For HTTP status code in 200 range, the data is valid
            if 200 <= datas[signal.path]["Status"] <= 299:
                # print(datas[signal.path])
                if end_value:
                    signal.data = datas[signal.path]["Content"]
                else:
                    signal.data = datas[signal.path]["Content"]["Items"]
                try:
                    # Discard unnecessary data
                    if end_value:
                        signal.data = {
                            "Timestamp": format_timestamp(signal.data["Timestamp"]),
                            "Value": signal.data["Value"]["Value"],
                            "Name": signal.data["Value"]["Name"]
                        }
                    else:
                        signal.data = [{"Timestamp": format_timestamp(data["Timestamp"]),
                                        "Value": data["Value"]["Value"],
                                        "Name": data["Value"]["Name"]}
                                       for data in signal.data]
                except TypeError:
                    pass
            else:
                signal.data = []


class PIPoint:
    """
    This class provides methods and attributes that make easier to handle signals from the DB.
    Mainly focused on digital signals but still handles floats.
    """

    def __init__(self, pi_query):
        self.name = pi_query["Name"]
        self.descriptor = pi_query["Descriptor"]
        self.path = pi_query["Path"]
        self.source = pi_query["Path"].split("_")[0][-3:].lower()
        self.point_class = pi_query["PointClass"]
        self.point_type = pi_query["PointType"]
        self.reduced_descriptor = " | ".join(pi_query["Descriptor"].split(" | ")[1:-1])
        self.first_descriptor = pi_query["Descriptor"].split(" | ")[0]
        self.station = pi_query["Descriptor"].split(" | ")[-1]
        self.value = pi_query["Links"]["Value"]
        self.end_value = pi_query["Links"]["EndValue"]
        self.recorded_data = pi_query["Links"]["RecordedData"]
        self.voltage = find_voltage(pi_query)
        self.equipment = find_equipment_type(pi_query)
        self.data = None

    def get_endvalue(self):
        """Returns the last point the DB has, NOT the last shift.
        Returned data has Timestamp, Value and Name."""
        try:
            response = requests.get(url=self.end_value, verify=False)
            response.raise_for_status()
        except requests.exceptions.RequestException as error:
            print(f"Error ocurred: {error}")
            data = {}
        else:
            data = response.json()
            formatted_timestamp = format_timestamp(timestamp=data["Timestamp"])
            if "float" in self.point_type.lower():
                # In this case the signal is a measurment of analog magnitudes such as current or voltage
                data = {
                    "Timestamp": formatted_timestamp,
                    "Value": data["Value"]
                }
            else:
                # It's a digital signal like an alarm
                data = {
                    "Timestamp": formatted_timestamp,
                    "Value": data["Value"]["Value"],
                    "Name": data["Value"]["Name"]
                }
        finally:
            self.data = data

    def get_recorded_data(self, start_time="*-3d", end_time="*-0d", max_count=1000, dataframe=False):
        """
        Returns the recorded data for a given amount of days back.

        :param dataframe: If true the data is returned as a pandas dataframe, if false as a list of dictionaries.
        :param max_count: Maximum amount of points that can be returned.
        :param days_back_start: Amount of days back to start requesting data.
        :param days_back_end: Amount of days back to finish requesting data.
        Examples for days_back
            "*" (now)
            "*-8h" (8 hours ago)
            "01" (first of current month)
            "01/01" (first of current year)
            "Monday+8h"
            "Sat, 01 Nov 2008 19:35:00 GMT + 2y+5d-12h+30.55s"
            "Today" (Today at 00:00)
            "T-3d"
            "Yesterday + 03:45:30.25"
        """

        parameters = {
            "startTime": start_time,
            "endTime": end_time,
            "maxCount": max_count
        }
        try:
            response = requests.get(url=self.recorded_data, params=parameters, verify=False)
            response.raise_for_status()
        except requests.exceptions.RequestException as error:
            print(f"Error ocurred: {error}")
            formatted_data = []
        else:
            data = response.json()
            data = data["Items"]
            formatted_data = []
            for item in data:
                formatted_timestamp = format_timestamp(timestamp=item["Timestamp"])
                if "float" in self.point_type.lower():
                    # In this case the signal is a measurement of analog magnitudes such as current or voltage
                    new_item = {
                        "Timestamp": formatted_timestamp,
                        "Value": item["Value"]
                    }
                elif "string" in self.point_type.lower():
                    new_field = item["Value"].split(" | ")
                    new_item = {
                        "Timestamp": formatted_timestamp,
                        "Index": new_field[5],
                        "Station": new_field[0],
                        "Descriptor": new_field[1],
                        "Variable": new_field[2],
                        "Value": new_field[3],
                        "Path": new_field[4],
                    }
                else:
                    # It's a digital signal like an alarm
                    new_item = {
                        "Timestamp": formatted_timestamp,
                        "Value": item["Value"]["Value"],
                        "Name": item["Value"]["Name"]
                    }
                formatted_data.append(new_item)
        finally:
            if dataframe:
                formatted_data = pd.DataFrame(formatted_data)
            self.data = formatted_data
