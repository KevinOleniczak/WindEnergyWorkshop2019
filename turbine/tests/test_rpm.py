#!/usr/bin/python3
import RPi.GPIO as GPIO
import time
import sys

PulseCount=0
HallEffect_PIN = 37

GPIO.setmode(GPIO.BOARD)
GPIO.setup(HallEffect_PIN, GPIO.IN)

def MOTION(HallEffect_PIN):
    global PulseCount
    PulseCount = PulseCount + 1

GPIO.add_event_detect(HallEffect_PIN, GPIO.RISING, callback=MOTION, bouncetime=10)

try:
    print("Ensure the turbine is spinning. Counting the number of rotations for 5 seconds...")
    time.sleep(5)
    print("Rotations ", PulseCount)
    GPIO.cleanup()
except:
    pass

if PulseCount > 0:
    print("RPM Sensor is working")
    sys.exit(0)
else:
    print("RPM Sensor is NOT working")
    sys.exit(1)

