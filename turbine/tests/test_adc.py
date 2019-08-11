# ADC MCP3008
# Software SPI configuration:
#import Adafruit_GPIO.SPI as SPI
import Adafruit_MCP3008
CLK  = 11 #pin 23
MISO = 9 #pin 21
MOSI = 10 #pin 19
CS   = 8 #pin 24
#mcp = Adafruit_MCP3008.MCP3008(23, 24, 21, 19)
mcp = Adafruit_MCP3008.MCP3008(clk=CLK, cs=CS, miso=MISO, mosi=MOSI)


import time
while True:
    refVal = mcp.read_adc(0)
    calcVolt = round(((3300/1023) * refVal) / 1000, 2)

    print('Raw ADC Value: ', refVal)
    print('ADC Voltage: ' + str(calcVolt) + 'V')
    time.sleep(0.5)
    