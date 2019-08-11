#!/usr/bin/python3

import RPi.GPIO as GPIO
import datetime
import time
#import MySQLdb
#from WindRainDatabase import *

# This is some code totest the response of the wind speed sensor


WindCount=0

Wind_PIN = 37 

GPIO.setmode(GPIO.BOARD)
GPIO.setup(Wind_PIN, GPIO.IN)

#############################################################################################

def main():
    global WindCount
        
    try:
        GPIO.add_event_detect(Wind_PIN, GPIO.RISING, callback=MOTION, bouncetime=10)
        time.sleep(10)
 #       InsertToDB(1, WindCount)
        print("Pulses",WindCount)
        GPIO.cleanup()

    except KeyboardInterrupt:
        print("Quit")
        GPIO.cleanup()

#############################################################################################
#############################################################################################

def MOTION(Rain_PIN):
    global WindCount
    WindCount = WindCount + 1

if __name__ == "__main__":
   main() 

