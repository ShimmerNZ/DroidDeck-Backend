# DroidDeck Backend - WALL-E Robot Control System

**Version:** 2.0  
**Platform:** Raspberry Pi 5  
**Python:** 3.9.13

Complete backend system for controlling WALL-E robots with dual servo controllers, stepper motors, cameras, and multi-client support.

---

## 📑 Table of Contents

1. [System Architecture](#system-architecture)
2. [Hardware Requirements](#hardware-requirements)
3. [Installation](#installation)
4. [Pinout & Wiring](#pinout--wiring)
5. [Configuration Files](#configuration-files)
6. [Bluetooth Controller Setup](#bluetooth-controller-setup)
7. [API Documentation](#api-documentation)
8. [Bottango Integration](#bottango-integration)
9. [Troubleshooting](#troubleshooting)

---

## System Architecture

### Overview

The DroidDeck backend is a modular Python system that runs on Raspberry Pi 5, providing real-time hardware control, WebSocket communication, and multi-client camera streaming.

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend Clients                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ PyQt6 App    │  │ Web Browser  │  │ Mobile App   │          │
│  │ (Steam Deck) │  │ (Any Device) │  │ (Optional)   │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         │                  │                  │                   │
│         └──────────────────┴──────────────────┘                   │
│                            │                                      │
│                     WebSocket (ws://pi:8766)                      │
│                     Socket.IO (http://pi:5000)                    │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────┴───────────────────────────────────┐
│                    DroidDeck Backend (main.py)                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │               WALLEBackend Controller                     │   │
│  │  • System state management (Normal/Failsafe/Emergency)   │   │
│  │  • Client connection handling                            │   │
│  │  • Message routing and broadcasting                      │   │
│  └──────┬──────────────────────────────────────────┬────────┘   │
│         │                                           │             │
│  ┌──────▼──────────┐                     ┌─────────▼──────────┐ │
│  │ WebSocket       │                     │ Web Server         │ │
│  │ Handler         │                     │ (webapp.py)        │ │
│  │ (Port 8766)     │                     │ Flask-SocketIO     │ │
│  │                 │                     │ (Port 5000)        │ │
│  │ • PyQt6 clients │                     │ • Web UI clients   │ │
│  │ • Raw WebSocket │                     │ • HTTP/SocketIO    │ │
│  └─────────────────┘                     └────────────────────┘ │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │             Core Service Modules                          │   │
│  │                                                           │   │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────┐ │   │
│  │  │ Hardware       │  │ Scene Engine   │  │ Audio      │ │   │
│  │  │ Service        │  │                │  │ Controller │ │   │
│  │  │                │  │ • 33+ scenes   │  │            │ │   │
│  │  │ • Servo ctrl   │  │ • Audio sync   │  │ • Pygame   │ │   │
│  │  │ • Stepper ctrl │  │ • Bottango     │  │ • TTS      │ │   │
│  │  │ • Safety sys   │  │   import       │  │ • FX       │ │   │
│  │  └────────────────┘  └────────────────┘  └────────────┘ │   │
│  │                                                           │   │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────┐ │   │
│  │  │ Telemetry      │  │ Bluetooth      │  │ Camera     │ │   │
│  │  │ System         │  │ Controller     │  │ Proxy      │ │   │
│  │  │                │  │                │  │            │ │   │
│  │  │ • ADC reading  │  │ • PS4/Xbox     │  │ • ESP32    │ │   │
│  │  │ • Voltage mon  │  │ • Auto-detect  │  │ • MJPEG    │ │   │
│  │  │ • Alert system │  │ • Calibration  │  │ • Multi-   │ │   │
│  │  │                │  │                │  │   client   │ │   │
│  │  └────────────────┘  └────────────────┘  └────────────┘ │   │
│  │                                                           │   │
│  │  ┌────────────────────────────────────────────────────┐ │   │
│  │  │ Shared Serial Manager                              │ │   │
│  │  │ • Priority queue (Emergency > High > Normal > Bg)  │ │   │
│  │  │ • Batch optimization                               │ │   │
│  │  │ • Thread-safe operations                           │ │   │
│  │  │ • Auto-retry logic                                 │ │   │
│  │  └────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────┴──────────────────────────────────────┐
│                      Hardware Layer                               │
│                                                                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ Maestro 1       │  │ Maestro 2       │  │ NEMA23 Motor    │  │
│  │ (Device #12)    │  │ (Device #13)    │  │ + TB6600        │  │
│  │                 │  │                 │  │                 │  │
│  │ • Ch 0-17       │  │ • Ch 0-17       │  │ • GPIO ctrl     │  │
│  │ • Head/Eyes/    │  │ • Arms/Tracks   │  │ • Limit switch  │  │
│  │   Neck          │  │                 │  │ • 1/4 microstep │  │
│  │                 │  │                 │  │                 │  │
│  │ /dev/ttyAMA0    │  │ /dev/ttyAMA0    │  │ GPIO 16,12,13   │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
│                                                                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ ADS1115 ADC     │  │ ESP32-CAM       │  │ Bluetooth       │  │
│  │                 │  │                 │  │ Controller      │  │
│  │ • Voltage sense │  │ • MJPEG stream  │  │                 │  │
│  │ • Current sense │  │ • 800x600       │  │ • PS4/Xbox/Pro  │  │
│  │ • I2C (0x48)    │  │ • WiFi          │  │ • USB/BT        │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

### Module Responsibilities

#### Core Controller (`main.py`)
- System initialization and lifecycle management
- Client connection tracking and broadcasting
- State machine (Normal, Failsafe, Emergency, Idle, Demo)
- Bottango animation auto-import on startup
- Graceful shutdown with cleanup

#### Hardware Service (`hardware_service.py`)
- Dual Maestro controller management via shared serial
- NEMA23 stepper motor control with TB6600 driver
- Emergency stop and failsafe systems
- Servo position/speed control with limits
- Hardware health monitoring

#### WebSocket Handler (`websocket_handler.py`)
- Message parsing and routing
- Command execution (servo, scene, emergency, etc.)
- Real-time telemetry broadcasting
- Frontend controller input handling
- Error responses and validation

#### Scene Engine (`scene_engine.py`)
- 33+ predefined animation scenes
- Audio-synchronized servo movements
- Bottango animation playback
- Category organization (Happy, Sad, Curious, etc.)
- Scene validation and testing

#### Telemetry System (`telemetry_system.py`)
- ADS1115 ADC reading (voltage/current)
- Servo position tracking from both Maestros
- Alert system for voltage/current thresholds
- Health data broadcasting to all clients

#### Shared Serial Manager (`shared_serial_manager.py`)
- Single `/dev/ttyAMA0` port shared between two Maestros
- Priority-based command queue
- Batch command optimization (70%+ efficiency)
- Thread-safe async operations
- Automatic retry with exponential backoff

#### Camera Proxy (`camera_proxy.py`)
- ESP32-CAM HTTP proxy with MJPEG rebroadcast
- Multi-client streaming support
- Bandwidth testing
- Manual stream control
- Port 8080 HTTP server

#### Web Server (`webapp.py`)
- Flask-SocketIO server on port 5000
- Web UI client support
- HTTP REST endpoints
- Socket.IO broadcasting
- Independent from main WebSocket server

#### Bluetooth Controller (`bluetooth_controller.py`)
- PS4, Xbox, Nintendo Pro controller support
- Auto-detection and connection
- Calibration system with persistent storage
- Real-time input processing
- Button/axis mapping configuration

#### Configuration Manager (`config_manager.py`)
- Hot-reload configuration without restart
- JSON schema validation
- Automatic config file watching
- Backup system with rollback
- Statistics tracking

---

## Hardware Requirements

### Core Components

| Component | Specification | Quantity | Notes |
|-----------|--------------|----------|-------|
| **Raspberry Pi 5** | 4GB+ RAM recommended | 1 | Main controller |
| **Pololu Maestro 18** | USB servo controller | 2 | Device #12 and #13 |
| **NEMA23 Stepper** | 1.8° bipolar motor | 1 | 800 steps with 1/4 microstepping |
| **TB6600 Driver** | 4.0A stepper driver | 1 | DIP switch configurable |
| **ADS1115** | 16-bit ADC I2C module | 1 | Address 0x48 |
| **ESP32-CAM** | WiFi camera module | 1 | MJPEG streaming |
| **Power Supply** | 5V 3A for Pi, 12-24V for servos | 2 | Separate supplies |

### Sensors

- **Voltage Divider**: 100kΩ/10kΩ for battery monitoring
- **ACS758 Current Sensor**: 2x for dual channel current monitoring
- **Limit Switch**: Normally-open for stepper homing
- **Emergency Stop**: Physical button (optional)

### Optional

- **Sabertooth 2x60**: Tank drive motor controller (configured but not active)
- **Bluetooth Controller**: PS4, Xbox One, or Nintendo Pro Controller

---

## Installation

### Prerequisites

- Fresh Raspberry Pi OS (64-bit recommended)
- Internet connection
- SSH enabled (for remote access)
- 16GB+ SD card

### Quick Installation

```bash
# 1. Clone repository
git clone https://github.com/yourusername/DroidDeck-Backend.git
cd DroidDeck-Backend

# 2. Run installer
chmod +x install.sh
./install.sh

# 3. Reboot to activate hardware interfaces
sudo reboot

# 4. After reboot, start backend
./DroidDeck.sh
```

### Installation Details

The `install.sh` script performs:

1. **System Update**: Updates all packages
2. **Dependencies**: Installs build tools, Python 3.9.13 via pyenv, audio libraries
3. **Python Environment**: Creates venv with all required packages
4. **Hardware Interfaces**: Enables I2C, SPI, UART in `/boot/firmware/config.txt`
5. **SMB Share**: Configures Samba for network file access
6. **Directory Structure**: Creates `configs/`, `logs/`, `audio/` folders

### Starting the Backend

The `DroidDeck.sh` script handles virtual environment activation, camera proxy, and main backend startup:

```bash
# Start backend with camera proxy (default)
./DroidDeck.sh

# Start without camera proxy
./DroidDeck.sh --no-camera

# Start camera proxy only (for testing)
./DroidDeck.sh --camera-only

# Show help
./DroidDeck.sh --help
```

**What the script does:**
1. Activates Python virtual environment
2. Verifies Python 3.9.13 is active
3. Starts camera proxy (if enabled) on port 8080
4. Loads joystick kernel modules for Bluetooth controllers
5. Starts main backend (WebSocket on 8766, Web UI on 5000)
6. Handles graceful shutdown with Ctrl+C

### Manual Configuration

If you prefer manual setup or need to customize the installation:

#### Enable Hardware Interfaces

```bash
sudo raspi-config
# Interface Options → I2C → Enable
# Interface Options → SPI → Enable  
# Interface Options → Serial Port → Enable
```

---

## Pinout & Wiring

### GPIO Pin Assignments

```
Raspberry Pi 5 GPIO Header
┌─────────────────────────────┐
│  3.3V  ●  ● 5V              │  Pin 1-2
│  SDA   ●  ● 5V              │  Pin 3-4  (I2C Data)
│  SCL   ●  ● GND             │  Pin 5-6  (I2C Clock)
│  GPIO4 ●  ● GPIO14 (TXD)    │  Pin 7-8  (UART TX)
│  GND   ●  ● GPIO15 (RXD)    │  Pin 9-10 (UART RX)
│  GPIO17●  ● GPIO18          │  Pin 11-12
│  GPIO27●  ● GND             │  Pin 13-14
│  GPIO22●  ● GPIO23          │  Pin 15-16
│  3.3V  ●  ● GPIO24          │  Pin 17-18
│  GPIO10●  ● GND             │  Pin 19-20
│  GPIO9 ●  ● GPIO25          │  Pin 21-22 (E-Stop)
│  GPIO11●  ● GPIO8           │  Pin 23-24
│  GND   ●  ● GPIO7           │  Pin 25-26
│  GPIO0 ●  ● GPIO1           │  Pin 27-28 (I2C ID EEPROM)
│  GPIO5 ●  ● GND             │  Pin 29-30
│  GPIO6 ●  ● GPIO12 (DIR)    │  Pin 31-32 (Stepper Dir)
│  GPIO13●  ● GND             │  Pin 33-34 (Stepper Enable)
│  GPIO19●  ● GPIO16 (STEP)   │  Pin 35-36 (Stepper Pulse)
│  GPIO26●  ● GPIO20          │  Pin 37-38 (Limit Switch)
│  GND   ●  ● GPIO21          │  Pin 39-40
└─────────────────────────────┘
```

### TB6600 Stepper Driver Wiring

```
TB6600 Driver
┌─────────────────────────┐
│  VCC  GND  ENA+ ENA-    │ ← Enable control
│   │    │    │    │      │
│   ↓    ↓    ↓    ↓      │
│  +24V GND  GPIO13 GND   │ (Pin 33, Pin 34)
│                          │
│  PUL+ PUL- DIR+ DIR-    │ ← Step/Direction control
│   │    │    │    │      │
│   ↓    ↓    ↓    ↓      │
│  GPIO16 GND GPIO12 GND  │ (Pin 36, Pin 32)
│                          │
│  A+  A-  B+  B-         │ ← Motor coils
│   │   │   │   │         │
│   └───┴───┴───┘         │
│       Motor             │
└─────────────────────────┘

DIP Switch Settings (1/4 Microstepping):
SW1: OFF OFF ON   (Microstep: 1/4)
SW2: ON  ON  ON   (Current: 4.0A)
```

### Pololu Maestro Controllers

Both controllers share `/dev/ttyAMA0` via Device Number addressing:

```
Maestro 1 (Device #12)          Maestro 2 (Device #13)
┌──────────────────────┐        ┌──────────────────────┐
│ USB (to Pi)          │        │ USB (to Pi)          │
│ VIN/GND (Power)      │        │ VIN/GND (Power)      │
│                      │        │                      │
│ Ch 0-17: Servos      │        │ Ch 0-17: Servos      │
│   • Head pan/tilt    │        │   • Arms (L/R)       │
│   • Eye servos       │        │   • Hands            │
│   • Neck servos      │        │   • Track motors     │
└──────────────────────┘        └──────────────────────┘
```

**Pololu Maestro Configuration:**
- Baud rate: 9600
- Serial mode: USB Dual Port
- Device numbers set via Maestro Control Center

[Pololu Maestro Documentation](https://www.pololu.com/docs/0J40)

### ADS1115 ADC (I2C)

```
ADS1115 Module
┌─────────────────┐
│ VDD → 3.3V      │ (Pin 1)
│ GND → GND       │ (Pin 6)
│ SCL → GPIO3     │ (Pin 5 - I2C Clock)
│ SDA → GPIO2     │ (Pin 3 - I2C Data)
│                 │
│ A0 → Voltage    │ (Battery via divider)
│ A1 → Current 1  │ (ACS758 sensor)
│ A2 → Current 2  │ (ACS758 sensor)
│ A3 → NC         │
└─────────────────┘

I2C Address: 0x48 (default)
```

### Voltage Divider Circuit

```
Battery+ ──┬── 100kΩ ──┬── 10kΩ ──┬── GND
           │           │          │
           │           └─ ADS1115 A0
           │
           └─ Servo Power Supply
```

**Calculation:**
- Input voltage range: 0-24V
- Output to ADC: 0-3.3V (safe for ADC)
- Voltage = ADC_reading * 11.0 (divider ratio)

### ESP32-CAM Wiring

```
ESP32-CAM Module
┌──────────────────┐
│ 5V   → 5V        │
│ GND  → GND       │
│ U0R  → (USB-TTL) │ (For programming)
│ U0T  → (USB-TTL) │ (For programming)
│ IO0  → GND       │ (Boot mode - program only)
│                  │
│ WiFi Antenna     │
│ Camera Module    │
└──────────────────┘

Programming: Use USB-TTL adapter (3.3V)
Runtime: Remove IO0-GND jumper
```

Upload `esp32cam.ino` via Arduino IDE:
- Board: "AI Thinker ESP32-CAM"
- Flash frequency: 80MHz
- Partition: Huge APP (3MB)

### Complete Wiring Summary

| Component | Connection | GPIO/Pin | Notes |
|-----------|-----------|----------|-------|
| **Stepper STEP** | TB6600 PUL+ | GPIO 16 | Pulse signal |
| **Stepper DIR** | TB6600 DIR+ | GPIO 12 | Direction control |
| **Stepper EN** | TB6600 ENA+ | GPIO 13 | Enable (LOW = on) |
| **Limit Switch** | NO switch | GPIO 26 | Pull-up, LOW = triggered |
| **Emergency Stop** | NO button | GPIO 25 | Pull-up, LOW = pressed |
| **Maestro 1 TX** | UART TX | GPIO 14 | Serial data out |
| **Maestro 1 RX** | UART RX | GPIO 15 | Serial data in |
| **ADS1115 SDA** | I2C Data | GPIO 2 | I2C communication |
| **ADS1115 SCL** | I2C Clock | GPIO 3 | I2C communication |

---

## Configuration Files

All configuration files are in JSON format and support hot-reload (changes detected automatically without restart).

### `hardware_config.json`

Complete hardware setup including stepper motor parameters:

```json
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
        "hardware": {
            "stepper_motor": {
                "steps_per_revolution": 800,
                "homing_speed": 1600,
                "normal_speed": 4000,
                "max_speed": 4800,
                "acceleration": 3200
            }
        }
    }
}
```

**Key Parameters:**

- **steps_per_revolution**: 800 for 1/4 microstepping (200 steps × 4)
- **homing_speed**: Steps/sec during limit switch homing
- **normal_speed**: Default movement speed (steps/sec)
- **max_speed**: Maximum safe speed
- **acceleration**: Ramp-up/down rate (steps/sec²)

### `servo_config.json`

Per-channel servo limits and home positions:

```json
{
  "m1_ch0": {
    "home": 1496,
    "min": 992,
    "max": 2000,
    "name": "Head Pan",
    "accel": 8
  },
  "m1_ch1": {
    "home": 1504,
    "min": 992,
    "max": 2000,
    "name": "Head Tilt"
  }
}
```

- **home**: Neutral position (µs pulse width)
- **min/max**: Software limits
- **accel**: Acceleration limit (0-255, optional)
- **name**: Human-readable label

### `camera_config.json`

ESP32-CAM streaming configuration:

```json
{
    "camera": {
        "esp32_url": "http://192.168.1.100",
        "resolution": "SVGA",
        "quality": 12,
        "brightness": 0,
        "contrast": 0,
        "saturation": 0
    },
    "stream": {
        "auto_start": true,
        "rebroadcast_port": 8080,
        "max_clients": 10
    }
}
```

### `scenes_config.json`

Animation scene library (33+ scenes):

```json
{
  "happy_beep": {
    "label": "Happy Beep",
    "emoji": "😊",
    "duration": 2.5,
    "audio_file": "beep_happy.wav",
    "audio_enabled": true,
    "categories": ["Happy", "Sound Effects"],
    "servo_moves": [
      {
        "channel": "m1_ch0",
        "timestamps": [0.0, 1.0, 2.0],
        "positions": [1500, 1800, 1500],
        "speeds": [50, 50, 30]
      }
    ]
  }
}
```

### `controller_config.json`

Bluetooth controller button/axis mappings:

```json
{
  "left_stick_x": {
    "action": "servo_control",
    "channel": "m1_ch0",
    "sensitivity": 1.0,
    "invert": false,
    "deadzone": 0.1
  },
  "button_a": {
    "action": "scene_trigger",
    "scene_name": "happy_beep"
  }
}
```

### `controller_calibration.json`

Auto-generated calibration data (persists across restarts):

```json
{
  "left_stick_x": {
    "min": -32768,
    "max": 32767,
    "center": 0,
    "deadzone": 0.05
  }
}
```

### Configuration Tuning

**Stepper Motor:**
- Increase `normal_speed` for faster movement (max 4800)
- Increase `acceleration` for snappier response (test for smoothness)
- Adjust `homing_speed` if limit switch detection is unreliable

**Servo Response:**
- Lower `servo_update_rate` (e.g., 0.01) for smoother interpolation
- Adjust per-channel `accel` values for speed/smoothness balance

**Telemetry:**
- Increase `telemetry_interval` (e.g., 0.5) to reduce CPU load
- Decrease (e.g., 0.1) for faster UI updates

---

## Bluetooth Controller Setup

### Supported Controllers

- Sony PlayStation 4 DualShock
- Microsoft Xbox One/Series Controller
- Nintendo Switch Pro Controller
- Generic USB/Bluetooth gamepads

### Initial Pairing

#### Via Bluetooth (Recommended)

```bash
# 1. Make Pi discoverable
bluetoothctl
power on
agent on
default-agent
discoverable on

# 2. Put controller in pairing mode:
#    PS4: Hold SHARE + PS button until light flashes
#    Xbox: Hold pairing button until light flashes
#    Switch Pro: Hold SYNC button until lights scroll

# 3. Scan and pair
scan on
# Wait for controller MAC address
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
exit
```

#### Via USB

1. Plug controller into Raspberry Pi USB port
2. Controller detected automatically via pygame
3. No pairing required

### Calibration

Calibration data saves automatically to `controller_calibration.json`:

```python
# Calibration happens automatically on first use
# Or trigger manual calibration via frontend:
{
    "type": "controller_calibrate",
    "axis": "left_stick_x"
}
```

### Button Mapping

Edit `controller_config.json` to customize:

```json
{
  "button_a": {
    "action": "scene_trigger",
    "scene_name": "happy_beep"
  },
  "button_b": {
    "action": "emergency_stop"
  },
  "left_stick_x": {
    "action": "servo_control",
    "channel": "m1_ch0",
    "sensitivity": 1.5,
    "invert": false
  },
  "dpad_up": {
    "action": "stepper_move",
    "direction": "forward",
    "speed": 2000
  }
}
```

**Action Types:**
- `servo_control`: Direct servo position control
- `scene_trigger`: Play animation scene
- `emergency_stop`: Emergency stop all motors
- `stepper_move`: Control NEMA23 stepper
- `custom`: Execute custom command

### Troubleshooting Controllers

```bash
# Check connected devices
lsusb  # USB controllers
hcitool con  # Bluetooth controllers

# Test controller input
jstest /dev/input/js0

# Pygame device info
python -c "import pygame; pygame.joystick.init(); print(pygame.joystick.get_count())"

# Reconnect Bluetooth
sudo systemctl restart bluetooth
bluetoothctl connect XX:XX:XX:XX:XX:XX
```

---

## API Documentation

### WebSocket API (Port 8766)

Primary API for PyQt6 frontend and programmatic control.

#### Connection

```javascript
const ws = new WebSocket('ws://192.168.1.100:8766');

ws.onopen = () => {
    console.log('Connected to DroidDeck backend');
};

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleMessage(data);
};
```

#### Message Format

All messages are JSON with `type` field:

```json
{
    "type": "command_name",
    "param1": "value1",
    "param2": "value2"
}
```

---

### Control Commands

#### Servo Control

```json
{
    "type": "servo",
    "channel": "m1_ch0",
    "position": 1500,
    "speed": 50
}
```

**Parameters:**
- `channel`: Format `m{maestro}_ch{channel}` (e.g., `m1_ch0`, `m2_ch17`)
- `position`: Pulse width in microseconds (992-2000)
- `speed`: Servo speed 0-255 (optional)

**Response:**
```json
{
    "type": "servo_ack",
    "channel": "m1_ch0",
    "position": 1500,
    "success": true
}
```

#### Scene Playback

```json
{
    "type": "scene",
    "scene_name": "happy_beep",
    "blocking": false
}
```

**Parameters:**
- `scene_name`: Scene identifier from `scenes_config.json`
- `blocking`: Wait for completion before accepting new commands (optional)

**Response:**
```json
{
    "type": "scene_started",
    "scene_name": "happy_beep",
    "duration": 2.5
}
```

```json
{
    "type": "scene_complete",
    "scene_name": "happy_beep"
}
```

#### Emergency Stop

```json
{
    "type": "emergency_stop"
}
```

Immediately stops all servos, steppers, and scenes.

**Response:**
```json
{
    "type": "emergency_stop_ack",
    "timestamp": 1234567890.123
}
```

#### Stepper Motor Control

```json
{
    "type": "stepper_move",
    "distance_cm": 5.0,
    "speed": 2000
}
```

**Parameters:**
- `distance_cm`: Distance in centimeters (negative = reverse)
- `speed`: Steps/second (default: configured `normal_speed`)

**Response:**
```json
{
    "type": "stepper_move_started",
    "distance_cm": 5.0,
    "estimated_duration": 2.1
}
```

```json
{
    "type": "stepper_move_complete",
    "distance_cm": 5.0,
    "actual_steps": 6250
}
```

#### Stepper Homing

```json
{
    "type": "stepper_home"
}
```

Moves stepper until limit switch triggers, then sets position to 0.

---

### Query Commands

#### Get Scene List

```json
{
    "type": "get_scene_list"
}
```

**Response:**
```json
{
    "type": "scene_list",
    "scenes": [
        {
            "name": "happy_beep",
            "label": "Happy Beep",
            "emoji": "😊",
            "duration": 2.5,
            "categories": ["Happy", "Sound Effects"],
            "audio_enabled": true
        }
    ]
}
```

#### Get Telemetry

```json
{
    "type": "get_telemetry"
}
```

**Response:**
```json
{
    "type": "telemetry",
    "timestamp": 1234567890.123,
    "voltage": 12.3,
    "current_ch1": 1.2,
    "current_ch2": 0.8,
    "maestro1": {
        "0": 1500,
        "1": 1600
    },
    "maestro2": {
        "0": 1400
    },
    "stepper": {
        "position_steps": 1000,
        "position_cm": 1.25,
        "homed": true,
        "state": "idle"
    }
}
```

#### Get Failsafe Status

```json
{
    "type": "get_failsafe_status"
}
```

**Response:**
```json
{
    "type": "failsafe_status",
    "failsafe_active": false,
    "state": "normal",
    "nema": {
        "enabled": true,
        "homed": true,
        "state": "idle"
    },
    "track_channels": [12, 13]
}
```

#### Get Controller Config

```json
{
    "type": "get_controller_config"
}
```

**Response:**
```json
{
    "type": "controller_config",
    "config": {
        "left_stick_x": {
            "action": "servo_control",
            "channel": "m1_ch0",
            "sensitivity": 1.0
        }
    }
}
```

---

### Failsafe Controls

#### Enable Failsafe

```json
{
    "type": "enable_failsafe"
}
```

Disables NEMA23 motor and specified track channels (from controller config).

**Response:**
```json
{
    "type": "failsafe_enabled",
    "disabled_channels": [12, 13],
    "nema_disabled": true
}
```

#### Disable Failsafe

```json
{
    "type": "disable_failsafe"
}
```

Re-enables motors (requires explicit action, safety by design).

**Response:**
```json
{
    "type": "failsafe_disabled",
    "nema_enabled": true
}
```

---

### Broadcast Messages

Backend automatically broadcasts these messages to all connected clients:

#### Telemetry Updates

Sent every `telemetry_interval` seconds (default 0.2s):

```json
{
    "type": "telemetry",
    "timestamp": 1234567890.123,
    "voltage": 12.3,
    "current_ch1": 1.2,
    "current_ch2": 0.8,
    "maestro1": { "0": 1500 },
    "maestro2": { "0": 1400 }
}
```

#### Scene Events

```json
{
    "type": "scene_started",
    "scene_name": "happy_beep"
}
```

```json
{
    "type": "scene_complete",
    "scene_name": "happy_beep"
}
```

#### Controller Input

When Bluetooth controller is active:

```json
{
    "type": "controller_input",
    "axes": {
        "left_stick_x": 0.5,
        "left_stick_y": -0.3
    },
    "buttons": {
        "button_a": true,
        "button_b": false
    }
}
```

---

### Socket.IO API (Port 5000)

Web browser client API using Flask-SocketIO.

#### Connection

```javascript
const socket = io('http://192.168.1.100:5000');

socket.on('connect', () => {
    console.log('Connected to web server');
});

socket.on('backend_message', (data) => {
    handleMessage(data);
});
```

#### Send Commands

```javascript
socket.emit('backend_command', {
    type: 'servo',
    channel: 'm1_ch0',
    position: 1500
});
```

Uses same message format as WebSocket API.

#### HTTP Endpoints

```
GET  /                    # Web UI
GET  /health              # Backend status check
POST /api/servo           # Direct servo control
POST /api/scene           # Scene playback
POST /api/emergency_stop  # Emergency stop
GET  /api/scenes          # List all scenes
GET  /api/telemetry       # Current telemetry
```

---

## Bottango Integration

Bottango is a professional animation software for servo control. DroidDeck includes automatic import/conversion.

### Workflow

1. **Create Animation in Bottango:**
   - Design servo movements with visual timeline
   - Use cubic bezier curves for smooth motion
   - Export as JSON

2. **Export from Bottango:**
   - File → Export → "DroidDeck Format" (or generic JSON)
   - Save as `animation_name.json`

3. **Import to DroidDeck:**
   - Place exported JSON files in `./bottango_imports/` directory
   - On backend startup, files are auto-converted to DroidDeck scenes
   - Converted scenes appear in `./scenes/` directory
   - Source files in `bottango_imports/` are deleted after successful conversion

4. **Play Animation:**
   - Scenes automatically registered in `scenes_registry.json`
   - Play via WebSocket: `{"type": "scene", "scene_name": "animation_name"}`
   - Or via frontend scene browser

### Bottango Export Format

DroidDeck expects Bottango exports with curve commands:

```json
{
  "commands": [
    {
      "type": "sC",
      "channel": 0,
      "start_time": 0,
      "duration": 1000,
      "start_pos": 1500,
      "p1": 100,
      "p2": 0,
      "p3": 1800,
      "p4": -50,
      "p5": 0
    }
  ]
}
```

### Conversion Details

The `bottango_converter.py` module:

- **Cubic Bezier Interpolation**: Preserves animator's timing and easing
- **Multi-Channel Support**: Handles 36 channels (both Maestros)
- **Audio Sync**: Optional audio file association
- **Category Tagging**: Auto-categorizes based on filename/metadata
- **Validation**: Checks for valid channel ranges and timing

### Manual Conversion

```bash
# Convert single file
python bottango_converter.py animation.json

# Convert all files in directory
python bottango_converter.py bottango_imports/

# Specify output directory
python bottango_converter.py input.json --output scenes/
```

### Troubleshooting Bottango

**Issue: Animations play too fast/slow**
- Check `duration` values in exported JSON
- Bottango export uses milliseconds
- DroidDeck scenes use seconds

**Issue: Servos don't move smoothly**
- Ensure bezier curve data is present (`p1`-`p5` parameters)
- Try exporting with higher curve density
- Check `servo_update_rate` in hardware config

**Issue: Import fails**
- Verify JSON syntax with `python -m json.tool file.json`
- Check channel numbers are 0-17
- Ensure position values are 992-2000 µs

---

## Troubleshooting

### Backend Won't Start

**Check Python version:**
```bash
python --version  # Should be 3.9.13
pyenv versions    # Verify pyenv setup
```

**Check virtual environment:**
```bash
source venv/bin/activate
pip list  # Verify all dependencies installed
```

**Check port conflicts:**
```bash
sudo netstat -tulpn | grep :8766  # WebSocket port
sudo netstat -tulpn | grep :5000  # Web server port
```

### Hardware Not Responding

**Maestro Controllers:**
```bash
# Check USB connection
lsusb | grep "Pololu"

# Check serial port
ls -l /dev/ttyAMA0
sudo chmod 666 /dev/ttyAMA0

# Test serial communication
python -c "import serial; s=serial.Serial('/dev/ttyAMA0', 9600); print('OK')"
```

**I2C (ADS1115):**
```bash
# Check I2C bus
sudo i2cdetect -y 1
# Should show 0x48 (ADC address)

# Enable I2C
sudo raspi-config
# Interface Options → I2C → Enable
```

**GPIO:**
```bash
# Check GPIO access
gpio readall

# Add user to gpio group
sudo usermod -a -G gpio $USER
sudo usermod -a -G i2c $USER
```

### Servo Issues

**Servo doesn't move:**
- Verify channel in `servo_config.json`
- Check power supply voltage (5-6V for most servos)
- Test with Maestro Control Center software
- Verify `min`/`max` limits allow movement

**Servo jitters:**
- Lower `servo_update_rate` in hardware config
- Add servo `accel` limit in servo config
- Check power supply current capacity
- Add capacitor (470µF-1000µF) near servos

**Incorrect position:**
- Calibrate servo home position in `servo_config.json`
- Use Maestro Control Center to find correct µs values
- Verify servo travel range (typically 900-2100µs)

### Stepper Motor Issues

**Motor not moving:**
```bash
# Check GPIO pins
gpio readall | grep -E "GPIO12|GPIO13|GPIO16"

# Test manually
echo "16" > /sys/class/gpio/export
echo "out" > /sys/class/gpio/gpio16/direction
echo "1" > /sys/class/gpio/gpio16/value  # Pulse
```

**Motor vibrates but doesn't turn:**
- Verify DIP switch settings on TB6600
- Check wiring (A+, A-, B+, B-)
- Increase current limit (SW2 switches)
- Verify 24V power supply

**Limit switch not working:**
```bash
# Test switch
gpio readall | grep "GPIO26"
# Should change state when pressed

# Check wiring
gpio -g mode 26 up  # Enable pull-up
gpio -g read 26     # Should be 1 when open, 0 when closed
```

### Camera Issues

**Cannot connect to ESP32-CAM:**
- Verify WiFi connection: `ping 192.168.1.100`
- Check camera URL in `camera_config.json`
- Test directly: `curl http://192.168.1.100`
- Reflash ESP32-CAM with `esp32cam.ino`

**Poor video quality:**
- Increase `quality` parameter (lower number = higher quality)
- Change `resolution` (SVGA, XGA, HD)
- Improve WiFi signal strength
- Reduce `max_clients` for better bandwidth

**Stream lags:**
- Lower resolution to SVGA (800x600)
- Increase `quality` parameter to reduce bandwidth
- Use wired Ethernet for Pi if possible

### Network Issues

**Cannot access from frontend:**
```bash
# Check backend is running
ps aux | grep python

# Check firewall
sudo ufw status
sudo ufw allow 8766/tcp  # WebSocket
sudo ufw allow 5000/tcp  # Web server
sudo ufw allow 8080/tcp  # Camera proxy

# Check IP address
hostname -I
```

**SMB share not accessible:**
```bash
# Check Samba status
sudo systemctl status smbd

# Restart Samba
sudo systemctl restart smbd nmbd

# Test connection
smbclient -L //localhost -U guest
```

### Performance Issues

**High CPU usage:**
- Increase `telemetry_interval` (e.g., 0.5s)
- Increase `servo_update_rate` (e.g., 0.05s)
- Disable camera stream when not needed
- Reduce number of active scenes

**Memory leaks:**
```bash
# Monitor memory
watch -n 1 free -h

# Check process memory
ps aux | grep python | awk '{print $6}'

# Restart backend if memory grows continuously
```

**Slow WebSocket response:**
- Check for network packet loss: `ping -c 100 192.168.1.100`
- Verify no CPU throttling: `vcgencmd measure_temp`
- Cool Raspberry Pi if temp > 70°C
- Close unused frontend clients

### Log Files

```bash
# View backend logs
tail -f logs/droiddeck_backend.log

# Search for errors
grep ERROR logs/droiddeck_backend.log

# Clear old logs
rm logs/*.log
```

### Common Error Messages

**"Serial port permission denied":**
```bash
sudo chmod 666 /dev/ttyAMA0
sudo usermod -a -G dialout $USER
```

**"I2C address not found":**
```bash
# Check connections
sudo i2cdetect -y 1
# Verify ADS1115 at 0x48
```

**"WebSocket connection failed":**
- Check firewall allows port 8766
- Verify backend is running: `ps aux | grep main.py`
- Test with curl: `curl http://localhost:8766`

**"Config file not found":**
```bash
# Verify file exists
ls -l configs/hardware_config.json

# Create from template if missing
cp configs/hardware_config.json.example configs/hardware_config.json
```

---

## Support & Resources

- **Pololu Maestro Guide:** https://www.pololu.com/docs/0J40
- **TB6600 Datasheet:** Search "TB6600 stepper driver manual"
- **ADS1115 Library:** https://github.com/adafruit/Adafruit_CircuitPython_ADS1x15
- **GPIO Pinout:** https://pinout.xyz
- **Bottango Software:** https://bottango.com

---

**Built with ❤️ for the WALL-E community**