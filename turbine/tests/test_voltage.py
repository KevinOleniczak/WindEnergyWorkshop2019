from __future__ import division
import sys
import Adafruit_MCP3008
CLK  = 11 #pin 23
MISO = 9 #pin 21
MOSI = 10 #pin 19
CS   = 8 #pin 24

refVal = None

try:
    print("Ensure the turbine is connected and spinning. Attempting to measure voltage generation...")
    mcp = Adafruit_MCP3008.MCP3008(clk=CLK, cs=CS, miso=MISO, mosi=MOSI)
    refVal = mcp.read_adc(0) #turbine is on channel 0
    calcVolt = ((3300/1023) * refVal) / 1000

    print('Raw ADC Value: ', refVal)
    print("ADC Voltage: {0:.3f} V".format(calcVolt))

except Exception as e:
        print(str(e))

if refVal == None:
    print("Voltage Sensor is NOT working. Check connections and ensure the turbine is spinning.")
    sys.exit(1)
else:
    if calcVolt == 0:
        print("Voltage Sensor is not detecting any voltage reading. Is the turbine connected and spinning?")
        sys.exit(1)
    else:
        print("Voltage Sensor is working")
        sys.exit(0)


