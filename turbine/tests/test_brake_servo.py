import RPi.GPIO as GPIO
import time
import sys

brakePin = 10
successCnt = 0

if len(sys.argv) - 1 > 0:
    runMode = sys.argv[1]
else:
    runMode = 'interactive'

try:
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(brakePin, GPIO.OUT)
    p = GPIO.PWM(brakePin, 50)

    if runMode == 'interactive':
        print("Ensure the turbine is connected. Keep and eye on the brake arm as you test a few positions...")
        raw_input("Press ENTER to begin...")

    p.start(8.3)
    time.sleep(1)

    #Off
    p.start(8.0)
    time.sleep(0.5)
    p.start(0)

    if runMode == 'interactive':
        answer = raw_input("The brake arm has been moved to a safe OFF position away from the turbine hub. Look good? [Y/n]:  ").lower()
        if answer == 'y':
            print("Servo Brake is working in the OFF position.")
            successCnt += 1
        else:
            print("Servo Brake is NOT working in the OFF position.")
    else:
        successCnt += 1

    #ON
    p.start(7.0)
    time.sleep(0.5)
    p.start(0)
    if runMode == 'interactive':
        answer = raw_input("The brake arm has been moved to apply pressure in the ON position against the turbine hub. Did you see the brake arm move? [Y/n]:  ").lower()
        if answer == 'y':
            print("Servo Brake is working in the ON position.")
            successCnt += 1
        else:
            print("Servo Brake is NOT working in the ON position.")
    else:
        successCnt += 1

    #Off
    p.start(8.0)
    time.sleep(0.5)
    p.start(0)

except:
    pass

GPIO.cleanup()

if successCnt == 2:
    print("The Brake Servo is working")
    sys.exit(0)
else:
    print("The Brake Servo is NOT working. Check connections.")
    sys.exit(1)

