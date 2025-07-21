#!/bin/bash

ID=$(id -u)
if [ "$ID" != 0 ];then
        sudo $0
        exit
else
	cd "$(dirname "$0")"
fi

user=packhowl
SERVICE=packhowl.service
SSL_CERTS_DIR=.packhowl/certs


####################### SETUP USER ##############################################

read -p " Setup system user $user or reinstall package in ${user}'s homespace? [y] > " ANS
if [ "$ANS" == y ];then
	if [ ! -d /var/lib/$user ];then
			echo " -- Create $user user"
		sudo useradd --system --user-group --create-home --home-dir /var/lib/$user --shell /usr/sbin/nologin $user
		sudo chown -R $user:$user /var/lib/$user
		chmod 700 /var/lib/$user
	fi

	echo " -- Moving directory to /var/lib/$user"
	DIR_PATH=$(pwd)
	PACKAGE_DIR=$(basename "$PWD")

	##### Safety
	if [[ -z "$PACKAGE_DIR" || "$PACKAGE_DIR" == "/" || "$PACKAGE_DIR" =~ ^[[:space:]]*$ ]]; then
		echo "❌ Error: PACKAGE_DIR is unset, empty, or root (/). Aborting."
		exit 1
	fi


	mv $DIR_PATH /var/lib/$user

	echo " -- Changing directory to /var/lib/$user"
	cd /var/lib/$user

	echo " -- Unpacking to /var/lib/$user"
	mv $PACKAGE_DIR/* .

	if [ -d $PACKAGE_DIR/.venv ];then
		mv $PACKAGE_DIR/.venv .
	fi

	echo " -- Removing leftover packaging at /var/lib/$user/$PACKAGE_DIR"
	rm -rf $PACKAGE_DIR

	echo " -- Making $SSL_CERTS_DIR"
	mkdir -p $SSL_CERTS_DIR

	echo " -- Setting permissions"
	chown -R $user:$user /var/lib/$user
	chmod 700 /var/lib/$user

elif [ -d /var/lib/$user ];then
	cd /var/lib/$user
else
	read -p ' 
Not secure to run the server without a system user account,
 sure you want to continue? [y] > ' ANS
	if [ "$ANS" != y ];then
		exit 0
	else
		user=${SUDO_USER}
	fi
fi




#################### KEYS GEN ################################

read -p " Generate SSL Keys [y] ?> " ANS
if [ "$ANS" == y ];then
	read -p " List all Client Hostnames > " HOSTNAMES
	if [ -z "$HOSTNAMES" ];then
		echo "❌ Error: Hostnames missing..."
		exit
	fi 
	chmod +x generate_keys.sh
	sudo -u $user ./generate_keys.sh -c "$HOSTNAMES"
fi



################### SERVICE #################################


read -p " Install service [y] ?> " ANS
if [ "$ANS" == y ];then

echo " -- Create systemd service"
DIR_PATH=$(pwd)
cat <<EOF > /etc/systemd/system/$SERVICE
[Unit]
Description=--- Pack Howl Service---
After=default.target

[Service]
User=$user
Group=$user
Type=fork
WorkingDirectory=$DIR_PATH
ExecStart=$DIR_PATH/run_server.sh
ExecStop=/bin/kill -9 $MAINPID
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF


systemctl daemon-reload
systemctl enable $SERVICE
systemctl restart $SERVICE

fi
