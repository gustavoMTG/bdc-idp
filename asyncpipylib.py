import requests
import urllib3
from datetime import datetime, timedelta
import warnings
import asyncio
import aiohttp

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Not best practice to hard code endpoint ¯\_(ツ)_/¯
API_ENDPOINT = "https://172.22.10.218/PIWebApi/dataservers/s07dsUdtehyU67YDNfE1e0JQUElTRVJWRVI/points"


def pi_sync_get_alarms_list(name_filter, max_count=3000):
    
    '''
    API request from PI database, returns a list of alarms that matchs with the nameFilter. 
    Each point of the list as a JSON.
    '''
    
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
    '''Internal function'''
    timestamp = timestamp[:-2]  # Remove the "Z" at the end and one decimal digit because timestamp only handles microsenconds (6 decimals)
    timestamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%f")
    local_date_time = timestamp - timedelta(hours=3)
    return local_date_time

def find_alarm_code(pi_query):
    '''Internal function'''
    try:
        descriptor_fraction = pi_query["Path"].split("_AL_")[1]
        descriptor_fraction = descriptor_fraction.split(".")[0]
        return descriptor_fraction
    except IndexError:
        return "Not defined"

def find_voltage(pi_query):
    '''Internal function'''
    try:
        kv_index = pi_query["Descriptor"].split(" ")
        kv_index = kv_index.index("kV")
        return pi_query["Descriptor"].split(" ")[kv_index - 1]
    except ValueError:
        return "Not defined"

def find_equipment_type(pi_query):
    '''Internal function'''
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
                break
    if not found:
        return "Not defined"

def get_converted_alarms(name_filter, max_count=3000, exclude_borr=True):
    '''This function returns a list of alarms already converted to PI_point objects'''
    alarms = pi_sync_get_alarms_list(name_filter=name_filter, max_count=max_count)
    alarms = [PIPoint(alarm) for alarm in alarms]
    if exclude_borr:
        alarms = [alarm for alarm in alarms if ".borr" not in alarm.path.lower()]
    if len(alarms) == max_count:
        warnings.warn(f"Max count reached: {max_count} signals", Warning)
    elif len(alarms) == 0:
        warnings.warn(f"No signal returned", Warning)
    return alarms


################################################################################################################
########################################### ASYNC METHODS ####################################################
def process_async_endvalue(signals):
    for signal in signals:
        signal.data = {
            "Timestamp": datetime.fromisoformat(signal.data["Timestamp"].replace("Z", "")) - timedelta(hours=3),
            "Name": signal.data["Value"]["Name"],
            "Value": signal.data["Value"]["Value"]
        }


def get_async_request_endvalue(signals):
    async def fetch(signal, session):
        try:
            async with session.get(signal.end_value, ssl=False) as response:
                signal.data = await response.json()  # Store response in data attribute
        except aiohttp.ClientError as e:
            print(f"Asynchronous error with {signal.path}: {str(e)}")

    async def fetch_all(signals):
        async with aiohttp.ClientSession() as session:
            tasks = [fetch(signal, session) for signal in signals]
            await asyncio.gather(*tasks)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(fetch_all(signals))

    process_async_endvalue(signals)


class PIPoint:
    
    '''
    This class provides methods and attributes that make easier to handle signals from the DB. 
    Mainly focused on digital signals but still handles floats.
    '''
    
    def __init__(self, pi_query):
        self.name = pi_query["Name"]
        self.descriptor = pi_query["Descriptor"]
        self.code = find_alarm_code(pi_query)
        self.path = pi_query["Path"]
        self.source = pi_query["Path"].split("_")[0][-3:].lower()
        self.point_class = pi_query["PointClass"]
        self.point_type = pi_query["PointType"]
        self.is_cb_position = True if "estado del interruptor" in pi_query["Descriptor"].lower() else False
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
        '''Returns the last point the DB has, NOT the last shift.
        Returned data has Timestamp, Value and Name.'''
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
                # In this case the signal is a measurement of analog magnitudes such as current or voltage
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
            return data
    
    def get_recorded_data(self, start_time="*-3d", end_time="*-0d", max_count=1000, dataframe=False):
        '''
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
        '''
        
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
            formated_data = []
        else:
            data = response.json()
            data = data["Items"]
            formated_data = []
            for item in data:
                formatted_timestamp = format_timestamp(timestamp=item["Timestamp"])
                if "float" in self.point_type.lower():
                    # In this case the signal is a measurment of analog magnitudes such as current or voltage
                    new_item = {
                        "Timestamp": formatted_timestamp,
                        "Value": item["Value"]
                    }
                else:
                    # It's a digital signal like an alarm
                    new_item = {
                        "Timestamp": formatted_timestamp,
                        "Value": item["Value"]["Value"],
                        "Name": item["Value"]["Name"]
                    }
                formated_data.append(new_item)
        finally:
            if dataframe:
                formated_data = pd.DataFrame(formated_data)
            return formated_data