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
import datetime
import json
import uuid
import getopt, sys
from random import randint
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTShadowClient
from mpu6050 import mpu6050
import rgbled as RGBLED
import math

#configurable settings from the config.json file
configFile = None
myConfig = {}
cfgCertsPath = ""
cfgCaPath = ""
cfgCertPath = ""
cfgKeyPath = ""
cfgThingName = ""
cfgEndPoint = ""
cfgMqttPort = ""
cfgTimeoutSec = 10
cfgRetryLimit = 3
cfgBrakeOnPosition = 6.5
cfgBrakeOffPosition = 8
cfgVibeDataSampleCnt = 50

myUUID = str(uuid.getnode())

logger = logging.getLogger(__name__)
GPIO.setmode(GPIO.BCM)

LED_LAST_STATE = ""

accelerometer = mpu6050(0x68)
accel_x = 0
accel_y = 0
accel_z = 0

#calibration offsets
accel_x_cal = 0
accel_y_cal = 0
accel_z_cal = 0

#AWS IoT Stuff
myAWSIoTMQTTClient = None
myShadowClient = None
myDeviceShadow = None
aws_session = None
myDataSendMode = "normal"
myDataInterval = 5

#Turbine rotation speed sensor
turbine_rotation_sensor_pin = 26 #pin 37
rpm = 0
elapse = 0
pulse = 0
last_pulse = 0
start_timer = time.time()

#Servo control for turbine brake
cfgBrakeOffPosition = 7.5
myBrakePosPWM = cfgBrakeOffPosition
cfgBrakeOnPosition = cfgBrakeOffPosition
turbine_servo_brake_pin = 15 #pin 10
GPIO.setwarnings(False)
GPIO.setup(turbine_servo_brake_pin, GPIO.OUT)
brakePWM = GPIO.PWM(turbine_servo_brake_pin, 50)
brake_state = "TBD"
brakePWM.start(0)

GPIO.setup(21, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)

#ADC MCP3008 used to sample the voltage level
import Adafruit_MCP3008
CLK  = 11 #pin 23
MISO = 9  #pin 21
MOSI = 10 #pin 19
CS   = 8  #pin 24
mcp = Adafruit_MCP3008.MCP3008(clk=CLK, cs=CS, miso=MISO, mosi=MOSI)

def aws_connect():
    # Init AWSIoTMQTTClient
    global myAWSIoTMQTTClient
    global myShadowClient
    global myDeviceShadow

    ca = cfgCertsPath + '/' + cfgCaPath
    key = cfgCertsPath + '/' + cfgKeyPath
    cert = cfgCertsPath + '/' + cfgCertPath

    myShadowClient = AWSIoTMQTTShadowClient(cfgThingName)
    myShadowClient.configureEndpoint(cfgEndPoint, cfgMqttPort)
    myShadowClient.configureCredentials(ca, key, cert)
    myAWSIoTMQTTClient = myShadowClient.getMQTTConnection()

    # AWSIoTMQTTClient connection configuration
    myAWSIoTMQTTClient.configureAutoReconnectBackoffTime(1, 32, 20)
    myAWSIoTMQTTClient.configureOfflinePublishQueueing(-1)  # Infinite offline Publish queueing
    myAWSIoTMQTTClient.configureDrainingFrequency(2)  # Draining: 2 Hz
    myAWSIoTMQTTClient.configureConnectDisconnectTimeout(cfgTimeoutSec)
    myAWSIoTMQTTClient.configureMQTTOperationTimeout(cfgTimeoutSec)

    for attempt in range(1, cfgRetryLimit):
        try:
            myAWSIoTMQTTClient.connect()
            print ("AWS IoT Connected")
        except connectTimeoutException:
            continue
        break

    # Shadow config
    myShadowClient.configureAutoReconnectBackoffTime(1, 32, 20)
    myShadowClient.configureConnectDisconnectTimeout(cfgTimeoutSec)
    myShadowClient.configureMQTTOperationTimeout(cfgTimeoutSec)

    for attempt in range(1, cfgRetryLimit):
        try:
            myShadowClient.connect()
            print ("AWS IoT Shadow Topic Subscribed")
        except connectTimeoutException:
            continue
        break

    myDeviceShadow = myShadowClient.createShadowHandlerWithName(cfgThingName, True)
    myDeviceShadow.shadowRegisterDeltaCallback(myShadowCallbackDelta)

    cmdTopic = str("cmd/windfarm/turbine/" + cfgThingName + "/#")
    myAWSIoTMQTTClient.subscribe(cmdTopic, 1, customCallbackCmd)
    print ("AWS IoT Command Topic Subscribed: " + cmdTopic)

