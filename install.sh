#!/bin/bash
# DroidDeck Backend Installer for Raspberry Pi 5
# Version: 5.0 - Includes WiFi hotspot and systemd service setup

set -e

echo "DroidDeck Backend Installer v5.0"
echo "================================="
echo "Target: Raspberry Pi 5 with Python 3.9.13"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m'

print_status()  { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
print_step()    { echo -e "${BLUE}[STEP]${NC} $1"; }
print_success() { echo -e "${PURPLE}[SUCCESS]${NC} $1"; }

BACKEND_DIR=$(pwd)
CURRENT_USER=$(whoami)
SERVICE_NAME="droiddeck-backend"

# ---------------------------------------------------------------------------
# System check
# ---------------------------------------------------------------------------

check_raspberry_pi() {
    print_step "Checking system compatibility..."
    if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        print_warning "Not running on Raspberry Pi - some features may not work"
        read -p "Continue anyway? (y/N): " -n 1 -r; echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    else
        PI_MODEL=$(grep "Raspberry Pi" /proc/cpuinfo | head -1 | cut -d: -f2 | xargs)
        print_success "Detected: $PI_MODEL"
    fi
}

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

update_system() {
    print_step "Updating system packages..."
    sudo apt update
    sudo apt upgrade -y
    print_success "System updated"
}

install_system_deps() {
    print_step "Installing system dependencies..."
    sudo apt install -y \
        build-essential cmake pkg-config git curl wget \
        python3 python3-pip python3-venv python3-dev \
        i2c-tools python3-smbus minicom \
        python3-rpi.gpio python3-gpiozero python3-lgpio \
        alsa-utils pulseaudio pulseaudio-utils portaudio19-dev libasound2-dev \
        ffmpeg sox libsox-fmt-all espeak \
        samba samba-common-bin cifs-utils \
        make libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
        llvm libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
        libffi-dev liblzma-dev libgdbm-dev libnss3-dev \
        network-manager
    print_success "System dependencies installed"
}

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------

setup_pyenv() {
    print_step "Setting up pyenv with Python 3.9.13..."
    if command -v pyenv >/dev/null 2>&1; then
        print_status "pyenv already installed"
    else
        print_status "Installing pyenv..."
        curl https://pyenv.run | bash
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
    if ! pyenv versions | grep -q "3.9.13"; then
        print_status "Installing Python 3.9.13..."
        pyenv install 3.9.13
    fi
    pyenv local 3.9.13
    print_success "Python 3.9.13 configured"
}

create_venv() {
    print_step "Creating virtual environment..."
    python -m venv venv
    source venv/bin/activate
    pip install --upgrade pip setuptools wheel
    print_success "Virtual environment created"
}

install_python_deps() {
    print_step "Installing Python dependencies..."
    source venv/bin/activate
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
        "numpy>=1.21.6" \
        "watchdog>=2.1.0" \
        "jsonschema>=3.2.0" \
        "dataclasses-json>=0.5.0" \
        "python-dateutil>=2.8.0"
    pip install gpiozero rpi-lgpio adafruit-circuitpython-ads1x15 adafruit-blinka lgpio
    pip install pyaudio wave mutagen
    pip install "opencv-python==4.5.5.64" --no-cache-dir || print_warning "OpenCV install skipped"
    print_success "Python dependencies installed"
}

# ---------------------------------------------------------------------------
# Pi hardware interfaces
# ---------------------------------------------------------------------------

configure_pi_interfaces() {
    print_step "Configuring Raspberry Pi interfaces..."
    CONFIG_FILE="/boot/firmware/config.txt"
    [ ! -f "$CONFIG_FILE" ] && CONFIG_FILE="/boot/config.txt"
    if [ ! -f "$CONFIG_FILE" ]; then
        print_error "Cannot find Pi config file"; return 1
    fi
    sudo cp "$CONFIG_FILE" "${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    grep -q "dtparam=i2c_arm=on" "$CONFIG_FILE" || echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_FILE" > /dev/null
    grep -q "dtparam=spi=on"     "$CONFIG_FILE" || echo "dtparam=spi=on"     | sudo tee -a "$CONFIG_FILE" > /dev/null
    grep -q "enable_uart=1"      "$CONFIG_FILE" || echo "enable_uart=1"      | sudo tee -a "$CONFIG_FILE" > /dev/null
    print_success "Hardware interfaces enabled"
}

# ---------------------------------------------------------------------------
# Directories and SMB
# ---------------------------------------------------------------------------

create_directories() {
    print_step "Creating directory structure..."
    mkdir -p configs/backups logs audio bottango_imports scenes
    print_success "Directories verified"
}

setup_smb_share() {
    print_step "Setting up SMB file sharing..."
    if [ -f /etc/samba/smb.conf ]; then
        sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.backup.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
    fi
    cat > /tmp/droiddeck_smb.conf << EOF

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
    sudo tee -a /etc/samba/smb.conf < /tmp/droiddeck_smb.conf > /dev/null
    rm -f /tmp/droiddeck_smb.conf
    chmod 775 "$BACKEND_DIR"
    chmod 775 "$BACKEND_DIR/audio"         2>/dev/null || true
    chmod 775 "$BACKEND_DIR/configs"       2>/dev/null || true
    chmod 775 "$BACKEND_DIR/logs"          2>/dev/null || true
    chmod 775 "$BACKEND_DIR/bottango_imports" 2>/dev/null || true
    chmod 775 "$BACKEND_DIR/scenes"        2>/dev/null || true
    sudo systemctl enable smbd nmbd
    sudo systemctl restart smbd nmbd
    print_success "SMB file sharing configured"
}

# ---------------------------------------------------------------------------
# WiFi hotspot (USB adapter - mt7921u / Netgear A7500 or compatible)
# ---------------------------------------------------------------------------

setup_wifi_hotspot() {
    print_step "Setting up WiFi hotspot on USB adapter..."

    # Install mt7921u firmware if missing
    if ! ls /lib/firmware/mediatek/WIFI_MT7961_patch_mcu_1_2_hdr.bin &>/dev/null; then
        print_status "Installing mt7921u firmware..."
        sudo apt install -y firmware-mediatek || print_warning "firmware-mediatek install failed - adapter may not work"
    fi

    # Persist udev rule for Netgear A7500 VID/PID
    if [ ! -f /etc/udev/rules.d/99-mt7921u.rules ]; then
        print_status "Creating udev rule for mt7921u..."
        sudo tee /etc/udev/rules.d/99-mt7921u.rules << 'EOF'
ACTION=="add", SUBSYSTEM=="usb", ENV{ID_VENDOR_ID}=="0846", ENV{ID_MODEL_ID}=="9065", RUN+="/usr/sbin/modprobe mt7921u", RUN+="/bin/sh -c 'echo 0846 9065 > /sys/bus/usb/drivers/mt7921u/new_id'"
EOF
        sudo udevadm control --reload-rules
    fi

    # Load module at boot
    if ! grep -q "mt7921u" /etc/modules-load.d/mt7921u.conf 2>/dev/null; then
        echo "mt7921u" | sudo tee /etc/modules-load.d/mt7921u.conf > /dev/null
    fi

    # Check if wlan1 is available right now
    if ! ip link show wlan1 &>/dev/null; then
        print_warning "wlan1 not found - USB adapter may need to be plugged in."
        print_warning "Hotspot NetworkManager profile will be created but not activated until adapter is present."
    fi

    # Create the hotspot connection profile if it doesn't already exist
    if ! nmcli con show "Walle-Hotspot" &>/dev/null; then
        print_status "Creating Walle hotspot profile..."
        sudo nmcli con add type wifi ifname wlan1 con-name "Walle-Hotspot" autoconnect yes ssid "Walle" \
            802-11-wireless.mode ap \
            802-11-wireless.band a \
            802-11-wireless.channel 36 \
            ipv4.method shared \
            wifi-sec.key-mgmt wpa-psk \
            wifi-sec.psk "EVEROCKS2025" \
            wifi-sec.proto rsn \
            wifi-sec.pairwise ccmp \
            wifi-sec.group ccmp

        if ip link show wlan1 &>/dev/null; then
            sudo nmcli con up "Walle-Hotspot" || print_warning "Could not activate hotspot yet - will start after reboot"
        fi
    else
        print_status "Walle-Hotspot profile already exists - skipping"
    fi

    # Enable IP forwarding so hotspot clients get internet via wlan0
    if ! grep -q "net.ipv4.ip_forward=1" /etc/sysctl.d/99-ip-forward.conf 2>/dev/null; then
        echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-ip-forward.conf > /dev/null
        sudo sysctl -w net.ipv4.ip_forward=1
    fi

    print_success "WiFi hotspot configured (SSID: Walle, password: EVEROCKS2025)"
}

# ---------------------------------------------------------------------------
# ESP32 hotspot (onboard wlan0 virtual AP for 2.4GHz devices)
# ---------------------------------------------------------------------------

setup_esp32_hotspot() {
    print_step "Setting up ESP32 2.4GHz hotspot on wlan0..."

    # Force home WiFi connection to 2.4GHz so wlan0ap can share the channel
    if nmcli con show "preconfigured" &>/dev/null; then
        print_status "Forcing wlan0 home connection to 2.4GHz band..."
        sudo nmcli connection modify preconfigured \
            802-11-wireless.band bg \
            802-11-wireless.channel 1 \
            connection.autoconnect-retries 0
        sudo nmcli connection down preconfigured 2>/dev/null || true
        sudo nmcli connection up preconfigured || print_warning "Could not reconnect wlan0 - may need manual reconnect"
    else
        print_warning "Home WiFi connection not found as preconfigured - wlan0 band not changed"
    fi

    # Create virtual AP interface if not already present
    if ! ip link show wlan0ap &>/dev/null; then
        print_status "Creating wlan0ap virtual interface..."
        sudo iw dev wlan0 interface add wlan0ap type __ap
        sudo ip link set wlan0ap up
    fi

    # Create the ESP32 hotspot profile if it does not already exist
    if ! nmcli con show "Walle-ESP32" &>/dev/null; then
        print_status "Creating Walle-ESP32 hotspot profile..."
        sudo nmcli con add type wifi ifname wlan0ap con-name "Walle-ESP32" autoconnect yes ssid "Walle" \
            802-11-wireless.mode ap \
            802-11-wireless.band bg \
            802-11-wireless.channel 1 \
            ipv4.method shared \
            ipv4.addresses 10.43.0.1/24 \
            wifi-sec.key-mgmt wpa-psk \
            wifi-sec.psk "EVEROCKS2025" \
            wifi-sec.proto rsn \
            wifi-sec.pairwise ccmp \
            wifi-sec.group ccmp

        sudo nmcli con up "Walle-ESP32" || print_warning "Could not activate ESP32 hotspot yet - will start after reboot"
    else
        print_status "Walle-ESP32 profile already exists - skipping"
    fi

    # Dispatcher script to recreate wlan0ap virtual interface after each reboot
    sudo tee /etc/NetworkManager/dispatcher.d/99-wlan0ap > /dev/null << EOF
#!/bin/bash
IFACE=\$1
ACTION=\$2

if [ "\$IFACE" = "wlan0" ] && [ "\$ACTION" = "up" ]; then
    if ! ip link show wlan0ap &>/dev/null; then
        iw dev wlan0 interface add wlan0ap type __ap
        ip link set wlan0ap up
        nmcli con up "Walle-ESP32"
    fi
fi
EOF
    sudo chmod +x /etc/NetworkManager/dispatcher.d/99-wlan0ap

    print_success "ESP32 hotspot configured (SSID: Walle, 2.4GHz, subnet: 10.43.0.0/24)"
}

# ---------------------------------------------------------------------------
# Systemd service
# ---------------------------------------------------------------------------

setup_systemd_service() {
    print_step "Setting up DroidDeck backend systemd service..."

    # Find python path inside venv
    PYTHON_PATH="$BACKEND_DIR/venv/bin/python"
    if [ ! -f "$PYTHON_PATH" ]; then
        print_warning "venv not found at expected path - service may need manual adjustment"
        PYTHON_PATH=$(which python3)
    fi

    sudo tee /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=DroidDeck WALL-E Robot Backend
After=network-online.target time-sync.target
Wants=network-online.target

[Service]
Type=notify
NotifyAccess=main
User=$CURRENT_USER
WorkingDirectory=$BACKEND_DIR
ExecStart=$PYTHON_PATH main.py
Restart=on-failure
RestartSec=5
WatchdogSec=30
TimeoutStartSec=90
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME
ExecStartPre=/bin/sleep 3

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable ${SERVICE_NAME}

    # Persistent journald storage so service logs survive reboots and
    # power cuts - essential for diagnosing crashes at events. The drop-in
    # leaves the rest of the journald config untouched.
    print_status "Enabling persistent journald storage..."
    sudo mkdir -p /var/log/journal
    sudo mkdir -p /etc/systemd/journald.conf.d
    sudo tee /etc/systemd/journald.conf.d/droiddeck-persistent.conf > /dev/null << EOF
[Journal]
Storage=persistent
SystemMaxUse=200M
EOF
    sudo systemctl restart systemd-journald

    # Allow the service user to restart the backend without a password prompt
    echo "$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart ${SERVICE_NAME}" | \
        sudo tee /etc/sudoers.d/droiddeck-restart > /dev/null
    sudo chmod 440 /etc/sudoers.d/droiddeck-restart

    print_success "Service installed and enabled: ${SERVICE_NAME}"
    print_status "Start now with: sudo systemctl start ${SERVICE_NAME}"
    print_status "View logs with: journalctl -u ${SERVICE_NAME} -f"
}

# ---------------------------------------------------------------------------
# Config verification
# ---------------------------------------------------------------------------

verify_configs() {
    print_step "Verifying configuration files..."
    if [ ! -d "configs" ]; then
        print_error "configs directory not found - clone the complete repository"
        exit 1
    fi
    MISSING_CONFIGS=()
    [ ! -f "configs/hardware_config.json" ] && MISSING_CONFIGS+=("hardware_config.json")
    [ ! -f "configs/camera_config.json"   ] && MISSING_CONFIGS+=("camera_config.json")
    if [ ${#MISSING_CONFIGS[@]} -gt 0 ]; then
        print_warning "Missing config files: ${MISSING_CONFIGS[*]} - will be created on first run"
    else
        print_success "All configuration files present"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    print_status "Starting DroidDeck Backend Installation"
    echo "Directory : $BACKEND_DIR"
    echo "User      : $CURRENT_USER"
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
    setup_wifi_hotspot
    setup_esp32_hotspot
    setup_systemd_service
    verify_configs

    echo ""
    echo "DroidDeck Backend Installation Complete!"
    echo "========================================"
    echo ""
    print_success "Installed components:"
    echo "  Python 3.9.13 with pyenv"
    echo "  All backend Python dependencies"
    echo "  Hardware interfaces (I2C, SPI, UART)"
    echo "  Directory structure"
    echo "  SMB file sharing"
    echo "  WiFi hotspot - Steam Deck (Walle / EVEROCKS2025) on wlan1 5GHz  - 10.42.0.1"
    echo "  WiFi hotspot - ESP32     (Walle / EVEROCKS2025) on wlan0ap 2.4GHz - 10.43.0.1"
    echo "  Systemd service: $SERVICE_NAME"
    echo ""

    HOSTNAME=$(hostname)
    IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "unknown")

    print_success "Network access:"
    echo "  Steam Deck WebSocket   : ws://10.42.0.1:8766"
    echo "  Home network WebSocket : ws://$IP_ADDRESS:8766"
    echo "  ESP32 camera subnet    : 10.43.0.0/24"
    echo "  SMB (Windows)          : \\\\$HOSTNAME\\DroidDeck-Backend"
    echo "  SMB (Mac/Linux)        : smb://$IP_ADDRESS/DroidDeck-Backend"
    echo ""
    print_success "Service management:"
    echo "  Start  : sudo systemctl start $SERVICE_NAME"
    echo "  Stop   : sudo systemctl stop $SERVICE_NAME"
    echo "  Status : sudo systemctl status $SERVICE_NAME"
    echo "  Logs   : journalctl -u $SERVICE_NAME -f"
    echo ""
    print_step "A reboot is required to activate all hardware interfaces."
    echo ""
    read -p "Reboot now? (Y/n): " -n 1 -r; echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        print_status "Rebooting in 5 seconds... (Ctrl+C to cancel)"
        sleep 5
        sudo reboot
    else
        print_warning "Remember to reboot before using hardware features."
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi