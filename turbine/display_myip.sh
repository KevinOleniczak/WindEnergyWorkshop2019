#!/bin/bash
sleep 5
cd /home/pi/WindEnergyWorkshop2019
echo "git pull for WindEnergyWorkshop2019"
git remote set-url origin https://github.com/KevinOleniczak/WindEnergyWorkshop2019.git
git pull
/home/pi/WindEnergyWorkshop2019/turbine/tests/test_hardware.sh auto
/usr/bin/python /home/pi/WindEnergyWorkshop2019/turbine/myip_oled.py $?
