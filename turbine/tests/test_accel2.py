import time

from mpu6050 import mpu6050

sensor = mpu6050(0x68)

print('Printing accelerometer X, Y, Z axis values, press Ctrl-C to quit...')
while True:
    # Read the X, Y, Z axis acceleration values and print them.
    accelerometer_data = sensor.get_accel_data() 
    # Grab the X, Y, Z components from the reading and print them out.
    accel_x = accelerometer_data["x"] 
    accel_y = accelerometer_data["y"]
    accel_z = accelerometer_data["z"]
    print("accel_x: " + str(accel_x) + " accel_y: " + str(accel_y) + " accel_z: " + str(accel_z )) 
    # Wait half a second and repeat.
    time.sleep(0.5)

