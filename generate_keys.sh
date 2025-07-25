#!/bin/bash
cd "$(dirname "$0")"

# Written by StormTheory
# https://github.com/stormtheory/packhowl

### Use hostnames
SERVER='server'
CLIENTS=''

user=packhowl
user_home="/var/lib/${user}"
DATA_DIR="${user_home}/.packhowl"
CERTS_DIR="${DATA_DIR}/certs"
WHITELIST="${CERTS_DIR}/cn_whitelist.txt"

if [ ! -d $user_home ];then
	echo "ERROR: $user_home not found..."
	exit 1
elif [ ! -d $CERTS_DIR ];then
        echo "ERROR: $CERTS_DIR not found..."
        exit 1
elif [ ! -d $DATA_DIR ];then
        echo "ERROR: $DATA_DIR not found..."
        exit 1
fi

# See where we are working from and with
if [[ "$(pwd)" == "/opt/"* ]]; then
	WORKING=/etc/ssl/packhowl
	ID=$(id -u)
	if [ "$ID" != 0 ];then
        	sudo $0
        	exit
	else
        	cd "$(dirname "$0")"
	fi
else
	WORKING=./certs
fi

########################## NOTES #######################################

# ca.crt         # Shared trust anchor
# ca.key         # CA private key (keep secure!)
# server.crt     # Server certificate
# server.key     # Server private key
# client.crt     # Client certificate (used by the agent)
# client.key     # Client private key



# CA certificate                (ca.crt)        Public root certificate trusted by both server and clients to verify certs are valid            Distributed to all clients and server (public, safe to share)
# CA private key                (ca.key)        Private key used to sign server and client certificates (very sensitive!)                       Must be kept secret and secure in a vault or offline
# Server certificate    (server.crt)    Public cert proving server identity to clients  On the server, public
# Server private key    (server.key)    Private key that pairs with server certificate; used to decrypt and prove server ownership      Keep secret on the server
# Client certificate    (client.crt)    Public cert proving client identity to server   On the client, public


####################### OPTIONS #################################################

# 🧾 Help text
show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -c <client hostname(s)>  Generate CA and Client certs       
  -n <client hostname>     Generate new Client cert
  -d                       Debug mode
  -h                       Show this help message

Example:
  $0 -dn 
EOF
}

# 🔧 Default values
GEN_DIR=false
GEN_CA=false
GEN_CN=false
DEBUG=false

# 🔍 Parse options
while getopts ":c:n:dh" opt; do
  case ${opt} in
     c)
	GEN_DIR=true
        GEN_CA=true
	GEN_CN=true
        CLIENTS="$OPTARG"
        ;;	
     n)
	GEN_DIR=true
	GEN_CA=false
	GEN_CN=true
	CLIENTS="$OPTARG"
        ;;
     d)
        DEBUG=true
        ;;
    h)
      show_help
      exit 0
      ;;
    \?)
      echo "❌ Invalid option: -$OPTARG" >&2
      show_help
      exit 1
      ;;
    :)
      echo "❌ Option -$OPTARG requires an argument." >&2
      show_help
      exit 1
      ;;
  esac
done


PACKAGES='openssl'
for package in $PACKAGES; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed"; then
        echo "✅ Installed... $package"
    else
        echo "⚠️  $package is required and must be installed from your distro."
        sudo apt update && sudo apt install -y "$package"
    fi
done


if [ -z "$CLIENTS" ];then
	echo "No Client hostname(s) found..."
	exit 1
fi

if [ "$GEN_DIR" = true ];then
	### Make and permission
	if [ ! -d $WORKING ];then
		mkdir -p $WORKING
	fi
	chmod 700 $WORKING
	cd $WORKING
fi

if [ "$GEN_CA" = true ];then

	### Remove Old certs
	if [ -d ca ];then
		rm ca.crt
		rm ca.srl
		rm -rf ca
		rm -rf server
		rm -rf client
		rm $WHITELIST
	fi

	### Build Dir
	if [ ! -d ca ];then
		mkdir ca
		mkdir server
		mkdir client
		touch $WHITELIST
	fi

	### CA
	echo " Generate private key for the CA"
	openssl genrsa -out ca/ca.key 4096

	echo " Create root CA certificate"
	openssl req -x509 -new -nodes -key ca/ca.key -sha256 -days 1825 -out ca.crt -subj "/CN=MyRootCA"

	### Server
	# Generate server private key
	openssl genrsa -out server/server.key 2048

	# Generate server certificate signing request (CSR)
	openssl req -new -key server/server.key -out server/server.csr -subj "/CN=server"

	# Sign the server cert with your CA
	openssl x509 -req -in server/server.csr -CA ca.crt -CAkey ca/ca.key -CAcreateserial \
	-out server/server.crt -days 825 -sha256

	# Combine cert and key into one PEM
	cat server/server.crt server/server.key > server/server.pem

	# Create ca.pem
	cp ca.crt ca.pem
fi

if [ "$GEN_CN" = true ];then
	#### Client #############################

	for client in $CLIENTS;do
		# Generate client private key
		openssl genrsa -out client/$client.key 2048

		# Create CSR with a unique CN (must match your whitelist.txt)
		openssl req -new -key client/$client.key -out client/$client.csr -subj "/CN=$client"

		# Sign it with the CA
		openssl x509 -req -in client/$client.csr -CA ca.crt -CAkey ca/ca.key -CAcreateserial \
	-out client/$client.crt -days 825 -sha256
		
		# Make PEM
		cat client/$client.crt client/$client.key > client/$client.pem
		echo "$client" >> $WHITELIST
	
	done

	mkdir -p $CERTS_DIR
	rm $CERTS_DIR/*.pem
	
	cp -v ca.pem server/*.pem client/*.pem $CERTS_DIR
	chown -R $user:$user $CERTS_DIR
	chmod 600 $CERTS_DIR/*
	
	ls -l $CERTS_DIR/*
	
fi
