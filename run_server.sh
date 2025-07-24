#!/bin/bash
cd "$(dirname "$0")"

# Written by StormTheory
# https://github.com/stormtheory/packhowl

### Creates or opens the virtual Enviorment needed for app tools to run
##### Note you will need at least 2G of /tmp space available for the startup install.
##### Virtual environment may take up 500Mb of space for all needed packages.
##### Runs the creating and installing of the virtual environment setup one time.

APP='packhowl'
PYENV_DIR='./.venv'
RUN='.run_server_installed'

# No running as root!
ID=$(id -u)
if [ "$ID" == '0'  ];then
        echo "Not safe to run as root... exiting..."
        exit
fi

# See where we are working from and with
if [[ "$(pwd)" == "/opt/"* ]]; then
	PYENV_DIR="${HOME}/.venv-${APP}"
else
	PYENV_DIR='./.venv'
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


if [ ! -d $PYENV_DIR ];then
        ENV_INSTALL=True
        PIP_INSTALL=True
elif [ -f $PYENV_DIR/$RUN ];then
        echo "✅ Installed... $PYENV_DIR"
        echo "✅ Installed... $RUN"
        ENV_INSTALL=False
        PIP_INSTALL=False
elif [ ! -f $PYENV_DIR/$RUN ];then
	echo "✅ Installed... $PYENV_DIR"
        ENV_INSTALL=False
        PIP_INSTALL=True
else
        exit 1
fi

if [ "$ENV_INSTALL" == 'True' ];then
### Checking dependencies
        
PACKAGES='python3.12-venv python3.12-dev'
for package in $PACKAGES; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed"; then
        echo "✅ Installed... $package"
    else
        echo "⚠️  $package is required and must be installed from your distro."
        sudo apt update && sudo apt install -y "$package"
    fi
done

#### Build the Env Box	
	# 1. Create a virtual environment
		python3 -m venv $PYENV_DIR

	# 2. Activate it
		source $PYENV_DIR/bin/activate

	# 3. Update
		pip install --upgrade pip
fi


if [ "$PIP_INSTALL" == True ];then
	### SERVER NEEDS
        source $PYENV_DIR/bin/activate

touch $PYENV_DIR/$RUN
fi


# 🛡️  Set safe defaults
set -euo pipefail
IFS=$'\n\t'


#### Run the Box
        source $PYENV_DIR/bin/activate

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
