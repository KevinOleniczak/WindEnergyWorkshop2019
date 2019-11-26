import RPi.GPIO as GPIO
import time
import sys
import getopt
import os
import json

# Turbine rotation speed sensor
turbine_rotation_sensor_pin = 26  # pin 37
turbineRPM = 0
turbineRpmElapse = 0
turbineRotationCnt = 0
lastTurbineRotationCnt = 0
start_timer = time.time()

# Servo control for turbine brake
turbine_servo_brake_pin = 15  # pin 10
brakeServo = None

def initTurbineGPIO():
    global GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    print("Turbine GPIO initialized")

def initTurbineRPMSensor():
    global GPIO
    GPIO.setup(turbine_rotation_sensor_pin, GPIO.IN, GPIO.PUD_UP)
    GPIO.add_event_detect(turbine_rotation_sensor_pin, GPIO.FALLING, callback=calculateTurbineElapse, bouncetime=20)
    print("Turbine rotation sensor is connected")

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

def initTurbineBrake():
    global brakeServo, GPIO
    GPIO.setup(turbine_servo_brake_pin, GPIO.OUT)
    brakeServo = GPIO.PWM(turbine_servo_brake_pin, 50)
    brakeServo.start(0)
    print("Turbine brake connected")

brakePin = 10
pwm_off = 10
stopped_at_pwm = 10
configFile = ''

def main():
    global stopped_at_pwm, pwm_off
    successCnt = 0
    initTurbineGPIO()
    initTurbineRPMSensor()
    initTurbineBrake()

    try:
        print("Ensure the turbine is spinning relatively fast. The turbine brake will be applied to determine the optimal min and max position. Do not change the wind source while this takes place.")

        pwm_start = 10
        pwm = pwm_start
        brakeServo.start(pwm_start)
        calculateTurbineSpeed()
        time.sleep(5)
        calculateTurbineSpeed()
        print("Turbine speed: " + str(turbineRPM))
        starting_turbine_speed = turbineRPM

        if turbineRPM == 0:
            print("Turbine is not spinning. Can't do the auto calibrate without wind.")
        else:
            for i in range(1, pwm_start*10):
                if turbineRPM < (starting_turbine_speed * .8):
                    pwm -= 0.1
                else:
                    pwm -= 0.2
                print("Trying {0:.1f}".format(pwm))
                brakeServo.ChangeDutyCycle(pwm)
                time.sleep(2)
                calculateTurbineSpeed()
                print("Turbine speed: " + str(turbineRPM))
                if turbineRPM == 0:
                    print("Found an initial stopping point at " + str(pwm))
                    stopped_at_pwm = pwm - 0.2
                    print("Applying a little extra stopping pressure")
                    break

        #Off
        print("Letting the turbine speed up again...")
        brakeServo.ChangeDutyCycle(pwm_start)
        time.sleep(7)
        calculateTurbineSpeed()
        print("Turbine speed: " + str(turbineRPM))

        #test if the stop point can provide hard braking
        print("Applying full braking force to test hard stopping")
        brakeServo.ChangeDutyCycle(stopped_at_pwm)
        time.sleep(5)
        calculateTurbineSpeed()
        time.sleep(5)
        calculateTurbineSpeed()
        print("Turbine speed: " + str(turbineRPM))

        if turbineRPM == 0:
            print("That value looks good for quick stopping")
            successCnt = 1
        else:
            for i in range (1, 5):
                print("More braking force needed... adding force")
                stopped_at_pwm -= 0.1
                time.sleep(5)
                print("Trying {0:.1f}".format(stopped_at_pwm))
                brakeServo.ChangeDutyCycle(stopped_at_pwm)
                time.sleep(5)
                calculateTurbineSpeed()
                print("Turbine speed: " + str(turbineRPM))
                if turbineRPM == 0:
                    print("That value looks good for quick stopping")
                    successCnt = 1
                    break
                else:
                    print("Unable to completely stop the turbine")

        #off
        pwm_off = stopped_at_pwm + 1
        brakeServo.ChangeDutyCycle(pwm_off)
        time.sleep(1)
        brakeServo.ChangeDutyCycle(0)

    except Exception as e:
        s = str(e)
        print(s)

    GPIO.cleanup()

    if successCnt == 1:
        print("Success")
        print("Brake on  position determined to be: {0:.1f}".format(stopped_at_pwm))
        print("Brake off position determined to be: {0:.1f}".format(pwm_off))

        with open(configFile, 'r+') as f:
            myConfig = json.load(f)
            myConfig['settings']['brakeServo']['onPosition'] = round(stopped_at_pwm,2)
            myConfig['settings']['brakeServo']['offPosition'] = round(pwm_off,2)
            f.seek(0)
            f.truncate()
            json.dump(myConfig, f)

        sys.exit(0)
    else:
        print("Failed to find a good stop point")
        sys.exit(1)

if __name__ == "__main__":
    #global configFile

    # Usage
    usageInfo = """Usage:

    python auto_cal_brake.py --config <config json file>
    """

    # Read in command-line parameters
    try:
        opts, args = getopt.getopt(sys.argv[1:], "", ["config="])
        if len(opts) == 0:
            raise getopt.GetoptError("No input parameters!")
        for opt, arg in opts:
            if opt in ("--config"):
                configFile = arg
                if not os.path.isfile(configFile):
                    print("Config file is invalid")
                    exit(1)

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

    main()

