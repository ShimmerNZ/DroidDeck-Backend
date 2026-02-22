# 🤖 DroidDeck — WALL-E Robot Control System

**Updated: February 2026**

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Hardware Configuration](#hardware-configuration)
3. [Power Supply](#power-supply)
4. [TB6600 Stepper Driver Configuration](#tb6600-stepper-driver-configuration)
5. [Pololu Maestro Configuration](#pololu-maestro-configuration)
6. [Software Architecture](#software-architecture)
7. [Bottango Animation Integration](#bottango-animation-integration)
8. [Configuration Files](#configuration-files)
9. [Installation & Setup](#installation--setup)
10. [Bluetooth Controller Setup](#bluetooth-controller-setup)
11. [API Documentation](#api-documentation)
12. [Troubleshooting](#troubleshooting)

---

## System Overview

DroidDeck is a production-grade robotics control platform for WALL-E animatronic robots. It replaces legacy solutions (Padawan, Kyber) with a modern, unified system built around a Raspberry Pi 5 and Steam Deck interface.

- **Backend**: Python asyncio WebSocket server with shared serial management and motion mixing
- **Frontend**: PyQt6 Steam Deck application with real-time telemetry, scene editing, and controller calibration
- **Web Interface**: Flask/Socket.IO browser interface accessible from any device on the network
- **Camera System**: ESP32-CAM with HTTP proxy for multi-client streaming and gesture recognition
- **Hardware Control**: Dual Pololu Maestro 24-channel servo controllers, NEMA 23 stepper motor with TB6600 driver, Sabertooth 2×60 brushed motor controller for tank drive
- **Scene Management**: Bottango-imported animations with cubic bezier interpolation, audio-synchronized servo movements, and layered motion mixing
- **Safety Systems**: Emergency stop, limit switches, hardware watchdog, voltage/current monitoring, and graceful failsafe defaults

---

## Hardware Configuration

### Core Processing

- **Raspberry Pi 5** — Main controller running the backend, GPIO control, and serial communication
- **Python 3.9.13** — Managed via pyenv for consistency
- **Steam Deck** — Primary user interface running the PyQt6 frontend via distrobox

### Servo Control

- **Pololu Maestro 24-channel (×2)** — 48 total servo channels
  - **Maestro 1** — Device #12 on `/dev/ttyAMA0` at 57600 baud
  - **Maestro 2** — Device #13 on `/dev/ttyAMA0` at 57600 baud
  - Both controllers share a single serial port via the shared serial manager
  - Priority-based command queuing: Emergency → Realtime → Normal → Background
  - Batch command support for efficient multi-servo updates at 50Hz

### Tank Drive

- **Sabertooth 2×60** — Dual brushed motor controller for the drive tracks
  - Connected to **Maestro 1** via servo signal input (channels mapped in `movement_controls.json`)
  - The Maestro outputs a standard servo PWM signal; the Sabertooth interprets this as a drive command
  - No direct serial connection to the Raspberry Pi — all control goes through the Maestro

### Stepper Motor System

- **NEMA 23 Stepper Motor** — Drives the gantry/linear positioning system
- **TB6600 Driver** — 4 microstep (800 pulse/rev) configuration
- **Lead Screw** — 8mm pitch for precise linear movement
- **Limit Switch** — Hardware homing on GPIO 26, active-LOW with pull-up

### Sensors & Monitoring

- **ADS1115 ADC** — 16-bit precision analog-to-digital converter (I²C)
  - Channel 0: Battery voltage monitoring (via voltage divider)
  - Channel 1: Current sensor 1 (ACS758)
  - Channel 2: Current sensor 2 (ACS758)
- **GPIO Safety**: Limit switch (GPIO 26), emergency stop (GPIO 25), status indicators

### Camera & Communication

- **ESP32-CAM** — WiFi video streaming with MediaPipe gesture recognition (right wave, left wave, hands up)
- **Native Audio** — Raspberry Pi audio output via pygame
- **SMB Network Sharing** — Remote file access for development and configuration
- **Bluetooth** — Controller support (PS4, Xbox, generic gamepads) via pygame

---

## Power Supply

The robot uses a 4S LiPo battery (~14.8V nominal) as the main power source, with regulated supplies for each subsystem:

| Supply | Voltage | Rating | Powers |
|---|---|---|---|
| UBEC 1 | 5V | 5A | Raspberry Pi 5 and electronics |
| UBEC 2 | 5V | 5A | Maestro 2 and 5V servos (AMCE-5A servos) |
| UBEC 3 | 7.2V | 10A | High-voltage servos on Maestro 1 |
| Main bus | 4S LiPo (~14.8V) | — | Sabertooth 2×60 motor drive, TB6600 stepper driver |

> The AMCE-5A servos connected to Maestro 2 run on the 5V UBEC. High-voltage servos on Maestro 1 are supplied by the 7.2V UBEC. Both UBECs derive their input from the 4S LiPo main bus.

---

## TB6600 Stepper Driver Configuration

### DIP Switch Settings

The TB6600 is configured for **4 microstep / 800 pulse per revolution** with **2.5A continuous / 2.9A peak** current.

| Switch | Position | Function |
|---|---|---|
| SW1 | ON | Pulse/rev — 800 (4 microstep) |
| SW2 | OFF | Pulse/rev — 800 (4 microstep) |
| SW3 | OFF | Pulse/rev — 800 (4 microstep) |
| SW4 | OFF | Current — 2.5A / 2.9A peak |
| SW5 | ON | Current — 2.5A / 2.9A peak |
| SW6 | ON | Current — 2.5A / 2.9A peak |

SW1–SW3 together select 800 pulse/rev (4 microstep mode).
SW4–SW6 together select 2.5A continuous, 2.9A peak current.

### Physical Connections

```
TB6600 → Raspberry Pi 5
├── PUL+ → GPIO 16 (Step)
├── PUL- → GND
├── DIR+ → GPIO 12 (Direction)
├── DIR- → GND
├── ENA+ → GPIO 13 (Enable)
└── ENA- → GND

TB6600 → NEMA 23 Motor
├── A+ → Motor Phase A+
├── A- → Motor Phase A-
├── B+ → Motor Phase B+
└── B- → Motor Phase B-

Power → TB6600
├── VCC → 4S LiPo main bus
└── GND → Common ground
```

### Position Calculation

```
Steps per cm = steps_per_revolution / lead_screw_pitch (in cm)
             = 800 / 0.8 = 1000 steps per cm

Position in cm    = current_steps / 1000
Position in steps = position_cm × 1000
```

---

## Pololu Maestro Configuration

Both Maestro controllers are **24-channel** units sharing a single UART at `/dev/ttyAMA0`.

### Baud Rate

The baud rate on both Maestros has been changed from the factory default to **57600**. This must be set in Maestro Control Center under **Serial Settings → Baud rate**. The hardware_config.json reflects this value.

### Device Numbers

Device numbers are set in Maestro Control Center under **Serial Settings → Device number**:
- Maestro 1: **Device #12**
- Maestro 2: **Device #13**

These device numbers allow both controllers to share the same TX line using Pololu's daisy-chain serial protocol.

### Channel Mapping

Channels 0–23 map to Maestro 1 (`m1_ch0` → `m1_ch23`) and channels 24–47 in Bottango map to Maestro 2 (`m2_ch0` → `m2_ch23`) in the DroidDeck channel naming scheme.

---

## Diagrams

Two reference diagrams are provided alongside this README:

**[droiddeck_wiring.html](droiddeck_wiring.html)** — Hardware wiring reference covering power distribution, the shared serial bus, all GPIO pin assignments, TB6600 stepper connections, ADS1115 sensor wiring, Sabertooth tank drive connections, ESP32-CAM setup, and the Maestro channel map overview.

**[droiddeck_architecture.html](droiddeck_architecture.html)** — Motion pipeline architecture showing the complete data flow for both control paths: Path A (Steam Deck joystick → WebSocket → hardware_service → Maestro, bypassing the mixer) and Path B (scene trigger → scene engine → MotionMixer → ConstraintPipeline → CommandDispatcher → Maestro batch serial). Includes the serial protocol byte format for both single and batch target commands, and the channel locking table during scene playback.

---

## Software Architecture

### Component Overview

```
DroidDeck Backend (main.py)
├── SharedSerialPortManager     — Thread-safe serial comms to both Maestros
├── HardwareService             — Servo command interface and channel management
├── MotionMixer                 — Layered motion blending (joystick / idle / scene)
│   ├── Joystick Layer (priority 0)
│   ├── Idle Layer    (priority 5)
│   └── Scene Layer   (priority 10)
├── ConstraintPipeline          — Velocity limiting, deadband, position clamping
├── CommandDispatcher           — Batches channel commands per Maestro device
├── EnhancedSceneEngine         — Scene loading, playback, and audio sync
├── NEMA23Controller            — TB6600 stepper motor positioning
├── BluetoothController         — PS4/Xbox/generic gamepad input
├── TelemetrySystem             — Voltage, current, and temperature monitoring
├── CameraProxy                 — ESP32-CAM stream relay for multiple clients
├── AudioController             — pygame audio playback
├── DroidDeckWebServer          — Flask/Socket.IO web interface (port 5000)
└── WebSocket Server            — Primary frontend comms (port 8766)
```

### Motion Mixer Architecture

The motion system uses a layered blending approach to prevent conflicts between simultaneous input sources:

- **Joystick layer** handles real-time Steam Deck or Bluetooth controller input
- **Idle layer** provides automatic idle animations when no other input is active
- **Scene layer** plays back imported Bottango animations with cubic bezier interpolation
- Layers blend using additive or override modes with configurable fade in/out rates
- The scene layer sets a `channel_mask` that locks specific channels from joystick influence during playback
- All layers converge through the ConstraintPipeline before dispatch to prevent runaway servo movement

> **Note**: The Steam Deck frontend joystick sends `{type: "servo"}` WebSocket messages that bypass the MotionMixer entirely via `hardware_service.set_servo_position()`. This is intentional for low-latency direct control but means the Steam Deck joystick path does not pass through channel locking. The Bluetooth controller path goes through the full mixer pipeline.

### Key Design Patterns

- **Shared serial management**: Both Maestros share `/dev/ttyAMA0` with the shared serial manager handling command queuing, device addressing, and retry logic
- **Priority queue**: Emergency stop preempts all other commands; scene playback preempts idle; joystick operates at the base layer
- **Hot-reload config**: JSON configuration files are watched and reloaded without requiring a restart
- **Graceful degradation**: All hardware components have fallback modes; the system starts in failsafe with motors disabled

---

## Bottango Animation Integration

DroidDeck imports animation timelines from Bottango and converts them to its internal scene format with full cubic bezier interpolation.

### Required Export Settings in Bottango

When exporting from Bottango, the driver must be configured with these exact settings:

**Curve Handling:**

| Setting | Value |
|---|---|
| Curve Strategy | Pre-Cache Curves (Default) |
| Effector curve buffer | 3 curves |
| Curve lead time in MS | 1000 ms |
| Effector Signal Format | Scaled Int (Default) |
| Scaled Int Max | 8192 |
| Custom Motor Signal Range | 16 Bit (Default) |
| Allow Instant Curves | ON |
| Offset time by last sync time | ON |
| Allow Synchronized Curves | OFF |

These settings ensure the exported JSON contains the `sC` curve commands with the correct format that the DroidDeck converter expects.

### Channel Mapping

Bottango channel numbers map directly to DroidDeck Maestro channels:

```
Bottango channels 0–23  → Maestro 1 (m1_ch0 to m1_ch23)
Bottango channels 24–47 → Maestro 2 (m2_ch0 to m2_ch23)
```

### Import Workflow

1. Design and record your animation in Bottango
2. Export the animation as a JSON file using the driver settings above
3. Drop the exported JSON into the `bottango_imports/` folder on the Raspberry Pi
4. The backend watchdog auto-detects the file, converts it, and saves the scene to `scenes/`
5. The new scene appears immediately in the DroidDeck scene list — no restart required

You can also convert manually:
```bash
python3 bottango_converter.py my_animation.json --output-dir scenes/
```

### How the Conversion Works

The `bottango_converter.py` module:
- Parses `rSVPin` setup commands to extract servo PWM ranges per channel
- Parses `sC` curve commands as cubic bezier control point data
- Samples the bezier curves at 50ms intervals (20 FPS)
- Converts Bottango's Scaled Int (0–8192) values to Maestro quarter-microsecond units
- Outputs a DroidDeck scene JSON with locked channels, timesteps, and metadata

The cubic bezier implementation preserves the exact motion curves authored in Bottango, including easing in/out, so animations play back with the same feel as they were designed.

---

## Configuration Files

### hardware_config.json

```json
{
  "hardware": {
    "maestro1": {
      "port": "/dev/ttyAMA0",
      "baud_rate": 57600,
      "device_number": 12
    },
    "maestro2": {
      "port": "/dev/ttyAMA0",
      "baud_rate": 57600,
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
    "hardware": {
      "stepper_motor": {
        "steps_per_revolution": 800,
        "homing_speed": 400,
        "normal_speed": 1000,
        "max_speed": 1200,
        "acceleration": 800
      }
    }
  }
}
```

### Other Configuration Files

| File | Purpose |
|---|---|
| `servo_config.json` | Per-channel servo min/max/home positions for both Maestros |
| `controller_config.json` | Button and axis mappings for the Steam Deck controller |
| `controller_mappings.json` | Mapping of controller inputs to servo/scene/stepper actions |
| `controller_calibration.json` | Saved calibration data for stick deadzone and range |
| `motion_config.json` | MotionMixer layer weights, fade rates, and blend modes |
| `movement_controls.json` | Tank drive servo channels and sensitivity settings |
| `scenes_config.json` | Scene library definitions and category assignments |
| `camera_config.json` | ESP32-CAM IP, streaming resolution, and proxy settings |
| `steamdeck_config.json` | Steam Deck-specific UI and layout preferences |
| `web_config.json` | Web server host/port (default port 5000) |
| `theme_config.json` | UI theme and colour settings |
| `voltage_alert_config.json` | Low battery warning thresholds |

---

## Installation & Setup

### Raspberry Pi Setup

```bash
# Clone the repository
git clone <repo-url> ~/droiddeck
cd ~/droiddeck

# Run the install script
chmod +x DD_Install.sh
./DD_Install.sh
```

The install script handles pyenv, Python 3.9.13, all pip dependencies, systemd service registration, and SMB share configuration.

### Starting the Backend

```bash
# Via systemd (auto-starts on boot)
sudo systemctl start droiddeck-backend
sudo systemctl enable droiddeck-backend

# Manually
cd ~/droiddeck
python3 main.py
```

### Steam Deck Frontend

The frontend runs inside a distrobox container on the Steam Deck. Add DroidDeck as a non-Steam game pointing to `DroidDeck.sh` so it launches via Game Mode.

```bash
# First-time setup on Steam Deck
chmod +x DroidDeck.sh
./DroidDeck.sh
```

### Verifying Serial Setup

```bash
# Confirm both Maestros are visible on the shared port
ls -la /dev/ttyAMA*

# Test serial connection at the configured baud rate
screen /dev/ttyAMA0 57600

# Check I²C for ADS1115
i2cdetect -y 1
```

---

## Bluetooth Controller Setup

### Pairing a Controller

```bash
bluetoothctl
power on
agent on
scan on
# Wait for your controller MAC to appear
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
```

**PS4**: Hold Share + PS button until light bar flashes rapidly to enter pairing mode.
**Xbox**: Hold the pairing button until the Xbox button flashes rapidly.

### Auto-Connect on Boot

```bash
bluetoothctl trust XX:XX:XX:XX:XX:XX
sudo systemctl enable bluetooth
```

The backend detects the first available controller (index 0) at startup.

### Troubleshooting Controller Issues

**Controller not connecting:**
```bash
sudo systemctl restart bluetooth
bluetoothctl remove XX:XX:XX:XX:XX:XX
# Re-pair from scratch
```

**Controller pairs but no input detected:**
```bash
python3 -c "import pygame; pygame.init(); pygame.joystick.init(); print(pygame.joystick.get_count())"
tail -f logs/droiddeck.log | grep -i controller
```

**Controller keeps disconnecting:**
```bash
sudo nano /etc/bluetooth/main.conf
# Add under [General]:
# IdleTimeout = 0
# FastConnectable = true
sudo systemctl restart bluetooth
```

---

## API Documentation

### WebSocket API — `ws://[PI_IP]:8766`

**Servo control:**
```json
{ "type": "servo", "channel": "m1_ch0", "position": 6000 }
```

**Scene playback:**
```json
{ "type": "scene", "emotion": "happy" }
{ "type": "get_scenes" }
{ "type": "stop_scene" }
```

**Stepper motor:**
```json
{ "type": "stepper", "command": "move_to_position", "position_cm": 10.0 }
{ "type": "stepper", "command": "home" }
{ "type": "stepper", "command": "get_status" }
```

**Safety:**
```json
{ "type": "emergency_stop" }
{ "type": "enable_motors" }
{ "type": "disable_motors" }
```

**Controller:**
```json
{ "type": "get_controller_status" }
{ "type": "start_calibration_mode" }
{ "type": "stop_calibration_mode" }
```

**Configuration:**
```json
{ "type": "config", "action": "reload", "config_name": "hardware" }
```

### Web Interface — `http://[PI_IP]:5000`

The web interface mirrors the main DroidDeck controls and is accessible from any browser on the local network. It connects via Socket.IO on the same port.

---

## Troubleshooting

### Serial / Maestro Issues

**No communication with Maestros:**
```bash
ls -la /dev/ttyAMA*
# Verify baud rate is 57600 in both hardware_config.json and Maestro Control Center
# Verify device numbers match (12 and 13)
screen /dev/ttyAMA0 57600
```

**Servos not responding:**
- Check that motors are enabled in the UI (system starts in failsafe mode)
- Confirm the correct UBEC is powered for the servo voltage range being used
- Verify servo min/max/home values in `servo_config.json` are within the Maestro's configured limits

### Stepper Motor Issues

**Motor not moving:**
```bash
# Verify GPIO connections match StepperConfig defaults
# Check limit switch is not triggered (GPIO 26)
# Confirm TB6600 DIP switches match the table above
```

**Motor stalling or losing steps:**
- Confirm current setting (SW4–SW6) matches the NEMA 23 motor's rated current
- Check power supply voltage under load — the 4S LiPo should read above 13V under normal draw

**Homing fails:**
```bash
# Test limit switch manually
# Verify GPIO 26 reads HIGH normally and LOW when triggered
```

### Bottango Import Issues

**Scene doesn't appear after dropping file:**
- Confirm the file is valid Bottango JSON (exported with the settings listed above)
- Check `logs/droiddeck.log` for conversion errors
- Verify `bottango_imports/` directory exists and is writable

**Animation plays incorrectly:**
- Confirm Effector Signal Format is set to **Scaled Int** and Scaled Int Max is **8192**
- Confirm Custom Motor Signal Range is **16 Bit**
- Verify channel numbers in Bottango match the intended Maestro channels

### System Diagnostics

```bash
# Live backend log
tail -f logs/droiddeck.log

# Check running services
sudo systemctl status droiddeck-backend
sudo systemctl status bluetooth

# Network ports
netstat -tulpn | grep -E "(8766|5000)"

# I²C devices (ADS1115 should appear at 0x48)
i2cdetect -y 1

# Python environment
python3 --version  # Should be 3.9.13
```

---

## System Status

### Production Ready
- Shared serial communication at 57600 baud with priority queuing
- NEMA 23 stepper with TB6600 driver at 800 pulse/rev (4 microstep)
- Dual Pololu Maestro 24-channel controllers (devices #12 and #13)
- Sabertooth 2×60 tank drive via Maestro servo signal
- Bottango animation import with cubic bezier interpolation
- Layered motion mixer with joystick, idle, and scene layers
- Scene playback at 20 FPS with channel locking
- Audio-synchronized scene support
- Telemetry monitoring (voltage, current via ACS758 + ADS1115)
- ESP32-CAM streaming with gesture recognition
- Web interface via Flask/Socket.IO
- Bluetooth controller support (PS4/Xbox/generic)
- Hot-reload configuration management

### In Progress
- Non-blocking hardware PWM migration for stepper motor
- Enhanced gesture recognition (left hand, right hand, both hands)
- WebSocket message batching optimisation

### Future
- AI behaviour system
- Voice recognition
- Advanced computer vision integration
