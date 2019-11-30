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
import subprocess
import Adafruit_SSD1306
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

# configurable settings from the config.json file
configFile = None
myConfig = {}
cfgCertsPath = ""
cfgCaPath = ""
cfgCertPath = ""
cfgKeyPath = ""
cfgThingName = ""
cfgEndPoint = ""
cfgMqttPort = ""
cfgGgHost = ""
cfgTimeoutSec = 10
cfgRetryLimit = 3
cfgUseGreengrass = "no"
cfgBrakeOnPosition = 6.5
cfgBrakeOffPosition = 7.5
cfgVibeDataSampleCnt = 50
cfgBrakePressureFactor = 1

# determine a unique deviceID for this Raspberry PI to be used in the IoT message
# getnode() - Gets the hardware address as a 48-bit positive integer
turbineDeviceId = str(uuid.getnode())

# Enable logging
logger = logging.getLogger(__name__)

#Keep track of iot connection state
turbineIoTConnectedState = ""

# Keep track of the safety state
turbineSafetyState = ""

# Keep track of the desired LED state
ledLastState = ""
lastPayloadMsg = {
    'thing_name': cfgThingName,
    'deviceID': turbineDeviceId,
    'timestamp': str(datetime.utcnow().isoformat()),
    'loop_cnt': 0,
    'turbine_speed': 0,
    'turbine_rev_cnt': 0,
    'turbine_voltage': 0,
    'turbine_vibe_x': 0,
    'turbine_vibe_y': 0,
    'turbine_vibe_z': 0,
    'turbine_vibe_peak': 0,
    'turbine_vibe_avg': 0,
    'turbine_sample_cnt': 0,
    'brake_pct': 0
    }

# The accelerometer is used to measure vibration levels
accelerometer = None
accelX = 0
accelY = 0
accelZ = 0

# calibration offsets that account for the initial static position of the accelerometer when idle
accelXCal = 0
accelYCal = 0
accelZCal = 0

# AWS IoT Stuff
awsIoTMQTTClient = None
awsShadowClient = None
turbineDeviceShadow = None
dataPublishSendMode = "normal"
dataPublishHiResSendMode = "off"
dataPublishInterval = 5

# Turbine rotation speed sensor
turbine_rotation_sensor_pin = 26  # pin 37
turbineRPM = 0
turbineRpmElapse = 0
turbineRotationCnt = 0
lastTurbineRotationCnt = 0
start_timer = time.time()

# Servo control for turbine brake
turbineBrakePosPCT = 0
turbine_servo_brake_pin = 15  # pin 10
brakeState = "TBD"
brakeServo = None

# ADC MCP3008 used to sample the voltage level
CLK = 11  # pin 23
MISO = 9  # pin 21
MOSI = 10  # pin 19
CS = 8  # pin 24
adcSensor = None

# RGB LED GPIO pins
ledRedPin = 5
ledGreenPin = 6
ledBluePin = 13

oledDisplay = None
oledImage = None
oledDraw = None
oledFont = None
oledTop = 0

def initTurbineGPIO():
    global GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    print("Turbine GPIO initialized")


def initTurbineLED():
    global GPIO
    GPIO.setup(ledRedPin, GPIO.OUT)
    GPIO.setup(ledGreenPin, GPIO.OUT)
    GPIO.setup(ledBluePin, GPIO.OUT)
    ledOn("blue")
    print("Turbine LED initialized")

def initOLED():
    global oledDisplay, oledImage, oledDraw, oledTop, oledFont

    #stop the running service that is showing the IP address on boot
    #systemctl stop display_myip.service
    return_code = subprocess.call(['sudo', 'systemctl', 'stop', 'display_myip.service'])
    sleep(1)

    # 128x64 display with hardware I2C:
    RST = None     # on the PiOLED this pin isnt used
    oledDisplay = Adafruit_SSD1306.SSD1306_128_64(rst=RST)
    oledDisplay.begin()

    # Clear display.
    oledDisplay.clear()
    oledDisplay.display()

    # Create blank image for drawing.
    # Make sure to create image with mode '1' for 1-bit color.
    width = oledDisplay.width
    height = oledDisplay.height
    oledImage = Image.new('1', (width, height))

    # Get drawing object to draw on image.
    oledDraw = ImageDraw.Draw(oledImage)

    # Draw a black filled box to clear the image.
    oledDraw.rectangle((0,0,width,height), outline=0, fill=0)

    padding = -2
    oledTop = padding
    bottom = height-padding
    x = 0
    oledFont = ImageFont.load_default()
    oledDraw.text((x, oledTop),       cfgThingName,  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+8),     "IP: " + getIp(),  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+16),    "IoT: ",  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+26),    "Speed: ",  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+36),    "Voltage: ",  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+46),    "Vibe Peak: ",  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+56),    "Brake:     Pct:",  font=oledFont, fill=255)

    oledDisplay.image(oledImage)
    oledDisplay.display()

    print("OLED Display initialized")

