#!/bin/bash
echo "****************************" 
echo "Wind Tubine IoT Device Setup"
echo "****************************"
echo "Downloading bundle from Amazon S3" 
if wget -O /home/pi/certs/bundle.zip "$1"; then 
   echo "Extracing bundle"
   if unzip -o /home/pi/certs/bundle.zip -d /home/pi/certs; then
      echo "Files extracted to /home/pi/certs"
      echo "You may run the start_turbine.sh program when ready"
   else
      echo "Error occurred extracting the bundle.zip file in /hhome/pi/certs. "
   fi
else
   echo "An error occurred. Make sure the presigned url provided is wrapped in double-quotes and not expired" 
fi
