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
import socket
import getopt, sys
from random import randint
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTShadowClient
from mpu6050 import mpu6050
import Adafruit_MCP3008
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
cfgBrakeOffPosition = 7.5
cfgVibeDataSampleCnt = 50

#determine a unique deviceID for this Raspberry PI to be used in the IoT message
# getnode() - Gets the hardware address as a 48-bit positive integer
turbineDeviceId = str(uuid.getnode())

#Enable logging
logger = logging.getLogger(__name__)

#Keep track of the safety state
turbineSafetyState = ""

#Keep track of the desired LED state
ledLastState = ""

#The accelerometer is used to measure vibration levels
accelerometer = None
accel_x = 0
accel_y = 0
accel_z = 0

#calibration offsets that account for the initial static position of the accelerometer when idle
accel_x_cal = 0
accel_y_cal = 0
accel_z_cal = 0

#AWS IoT Stuff
myAWSIoTMQTTClient = None
myShadowClient = None
myDeviceShadow = None
myDataSendMode = "normal"
myDataInterval = 5

#Turbine rotation speed sensor
turbine_rotation_sensor_pin = 26 #pin 37
turbineRPM = 0
turbineRpmElapse = 0
turbineRotationCnt = 0
lastTurbineRotationCnt = 0
start_timer = time.time()

#Servo control for turbine brake
myBrakePosPWM = cfgBrakeOffPosition
turbine_servo_brake_pin = 15 #pin 10
brakeState = "TBD"
brakeServo = None

#ADC MCP3008 used to sample the voltage level
CLK  = 11 #pin 23
MISO = 9  #pin 21
MOSI = 10 #pin 19
CS   = 8  #pin 24
adcSensor = None

#RGB LED
ledRedPin   = 5
ledGreenPin = 6
ledBluePin  = 13

def initTurbineGPIO():
    global GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    print ("Turbine GPIO initialized")

def initTurbineLED():
    global GPIO
    GPIO.setup(ledRedPin, GPIO.OUT)
    GPIO.setup(ledGreenPin, GPIO.OUT)
    GPIO.setup(ledBluePin, GPIO.OUT)
    ledOn("blue")
    print ("Turbine LED initialized")

def connectTurbineIoT():
    global myAWSIoTMQTTClient, myShadowClient, myDeviceShadow

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

    #Attempt to connect
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
    myDeviceShadow.shadowRegisterDeltaCallback(shadowCallbackDelta)

    #Subscribe to the command topics
    cmdTopic = str("cmd/windfarm/turbine/" + cfgThingName + "/#")
    myAWSIoTMQTTClient.subscribe(cmdTopic, 1, customCallbackCmd)
    print ("AWS IoT Command Topic Subscribed: " + cmdTopic)

def initTurbineRPMSensor():
    global GPIO
    GPIO.setup(turbine_rotation_sensor_pin, GPIO.IN, GPIO.PUD_UP)
    GPIO.add_event_detect(turbine_rotation_sensor_pin, GPIO.FALLING, callback = calculateTurbineElapse, bouncetime = 20)
    print ("Turbine rotation sensor is connected")

def initTurbineButtons():
    global GPIO
    #Setup to read 3 button switches
    GPIO.setup(21, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)
    GPIO.setup(20, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)
    GPIO.setup(16, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)
    print ("Turbine buttons enabled")

def initTurbineVoltageSensor():
    global adcSensor
    adcSensor = Adafruit_MCP3008.MCP3008(clk=CLK, cs=CS, miso=MISO, mosi=MOSI)
    print ("Turbine voltage sensor is connected")

def initTurbineVibeSensor():
    global accelerometer
    accelerometer = mpu6050(0x68)
    print ("Turbine vibration sensor is connected")

def initTurbineBrake():
    global brakeServo, GPIO
    GPIO.setup(turbine_servo_brake_pin, GPIO.OUT)
    brakeServo = GPIO.PWM(turbine_servo_brake_pin, 50)
    brakeServo.start(0)
    print ("Turbine brake connected")

def resetTurbineBrake():
    requestTurbineBrakeAction("OFF")
    turbineBrakeAction("OFF")
    print ("Turbine brake reset")

def checkButtons():
    buttonState = GPIO.input(21) #Switch1 (S1)
    if buttonState == True:
        print("Manual brake reset event")
        resetTurbineBrake()
        buttonState = False

    buttonState = GPIO.input(20) #Switch2 (S2)
    if buttonState == True:
        print("Set brake on event")
        if brakeState == False:
            requestTurbineBrakeAction("ON")
            turbineBrakeAction("ON")
        buttonState = False

    buttonState = GPIO.input(16) #Switch3 (S3)
    if buttonState == True:
        print("TBD Button")
        buttonState = False

def calculateTurbineElapse(channel):      # callback function
    global turbineRotationCnt, start_timer, turbineRpmElapse
    turbineRotationCnt+=1                   # increase cnt by 1 whenever interrupt occurred
    turbineRpmElapse = time.time() - start_timer      # time elapsed for every 1 complete rotation
    start_timer = time.time()               # let current time equal to start_timer

