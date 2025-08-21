#!/bin/bash
# WALL-E System Setup Script for Raspberry Pi 5

set -e  # Exit on any error

echo "🤖 WALL-E System Setup Script"
echo "=============================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Check if running on Raspberry Pi
check_raspberry_pi() {
    if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        print_warning "Not running on Raspberry Pi - some features may not work"
    else
        print_status "Running on Raspberry Pi ✅"
    fi
}

# Update system packages
update_system() {
    print_step "Updating system packages..."
    sudo apt update
    sudo apt upgrade -y
    print_status "System updated ✅"
}


# Enable I2C and serial interfaces
enable_interfaces() {
    print_step "Enabling I2C and Serial interfaces..."
    
    # Enable I2C
    if ! grep -q "dtparam=i2c_arm=on" /boot/config.txt; then
        echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
        print_status "I2C enabled in /boot/config.txt"
    fi
    
    # Enable serial port
    if ! grep -q "enable_uart=1" /boot/config.txt; then
        echo "enable_uart=1" | sudo tee -a /boot/config.txt
        print_status "UART enabled in /boot/config.txt"
    fi
    
    # Add user to i2c group
    sudo usermod -a -G i2c $USER
    
    print_status "Interfaces configured ✅"
}


# Install audio system dependencies
install_audio_deps() {
    print_step "Installing audio system dependencies..."
    
    # Install audio packages
    sudo apt install -y \
        alsa-utils \
        pulseaudio \
        pulseaudio-utils \
        espeak \
        espeak-data \
        libespeak-dev \
        ffmpeg \
        sox \
        libsox-fmt-all
    
    # Configure audio output (use audio jack by default)
    sudo raspi-config nonint do_audio 1
    
    # Set reasonable volume
    amixer sset PCM,0 70% 2>/dev/null || \
    amixer sset Master 70% 2>/dev/null || \
    amixer sset Headphone 70% 2>/dev/null || \
    print_warning "Could not set default volume"
    
    print_status "Audio system dependencies installed ✅"
}

# Create directory structure
create_directories() {
    print_step "Creating directory structure..."
    
    mkdir -p config
    mkdir -p logs
    mkdir -p audio
    mkdir -p icons
    mkdir -p scenes
    
    print_status "Directory structure created ✅"
}

# Create systemd service files
create_services() {
    print_step "Creating systemd service files..."
    
    # WALL-E main service
    cat > walle.service << EOF
[Unit]
Description=WALL-E Robot Control System
After=network.target
Wants=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
Environment=PATH=$(pwd)/venv/bin
ExecStart=$(pwd)/venv/bin/python $(pwd)/walle_system_manager.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    # Install service
    sudo cp walle.service /etc/systemd/system/
    sudo systemctl daemon-reload
    
    print_status "Systemd service created ✅"
    print_status "To enable auto-start: sudo systemctl enable walle"
    print_status "To start service: sudo systemctl start walle"
}

# Set up udev rules for consistent device naming
setup_udev_rules() {
    print_step "Setting up udev rules for device naming..."
    
    cat > 99-walle-devices.rules << EOF
# WALL-E Device Rules
# Pololu Maestro controllers
SUBSYSTEM=="tty", ATTRS{idVendor}=="1ffb", ATTRS{idProduct}=="008a", SYMLINK+="maestro%n"

# USB-to-serial adapters
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="usb-serial%n"
EOF

    sudo cp 99-walle-devices.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    
    print_status "Udev rules installed ✅"
}

# Create startup script
create_startup_script() {
    print_step "Creating startup script..."
    
    cat > start_walle.sh << 'EOF'
#!/bin/bash
# WALL-E Startup Script

cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Start system manager
python walle_system_manager.py
EOF

    chmod +x start_walle.sh
    
    print_status "Startup script created ✅"
}

