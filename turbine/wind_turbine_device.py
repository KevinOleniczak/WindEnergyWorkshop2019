from __future__ import division
import logging
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
import Adafruit_LSM303
import rgbled as RGBLED
import math

myUUID = str(uuid.getnode())
print(myUUID)

logger = logging.getLogger(__name__)
GPIO.setmode(GPIO.BCM)

LED_LAST_STATE = ""

# Create a LSM303 instance. Accelerometer
accelerometer = Adafruit_LSM303.LSM303()
# Alternatively you can specify the I2C bus with a bus parameter:
#lsm303 = Adafruit_LSM303.LSM303(busum=2)
accel_x = 0
accel_y = 0
accel_z = 0

#calibration offsets
accel_x_cal = -3
accel_y_cal = 38
accel_z_cal = 1052

#run with...
#python wind_turbine_device.py -e windfarm.awsworkshops.com -r ggc_rootCA.pem -c 54xxxxx9.cert.pem -k 54xxxxx9.private.key -n WindTurbine1
#python wind_turbine_device.py -e 192.168.1.132 -r ggc_rootCA.pem -c 54xxxxx9.cert.pem -k 54xxxxx9.private.key -n WindTurbine1

#AWS IoT Stuff
myAWSIoTMQTTClient = None
myShadowClient = None
myDeviceShadow = None
useWebsocket = False
myClientID = ""
host = ""
rootCAPath = ""
certificatePath = ""
privateKeyPath = ""
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
turbine_servo_brake_pin = 15 #pin 10
GPIO.setwarnings(False)
GPIO.setup(turbine_servo_brake_pin, GPIO.OUT)
brakePWM = GPIO.PWM(turbine_servo_brake_pin, 50)
brake_state = "TBD"
brakePWM.start(3)

GPIO.setup(21, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)

# ADC MCP3008
# Software SPI configuration:
#import Adafruit_GPIO.SPI as SPI
import Adafruit_MCP3008
CLK  = 11 #pin 23
MISO = 9 #pin 21
MOSI = 10 #pin 19
CS   = 8 #pin 24
#mcp = Adafruit_MCP3008.MCP3008(23, 24, 21, 19)
mcp = Adafruit_MCP3008.MCP3008(clk=CLK, cs=CS, miso=MISO, mosi=MOSI)

def aws_connect():
    # Init AWSIoTMQTTClient
    global myAWSIoTMQTTClient
    global myShadowClient
    global myDeviceShadow

    if useWebsocket:
        myAWSIoTMQTTClient = AWSIoTMQTTClient(myClientID, useWebsocket=True)
        myAWSIoTMQTTClient.configureEndpoint(host, 443)
        myAWSIoTMQTTClient.configureCredentials(rootCAPath)

        myShadowClient = AWSIoTMQTTShadowClient(myClientID)

    else:
        #myAWSIoTMQTTClient = AWSIoTMQTTClient(myClientID)
        #myAWSIoTMQTTClient.configureEndpoint(host, 8883)
        #myAWSIoTMQTTClient.configureCredentials(rootCAPath, privateKeyPath, certificatePath)

        myShadowClient = AWSIoTMQTTShadowClient(myClientID)
        myShadowClient.configureEndpoint(host, 8883)
        myShadowClient.configureCredentials(rootCAPath, privateKeyPath, certificatePath)
        myAWSIoTMQTTClient = myShadowClient.getMQTTConnection()

    lwt_message = {
            "state": {
                "reported": {
                    "connected":"false"
                }
            }
        }
    myShadowClient.configureLastWill("windfarm-turbines/lwt", json.dumps(lwt_message).encode("utf-8"), 0)

    # AWSIoTMQTTClient connection configuration
    myAWSIoTMQTTClient.configureAutoReconnectBackoffTime(1, 32, 20)
    myAWSIoTMQTTClient.configureOfflinePublishQueueing(-1)  # Infinite offline Publish queueing
    myAWSIoTMQTTClient.configureDrainingFrequency(2)  # Draining: 2 Hz
    myAWSIoTMQTTClient.configureConnectDisconnectTimeout(5)  # 10 sec
    myAWSIoTMQTTClient.configureMQTTOperationTimeout(5)  # 5 sec
    myAWSIoTMQTTClient.connect()
    #myAWSIoTMQTTClient.subscribe("$aws/...", 1, customCallbackDeltaTest)
    print ("AWS IoT Connected")

    # Shadow config
    myShadowClient.configureAutoReconnectBackoffTime(1, 32, 20)
    myShadowClient.configureConnectDisconnectTimeout(10)  # 10 sec
    myShadowClient.configureMQTTOperationTimeout(10)  # 5 sec
    myShadowClient.connect()
    myDeviceShadow = myShadowClient.createShadowHandlerWithName(myClientID, True)
    myDeviceShadow.shadowRegisterDeltaCallback(myShadowCallbackDelta)

    conn_message = {
            "state": {
                "reported": {
                    "connected":"true"
                }
            }
        }
    myDeviceShadow.shadowUpdate(json.dumps(conn_message).encode("utf-8"), myShadowCallback, 5)
    print ("AWS IoT Shadow Connected")

    myAWSIoTMQTTClient.subscribe("$aws/things/" + myClientID + "/jobs/notify-next", 1, customCallbackJobs)
    print ("AWS IoT Jobs Connected")

