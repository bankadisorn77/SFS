echo "$USER ALL=(ALL) NOPASSWD: /home/utac/Project/SFS-main/reconnectusb.sh, /bin/systemctl restart ueyeusbdrc.service, /bin/udevadm" | sudo tee /etc/sudoers.d/ids-camera-reset
sudo chmod 0440 /etc/sudoers.d/ids-camera-reset

chmod +x /home/utac/Project/SFS-main/reconnectusb.sh