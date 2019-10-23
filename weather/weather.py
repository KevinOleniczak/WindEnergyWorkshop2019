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
import math
from requests import get
from distutils.util import strtobool

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
cfgGgHost = ""
cfgTimeoutSec = 10
cfgRetryLimit = 3
cfgUseGreengrass = "no"

#determine a unique deviceID for this Raspberry PI to be used in the IoT message
# getnode() - Gets the hardware address as a 48-bit positive integer
weatherDeviceId = str(uuid.getnode())

#Enable logging
logger = logging.getLogger(__name__)

#AWS IoT Stuff
awsIoTMQTTClient = None
awsShadowClient = None
weatherDeviceShadow = None
dataPublishSendMode = "normal"
dataPublishInterval = 5

#Annemometer rotation speed sensor
wind_speed_sensor_pin = 22 #pin 15
lastWindSpeedRotationCnt = 0
windSpeedRPM = 0
windSpeedMPH = 0
start_timer = time.time()
weatherRotationCnt = 0
weatherSpeedElapse = 0

#RGB LED GPIO pins
ledRedPin   = 5
ledGreenPin = 6
ledBluePin  = 13

def initGPIO():
    global GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    print ("GPIO initialized")

def initLED():
    global GPIO
    GPIO.setup(ledRedPin, GPIO.OUT)
    GPIO.setup(ledGreenPin, GPIO.OUT)
    GPIO.setup(ledBluePin, GPIO.OUT)
    ledOn("blue")
    print ("LED initialized")

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

def discoverGreengrassHost(key, cert, ca):
    #call the Greengrass Discovery API to find the details of the gg group core
    url = 'https://' + cfgGgHost + ':8443/greengrass/discover/thing/' + cfgThingName
    headers = {"Content-Type":"application/json"}

    for attempt in range(0, 5):
        response = get(url, headers=headers, cert=(cert,key),  verify=ca)
        if response:
            resp = json.loads(response.content)
            ggCA = resp['GGGroups'][0]['CAs'][0]
            ggCA = ggCA.strip('\"')
            with open(cfgCertsPath + '/gg-group-ca.pem', 'w') as outfile:
                outfile.writelines(ggCA)
            break
        else:
            print("Error calling AWS Greengrass discovery API")
            resp = {}
    return resp

def connectIoTAttempt(ep, port, rootca, key, cert, timeoutSec, retryLimit):
    global awsIoTMQTTClient, awsShadowClient, weatherDeviceShadow

    awsShadowClient = AWSIoTMQTTShadowClient(cfgThingName)
    awsShadowClient.configureEndpoint(ep, port)
    awsShadowClient.configureCredentials(rootca, key, cert)
    awsIoTMQTTClient = awsShadowClient.getMQTTConnection()

    # AWSIoTMQTTClient connection configuration
    awsIoTMQTTClient.configureAutoReconnectBackoffTime(1, 32, 20)
    awsIoTMQTTClient.configureOfflinePublishQueueing(-1)  # Infinite offline Publish queueing
    awsIoTMQTTClient.configureDrainingFrequency(2)  # Draining: 2 Hz
    awsIoTMQTTClient.configureConnectDisconnectTimeout(timeoutSec)
    awsIoTMQTTClient.configureMQTTOperationTimeout(timeoutSec)

    #Attempt to connect
    for attempt in range(0, retryLimit):
        try:
            if awsIoTMQTTClient.connect():
                print ("AWS IoT connected")
                ledOn("green")
        except Exception,e:
            print str(e)
            continue
        break

    # Shadow config
    awsShadowClient.configureAutoReconnectBackoffTime(1, 32, 20)
    awsShadowClient.configureConnectDisconnectTimeout(timeoutSec)
    awsShadowClient.configureMQTTOperationTimeout(timeoutSec)

    for attempt in range(0, retryLimit):
        try:
            if awsShadowClient.connect():
                print ("AWS IoT shadow topic subscribed")
        except Exception,e:
            print str(e)
            continue
        break

    weatherDeviceShadow = awsShadowClient.createShadowHandlerWithName(cfgThingName, True)
    weatherDeviceShadow.shadowRegisterDeltaCallback(shadowCallbackDelta)

    #Subscribe to the command topics
    cmdTopic = str("cmd/windfarm/weather/" + cfgThingName + "/#")
    awsIoTMQTTClient.subscribe(cmdTopic, 1, customCallbackCmd)
    print ("AWS IoT Command Topic Subscribed: " + cmdTopic)

    return True

