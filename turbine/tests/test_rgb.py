import sys
import RPi.GPIO as GPIO
from time import sleep

successCnt = 0

#GPIO pins
redPin = 29
greenPin = 31
bluePin = 33

GPIO.setmode(GPIO.BOARD)
GPIO.setup(redPin, GPIO.OUT)
GPIO.setup(greenPin, GPIO.OUT)
GPIO.setup(bluePin, GPIO.OUT)

GPIO.output(redPin, GPIO.LOW)
GPIO.output(greenPin, GPIO.LOW)
GPIO.output(bluePin, GPIO.LOW)

if len(sys.argv) - 1 > 0:
    runMode = sys.argv[1]
else:
    runMode = 'interactive'

try:
    print("Ensure the turbine is connected. Let's test all three primary colors...")

    #RED
    GPIO.output(redPin, GPIO.HIGH)
    if runMode == 'interactive':
        answer = raw_input("Do you see RED on the base LED and the LED on top of the turbine? [Y/n]:  ").lower()
        if answer in ['Y','y']:
            print("RED is working")
            GPIO.output(redPin, GPIO.LOW)
            successCnt += 1
        else:
            print("RED is NOT working")
    else:
        sleep(2)
        GPIO.output(redPin, GPIO.LOW)
        successCnt += 1

    #GREEN
    GPIO.output(greenPin, GPIO.HIGH)
    if runMode == 'interactive':
        answer = raw_input("Do you see GREEN on the base LED and the LED on top of the turbine? [Y/n]:  ").lower()
        if answer == 'y':
            print("GREEN is working")
            GPIO.output(greenPin, GPIO.LOW)
            successCnt += 1
        else:
            print("GREEN is NOT working")
    else:
        sleep(2)
        GPIO.output(greenPin, GPIO.LOW)
        successCnt += 1

    #BLUE
    GPIO.output(bluePin, GPIO.HIGH)
    if runMode == 'interactive':
        answer = raw_input("Do you see BLUE on the base LED and the LED on top of the turbine? Y/y or N/n:  ").lower()
        if answer == 'y':
            print("BLUE is working")
            GPIO.output(bluePin, GPIO.LOW)
            successCnt += 1
        else:
            print("BLUE is NOT working")
    else:
        sleep(2)
        GPIO.output(bluePin, GPIO.LOW)
        successCnt += 1

except:
    pass

GPIO.cleanup()

if successCnt == 3:
    print("The RGB LED Light is working")
    sys.exit(0)
else:
    print("The RGB LED Light is NOT working. Check connections.")
    sys.exit(1)

