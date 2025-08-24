#!/bin/bash
# WALL-E System Setup Script for Raspberry Pi 5 - Updated Version with Python 3.9.13

set -e  # Exit on any error

echo "🤖 WALL-E System Setup Script v2.0"
echo "=================================="

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
        # Get Pi model info
        PI_MODEL=$(grep "Raspberry Pi" /proc/cpuinfo | head -1 | cut -d: -f2 | xargs)
        print_status "Detected: $PI_MODEL"
    fi
}

# Update system packages
update_system() {
    print_step "Updating system packages..."
    sudo apt update
    sudo apt upgrade -y
    print_status "System updated ✅"
}

# Install system dependencies
install_system_deps() {
    print_step "Installing system dependencies..."
    
    # Core system packages
    sudo apt install -y \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        git \
        curl \
        wget \
        build-essential \
        cmake \
        pkg-config
    
    # I2C and serial tools
    sudo apt install -y \
        i2c-tools \
        python3-smbus \
        minicom \
        screen
    
    # SMB/CIFS file sharing
    sudo apt install -y \
        samba \
        samba-common-bin
    
    # Audio system dependencies
    sudo apt install -y \
        alsa-utils \
        pulseaudio \
        pulseaudio-utils \
        espeak \
        espeak-data \
        libespeak-dev \
        ffmpeg \
        sox \
        libsox-fmt-all \
        portaudio19-dev \
        libasound2-dev
    
    # OpenCV dependencies for Raspberry Pi
    sudo apt install -y \
        libopencv-dev \
        python3-opencv 
    
    # GPIO libraries
    sudo apt install -y \
        python3-rpi.gpio \
        python3-gpiozero
    
    print_status "System dependencies installed ✅"
}

# Install and setup pyenv with Python 3.9.13
setup_pyenv() {
    print_step "Setting up pyenv with Python 3.9.13..."
    
    # Check if pyenv is already installed
    if command -v pyenv >/dev/null 2>&1; then
        print_status "pyenv is already installed"
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
            liblzma-dev
        
        # Install pyenv
        curl https://pyenv.run | bash
        
        # Add pyenv to shell configuration
        SHELL_CONFIG=""
        if [ -f "$HOME/.bashrc" ]; then
            SHELL_CONFIG="$HOME/.bashrc"
        elif [ -f "$HOME/.zshrc" ]; then
            SHELL_CONFIG="$HOME/.zshrc"
        else
            SHELL_CONFIG="$HOME/.profile"
        fi
        
        # Add pyenv initialization to shell config if not already present
        if ! grep -q 'export PYENV_ROOT' "$SHELL_CONFIG" 2>/dev/null; then
            echo '' >> "$SHELL_CONFIG"
            echo '# pyenv configuration' >> "$SHELL_CONFIG"
            echo 'export PYENV_ROOT="$HOME/.pyenv"' >> "$SHELL_CONFIG"
            echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> "$SHELL_CONFIG"
            echo 'eval "$(pyenv init -)"' >> "$SHELL_CONFIG"
            print_status "Added pyenv to $SHELL_CONFIG"
        fi
        
        # Initialize pyenv for current session
        export PYENV_ROOT="$HOME/.pyenv"
        command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init -)"
        
        print_status "pyenv installed ✅"
    fi
    
    # Check if Python 3.9.13 is already installed
    if pyenv versions --bare | grep -q "3.9.13"; then
        print_status "Python 3.9.13 is already installed"
    else
        print_status "Installing Python 3.9.13 (this may take several minutes)..."
        pyenv install 3.9.13
        print_status "Python 3.9.13 installed ✅"
    fi
    
    # Set Python 3.9.13 as local version for this project
    pyenv local 3.9.13
    print_status "Set Python 3.9.13 as local version for WALL-E project ✅"
    
    # Verify Python version
    PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    if [[ "$PYTHON_VERSION" == "3.9.13" ]]; then
        print_status "✅ Confirmed Python version: $PYTHON_VERSION"
    else
        print_warning "⚠️ Expected Python 3.9.13, but got: $PYTHON_VERSION"
        print_warning "You may need to restart your shell and run the script again"
    fi
}

