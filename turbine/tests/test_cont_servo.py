import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BOARD)

GPIO.setup(10, GPIO.OUT)

p = GPIO.PWM(10, 50)

p.start(7.5)

try:
        while True:
                time.sleep(5) 
                p.ChangeDutyCycle(7)  # turn brake on

                time.sleep(1)
                p.ChangeDutyCycle(7.5) # turn brake off

                time.sleep(2)
                p.ChangeDutyCycle(6)

                time.sleep(1)
                p.ChangeDutyCycle(7.5) # turn brake off
                
                time.sleep(2)
                p.ChangeDutyCycle(5)

                time.sleep(1) 
                p.ChangeDutyCycle(7.5) # turn brake off 
except KeyboardInterrupt:
        p.stop()
        GPIO.cleanup()