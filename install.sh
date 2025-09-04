#!/bin/bash
# WALL-E Complete System Installer for Raspberry Pi 5
# Supports the new modular architecture with all dependencies
# Version: 3.0 - Updated for refactored backend

set -e  # Exit on any error

echo "🤖 WALL-E Complete System Installer v3.0"
echo "========================================"
echo "🎯 Target: Raspberry Pi 5 with Python 3.9.13"
echo "🏗️ Architecture: Modular Backend System"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
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

print_success() {
    echo -e "${PURPLE}[SUCCESS]${NC} $1"
}

# Check if running on Raspberry Pi
check_raspberry_pi() {
    print_step "Checking system compatibility..."
    
    if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        print_warning "Not running on Raspberry Pi - some features may not work"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        PI_MODEL=$(grep "Raspberry Pi" /proc/cpuinfo | head -1 | cut -d: -f2 | xargs)
        print_success "Detected: $PI_MODEL"
        
        # Check for Pi 5 specifically
        if echo "$PI_MODEL" | grep -q "Raspberry Pi 5"; then
            print_success "✅ Raspberry Pi 5 detected - optimal compatibility"
        else
            print_warning "⚠️ Not Pi 5 - some GPIO features may differ"
        fi
    fi
}

# Update system packages
update_system() {
    print_step "Updating system packages..."
    sudo apt update
    sudo apt upgrade -y
    print_success "System packages updated"
}

# Install system dependencies
install_system_deps() {
    print_step "Installing system dependencies..."
    
    # Core development tools
    sudo apt install -y \
        build-essential \
        cmake \
        pkg-config \
        git \
        curl \
        wget \
        vim \
        htop \
        screen \
        tmux
    
    # Python development
    sudo apt install -y \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        python3-setuptools \
        python3-wheel
    
    # I2C and serial tools
    sudo apt install -y \
        i2c-tools \
        python3-smbus \
        minicom
    
    # GPIO libraries for Pi 5
    sudo apt install -y \
        python3-rpi.gpio \
        python3-gpiozero \
        python3-lgpio \
        lgpio-tools
    
    # Audio system
    sudo apt install -y \
        alsa-utils \
        pulseaudio \
        pulseaudio-utils \
        portaudio19-dev \
        libasound2-dev \
        espeak \
        espeak-data \
        libespeak-dev \
        ffmpeg \
        sox \
        libsox-fmt-all
    
    # Computer vision and image processing
    sudo apt install -y \
        libopencv-dev \
        python3-opencv \
        libatlas-base-dev \
        libhdf5-dev \
        libhdf5-serial-dev \
        libhdf5-103 \
        libqtgui4 \
        libqtwebkit4 \
        libqt4-test \
        python3-pyqt5 \
        libarmadillo-dev \
        libblas-dev \
        liblapack-dev \
        gfortran \
        libfreetype6-dev \
        python3-h5py
    
    # Network and file sharing
    sudo apt install -y \
        samba \
        samba-common-bin \
        cifs-utils
    
    # File watching (for config hot-reload)
    sudo apt install -y \
        python3-watchdog \
        inotify-tools
    
    # Web server components
    sudo apt install -y \
        python3-flask \
        nginx
    
    print_success "System dependencies installed"
}

# Install and setup pyenv with Python 3.9.13
setup_pyenv() {
    print_step "Setting up pyenv with Python 3.9.13..."
    
    # Check if pyenv is already installed
    if command -v pyenv >/dev/null 2>&1; then
        print_status "pyenv already installed"
    else
        print_status "Installing pyenv..."
        
        # Install pyenv dependencies
        sudo apt install -y \
            make \
            build-essential \
            libssl-dev \
            zlib1g-dev \
            libbz2-dev \
            libreadline-dev \
            libsqlite3-dev \
            wget \
            curl \
            llvm \
            libncursesw5-dev \
            xz-utils \
            tk-dev \
            libxml2-dev \
            libxmlsec1-dev \
            libffi-dev \
            liblzma-dev \
            libgdbm-dev \
            libnss3-dev
        
        # Install pyenv
        curl https://pyenv.run | bash
        
        # Add pyenv to shell configuration
        SHELL_CONFIG="$HOME/.bashrc"
        
        # Add pyenv initialization if not already present
        if ! grep -q 'export PYENV_ROOT' "$SHELL_CONFIG" 2>/dev/null; then
            {
                echo ''
                echo '# pyenv configuration'
                echo 'export PYENV_ROOT="$HOME/.pyenv"'
                echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"'
                echo 'eval "$(pyenv init -)"'
                echo 'eval "$(pyenv virtualenv-init -)"'
            } >> "$SHELL_CONFIG"
            print_success "Added pyenv to $SHELL_CONFIG"
        fi
        
        # Initialize pyenv for current session
        export PYENV_ROOT="$HOME/.pyenv"
        command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init -)"
        eval "$(pyenv virtualenv-init -)"
        
        print_success "pyenv installed successfully"
    fi
    
    # Initialize pyenv for current session
    export PYENV_ROOT="$HOME/.pyenv"
    command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"
    eval "$(pyenv virtualenv-init -)"
    
    # Install Python 3.9.13 if not present
    if pyenv versions --bare | grep -q "3.9.13"; then
        print_status "Python 3.9.13 already installed"
    else
        print_status "Installing Python 3.9.13 (this will take several minutes)..."
        
        # Set environment variables for better compilation on Pi 5
        export PYTHON_CONFIGURE_OPTS="--enable-optimizations"
        export CFLAGS="-O2"
        
        pyenv install 3.9.13
        print_success "Python 3.9.13 installed successfully"
    fi
    
    # Set Python 3.9.13 as local version
    pyenv local 3.9.13
    print_success "Set Python 3.9.13 as local version"
    
    # Verify Python version
    PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    if [[ "$PYTHON_VERSION" == "3.9.13" ]]; then
        print_success "✅ Confirmed Python version: $PYTHON_VERSION"
    else
        print_error "❌ Expected Python 3.9.13, got: $PYTHON_VERSION"
        print_warning "You may need to restart your shell"
    fi
}

