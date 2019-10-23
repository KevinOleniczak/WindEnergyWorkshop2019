# Copyright 2018. Amazon Web Services, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import division
import logging
import os.path
import RPi.GPIO as GPIO
from time import sleep
import time, math
from datetime import datetime
import json
import uuid
import socket
import getopt, sys
from random import randint
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTShadowClient
from mpu6050 import mpu6050
import Adafruit_MCP3008
import math
from requests import get
from distutils.util import strtobool

# configurable settings from the config.json file
config_file = None
my_config = {}
cfg_certs_path = ''
cfg_ca_path = ''
cfg_cert_path = ''
cfg_key_path = ''
cfg_thing_name = ''
cfg_end_point = ''
cfg_mqtt_port = ''
cfg_gg_host = ''
cfg_timeout_sec = 10
cfg_retry_limit = 3
cfg_use_greengrass = 'no'  # TODO: should probably be true/false rather
cfg_brake_on_position = 6.5
cfg_brake_off_position = 7.5
cfg_vibe_data_sample_cnt = 50

# determine a unique deviceID for this Raspberry Pi to be used in the IoT message
# getnode() - Gets the hardware address as a 48-bit positive integer
turbine_device_id = str(uuid.getnode())

# Enable logging
# logger = logging.getLogger(__name__)
log_path = '/home/pi/certs'
file_name = 'turbine'

logFormatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
logger = logging.getLogger()
logger.setLevel(logging.INFO)  # TODO: Can we extract this into the config.json file?

# TODO: Can we extract this into the config.json file? Also include a setting for console and/or file logging.
fileHandler = logging.FileHandler("{0}/{1}.log".format(log_path, file_name))
fileHandler.setFormatter(logFormatter)
logger.addHandler(fileHandler)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
logger.addHandler(consoleHandler)

# Keep track of the safety state
turbine_safety_state = ''  # TODO: there should be only 2 states (safe/unsafe), can we default to one? Use constants?

# Keep track of the desired LED state
led_last_state = ''  # TODO: How many states can we have? Use constants?

# The accelerometer is used to measure vibration levels
accelerometer = None
accel_x = 0
accel_y = 0
accel_z = 0

# calibration offsets that account for the initial static position of the accelerometer when idle
accel_x_cal = 0
accel_y_cal = 0
accel_z_cal = 0

# AWS IoT Stuff
aws_iot_mqtt_client = None
aws_shadow_client = None
turbine_device_shadow = None
data_publish_send_mode = 'normal'  # TODO: How many modes can we have? Use constants?
data_publish_interval = 5

# Turbine rotation speed sensor
turbine_rotation_sensor_pin = 26  # pin 37
turbine_rpm = 0
turbine_rpm_elapse = 0
turbine_rotation_count = 0
last_turbine_rotation_count = 0
start_timer = time.time()

# Servo control for turbine brake
turbine_brake_pos_pwm = cfg_brake_off_position
turbine_servo_brake_pin = 15  # pin 10
brake_state = 'TBD'  # TODO: How many states can we have? Use constants?
brake_servo = None

# ADC MCP3008 used to sample the voltage level
clk = 11  # pin 23
miso = 9  # pin 21
mosi = 10  # pin 19
cs = 8  # pin 24
adc_sensor = None

# RGB LED GPIO pins
led_red_pin = 5
led_green_pin = 6
led_blue_pin = 13


def init_turbine_gpio():
    global GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    logger.info("Turbine GPIO initialized")


def init_turbine_led():
    global GPIO
    GPIO.setup(led_red_pin, GPIO.OUT)
    GPIO.setup(led_green_pin, GPIO.OUT)
    GPIO.setup(led_blue_pin, GPIO.OUT)
    led_on('blue')  # TODO: Can we use constants here?
    logger.info("Turbine LED initialized")


def store_last_greengrass_host(gg_info, ep, port):
    msg = gg_info  # TODO: Why not refer to the gg_info dict directly in the below code?
    msg['LAST_HostAddress'] = ep
    msg['LAST_PortNumber'] = port
    msg['timestamp'] = str(datetime.utcnow().isoformat())
    with open(cfg_certs_path + '/gg-last-host.json', 'w') as outfile:
        json.dump(msg, outfile)


def get_last_greengrass_host():
    gg_info = {}
    if os.path.exists(cfg_certs_path + '/gg-last-host.json'):
        with open(cfg_certs_path + '/gg-last-host.json', 'r') as infile:
            gg_info = json.load(infile)
    return gg_info


def discover_greengrass_host(key, cert, ca):
    # call the Greengrass Discovery API to find the details of the gg group core
    url = 'https://' + cfg_gg_host + ':8443/greengrass/discover/thing/' + cfg_thing_name
    headers = {"Content-Type": "application/json"}
    resp = {}

    for attempt in range(0, 5):
        response = get(url, headers=headers, cert=(cert, key), verify=ca)
        if response:
            resp = json.loads(response.content)
            gg_ca = resp['GGGroups'][0]['CAs'][0]
            gg_ca = gg_ca.strip('\"')
            with open(cfg_certs_path + '/gg-group-ca.pem', 'w') as outfile:
                outfile.writelines(gg_ca)
            break
        else:
            print("Error calling AWS Greengrass discovery API")

    return resp


def connect_turbine_iot_attempt(ep, port, root_ca, key, cert, timeout_sec, retry_limit):
    global aws_iot_mqtt_client, aws_shadow_client, turbine_device_shadow

    aws_shadow_client = AWSIoTMQTTShadowClient(cfg_thing_name)
    aws_shadow_client.configureEndpoint(ep, port)
    aws_shadow_client.configureCredentials(root_ca, key, cert)
    aws_iot_mqtt_client = aws_shadow_client.getMQTTConnection()

    # AWSIoTMQTTClient connection configuration
    aws_iot_mqtt_client.configureAutoReconnectBackoffTime(1, 32, 20)
    aws_iot_mqtt_client.configureOfflinePublishQueueing(-1)  # Infinite offline Publish queueing
    aws_iot_mqtt_client.configureDrainingFrequency(2)  # Draining: 2 Hz
    aws_iot_mqtt_client.configureConnectDisconnectTimeout(timeout_sec)
    aws_iot_mqtt_client.configureMQTTOperationTimeout(timeout_sec)

    # Attempt to connect
    for attempt in range(0, retry_limit):
        try:
            if aws_iot_mqtt_client.connect():
                logger.info("AWS IoT connected")  # TODO: change to "MQTT client connected"?
        except Exception:
            logger.exception("Exception in aws_iot_mqtt_client.connect()")
            continue
        break

    # Shadow config
    aws_shadow_client.configureAutoReconnectBackoffTime(1, 32, 20)
    aws_shadow_client.configureConnectDisconnectTimeout(timeout_sec)
    aws_shadow_client.configureMQTTOperationTimeout(timeout_sec)

    for attempt in range(0, retry_limit):
        try:
            if aws_shadow_client.connect():
                logger.info("AWS IoT shadow topics subscribed")  # TODO: change to "shadow client connected"?
        except Exception:
            logger.exception("Exception in aws_shadow_client.connect()")
            continue
        break

    turbine_device_shadow = aws_shadow_client.createShadowHandlerWithName(cfg_thing_name, True)
    turbine_device_shadow.shadowRegisterDeltaCallback(shadow_callback_delta)

    # Subscribe to the command topics
    cmd_topic = str("cmd/windfarm/turbine/" + cfg_thing_name + "/#")  # TODO: Can we use constants here?
    aws_iot_mqtt_client.subscribe(cmd_topic, 1, custom_callback_cmd)
    logger.info("AWS IoT Command Topic Subscribed: " + cmd_topic)

    return True


