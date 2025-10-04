#!/bin/bash
# Quick Installer - Pulls from GitHub

echo "🤖 DroidDeck Backend Quick Installer"
echo "=================================="

# Check for git
if ! command -v git &> /dev/null; then
    echo "Installing git..."
    sudo apt update && sudo apt install -y git
fi

# Clone repository
if [ -d "DroidDeck" ]; then
    echo "Directory exists. Pulling latest changes..."
    cd DroidDeck
    git pull
else
    echo "Cloning repository..."
    git clone https://github.com/ShimmerNZ/DroidDeck-Backend.git
    cd DroidDeck
fi

# Make install script executable
chmod +x install.sh

# Run main installer
./install.sh