def storeLastGreengrassHost(ggInfo, ep, port):
    msg = ggInfo
    msg['LAST_HostAddress'] = ep
    msg['LAST_PortNumber'] = port
    msg['timestamp'] = str(datetime.utcnow().isoformat())
    with open(cfgCertsPath + '/gg-last-host.json', 'w') as outfile:
        json.dump(msg, outfile)


def getLastGreengrassHost():
    ggInfo = {}
    if os.path.exists(cfgCertsPath + '/gg-last-host.json'):
        with open(cfgCertsPath + '/gg-last-host.json', 'r') as infile:
            ggInfo = json.load(infile)
    return ggInfo

def hostReachable(hostname):
    #attempt toping the host once and wait 2 seconds
    status = subprocess.call("ping -c1 -w2 " + hostname + " > /dev/null 2>&1", shell=True)
    if status == 0:
      return True
    else:
      return False

def discoverGreengrassHost(key, cert, ca):
    # call the Greengrass Discovery API to find the details of the gg group core
    url = 'https://' + cfgGgHost + ':8443/greengrass/discover/thing/' + cfgThingName
    headers = {"Content-Type": "application/json"}
    resp = {}

    for attempt in range(0, 5):
        response = get(url, headers=headers, cert=(cert, key), verify=ca)
        if response:
            resp = json.loads(response.content)
            ggCA = resp['GGGroups'][0]['CAs'][0]
            ggCA = ggCA.strip('\"')
            with open(cfgCertsPath + '/gg-group-ca.pem', 'w') as outfile:
                outfile.writelines(ggCA)
            break
        else:
            print("Error calling AWS Greengrass discovery API")
    return resp


def connectTurbineIoTAttempt(ep, port, rootca, key, cert, timeoutSec, retryLimit):
    global awsIoTMQTTClient, awsShadowClient, turbineDeviceShadow

    awsShadowClient = AWSIoTMQTTShadowClient(cfgThingName)
    awsShadowClient.configureEndpoint(ep, int(port))
    awsShadowClient.configureCredentials(rootca, key, cert)
    awsIoTMQTTClient = awsShadowClient.getMQTTConnection()

    # AWSIoTMQTTClient connection configuration
    awsIoTMQTTClient.configureAutoReconnectBackoffTime(1, 32, 20)
    awsIoTMQTTClient.configureOfflinePublishQueueing(-1)  # Infinite offline Publish queueing
    awsIoTMQTTClient.configureDrainingFrequency(2)  # Draining: 2 Hz
    awsIoTMQTTClient.configureConnectDisconnectTimeout(timeoutSec) #seconds
    awsIoTMQTTClient.configureMQTTOperationTimeout(timeoutSec) #seconds
    awsIoTMQTTClient.configureConnectDisconnectTimeout(timeoutSec)
    awsIoTMQTTClient.configureMQTTOperationTimeout(timeoutSec)
    awsIoTMQTTClient.onOnline = awsIoTClientOnConnectCallback
    awsIoTMQTTClient.onOffline = awsIoTClientOnDisconnectCallback

    # Attempt to connect
    for attempt in range(0, retryLimit):
        try:
            awsIoTMQTTClient.connect()
        except Exception as e:
            print(str(e))
            continue
        break

    # Shadow config
    awsShadowClient.configureAutoReconnectBackoffTime(1, 32, 20)
    awsShadowClient.configureConnectDisconnectTimeout(timeoutSec)
    awsShadowClient.configureMQTTOperationTimeout(timeoutSec)

    for attempt in range(0, retryLimit):
        try:
            if awsShadowClient.connect():
                print("AWS IoT shadow topic subscribed")
        except Exception as e:
            print(str(e))
            continue
        break

    turbineDeviceShadow = awsShadowClient.createShadowHandlerWithName(cfgThingName, True)
    turbineDeviceShadow.shadowRegisterDeltaCallback(shadowCallbackDelta)

    # Subscribe to the command topics
    cmdTopic = str("cmd/windfarm/turbine/" + cfgThingName + "/#")
    awsIoTMQTTClient.subscribe(cmdTopic, 1, customCallbackCmd)
    print("AWS IoT Command Topic Subscribed: " + cmdTopic)

    return True


