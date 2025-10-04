#!/bin/bash
# DroidDeck Backend Installer for Raspberry Pi 5
# Streamlined installation - Essential components only
# Version: 4.0

set -e

echo "🤖 DroidDeck Backend Installer v4.0"
echo "===================================="
echo "🎯 Target: Raspberry Pi 5 with Python 3.9.13"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }
print_success() { echo -e "${PURPLE}[SUCCESS]${NC} $1"; }

# Check Raspberry Pi
check_raspberry_pi() {
    print_step "Checking system compatibility..."
    
    if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        print_warning "Not running on Raspberry Pi - some features may not work"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    else
        PI_MODEL=$(grep "Raspberry Pi" /proc/cpuinfo | head -1 | cut -d: -f2 | xargs)
        print_success "Detected: $PI_MODEL"
    fi
}

# Update system
update_system() {
    print_step "Updating system packages..."
    sudo apt update
    sudo apt upgrade -y
    print_success "System updated"
}

# Install system dependencies
install_system_deps() {
    print_step "Installing system dependencies..."
    
    sudo apt install -y \
        build-essential cmake pkg-config git curl wget \
        python3 python3-pip python3-venv python3-dev \
        i2c-tools python3-smbus minicom \
        python3-rpi.gpio python3-gpiozero python3-lgpio \
        alsa-utils pulseaudio pulseaudio-utils portaudio19-dev libasound2-dev \
        ffmpeg sox libsox-fmt-all \
        samba samba-common-bin cifs-utils \
        make libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
        llvm libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
        libffi-dev liblzma-dev libgdbm-dev libnss3-dev
    
    print_success "System dependencies installed"
}

# Setup pyenv with Python 3.9.13
setup_pyenv() {
    print_step "Setting up pyenv with Python 3.9.13..."
    
    if command -v pyenv >/dev/null 2>&1; then
        print_status "pyenv already installed"
    else
        print_status "Installing pyenv..."
        curl https://pyenv.run | bash
        
        # Add to bashrc if not present
        if ! grep -q "pyenv init" "$HOME/.bashrc"; then
            cat >> "$HOME/.bashrc" << 'EOF'

# Pyenv configuration
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
EOF
            print_status "Added pyenv to .bashrc"
        fi
        
        export PYENV_ROOT="$HOME/.pyenv"
        export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init -)"
    fi
    
    # Install Python 3.9.13
    if ! pyenv versions | grep -q "3.9.13"; then
        print_status "Installing Python 3.9.13..."
        pyenv install 3.9.13
    fi
    
    pyenv local 3.9.13
    print_success "Python 3.9.13 configured"
}

# Create virtual environment
create_venv() {
    print_step "Creating virtual environment..."
    
    python -m venv venv
    source venv/bin/activate
    
    pip install --upgrade pip setuptools wheel
    
    print_success "Virtual environment created"
}

# Install Python dependencies
install_python_deps() {
    print_step "Installing Python dependencies..."
    
    source venv/bin/activate
    
    # Core backend
    pip install \
        "websockets>=10.0" \
        "pyserial>=3.5" \
        "psutil>=5.8.0" \
        "pygame>=2.1.0" \
        "requests>=2.25.0" \
        "flask>=2.0.0" \
        "flask-socketio>=5.0.0" \
        "python-engineio>=4.0.0" \
        "python-socketio>=5.0.0" \
        "numpy>=1.21.6"
    
    # Hardware control
    pip install \
        gpiozero \
        rpi-lgpio \
        adafruit-circuitpython-ads1x15 \
        adafruit-blinka \
        lgpio
    
    # Modular backend
    pip install \
        "watchdog>=2.1.0" \
        "jsonschema>=3.2.0" \
        "dataclasses-json>=0.5.0" \
        "python-dateutil>=2.8.0"
    
    # Audio
    pip install pyaudio wave mutagen
    
    # Optional: Computer vision
    pip install "opencv-python==4.5.5.64" --no-cache-dir || print_warning "OpenCV install skipped"
    
    print_success "Python dependencies installed"
}

