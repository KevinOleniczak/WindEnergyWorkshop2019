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
accelX = 0
accelY = 0
accelZ = 0

#calibration offsets that account for the initial static position of the accelerometer when idle
accelXCal = 0
accelYCal = 0
accelZCal = 0

#AWS IoT Stuff
awsIoTMQTTClient = None
awsShadowClient = None
turbineDeviceShadow = None
dataPublishSendMode = "normal"
dataPublishInterval = 5

#Turbine rotation speed sensor
turbine_rotation_sensor_pin = 26 #pin 37
turbineRPM = 0
turbineRpmElapse = 0
turbineRotationCnt = 0
lastTurbineRotationCnt = 0
start_timer = time.time()

#Servo control for turbine brake
turbineBrakePosPWM = cfgBrakeOffPosition
turbine_servo_brake_pin = 15 #pin 10
brakeState = "TBD"
brakeServo = None

#ADC MCP3008 used to sample the voltage level
CLK  = 11 #pin 23
MISO = 9  #pin 21
MOSI = 10 #pin 19
CS   = 8  #pin 24
adcSensor = None

#RGB LED GPIO pins
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
    global awsIoTMQTTClient, awsShadowClient, turbineDeviceShadow

    ca = cfgCertsPath + '/' + cfgCaPath
    key = cfgCertsPath + '/' + cfgKeyPath
    cert = cfgCertsPath + '/' + cfgCertPath

    awsShadowClient = AWSIoTMQTTShadowClient(cfgThingName)
    awsShadowClient.configureEndpoint(cfgEndPoint, cfgMqttPort)
    awsShadowClient.configureCredentials(ca, key, cert)
    awsIoTMQTTClient = awsShadowClient.getMQTTConnection()

    # AWSIoTMQTTClient connection configuration
    awsIoTMQTTClient.configureAutoReconnectBackoffTime(1, 32, 20)
    awsIoTMQTTClient.configureOfflinePublishQueueing(-1)  # Infinite offline Publish queueing
    awsIoTMQTTClient.configureDrainingFrequency(2)  # Draining: 2 Hz
    awsIoTMQTTClient.configureConnectDisconnectTimeout(cfgTimeoutSec)
    awsIoTMQTTClient.configureMQTTOperationTimeout(cfgTimeoutSec)

    #Attempt to connect
    for attempt in range(1, cfgRetryLimit):
        try:
            awsIoTMQTTClient.connect()
            print ("AWS IoT connected")
        except:
            continue
        break

    # Shadow config
    awsShadowClient.configureAutoReconnectBackoffTime(1, 32, 20)
    awsShadowClient.configureConnectDisconnectTimeout(cfgTimeoutSec)
    awsShadowClient.configureMQTTOperationTimeout(cfgTimeoutSec)

    for attempt in range(1, cfgRetryLimit):
        try:
            awsShadowClient.connect()
            print ("AWS IoT shadow topic subscribed")
        except:
            continue
        break

    turbineDeviceShadow = awsShadowClient.createShadowHandlerWithName(cfgThingName, True)
    turbineDeviceShadow.shadowRegisterDeltaCallback(shadowCallbackDelta)

    #Subscribe to the command topics
    cmdTopic = str("cmd/windfarm/turbine/" + cfgThingName + "/#")
    awsIoTMQTTClient.subscribe(cmdTopic, 1, customCallbackCmd)
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
    processShadowChange("brake_status", "OFF", "desired")
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
            processShadowChange("brake_status", "ON", "desired")
            turbineBrakeAction("ON")
        buttonState = False

    buttonState = GPIO.input(16) #Switch3 (S3)
    if buttonState == True:
        print("TBD Button")
        buttonState = False

    sleep(0.1)

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
    global accelXCal, accelYCal, accelZCal
    print("Keep the turbine stationary for calibration...")

    #get the speed... since the turbine has just started up, need to wait a bit and take a second reading to get am accurate value
    speed = calculateTurbineSpeed()
    sleep(3)
    speed = calculateTurbineSpeed()
    while speed > 0:
        print(">>Please stop the turbine from spinning so the calibration can proceed.")
        sleep(3)
        speed = calculateTurbineSpeed()

    accelXList = []
    accelYList = []
    accelZList = []
    #get 20 samples and average them
    for i in range(1,20):
        # Read the X, Y, Z axis acceleration values
        accel = accelerometer.get_accel_data()
        accelX = accel["x"]
        accelY = accel["y"]
        accelZ = accel["z"]
        accelXList.append(accelX)
        accelYList.append(accelY)
        accelZList.append(accelZ)
        sleep(0.1)

    # Assign to the calibration variable set
    accelXCal = sum(accelXList) / len(accelXList)
    accelYCal = sum(accelYList) / len(accelYList)
    accelZCal = sum(accelZList) / len(accelZList)
    print('Vibration calibration (XYZ): '+ str(accelXCal) + ' ' + str(accelYCal) + ' ' + str(accelZCal))