def init_turbine_GPIO():                    # initialize GPIO
    GPIO.setwarnings(False)
    GPIO.setup(turbine_rotation_sensor_pin, GPIO.IN, GPIO.PUD_UP)
    print ("Turbine rotation sensor is connected")

def init_turbine_brake():
    request_turbine_brake_action("OFF")
    turbine_brake_action("OFF")

def manual_turbine_reset():
    button_state = GPIO.input(21)
    if button_state == True:
        print("Manual brake reset event")
        init_turbine_brake()
        button_state = False


def calculate_turbine_elapse(channel):      # callback function
    global pulse, start_timer, elapse
    pulse+=1                                # increase pulse by 1 whenever interrupt occurred
    elapse = time.time() - start_timer      # elapse for every 1 complete rotation made!
    start_timer = time.time()               # let current time equals to start_timer

def calculate_turbine_speed():
    global rpm,last_pulse
    if elapse !=0:   # to avoid DivisionByZero error
        rpm = 1/elapse * 60
    if pulse == last_pulse:
        rpm = 0
    else:
        last_pulse = pulse
    return rpm

def calibrate_turbine_vibe():
    global accel_x_cal, accel_y_cal, accel_z_cal
    # Read the X, Y, Z axis acceleration values
    accel = accelerometer.get_accel_data()
    accel_x = accel["x"]
    accel_y = accel["y"]
    accel_z = accel["z"]
    print('Vibration calibration: '+ str(accel_x) + ' ' + str(accel_y) + ' ' + str(accel_z))

    # Assign to the calibration variable set
    accel_x_cal = accel["x"]
    accel_y_cal = accel["y"]
    accel_z_cal = accel["z"]

    return 1

def calculate_turbine_vibe():
    global accel_x, accel_y, accel_z
    # Read the X, Y, Z axis acceleration values and print them.
    accel = accelerometer.get_accel_data()

    # Grab the X, Y, Z components from the reading and print them out.
    accel_x = accel["x"]
    accel_y = accel["y"]
    accel_z = accel["z"]

    # Apply calibration offsets
    accel_x -= accel_x_cal
    accel_y -= accel_y_cal
    accel_z -= accel_z_cal
    return 1

def get_turbine_voltage():
    # The read_adc function will get the value of the specified channel (0-7).
    refVal = mcp.read_adc(0)
    calcVolt = round(((3300/1023) * refVal) / 1000, 2)
    return calcVolt