# Create Python virtual environment
create_venv() {
    print_step "Creating Python virtual environment..."
    
    # Verify Python version
    PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    if [[ "$PYTHON_VERSION" != "3.9.13" ]]; then
        print_error "Python 3.9.13 required, found: $PYTHON_VERSION"
        print_warning "Run: source ~/.bashrc && cd $(pwd)"
        exit 1
    fi
    
    # Remove existing venv if present
    if [ -d "venv" ]; then
        print_warning "Removing existing virtual environment..."
        rm -rf venv
    fi
    
    # Create new virtual environment
    python -m venv venv
    source venv/bin/activate
    
    # Verify venv Python version
    VENV_PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    print_success "Virtual environment Python: $VENV_PYTHON_VERSION"
    
    # Upgrade pip and tools
    pip install --upgrade pip setuptools wheel
    
    print_success "Virtual environment created successfully"
}

# Install Python dependencies for modular backend
install_python_deps() {
    print_step "Installing Python dependencies for modular backend..."
    
    source venv/bin/activate
    
    # Core backend dependencies
    print_status "Installing core backend packages..."
    pip install \
        asyncio \
        websockets>=10.0 \
        pyserial>=3.5 \
        psutil>=5.8.0 \
        pygame>=2.1.0 \
        requests>=2.25.0 \
        flask>=2.0.0 \
        numpy>=1.21.6
    
    # Hardware control packages
    print_status "Installing hardware control packages..."
    pip install \
        RPi.GPIO \
        adafruit-circuitpython-ads1x15 \
        adafruit-blinka \
        lgpio
    
    # New modular backend dependencies
    print_status "Installing modular backend packages..."
    pip install \
        watchdog>=2.1.0 \
        jsonschema>=3.2.0 \
        dataclasses-json>=0.5.0 \
        python-dateutil>=2.8.0
    
    # Computer vision (try optimized version first)
    print_status "Installing computer vision packages..."
    if ! pip install opencv-python==4.5.5.64 --no-cache-dir; then
        print_warning "Standard OpenCV install failed, using system version"
        # Create symlink to system OpenCV
        SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
        if [ -f /usr/lib/python3/dist-packages/cv2/python-*/cv2*.so ]; then
            ln -sf /usr/lib/python3/dist-packages/cv2 "$SITE_PACKAGES/"
            print_success "Linked system OpenCV to virtual environment"
        fi
    fi
    
    # Audio processing
    print_status "Installing audio processing packages..."
    pip install \
        pyaudio \
        wave \
        mutagen
    
    print_success "Python dependencies installed successfully"
}

# Configure Raspberry Pi interfaces
configure_pi_interfaces() {
    print_step "Configuring Raspberry Pi interfaces..."
    
    # Check if we need to configure interfaces
    CONFIG_FILE="/boot/firmware/config.txt"
    if [ ! -f "$CONFIG_FILE" ]; then
        CONFIG_FILE="/boot/config.txt"
    fi
    
    if [ ! -f "$CONFIG_FILE" ]; then
        print_error "Cannot find Pi config file"
        return 1
    fi
    
    # Backup config file
    sudo cp "$CONFIG_FILE" "${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    print_status "Backed up config file"
    
    # Enable I2C
    if ! grep -q "dtparam=i2c_arm=on" "$CONFIG_FILE"; then
        echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_FILE" > /dev/null
        print_status "Enabled I2C interface"
    fi
    
    # Enable SPI  
    if ! grep -q "dtparam=spi=on" "$CONFIG_FILE"; then
        echo "dtparam=spi=on" | sudo tee -a "$CONFIG_FILE" > /dev/null
        print_status "Enabled SPI interface"
    fi
    
    # Enable UART
    if ! grep -q "enable_uart=1" "$CONFIG_FILE"; then
        echo "enable_uart=1" | sudo tee -a "$CONFIG_FILE" > /dev/null
        print_status "Enabled UART interface"
    fi
    
    # Disable Bluetooth on primary UART (for serial communication)
    if ! grep -q "dtoverlay=disable-bt" "$CONFIG_FILE"; then
        echo "dtoverlay=disable-bt" | sudo tee -a "$CONFIG_FILE" > /dev/null
        print_status "Disabled Bluetooth on primary UART"
    fi
    
    # Add user to required groups
    sudo usermod -a -G i2c,spi,gpio,audio,dialout,video "$USER"
    print_status "Added user to hardware access groups"
    
    print_success "Pi interfaces configured"
}

# Manual configuration prompt
prompt_manual_config() {
    print_step "Manual Configuration Required"
    echo ""
    print_warning "⚠️  IMPORTANT: Manual configuration via raspi-config needed"
    echo ""
    echo "Please run the following command and configure these options:"
    echo ""
    echo "  ${BLUE}sudo raspi-config${NC}"
    echo ""
    echo "Navigate to:"
    echo "  📋 3 Interface Options → I1 SSH → Enable (for remote access)"
    echo "  📋 3 Interface Options → I5 I2C → Enable (for sensors)"  
    echo "  📋 3 Interface Options → I6 SPI → Enable (if needed)"
    echo "  📋 3 Interface Options → I8 Remote GPIO → Enable (optional)"
    echo "  📋 1 System Options → S5 Boot/Auto Login → Console Autologin"
    echo ""
    echo "After making changes, raspi-config will ask to reboot."
    echo "Choose 'Yes' to reboot now, or 'No' to reboot later."
    echo ""
    read -p "Press Enter when you've completed raspi-config setup..."
}

# Create directory structure
create_directories() {
    print_step "Creating directory structure..."
    
    # Main directories
    mkdir -p configs
    mkdir -p modules  
    mkdir -p logs
    mkdir -p audio
    mkdir -p icons
    mkdir -p scenes
    mkdir -p backups
    
    # Config subdirectories
    mkdir -p configs/backups
    
    print_success "Directory structure created"
}

# Create configuration files with current architecture
create_config_files() {
    print_step "Creating configuration files for modular backend..."
    
    # Hardware configuration (matches your current system)
    cat > configs/hardware_config.json << 'EOF'
{
    "hardware": {
        "maestro1": {
            "port": "/dev/ttyAMA0",
            "baud_rate": 9600,
            "device_number": 12,
            "description": "Primary Maestro for head and upper body servos"
        },
        "maestro2": {
            "port": "/dev/ttyAMA0", 
            "baud_rate": 9600,
            "device_number": 13,
            "description": "Secondary Maestro for arm and body servos"
        },
        "sabertooth": {
            "port": "/dev/ttyAMA1",
            "baud_rate": 9600,
            "description": "Sabertooth 2x60 motor controller (future use)"
        },
        "gpio": {
            "motor_step_pin": 16,
            "motor_dir_pin": 12,
            "motor_enable_pin": 13,
            "limit_switch_pin": 26,
            "emergency_stop_pin": 25,
            "description": "GPIO pin assignments for stepper motor and safety"
        },
        "timing": {
            "telemetry_interval": 0.2,
            "servo_update_rate": 0.02,
            "description": "System timing intervals in seconds"
        },
        "audio": {
            "directory": "audio",
            "volume": 0.7,
            "description": "Audio system configuration"
        }
    }
}
EOF

    # Camera configuration (matches your current ESP32 setup)
    cat > configs/camera_config.json << 'EOF'
{
    "esp32_ip": "10.1.1.203",
    "esp32_http_port": 81,
    "esp32_ws_port": 82,
    "esp32_url": "http://10.1.1.203:81/stream",
    "rebroadcast_port": 8081,
    "enable_stats": true,
    "connection_timeout": 10,
    "max_connection_errors": 10,
    "frame_quality": 80,
    "ws_reconnect_delay": 5,
    "http_reconnect_delay": 3,
    "auto_start_stream": false,
    "description": "ESP32-CAM configuration with manual stream control"
}
EOF

    # Copy existing scenes config if present, otherwise create default
    if [ -f "scenes_config.json" ]; then
        cp scenes_config.json configs/scenes_config.json
        print_status "Copied existing scenes configuration"
    else
        # Create minimal scenes config (your existing scenes will be preserved)
        cat > configs/scenes_config.json << 'EOF'
[
    {
        "label": "Happy Greeting",
        "emoji": "😊",
        "categories": ["Happy", "Greeting"],
        "duration": 3.0,
        "audio_enabled": true,
        "audio_file": "Audio-clip-_CILW-2022_-Greetings.mp3",
        "script_enabled": true,
        "script_name": 1,
        "delay": 0
    },
    {
        "label": "Wave Response", 
        "emoji": "👋",
        "categories": ["Gesture", "Response"],
        "duration": 3.0,
        "audio_enabled": true,
        "audio_file": "Audio-clip-_CILW-2022_-Greetings.mp3",
        "script_enabled": true,
        "script_name": 3,
        "delay": 0
    }
]
EOF
    fi

    print_success "Configuration files created"
}