def init_turbine_GPIO():                    # initialize GPIO
    global turbine_rotation_sensor_pin
    GPIO.setwarnings(False)
    GPIO.setup(turbine_rotation_sensor_pin, GPIO.IN, GPIO.PUD_UP)
    print ("Turbine is connected")

def init_turbine_brake():
    #global myDeviceShadow
    #myDeviceShadow.shadowDelete(myShadowCallback, 5)
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
    global pulse,elapse,rpm,last_pulse
    if elapse !=0:   # to avoid DivisionByZero error
        rpm = 1/elapse * 60
    if pulse == last_pulse:
        rpm = 0
    else:
        last_pulse = pulse
    return rpm

def calibrate_turbine_vibe():
    global accel_x_cal, accel_y_cal, accel_z_cal
    # Read the X, Y, Z axis acceleration values and print them.
    accel, mag = accelerometer.read()
    accel_x, accel_y, accel_z = accel
    print('Vibration calibration: '+ str(accel_x) + ' ' + str(accel_y) + ' ' + str(accel_z))
    # Grab the X, Y, Z components from the reading and print them out.
    #accel_x_cal, accel_y_cal, accel_z_cal = accel
    return 1

def calculate_turbine_vibe():
    global accel_x, accel_y, accel_z, accel_x_cal, accel_y_cal, accel_z_cal
    # Read the X, Y, Z axis acceleration values and print them.
    accel, mag = accelerometer.read()
    # Grab the X, Y, Z components from the reading and print them out.
    accel_x, accel_y, accel_z = accel
    #apply calibration offsets
    accel_x -= accel_x_cal
    accel_y -= accel_y_cal
    accel_z -= accel_z_cal
    #mag_x, mag_z, mag_y = mag
    return 1

def get_turbine_voltage():
    global mcp
    # The read_adc function will get the value of the specified channel (0-7).
    refVal = mcp.read_adc(0)
    calcVolt = round(((3300/1023) * refVal) / 1000, 2)
    return calcVolt

def turbine_brake_action(action):
    global brakePWM,brake_state,myDeviceShadow,LED_LAST_STATE
    if action == brake_state:
        #thats already the known action state
        #print "Already there"
        return "Already there"

    if action == "ON":
        print "Applying turbine brake!"
        RGBLED.whiteOff()
        RGBLED.redOn()
        LED_LAST_STATE = "Red"
        brakePWM.ChangeDutyCycle(11) # turn towards 180 degree
    elif action == "OFF":
        print "Resetting turbine brake."
        RGBLED.whiteOff()
        RGBLED.greenOn()
        LED_LAST_STATE = "Green"
        brakePWM.ChangeDutyCycle(3)  # turn towards 0 degree
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
    global myClientID,myDataSendMode,myDataInterval
    print responseStatus
    print "delta shadow callback >> " + payload

    if responseStatus == "delta/" + myClientID:
        payloadDict = json.loads(payload)
        print "shadow delta >> " + payload
        try:
            if "brake_status" in payloadDict["state"]:
                 turbine_brake_action(payloadDict["state"]["brake_status"])
            if "data_path" in payloadDict["state"]:
                 myDataSendMode = process_data_path_changes("data_path", payloadDict["state"]["data_path"])
            if "data_fast_interval" in payloadDict["state"]:
                 myDataInterval = int(process_data_path_changes("data_fast_interval", payloadDict["state"]["data_fast_interval"]))
        except:
            print "delta cb error"