def turbine_brake_action(action):
    global brakePWM,brake_state,myDeviceShadow,LED_LAST_STATE,myBrakePosPWM
    if action == brake_state:
        return "Already there"

    if action == "ON":
        print ("Applying turbine brake!")
        RGBLED.whiteOff()
        RGBLED.redOn()
        LED_LAST_STATE = "Red"
        myBrakePosPWM = cfgBrakeOnPosition
        brakePWM.ChangeDutyCycle(myBrakePosPWM)
        sleep(3)
        brakePWM.ChangeDutyCycle(0)

    elif action == "OFF":
        print ("Resetting turbine brake.")
        RGBLED.whiteOff()
        RGBLED.greenOn()
        LED_LAST_STATE = "Green"
        myBrakePosPWM = cfgBrakeOffPosition
        brakePWM.ChangeDutyCycle(myBrakePosPWM)
        sleep(1)
        brakePWM.ChangeDutyCycle(0)

    else:
        return "NOT AN ACTION"
    brake_state = action

    shadow_payload = {
            "state": {
                "reported": {
                    "brake_status": brake_state
                }
            }
        }
    #print shadow_payload
    still_trying = True
    try_cnt = 0
    while still_trying:
        try:
            myDeviceShadow.shadowUpdate(json.dumps(shadow_payload).encode("utf-8"), myShadowCallback, 5)
            still_trying = False
        except:
            try_cnt += 1
            print("Try " + str(try_cnt))
            sleep(1)
            if try_cnt > 10:
                still_trying = False

    return brake_state


def turbine_brake_change (newPWMval,newActionDurSec,newReturnToOff):
    global brakePWM,brake_state,LED_LAST_STATE
    RGBLED.whiteOff()
    if newPWMval == cfgBrakeOffPosition:
        RGBLED.magentaOff()
        RGBLED.greenOn()
        LED_LAST_STATE = "Green"
    else:
        RGBLED.magentaOn()
        LED_LAST_STATE = "Magenta"

    brakePWM.ChangeDutyCycle(newPWMval)

    if newActionDurSec == None:
        sleep(1)
    else:
        sleep(newActionDurSec)

    if newReturnToOff:
        #return to off position and then stop
        brakePWM.ChangeDutyCycle(cfgBrakeOffPosition)
        sleep(0.5)
        brakePWM.ChangeDutyCycle(0)
    else:
        #remove brake pressure after specified duration
        brakePWM.ChangeDutyCycle(0)

    RGBLED.magentaOff()
    RGBLED.greenOn()
    LED_LAST_STATE = "Green"


def request_turbine_brake_action(action):
    global myDeviceShadow,LED_LAST_STATE

    if action == "ON":
        RGBLED.whiteOff()
        RGBLED.redOn()
        LED_LAST_STATE = "Red"
        pass
    elif action == "OFF":
        RGBLED.whiteOff()
        RGBLED.greenOn()
        LED_LAST_STATE = "Green"
        pass
    else:
        return "NOT AN ACTION"

    new_brake_state = action

    shadow_payload = {
            "state": {
                "desired": {
                    "brake_status": new_brake_state
                }
            }
        }

    still_trying = True
    try_cnt = 0
    while still_trying:
        try:
            myDeviceShadow.shadowUpdate(json.dumps(shadow_payload).encode("utf-8"), myShadowCallback, 5)
            still_trying = False
        except:
            try_cnt += 1
            sleep(1)
            if try_cnt > 10:
                still_trying = False

    return new_brake_state


def process_data_path_changes(param,value):
    global myDeviceShadow

    shadow_payload = {
            "state": {
                "reported": {
                    param: value
                }
            }
        }
    #print shadow_payload
    still_trying = True
    try_cnt = 0
    while still_trying:
        try:
            myDeviceShadow.shadowUpdate(json.dumps(shadow_payload).encode("utf-8"), myShadowCallback, 5)
            still_trying = False
        except:
            try_cnt += 1
            print("Try " + str(try_cnt))
            sleep(1)
            if try_cnt > 10:
                still_trying = False

    return value


def init_turbine_interrupt():
    GPIO.add_event_detect(turbine_rotation_sensor_pin, GPIO.FALLING, callback = calculate_turbine_elapse, bouncetime = 20)