# Create sample audio files
create_sample_audio() {
    print_step "Creating sample audio files..."
    
    # Test if espeak works
    if command -v espeak >/dev/null 2>&1; then
        # Create sample TTS files with better settings for Pi 5
        espeak "Hello, I am WALL-E! My modular backend is ready!" -w audio/walle_greeting.wav -s 120 -p 40 2>/dev/null || true
        espeak "Systems online. All modules loaded successfully." -w audio/system_ready.wav -s 110 -p 50 2>/dev/null || true
        espeak "Wave detected! Hello there!" -w audio/wave_response.wav -s 130 -p 60 2>/dev/null || true
        espeak "Emergency stop activated." -w audio/emergency_stop.wav -s 90 -p 30 2>/dev/null || true
        
        print_success "Sample TTS audio files created"
    else
        print_warning "espeak not available - skipping TTS audio creation"
    fi
    
    # Create audio directory README
    cat > audio/README.md << 'EOF'
# 🎵 WALL-E Audio Files

## Supported Formats
- MP3, WAV, OGG, M4A, FLAC

## File Naming
- Use descriptive names: `happy_greeting.mp3`
- Scenes reference files by name (without extension)
- Files are scanned automatically on startup

## Adding Files
1. Copy audio files to this directory
2. Files appear automatically in frontend
3. Reference in scenes using filename without extension

## Network Upload
Access via SMB share: `\\HOSTNAME\walle-audio`

## Generated Files
- `walle_greeting.wav` - System startup greeting
- `system_ready.wav` - Backend ready notification  
- `wave_response.wav` - Gesture response
- `emergency_stop.wav` - Safety alert
EOF

    print_success "Audio system configured"
}

# Setup SMB file sharing (simplified)
setup_smb_sharing() {
    print_step "Setting up SMB file sharing..."
    
    WALLE_DIR=$(pwd)
    CURRENT_USER=$(whoami)
    
    # Backup existing smb.conf
    if [ -f /etc/samba/smb.conf ]; then
        sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup.$(date +%Y%m%d) 2>/dev/null || true
    fi
    
    # Create SMB configuration
    cat > walle_smb_shares.conf << EOF
# WALL-E Modular Backend Shares
[walle]
    comment = WALL-E Robot Project (Modular Backend)
    path = $WALLE_DIR
    browseable = yes
    read only = no
    guest ok = yes
    create mask = 0664
    directory mask = 0775
    force user = $CURRENT_USER
    public = yes
    writable = yes
    
[walle-audio]
    comment = WALL-E Audio Files
    path = $WALLE_DIR/audio
    browseable = yes
    read only = no
    guest ok = yes
    create mask = 0664
    directory mask = 0775
    force user = $CURRENT_USER
    public = yes
    writable = yes

[walle-configs]
    comment = WALL-E Configuration Files
    path = $WALLE_DIR/configs
    browseable = yes
    read only = no
    guest ok = yes
    create mask = 0664
    directory mask = 0775
    force user = $CURRENT_USER
    public = yes
    writable = yes

[walle-logs]
    comment = WALL-E Log Files
    path = $WALLE_DIR/logs
    browseable = yes
    read only = yes
    guest ok = yes
    force user = $CURRENT_USER
    public = yes
EOF

    # Append to system smb.conf
    sudo tee -a /etc/samba/smb.conf < walle_smb_shares.conf > /dev/null
    
    # Set permissions
    chmod 775 "$WALLE_DIR"
    chmod 775 "$WALLE_DIR"/audio 2>/dev/null || true
    chmod 775 "$WALLE_DIR"/configs 2>/dev/null || true
    chmod 775 "$WALLE_DIR"/logs 2>/dev/null || true
    
    # Add user to samba group
    sudo usermod -a -G sambashare "$CURRENT_USER" 2>/dev/null || true
    
    # Start samba services
    sudo systemctl enable smbd nmbd
    sudo systemctl restart smbd nmbd
    
    # Get network info
    HOSTNAME=$(hostname)
    IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "IP_NOT_FOUND")
    
    print_success "SMB file sharing configured"
    echo ""
    echo "📁 Network Access:"
    echo "  🖥️  Windows: \\\\$HOSTNAME\\walle"
    echo "  🍎 macOS: smb://$HOSTNAME/walle" 
    echo "  🐧 Linux: smb://$IP_ADDRESS/walle"
    echo ""
    echo "🎵 Quick Access:"
    echo "  Audio: \\\\$HOSTNAME\\walle-audio"
    echo "  Config: \\\\$HOSTNAME\\walle-configs"
    echo "  Logs: \\\\$HOSTNAME\\walle-logs"
    echo ""
    
    # Cleanup
    rm -f walle_smb_shares.conf
}

# Test hardware connections
test_hardware() {
    print_step "Testing hardware connections..."
    
    # Test I2C
    if command -v i2cdetect >/dev/null 2>&1; then
        print_status "Scanning I2C devices..."
        i2cdetect -y 1 2>/dev/null | grep -E "[0-9a-f]{2}" && print_success "I2C devices detected" || print_warning "No I2C devices found"
    fi
    
    # Test GPIO access
    if command -v pinout >/dev/null 2>&1; then
        print_status "GPIO system available"
    else
        print_warning "GPIO tools not found"
    fi
    
    # List USB devices
    print_status "USB devices:"
    lsusb | grep -E "(Pololu|FTDI|Arduino|Silicon Labs)" || print_warning "No known USB devices found"
    
    # Test audio system
    print_status "Testing audio system..."
    if command -v aplay >/dev/null 2>&1; then
        # Set reasonable volume levels
        amixer sset PCM,0 70% 2>/dev/null || \
        amixer sset Master 70% 2>/dev/null || \
        amixer sset Headphone 70% 2>/dev/null || true
        print_success "Audio system configured"
    fi
    
    print_success "Hardware test completed"
}

# Test Python installation
test_python_installation() {
    print_step "Testing Python 3.9.13 installation..."
    
    source venv/bin/activate
    
    # Test Python version
    PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    if [[ "$PYTHON_VERSION" == "3.9.13" ]]; then
        print_success "✅ Python 3.9.13 confirmed"
    else
        print_warning "⚠️ Expected 3.9.13, got: $PYTHON_VERSION"
    fi
    
    # Test critical imports for modular backend
    python -c "
import asyncio
import websockets  
import serial
import pygame
import flask
import watchdog
print('✅ Core backend modules working')
"
    
    # Test hardware-specific imports
    python -c "
try:
    import cv2
    print('✅ OpenCV working')
except ImportError:
    print('⚠️ OpenCV not available')

try:
    import RPi.GPIO
    print('✅ RPi.GPIO working')
except ImportError:
    print('⚠️ RPi.GPIO not available')

try:
    import adafruit_ads1x15.ads1115
    print('✅ ADC libraries working')
except ImportError:
    print('⚠️ ADC libraries not available')
"
    
    print_success "Python installation test completed"
}

# Create service files
create_services() {
    print_step "Creating systemd services..."
    
    # Main WALL-E service
    cat > walle-backend.service << EOF
[Unit]
Description=WALL-E Modular Backend System
After=network.target sound.target
Wants=network.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$(pwd)
Environment=PATH=$(pwd)/venv/bin:/home/$USER/.pyenv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=PYENV_ROOT=/home/$USER/.pyenv
Environment=PYTHONPATH=$(pwd)
ExecStartPre=/bin/bash -c 'source $(pwd)/venv/bin/activate && python --version'
ExecStart=$(pwd)/venv/bin/python $(pwd)/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

    # Install service
    sudo cp walle-backend.service /etc/systemd/system/
    sudo systemctl daemon-reload
    
    print_success "Systemd service created"
    print_status "Service: walle-backend.service"
    
    # Clean up temp file
    rm -f walle-backend.service
}

# Create startup and management scripts  
create_scripts() {
    print_step "Creating management scripts..."
    
    # Enhanced startup script
    cat > start_walle.sh << 'EOF'
#!/bin/bash
# WALL-E Modular Backend Startup Script
cd "$(dirname "$0")"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

echo "🤖 Starting WALL-E Modular Backend System..."

# Initialize pyenv
if command -v pyenv >/dev/null 2>&1; then
    eval "$(pyenv init -)"
fi

# Check Python version
PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
if [[ "$PYTHON_VERSION" != "3.9.13" ]]; then
    print_warning "Expected Python 3.9.13, found: $PYTHON_VERSION"
    print_warning "Try: source ~/.bashrc && cd $(pwd)"
fi

# Check virtual environment
if [ ! -d "venv" ]; then
    print_error "Virtual environment not found. Run install.sh first."
    exit 1
fi

# Activate virtual environment
source venv/bin/activate
VENV_PYTHON=$(python --version 2>&1 | cut -d' ' -f2)
print_status "Using Python $VENV_PYTHON in virtual environment"

# Parse command line arguments
START_CAMERA=true
DAEMON_MODE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-camera) START_CAMERA=false; shift ;;
        --daemon) DAEMON_MODE=true; shift ;;
        --help|-h)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --no-camera    Start without camera proxy"
            echo "  --daemon       Run as background daemon"
            echo "  --help, -h     Show help"
            exit 0 ;;
        *) print_error "Unknown option: $1"; exit 1 ;;
    esac
done

# Function to start camera proxy
start_camera_proxy() {
    if [ ! -f "modules/camera_proxy.py" ]; then
        print_warning "Camera proxy not found"
        return 1
    fi
    
    print_step "Starting camera proxy..."
    python modules/camera_proxy.py &
    CAMERA_PID=$!
    echo $CAMERA_PID > camera_proxy.pid
    sleep 2
    
    if kill -0 $CAMERA_PID 2>/dev/null; then
        IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")
        print_status "📷 Camera stream: http://$IP:8081/stream"
        return 0
    else
        print_error "Camera proxy failed to start"
        return 1
    fi
}

# Cleanup function
cleanup() {
    print_step "Shutting down WALL-E services..."
    if [ -f "camera_proxy.pid" ]; then
        CAMERA_PID=$(cat camera_proxy.pid)
        if kill -0 $CAMERA_PID 2>/dev/null; then
            kill $CAMERA_PID
            wait $CAMERA_PID 2>/dev/null
        fi
        rm -f camera_proxy.pid
    fi
    print_status "✅ Cleanup complete"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start camera proxy if enabled
if [ "$START_CAMERA" = true ]; then
    start_camera_proxy
fi

# Display network information
HOSTNAME=$(hostname)
IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")

echo ""
print_step "WALL-E Modular Backend Status:"
echo "  🤖 Core System: Starting..."
echo "  📷 Camera: ${START_CAMERA:+✅ Enabled}${START_CAMERA:-❌ Disabled}"
echo ""
echo "🌐 Network Access:"
echo "  📡 WebSocket: ws://$IP:8766"
echo "  📁 File Share: \\\\$HOSTNAME\\walle"
if [ "$START_CAMERA" = true ]; then
    echo "  📷 Camera: http://$IP:8081/stream"
fi
echo ""

# Start main backend
if [ "$DAEMON_MODE" = true ]; then
    print_status "Starting in daemon mode..."
    nohup python main.py > logs/walle_daemon.log 2>&1 &
    echo $! > walle_backend.pid
    print_status "WALL-E backend started (PID: $(cat walle_backend.pid))"
    print_status "Logs: tail -f logs/walle_daemon.log"
else
    print_status "Starting in interactive mode (Ctrl+C to stop)..."
    python main.py
fi
EOF

    # Management script
    cat > manage_walle.sh << 'EOF'
#!/bin/bash
# WALL-E System Management Script

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

show_status() {
    echo "🤖 WALL-E System Status"
    echo "======================"
    
    # Check if backend is running
    if [ -f "walle_backend.pid" ]; then
        PID=$(cat walle_backend.pid)
        if kill -0 $PID 2>/dev/null; then
            print_status "✅ Backend running (PID: $PID)"
        else
            print_warning "❌ Backend PID file exists but process not running"
            rm -f walle_backend.pid
        fi
    else
        print_warning "❌ Backend not running"
    fi
    
    # Check camera proxy
    if [ -f "camera_proxy.pid" ]; then
        CAMERA_PID=$(cat camera_proxy.pid)
        if kill -0 $CAMERA_PID 2>/dev/null; then
            print_status "✅ Camera proxy running (PID: $CAMERA_PID)"
        else
            print_warning "❌ Camera proxy PID file exists but process not running"
            rm -f camera_proxy.pid
        fi
    else
        print_warning "❌ Camera proxy not running"
    fi
    
    # Check service status
    if systemctl is-active --quiet walle-backend; then
        print_status "✅ Systemd service: Active"
    else
        print_warning "❌ Systemd service: Inactive"
    fi
    
    # Network info
    HOSTNAME=$(hostname)
    IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "unknown")
    echo ""
    echo "🌐 Network Access:"
    echo "  📡 WebSocket: ws://$IP:8766"
    echo "  📁 SMB Share: \\\\$HOSTNAME\\walle"
    echo "  📷 Camera: http://$IP:8081/stream"
}

start_service() {
    print_step "Starting WALL-E service..."
    sudo systemctl start walle-backend
    sleep 2
    show_status
}

stop_service() {
    print_step "Stopping WALL-E service..."
    sudo systemctl stop walle-backend
    
    # Also stop any running processes
    if [ -f "walle_backend.pid" ]; then
        PID=$(cat walle_backend.pid)
        if kill -0 $PID 2>/dev/null; then
            kill $PID
            wait $PID 2>/dev/null
        fi
        rm -f walle_backend.pid
    fi
    
    if [ -f "camera_proxy.pid" ]; then
        CAMERA_PID=$(cat camera_proxy.pid)
        if kill -0 $CAMERA_PID 2>/dev/null; then
            kill $CAMERA_PID
            wait $CAMERA_PID 2>/dev/null
        fi
        rm -f camera_proxy.pid
    fi
    
    show_status
}

restart_service() {
    print_step "Restarting WALL-E service..."
    stop_service
    sleep 2
    start_service
}

enable_service() {
    print_step "Enabling WALL-E service for auto-start..."
    sudo systemctl enable walle-backend
    print_status "✅ Service will start automatically on boot"
}

