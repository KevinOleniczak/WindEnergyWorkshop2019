#!/bin/bash
sleep 5
cd /home/pi/WindEnergyWorkshop2019
echo "git fetch for WindEnergyWorkshop2019"
git remote set-url origin https://github.com/KevinOleniczak/WindEnergyWorkshop2019.git
git fetch origin
git reset --hard origin/master
/home/pi/WindEnergyWorkshop2019/turbine/tests/test_hardware.sh auto
/usr/bin/python /home/pi/WindEnergyWorkshop2019/turbine/myip_oled.py $?