def myShadowCallbackDelta(payload, responseStatus, token):
    global cfgThingName,myDataSendMode,myDataInterval
    print ("delta shadow callback >> " + payload)

    if responseStatus == "delta/" + cfgThingName:
        payloadDict = json.loads(payload)
        print ("shadow delta >> " + payload)
        try:
            if "brake_status" in payloadDict["state"]:
                 turbine_brake_action(payloadDict["state"]["brake_status"])
            if "data_path" in payloadDict["state"]:
                 myDataSendMode = process_data_path_changes("data_path", payloadDict["state"]["data_path"])
            if "data_fast_interval" in payloadDict["state"]:
                 myDataInterval = int(process_data_path_changes("data_fast_interval", payloadDict["state"]["data_fast_interval"]))
        except:
            print ("delta cb error")

def myShadowCallback(payload, responseStatus, token):
    if responseStatus == "timeout":
        print("Update request " + token + " time out!")

    if responseStatus == "accepted":
        print("shadow accepted")

    if responseStatus == "rejected":
        print("Update request " + token + " rejected!")

def customCallbackCmd(client, userdata, message):
    global cfgThingName,myDataSendMode,myDataInterval,myBrakePosPWM
    if message.topic == "cmd/windfarm/turbine/" + cfgThingName + "/brake":
        payloadDict = json.loads(message.payload)
        try:
            myBrakePosPWM = float(payloadDict["pwm_value"])
            myBrakeActionDurSec = None
            if "duration_sec" in payloadDict:
                myDurSec = int(payloadDict["duration_sec"])
            else:
                myDurSec = 1

            if "return_to_off" in payloadDict:
                myRet2Off = bool(payloadDict["return_to_off"])
            else:
                myRet2Off = True

            if "duration_sec" in payloadDict:
                myDurSec = int(payloadDict["duration_sec"])
                print ("Brake change >> " + str(myBrakePosPWM) + " with duration of " + str(myDurSec) + " seconds")
            else:
                myDurSec = 1
                print ("Brake change >> " + str(myBrakePosPWM) + " with duration of 1 second")

            turbine_brake_change(myBrakePosPWM, myBrakeActionDurSec, myRet2Off)

        except:
            print ("brake change failed")


def main():
    global rpm,pulse,myAWSIoTMQTTClient,myShadowClient,myDeviceShadow,myDataSendMode,myDataInterval,LED_LAST_STATE,myBrakePosPWM
    print ("AWS IoT Wind Energy Turbine Program")
    print("DeviceID: " + myUUID)
    print("ThingName: " + cfgThingName)
    RGBLED.whiteOff()
    RGBLED.blueOn()
    my_loop_cnt = 0
    data_sample_cnt = 0
    last_reported_speed = -1
    myVibeDataList = []

    try:
        aws_connect()
        init_turbine_GPIO()
        init_turbine_interrupt()
        sleep(3)
        init_turbine_brake()
        print("Keep the turbine stationary for calibration.")
        calibrate_turbine_vibe()
        print("Turbine Monitoring Starting...")
        RGBLED.whiteOff()
        RGBLED.greenOn()
        LED_LAST_STATE = "Green"

        while True:
            calculate_turbine_speed()
            my_loop_cnt += 1
            peak_vibe = 0
            vibe = 0
            peak_vibe_x = 0
            peak_vibe_y = 0
            peak_vibe_z = 0
            del myVibeDataList[:]

            #sampling of vibration between published messages
            for data_sample_cnt in range(cfgVibeDataSampleCnt, 0, -1):
                calculate_turbine_vibe()
                vibe = math.sqrt(accel_x ** 2 + accel_y ** 2 + accel_z ** 2)

                #store the peak vibration value
                peak_vibe = max(peak_vibe, vibe)
                myVibeDataList.append(vibe)
                peak_vibe_x = max(peak_vibe_x, abs(accel_x))
                peak_vibe_y = max(peak_vibe_y, abs(accel_y))
                peak_vibe_z = max(peak_vibe_z, abs(accel_z))

                #check for a manual reset using the button
                manual_turbine_reset()
                sleep(0.1)

            avg_vibe = sum(myVibeDataList) / len(myVibeDataList)
            myReport = {
                'thing_name' : cfgThingName,
                'deviceID' : myUUID,
                'timestamp' : str(datetime.datetime.utcnow().isoformat()),
                'loop_cnt' : str(my_loop_cnt),
                'turbine_speed' : rpm,
                'turbine_rev_cnt' : pulse,
                'turbine_voltage' : str(get_turbine_voltage()),
                'turbine_vibe_x' : peak_vibe_x,
                'turbine_vibe_y' : peak_vibe_y,
                'turbine_vibe_z' : peak_vibe_z,
                'turbine_vibe_peak': peak_vibe,
                'turbine_vibe_avg': avg_vibe,
                'turbine_sample_cnt': str(len(myVibeDataList)),
                'pwm_value': myBrakePosPWM
                }

            try:
                print('rpm:{0:.0f}-RPM turbine_rev_cnt:{1} peak-vibe:{2} avg-vibe:{6} brake_pwm:{3} loop_cnt:{4} voltage:{5}'.format(rpm,pulse,peak_vibe,str(myBrakePosPWM),str(my_loop_cnt),str(get_turbine_voltage()),str(avg_vibe)) )
                if rpm > 0 or last_reported_speed != 0:
                     myTopic = "dt/windfarm/turbine/" + cfgThingName
                     last_reported_speed = rpm
                     RGBLED.whiteOff()
                     myAWSIoTMQTTClient.publish(myTopic, json.dumps(myReport), 0)
                     sleep(0.08)
                     if LED_LAST_STATE == "Red":
                         RGBLED.redOn()
                     if LED_LAST_STATE == "Magenta":
                         RGBLED.magentaOn()
                     else:
                         RGBLED.greenOn()
            except:
                logger.warning("exception while publishing")
                raise

    except (KeyboardInterrupt, SystemExit): #when you press ctrl+c
        print("Disconnecting AWS IoT")
        RGBLED.whiteOff()
        myShadowClient.disconnect()
        sleep(2)
        print("Done.\nExiting.")

