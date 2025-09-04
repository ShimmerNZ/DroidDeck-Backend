# 🤖 WALL-E Robot Control System Documentation

**Updated: December 2024 - Comprehensive System Review**

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture) 
3. [Implementation Status](#implementation-status)
4. [Hardware Components](#hardware-components)
5. [Software Architecture](#software-architecture)
6. [API Documentation](#api-documentation)
7. [Configuration Files](#configuration-files)
8. [Installation & Setup](#installation--setup)
9. [Code Organization Recommendations](#code-organization-recommendations)
10. [Future Improvements](#future-improvements)
11. [Troubleshooting](#troubleshooting)

---

## System Overview

WALL-E is a comprehensive robotics platform featuring:

- **Backend**: Python-based WebSocket server with shared serial management
- **Frontend**: PyQt6 application (referenced but not included in current files)
- **Camera System**: ESP32-CAM with HTTP proxy for multi-client streaming
- **Hardware Control**: Dual Pololu Maestro servo controllers, NEMA 23 stepper motor, ADC sensors
- **Scene Management**: Audio-synchronized servo movements with configurable scenes
- **Safety Systems**: Emergency stop, failsafe modes, hardware monitoring

**Current Python Version**: 3.9.13 (managed via pyenv)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Steam Deck / PC / Mobile Device                              │
│ ┌─────────────────────────────────────────────────────┐      │
│ │ PyQt6 Frontend Application (wall_e_frontend.py)   │      │
│ │ ├── Home Screen (Scene/Emotion Control)           │      │
│ │ ├── Camera Feed (MediaPipe Wave Detection)        │      │
│ │ ├── Health Monitor (Real-time Telemetry)          │      │
│ │ ├── Servo Configuration & Testing                 │      │
│ │ └── Controller Mapping                            │      │
│ └─────────────────────────────────────────────────────┘      │
└────────────────────┬─────────────────────────────────────────┘
                     │ WebSocket (ws://IP:8766)
                     │ HTTP Camera Stream (http://IP:8081)
┌────────────────────┴─────────────────────────────────────────┐
│ Raspberry Pi 5 - WALL-E Backend                             │
│ ┌─────────────────────────────────────────────────────┐      │
│ │ main.py - Core Backend System                     │      │
│ │ ├── WALLEBackend (Main orchestrator)              │      │
│ │ ├── WebSocket Server (asyncio-based)              │      │
│ │ ├── Scene Engine (Audio + Servo coordination)     │      │
│ │ ├── SafeTelemetrySystem (Hardware monitoring)     │      │
│ │ ├── NativeAudioController (pygame-based)          │      │
│ │ └── SafeMotorController (GPIO wrapper)            │      │
│ └─────────────────────────────────────────────────────┘      │
│ ┌─────────────────────────────────────────────────────┐      │
│ │ modules/shared_serial_manager.py                   │      │
│ │ ├── SharedSerialPortManager                       │      │
│ │ ├── MaestroControllerShared                       │      │
│ │ └── Priority-based Command Queuing                │      │
│ └─────────────────────────────────────────────────────┘      │
│ ┌─────────────────────────────────────────────────────┐      │
│ │ modules/nema23_controller.py                       │      │
│ │ ├── NEMA23Controller (Stepper motor control)      │      │
│ │ ├── StepperControlInterface (WebSocket interface) │      │
│ │ └── Homing & Positioning System                   │      │
│ └─────────────────────────────────────────────────────┘      │
│ ┌─────────────────────────────────────────────────────┐      │
│ │ modules/camera_proxy.py                            │      │
│ │ ├── CameraProxy (ESP32 stream handler)            │      │
│ │ ├── Flask HTTP Server (port 8081)                 │      │
│ │ └── Multi-client MJPEG rebroadcasting             │      │
│ └─────────────────────────────────────────────────────┘      │
└───────────┬────────────────────┬──────────────┬──────────────┘
            │ Serial             │ I2C          │ Network
┌───────────┴────────┐ ┌─────────┴─────────┐ ┌───┴────┐
│ Maestro 1 & 2     │ │ ADS1115 ADC       │ │ESP32CAM│
│ (Shared /dev/ttyAMA0) │ (Battery/Current) │ │ Stream │
│ Device #12 & #13   │ │ Voltage Dividers  │ │        │
└────────────────────┘ └───────────────────┘ └────────┘
┌────────────────────┐ ┌───────────────────┐
│ TB6600 Stepper     │ │ GPIO Safety       │
│ Driver (NEMA 23)   │ │ ├── Limit Switch  │
│ ├── Step Pin: 16   │ │ ├── E-Stop: 25   │
│ ├── Dir Pin: 12    │ │ └── Enable: 13   │
│ └── Enable Pin: 13 │ └───────────────────┘
└────────────────────┘
```

---

## Implementation Status

### ✅ **Fully Implemented Components**

#### **1. Shared Serial Communication System**
- **File**: `modules/shared_serial_manager.py`
- **Status**: ✅ Complete and Production Ready
- **Features**:
  - Multiple Maestro controllers sharing single serial port
  - Priority-based command queuing (EMERGENCY → BACKGROUND)
  - Thread-safe async communication
  - Automatic retry logic and error handling
  - Comprehensive statistics and monitoring
  - Device registration and management

#### **2. NEMA 23 Stepper Motor Control**
- **File**: `modules/nema23_controller.py`  
- **Status**: ✅ Complete and Production Ready
- **Features**:
  - Automatic homing with limit switch detection
  - Smooth acceleration/deceleration curves
  - Position tracking in steps and centimeters
  - Safety limits and soft boundaries
  - WebSocket control interface
  - Emergency stop functionality

#### **3. Camera Streaming System**
- **File**: `modules/camera_proxy.py`
- **Status**: ✅ Complete with Stream Control
- **Features**:
  - ESP32-CAM proxy server (Flask-based)
  - Manual start/stop stream control
  - Real-time camera settings adjustment
  - Multi-client MJPEG rebroadcasting
  - Bandwidth testing endpoint
  - Connection health monitoring

#### **4. Scene Management System**
- **File**: `main.py` (SceneEngine class)
- **Status**: ✅ Complete with Dynamic Loading
- **Features**:
  - JSON-based scene configuration
  - Audio-synchronized servo movements
  - Frontend scene editor integration
  - Real-time scene testing
  - Category-based organization

#### **5. Telemetry & Health Monitoring**
- **File**: `main.py` (SafeTelemetrySystem class)
- **Status**: ✅ Complete with Real Hardware Support
- **Features**:
  - Real ADC readings (ADS1115) with simulation fallback
  - Battery voltage monitoring with alarms
  - Dual current sensing channels
  - CPU, memory, temperature monitoring
  - Hardware availability detection

#### **6. Audio System**
- **File**: `main.py` (NativeAudioController class)
- **Status**: ✅ Complete Native Implementation
- **Features**:
  - pygame-based audio playback
  - Multi-format support (MP3, WAV, OGG, M4A)
  - Volume control and playlist management
  - Scene-synchronized audio
  - File scanning and management

### ⚠️ **Partially Implemented Components**

#### **1. WebSocket Message Handling**
- **Location**: `main.py` (handle_client_message method)
- **Status**: ⚠️ Functional but Could Be Modularized
- **Current State**: Large switch-case method handling all message types
- **Recommendation**: Extract to separate WebSocket handler module


### ❌ **Missing/To Be Completed Components**


#### **2. Advanced Safety Systems**
- **Status**: ❌ Basic emergency stop only
- **Missing**: 
  - Voltage-based automatic shutdown
  - Current limiting
  - Temperature-based throttling
  - Comprehensive failsafe modes

#### **3. Bluetooth Controller Support**
- **Status**: ❌ Not implemented
- **Required**: Direct Steam Deck controller integration
- **Alternative**: Currently relies on frontend for control

#### **4. Configuration Management System**
- **Status**: ❌ Basic JSON loading only
- **Missing**:
  - Hot-reload of configurations
  - Configuration validation
  - Runtime configuration updates
  - Backup/restore system

#### **5. Advanced Scene Timeline Editor**
- **Status**: ❌ Basic scene support only
- **Missing**:
  - Complex multi-step sequences
  - Timeline-based editing
  - Scene chaining and transitions
  - Variable timing control

---

## Hardware Components

### **Core Processing**
- **Raspberry Pi 5**: Main controller with enhanced GPIO
- **Python 3.9.13**: Managed via pyenv for consistency

### **Motion Control**
- **Pololu Maestro 18-channel (x2)**: 36 total servo channels
  - Device #12 & #13 on shared `/dev/ttyAMA0` port
  - Priority-based command queuing
  - Real-time position feedback
- **TB6600 Stepper Driver**: NEMA 23 motor control
  - Step: GPIO 16, Dir: GPIO 12, Enable: GPIO 13
  - Automatic homing and positioning
- **Sabertooth 2x60**: Tank drive motor controller (configured but not implemented)

### **Sensors & Monitoring**
- **ADS1115 ADC**: 16-bit current and voltage sensing
  - Channel 0: Battery voltage (with voltage divider)
  - Channel 1 & 2: Dual current sensors (ACS758)
- **GPIO Safety**: Limit switches and emergency stop

### **Media & Communication**
- **ESP32-CAM**: WiFi camera with settings control
- **Native Audio**: Built-in Pi audio with pygame
- **SMB Sharing**: Network file access for easy management

---

## Software Architecture

### **Main Application Structure**

```python
# main.py - Core Architecture
WALLEBackend
├── SharedSerialPortManager (hardware communication)
├── SceneEngine (audio + servo coordination)  
├── SafeTelemetrySystem (sensor monitoring)
├── NativeAudioController (audio playback)
├── SafeMotorController (GPIO wrapper)
├── NEMA23Controller (stepper motor)
└── WebSocket Server (client communication)
```

### **Key Design Patterns**

1. **Shared Resource Management**: Single serial port shared between multiple Maestro controllers
2. **Priority Queue System**: Commands processed by priority (Emergency → Background)
3. **Async/Await**: Non-blocking communication and telemetry
4. **Observer Pattern**: Callbacks for hardware state changes
5. **Factory Pattern**: Hardware abstraction with graceful fallbacks

### **Thread Safety**
- Threading locks for shared resources
- Async queues for command processing
- Thread-safe callbacks for real-time updates

---

## API Documentation

### **WebSocket API** (`ws://[IP]:8766`)

#### **Core Control Messages**

**Servo Control**
```json
{
  "type": "servo",
  "channel": "m1_ch5",
  "pos": 1500,
  "priority": "normal"
}
```

**Scene Execution**
```json
{
  "type": "scene", 
  "emotion": "happy"
}
```

**Stepper Motor Control** 
```json
{
  "type": "stepper",
  "command": "move_to_position",
  "position_cm": 15.0
}
```

**Scene Management**
```json
{
  "type": "get_scenes"
}
{
  "type": "save_scenes",
  "scenes": [...]
}
```

#### **Telemetry Broadcast**
```json
{
  "type": "telemetry",
  "timestamp": 1234567890.123,
  "cpu": 45.2,
  "memory": 62.1,
  "temperature": 48.5,
  "battery_voltage": 14.8,
  "current": 5.2,
  "current_a1": 2.1,
  "maestro1": {...},
  "maestro2": {...},
  "stepper_motor": {...},
  "shared_managers": {...}
}
```

### **Camera Proxy API** (`http://[IP]:8081`)

**Stream Control**
```bash
POST /stream/start    # Start camera stream
POST /stream/stop     # Stop camera stream  
GET  /stream/status   # Get stream status
```

**Camera Settings**
```bash
GET  /camera/settings                    # Get all settings
POST /camera/settings                    # Update multiple settings
POST /camera/setting/resolution?value=5  # Update single setting
```

---

## Configuration Files

### **hardware_config.json**
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
    }
  }
}
```

### **scenes_config.json**
- **Status**: ✅ Comprehensive scene definitions
- **Features**: 33+ predefined scenes with categories
- **Categories**: Happy, Sad, Curious, Angry, Surprise, Love, Calm, Sound Effect, Misc, Idle, Sleepy

### **camera_config.json** 
- **ESP32 URL configuration**
- **Quality and resolution settings**
- **Connection timeout parameters**

---

## Installation & Setup

### **Automated Installation**
```bash
# 1. Clone and setup
git clone <repository> ~/wall-e-robot
cd ~/wall-e-robot

# 2. Run comprehensive installer  
chmod +x install.sh
./install.sh

# 3. Start system
./start_walle.sh
```

### **Key Installation Features**
- **Python 3.9.13** via pyenv
- **Virtual environment** with all dependencies
- **SMB file sharing** for network access
- **Audio system** with TTS samples
- **GPIO/I2C** interface enabling
- **Systemd service** creation

---



## Future Improvements

### **🎯 Immediate Priorities**

1. **Sabertooth Integration** - Complete tank drive motor control
2. **WebSocket Handler Extraction** - Improve maintainability  
3. **Advanced Safety Systems** - Voltage/current based protection
4. **Configuration Hot-Reload** - Runtime config updates

### **🚀 Medium Term**

1. **Bluetooth Controller** - Direct Steam Deck integration
2. **Scene Timeline Editor** - Complex sequence creation
3. **Web Interface** - Browser-based control panel
4. **Mobile App** - React Native control interface

### **🌟 Advanced Features**

1. **AI Behavior System** - Autonomous emotional responses
2. **Voice Recognition** - Speech command processing
3. **Computer Vision** - Advanced gesture and object detection
4. **Multi-Robot Coordination** - Fleet management

---

## Troubleshooting

### **Common Issues**

**Serial Communication**
```bash
# Check devices
ls -la /dev/ttyAMA*
ls -la /dev/ttyUSB*

# Test serial connection  
screen /dev/ttyAMA0 9600
```

**Camera Issues**
```bash
# Check ESP32 connectivity
ping 10.1.1.203

# Restart camera proxy
./start_walle.sh --camera-only
```

**Python Environment**
```bash
# Verify Python version
python --version  # Should be 3.9.13

# Check virtual environment
source venv/bin/activate
pip list
```

**SMB File Sharing**
```bash
# Check SMB status
./manage_smb.sh status

# Restart services
./manage_smb.sh restart
```

### **Debug Commands**

```bash
# Real-time logs
tail -f logs/walle_enhanced_backend.log

# System resources
htop

# I2C devices
i2cdetect -y 1

# Network services
netstat -tulpn | grep -E "(8766|8081)"
```

---

## System Status Summary

### **✅ Production Ready Components**
- Shared serial communication
- NEMA 23 stepper control  
- Camera streaming system
- Scene management
- Audio system
- Telemetry monitoring


### **❌ Missing Components**
- Sabertooth motor driver
- Advanced safety systems
- Bluetooth controller
- Configuration hot-reload

**Overall Assessment**: The system is well-architected with excellent hardware abstraction and safety considerations. The modular approach with shared serial management and priority queuing shows professional-grade robotics programming. Main areas for improvement are code organization and completing missing hardware integrations.