# Test hardware connections
test_hardware() {
    print_step "Testing hardware connections..."
    
    # Test I2C
    if command -v i2cdetect &> /dev/null; then
        print_status "I2C tools available, scanning for devices..."
        i2cdetect -y 1 || print_warning "No I2C devices found"
    fi
    
    # List USB devices
    print_status "USB devices:"
    lsusb | grep -E "(Pololu|FTDI|Arduino)" || print_warning "No known USB devices found"
    
    # List serial ports
    print_status "Serial ports:"
    ls -la /dev/tty{ACM,USB}* 2>/dev/null || print_warning "No serial devices found"
    
    print_status "Hardware test complete ✅"
}

# Main installation process
main() {
    print_status "Starting WALL-E system installation..."
    
    check_raspberry_pi
    update_system
    install_audio_deps
    enable_interfaces
    create_directories
    setup_udev_rules
    create_services
    create_startup_script
    test_hardware
    
    echo ""
    echo "🎉 WALL-E System Installation Complete!"
    echo "======================================"
    echo ""
    print_status "Native Audio System Ready!"
    echo "  📁 Audio files go in: ./audio/"
    echo "  🎵 Supported formats: MP3, WAV, OGG, M4A"
    echo "  🎤 Text-to-speech: espeak installed"
    echo "  🔊 Volume control: amixer/alsamixer"
    echo ""
    print_status "Next steps:"
    echo "  1. Reboot to enable I2C/Serial: sudo reboot"
    echo "  2. Connect your hardware:"
    echo "     - Maestro 1: USB port (will be /dev/ttyACM0)"
    echo "     - Maestro 2: USB port (will be /dev/ttyACM1)"
    echo "     - Audio: Built-in 3.5mm jack or HDMI"
    echo "     - Current sensors: I2C (SDA/SCL)"
    echo "     - Emergency stop: GPIO 22"
    echo "     - Limit switch: GPIO 18"
    echo "  3. Add audio files to ./audio/ directory"
    echo "  4. Test the system: ./start_walle.sh"
    echo "  5. Start frontend: python wall_e_frontend.py"
    echo ""
    print_status "Audio file naming:"
    echo "  - track_001.mp3, track_002.wav (numbered tracks)"
    echo "  - happy.mp3, sad.wav (named by emotion)"
    echo "  - System will auto-generate TTS examples"
    echo ""
    print_status "Service management:"
    echo "  - Start service: sudo systemctl start walle"
    echo "  - Enable auto-start: sudo systemctl enable walle"
    echo "  - View logs: sudo journalctl -u walle -f"
    echo ""
    print_status "Audio testing:"
    echo "  - Test speakers: speaker-test -t wav"
    echo "  - Test TTS: espeak 'Hello WALL-E'"
    echo "  - Volume control: alsamixer"
    echo ""
    print_warning "A reboot is required to enable I2C and Serial interfaces!"

    
    echo ""
    echo "🎉 WALL-E System Installation Complete!"
    echo "======================================"
    echo ""
    print_status "Next steps:"
    echo "  1. Reboot to enable I2C/Serial: sudo reboot"
    echo "  2. Connect your hardware:"
    echo "     - Maestro 1: USB port (will be /dev/ttyACM0)"
    echo "     - Maestro 2: USB port (will be /dev/ttyACM1)"
    echo "     - DFPlayer: GPIO 14/15 (UART)"
    echo "     - Current sensors: I2C (SDA/SCL)"
    echo "     - Emergency stop: GPIO 22"
    echo "     - Limit switch: GPIO 18"
    echo "  3. Test the system: ./start_walle.sh"
    echo "  4. Start frontend: python wall_e_frontend.py"
    echo ""
    print_status "Service management:"
    echo "  - Start service: sudo systemctl start walle"
    echo "  - Enable auto-start: sudo systemctl enable walle"
    echo "  - View logs: sudo journalctl -u walle -f"
    echo ""
    print_status "Configuration files created in config/ directory"
    print_status "Edit these files to match your specific hardware setup"
    echo ""
    print_warning "A reboot is required to enable I2C and Serial interfaces!"
}

# Run main installation if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi