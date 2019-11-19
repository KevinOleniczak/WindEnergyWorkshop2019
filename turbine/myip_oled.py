# Copyright (c) 2017 Adafruit Industries
# Author: Tony DiCola & James DeVito
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
from time import sleep
import sys
import Adafruit_GPIO.SPI as SPI
import Adafruit_SSD1306
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
import subprocess
import RPi.GPIO as GPIO
import time

# Raspberry Pi pin configuration:
RST = None     # on the PiOLED this pin isnt used
lastMessage = ""

# 128x64 display with hardware I2C:
disp = Adafruit_SSD1306.SSD1306_128_64(rst=RST)
oledConnected = False
PulseCount=0
HallEffect_PIN = 26 #pin 37

# RGB LED GPIO pins
ledRedPin = 5
ledGreenPin = 6
ledBluePin = 13

def initGPIO():
    global GPIO
    GPIO.setmode(GPIO.BCM)

def resetGPIO():
    global GPIO
    #need to reinitialize gpio pins since there was contention for them in the full diagnostic tests
    GPIO.cleanup()
    initGPIO()
    initHall()
    initTurbineLED()
    initButtons()

def ledOn(color=None):
    global ledLastState, GPIO
    # reset by turning off all 3 colors
    GPIO.output(ledRedPin, 0)
    GPIO.output(ledGreenPin, 0)
    GPIO.output(ledBluePin, 0)

    if color == None:
        color = ledLastState
    else:
        ledLastState = color

    if color == "red":
        GPIO.output(ledRedPin, 1)
    elif color == "green":
        GPIO.output(ledGreenPin, 1)
    elif color == "blue":
        GPIO.output(ledBluePin, 1)
    elif color == "magenta":
        GPIO.output(ledRedPin, 1)
        GPIO.output(ledBluePin, 1)
    elif color == "white":
        GPIO.output(ledRedPin, 1)
        GPIO.output(ledGreenPin, 1)
        GPIO.output(ledBluePin, 1)
    else:
        pass

def ledOff(remember=None):
    global ledLastState, GPIO
    # reset by turning off all 3 colors
    GPIO.output(ledRedPin, 0)
    GPIO.output(ledGreenPin, 0)
    GPIO.output(ledBluePin, 0)

    if remember == None:
        ledLastState = ''

