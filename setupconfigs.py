#!/usr/bin/env python3
"""
WALL-E Configuration Setup Script
Creates all necessary configuration files for the optimized WALL-E system
"""

import json
import os
from pathlib import Path

def create_config_directory():
    """Create configs directory"""
    config_dir = Path("configs")
    config_dir.mkdir(exist_ok=True)
    print(f"✅ Created config directory: {config_dir.absolute()}")
    return config_dir

def create_hardware_config():
    """Create hardware_config.json"""
    config = {
        "hardware": {
            "maestro1": {
                "port": "/dev/ttyAMA1",
                "baud_rate": 19200,
                "device_number": 12,
                "channels": 18,
                "gpio_tx": 14,
                "gpio_rx": 15,
                "function": "head_control",
                "description": "Primary Maestro for head pan/tilt and upper body servos"
            },
            "maestro2": {
                "port": "/dev/ttyAMA1", 
                "baud_rate": 19200,
                "device_number": 13,
                "channels": 18,
                "gpio_tx": 14,
                "gpio_rx": 15,
                "function": "body_control",
                "description": "Secondary Maestro for arm and body servos"
            },
            "sabertooth": {
                "port": "/dev/ttyAMA0",
                "baud_rate": 9600,
                "gpio_tx": 8,
                "gpio_rx": 10,
                "function": "tank_drive",
                "motor1_range": [1, 127],
                "motor2_range": [128, 255],
                "stop_values": [64, 192],
                "description": "Sabertooth 2x60 motor controller for tank tracks"
            }
        },
        "gpio": {
            "emergency_stop_pin": 25,
            "limit_switch_pin": 26,
            "motor_step_pin": 16,
            "motor_dir_pin": 12,
            "motor_enable_pin": 13,
            "description": "GPIO pin assignments for safety and motor control"
        },
        "adc": {
            "enabled": True,
            "i2c_address": "0x48",
            "channels": {
                "battery_voltage": 0,
                "current_sensor_1": 1,
                "current_sensor_2": 2
            },
            "calibration": {
                "voltage_divider_ratio": 4.9,
                "current_sensitivity": 0.02,
                "zero_current_voltage": 0.0,
                "reference_voltage": 3.3
            },
            "description": "ADS1115 ADC configuration for voltage and current monitoring"
        }
    }
    return config

def create_joystick_config():
    """Create joystick_config.json"""
    config = {
        "joystick": {
            "enabled": True,
            "device_index": 0,
            "update_rate_hz": 20,
            "deadband": 0.1,
            "expo_curve": 1.0,
            "description": "Xbox/PlayStation controller configuration for WALL-E control",
            "controls": {
                "left_stick": {
                    "function": "tank_drive",
                    "x_axis": 0,
                    "y_axis": 1,
                    "invert_x": False,
                    "invert_y": True,
                    "max_power": 1.0,
                    "ramp_rate": 0.1,
                    "description": "Left stick controls tank drive - Y=forward/back, X=turn"
                },
                "right_stick": {
                    "function": "head_control", 
                    "x_axis": 2,
                    "y_axis": 3,
                    "invert_x": False,
                    "invert_y": True,
                    "pan_range": 1000,
                    "tilt_range": 500,
                    "center_position": 1500,
                    "description": "Right stick controls head - X=pan, Y=tilt"
                }
            }
        },
        "safety": {
            "timeout_ms": 500,
            "emergency_priority": 1,
            "max_command_rate": 50,
            "auto_center_on_disconnect": True,
            "description": "Safety settings for joystick control"
        }
    }
    return config

def create_performance_config():
    """Create performance_config.json"""
    config = {
        "performance": {
            "telemetry_interval_ms": 200,
            "servo_update_rate_hz": 50,
            "position_read_rate_hz": 2,
            "frontend_update_rate_hz": 10,
            "joystick_update_rate_hz": 20,
            "description": "Core performance timing settings"
        },
        "communication": {
            "command_timeout_ms": 1000,
            "connection_retry_interval_s": 5,
            "max_connection_attempts": 5,
            "queue_check_interval_ms": 50,
            "serial_read_timeout_ms": 10,
            "serial_write_timeout_ms": 10,
            "description": "Serial communication timing and retry settings"
        },
        "optimization": {
            "remove_serial_flush": True,
            "reduce_delays": True,
            "batch_position_reads": True,
            "async_telemetry": True,
            "threaded_communication": True,
            "fast_emergency_stop": True,
            "description": "Performance optimization flags"
        },
        "targets": {
            "joystick_response_ms": 5,
            "emergency_stop_ms": 2,
            "servo_command_ms": 10,
            "position_read_ms": 20,
            "telemetry_update_ms": 200,
            "description": "Target performance metrics for validation"
        }
    }
    return config

