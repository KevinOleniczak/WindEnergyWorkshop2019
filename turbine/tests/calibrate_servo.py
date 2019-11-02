import RPi.GPIO as GPIO
import time
import sys

brakePin = 10

GPIO.setmode(GPIO.BOARD)
GPIO.setup(brakePin, GPIO.OUT)
p = GPIO.PWM(brakePin, 50)
p.start(8)

print("Wind Turbine Brake Calibration Program")
print("This program will help you understand the range of brake pressure values and their effect on the turbine.")
print("Test a few values to get an idea of what light, moderate and heavy braking is.")
print("You do this by entering a decimal value between 6 and 8.")
print("Light braking should be around 7")
print("Moderate braking should be around 6.8")
print("Heavy braking should be around 6.5")
print("Try entering a value followed by the ENTER key to test it...")

try:
    while True:
        data = sys.stdin.readline()
        print("Sending: " + str(float(data)) + "  Ctrl-C to exit.")
        p.ChangeDutyCycle(float(data))
        time.sleep(0.2)
        p.ChangeDutyCycle(0)

except KeyboardInterrupt:
        p.stop()
        GPIO.cleanup()