def initButtons():
    global GPIO
    GPIO.setup(21, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(20, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(16, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

def initTurbineLED():
    global GPIO
    GPIO.setup(ledRedPin, GPIO.OUT)
    GPIO.setup(ledGreenPin, GPIO.OUT)
    GPIO.setup(ledBluePin, GPIO.OUT)

def MOTION(HallEffect_PIN):
    global PulseCount
    PulseCount = PulseCount + 1

def initHall():
    global GPIO
    GPIO.setup(HallEffect_PIN, GPIO.IN)
    GPIO.add_event_detect(HallEffect_PIN, GPIO.RISING, callback=MOTION, bouncetime=10)

def runFullDiagnostic():
    global GPIO
    resultMsg = "Full Test: Starting..."
    updateOLED(resultMsg)
    return_code = subprocess.call('/home/pi/WindEnergyWorkshop2019/turbine/tests/test_hardware.sh auto', shell=True)
    resetGPIO()
    return return_code

def checkRPM():
    global PulseCount, GPIO
    try:
        resultMsg = "RPM Test: Starting..."
        updateOLED(resultMsg)
        PulseCount = 0
        print("Ensure the turbine is spinning. Counting the number of rotations for 5 seconds...")
        time.sleep(5)
        print("Rotations ", PulseCount)
        if PulseCount > 0:
            resultMsg = "RPM Test: Pass"
            ledOn("green")
        else:
            resultMsg = "RPM Test: Fail"
            ledOn("red")
        updateOLED(resultMsg)
        sleep(5)
    except:
        pass

def checkButtons():
    buttonState = GPIO.input(21)  # Switch1 (S1)
    if buttonState == True:
        buttonState = False

    buttonState = GPIO.input(20)  # Switch2 (S2)
    if buttonState == True:
        updateOLED(processDiagResult(runFullDiagnostic()))
        buttonState = False

    buttonState = GPIO.input(16)  # Switch3 (S3)
    if buttonState == True:
        checkRPM()  #this test is here for assembly testing purposes
        buttonState = False

    ##debounce
    sleep(0.1)

def initOLED():
    global disp, oledConnected
    try:
        # Initialize library.
        disp.begin()

        # Clear display.
        disp.clear()
        disp.display()
        oledConnected = True

    except:
        oledConnected = False

def processDiagResult(aResult):
    print(aResult)
    if int(aResult) == 0:
        msg = 'Hardware: Pass'
        ledOn("green")
    else:
        msg = 'Hardware: Fail ' + str(aResult)
        ledOn("red")
    return msg

def updateOLED(aMessage = None):
    global lastMessage
    # Create blank image for drawing.
    # Make sure to create image with mode '1' for 1-bit color.
    width = disp.width
    height = disp.height
    image = Image.new('1', (width, height))

    # Get drawing object to draw on image.
    draw = ImageDraw.Draw(image)

    # Draw a black filled box to clear the image.
    draw.rectangle((0,0,width,height), outline=0, fill=0)

    # Draw some shapes.
    # First define some constants to allow easy resizing of shapes.
    padding = -2
    top = padding
    bottom = height-padding
    # Move left to right keeping track of the current x position for drawing shapes.
    x = 0

    # Load default font.
    font = ImageFont.load_default()

    # Draw a black filled box to clear the image.
    draw.rectangle((0,0,width,height), outline=0, fill=0)

    # Shell scripts for system monitoring from here : https://unix.stackexchange.com/questions/119126/command-to-display-memory-usage-disk-usage-and-cpu-load
    cmd = "hostname -I | cut -d\' \' -f1"
    IP = subprocess.check_output(cmd, shell = True )
    cmd = "top -bn1 | grep load | awk '{printf \"CPU Load: %.2f\", $(NF-2)}'"
    CPU = subprocess.check_output(cmd, shell = True )
    cmd = "free -m | awk 'NR==2{printf \"Mem: %s/%sMB %.2f%%\", $3,$2,$3*100/$2 }'"
    MemUsage = subprocess.check_output(cmd, shell = True )
    cmd = "df -h | awk '$NF==\"/\"{printf \"Disk: %d/%dGB %s\", $3,$2,$5}'"
    Disk = subprocess.check_output(cmd, shell = True )

    # Write two lines of text.
    draw.text((x, top),       "IP: " + str(IP),  font=font, fill=255)
    draw.text((x, top+8),     str(CPU), font=font, fill=255)
    draw.text((x, top+16),    str(MemUsage),  font=font, fill=255)
    draw.text((x, top+25),    str(Disk),  font=font, fill=255)

    #Diagnostic test result
    if aMessage == None:
        aMessage = lastMessage
    else:
        lastMessage = aMessage
    draw.text((x, top+40),    aMessage,  font=font, fill=255)

    # Display image.
    disp.image(image)
    disp.display()

def main():
    global oledConnected, lastMessage, GPIO
    resetGPIO()

    if len(sys.argv) - 1 > 0:
        lastMessage = processDiagResult(sys.argv[1])
    try:
        while True:
            if not oledConnected:
                initOLED()

            if oledConnected:
                try:
                    checkButtons()
                    updateOLED()
                    sleep(5)

                except (KeyboardInterrupt, SystemExit):  # when you press ctrl+c
                    exit()
                except Exception as e:
                    print(str(e))
                    oledConnected = False
            else:
                #wait and try to find the board again
                sleep(5)

    except (KeyboardInterrupt, SystemExit):  # when you press ctrl+c
        ledOff()
        GPIO.cleanup()

if __name__ == "__main__":
    main()