# Create Python virtual environment with specific version
create_venv() {
    print_step "Creating Python virtual environment with Python 3.9.13..."
    
    # Ensure we're using the correct Python version
    PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    if [[ "$PYTHON_VERSION" != "3.9.13" ]]; then
        print_error "Expected Python 3.9.13, but found: $PYTHON_VERSION"
        print_error "Please restart your shell and run this script again"
        exit 1
    fi
    
    if [ -d "venv" ]; then
        print_warning "Virtual environment already exists, removing..."
        rm -rf venv
    fi
    
    # Create virtual environment with pyenv Python 3.9.13
    python -m venv venv
    source venv/bin/activate
    
    # Verify virtual environment Python version
    VENV_PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    print_status "Virtual environment Python version: $VENV_PYTHON_VERSION"
    
    # Upgrade pip
    pip install --upgrade pip setuptools wheel
    
    print_status "Virtual environment created with Python 3.9.13 ✅"
}

# Install Python dependencies
install_python_deps() {
    print_step "Installing Python dependencies..."
    
    source venv/bin/activate
    
    # Core dependencies
    pip install \
        asyncio \
        websockets \
        pyserial \
        psutil \
        pygame \
        requests \
        flask \
        numpy==1.21.6 \
        lgpio \
        adafruit-circuitpython-ads1x15 \
        adafruit-blinka

    # Try to install OpenCV with fallback
    print_status "Installing OpenCV (this may take a while)..."
    if ! pip install opencv-python==4.5.5.64 --no-cache-dir; then
        print_warning "pip OpenCV install failed, using system OpenCV"
        # Create symlink to system OpenCV if needed
        SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
        if [ -f /usr/lib/python3/dist-packages/cv2/python-3.*/cv2*.so ]; then
            print_status "Linking system OpenCV to virtual environment"
            ln -sf /usr/lib/python3/dist-packages/cv2 "$SITE_PACKAGES/"
        fi
    fi
    
    print_status "Python dependencies installed ✅"
}

# Enable I2C and serial interfaces
enable_interfaces() {
    print_step "Enabling I2C and Serial interfaces..."
    
    # Backup config.txt
    sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.backup.$(date +%Y%m%d) 2>/dev/null || \
    sudo cp /boot/config.txt /boot/config.txt.backup.$(date +%Y%m%d) 2>/dev/null || \
    print_warning "Could not backup config.txt"
    
    CONFIG_FILE="/boot/firmware/config.txt"
    if [ ! -f "$CONFIG_FILE" ]; then
        CONFIG_FILE="/boot/config.txt"
    fi
    
    # Enable I2C
    if ! grep -q "dtparam=i2c_arm=on" "$CONFIG_FILE"; then
        echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_FILE"
        print_status "I2C enabled in $CONFIG_FILE"
    fi
    
    # Enable serial port
    if ! grep -q "enable_uart=1" "$CONFIG_FILE"; then
        echo "enable_uart=1" | sudo tee -a "$CONFIG_FILE"
        print_status "UART enabled in $CONFIG_FILE"
    fi
    
    # Add user to required groups
    sudo usermod -a -G i2c,spi,gpio,audio,dialout $USER
    
    print_status "Interfaces configured ✅"
}

# Create directory structure
create_directories() {
    print_step "Creating directory structure..."
    
    mkdir -p configs
    mkdir -p modules
    mkdir -p logs
    mkdir -p audio
    mkdir -p icons
    mkdir -p scenes
    
    print_status "Directory structure created ✅"
}