def myShadowCallback(payload, responseStatus, token):
    # payload is a JSON string ready to be parsed using json.loads(...)
    # in both Py2.x and Py3.x
    #print responseStatus
    #print "shadow callback >> " + payload

    if responseStatus == "timeout":
        print("Update request " + token + " time out!")

    if responseStatus == "accepted":
        print "shadow accepted"
        #payloadDict = json.loads(payload)
        #print("Update request with token: " + token + " accepted!")
        #print("property: " + str(payloadDict["state"]["desired"]["property"]))
        #print("~~~~~~~~~~~~~~~~~~~~~~~\n\n")

    if responseStatus == "rejected":
        print("Update request " + token + " rejected!")

def customCallbackJobs(payload, responseStatus, token):
    global myClientID,myDataSendMode,myDataInterval
    print responseStatus
    print "Next job callback >> " + payload

    if responseStatus == "delta/" + myClientID:
        payloadDict = json.loads(payload)
        print "shadow delta >> " + payload
        try:
            if "brake_status" in payloadDict["state"]:
                 turbine_brake_action(payloadDict["state"]["brake_status"])
            if "data_path" in payloadDict["state"]:
                 myDataSendMode = process_data_path_changes("data_path", payloadDict["state"]["data_path"])
            if "data_fast_interval" in payloadDict["state"]:
                 myDataInterval = process_data_path_changes("data_fast_interval", payloadDict["state"]["data_fast_interval"])
        except:
            print "delta cb error"

def evaluate_turbine_safety():
    global rpm
    if rpm > 20:
        request_turbine_brake_action("ON")
    #else:
    #    turbine_brake_action("OFF")

def main():
    global myClientID,rpm,pulse,myAWSIoTMQTTClient,myShadowClient,myDeviceShadow,myDataSendMode,myDataInterval,LED_LAST_STATE
    RGBLED.whiteOff()
    RGBLED.blueOn()
    my_loop_cnt = 0
    data_sample_cnt = 0
    last_reported_speed = -1
    myDataIntervalCnt = 19
    myVibeDataList = []

    try:
        aws_connect()
        init_turbine_GPIO()
        init_turbine_interrupt()
        sleep(5)
        init_turbine_brake()
        calibrate_turbine_vibe()
        print "Turbine Monitoring Starting..."
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

            if myDataSendMode == "faster":
                myDataIntervalCnt = myDataInterval
            else:
                myDataIntervalCnt = 50

            #sampling of vibration between published messages
            for data_sample_cnt in range(myDataIntervalCnt, 0, -1):
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
                'deviceID' : myUUID,
                'thing_name' : myClientID,
                'timestamp' : str(datetime.datetime.utcnow().isoformat()),
                'loop_cnt' : str(my_loop_cnt),
                'location' : "ORD10-14",
                'lat' : 42.888,
                'lng' : -88.123,
                'turbine_temp' : 75, #temp fix
                'turbine_speed' : rpm,
                'turbine_rev_cnt' : pulse,
                'turbine_voltage' : str(get_turbine_voltage()),
                'turbine_vibe_x' : peak_vibe_x,
                'turbine_vibe_y' : peak_vibe_y,
                'turbine_vibe_z' : peak_vibe_z,
                'turbine_vibe_peak': peak_vibe,
                'turbine_vibe_avg': avg_vibe,
                'turbine_sample_cnt': str(len(myVibeDataList))
                }

            try:
                print('rpm:{0:.0f}-RPM pulse:{1} peak-accel:{2} avg-vibe:{6} brake:{3} cnt:{4} voltage:{5}'.format(rpm,pulse,peak_vibe,brake_state,str(my_loop_cnt),str(get_turbine_voltage()),str(avg_vibe)) )
                if rpm > 0 or last_reported_speed != 0:
                     if myDataSendMode == "faster":
                         myTopic = "windturbine-data-faster"
                     elif myDataSendMode == "cheaper":
                         myTopic = "windturbine-data-cheaper"
                     else:
                         myTopic = "windturbine-data"

                     last_reported_speed = rpm
                     RGBLED.whiteOff()
                     myAWSIoTMQTTClient.publish(myTopic, json.dumps(myReport), 0)
                     sleep(0.08)
                     if LED_LAST_STATE == "Red":
                         RGBLED.redOn()
                     else:
                         RGBLED.greenOn()
            except:
                logger.warning("exception while publishing")
                raise

    except (KeyboardInterrupt, SystemExit): #when you press ctrl+c
        print("Disconnecting AWS IoT")
        RGBLED.whiteOff()
        conn_message = {
            "state": {
                "reported": {
                    "connected":"false"
                }
            }
        }
        myDeviceShadow.shadowUpdate(json.dumps(conn_message).encode("utf-8"), myShadowCallback, 5)
        sleep(1)
        myShadowClient.disconnect()
        sleep(2)
        print ("Done.\nExiting.")