def connect_turbine_iot():
    ca = cfg_certs_path + '/' + cfg_ca_path
    key = cfg_certs_path + '/' + cfg_key_path
    cert = cfg_certs_path + '/' + cfg_cert_path
    local_networks = ["127.0.0.1", "::1"]

    # if using Greengrass, there may be multiple addresses to reach the gg core/host.
    if cfg_use_greengrass == 'yes':
        logger.info("Configured to use AWS Greengrass")
        # attempt to reconnect to the last good host
        gg_info = get_last_greengrass_host()

        # if not none exists, attempt discovery
        if gg_info == {}:
            gg_info = discover_greengrass_host(key, cert, ca)
        else:
            logger.info("Using last known Greengrass discovery info")

        if gg_info == {}:
            logger.warning("Can't find a way to connect to Greengrass. Exiting.")
            quit()

        timeout_sec = 10
        retry_limit = 1
        gg_ca = cfg_certs_path + '/gg-group-ca.pem'
        result = False  # TODO: Validate this is the right location to default this setting to False.

        # TODO: should probably add a message somewhere below saying: logger.info("Configured to use AWS Greengrass")
        if 'GGGroups' in gg_info:
            # Try them all until one connects.
            for ggg in gg_info['GGGroups']:
                for core in ggg['Cores']:
                    for conn in core['Connectivity']:
                        if conn['HostAddress'] not in local_networks:
                            logger.info("Attempting to connect to Greengrass at " + conn['HostAddress'] + ":" + str(
                                conn['PortNumber']))
                            result = connect_turbine_iot_attempt(conn['HostAddress'], conn['PortNumber'], gg_ca, key,
                                                                 cert, timeout_sec, retry_limit)
                            if result:
                                # store last known good host, port and root_ca
                                store_last_greengrass_host(gg_info, conn['HostAddress'], conn['PortNumber'])
                                break
                    if result:
                        break
                if result:
                    break
        else:
            # TODO: This might not be a .warning() but rather a .info()
            logger.warning("No greengrass hosts discovered - check your connection to the internet and try again")

    else:
        # connection is to IoT Core
        logger.info("Configured to use AWS IoT Core")
        result = connect_turbine_iot_attempt(cfg_end_point, cfg_mqtt_port, ca, key, cert, cfg_timeout_sec,
                                             cfg_retry_limit)

    return result


def init_turbine_rpm_sensor():
    global GPIO
    GPIO.setup(turbine_rotation_sensor_pin, GPIO.IN, GPIO.PUD_UP)
    GPIO.add_event_detect(turbine_rotation_sensor_pin, GPIO.FALLING, callback=calculate_turbine_elapse, bouncetime=20)
    logger.info("Turbine rotation sensor is connected")


