#!/bin/bash
cd "$(dirname "$0")"

# Written by StormTheory
# https://github.com/stormtheory/packhowl

### Creates or opens the virtual Enviorment needed for app tools to run
##### Note you will need at least 4G of /tmp space available for the startup install.
##### Virtual environment may take up 2Gbs of space for all needed packages.
##### Runs the creating and installing of the virtual environment setup one time.

PYENV_DIR='./.venv'
RUN='.run_client_installed'

# No running as root!
ID=$(id -u)
if [ "$ID" == '0'  ];then
        echo "Not safe to run as root... exiting..."
        exit
fi

# CAN ONLY BE ONE!!!!
# --- Get full, resolved path to the script ---
SCRIPT_PATH=$(readlink -f "$0")                 # Full path to this script
SCRIPT_NAME=$(basename "$SCRIPT_PATH")          # Just the filename
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")            # Parent directory path

# --- Count instances of this exact script (excluding our own PID) ---
RUNNING=$(ps -eo pid,args | grep "$SCRIPT_PATH" | grep -v " $$" | grep -v "^ *$$ " | wc -l)

# --- Exit if another instance is running ---
if [[ "$RUNNING" -ge 1 ]]; then
  echo "Another instance of $SCRIPT_NAME in $SCRIPT_DIR is running. Exiting."
  exit 1
fi


# 🛡️ Set safe defaults
set -euo pipefail
IFS=$'\n\t'

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
LOOPBACK=false

# 🔍 Parse options
while getopts ":wldhc" opt; do
  case ${opt} in
    d)
        DEBUG=true
        ;;
    l)
        LOOPBACK=true
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
        APT_LIST=$(apt list 2>/dev/null)
        ENV_INSTALL=True
        PIP_INSTALL=True
elif [ -f $PYENV_DIR/$RUN ];then
        echo "✅ Installed... .venv"
        echo "✅ Installed... $RUN"
        ENV_INSTALL=False
        PIP_INSTALL=False
elif [ ! -f $PYENV_DIR/$RUN ];then
	echo "✅ Installed... .venv"
        APT_LIST=$(apt list 2>/dev/null)
        ENV_INSTALL=False
        PIP_INSTALL=True
else
        exit 1
fi

if [ "$ENV_INSTALL" == 'True' ];then
### Checking dependencies
        
        if echo "$APT_LIST"|grep python3.12-dev;then
                echo "✅ Installed... python3.12-dev"
        else
                echo "⚠️ Installing python3.12-dev"
                sudo apt install python3.12-dev
        fi

        if echo "$APT_LIST"|grep python3.12-venv;then
                echo "✅ Installed... python3.12-venv"
        else
                echo "⚠️ Installing python3.12-venv"
                sudo apt install python3.12-venv
        fi

#### Build the Env Box	
	# 1. Create a virtual environment
		python3 -m venv $PYENV_DIR

	# 2. Activate it
		source $PYENV_DIR/bin/activate

	# 3. Update
		pip install --upgrade pip
fi



if [ "$PIP_INSTALL" == True ];then
	### CLIENT NEEDS
        source $PYENV_DIR/bin/activate


        if echo "$APT_LIST"|grep portaudio19-dev;then
                echo "✅ Installed... portaudio19-dev"
        else
                echo "⚠️ Install portaudio19-dev for audio"
                sudo apt install portaudio19-dev
        fi
        if echo "$APT_LIST"|grep cmake;then
                echo "✅ Installed... cmake"
        else
                echo "⚠️ Install cmake for compiling for audio/voice libs"
                sudo apt install cmake
        fi

        ### GUI
        pip install PySide6
        pip install pyside6-essentials

        ### Keyboard hotkeys
        pip install pynput

        ### Audio/Voice
        pip install sounddevice
	pip install numpy
        pip install samplerate

        ### Encoding
        pip install opuslib


touch $PYENV_DIR/$RUN
fi

if [ ! -d $HOME/.packhowl/certs ];then
        mkdir -p ${HOME}/.packhowl/certs
fi

#### Run the Box
        source $PYENV_DIR/bin/activate

if [ $LOOPBACK == true ];then
	#### Export Variables
		export PYTHONWARNINGS="ignore"
	#### Run the AI
		echo "Starting Client"
		python3 client.py -l
		exit 0        
elif [ $DEBUG == true ];then
	#### Export Variables
		export PYTHONWARNINGS="ignore"
	#### Run the AI
		echo "Starting Client"
		python3 client.py -d
		exit 0
elif [ $APP == true ];then
	#### Export Variables
		export PYTHONWARNINGS="ignore"
	#### Run the AI
		echo "Starting Client"
		python3 client.py
		exit 0
fi
echo "ERROR!"
exit 1