def create_safety_config():
    """Create safety_config.json"""
    config = {
        "safety": {
            "emergency_stop": {
                "max_response_time_ms": 2,
                "priority": 1,
                "gpio_pin": 25,
                "bounce_time_ms": 300,
                "actions": [
                    "stop_all_motors",
                    "center_all_servos", 
                    "stop_audio",
                    "broadcast_emergency"
                ],
                "description": "Hardware emergency stop configuration"
            },
            "failsafe_mode": {
                "timeout_conditions": [
                    "no_joystick_input_ms > 500",
                    "no_websocket_heartbeat_ms > 5000",
                    "communication_errors > 10"
                ],
                "actions": [
                    "reduce_motor_power",
                    "limit_servo_range",
                    "emergency_audio_alert",
                    "return_to_safe_position"
                ],
                "description": "Automatic failsafe activation conditions and responses"
            },
            "input_validation": {
                "servo_position_range": [500, 2500],
                "motor_power_range": [-1.0, 1.0],
                "joystick_deadband": 0.1,
                "command_rate_limit_hz": 50,
                "description": "Input validation and limiting settings"
            },
            "system_limits": {
                "max_voltage": 16.8,
                "min_voltage": 10.0,
                "critical_voltage": 11.0,
                "max_current": 60.0,
                "max_temperature": 85.0,
                "max_queue_size": 1000,
                "description": "System operational limits and alerts"
            }
        }
    }
    return config

def create_priority_config():
    """Create priority_config.json"""
    config = {
        "command_priorities": {
            "emergency_stop": 1,
            "gpio_interrupt": 1,
            "joystick_tank_drive": 2,
            "joystick_head_control": 2,
            "servo_direct": 3,
            "scene_playback": 3,
            "audio_control": 3,
            "servo_position_read": 4,
            "status_request": 4,
            "telemetry_update": 4,
            "diagnostics": 5,
            "statistics_collection": 5,
            "description": "Priority levels for different command types (1=highest, 5=lowest)"
        },
        "timing": {
            "emergency_max_delay_ms": 2,
            "realtime_max_delay_ms": 5,
            "normal_max_delay_ms": 100,
            "low_max_delay_ms": 1000,
            "background_max_delay_ms": 10000,
            "description": "Maximum acceptable delays for each priority level"
        },
        "queue_limits": {
            "emergency": 10,
            "realtime": 50,
            "normal": 100,
            "low": 200,
            "background": 500,
            "description": "Maximum queue sizes for each priority level"
        }
    }
    return config

def save_config(config_dir, filename, config_data):
    """Save configuration to file"""
    config_path = config_dir / filename
    try:
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)
        print(f"✅ Created: {config_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to create {config_path}: {e}")
        return False

def main():
    """Main setup function"""
    print("🤖 WALL-E Configuration Setup")
    print("=" * 50)
    
    # Create config directory
    config_dir = create_config_directory()
    
    # Configuration files to create
    configs = [
        ("hardware_config.json", create_hardware_config()),
        ("joystick_config.json", create_joystick_config()),
        ("performance_config.json", create_performance_config()),
        ("safety_config.json", create_safety_config()),
        ("priority_config.json", create_priority_config())
    ]
    
    # Create all configuration files
    success_count = 0
    for filename, config_data in configs:
        if save_config(config_dir, filename, config_data):
            success_count += 1
    
    print("=" * 50)
    print(f"📊 Configuration Setup Complete: {success_count}/{len(configs)} files created")
    
    if success_count == len(configs):
        print("✅ All configuration files created successfully!")
        print("\n🔧 Next Steps:")
        print("1. Review and customize the config files for your hardware")
        print("2. Update your main.py with the ConfigurationManager")
        print("3. Test the system: python3 main.py")
        print("4. Connect joystick and test real-time control")
        print("\n🎮 Your WALL-E is ready for professional robot control!")
    else:
        print("⚠️  Some configuration files failed to create")
        print("Please check file permissions and try again")

def check_existing_configs():
    """Check if config files already exist"""
    config_dir = Path("configs")
    if not config_dir.exists():
        return False
    
    config_files = [
        "hardware_config.json",
        "joystick_config.json", 
        "performance_config.json",
        "safety_config.json",
        "priority_config.json"
    ]
    
    existing_files = []
    for filename in config_files:
        config_path = config_dir / filename
        if config_path.exists():
            existing_files.append(filename)
    
    if existing_files:
        print(f"⚠️  Found existing config files: {', '.join(existing_files)}")
        response = input("Do you want to overwrite them? (y/N): ").lower().strip()
        return response == 'y'
    
    return True

if __name__ == "__main__":
    if check_existing_configs():
        main()
    else:
        print("❌ Setup cancelled - existing config files preserved")
        print("💡 Tip: You can manually edit the existing config files or")
        print("   delete the configs/ directory to start fresh")