disable_service() {
    print_step "Disabling WALL-E auto-start..."
    sudo systemctl disable walle-backend
    print_status "✅ Service will not start automatically on boot"
}

show_logs() {
    if [ "$2" = "follow" ] || [ "$2" = "-f" ]; then
        print_status "Following WALL-E logs (Ctrl+C to exit)..."
        sudo journalctl -u walle-backend -f
    else
        print_status "Recent WALL-E logs:"
        sudo journalctl -u walle-backend --no-pager -n 50
    fi
}

case "${1:-status}" in
    "status"|"st") show_status ;;
    "start") start_service ;;
    "stop") stop_service ;;
    "restart"|"rs") restart_service ;;
    "enable") enable_service ;;
    "disable") disable_service ;;
    "logs") show_logs "$@" ;;
    *) 
        echo "Usage: $0 {status|start|stop|restart|enable|disable|logs [follow]}"
        echo ""
        echo "Commands:"
        echo "  status    - Show system status"
        echo "  start     - Start WALL-E service"
        echo "  stop      - Stop WALL-E service"
        echo "  restart   - Restart WALL-E service"
        echo "  enable    - Enable auto-start on boot"
        echo "  disable   - Disable auto-start"
        echo "  logs      - Show recent logs"
        echo "  logs -f   - Follow logs in real-time"
        ;;
esac
EOF

    # SMB management script (simplified)
    cat > manage_smb.sh << 'EOF'
#!/bin/bash
# WALL-E SMB Management Script

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

show_status() {
    echo "📁 WALL-E SMB File Sharing Status"
    echo "================================="
    
    HOSTNAME=$(hostname)
    IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "unknown")
    
    echo "🌐 Access URLs:"
    echo "  🖥️  Windows: \\\\$HOSTNAME\\walle"
    echo "  🍎 macOS: smb://$HOSTNAME/walle"
    echo "  🐧 Linux: smb://$IP/walle"
    echo ""
    echo "📂 Quick Access:"
    echo "  🎵 Audio: \\\\$HOSTNAME\\walle-audio"
    echo "  ⚙️  Config: \\\\$HOSTNAME\\walle-configs"
    echo "  📊 Logs: \\\\$HOSTNAME\\walle-logs"
    echo ""
    
    if systemctl is-active --quiet smbd; then
        print_status "✅ SMB service running"
    else
        print_status "❌ SMB service not running"
        echo "  Run: sudo systemctl start smbd"
    fi
}

restart_smb() {
    print_step "Restarting SMB services..."
    sudo systemctl restart smbd nmbd
    sleep 1
    show_status
}

case "${1:-status}" in
    "status") show_status ;;
    "restart") restart_smb; show_status ;;
    *) echo "Usage: $0 {status|restart}" ;;
esac
EOF

    # Make scripts executable
    chmod +x start_walle.sh manage_walle.sh manage_smb.sh
    
    print_success "Management scripts created"
    echo "  📝 start_walle.sh - Start WALL-E system"
    echo "  📝 manage_walle.sh - Service management"
    echo "  📝 manage_smb.sh - SMB file sharing"
}

# Create documentation
create_documentation() {
    print_step "Creating system documentation..."
    
    cat > WALL-E_SETUP.md << 'EOF'
# 🤖 WALL-E Modular Backend System

## Quick Start

### Start WALL-E System
```bash
./start_walle.sh                 # Interactive mode
./start_walle.sh --daemon        # Background mode
./start_walle.sh --no-camera     # Without camera
```

### Service Management
```bash
./manage_walle.sh status         # Check status
./manage_walle.sh start          # Start service
./manage_walle.sh stop           # Stop service  
./manage_walle.sh restart        # Restart service
./manage_walle.sh logs -f        # View live logs
```

### File Access
- **Windows**: `\\HOSTNAME\walle`
- **macOS/Linux**: `smb://HOSTNAME/walle`
- **Audio Upload**: `\\HOSTNAME\walle-audio`
- **Config Edit**: `\\HOSTNAME\walle-configs`

## System Architecture

### Modular Components
- `main.py` - System orchestrator (400 lines)
- `modules/websocket_handler.py` - Message routing
- `modules/hardware_service.py` - Hardware abstraction
- `modules/scene_engine.py` - Scene management
- `modules/audio_controller.py` - Audio system
- `modules/telemetry_system.py` - Monitoring
- `modules/config_manager.py` - Configuration

### Hardware Support
- **Maestro Controllers**: Dual 18-channel servo control
- **Stepper Motor**: NEMA 23 with homing
- **ADC Sensors**: Battery and current monitoring
- **ESP32-CAM**: Video streaming with manual control
- **Audio System**: Native Pi audio with multiple formats

### Configuration
- **Hot-Reload**: Configuration changes apply automatically
- **Validation**: All configs validated with schemas
- **Backups**: Automatic config backups on changes
- **Network Access**: Edit configs via SMB shares

## Troubleshooting

### Python Issues
```bash
# Check Python version
python --version  # Should be 3.9.13

# Restart shell if wrong version
source ~/.bashrc && cd $(pwd)

# Recreate virtual environment
rm -rf venv
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Hardware Issues
```bash
# Test I2C devices
i2cdetect -y 1

# Check serial ports
ls -la /dev/tty*

# Test GPIO access
pinout

# Check system logs
sudo journalctl -u walle-backend -f
```

### Network Issues
```bash
# Check SMB shares
./manage_smb.sh status

# Restart networking
sudo systemctl restart networking

# Check IP address
hostname -I
```

## Performance Monitoring

### System Health
- Access WebSocket: `ws://IP:8766`
- Camera stream: `http://IP:8081/stream`
- Log files: `logs/walle_backend_refactored.log`

### Telemetry Features
- Real-time system monitoring
- Battery and current sensing
- Hardware status tracking
- Automatic alerting system
- Health score calculation

## Development

### Adding New Features
1. Create new module in `modules/`
2. Register with hardware service
3. Add WebSocket handlers if needed
4. Update configuration schema
5. Test with modular architecture

### Configuration Schema
All configs are validated automatically:
- Hardware settings checked for valid ranges
- GPIO pins validated for Pi compatibility  
- Network settings verified
- Scene formats validated

## Support Files

- `requirements.txt` - Python dependencies
- `systemd service` - Auto-start configuration
- `SMB shares` - Network file access
- `Log rotation` - Automatic log management

For detailed API documentation, see the WebSocket message formats in the main documentation.
EOF

    print_success "Documentation created: WALL-E_SETUP.md"
}