def connectIoT():
    ca = cfgCertsPath + '/' + cfgCaPath
    key = cfgCertsPath + '/' + cfgKeyPath
    cert = cfgCertsPath + '/' + cfgCertPath
    localNetworks = ["127.0.0.1", "::1"]

    #if using Greengrass, there may be multiple addresses to reach the gg core/host.
    if cfgUseGreengrass == 'yes':
        print("Configured to use AWS Greengrass...")
        #attempt to reconnect to the last good host
        ggInfo = getLastGreengrassHost()

        #if not none exists, attempt discovery
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
            #Try them all until one connects.
            for ggg in ggInfo['GGGroups']:
                for core in ggg['Cores']:
                    for conn in core['Connectivity']:
                        if conn['HostAddress'] not in localNetworks:
                            print("Attempting to connect to Greengrass at: " + conn['HostAddress'] + ":" + str(conn['PortNumber']))
                            result = connectIoTAttempt(conn['HostAddress'], conn['PortNumber'], ggCA, key, cert, timeoutSec, retryLimit)
                            if result:
                                #store last known good host,port and rootca
                                storeLastGreengrassHost(ggInfo, conn['HostAddress'], conn['PortNumber'])
                                break
                    if result:
                        break
                if result:
                    break
        else:
            result = False
            print("No greengrass hosts discovered. Check your connection to the internet and try again.")

    else:
        #connection is to IoT Core
        print("Configured to use AWS IoT Core...")
        result = connectIoTAttempt(cfgEndPoint, cfgMqttPort, ca, key, cert, cfgTimeoutSec, cfgRetryLimit)

    return result

def initWindSpeedSensor():
    global GPIO
    GPIO.setup(wind_speed_sensor_pin, GPIO.IN, GPIO.PUD_UP)
    GPIO.add_event_detect(wind_speed_sensor_pin, GPIO.BOTH, callback = windSpeedSensorCallback, bouncetime = 100)
    print ("Wind speed sensor is connected")

def initButtons():
    global GPIO
    #Setup to read 3 button switches
    GPIO.setup(21, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)
    GPIO.setup(20, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)
    GPIO.setup(16, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)
    print ("Buttons enabled")

def checkButtons():
    buttonState = GPIO.input(21) #Switch1 (S1)
    if buttonState == True:
        #Placeholder
        buttonState = False

    buttonState = GPIO.input(20) #Switch2 (S2)
    if buttonState == True:
        #Placeholder
        buttonState = False

    buttonState = GPIO.input(16) #Switch3 (S3)
    if buttonState == True:
        #Placeholder
        buttonState = False

    sleep(0.1)

def calculate_wind_speed():
    global windSpeedRPM,windSpeedMPH,lastWindSpeedRotationCnt
    if weatherSpeedElapse !=0:   # to avoid DivisionByZero error
        windSpeedRPM = 1/weatherSpeedElapse * 60
    if weatherRotationCnt == lastWindSpeedRotationCnt:
        windSpeedRPM = 0
    else:
        lastWindSpeedRotationCnt = weatherRotationCnt
    windSpeedMPH = 2.23694 * (2*windSpeedRPM*0.0078)      # calculate M/sec
    return windSpeedRPM

def windSpeedSensorCallback(channel):
  # Called if sensor output changes
  global weatherRotationCnt, start_timer, weatherSpeedElapse
  weatherRotationCnt+=1                                # increase weatherRotationCnt by 1 whenever interrupt occurred
  weatherSpeedElapse = time.time() - start_timer      # weatherSpeedElapse for every 1 complete rotation made!
  start_timer = time.time()               # let current time equals to start_timer
  #calculate_wind_speed()

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