def init_turbine_buttons():
    global GPIO
    # Setup to read 3 button switches
    GPIO.setup(21, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(20, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(16, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    logger.info("Turbine buttons enabled")


def init_turbine_voltage_sensor():
    global adc_sensor
    adc_sensor = Adafruit_MCP3008.MCP3008(clk=clk, cs=cs, miso=miso, mosi=mosi)
    logger.info("Turbine voltage sensor is connected")


def init_turbine_vibe_sensor():
    global accelerometer
    try:
        accelerometer = mpu6050(0x68)
        logger.info("Turbine vibration sensor is connected")
    except Exception:
        logger.error("The turbine appears to be disconnected - please check the connection")
        logger.exception("Exception in init_turbine_vibe_sensor()")


def init_turbine_brake():
    global brake_servo, GPIO
    GPIO.setup(turbine_servo_brake_pin, GPIO.OUT)
    brake_servo = GPIO.PWM(turbine_servo_brake_pin, 50)
    brake_servo.start(0)
    logger.info("Turbine brake connected")


def reset_turbine_brake():
    process_shadow_change('brake_status', 'OFF', 'desired')
    turbine_brake_action('OFF')
    logger.info("Turbine brake reset")


def check_buttons():
    button_state = GPIO.input(21)  # Switch1 (S1)
    if button_state:
        logger.info("Manual brake reset event")
        reset_turbine_brake()
        button_state = False

    button_state = GPIO.input(20)  # Switch2 (S2)
    if button_state:
        logger.info("Set brake on event")
        if not brake_state:
            process_shadow_change("brake_status", "ON", "desired")
            turbine_brake_action("ON")
        button_state = False  # TODO: remove local button_state if not being used

    button_state = GPIO.input(16)  # Switch3 (S3)
    if button_state:
        logger.info("TBD button pressed")  # TODO: What does this message mean? Is the use of button 3 'TBD'?
        button_state = False  # TODO: remove local button_state if not being used

    sleep(0.1)


def calculate_turbine_elapse(channel):  # callback function
    global turbine_rotation_count, start_timer, turbine_rpm_elapse
    turbine_rotation_count += 1  # increase cnt by 1 whenever interrupt occurred
    turbine_rpm_elapse = time.time() - start_timer  # time elapsed for every 1 complete rotation
    start_timer = time.time()  # let current time equal to start_timer


def calculate_turbine_speed():
    global turbine_rpm, last_turbine_rotation_count
    if turbine_rpm_elapse != 0:  # to avoid DivisionByZero error
        turbine_rpm = 1 / turbine_rpm_elapse * 60
    if turbine_rotation_count == last_turbine_rotation_count:
        turbine_rpm = 0
    else:
        last_turbine_rotation_count = turbine_rotation_count
    return turbine_rpm


def check_turbine_vibe_sensor_available():
    try:
        accel = accelerometer.get_accel_data()  # TODO: Why store the return value in accel? It's local only.
        return True
    except Exception:
        logger.exception("Exception in check_turbine_vibe_sensor_available()")
        return False


def calibrate_turbine_vibe_sensor():
    global accel_x_cal, accel_y_cal, accel_z_cal
    if not check_turbine_vibe_sensor_available():
        logger.warning("The turbine appears to be disconnected - please check the connection")
        return 0

    logger.info("Keep the turbine stationary for calibration")

    # Get the current speed.
    # Since the turbine has just started up, need to wait a bit and take a second reading to get am accurate value.
    speed = calculate_turbine_speed()
    sleep(3)
    speed = calculate_turbine_speed()
    while speed > 0:
        logger.warning("Please stop the turbine from spinning so the calibration can proceed")
        sleep(3)
        speed = calculate_turbine_speed()

    accel_x_list = []
    accel_y_list = []
    accel_z_list = []
    # get 20 samples and average them
    for i in range(1, 20):
        # Read the X, Y, Z axis acceleration values
        try:
            accel = accelerometer.get_accel_data()
            _accel_x = accel['x']
            _accel_y = accel['y']
            _accel_z = accel['z']
            accel_x_list.append(_accel_x)
            accel_y_list.append(_accel_y)
            accel_z_list.append(_accel_z)
        except Exception:
            logger.error("The turbine appears to be disconnected - please check the connection")
            logger.exception("Exception in calibrate_turbine_vibe_sensor()")
        sleep(0.1)

    # Assign to the calibration variable set
    accel_x_cal = sum(accel_x_list) / len(accel_x_list)
    accel_y_cal = sum(accel_y_list) / len(accel_y_list)
    accel_z_cal = sum(accel_z_list) / len(accel_z_list)
    logger.info(
        "Vibration calibration - X: " + str(accel_x_cal) + " Y: " + str(accel_y_cal) + " Z: " + str(accel_z_cal))


def calculate_turbine_vibe():
    global accel_x, accel_y, accel_z
    # Read the X, Y, Z axis acceleration values
    try:
        accel = accelerometer.get_accel_data()

        # Grab the X, Y, Z vales
        accel_x = accel["x"]
        accel_y = accel["y"]
        accel_z = accel["z"]

        # Apply calibration offsets
        accel_x -= accel_x_cal
        accel_y -= accel_y_cal
        accel_z -= accel_z_cal
        return 1
    except Exception:
        logger.exception("Exception in calculate_turbine_vibe()")
        return 0


def get_turbine_voltage(channel):
    # The read_adc function will get the value of the specified channel (0-7).
    ref_val = adc_sensor.read_adc(channel)
    calc_volt = round(((3300 / 1023) * ref_val) / 1000, 2)
    return calc_volt


def get_ip():
    ip = '0.0.0.0'
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        logger.exception("Exception in get_ip()")
        (ip == '127.0.0.1') | (ip == '127.0.1.1')
    finally:
        s.close()
    logger.debug("IP address is " + ip)
    return ip


def turbine_brake_action(action):
    global brake_servo, brake_state, turbine_device_shadow, turbine_brake_pos_pwm
    if action == brake_state:
        return "Already there"  # TODO: What is this return string used for? It doesn't sound very good.

    if action == 'ON':  # TODO: Can we use constants here?
        logger.info("Applying turbine brake")
        turbine_brake_pos_pwm = cfg_brake_on_position
        brake_servo.ChangeDutyCycle(turbine_brake_pos_pwm)
        sleep(3)
        brake_servo.ChangeDutyCycle(0)

    elif action == "OFF":  # TODO: Can we use constants here?
        logger.info("Resetting turbine brake")
        turbine_brake_pos_pwm = cfg_brake_off_position
        brake_servo.ChangeDutyCycle(turbine_brake_pos_pwm)
        sleep(1)
        brake_servo.ChangeDutyCycle(0)

    else:
        return "NOT AN ACTION"  # TODO: What is this return string used for?
    brake_state = action

    shadow_payload = {
        "state": {
            "reported": {
                "brake_status": brake_state
            }
        }
    }
    # print shadow_payload  # TODO: Remove this line if not being used.
    still_trying = True
    try_count = 0
    while still_trying:
        try:
            turbine_device_shadow.shadowUpdate(json.dumps(shadow_payload).encode("utf-8"), shadow_callback, 5)
            still_trying = False
        except Exception:
            logger.exception("Exception in turbine_brake_action()::while")  # TODO: Can be possibly suppressed.
            try_count += 1
            logger.info("Try " + str(try_count))
            sleep(1)
            if try_count > 10:
                still_trying = False

    return brake_state


def turbine_brake_change(new_pwm_val, new_action_dur_sec, new_return_to_off):
    global brake_servo, brake_state
    brake_servo.ChangeDutyCycle(new_pwm_val)

    if new_action_dur_sec is None:
        sleep(1)
    else:
        sleep(new_action_dur_sec)

    if new_return_to_off:
        # return to off position and then stop
        brake_servo.ChangeDutyCycle(cfg_brake_off_position)
        sleep(0.5)
        brake_servo.ChangeDutyCycle(0)
    else:
        # remove brake pressure after specified duration
        brake_servo.ChangeDutyCycle(0)


# generic procedure to acknowledge shadow changes
def process_shadow_change(param, value, type):
    global turbine_device_shadow
    # type will be either desired or reported
    shadow_payload = {
        "state": {
            type: {
                param: value
            }
        }
    }

    # TODO: This part of the code looks (almost) identical as the one in turbine_brake_action. Extract into method?
    still_trying = True
    try_count = 0
    while still_trying:
        try:
            turbine_device_shadow.shadowUpdate(json.dumps(shadow_payload).encode("utf-8"), shadow_callback, 5)
            still_trying = False
        except Exception:
            logger.exception("Exception in process_shadow_change()::while")  # TODO: Can be possibly suppressed.
            try_count += 1
            logger.info("Try " + str(try_count))
            sleep(1)
            if try_count > 10:
                still_trying = False
    return value


def shadow_callback_delta(payload, response_status, token):
    global data_publish_send_mode, data_publish_interval, vibe_limit  # TODO: vibe_limit is undefined at Global level
    logger.info("Delta shadow callback: " + payload)

    if response_status == 'delta/' + cfg_thing_name:
        payload_dict = json.loads(payload)
        logger.info("Shadow delta: " + payload)  # TODO: Why are we printing the contents of 'payload' again?
        try:
            if 'brake_status' in payload_dict['state']:  # TODO: Can we use constants here?
                turbine_brake_action(payload_dict['state']['brake_status'])
            if 'data_path' in payload_dict['state']:  # TODO: Can we use constants here?
                data_publish_send_mode = process_shadow_change('data_path', payload_dict['state']['data_path'],
                                                               'reported')
            if 'data_fast_interval' in payload_dict['state']:  # TODO: Can we use constants here?
                # TODO: Is this line of code correct? Seems like there was a bracket misplaced.
                data_publish_interval = int(
                    process_shadow_change('data_fast_interval', payload_dict['state']['data_fast_interval'],
                                          'reported'))
            if 'vibe_limit' in payload_dict['state']:  # TODO: Can we use constants here?
                vibe_limit = float(payload_dict['state']['vibe_limit'])
                process_shadow_change('vibe_limit', vibe_limit, 'reported')
        except Exception:
            logger.exception("Exception in shadow_callback_delta()::try")


def shadow_callback(payload, response_status, token):
    if response_status == 'timeout':
        logger.warning("Update request " + token + " timed out")

    if response_status == 'accepted':
        logger.info("Shadow accepted")

    if response_status == 'rejected':
        logger.error("Update request " + token + " rejected")


def custom_callback_cmd(client, userdata, message):
    global turbine_brake_pos_pwm

    if message.topic == 'cmd/windfarm/turbine/' + cfg_thing_name + '/brake':  # TODO: Can we use constants here?
        payload_dict = json.loads(message.payload)
        try:
            turbine_brake_pos_pwm = float(payload_dict['pwm_value'])
            brake_action_dur_sec = None
            if 'duration_sec' in payload_dict:
                my_dur_sec = int(payload_dict['duration_sec'])  # TODO: Is this return value used anywhere? It's local.
            else:
                my_dur_sec = 1  # TODO: Is this return value used anywhere? It's local.

            if 'return_to_off' in payload_dict:  # TODO: Can we use constants here?
                ret2_off = strtobool(payload_dict['return_to_off'].lower())
            else:
                ret2_off = True

            if 'duration_sec' in payload_dict:  # TODO: Can we use constants here?
                my_dur_sec = int(payload_dict['duration_sec'])
                logger.info(
                    "Brake change " + str(turbine_brake_pos_pwm) + " with duration of " + str(my_dur_sec) + " seconds")
            else:
                my_dur_sec = 1
                logger.info("Brake change " + str(turbine_brake_pos_pwm) + " with duration of 1 second")

            turbine_brake_change(turbine_brake_pos_pwm, brake_action_dur_sec, ret2_off)

        except Exception:
            logger.error("Brake change failed")
            logger.exception("Exception in custom_callback_cmd()::try")


def determine_turbine_safety_state(vibe, _vibe_limit=5):
    global turbine_safety_state
    if vibe > _vibe_limit:
        turbine_safety_state = 'unsafe'  # TODO: Can we use constants here?
        led_on('red')  # TODO: Can we use constants here?
    elif vibe > (_vibe_limit * 0.8):  # 80% threshold check
        turbine_safety_state = 'warning'  # TODO: Can we use constants here?
        led_on('magenta')  # TODO: Can we use constants here?
    else:
        turbine_safety_state = 'safe'  # TODO: Can we use constants here?
        led_on('green')  # TODO: Can we use constants here?


def led_on(color=None):
    global led_last_state, GPIO
    # reset by turning off all 3 colors
    GPIO.output(led_red_pin, 0)  # TODO: Can we use constants here?
    GPIO.output(led_green_pin, 0)  # TODO: Can we use constants here?
    GPIO.output(led_blue_pin, 0)  # TODO: Can we use constants here?

    if color is None:
        color = led_last_state
    else:
        led_last_state = color

    if color == 'red':  # TODO: Can we use constants here?
        GPIO.output(led_red_pin, 1)
    elif color == 'green':  # TODO: Can we use constants here?
        GPIO.output(led_green_pin, 1)
    elif color == 'blue':  # TODO: Can we use constants here?
        GPIO.output(led_blue_pin, 1)
    elif color == 'magenta':  # TODO: Can we use constants here?
        GPIO.output(led_red_pin, 1)
        GPIO.output(led_blue_pin, 1)
    elif color == 'white':  # TODO: Can we use constants here?
        GPIO.output(led_red_pin, 1)
        GPIO.output(led_green_pin, 1)
        GPIO.output(led_blue_pin, 1)
    else:
        pass


def led_off(remember=None):
    global led_last_state, GPIO
    # reset by turning off all 3 colors
    GPIO.output(led_red_pin, 0)
    GPIO.output(led_green_pin, 0)
    GPIO.output(led_blue_pin, 0)

    if remember is None:
        led_last_state = ''


def led_flash(mode='off-on', duration=None):
    if mode == 'on-off':  # TODO: Can we use constants here? How many modes are there?
        led_on()
        if duration is None:
            sleep(0.08)  # TODO: Can we use constants here?
        else:
            sleep(duration)
        led_off(led_last_state)
    else:  # off-on
        led_off(led_last_state)
        if duration is None:
            sleep(0.08)  # TODO: Can we use constants here?
        else:
            sleep(duration)
        led_on()


def main():
    logger.info("AWS IoT Wind Energy Turbine Program")
    logger.info("DeviceID: " + turbine_device_id)
    logger.info("ThingName: " + cfg_thing_name)
    loop_count = 0
    data_sample_count = 0  # TODO: Is this local variable used?
    last_reported_speed = -1  # TODO: Is this local variable used?
    vibe_data_list = []

    try:
        init_turbine_gpio()
        init_turbine_led()
        init_turbine_rpm_sensor()
        init_turbine_voltage_sensor()
        init_turbine_buttons()
        init_turbine_vibe_sensor()
        calibrate_turbine_vibe_sensor()

        connect_turbine_iot()
        init_turbine_brake()
        reset_turbine_brake()

        logger.info("Starting turbine monitoring")

        while True:
            calculate_turbine_speed()
            loop_count += 1
            peak_vibe = 0
            current_vibe = 0  # TODO: Is this local variable used?
            peak_vibe_x = 0
            peak_vibe_y = 0
            peak_vibe_z = 0
            avg_vibe = 0
            del vibe_data_list[:]

            # Sampling of vibration between published messages
            if check_turbine_vibe_sensor_available():
                for data_sample_count in range(cfg_vibe_data_sample_cnt, 0, -1):
                    calculate_turbine_vibe()
                    current_vibe = math.sqrt(accel_x ** 2 + accel_y ** 2 + accel_z ** 2)

                    # Store the peak vibration value
                    peak_vibe = max(peak_vibe, current_vibe)
                    vibe_data_list.append(current_vibe)
                    peak_vibe_x = max(peak_vibe_x, abs(accel_x))
                    peak_vibe_y = max(peak_vibe_y, abs(accel_y))
                    peak_vibe_z = max(peak_vibe_z, abs(accel_z))

                    # Check for a button press events
                    check_buttons()

                if len(vibe_data_list) > 0:
                    avg_vibe = sum(vibe_data_list) / len(vibe_data_list)
                else:
                    avg_vibe = 0

                determine_turbine_safety_state(peak_vibe)
            else:
                logger.warning("The turbine appears to be disconnected - please check the connection")

            turbine_voltage = get_turbine_voltage(0)  # channel 0 of the ADC

            device_payload = {
                'thing_name': cfg_thing_name,
                'deviceID': turbine_device_id,
                'timestamp': str(datetime.utcnow().isoformat()),
                'loop_count': str(loop_count),
                'turbine_speed': turbine_rpm,
                'turbine_rev_cnt': turbine_rotation_count,
                'turbine_voltage': str(turbine_voltage),
                'turbine_vibe_x': peak_vibe_x,
                'turbine_vibe_y': peak_vibe_y,
                'turbine_vibe_z': peak_vibe_z,
                'turbine_vibe_peak': peak_vibe,
                'turbine_vibe_avg': avg_vibe,
                'turbine_sample_cnt': str(len(vibe_data_list)),
                'pwm_value': turbine_brake_pos_pwm
            }

            try:
                device_msg = (
                    'Speed:{0:.0f}-RPM '
                    'Voltage:{1:.3f} '
                    'Rotations:{2} '
                    'Peak-Vibe:{3:.3f} '
                    'Avg-Vibe:{4:.3f} '
                    'Brake-PWM:{5} '
                    'LoopCnt:{6} '
                ).format(
                    turbine_rpm,
                    turbine_voltage,
                    turbine_rotation_count,
                    peak_vibe,
                    avg_vibe,
                    turbine_brake_pos_pwm,
                    loop_count
                )
                logger.info(device_msg)

                # determine the desired topic to publish on
                if data_publish_send_mode == 'faster':  # TODO: Can we use constants here?
                    # faster method is for use with Greengrass to Kinesis
                    publish_topic = 'dt/windfarm/turbine/' + cfg_thing_name + '/faster'
                elif data_publish_send_mode == 'cheaper':  # TODO: Can we use constants here?
                    # cheaper method is for use with IoT Core Basic Ingest
                    # It publishes directly to the IoT Rule
                    publish_topic = '$aws/rules/EnrichWithShadow'
                else:
                    publish_topic = 'dt/windfarm/turbine/' + cfg_thing_name

                # make sure at least a final message is sent when the turbine is stopped
                last_reported_speed = turbine_rpm

                # Only publish data if the turbine is spinning
                if turbine_rpm > 0 or last_reported_speed != 0:
                    # publish with QOS 0
                    # TODO: Why store 'response' if it's not used?
                    response = aws_iot_mqtt_client.publish(publish_topic, json.dumps(device_payload), 0)
                    led_flash()
                else:
                    # Publish with QOS 0
                    # TODO: Why store 'response' if it's not used?
                    response = aws_iot_mqtt_client.publish(publish_topic, json.dumps(device_payload), 0)
                    led_flash()
                    logger.info("Turbine is idle - sleeping for 60 seconds")
                    # sleep a few times with a speed check to see if the turbine is spinning again
                    for i in range(1, 12):
                        calculate_turbine_speed()
                        last_reported_speed = turbine_rpm  # TODO: Is this local variable used?
                        if turbine_rpm > 0:
                            # TODO: Isn't that backoff already implemented elsewhere?
                            # need to do this to allow elapse time to grow for a realistic calculation on the next call
                            sleep(5)
                            break
                        sleep(5)  # slow down the publishing rate

            except Exception:
                logger.warning("Exception while publishing in main()")
                raise

    except (KeyboardInterrupt, SystemExit):  # when you press ctrl+c
        logger.info("Disconnecting from AWS IoT")
        led_off()
        if aws_shadow_client is not None:
            aws_shadow_client.disconnect()
        sleep(2)
        logger.info("Done, exiting.")
        logging.shutdown()


if __name__ == '__main__':

    # Usage
    usage_info = """Usage:

    python turbine.py -config <config json file>
    """

    # Read in command-line parameters
    try:
        opts, args = getopt.getopt(sys.argv[1:], '', ['config='])
        if len(opts) == 0:
            raise getopt.GetoptError("No input parameters")
        for opt, arg in opts:
            if opt in '--config':
                config_file = arg
                if os.path.isfile(config_file):
                    with open(config_file) as f:
                        my_config = json.load(f)

                    cfg_thing_name = my_config['deviceThing']['thingName']
                    cfg_thing_name = cfg_thing_name.strip()

                    cfg_certs_path = my_config['certsPath']
                    cfg_ca_path = my_config['deviceThing']['caPath']
                    cfg_cert_path = my_config['deviceThing']['certPath']
                    cfg_key_path = my_config['deviceThing']['keyPath']
                    cfg_end_point = my_config['deviceThing']['endPoint']
                    cfg_mqtt_port = my_config['deviceThing']['mqttPort']
                    cfg_gg_host = my_config['deviceThing']['ggHost']
                    cfg_timeout_sec = my_config['runtime']['connection']['timeoutSec']
                    cfg_retry_limit = my_config['runtime']['connection']['retryLimit']
                    cfg_use_greengrass = my_config['runtime']['connection']['useGreengrass']  # TODO: Should be boolean
                    cfg_brake_on_position = my_config['settings']['brakeServo']['onPosition']
                    cfg_brake_off_position = my_config['settings']['brakeServo']['offPosition']
                    cfg_vibe_data_sample_cnt = my_config['settings']['vibration']['dataSampleCnt']

    except getopt.GetoptError:
        print(usage_info)
        logger.exception("GetoptError exception in __main__")
        exit(1)

    # Missing configuration notification
    missingConfiguration = False
    if not config_file:
        print("Missing '--config'")
        missingConfiguration = True
    if missingConfiguration:
        exit(2)

    # logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    # logging.basicConfig(filename='/home/pi/certs/turbine.log', level=logging.DEBUG,
    #                     format='%(asctime)s - %(levelname)s - %(message)s')
    logger.info("Welcome to the AWS Wind Energy Turbine Device Reporter")
    main()