def connectTurbineIoT():
    ca = cfgCertsPath + '/' + cfgCaPath
    key = cfgCertsPath + '/' + cfgKeyPath
    cert = cfgCertsPath + '/' + cfgCertPath
    localNetworks = ["127.0.0.1", "::1"]

    # if using Greengrass, there may be multiple addresses to reach the gg core/host.
    if cfgUseGreengrass == 'yes':
        print("Configured to use AWS Greengrass...")
        # attempt to reconnect to the last good host
        ggInfo = getLastGreengrassHost()

        #check if the last good host is still reachable, if not start over
        if not hostReachable(ggInfo['LAST_HostAddress']):
            print("Unable to reach last known Greengrass host, will rediscover.")
            ggInfo = {}

        # if not none exists, attempt discovery
        if ggInfo == {}:
            ggInfo = discoverGreengrassHost(key, cert, ca)
        else:
            print("Using last known Greengrass discovery info")

        if ggInfo == {}:
            print("Can't find a way to connect to Greengrass. Exiting.")
            quit()

        timeoutSec = 10
        retryLimit = 1
        ggCA = cfgCertsPath + '/gg-group-ca.pem'

        if 'GGGroups' in ggInfo:
            # Try them all until one connects.
            for ggg in ggInfo['GGGroups']:
                for core in ggg['Cores']:
                    for conn in core['Connectivity']:
                        #check if the provided host address is in a list of local interfaces that cannot be used
                        if conn['HostAddress'] not in localNetworks:
                            print("Attempting to connect to Greengrass at: " + conn['HostAddress'] + ":" + str(
                                conn['PortNumber']))
                            result = connectTurbineIoTAttempt(conn['HostAddress'], conn['PortNumber'], ggCA, key, cert,
                                                              timeoutSec, retryLimit)
                            if result:
                                # store last known good host,port and rootca
                                storeLastGreengrassHost(ggInfo, conn['HostAddress'], conn['PortNumber'])
                                break
                    if result:
                        break
                if result:
                    break
        else:
            result = False
            print("No Greengrass hosts discovered. Check your connection to the internet and try again.")

    else:
        # connection is to IoT Core
        print("Configured to use AWS IoT Core...")
        result = connectTurbineIoTAttempt(cfgEndPoint, cfgMqttPort, ca, key, cert, cfgTimeoutSec, cfgRetryLimit)

    return result

#the aws iot sdk provides callbacks for connect and disconnect events
def awsIoTClientOnConnectCallback():
    global turbineIoTConnectedState
    turbineIoTConnectedState = "Connected"
    updateOledDisplay()
    print('IoT connection state: ' + turbineIoTConnectedState)

def awsIoTClientOnDisconnectCallback():
    global turbineIoTConnectedState
    turbineIoTConnectedState = "Disconnected"
    updateOledDisplay()
    print('IoT connection state: ' + turbineIoTConnectedState)

def initTurbineRPMSensor():
    global GPIO
    GPIO.setup(turbine_rotation_sensor_pin, GPIO.IN, GPIO.PUD_UP)
    GPIO.add_event_detect(turbine_rotation_sensor_pin, GPIO.FALLING, callback=calculateTurbineElapse, bouncetime=20)
    print("Turbine rotation sensor is connected")


