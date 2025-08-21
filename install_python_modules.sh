#!/bin/bash
# Activate pyenv and wall-e environment
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
pyenv activate wall-e

#updated pip
python -m pip install --upgrade pip

# Install required Python modules
pip install numpy opencv-python websockets aiofiles loguru evdev RPi.GPIO adafruit-ads1x15 adafruit-circuitpython-ads1x15
pip3 install numpy opencv-python websockets aiofiles loguru evdev RPi.GPIO adafruit-ads1x15 adafruit-circuitpython-ads1x15

# you're going to use i2c so you can use this to scan for devices 
sudo apt install -y i2c-tools