# Create configuration files
create_config_files() {
    print_step "Creating configuration files..."
    
    # Hardware configuration
    cat > configs/hardware_config.json << 'EOF'
{
    "hardware": {
        "maestro1": {
            "port": "/dev/ttyAMA0",
            "baud_rate": 9600,
            "device_number": 12
        },
        "maestro2": {
            "port": "/dev/ttyAMA0",
            "baud_rate": 9600,
            "device_number": 13
        },
        "sabertooth": {
            "port": "/dev/ttyAMA1",
            "baud_rate": 9600
        },
        "gpio": {
            "motor_step_pin": 16,
            "motor_dir_pin": 12,
            "motor_enable_pin": 13,
            "limit_switch_pin": 26,
            "emergency_stop_pin": 25
        },
        "timing": {
            "telemetry_interval": 0.2,
            "servo_update_rate": 0.02
        },
        "audio": {
            "directory": "audio",
            "volume": 0.7
        }
    }
}
EOF

    # Camera configuration
    cat > configs/camera_config.json << 'EOF'
{
    "esp32_url": "http://esp32.local:81/stream",
    "rebroadcast_port": 8081,
    "enable_stats": true,
    "resolution": "640x480",
    "quality": 80
}
EOF

    # Scenes configuration
    cat > configs/scenes.json << 'EOF'
{
    "happy": {
        "emoji": "😊",
        "category": "Happy",
        "duration": 3.0,
        "audio_file": "track_002",
        "servos": {
            "m1_ch0": {"target": 1500, "speed": 50},
            "m1_ch1": {"target": 1200, "speed": 30}
        }
    },
    "sad": {
        "emoji": "😢", 
        "category": "Sad",
        "duration": 4.0,
        "audio_file": "track_004",
        "servos": {
            "m1_ch0": {"target": 1000, "speed": 20},
            "m1_ch1": {"target": 1800, "speed": 20}
        }
    },
    "wave_response": {
        "emoji": "👋",
        "category": "Gesture", 
        "duration": 3.0,
        "audio_file": "track_008",
        "servos": {
            "m1_ch3": {"target": 1200, "speed": 60}
        }
    },
    "excited": {
        "emoji": "🤩",
        "category": "Happy",
        "duration": 2.5,
        "audio_file": "track_001",
        "servos": {
            "m1_ch0": {"target": 1800, "speed": 80},
            "m1_ch1": {"target": 800, "speed": 80}
        }
    }
}
EOF

    print_status "Configuration files created ✅"
}