def initTurbineButtons():
    global GPIO
    # Setup to read 3 button switches
    GPIO.setup(21, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(20, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(16, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    print("Turbine buttons enabled")


def initTurbineVoltageSensor():
    global adcSensor
    adcSensor = Adafruit_MCP3008.MCP3008(clk=CLK, cs=CS, miso=MISO, mosi=MOSI)
    print("Turbine voltage sensor is connected")


def initTurbineVibeSensor():
    global accelerometer
    try:
        accelerometer = mpu6050(0x68)
        print("Turbine vibration sensor is connected")
    except:
        print("The turbine appears to be disconnected. Please check the connection.")


def initTurbineBrake():
    global brakeServo, GPIO
    GPIO.setup(turbine_servo_brake_pin, GPIO.OUT)
    brakeServo = GPIO.PWM(turbine_servo_brake_pin, 50)
    brakeServo.start(0)
    print("Turbine brake connected")


def resetTurbineBrake():
    processShadowChange("brake_status", "OFF", "reported")
    turbineBrakeAction("OFF")
    print("Turbine brake reset")

def updateOledDisplay():
    global oledDisplay, oledDraw, oledImage

    width = oledDisplay.width
    height = oledDisplay.height
    oledDraw.rectangle((0,0,width,height), outline=0, fill=0)

    x = 0
    oledDraw.text((x, oledTop),       cfgThingName,  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+8),     "IP: " + getIp(),  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+16),    "IoT:" + turbineIoTConnectedState,  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+26),    "Speed: {0:.1f}".format(lastPayloadMsg['turbine_speed']),  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+36),    "Voltage: {0:.1f}".format(lastPayloadMsg['turbine_voltage']),  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+46),    "Peak Vibe: {0:.2f}".format(lastPayloadMsg['turbine_vibe_peak']),  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+56),    "Brake:" + brakeState + " Pct: " + str(lastPayloadMsg['brake_pct']),  font=oledFont, fill=255)

    oledDisplay.image(oledImage)
    oledDisplay.display()

def clearOledDisplay():
    global oledDisplay, oledDraw, oledImage

    width = oledDisplay.width
    height = oledDisplay.height
    oledDraw.rectangle((0,0,width,height), outline=0, fill=0)

    x = 0
    oledDraw.text((x, oledTop),       cfgThingName,  font=oledFont, fill=255)
    oledDraw.text((x, oledTop+8),     "IP: " + getIp(),  font=oledFont, fill=255)

    oledDisplay.image(oledImage)
    oledDisplay.display()

def checkButtons():
    buttonState = GPIO.input(21)  # Switch1 (S1)
    if buttonState == True:
        print("Manual brake reset event")
        resetTurbineBrake()
        buttonState = False

    buttonState = GPIO.input(20)  # Switch2 (S2)
    if buttonState == True:
        if brakeState == "OFF":
            print("Toggle brake on event")
            processShadowChange("brake_status", "ON", "reported")
            turbineBrakeAction("ON")
        else:
            print("Toggle brake off event")
            processShadowChange("brake_status", "OFF", "reported")
            turbineBrakeAction("OFF")
        buttonState = False

    buttonState = GPIO.input(16)  # Switch3 (S3)
    if buttonState == True:
        print("TBD Button")
        buttonState = False

    ##debounce
    sleep(0.1)


def calculateTurbineElapse(channel):  # callback function
    global turbineRotationCnt, start_timer, turbineRpmElapse
    turbineRotationCnt += 1  # increase cnt by 1 whenever interrupt occurred
    turbineRpmElapse = time.time() - start_timer  # time elapsed for every 1 complete rotation
    start_timer = time.time()  # let current time equal to start_timer


def calculateTurbineSpeed():
    global turbineRPM, lastTurbineRotationCnt
    if turbineRpmElapse != 0:  # to avoid DivisionByZero error
        turbineRPM = 1 / turbineRpmElapse * 60
    if turbineRotationCnt == lastTurbineRotationCnt:
        turbineRPM = 0
    else:
        lastTurbineRotationCnt = turbineRotationCnt
    return turbineRPM


def checkTurbineVibeSensorAvailable():
    try:
        accel = accelerometer.get_accel_data()
        return True
    except:
        return False


def calibrateTurbineVibeSensor():
    global accelXCal, accelYCal, accelZCal
    if not checkTurbineVibeSensorAvailable():
        print("The turbine appears to be disconnected. Please check the connection.")
        return 0

    print("Keep the turbine stationary for calibration...")

    # get the speed... since the turbine has just started up, need to wait a bit and take a second reading to get am accurate value
    speed = calculateTurbineSpeed()
    sleep(3)
    speed = calculateTurbineSpeed()
    while speed > 0:
        print(">>Please stop the turbine from spinning so the calibration can proceed.")
        turbineBrakeAction("ON", False)
        sleep(3)
        speed = calculateTurbineSpeed()

    accelXList = []
    accelYList = []
    accelZList = []
    # get 20 samples and average them
    for i in range(1, 20):
        # Read the X, Y, Z axis acceleration values
        try:
            accel = accelerometer.get_accel_data()
            accelX = accel["x"]
            accelY = accel["y"]
            accelZ = accel["z"]
            accelXList.append(accelX)
            accelYList.append(accelY)
            accelZList.append(accelZ)
        except:
            print("The turbine appears to be disconnected. Please check the connection.")
        sleep(0.1)

    # Assign to the calibration variable set
    accelXCal = sum(accelXList) / len(accelXList)
    accelYCal = sum(accelYList) / len(accelYList)
    accelZCal = sum(accelZList) / len(accelZList)
    print('Vibration calibration (XYZ): ' + str(accelXCal) + ' ' + str(accelYCal) + ' ' + str(accelZCal))
    turbineBrakeAction("OFF")

