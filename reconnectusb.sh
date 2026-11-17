#!/bin/bash
echo "Scanning IDS cameras..."

CAM_DEVS=$(lsusb -d 1409: | awk '{print $2 "/" $4}' | sed 's/://')

for dev in $CAM_DEVS; do
    echo "Stagger-resetting /dev/bus/usb/$dev..."
    sudo udevadm trigger --action=change --subsystem-match=usb
    sleep 1.5
done

sudo systemctl restart ueyeusbdrc.service
echo "IDS Cameras ready."