# Create sample audio files with text-to-speech
create_sample_audio() {
    print_step "Creating sample audio files..."
    
    # Create some sample TTS files
    espeak "Hello, I am WALL-E!" -w audio/track_001.wav -s 120 -p 40 2>/dev/null || \
        print_warning "Could not create sample audio file 1"
    
    espeak "I am happy to see you!" -w audio/track_002.wav -s 110 -p 50 2>/dev/null || \
        print_warning "Could not create sample audio file 2"
    
    espeak "WALL-E is sad." -w audio/track_004.wav -s 90 -p 30 2>/dev/null || \
        print_warning "Could not create sample audio file 4"
    
    espeak "Hello there!" -w audio/track_008.wav -s 130 -p 60 2>/dev/null || \
        print_warning "Could not create sample audio file 8"
    
    print_status "Sample audio files created ✅"
    
    # Create SMB sharing documentation
    cat > SMB_SHARING.md << 'EOF'
# 🌐 WALL-E SMB File Sharing Quick Guide

## 📁 Access Your WALL-E Files Over Network

### Windows:
- Main project: `\\HOSTNAME\walle`
- Audio files: `\\HOSTNAME\walle-audio`
- Config files: `\\HOSTNAME\walle-configs`

### macOS/Linux:
- Main project: `smb://HOSTNAME/walle`
- Audio files: `smb://HOSTNAME/walle-audio`
- Config files: `smb://HOSTNAME/walle-configs`

*(Replace HOSTNAME with your Pi's name, usually "wall-e")*

## 🎵 Upload Audio Files:
1. Connect to the audio share
2. Drag & drop MP3, WAV, OGG files
3. Name them: `happy.mp3`, `sad.wav`, `track_001.mp3`
4. Files are immediately available to WALL-E!

## ⚙️ Edit Settings:
1. Connect to config share  
2. Edit JSON files with any text editor
3. Restart WALL-E to apply changes

## 🔧 Troubleshooting:
```bash
./manage_smb.sh status    # Check connection info
./manage_smb.sh restart   # Fix connection issues
```

**No password required - guest access enabled!**
EOF

    print_status "Documentation created ✅"
}

# Set up udev rules for consistent device naming
setup_udev_rules() {
    print_step "Setting up udev rules for device naming..."
    
    cat > 99-walle-devices.rules << 'EOF'
# WALL-E Device Rules
# Pololu Maestro controllers
SUBSYSTEM=="tty", ATTRS{idVendor}=="1ffb", ATTRS{idProduct}=="008a", SYMLINK+="maestro%n"

# USB-to-serial adapters
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="usb-serial%n"

# ESP32 devices
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", SYMLINK+="esp32-%n"
EOF

    sudo cp 99-walle-devices.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    
    print_status "Udev rules installed ✅"
}

# Set up SMB file sharing
setup_smb_sharing() {
    print_step "Setting up SMB file sharing..."
    
    # Get current directory and user
    WALLE_DIR=$(pwd)
    CURRENT_USER=$(whoami)
    
    # Backup existing smb.conf
    sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup.$(date +%Y%m%d) 2>/dev/null || \
        print_warning "Could not backup smb.conf"
    
    # Add WALL-E share configuration
    cat >> walle_smb.conf << EOF

# WALL-E Project Share
[walle]
    comment = WALL-E Robot Project Files
    path = $WALLE_DIR
    browseable = yes
    read only = no
    guest ok = yes
    create mask = 0664
    directory mask = 0775
    force user = $CURRENT_USER
    force group = $CURRENT_USER
    public = yes
    writable = yes
    
# Audio files quick access
[walle-audio]
    comment = WALL-E Audio Files
    path = $WALLE_DIR/audio
    browseable = yes
    read only = no
    guest ok = yes
    create mask = 0664
    directory mask = 0775
    force user = $CURRENT_USER
    force group = $CURRENT_USER
    public = yes
    writable = yes

# Configuration files
[walle-configs]
    comment = WALL-E Configuration Files
    path = $WALLE_DIR/configs
    browseable = yes
    read only = no
    guest ok = yes
    create mask = 0664
    directory mask = 0775
    force user = $CURRENT_USER
    force group = $CURRENT_USER
    public = yes
    writable = yes
EOF

    # Append to system smb.conf
    sudo tee -a /etc/samba/smb.conf < walle_smb.conf > /dev/null
    
    # Set proper permissions on directories
    chmod 775 "$WALLE_DIR"
    chmod 775 "$WALLE_DIR"/audio 2>/dev/null || true
    chmod 775 "$WALLE_DIR"/configs 2>/dev/null || true
    chmod 775 "$WALLE_DIR"/logs 2>/dev/null || true
    
    # Make sure user is in required groups
    sudo usermod -a -G sambashare $CURRENT_USER 2>/dev/null || true
    
    # Test samba configuration
    if sudo testparm -s > /dev/null 2>&1; then
        print_status "Samba configuration is valid"
    else
        print_error "Samba configuration has errors"
        sudo testparm -s
    fi
    
    # Start and enable Samba services
    sudo systemctl enable smbd
    sudo systemctl enable nmbd
    sudo systemctl restart smbd
    sudo systemctl restart nmbd
    
    # Get network info for user
    HOSTNAME=$(hostname)
    IP_ADDRESS=$(hostname -I | awk '{print $1}')
    
    print_status "SMB file sharing configured ✅"
    print_status "Access your WALL-E files from any computer:"
    echo "  📁 Windows: \\\\$HOSTNAME\\walle or \\\\$IP_ADDRESS\\walle"
    echo "  📁 macOS: smb://$HOSTNAME/walle or smb://$IP_ADDRESS/walle"
    echo "  📁 Linux: smb://$HOSTNAME/walle or smb://$IP_ADDRESS/walle"
    echo ""
    echo "  🎵 Audio files: \\\\$HOSTNAME\\walle-audio"
    echo "  ⚙️ Config files: \\\\$HOSTNAME\\walle-configs"
    echo "  📜 All project: \\\\$HOSTNAME\\walle"
    echo ""
    print_status "Guest access enabled - no password required!"
    
    # Clean up temporary file
    rm -f walle_smb.conf
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
Environment="PATH=$(pwd)/venv/bin:/home/$USER/.pyenv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYENV_ROOT=/home/$USER/.pyenv"
ExecStartPre=/bin/bash -c 'eval "\$(pyenv init -)" && pyenv local 3.9.13'
ExecStart=$(pwd)/venv/bin/python $(pwd)/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # Install service
    sudo cp walle.service /etc/systemd/system/
    sudo systemctl daemon-reload
    
    print_status "Systemd service created ✅"
    print_status "Service configured to use Python 3.9.13 via pyenv"
}

# Create startup script
create_startup_script() {
    print_step "Creating startup scripts..."
    
    # Main startup script
    cat > start_walle.sh << 'EOF'
#!/bin/bash
# Enhanced WALL-E Startup Script with Camera Proxy Support

cd "$(dirname "$0")"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

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

echo "🤖 Starting WALL-E System..."

# Initialize pyenv if available
if command -v pyenv >/dev/null 2>&1; then
    eval "$(pyenv init -)"
fi

# Check Python version
PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
if [[ "$PYTHON_VERSION" != "3.9.13" ]]; then
    print_warning "Expected Python 3.9.13, found: $PYTHON_VERSION"
    print_warning "Try: source ~/.bashrc && cd $(pwd)"
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    print_error "Virtual environment not found. Run install.sh first."
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Verify Python version in venv
VENV_PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
print_status "Using Python $VENV_PYTHON_VERSION in virtual environment"

# Function to check if OpenCV is working
check_opencv() {
    python -c "import cv2" 2>/dev/null
    return $?
}

# Function to start camera proxy
start_camera_proxy() {
    if [ ! -f "modules/camera_proxy.py" ]; then
        print_warning "Camera proxy not found at modules/camera_proxy.py"
        return 1
    fi
    
    if check_opencv; then
        print_step "Starting camera proxy..."
        python modules/camera_proxy.py &
        CAMERA_PID=$!
        
        # Give it time to start
        sleep 3
        
        # Check if it's still running
        if kill -0 $CAMERA_PID 2>/dev/null; then
            print_status "✅ Camera proxy started (PID: $CAMERA_PID)"
            echo $CAMERA_PID > camera_proxy.pid
            
            # Get IP address for display
            IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")
            print_status "📷 Camera stream: http://$IP_ADDRESS:8081/stream"
            return 0
        else
            print_error "❌ Camera proxy failed to start"
            return 1
        fi
    else
        print_warning "OpenCV not available, skipping camera proxy"
        return 1
    fi
}

# Function to stop camera proxy
stop_camera_proxy() {
    if [ -f "camera_proxy.pid" ]; then
        CAMERA_PID=$(cat camera_proxy.pid)
        if kill -0 $CAMERA_PID 2>/dev/null; then
            print_status "Stopping camera proxy (PID: $CAMERA_PID)..."
            kill $CAMERA_PID
            wait $CAMERA_PID 2>/dev/null
        fi
        rm -f camera_proxy.pid
    fi
}

# Function for cleanup on exit
cleanup() {
    print_step "Shutting down WALL-E services..."
    stop_camera_proxy
    print_status "✅ Cleanup complete"
    exit 0
}

# Set up signal handlers for graceful shutdown
trap cleanup SIGINT SIGTERM

# Parse command line arguments
START_CAMERA=true
SHOW_HELP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-camera)
            START_CAMERA=false
            shift
            ;;
        --camera-only)
            print_status "Starting camera proxy only..."
            start_camera_proxy
            if [ $? -eq 0 ]; then
                print_status "Camera proxy running. Press Ctrl+C to stop."
                # Wait for camera proxy process
                if [ -f "camera_proxy.pid" ]; then
                    CAMERA_PID=$(cat camera_proxy.pid)
                    wait $CAMERA_PID 2>/dev/null
                fi
            else
                print_error "Failed to start camera proxy"
                exit 1
            fi
            exit 0
            ;;
        --help|-h)
            SHOW_HELP=true
            break
            ;;
        *)
            print_error "Unknown option: $1"
            SHOW_HELP=true
            break
            ;;
    esac