def calculateTurbineVibe():
    global accelX, accelY, accelZ
    # Read the X, Y, Z axis acceleration values
    try:
        accel = accelerometer.get_accel_data()

        # Grab the X, Y, Z vales
        accelX = accel["x"]
        accelY = accel["y"]
        accelZ = accel["z"]

        # Apply calibration offsets
        accelX -= accelXCal
        accelY -= accelYCal
        accelZ -= accelZCal
        return 1
    except:
        return 0


def getTurbineVoltage(channel):
    # The read_adc function will get the value of the specified channel (0-7).
    refVal = adcSensor.read_adc(channel)
    calcVolt = round(((3300 / 1023) * refVal) / 1000, 2)
    return calcVolt


def getIp():
    IP = '0.0.0.0'
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except:
        (IP == '127.0.0.1') | (IP == '127.0.1.1')
    finally:
        s.close()
    return IP

def getBrakePWM(newPositionPct):
    try:
        newPWM = cfgBrakeOffPosition - ((cfgBrakeOffPosition - cfgBrakeOnPosition) * (newPositionPct/100))
    except:
        newPWM = cfgBrakeOffPosition
    return newPWM

def turbineBrakeAction(action, brakeRelease = True):
    global brakeServo, brakeState, turbineDeviceShadow, turbineBrakePosPCT
    if action == brakeState:
        return "Already there"

    if action == "ON":
        print("Applying turbine brake!")
        turbineBrakePosPCT = 100

        brakeServo.ChangeDutyCycle(getBrakePWM(turbineBrakePosPCT))
        sleep(3)
        if brakeRelease:
            brakeServo.ChangeDutyCycle(0)
        brakeState = action

    elif action == "OFF":
        print("Resetting turbine brake")
        turbineBrakePosPCT = 0
        brakeServo.ChangeDutyCycle(getBrakePWM(turbineBrakePosPCT))
        sleep(1)
        brakeServo.ChangeDutyCycle(0)
        brakeState = action

    else:
        return "NOT AN ACTION"

    #update the display
    updateOledDisplay()

    shadowPayload = {
        "state": {
            "reported": {
                "brake_status": brakeState
            }
        }
    }
    # print shadowPayload
    stillTrying = True
    tryCnt = 0
    while stillTrying:
        try:
            turbineDeviceShadow.shadowUpdate(json.dumps(shadowPayload).encode("utf-8"), shadowCallback, 5)
            stillTrying = False
        except:
            tryCnt += 1
            #print("Try " + str(tryCnt))
            sleep(1)
            if tryCnt > 10:
                stillTrying = False

    return brakeState


def turbineBrakeChange(newPCTval, newActionDurSec, newReturnToOff):
    global brakeServo
    brakeServo.ChangeDutyCycle(getBrakePWM(newPCTval))

    if newActionDurSec == None:
        sleep(1)
    else:
        sleep(newActionDurSec * cfgBrakePressureFactor)

    if newReturnToOff:
        # return to off position and then stop
        brakeServo.ChangeDutyCycle(cfgBrakeOffPosition)
        sleep(0.5)
        brakeServo.ChangeDutyCycle(0)
    else:
        # remove brake pressure after specified duration
        brakeServo.ChangeDutyCycle(0)


# get the latest shadow and update local variables
def initShadowVariables():
    turbineDeviceShadow.shadowGet(shadowCallbackReported, 10)


def shadowCallbackReported(payload, responseStatus, token):
    global cfgBrakeOnPosition, cfgBrakeOffPosition
    try:
        payloadDict = json.loads(payload)
        #print ("shadow Report >> " + payload)

        if "data_path" in payloadDict["state"]["reported"]:
            dataPublishSendMode = payloadDict["state"]["reported"]["data_path"]

        if "data_fast_interval" in payloadDict["state"]["reported"]:
            dataPublishInterval = int(payloadDict["state"]["reported"]["data_fast_interval"])

        if "vibe_limit" in payloadDict["state"]["reported"]:
            vibe_limit = float(payloadDict["state"]["reported"]["vibe_limit"])

        if "hires_publish_mode" in payloadDict["state"]["reported"]:
            dataPublishHiResSendMode = payloadDict["state"]["reported"]["hires_publish_mode"]

        if "brake_on_pwm" in payloadDict["state"]["reported"]:
            cfgBrakeOnPosition = float(payloadDict["state"]["reported"]["brake_on_pwm"])

        if "brake_off_pwm" in payloadDict["state"]["reported"]:
            cfgBrakeOffPosition = float(payloadDict["state"]["reported"]["brake_off_pwm"])

        print("Turbine is in sync with the shadow settings.")

    except Exception as e:
        print("Shadow get failed")
        pass


