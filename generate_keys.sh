#!/bin/bash
cd "$(dirname "$0")"

# Written by StormTheory
# https://github.com/stormtheory/silent-link

### Use hostnames
SERVER='server'
CLIENTS=''

DATA_DIR="${HOME}/.silentlink"

WORKING=./certs
mkdir -p $WORKING
chmod 700 $WORKING
cd $WORKING

if [ -z "$CLIENTS" ];then
	echo "No Client hostname(s) found..."
	exit 1
fi

### Remove Old certs
if [ -d ca ];then
	rm ca.crt
	rm ca.srl
	rm -rf ca
	rm -rf server
	rm -rf client
fi

### Build Dir
if [ ! -d ca ];then
	mkdir ca
	mkdir server
	mkdir client
fi

######################################################################

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

######################################################################

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

done

mkdir -p $DATA_DIR/certs
rm $DATA_DIR/certs/*
cp -v ca.crt $DATA_DIR/certs/ca.pem
cp -v server/*.pem client/*.pem $DATA_DIR/certs/

ls -l $DATA_DIR/certs/*