#generic procedure to acknowledge shadow changes
def processShadowChange(param,value,type):
    global weatherDeviceShadow
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
            weatherDeviceShadow.shadowUpdate(json.dumps(shadowPayload).encode("utf-8"), shadowCallback, 5)
            stillTrying = False
        except:
            tryCnt += 1
            print("Try " + str(tryCnt))
            sleep(1)
            if tryCnt > 10:
                stillTrying = False
    return value

def shadowCallbackDelta(payload, responseStatus, token):
    print ("delta shadow callback >> " + payload)

    if responseStatus == "delta/" + cfgThingName:
        payloadDict = json.loads(payload)
        print ("shadow delta >> " + payload)
        try:
            Pass
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
    if message.topic == "cmd/windfarm/weather/" + cfgThingName + "/settings":
        payloadDict = json.loads(message.payload)

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
    print ("AWS IoT Wind Energy Weather Station Program")
    print("DeviceID: " + weatherDeviceId)
    print("ThingName: " + cfgThingName)
    loopCnt = 0
    dataSampleCnt = 0
    lastReportedSpeed = -1
    vibeDataList = []

    try:
        initGPIO()
        initLED()
        initWindSpeedSensor()
        initButtons()
        connectIoT()

        print("Starting weather station monitoring...")

        while True:
            loopCnt += 1
            sleep(5)
            #check for a button press events
            checkButtons()
            calculate_wind_speed()

            devicePayload = {
                'thing_name' : cfgThingName,
                'deviceID' : weatherDeviceId,
                'timestamp' : str(datetime.utcnow().isoformat()),
                'loop_cnt' : str(loopCnt),
                'wind_speed' : windSpeedMPH
                }

            try:
                deviceMsg = (
                    'Wind speed:{0:.2f}-MPH '
                    'LoopCnt:{1} '
                ).format(
                    windSpeedMPH,
                    loopCnt
                    )
                print(deviceMsg)

                #determine the desired topic to publish on
                if dataPublishSendMode == "faster":
                    #faster method is for use with Greengrass to Kinesis
                    publishTopic = "dt/windfarm/weather/" + cfgThingName + "/faster"
                else:
                    publishTopic = "dt/windfarm/weather/" + cfgThingName

                #make sure at least a final message is sent when the wind speed goes to 0
                lastReportedSpeed = windSpeedMPH

                #Only publish data if the annemometer is spinning
                if windSpeedMPH > 0 or lastReportedSpeed != 0:
                    #publish with QOS 0
                    response = awsIoTMQTTClient.publish(publishTopic, json.dumps(devicePayload), 0)
                    ledFlash()
                else:
                    #publish with QOS 0
                    response = awsIoTMQTTClient.publish(publishTopic, json.dumps(devicePayload), 0)
                    ledFlash()
                    print("Wind speed is 0... sleeping for 60 seconds")
                    #sleep a few times with a speed check to see if the annemometer is spinning again

                    for i in range(1,12):
                        calculate_wind_speed()
                        lastReportedSpeed = windSpeedMPH
                        if windSpeedMPH > 0:
                            sleep(5)  #need to do this to allow weatherSpeedElapse time to grow for a realistic calculation on the next call
                            break
                        sleep(5)  #slow down the publishing rate

            except:
                logger.warning("exception while publishing")
                raise

    except (KeyboardInterrupt, SystemExit): #when you press ctrl+c
        print("Disconnecting AWS IoT")
        ledOff()
        if not awsShadowClient == None:
            awsShadowClient.disconnect()
        sleep(2)
        print("Done.\nExiting.")

if __name__ == "__main__":

    # Usage
    usageInfo = """Usage:

    python weather.py -config <config json file>
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

    logging.basicConfig(filename='weather.log',level=logging.INFO,format='%(asctime)s %(message)s')
    logger.info("Welcome to the AWS Wind Energy Weather Station Device Reporter.")
    main()