# generic procedure to acknowledge shadow changes
def processShadowChange(param, value, type):
    global turbineDeviceShadow
    # type will be either desired or reported
    shadowPayload = {
        "state": {
            type: {
                param: value
            }
        }
    }

    stillTrying = True
    tryCnt = 0
    while stillTrying:
        try:
            turbineDeviceShadow.shadowUpdate(json.dumps(shadowPayload).encode("utf-8"), shadowCallback, 5)
            stillTrying = False
        except:
            tryCnt += 1
            #print("Try " + str(tryCnt))
            sleep(1)
            if tryCnt > 10:
                stillTrying = False
    return value


def shadowCallbackDelta(payload, responseStatus, token):
    global dataPublishSendMode, dataPublishInterval, vibe_limit, dataPublishHiResSendMode, cfgBrakeOnPosition, cfgBrakeOffPosition, cfgBrakePressureFactor
    #print("delta shadow callback >> " + payload)

    if responseStatus == "delta/" + cfgThingName:
        payloadDict = json.loads(payload)
        print("shadow delta >> " + payload)
        try:
            if "brake_status" in payloadDict["state"]:
                turbineBrakeAction(payloadDict["state"]["brake_status"])
            if "data_path" in payloadDict["state"]:
                dataPublishSendMode = processShadowChange("data_path", payloadDict["state"]["data_path"], "reported")
            if "data_fast_interval" in payloadDict["state"]:
                dataPublishInterval = int(
                    processShadowChange("data_fast_interval", payloadDict["state"]["data_fast_interval"]), "reported")
            if "vibe_limit" in payloadDict["state"]:
                vibe_limit = float(payloadDict["state"]["vibe_limit"])
                processShadowChange("vibe_limit", vibe_limit, "reported")
            if "hires_publish_mode" in payloadDict["state"]:
                dataPublishHiResSendMode = payloadDict["state"]["hires_publish_mode"]
                processShadowChange("hires_publish_mode", dataPublishHiResSendMode, "reported")
            if "brake_on_pwm" in payloadDict["state"]:
                cfgBrakeOnPosition = float(payloadDict["state"]["brake_on_pwm"])
                processShadowChange("brake_on_pwm", cfgBrakeOnPosition, "reported")
            if "brake_off_pwm" in payloadDict["state"]:
                cfgBrakeOffPosition = float(payloadDict["state"]["brake_off_pwm"])
                processShadowChange("brake_off_pwm", cfgBrakeOffPosition, "reported")
            if "brake_pressure_factor" in payloadDict["state"]:
                cfgBrakePressureFactor = int(payloadDict["state"]["brake_pressure_factor"])
                processShadowChange("brake_pressure_factor", cfgBrakePressureFactor, "reported")
        except Exception as e:
            print("delta cb error: " + str(e))


def shadowCallback(payload, responseStatus, token):
    if responseStatus == "timeout":
        print("Update request " + token + " time out!")

    if responseStatus == "accepted":
        print("shadow accepted")

    if responseStatus == "rejected":
        print("Update request " + token + " rejected!")


def customCallbackCmd(client, userdata, message):
    global turbineBrakePosPCT

    if message.topic == "cmd/windfarm/turbine/" + cfgThingName + "/brake":
        payloadDict = json.loads(message.payload)
        try:
            turbineBrakePosPCT = float(payloadDict["brake_pct"])
            brakeActionDurSec = None
            if "duration_sec" in payloadDict:
                brakeActionDurSec = int(payloadDict["duration_sec"])
            else:
                brakeActionDurSec = 1

            if "return_to_off" in payloadDict:
                ret2Off = strtobool(payloadDict["return_to_off"].lower())
            else:
                ret2Off = True

            print("Brake change >> " + str(turbineBrakePosPCT) + "% with duration of " + str(brakeActionDurSec) + " seconds and return to off >> " + str(ret2Off))
            turbineBrakeChange(turbineBrakePosPCT, brakeActionDurSec, ret2Off)

        except:
            print("brake change failed")


