#!/usr/bin/env bash
cd "$(dirname "$0")"

# Written by StormTheory
# https://github.com/stormtheory/packhowl

### Creates or opens the virtual Enviorment needed for app tools to run
##### Note you will need at least 4G of /tmp space available for the startup install.
##### Virtual environment may take up 2Gbs of space for all needed packages.
##### Runs the creating and installing of the virtual environment setup one time.


APP='packhowl'
PYENV_DIR='./.venv'
RUN='.run_client_installed'

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

# CAN ONLY BE ONE!!!!
APP='packhowl'
RAM_DIR='/dev/shm'
BASENAME=$(basename $0)
RAM_FILE="${RAM_DIR}/${APP}-${BASENAME}.lock"
fs_type=$(stat -f -c %T "$RAM_DIR")
if [ -d $RAM_DIR ];then
        if [[ "$fs_type" == "tmpfs" ]] || [[ "$fs_type" == "ramfs" ]]; then
                if [ -f $RAM_FILE ]; then
                echo "RAM lock file exists: $RAM_FILE"
                exit
                else
                        touch $RAM_FILE
                        chmod 600 $RAM_FILE
                        # Cleanup on exit
                        trap 'rm -f "$RAM_FILE"; echo "[*] Lock released."; exit' INT TERM EXIT
                fi
        else
                echo "[-] '$RAM_DIR' is NOT on a RAM disk (type: $fs_type)"
        fi
else
        echo "ERROR: $RAM_DIR not present to lock app."
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
        
# Get the major.minor version of the system's default python3
PYTHON_VERSION=$(python3 -c 'import sys; v=sys.version_info; print(f"{v.major}.{v.minor}")')
# Validate Python version (3.7+ required)
PY_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -ne 3 ] || [ "$PY_MINOR" -lt 7 ]; then
    echo "❌ Python 3.7+ is required. Found Python $PYTHON_VERSION"
    exit 1
fi
# Try versioned package names first
PACKAGES="python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev"

for package in $PACKAGES; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed"; then
        echo "✅ Installed... $package"
    else
        echo "⚠️  $package is not installed: $package"
        echo "➡️  Attempting to install $package"
        if ! sudo apt-get install -y "$package"; then
            echo "⚠️  Failed to install $package — trying fallback: python3-venv or python3-dev"
            fallback_pkg="python3-venv"
            [ "$package" = "python${PYTHON_VERSION}-dev" ] && fallback_pkg="python3-dev"
            sudo apt-get install -y "$fallback_pkg" || {
                echo "❌ Failed to install fallback: $fallback_pkg"
                exit 1
            }
        fi
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
	### CLIENT NEEDS
        source $PYENV_DIR/bin/activate

        
        package=portaudio19-dev 
        if dpkg-query -W -f='${Status}' $package 2>/dev/null | grep -q "install ok installed";then
                echo "✅ Installed... $package"
        else
                echo "⚠️ $package is required and must be installed from your distro for audio."
                sudo apt install $package
        fi
        package=cmake 
        if dpkg-query -W -f='${Status}' $package 2>/dev/null | grep -q "install ok installed";then
                echo "✅ Installed... $package"
        else
                echo "⚠️ $package is required and must be installed from your distro compiling of audio libraries."
                sudo apt install $package
        fi

        ######################################### xcb ##############################################
        # qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt xcb platform plugin.
        # qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.
        # This application failed to start because no Qt platform plugin could be initialized.
        #
        # This checks for either libxcb‑cursor0 or its legacy name xcb‑cursor0.
        # --- 1. Detect if either package is already installed --------------------
        check_xcb_cursor() {
        PKGS='libxcb-cursor0 xcb-cursor0'
        for PKG in $PKGS; do
                if dpkg-query -W -f='${Status}' "$PKG" 2>/dev/null|grep -q "install ok installed"; then
                echo "✅ Installed... $PKG"
                return 0                                       # All good, exit early
                fi
        done
        echo "⚠️  Neither libxcb-cursor0 nor xcb-cursor0 found.  Attempting install..."
        # --- 2. Discover which package name exists in the current repositories ----
        # (Using apt-cache policy because it is fast, does not modify system state,
        #  and respects APT pinning/mirrors.)
        for PKG in libxcb-cursor0 xcb-cursor0; do
                if dpkg-query -W -f='${Status}' "$PKG" 2>/dev/null|grep -q "install ok installed"; then
                CANDIDATE="$PKG"
                break
                fi
        done
        if [ -z "$CANDIDATE" ]; then
                echo "❌ No matching xcb‑cursor package available in your APT sources."
                return 1
        fi
        # --- 3. Install the discovered candidate package -------------------------
        echo "ℹ️  Installing $CANDIDATE ..."
        sudo apt update && sudo apt install -y "$CANDIDATE"
        # --- 4. Post‑install verification ----------------------------------------
        if dpkg-query -W -f='${Status}' "$PKG" 2>/dev/null|grep -q "install ok installed"; then
                echo "✅ Successfully installed $CANDIDATE"
                return 0
        else
                echo "❌ Installation of $CANDIDATE appears to have failed."
                return 1
        fi
        }
        check_xcb_cursor
        #################################### END ################################################################################

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

# 🛡️ Set safe defaults
set -euo pipefail
IFS=$'\n\t'

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
