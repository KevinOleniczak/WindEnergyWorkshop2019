import sys
from mpu6050 import mpu6050

accel_x = None

try:
    print("Ensure the turbine is connected. Attempting to retrieve XYZ data...")
    # Read the X, Y, Z axis acceleration values and print them.
    sensor = mpu6050(0x68)
    accelerometer_data = sensor.get_accel_data()
    accel_x = accelerometer_data["x"]
    accel_y = accelerometer_data["y"]
    accel_z = accelerometer_data["z"]
    print("accel_x: " + str(accel_x) + " accel_y: " + str(accel_y) + " accel_z: " + str(accel_z ))
except:
    pass

if accel_x == None:
    print("Accelerometer Sensor is NOT working")
    sys.exit(1)
else:
    print("Accelerometer Sensor is working")
    sys.exit(0)