def determineTurbineSafetyState(vibe, vibeLimit=5):
    global turbineSafetyState
    if vibe > vibeLimit:
        turbineSafetyState = 'unsafe'
        ledOn("red")
    elif vibe > (vibeLimit * 0.8):  # 80% threshold check
        turbineSafetyState = 'warning'
        ledOn("magenta")
    else:
        turbineSafetyState = 'safe'
        ledOn("green")


def ledOn(color=None):
    global ledLastState, GPIO
    # reset by turning off all 3 colors
    GPIO.output(ledRedPin, 0)
    GPIO.output(ledGreenPin, 0)
    GPIO.output(ledBluePin, 0)

    if color == None:
        color = ledLastState
    else:
        ledLastState = color

    if color == "red":
        GPIO.output(ledRedPin, 1)
    elif color == "green":
        GPIO.output(ledGreenPin, 1)
    elif color == "blue":
        GPIO.output(ledBluePin, 1)
    elif color == "magenta":
        GPIO.output(ledRedPin, 1)
        GPIO.output(ledBluePin, 1)
    elif color == "white":
        GPIO.output(ledRedPin, 1)
        GPIO.output(ledGreenPin, 1)
        GPIO.output(ledBluePin, 1)
    else:
        pass


def ledOff(remember=None):
    global ledLastState, GPIO
    # reset by turning off all 3 colors
    GPIO.output(ledRedPin, 0)
    GPIO.output(ledGreenPin, 0)
    GPIO.output(ledBluePin, 0)

    if remember == None:
        ledLastState = ''


def ledFlash(mode='off-on', duration=None):
    if mode == 'on-off':
        ledOn()
        if duration == None:
            sleep(0.08)
        else:
            sleep(duration)
        ledOff(ledLastState)
    else:  # off-on
        ledOff(ledLastState)
        if duration == None:
            sleep(0.08)
        else:
            sleep(duration)
        ledOn()