done

# Show help if requested or invalid option
if [ "$SHOW_HELP" = true ]; then
    echo ""
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --no-camera     Start WALL-E without camera proxy"
    echo "  --camera-only   Start only the camera proxy"
    echo "  --help, -h      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0              # Start WALL-E with camera proxy"
    echo "  $0 --no-camera # Start WALL-E without camera"
    echo "  $0 --camera-only # Start only camera proxy"
    echo ""
    exit 0
fi

# Check if main.py exists
if [ ! -f "main.py" ]; then
    print_error "main.py not found. Make sure all files are in place."
    exit 1
fi

# Start camera proxy if enabled
CAMERA_STARTED=false
if [ "$START_CAMERA" = true ]; then
    if start_camera_proxy; then
        CAMERA_STARTED=true
    fi
fi

# Show startup status
echo ""
print_step "WALL-E Service Status:"
echo "  🤖 Core System: Starting..."
if [ "$CAMERA_STARTED" = true ]; then
    echo "  📷 Camera Proxy: ✅ Running"
else
    echo "  📷 Camera Proxy: ❌ Disabled"
fi
echo ""

# Get network information
HOSTNAME=$(hostname)
IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")

# Display connection information
print_status "🚀 Starting WALL-E core backend..."
echo ""
echo "🌐 Network Access Information:"
echo "  📡 WebSocket Server: ws://$IP_ADDRESS:8766"
echo "  🌐 SMB File Shares: \\\\$HOSTNAME\\walle"
if [ "$CAMERA_STARTED" = true ]; then
    echo "  📷 Camera Stream: http://$IP_ADDRESS:8081/stream"
    echo "  📊 Camera Stats: http://$IP_ADDRESS:8081/stats"