if __name__ == "__main__":

    # Usage
    usageInfo = """Usage:

    python turbine.py -config <config json file>
    """

    # Read in command-line parameters
    try:
        opts, args = getopt.getopt(sys.argv[1:], "", ["config="])
        if len(opts) == 0:
            raise getopt.GetoptError("No input parameters!")
        for opt, arg in opts:
            if opt in ("--config"):
                configFile = arg
                if os.path.isfile(configFile):
                    with open(configFile) as f:
                        myConfig = json.load(f)

                    cfgThingName = myConfig['deviceThing']['thingName']
                    cfgThingName = cfgThingName.strip()

                    cfgCertsPath = myConfig['certsPath']
                    cfgCaPath = myConfig['deviceThing']['caPath']
                    cfgCertPath = myConfig['deviceThing']['certPath']
                    cfgKeyPath = myConfig['deviceThing']['keyPath']
                    cfgEndPoint = myConfig['deviceThing']['endPoint']
                    cfgMqttPort = myConfig['deviceThing']['mqttPort']
                    cfgTimeoutSec = myConfig['runtime']['connection']['timeoutSec']
                    cfgRetryLimit = myConfig['runtime']['connection']['retryLimit']
                    cfgBrakeOnPosition = myConfig['settings']['brakeServo']['onPosition']
                    cfgBrakeOffPosition = myConfig['settings']['brakeServo']['offPosition']
                    cfgVibeDataSampleCnt = myConfig['settings']['vibration']['dataSampleCnt']

    except getopt.GetoptError:
            print(usageInfo)
            exit(1)

    # Missing configuration notification
    missingConfiguration = False
    if not configFile:
        print("Missing '--config' ")
        missingConfiguration = True
    if missingConfiguration:
        exit(2)

    logging.basicConfig(filename='turbine.log',level=logging.INFO,format='%(asctime)s %(message)s')
    logger.info("Welcome to the AWS Wind Energy Turbine Device Reporter.")
    main()