def main():
    global lastPayloadMsg
    print("AWS IoT Wind Energy Turbine Program")
    print("DeviceID: " + turbineDeviceId)
    print("ThingName: " + cfgThingName)
    loopCnt = 0
    dataSampleCnt = 0
    lastReportedSpeed = -1
    vibeDataList = []

    try:
        initTurbineGPIO()
        initTurbineLED()
        initOLED()
        initTurbineRPMSensor()
        initTurbineVoltageSensor()
        initTurbineButtons()
        initTurbineBrake()
        initTurbineVibeSensor()
        calibrateTurbineVibeSensor()

        connectTurbineIoT()
        resetTurbineBrake()
        initShadowVariables()

        print("Starting turbine monitoring...")
        publishTopicHiRes = "dt/windfarm/turbine/" + cfgThingName + "/hi-res"

        while True:
            calculateTurbineSpeed()
            loopCnt += 1
            peakVibe = 0
            currentVibe = 0
            peakVibe_x = 0
            peakVibe_y = 0
            peakVibe_z = 0
            avgVibe = 0
            del vibeDataList[:]

            # sampling of vibration between published messages
            if checkTurbineVibeSensorAvailable():
                for dataSampleCnt in range(cfgVibeDataSampleCnt, 0, -1):
                    calculateTurbineVibe()
                    currentVibe = math.sqrt(accelX ** 2 + accelY ** 2 + accelZ ** 2)

                    # store the peak vibration value
                    peakVibe = max(peakVibe, currentVibe)
                    vibeDataList.append(currentVibe)
                    peakVibe_x = max(peakVibe_x, abs(accelX))
                    peakVibe_y = max(peakVibe_y, abs(accelY))
                    peakVibe_z = max(peakVibe_z, abs(accelZ))

                    # check for a button press events
                    checkButtons()

                    if dataPublishHiResSendMode == 'vibe' and turbineRPM > 0:
                        devicePayloadHiRes = {
                            'thing_name': cfgThingName,
                            'deviceID': turbineDeviceId,
                            'timestamp': str(datetime.utcnow().isoformat()),
                            'loop_cnt': str(loopCnt),
                            'turbine_vibe_x': accelX,
                            'turbine_vibe_y': accelY,
                            'turbine_vibe_z': accelZ,
                            'turbine_vibe': currentVibe
                        }
                        # publish every vibe measurement for detailed analysis and ml
                        response = awsIoTMQTTClient.publish(publishTopicHiRes, json.dumps(devicePayloadHiRes), 0)

                if len(vibeDataList) > 0:
                    avgVibe = sum(vibeDataList) / len(vibeDataList)
                else:
                    avgVibe = 0

                determineTurbineSafetyState(peakVibe)
            else:
                print("The turbine appears to be disconnected. Please check the connection.")

            turbineVoltage = getTurbineVoltage(0)  # channel 0 of the ADC

            devicePayload = {
                'thing_name': cfgThingName,
                'deviceID': turbineDeviceId,
                'timestamp': str(datetime.utcnow().isoformat()),
                'loop_cnt': loopCnt,
                'turbine_speed': turbineRPM,
                'turbine_rev_cnt': turbineRotationCnt,
                'turbine_voltage': turbineVoltage,
                'turbine_vibe_x': peakVibe_x,
                'turbine_vibe_y': peakVibe_y,
                'turbine_vibe_z': peakVibe_z,
                'turbine_vibe_peak': peakVibe,
                'turbine_vibe_avg': avgVibe,
                'turbine_sample_cnt': len(vibeDataList),
                'brake_pct': turbineBrakePosPCT
            }
            #last payload is used by the oled Display for updates when partial info exists
            lastPayloadMsg = devicePayload

            try:
                deviceMsg = (
                    'Speed:{0:.0f}-RPM '
                    'Voltage:{1:.3f} '
                    'Rotations:{2} '
                    'Peak-Vibe:{3:.3f} '
                    'Avg-Vibe:{4:.3f} '
                    'Brake-PCT:{5} '
                    'LoopCnt:{6} '
                ).format(
                    turbineRPM,
                    turbineVoltage,
                    turbineRotationCnt,
                    peakVibe,
                    avgVibe,
                    turbineBrakePosPCT,
                    loopCnt
                )
                print(deviceMsg)

                # determine the desired topic to publish on
                if dataPublishSendMode == "faster":
                    # faster method is for use with Greengrass to Kinesis
                    publishTopic = "dt/windfarm/turbine/" + cfgThingName + "/faster"
                elif dataPublishSendMode == "cheaper":
                    # cheaper method is for use with IoT Core Basic Ingest
                    # It publishes directly to the IoT Rule
                    publishTopic = "$aws/rules/EnrichWithShadow"
                else:
                    publishTopic = "dt/windfarm/turbine/" + cfgThingName

                # make sure at least a final message is sent when the turbine is stopped
                lastReportedSpeed = turbineRPM

                # Only publish data if the turbine is spinning
                if turbineRPM > 0 or lastReportedSpeed != 0:
                    # publish with QOS 0
                    response = awsIoTMQTTClient.publish(publishTopic, json.dumps(devicePayload), 0)
                    updateOledDisplay()
                    ledFlash()
                else:
                    # publish with QOS 0
                    response = awsIoTMQTTClient.publish(publishTopic, json.dumps(devicePayload), 0)
                    updateOledDisplay()
                    ledFlash()
                    print("Turbine is idle... sleeping for 60 seconds")
                    # sleep a few times with a speed check to see if the turbine is spinning again
                    for i in range(1, 12):
                        calculateTurbineSpeed()
                        lastReportedSpeed = turbineRPM
                        if turbineRPM > 0:
                            sleep(5)  # need to do this to allow elapse time to grow for a realistic calculation on the next call
                            break
                        sleep(5)  # slow down the publishing rate
                        checkButtons()

            except:
                logger.warning("exception while publishing")
                raise

    except (KeyboardInterrupt, SystemExit):  # when you press ctrl+c
        print("Disconnecting AWS IoT")
        ledOff()
        turbineBrakeAction("OFF")
        clearOledDisplay()
        GPIO.cleanup()
        if not awsShadowClient == None:
            try:
                awsShadowClient.disconnect()
            except:
                pass
        sleep(3)
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
                    cfgGgHost = myConfig['deviceThing']['ggHost']
                    cfgTimeoutSec = myConfig['runtime']['connection']['timeoutSec']
                    cfgRetryLimit = myConfig['runtime']['connection']['retryLimit']
                    cfgUseGreengrass = myConfig['runtime']['connection']['useGreengrass']
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

    logging.basicConfig(filename='turbine.log', level=logging.INFO, format='%(asctime)s %(message)s')
    logger.info("Welcome to the AWS Wind Energy Turbine Device Reporter.")
    main()