fi
echo ""
print_status "Press Ctrl+C to stop all services"
echo ""

# Start main WALL-E system (this will block until stopped)
python main.py
EOF

    chmod +x start_walle.sh
    
    # SMB management script
    cat > manage_smb.sh << 'EOF'
#!/bin/bash
# WALL-E SMB Share Management Script

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

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

show_status() {
    echo "🌐 WALL-E SMB Sharing Status"
    echo "============================="
    
    HOSTNAME=$(hostname)
    IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "unknown")
    
    echo "  📁 Windows: \\\\$HOSTNAME\\walle"
    echo "  📁 macOS/Linux: smb://$HOSTNAME/walle"
    echo "  🎵 Audio: \\\\$HOSTNAME\\walle-audio"
    echo "  ⚙️ Config: \\\\$HOSTNAME\\walle-configs"
    echo ""
    
    if systemctl is-active --quiet smbd; then
        print_status "SMB service running ✅"
    else
        print_warning "SMB service not running ❌"
        echo "  Run: sudo systemctl start smbd"
    fi
}

restart_services() {
    print_step "Restarting SMB services..."
    sudo systemctl restart smbd nmbd
    print_status "Services restarted ✅"
}

case "${1:-status}" in
    "status") show_status ;;
    "restart") restart_services; show_status ;;
    *) echo "Usage: $0 {status|restart}" ;;
esac
EOF

    chmod +x manage_smb.sh
    
    print_status "Startup scripts created ✅"
    print_status "  ./start_walle.sh - Start WALL-E system"
    print_status "  ./manage_smb.sh - Manage SMB file sharing"
}

# Test hardware connections
test_hardware() {
    print_step "Testing hardware connections..."
    
    # Test I2C
    if command -v i2cdetect &> /dev/null; then
        print_status "I2C tools available, scanning for devices..."
        i2cdetect -y 1 2>/dev/null || print_warning "No I2C devices found"
    fi
    
    # List USB devices
    print_status "USB devices:"
    lsusb | grep -E "(Pololu|FTDI|Arduino|Silicon Labs)" || print_warning "No known USB devices found"
    
    # List serial ports
    print_status "Serial ports:"
    ls -la /dev/tty{ACM,USB}* 2>/dev/null || print_warning "No serial devices found"
    
    # Test audio
    print_status "Testing audio..."
    if command -v amixer &> /dev/null; then
        # Set reasonable volume
        amixer sset PCM,0 70% 2>/dev/null || \
        amixer sset Master 70% 2>/dev/null || \
        amixer sset Headphone 70% 2>/dev/null || \
        print_warning "Could not set default volume"
    fi
    
    print_status "Hardware test complete ✅"
}

# Test Python installation
test_python_installation() {
    print_step "Testing Python 3.9.13 installation..."
    
    source venv/bin/activate
    
    # Test Python version
    PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    if [[ "$PYTHON_VERSION" == "3.9.13" ]]; then
        print_status "✅ Python 3.9.13 confirmed in virtual environment"
    else
        print_warning "⚠️ Expected Python 3.9.13, got: $PYTHON_VERSION"
    fi
    
    # Test core imports
    python -c "
import asyncio
import websockets
import serial
import pygame
print('✅ Core Python libraries working')
"
    
    # Test OpenCV
    python -c "
try:
    import cv2
    print('✅ OpenCV working')
except ImportError as e:
    print('⚠️ OpenCV not available:', e)
"
    
    # Test GPIO (if on Pi)
    python -c "
try:
    import RPi.GPIO
    print('✅ RPi.GPIO working')
except ImportError:
    print('⚠️ RPi.GPIO not available (normal if not on Pi)')
"
    
    print_status "Python 3.9.13 installation test complete ✅"
}

