import RPi.GPIO as GPIO
import time
import sys

GPIO.setmode(GPIO.BOARD)

GPIO.setup(10, GPIO.OUT)
print("Enter the servo duty cycle value between 6 and 8 or 0 to stop")
p = GPIO.PWM(10, 50)
p.start(7)

try:
        while True:
                data = sys.stdin.readline()
                print("Sending: " + str(float(data)))
                p.ChangeDutyCycle(float(data))  
                time.sleep(0.2)
                p.ChangeDutyCycle(0)

except KeyboardInterrupt:
        p.stop()
        GPIO.cleanup()