def calculateTurbineSpeed():
    global turbineRPM, lastTurbineRotationCnt
    if turbineRpmElapse !=0:   # to avoid DivisionByZero error
        turbineRPM = 1/turbineRpmElapse * 60
    if turbineRotationCnt == lastTurbineRotationCnt:
        turbineRPM = 0
    else:
        lastTurbineRotationCnt = turbineRotationCnt
    return turbineRPM

def calibrateTurbineVibeSensor():
    global accel_x_cal, accel_y_cal, accel_z_cal
    # Read the X, Y, Z axis acceleration values
    print("Keep the turbine stationary for calibration...")

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

def calculateTurbineVibe():
    global accel_x, accel_y, accel_z
    # Read the X, Y, Z axis acceleration values
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

def getTurbineVoltage(channel):
    # The read_adc function will get the value of the specified channel (0-7).
    refVal = adcSensor.read_adc(channel)
    calcVolt = round(((3300/1023) * refVal) / 1000, 2)
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

def turbineBrakeAction(action):
    global brakeServo, brakeState, myDeviceShadow, myBrakePosPWM
    if action == brakeState:
        return "Already there"

    if action == "ON":
        print ("Applying turbine brake!")
        myBrakePosPWM = cfgBrakeOnPosition
        brakeServo.ChangeDutyCycle(myBrakePosPWM)
        sleep(3)
        brakeServo.ChangeDutyCycle(0)

    elif action == "OFF":
        print ("Resetting turbine brake.")
        myBrakePosPWM = cfgBrakeOffPosition
        brakeServo.ChangeDutyCycle(myBrakePosPWM)
        sleep(1)
        brakeServo.ChangeDutyCycle(0)

    else:
        return "NOT AN ACTION"
    brakeState = action

    shadow_payload = {
            "state": {
                "reported": {
                    "brake_status": brakeState
                }
            }
        }
    #print shadow_payload
    still_trying = True
    try_cnt = 0
    while still_trying:
        try:
            myDeviceShadow.shadowUpdate(json.dumps(shadow_payload).encode("utf-8"), shadowCallback, 5)
            still_trying = False
        except:
            try_cnt += 1
            print("Try " + str(try_cnt))
            sleep(1)
            if try_cnt > 10:
                still_trying = False

    return brakeState


def turbineBrakeChange (newPWMval,newActionDurSec,newReturnToOff):
    global brakeServo, brakeState
    brakeServo.ChangeDutyCycle(newPWMval)

    if newActionDurSec == None:
        sleep(1)
    else:
        sleep(newActionDurSec)

    if newReturnToOff:
        #return to off position and then stop
        brakeServo.ChangeDutyCycle(cfgBrakeOffPosition)
        sleep(0.5)
        brakeServo.ChangeDutyCycle(0)
    else:
        #remove brake pressure after specified duration
        brakeServo.ChangeDutyCycle(0)


def requestTurbineBrakeAction(action):
    global myDeviceShadow
    new_brakeState = action

    shadow_payload = {
            "state": {
                "desired": {
                    "brake_status": new_brakeState
                }
            }
        }

    still_trying = True
    try_cnt = 0
    while still_trying:
        try:
            myDeviceShadow.shadowUpdate(json.dumps(shadow_payload).encode("utf-8"), shadowCallback, 5)
            still_trying = False
        except:
            try_cnt += 1
            sleep(1)
            if try_cnt > cfgRetryLimit:
                still_trying = False

    return new_brakeState


def processDataPathChanges(param,value):
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
            myDeviceShadow.shadowUpdate(json.dumps(shadow_payload).encode("utf-8"), shadowCallback, 5)
            still_trying = False
        except:
            try_cnt += 1
            print("Try " + str(try_cnt))
            sleep(1)
            if try_cnt > 10:
                still_trying = False

    return value

def shadowCallbackDelta(payload, responseStatus, token):
    global myDataSendMode, myDataInterval
    print ("delta shadow callback >> " + payload)

    if responseStatus == "delta/" + cfgThingName:
        payloadDict = json.loads(payload)
        print ("shadow delta >> " + payload)
        try:
            if "brake_status" in payloadDict["state"]:
                 turbineBrakeAction(payloadDict["state"]["brake_status"])
            if "data_path" in payloadDict["state"]:
                 myDataSendMode = processDataPathChanges("data_path", payloadDict["state"]["data_path"])
            if "data_fast_interval" in payloadDict["state"]:
                 myDataInterval = int(processDataPathChanges("data_fast_interval", payloadDict["state"]["data_fast_interval"]))
        except:
            print ("delta cb error")

def shadowCallback(payload, responseStatus, token):
    if responseStatus == "timeout":
        print("Update request " + token + " time out!")

    if responseStatus == "accepted":
        print("shadow accepted")

    if responseStatus == "rejected":
        print("Update request " + token + " rejected!")

def customCallbackCmd(client, userdata, message):
    global myBrakePosPWM

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

            turbineBrakeChange(myBrakePosPWM, myBrakeActionDurSec, myRet2Off)

        except:
            print ("brake change failed")