def calculateTurbineVibe():
    global accelX, accelY, accelZ
    # Read the X, Y, Z axis acceleration values
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
    global brakeServo, brakeState, turbineDeviceShadow, turbineBrakePosPWM
    if action == brakeState:
        return "Already there"

    if action == "ON":
        print ("Applying turbine brake!")
        turbineBrakePosPWM = cfgBrakeOnPosition
        brakeServo.ChangeDutyCycle(turbineBrakePosPWM)
        sleep(3)
        brakeServo.ChangeDutyCycle(0)

    elif action == "OFF":
        print ("Resetting turbine brake.")
        turbineBrakePosPWM = cfgBrakeOffPosition
        brakeServo.ChangeDutyCycle(turbineBrakePosPWM)
        sleep(1)
        brakeServo.ChangeDutyCycle(0)

    else:
        return "NOT AN ACTION"
    brakeState = action

    shadowPayload = {
            "state": {
                "reported": {
                    "brake_status": brakeState
                }
            }
        }
    #print shadowPayload
    stillTrying = True
    tryCnt = 0
    while stillTrying:
        try:
            turbineDeviceShadow.shadowUpdate(json.dumps(shadowPayload).encode("utf-8"), shadowCallback, 5)
            stillTrying = False
        except:
            tryCnt += 1
            print("Try " + str(tryCnt))
            sleep(1)
            if tryCnt > 10:
                stillTrying = False

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


#generic procedure to acknowledge shadow changes
def processShadowChange(param,value,type):
    global turbineDeviceShadow
    #type will be either desired or reported
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
            print("Try " + str(tryCnt))
            sleep(1)
            if tryCnt > 10:
                stillTrying = False
    return value

def shadowCallbackDelta(payload, responseStatus, token):
    global dataPublishSendMode, dataPublishInterval, vibe_limit
    print ("delta shadow callback >> " + payload)

    if responseStatus == "delta/" + cfgThingName:
        payloadDict = json.loads(payload)
        print ("shadow delta >> " + payload)
        try:
            if "brake_status" in payloadDict["state"]:
                 turbineBrakeAction(payloadDict["state"]["brake_status"])
            if "data_path" in payloadDict["state"]:
                 dataPublishSendMode = processShadowChange("data_path", payloadDict["state"]["data_path"], "reported")
            if "data_fast_interval" in payloadDict["state"]:
                 dataPublishInterval = int(processShadowChange("data_fast_interval", payloadDict["state"]["data_fast_interval"]), "reported")
            if "vibe_limit" in payloadDict["state"]:
                 vibe_limit = float(payloadDict["state"]["vibe_limit"])
                 processShadowChange("vibe_limit", vibe_limit, "reported")
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
    global turbineBrakePosPWM

    if message.topic == "cmd/windfarm/turbine/" + cfgThingName + "/brake":
        payloadDict = json.loads(message.payload)
        try:
            turbineBrakePosPWM = float(payloadDict["pwm_value"])
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
                print ("Brake change >> " + str(turbineBrakePosPWM) + " with duration of " + str(myDurSec) + " seconds")
            else:
                myDurSec = 1
                print ("Brake change >> " + str(turbineBrakePosPWM) + " with duration of 1 second")

            turbineBrakeChange(turbineBrakePosPWM, myBrakeActionDurSec, myRet2Off)

        except:
            print ("brake change failed")

def determineTurbineSafetyState(vibe, vibeLimit=5):
    global turbineSafetyState
    if vibe > vibeLimit:
        turbineSafetyState = 'unsafe'
        ledOn("red")
    elif vibe > (vibeLimit * 0.8): # 80% threshold check
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
                currentVibe = math.sqrt(accelX ** 2 + accelY ** 2 + accelZ ** 2)

                #store the peak vibration value
                peakVibe = max(peakVibe, currentVibe)
                vibeDataList.append(currentVibe)
                peakVibe_x = max(peakVibe_x, abs(accelX))
                peakVibe_y = max(peakVibe_y, abs(accelY))
                peakVibe_z = max(peakVibe_z, abs(accelZ))

                #check for a button press events
                checkButtons()

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
                'pwm_value': turbineBrakePosPWM
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
                    turbineBrakePosPWM,
                    loopCnt
                    )
                print(deviceMsg)

                #determine the desired topic to publish on
                if dataPublishSendMode == "faster":
                    #faster method is for use with Greengrass to Kinesis
                    publishTopic = "dt/windfarm/turbine/" + cfgThingName + "/faster"
                elif dataPublishSendMode == "cheaper":
                    #cheaper method is for use with IoT Core Basic Ingest
                    #It publishes directly to the IoT Rule
                    publishTopic = "$aws/rules/EnrichWithShadow"
                else:
                    publishTopic = "dt/windfarm/turbine/" + cfgThingName

                #make sure at least a final message is sent when the turbine is stopped
                lastReportedSpeed = turbineRPM

                #Only publish data if the turbine is spinning
                if turbineRPM > 0 or lastReportedSpeed != 0:
                    #publish with QOS 0
                    awsIoTMQTTClient.publish(publishTopic, json.dumps(devicePayload), 0)
                    ledFlash()
                else:
                    #publish with QOS 0
                    awsIoTMQTTClient.publish(publishTopic, json.dumps(devicePayload), 0)
                    ledFlash()
                    print("Turbine is idle... sleeping for 60 seconds")
                    #sleep a few times with a speed check to see if the turbine is spinning again
                    for i in range(1,12):
                        calculateTurbineSpeed()
                        lastReportedSpeed = turbineRPM
                        if turbineRPM > 0:
                            sleep(5)  #need to do this to allow elapse time to grow for a realistic calculation on the next call
                            break
                        sleep(5)  #slow down the publishing rate

            except:
                logger.warning("exception while publishing")
                raise

    except (KeyboardInterrupt, SystemExit): #when you press ctrl+c
        print("Disconnecting AWS IoT")
        ledOff()
        awsShadowClient.disconnect()
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

