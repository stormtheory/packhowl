#!/usr/bin/env bash
cd "$(dirname "$0")"

# Written by StormTheory
# https://github.com/stormtheory/packhowl

DIR=$(pwd)


echo "[Desktop Entry]
Name=Pack Howl
Exec=$DIR/run_client.sh
Comment=
Terminal=false
PrefersNonDefaultGPU=false
Icon=$DIR/assets/wolf_red_bg.png
Type=Application
Name[en_US]=Pack Howl" > $HOME/Desktop/PackHowl.desktop