# Main installation process
main() {
    print_status "Starting WALL-E system installation..."
    echo "Current directory: $(pwd)"
    echo "Current user: $(whoami)"
    echo ""
    
    check_raspberry_pi
    update_system
    install_system_deps
    enable_interfaces
    create_directories
    setup_pyenv
    create_venv
    install_python_deps
    create_config_files
    create_sample_audio
    setup_udev_rules
    setup_smb_sharing
    create_services
    create_startup_script
    test_hardware
    test_python_installation
    
    echo ""
    echo "🎉 WALL-E System Installation Complete!"
    echo "======================================"
    echo ""
    print_status "✅ Native Audio System Ready!"
    echo "  📁 Audio files go in: ./audio/"
    echo "  🎵 Supported formats: MP3, WAV, OGG, M4A"
    echo "  🎤 Text-to-speech: espeak installed"
    echo "  🔊 Volume control: amixer/alsamixer"
    echo ""
    print_status "✅ Configuration Files Created:"
    echo "  📁 configs/hardware_config.json - Hardware settings"
    echo "  📁 configs/camera_config.json - Camera settings"
    echo "  📁 configs/scenes.json - Robot scenes and emotions"
    echo ""
    print_status "✅ Python Environment Ready:"
    echo "  🐍 Python 3.9.13 installed via pyenv"
    echo "  📁 Virtual environment: ./venv/"
    echo "  📦 All dependencies installed"
    echo "  🔧 OpenCV configured for Raspberry Pi"
    echo ""
    print_status "✅ SMB File Sharing Ready:"
    HOSTNAME=$(hostname)
    IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "IP_NOT_FOUND")
    echo "  🌐 Network access enabled for easy file management"
    echo "  📁 Windows: \\\\$HOSTNAME\\walle"
    echo "  📁 macOS/Linux: smb://$HOSTNAME/walle"
    echo "  🎵 Audio quick access: \\\\$HOSTNAME\\walle-audio"
    echo "  ⚙️ Config quick access: \\\\$HOSTNAME\\walle-configs"
    echo "  🔓 Guest access - no password required"
    echo ""
    print_status "Next steps:"
    echo "  1. 🔄 Reboot to enable I2C/Serial: sudo reboot"
    echo "  2. 🔌 Connect your hardware:"
    echo "     • Maestro controllers: USB ports"
    echo "     • Audio: Built-in 3.5mm jack or HDMI"
    echo "     • ESP32 camera: USB or network"
    echo "     • Current sensors: I2C (SDA/SCL pins)"
    echo "     • Emergency stop: GPIO 25"
    echo "     • Limit switch: GPIO 26"
    echo "  3. 🎵 Add audio files to ./audio/ directory"
    echo "  4. 🧪 Test the system: ./start_walle.sh"
    echo "  5. 🌐 Start frontend in another terminal"
    echo ""
    print_status "Service management:"
    echo "  • Start service: sudo systemctl start walle"
    echo "  • Enable auto-start: sudo systemctl enable walle"
    echo "  • View logs: sudo journalctl -u walle -f"
    echo "  • Stop service: sudo systemctl stop walle"
    echo ""
    print_status "File sharing management:"
    echo "  • Check SMB status: ./manage_smb.sh status"
    echo "  • Restart SMB: ./manage_smb.sh restart"
    echo "  • Upload files via network from any computer!"
    echo ""
    print_status "Troubleshooting:"
    echo "  • Test audio: speaker-test -t wav"
    echo "  • Test TTS: espeak 'Hello WALL-E'"
    echo "  • Volume control: alsamixer"
    echo "  • Check I2C: i2cdetect -y 1"
    echo "  • List serial: ls /dev/tty*"
    echo "  • SMB status: ./manage_smb.sh status"
    echo "  • SMB restart: ./manage_smb.sh restart"
    echo "  • Check Python: python --version (should be 3.9.13)"
    echo "  • List pyenv versions: pyenv versions"
    echo "  • SMB logs: sudo journalctl -u smbd"
    echo ""
    print_warning "⚠️ A reboot is required to enable I2C and Serial interfaces!"
    print_status "After reboot:"
    echo "  • If Python version issues: source ~/.bashrc && cd $(pwd)"
    echo "  • Start WALL-E: ./start_walle.sh"
    echo "  • Access files: \\\\$(hostname)\\walle"
    echo "  • Upload audio files via network share!"
}

# Run main installation if script is executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi