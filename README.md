# 🤖 WALL-E Robot Control System

**Updated: September 2025**

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Recent Changes & Updates](#recent-changes--updates)
3. [Hardware Configuration](#hardware-configuration)
4. [TB6600 Stepper Driver Configuration](#tb6600-stepper-driver-configuration)
5. [Motor & Movement Configuration](#motor--movement-configuration)
6. [Software Architecture](#software-architecture)
7. [Configuration Files](#configuration-files)
8. [Installation & Setup](#installation--setup)
9. [API Documentation](#api-documentation)
10. [Troubleshooting](#troubleshooting)

---

## System Overview

WALL-E is a comprehensive robotics platform featuring advanced motion control, scene management, and multi-client streaming capabilities:

- **Backend**: Python 3.9.13-based WebSocket server with shared serial management
- **Frontend**: PyQt6 application with real-time telemetry and scene editing
- **Camera System**: ESP32-CAM with HTTP proxy for multi-client streaming
- **Hardware Control**: Dual Pololu Maestro servo controllers, NEMA 23 stepper motor with TB6600 driver
- **Scene Management**: Audio-synchronized servo movements with 33+ predefined scenes
- **Safety Systems**: Emergency stop, limit switches, hardware monitoring, and graceful fallbacks

---

## Recent Changes & Updates

### ✅ **Major System Enhancements**

#### **1. Advanced Configuration Management System**
- **New File**: `config_manager.py`
- **Features**:
  - Hot-reload configuration changes without restart
  - Configuration validation and backup system
  - Automatic config file watching with debounced updates
  - Schema validation for hardware and scene configurations
  - Statistics tracking for configuration operations

#### **2. Enhanced TB6600 Stepper Motor Control**
- **File**: `modules/nema23_controller.py`
- **Key Improvements**:
  - **1/4 Microstepping Configuration**: Set for quieter operation at slow speeds
  - Configurable steps per revolution (default: 800 steps for 1.8° motors)
  - Adjustable lead screw pitch (default: 8mm per revolution)
  - Smooth acceleration/deceleration curves
  - Automatic homing with limit switch detection
  - Position tracking in both steps and centimeters

#### **3. Shared Serial Port Management**
- **File**: `modules/shared_serial_manager.py`
- **Features**:
  - Multiple Maestro controllers sharing single `/dev/ttyAMA0` port
  - Priority-based command queuing (Emergency → Background)
  - Thread-safe async communication with automatic retry logic
  - Batch command optimization for improved performance
  - Comprehensive statistics and error handling

#### **4. GPIO Compatibility Layer**
- **File**: `modules/gpio_compat.py`
- **Purpose**: Unified GPIO interface supporting multiple libraries
- **Libraries Supported**: RPi.GPIO, lgpio, gpiod with graceful fallbacks
- **Benefits**: Ensures compatibility across different Raspberry Pi configurations

#### **5. Enhanced Scene System**
- **File**: `scene_engine.py`
- **Improvements**:
  - 33+ predefined scenes with categories (Happy, Sad, Curious, etc.)
  - Audio-synchronized servo movements
  - Real-time scene testing and validation
  - Dynamic scene loading and saving
  - Category-based organization with emoji support

---

## Hardware Configuration

### **Core Processing**
- **Raspberry Pi 5**: Main controller with enhanced GPIO and processing power
- **Python 3.9.13**: Managed via pyenv for consistency and compatibility

### **Motion Control Hardware**

#### **Servo Control**
- **Pololu Maestro 18-channel (x2)**: 36 total servo channels
  - **Device #12 & #13** on shared `/dev/ttyAMA0` port (9600 baud)
  - Priority-based command queuing
  - Real-time position feedback and error detection

#### **Stepper Motor System**
- **NEMA 23 Stepper Motor**: Precision positioning system
- **TB6600 Stepper Driver**: Professional-grade microstep driver
- **Lead Screw**: 8mm pitch for precise linear movement
- **Limit Switch**: Hardware homing and safety boundaries

#### **Tank Drive (Configured)**
- **Sabertooth 2x60**: Dual motor controller for tank drive
- **Status**: Hardware configured but software integration pending

### **Sensors & Monitoring**
- **ADS1115 ADC**: 16-bit precision analog-to-digital converter
  - **Channel 0**: Battery voltage monitoring (with voltage divider)
  - **Channel 1 & 2**: Dual current sensors (ACS758) for power monitoring
- **GPIO Safety**: Limit switches, emergency stop, and status indicators

### **Media & Communication**
- **ESP32-CAM**: WiFi camera with configurable settings and streaming
- **Native Audio**: Built-in Raspberry Pi audio with pygame
- **SMB Network Sharing**: Easy file access and management

---

## TB6600 Stepper Driver Configuration

### **Current Configuration Settings**

The TB6600 driver is configured for **1/4 microstepping** to achieve quieter operation at slow speeds while maintaining good torque and precision.

#### **DIP Switch Settings for 1/4 Microstepping:**
```
SW1: OFF
SW2: OFF  
SW3: ON
```

#### **Current Settings (Adjust based on your NEMA 23 motor):**
```
SW4: OFF  
SW5: ON
SW6: ON
```
*Note: Verify current rating matches your specific NEMA 23 motor specifications*

### **Physical Connections**
```
TB6600 Driver → Raspberry Pi 5
├── PUL+ → GPIO 16 (Step Pin)
├── PUL- → GND
├── DIR+ → GPIO 12 (Direction Pin)  
├── DIR- → GND
├── ENA+ → GPIO 13 (Enable Pin)
└── ENA- → GND

TB6600 Driver → NEMA 23 Motor
├── A+ → Motor Phase A+
├── A- → Motor Phase A-
├── B+ → Motor Phase B+
└── B- → Motor Phase B-

Power Supply (24V recommended)
├── VCC → TB6600 VCC
└── GND → TB6600 GND
```

---

## Motor & Movement Configuration

### **Default Motor Specifications**
```python
# Current settings in nema23_controller.py
steps_per_revolution: int = 800      # For 1.8° stepper with 1/4 microstepping
lead_screw_pitch: float = 8.0        # 8mm per revolution
max_travel_cm: float = 20.0          # Maximum safe travel distance
default_position_cm: float = 5.0     # Default position from home
```

### **Speed & Acceleration Settings**
```python
homing_speed: int = 400              # Slow speed for accurate homing
normal_speed: int = 1000             # Standard movement speed  
max_speed: int = 1200                # Maximum movement speed
acceleration: int = 800              # Acceleration in steps/sec²
```

### **How to Modify Configuration**

#### **To Change Lead Screw Pitch:**
1. Edit `modules/nema23_controller.py`
2. Modify the `StepperConfig` class:
```python
lead_screw_pitch: float = 10.0    # Change from 8.0 to 10.0 for 10mm pitch
```

#### **To Change Steps Per Revolution:**
1. For different microstepping or motor:
```python
# Full step (1.8° motor): 200 steps
# Half step: 400 steps  
# Quarter step (current): 800 steps
# Eighth step: 1600 steps
steps_per_revolution: int = 1600  # Example for 1/8 microstepping
```

#### **Runtime Configuration Updates:**
You can also update settings via the configuration management system:
```python
# Update via config manager (requires restart)
config_updates = {
    "stepper_motor": {
        "steps_per_revolution": 1600,
        "lead_screw_pitch": 10.0,
        "normal_speed": 1500
    }
}
```

### **Position Calculation Formula**
```
Steps per cm = steps_per_revolution / (lead_screw_pitch / 10.0)
Current: 800 steps / (8mm / 10) = 1000 steps per cm

Position in cm = current_position_steps / steps_per_cm
Position in steps = position_cm * steps_per_cm
```

---

## Software Architecture

### **Main Application Structure**
```python
WALLEBackend (main.py)
├── ConfigurationManager (config_manager.py)
├── SharedSerialPortManager (modules/shared_serial_manager.py)
├── NEMA23Controller (modules/nema23_controller.py)
├── SceneEngine (scene_engine.py)
├── SafeTelemetrySystem (telemetry monitoring)
├── NativeAudioController (audio playback)
├── CameraProxy (modules/camera_proxy.py)
└── WebSocket Server (client communication)
```

### **Key Design Patterns**
1. **Shared Resource Management**: Single serial port shared between multiple Maestro controllers
2. **Priority Queue System**: Commands processed by priority (Emergency → Background)
3. **Observer Pattern**: Callbacks for hardware state changes and configuration updates
4. **Factory Pattern**: Hardware abstraction with graceful fallbacks
5. **Hot-Reload Pattern**: Configuration changes without system restart

---

## Configuration Files

### **hardware_config.json**
Complete hardware configuration with stepper motor settings:
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
    "gpio": {
      "motor_step_pin": 16,
      "motor_dir_pin": 12,
      "motor_enable_pin": 13,
      "limit_switch_pin": 26,
      "emergency_stop_pin": 25
    },
    "stepper_motor": {
      "steps_per_revolution": 800,
      "lead_screw_pitch": 8.0,
      "max_travel_cm": 20.0,
      "homing_speed": 400,
      "normal_speed": 1000,
      "max_speed": 1200,
      "acceleration": 800
    },
    "timing": {
      "telemetry_interval": 0.2,
      "servo_update_rate": 0.02
    }
  }
}
```

### **scenes_config.json**
- **Status**: ✅ 33+ predefined scenes
- **Categories**: Happy, Sad, Curious, Angry, Surprise, Love, Calm, Sound Effects, Misc, Idle, Sleepy
- **Features**: Audio synchronization, emoji support, category organization

### **camera_config.json**
- **ESP32-CAM Configuration**: URL, quality settings, resolution options
- **Stream Control**: Manual start/stop, bandwidth testing
- **Multi-client Support**: MJPEG rebroadcasting for multiple viewers

---

## Installation & Setup

sudo raspi-config
>Interfaces >Enable SSH
>Interfaces >Enable SPI
>Interfaces >Enable I2C

Finish

sudo apt update && sudo apt install -y git


### **Quick Start Installation**
```bash
# 1. Clone repository
git clone https://github.com/ShimmerNZ/DroidDeck-Backend.git
cd DroidDeck

# 2. Run automated installer
chmod +x install.sh
./install.sh

# 3. Start the system
./DroidDeck.sh
```

### **Installation Features**
- **Python 3.9.13** via pyenv management
- **Virtual environment** with all dependencies
- **SMB file sharing** for network file access
- **Audio system** with TTS samples and testing
- **GPIO/I2C interface** enabling and configuration
- **Systemd service** creation for auto-start

### **Manual Configuration Steps**

#### **1. Verify TB6600 DIP Switch Settings**
Ensure your TB6600 driver is configured for 1/4 microstepping:
- **SW1-3**: Configure for 1/4 step (OFF, OFF, ON)
- **SW4-6**: Set current rating to match your NEMA 23 motor

#### **2. Hardware Connections**
- Connect TB6600 to Raspberry Pi GPIO pins (16, 12, 13)
- Install limit switch on GPIO 26
- Connect emergency stop to GPIO 25
- Verify power supply (24V recommended for NEMA 23)

#### **3. Test Motor Configuration**
```bash
# Start system in test mode
python main.py --test-mode

# Test stepper motor homing
# Use WebSocket API to send test commands
```

---

## API Documentation

### **WebSocket API** (`ws://[IP]:8766`)

#### **Stepper Motor Control**
```json
{
  "type": "stepper",
  "command": "move_to_position",
  "position_cm": 15.0
}

{
  "type": "stepper", 
  "command": "home"
}

{
  "type": "stepper",
  "command": "get_status"
}
```

#### **Configuration Management**
```json
{
  "type": "config",
  "action": "reload",
  "config_name": "hardware"
}

{
  "type": "config",
  "action": "update",
  "config_name": "hardware",
  "updates": {
    "stepper_motor": {
      "normal_speed": 1500
    }
  }
}
```

#### **Scene Management**
```json
{
  "type": "scene",
  "emotion": "happy"
}

{
  "type": "get_scenes"
}

{
  "type": "test_scene",
  "scene": {...}
}
```

---

## Troubleshooting

### **TB6600 & Stepper Motor Issues**

#### **Motor Not Moving**
```bash
# Check GPIO connections
gpio readall

# Verify TB6600 power supply
# Check limit switch state
# Ensure proper DIP switch configuration
```

#### **Motor Movement Too Noisy**
- Verify 1/4 microstepping configuration (SW1-3: OFF, OFF, ON)
- Check motor current setting (SW4-6)
- Ensure proper motor wiring (A+, A-, B+, B-)

#### **Homing Issues**
```bash
# Check limit switch connection
# Verify GPIO 26 is properly configured
# Test limit switch manually
```

### **Configuration Issues**

#### **Hot-Reload Not Working**
```bash
# Check file watching permissions
# Verify configuration file syntax
# Review logs for validation errors
tail -f logs/walle_enhanced_backend.log
```

#### **Serial Communication Problems**
```bash
# Check serial devices
ls -la /dev/ttyAMA*

# Test serial connection
screen /dev/ttyAMA0 9600

# Verify Maestro device numbers (12 & 13)
```

### **System Diagnostics**
```bash
# Real-time system logs
tail -f logs/walle_enhanced_backend.log

# Hardware status check
i2cdetect -y 1

# Network services
netstat -tulpn | grep -E "(8766|8081)"

# Python environment verification
python --version  # Should be 3.9.13
source venv/bin/activate && pip list
```

---

## System Status & Features

### **✅ Production Ready**
- Shared serial communication with priority queuing
- NEMA 23 stepper control with TB6600 driver (1/4 microstepping)
- Configuration management with hot-reload
- ESP32-CAM streaming with multi-client support
- Scene management with 33+ predefined scenes
- Audio system with pygame integration
- Telemetry monitoring with ADS1115 ADC
- GPIO compatibility layer for different Pi models

### **⚠️ In Progress**
- Sabertooth tank drive integration
- Advanced safety systems (voltage/current based)
- Web-based configuration interface

### **❌ Future Enhancements**
- Bluetooth controller support
- AI behavior system
- Voice recognition
- Advanced computer vision

**Overall Assessment**: The system demonstrates professional-grade robotics programming with excellent hardware abstraction, safety considerations, and modular architecture. The TB6600 stepper configuration with 1/4 microstepping provides optimal balance of precision, torque, and noise reduction for the WALL-E application.