if __name__ == "__main__":

    # Usage
    usageInfo = """Usage:

    Use certificate based mutual authentication:
    python someprogram.py -e <endpoint> -r <rootCAFilePath> -c <certFilePath> -k <privateKeyFilePath> -n <your-thingname>

    Use MQTT over WebSocket:
    python someprogram.py -e <endpoint> -r <rootCAFilePath> -w

    Type "python someprogram.py -h" for available options.
    """
    # Help info
    helpInfo = """-e, --endpoint
            Your AWS IoT custom endpoint
    -r, --rootCA
            Root CA file path
    -c, --cert
            Certificate file path
    -k, --key
            Private key file path
    -n, --thingName
            Unique thing name as created in IoT Core
    -w, --websocket
            Use MQTT over WebSocket
    -h, --help
            Help information

    """

    # Read in command-line parameters
    try:
        opts, args = getopt.getopt(sys.argv[1:], "hwe:k:c:r:n:", ["help", "endpoint=", "key=", "cert=", "rootCA=", "thingName=", "websocket"])
        if len(opts) == 0:
            raise getopt.GetoptError("No input parameters!")
        for opt, arg in opts:
            if opt in ("-h", "--help"):
                print(helpInfo)
                exit(0)
            if opt in ("-e", "--endpoint"):
                host = arg
            if opt in ("-r", "--rootCA"):
                rootCAPath = arg
            if opt in ("-c", "--cert"):
                certificatePath = arg
            if opt in ("-k", "--key"):
                privateKeyPath = arg
            if opt in ("-n", "--thingName"):
                myClientID = arg
                print(myClientID)
            if opt in ("-w", "--websocket"):
                useWebsocket = True
    except getopt.GetoptError:
            print(usageInfo)
            exit(1)

    # Missing configuration notification
    missingConfiguration = False
    if not host:
        print("Missing '-e' or '--endpoint'")
        missingConfiguration = True
    if not rootCAPath:
        print("Missing '-r' or '--rootCA'")
        missingConfiguration = True
    if not myClientID:
        print("Missing '-n' or '--thingName'")
        missingConfiguration = True
    if not useWebsocket:
        if not certificatePath:
            print("Missing '-c' or '--cert'")
            missingConfiguration = True
        if not privateKeyPath:
            print("Missing '-k' or '--key'")
            missingConfiguration = True
    if missingConfiguration:
        exit(2)

    logging.basicConfig(filename='wind_turbine_device.log',level=logging.INFO,format='%(asctime)s %(message)s')
    logger.info("Welcome to the AWS Windfarm Turbine Device Reporter.")
    main()
