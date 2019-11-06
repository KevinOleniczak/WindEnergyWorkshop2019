#!/usr/bin/env bash

#Perform a collection of tests to validate the connectivity of sensors and actuators
echo ""
echo "****************************************"
echo "Welcome to the Wind Energy Turbine Test Program"
echo "Several tests will be performed with pauses after each one for you to review the results."
echo "Optionally you can choose to run this test program in auto mode without prompts by starting this script with the argument 'auto'."
echo ""

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
successCnt=0

if [ $# -eq 0 ]
then
  mode="interactive"
  echo "Interactive Mode"
else
  if [ "$1" = "auto" ]
  then
    mode="auto"
    echo "Automatic Mode"
  else
    echo "Unknown mode! Exiting."
    exit 1
  fi
fi


echo ""
echo "****************************************"
echo "TEST: Accelerometer"
echo "****************************************"
python $DIR/test_accel.py $mode
if [ $? -eq 0 ]
then
  echo "Accelerometer Sensor >> PASSED"
  ((successCnt=successCnt+1))
else
  echo "Accelerometer Sensor >> FAILED"
fi

if [ $mode != "auto" ]
then
  echo -n "Press ENTER to continue..."
  read ans
fi


echo ""
echo "****************************************"
echo "TEST: Rotation Sensor"
echo "****************************************"
python $DIR/test_rpm.py $mode
if [ $? -eq 0 ]
then
  echo "Rotation Sensor >> PASSED"
  ((successCnt=successCnt+1))
else
  echo "Rotation Sensor >> FAILED"
fi

if [ $mode != "auto" ]
then
  echo -n "Press ENTER to continue..."
  read ans
fi


echo ""
echo "****************************************"
echo "TEST: Voltage Sensor"
echo "****************************************"
python $DIR/test_voltage.py $mode
if [ $? -eq 0 ]
then
  echo "Voltage Sensor >> PASSED"
  ((successCnt=successCnt+1))
else
  echo "Voltage Sensor >> FAILED"
fi

if [ $mode != "auto" ]
then
  echo -n "Press ENTER to continue..."
  read ans
fi


echo ""
echo "****************************************"
echo "TEST: RGB LED Light"
echo "****************************************"
python $DIR/test_rgb.py $mode
if [ $? -eq 0 ]
then
  echo "RGB LED Light >> PASSED"
  ((successCnt=successCnt+1))
else
  echo "RGB LED Light >> FAILED"
fi

if [ $mode != "auto" ]
then
  echo -n "Press ENTER to continue..."
  read ans
fi


echo ""
echo "****************************************"
echo "TEST: Brake Servo"
echo "****************************************"
python $DIR/test_brake_servo.py $mode
if [ $? -eq 0 ]
then
  echo "Brake Servo >> PASSED"
  ((successCnt=successCnt+1))
else
  echo "Brake Servo >> FAILED"
fi

if [ $successCnt -eq 5 ]
then
  echo "All tests passed"
  exit 0
else
  echo "Some tests failed"
  exit 1
fi

