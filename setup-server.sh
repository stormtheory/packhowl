#!/bin/bash

## Do not promote of support the lack of use of a service user, because it's not safe and less private. 
## However using your own user can be done by running the run_server.sh script without using this setup script and setting up your own service file.
## Look at bottom of the script for a template for your own service.


ID=$(id -u)
if [ "$ID" != 0 ];then
        sudo $0
        exit
else
	cd "$(dirname "$0")"
fi

user=packhowl
user_home="/var/lib/${user}"
SERVICE=packhowl.service

# See where we are working from and with
if [[ "$(pwd)" == "/opt/"* ]]; then
	# Package Installer
	SSL_CERTS_DIR="${user_home}/.packhowl/certs"
	SERVICE_DIR=$(pwd)
else
	# Package Mover Script
       	SSL_CERTS_DIR="${user_home}/.packhowl/certs"
	SERVICE_DIR="${user_home}"
	OPT_DIR=/opt/packhowl
fi


####################### SETUP USER ##############################################

if [ "$1" == installer ];then
	ANS=y
else
	read -p " Setup system user $user or update package in ${user}'s homespace? [y] > " ANS
if
if [ "$ANS" == y ];then
	if [ ! -d $user_home];then
			echo " -- Create $user user and user's homespace"
		sudo useradd --system --user-group --create-home --home-dir $user_home --shell /usr/sbin/nologin $user
		sudo chown -R $user:$user $user_home
		chmod 700 $user_home

		echo " -- Making $SSL_CERTS_DIR"
        	mkdir -p $SSL_CERTS_DIR
	fi

if [ "$1" != installer ];then
		echo " -- Moving directory to $OPT_DIR"
		DIR_PATH=$(pwd)
		PACKAGE_DIR=$(basename "$PWD")

		if [ -d $OPT_DIR ];then
			read -p "Remove old $OPT_DIR? [y] > " ANS
			if [ "$ANS" == y ];then
				echo "Removing.. $OPT_DIR"
				rm -rf $OPT_DIR
			else
				echo "WARNING: $OPT_DIR was not removed..."
			fi
		else
			mkdir -p $OPT_DIR
		fi

		

		##### Safety
		if [[ -z "$PACKAGE_DIR" || "$PACKAGE_DIR" == "/" || "$PACKAGE_DIR" =~ ^[[:space:]]*$ ]]; then
			echo "❌ Error: PACKAGE_DIR is unset, empty, or root (/). Aborting."
			exit 1
		fi


		mv $DIR_PATH $OPT_DIR

		echo " -- Changing directory to $OPT_DIR"
		cd $OPT_DIR

		echo " -- Unpacking to "
		mv $PACKAGE_DIR/* .

		if [ -d $PACKAGE_DIR/.venv ];then
			mv $PACKAGE_DIR/.venv .
		fi

		echo " -- Removing leftover packaging at $OPT_DIR/$PACKAGE_DIR"
		rm -rf $PACKAGE_DIR
	fi

	echo " -- Setting permissions"
	chown -R $user:$user /var/lib/$user
	chmod 700 /var/lib/$user

elif [ -d $user_home ];then
	cd $user_home
else
	read -p ' 
 Not secure to run the server without a system user account,
 and not offically supported, tested, or promoted, sure you want to continue? [y] > ' ANS
	if [ "$ANS" != y ];then
		exit 0
	else
		user=${SUDO_USER}
	fi
fi




#################### KEYS GEN ################################

if [ "$1" == installer ];then
        ANS=no
else
	read -p " Generate SSL Keys [y] ?> " ANS
fi
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

if [ "$1" == installer ];then
        ANS=y
else
	read -p " Install service [y] ?> " ANS
fi

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
WorkingDirectory=$SERVICE_DIR
ExecStart=$SERVICE_DIR/run_server.sh
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
