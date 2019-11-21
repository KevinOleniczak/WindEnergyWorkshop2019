import RPi.GPIO as GPIO
import time
import sys

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
successCnt = 0
initTurbineGPIO()
initTurbineRPMSensor()
initTurbineBrake()

try:
    print("Ensure the turbine is spinning relatively fast. The turbine brake will be applied to determine the optimal min and max position. Do not change the wind source while this takes place.")

    pwm_start = 10
    brakeServo.start(pwm_start)
    calculateTurbineSpeed()
    time.sleep(5)
    calculateTurbineSpeed()
    print("Turbine speed: " + str(turbineRPM))

    if turbineRPM == 0:
        print("Turbine is not spinning. Can't do the auto calibrate without wind.")
    else:
        for i in range(1, pwm_start*10):
            pwm = pwm_start - float(i)/10
            print("Trying {0:.1f}".format(pwm))
            brakeServo.ChangeDutyCycle(pwm)
            time.sleep(2)
            calculateTurbineSpeed()
            print(turbineRPM)
            if turbineRPM == 0:
                print("found a stop point at " + str(pwm))
                successCnt = 1
                break

    #Off
    brakeServo.ChangeDutyCycle(pwm_start)
    time.sleep(0.5)
    brakeServo.ChangeDutyCycle(0)

except:
    pass

GPIO.cleanup()

if successCnt == 1:
    print("Success")
    sys.exit(0)
else:
    print("Failed to find a good stop point")
    sys.exit(1)