def determineTurbineSafetyState(vibe, vibeLimit=5):
    global turbineSafetyState
    if vibe > vibeLimit:
        turbineSafetyState = 'unsafe'
        ledOn("red")
    elif vibe > (vibeLimit * 0.8):
        turbineSafetyState = 'warning'
        ledOn("magenta")
    else:
        turbineSafetyState = 'safe'
        ledOn("green")

def ledOn(color=None):
    global ledLastState, GPIO
    #reset by turning off all 3 colors
    GPIO.output(ledRedPin, 0)
    GPIO.output(ledGreenPin, 0)
    GPIO.output(ledBluePin, 0)

    if color == None:
        color = ledLastState
    else:
        ledLastState = color

    if   color == "red":
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
    #reset by turning off all 3 colors
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
    else:  #off-on
        ledOff(ledLastState)
        if duration == None:
            sleep(0.08)
        else:
            sleep(duration)
        ledOn()

def main():
    print ("AWS IoT Wind Energy Turbine Program")
    print("DeviceID: " + turbineDeviceId)
    print("ThingName: " + cfgThingName)
    loopCnt = 0
    dataSampleCnt = 0
    lastReportedSpeed = -1
    vibeDataList = []

    try:
        initTurbineGPIO()
        initTurbineLED()
        initTurbineRPMSensor()
        initTurbineVoltageSensor()
        initTurbineButtons()
        initTurbineVibeSensor()
        calibrateTurbineVibeSensor()

        connectTurbineIoT()
        initTurbineBrake()
        resetTurbineBrake()

        print("Starting turbine monitoring...")

        while True:
            calculateTurbineSpeed()
            loopCnt += 1
            peakVibe = 0
            currentVibe = 0
            peakVibe_x = 0
            peakVibe_y = 0
            peakVibe_z = 0
            del vibeDataList[:]

            #sampling of vibration between published messages
            for dataSampleCnt in range(cfgVibeDataSampleCnt, 0, -1):
                calculateTurbineVibe()
                currentVibe = math.sqrt(accel_x ** 2 + accel_y ** 2 + accel_z ** 2)

                #store the peak vibration value
                peakVibe = max(peakVibe, currentVibe)
                vibeDataList.append(currentVibe)
                peakVibe_x = max(peakVibe_x, abs(accel_x))
                peakVibe_y = max(peakVibe_y, abs(accel_y))
                peakVibe_z = max(peakVibe_z, abs(accel_z))

                #check for a button press events
                checkButtons()
                sleep(0.1)

            avgVibe = sum(vibeDataList) / len(vibeDataList)
            determineTurbineSafetyState(peakVibe)
            turbineVoltage = getTurbineVoltage(0)  #channel 0 of the ADC

            devicePayload = {
                'thing_name' : cfgThingName,
                'deviceID' : turbineDeviceId,
                'timestamp' : str(datetime.datetime.utcnow().isoformat()),
                'loop_cnt' : str(loopCnt),
                'turbine_speed' : turbineRPM,
                'turbine_rev_cnt' : turbineRotationCnt,
                'turbine_voltage' : str(turbineVoltage),
                'turbine_vibe_x' : peakVibe_x,
                'turbine_vibe_y' : peakVibe_y,
                'turbine_vibe_z' : peakVibe_z,
                'turbine_vibe_peak': peakVibe,
                'turbine_vibe_avg': avgVibe,
                'turbine_sample_cnt': str(len(vibeDataList)),
                'pwm_value': myBrakePosPWM
                }

            try:
                deviceMsg = (
                    'Speed:{0:.0f}-RPM '
                    'Voltage:{1:.3f} '
                    'Rotations:{2} '
                    'Peak-Vibe:{3:.3f} '
                    'Avg-Vibe:{4:.3f} '
                    'Brake-PWM:{5} '
                    'LoopCnt:{6} '
                ).format(
                    turbineRPM,
                    turbineVoltage,
                    turbineRotationCnt,
                    peakVibe,
                    avgVibe,
                    myBrakePosPWM,
                    loopCnt
                    )
                print(deviceMsg)

                #Only publish data if the turbine is spinning
                if turbineRPM > 0 or lastReportedSpeed != 0:
                    if myDataSendMode == "faster":
                        #faster method is for use with Greengrass to Kinesis
                        publishTopic = "dt/windfarm/turbine/" + cfgThingName + "/faster"
                    elif myDataSendMode == "cheaper":
                        #cheaper method is for use with IoT Core Basic Ingest
                        #It publishes directly to the IoT Rule
                        publishTopic = "$aws/rules/EnrichWithShadow"
                    else:
                        publishTopic = "dt/windfarm/turbine/" + cfgThingName

                    lastReportedSpeed = turbineRPM

                    #publish with QOS 0
                    myAWSIoTMQTTClient.publish(publishTopic, json.dumps(devicePayload), 0)
                    ledFlash()

            except:
                logger.warning("exception while publishing")
                raise

    except (KeyboardInterrupt, SystemExit): #when you press ctrl+c
        print("Disconnecting AWS IoT")
        ledOff()
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

