#!/bin/bash

# Update and install dependencies
sudo apt update
sudo apt install -y \
  make build-essential libssl-dev zlib1g-dev \
  libbz2-dev libreadline-dev libsqlite3-dev \
  curl libncursesw5-dev xz-utils tk-dev \
  libxml2-dev libxmlsec1-dev libffi-dev \
  liblzma-dev git

# Install pyenv and pyenv-virtualenv
curl https://pyenv.run | bash

# Add pyenv to shell startup
echo 'export PATH="$HOME/.pyenv/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(pyenv init --path)"' >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc
echo 'eval "$(pyenv virtualenv-init -)"' >> ~/.bashrc

# Reload shell
export PATH="$HOME/.pyenv/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"

# Install Python 3.9.13 and create 'wall-e' virtual environment
pyenv install 3.9.13
pyenv virtualenv 3.9.13 wall-e
pyenv activate wall-e

# Confirm Python version
python --version