# Configure Pi interfaces
configure_pi_interfaces() {
    print_step "Configuring Raspberry Pi interfaces..."
    
    CONFIG_FILE="/boot/firmware/config.txt"
    [ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"
    
    if [ ! -f "$CONFIG_FILE" ]; then
        print_error "Cannot find Pi config file"
        return 1
    fi
    
    sudo cp "$CONFIG_FILE" "${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Enable I2C
    grep -q "dtparam=i2c_arm=on" "$CONFIG_FILE" || \
        echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_FILE" > /dev/null
    
    # Enable SPI
    grep -q "dtparam=spi=on" "$CONFIG_FILE" || \
        echo "dtparam=spi=on" | sudo tee -a "$CONFIG_FILE" > /dev/null
    
    # Enable UART
    grep -q "enable_uart=1" "$CONFIG_FILE" || \
        echo "enable_uart=1" | sudo tee -a "$CONFIG_FILE" > /dev/null
    
    print_success "Hardware interfaces enabled"
}

# Create essential directories
create_directories() {
    print_step "Creating directory structure..."
    
    # Only create if missing - configs should come from repo
    mkdir -p configs/backups
    mkdir -p logs
    mkdir -p audio
    
    print_success "Directories verified"
}

# Setup SMB share
setup_smb_share() {
    print_step "Setting up SMB file sharing..."
    
    BACKEND_DIR=$(pwd)
    CURRENT_USER=$(whoami)
    
    # Backup existing smb.conf
    if [ -f /etc/samba/smb.conf ]; then
        sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
    fi
    
    # Create SMB configuration
    cat > droiddeck_smb.conf << EOF
# DroidDeck Backend Share
[DroidDeck-Backend]
    comment = DroidDeck Robot Backend (Guest Access)
    path = $BACKEND_DIR
    browseable = yes
    read only = no
    guest ok = yes
    create mask = 0664
    directory mask = 0775
    force user = $CURRENT_USER
    public = yes
    writable = yes
EOF

    # Append to system smb.conf
    sudo tee -a /etc/samba/smb.conf < droiddeck_smb.conf > /dev/null
    
    # Set permissions
    chmod 775 "$BACKEND_DIR"
    chmod 775 "$BACKEND_DIR/audio" 2>/dev/null || true
    chmod 775 "$BACKEND_DIR/configs" 2>/dev/null || true
    chmod 775 "$BACKEND_DIR/logs" 2>/dev/null || true
    
    # Start samba services
    sudo systemctl enable smbd nmbd
    sudo systemctl restart smbd nmbd
    
    # Cleanup
    rm -f droiddeck_smb.conf
    
    print_success "SMB file sharing configured"
}

# Verify configs exist
verify_configs() {
    print_step "Verifying configuration files..."
    
    if [ ! -d "configs" ]; then
        print_error "configs directory not found!"
        print_error "Make sure you cloned the complete repository"
        exit 1
    fi
    
    # Check for essential config files
    MISSING_CONFIGS=()
    [ ! -f "configs/hardware_config.json" ] && MISSING_CONFIGS+=("hardware_config.json")
    [ ! -f "configs/camera_config.json" ] && MISSING_CONFIGS+=("camera_config.json")
    
    if [ ${#MISSING_CONFIGS[@]} -gt 0 ]; then
        print_warning "Missing config files: ${MISSING_CONFIGS[*]}"
        print_warning "These will be created on first run with defaults"
    else
        print_success "All configuration files present"
    fi
}

# Main installation
main() {
    print_status "🤖 Starting DroidDeck Backend Installation"
    echo "Current directory: $(pwd)"
    echo "Current user: $(whoami)"
    echo ""
    
    check_raspberry_pi
    update_system
    install_system_deps
    setup_pyenv
    create_venv
    install_python_deps
    configure_pi_interfaces
    create_directories
    setup_smb_share
    verify_configs
    
    echo ""
    echo "🎉 DroidDeck Backend Installation Complete!"
    echo "==========================================="
    echo ""
    print_success "✅ Installed Components:"
    echo "  🐍 Python 3.9.13 with pyenv"
    echo "  📦 All backend dependencies"
    echo "  🔧 Hardware interfaces configured"
    echo "  📁 Directory structure created"
    echo "  📂 SMB file sharing enabled"
    echo ""
    
    HOSTNAME=$(hostname)
    IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "unknown")
    
    print_success "✅ Network Access:"
    echo "  Windows: \\\\$HOSTNAME\\DroidDeck-Backend"
    echo "  Mac/Linux: smb://$IP_ADDRESS/DroidDeck-Backend"
    echo "  🔓 Guest access enabled (no password)"
    echo ""
    print_success "✅ Next Steps:"
    echo "  1. 🔄 Reboot to activate hardware: sudo reboot"
    echo "  2. 🚀 Start backend: python main.py"
    echo "  3. 🌐 WebSocket server will run on ws://$IP_ADDRESS:8766"
    echo ""
    print_step "⚠️  IMPORTANT: Reboot Required"
    echo "  Hardware interfaces (I2C, SPI, UART) need reboot to activate"
    echo ""
    
    read -p "Reboot now? (Y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        print_status "Rebooting in 5 seconds... (Ctrl+C to cancel)"
        sleep 5
        sudo reboot
    else
        print_warning "Remember to reboot before using hardware features!"
    fi
}

# Run installation
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi