#!/bin/bash
cd "$(dirname "$0")"

# Written by StormTheory
# https://github.com/stormtheory/packhowl

### Creates or opens the virtual Enviorment needed for app tools to run
##### Note you will need at least 2G of /tmp space available for the startup install.
##### Virtual environment may take up 500Mb of space for all needed packages.
##### Runs the creating and installing of the virtual environment setup one time.

APP='packhowl'

# No running as root!
ID=$(id -u)
if [ "$ID" == '0'  ];then
        echo "Not safe to run as root... exiting..."
        exit
fi

# 🧾 Help text
show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -d             Debug mode
  -h             Show this help message

Example:
  $0 -vdl
EOF
}

# 🔧 Default values
APP=true
DEBUG=false

# 🔍 Parse options
while getopts ":wldhc" opt; do
  case ${opt} in
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


# 🛡️  Set safe defaults
set -euo pipefail
IFS=$'\n\t'


if [ $DEBUG == true ];then
	#### Export Variables
		export PYTHONWARNINGS="ignore"
	#### Run the AI
		echo "Starting Client"
		python3 server.py --debug
		exit 0
elif [ $APP == true ];then
	#### Export Variables
		export PYTHONWARNINGS="ignore"
	#### Run the AI
		echo "Starting Server"
		python3 server.py
		exit 0
fi
echo "ERROR!"
exit 1