# Main installation function
main() {
    print_status "🤖 Starting WALL-E Complete Installation"
    echo "Current directory: $(pwd)"
    echo "Current user: $(whoami)"
    echo ""
    
    # Pre-flight checks
    check_raspberry_pi
    
    # System setup
    update_system
    install_system_deps
    
    # Python environment
    setup_pyenv
    create_venv
    install_python_deps
    
    # Pi configuration
    configure_pi_interfaces
    prompt_manual_config
    
    # Directory and file setup
    create_directories
    create_config_files
    create_sample_audio
    
    # Services and networking
    setup_smb_sharing
    create_services
    create_scripts
    create_documentation
    
    # Testing
    test_hardware
    test_python_installation
    
    echo ""
    echo "🎉 WALL-E Modular Backend Installation Complete!"
    echo "================================================="
    echo ""
    print_success "✅ System Components Installed:"
    echo "  🐍 Python 3.9.13 with pyenv"
    echo "  📦 All modular backend dependencies"
    echo "  🔧 Hardware interfaces configured"
    echo "  📁 SMB file sharing enabled"
    echo "  🎵 Audio system ready"
    echo "  ⚙️  Configuration management"
    echo "  📊 Advanced telemetry system"
    echo "  🌐 WebSocket server ready"
    echo ""
    print_success "✅ Network Access Ready:"
    HOSTNAME=$(hostname)
    IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "IP_NOT_FOUND")
    echo "  📡 WebSocket: ws://$IP_ADDRESS:8766"
    echo "  📁 File Access: \\\\$HOSTNAME\\walle"
    echo "  🎵 Audio Upload: \\\\$HOSTNAME\\walle-audio"
    echo "  ⚙️  Config Edit: \\\\$HOSTNAME\\walle-configs"
    echo ""
    print_success "✅ Management Commands:"
    echo "  🚀 Start System: ./start_walle.sh"
    echo "  ⚙️  Manage Service: ./manage_walle.sh status"
    echo "  📁 SMB Status: ./manage_smb.sh status"
    echo "  📖 Documentation: cat WALL-E_SETUP.md"
    echo ""
    print_step "⚠️  IMPORTANT: Reboot Required"
    echo "  Hardware interfaces (I2C, SPI, UART) need reboot to activate"
    echo "  After reboot, your modular WALL-E backend will be ready!"
    echo ""
    print_step "🎯 Next Steps:"
    echo "  1. 🔄 Reboot Pi: sudo reboot"
    echo "  2. 📁 Upload audio files via \\\\$HOSTNAME\\walle-audio"
    echo "  3. 🎭 Configure scenes via \\\\$HOSTNAME\\walle-configs"
    echo "  4. 🚀 Start backend: ./start_walle.sh"
    echo "  5. 🌐 Connect frontend to ws://$IP_ADDRESS:8766"
    echo ""
    print_success "🤖 Your WALL-E Modular Backend System is Ready!"
    echo ""
    read -p "Reboot now to activate hardware interfaces? (Y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        print_status "Rebooting in 5 seconds... (Ctrl+C to cancel)"
        sleep 5
        sudo reboot
    else
        print_warning "Remember to reboot before using hardware features!"
    fi
}

# Run main installation if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi