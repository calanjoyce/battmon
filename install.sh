#!/bin/sh
# Copy BattMon to the Cardputer Zero and install it.
# Usage: ./install.sh user@host   (the Cardputer's SSH login)
set -e
HOST=${1:?usage: ./install.sh user@host}
cd "$(dirname "$0")"
ssh "$HOST" 'mkdir -p ~/battmon'
scp -q battmon.py battlog.py make_icon.py battmon.desktop battmon-logger.service "$HOST":battmon/
ssh "$HOST" '
set -e
cd ~/battmon
sudo install -d /opt/battmon
sudo install -m 755 battmon.py battlog.py /opt/battmon/
python3 make_icon.py /tmp/battmon.png
sudo install -m 644 /tmp/battmon.png /usr/share/APPLaunch/share/images/battmon.png
sudo install -m 644 battmon.desktop /usr/share/APPLaunch/applications/battmon.desktop
install -D -m 644 battmon-logger.service ~/.config/systemd/user/battmon-logger.service
systemctl --user daemon-reload
systemctl --user enable --now battmon-logger.service
systemctl --user restart battmon-logger.service
systemctl --user restart APPLaunch.service
echo installed
'
