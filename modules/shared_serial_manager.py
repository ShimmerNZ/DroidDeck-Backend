#!/usr/bin/env python3
"""
shared_serial_manager.py - Shared Serial Manager for Multiple Maestro Controllers
Handles multiple Pololu Maestro controllers on the same serial port with proper routing and thread safety.
"""

import threading
import time
import queue
import serial
import logging
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import weakref

logger = logging.getLogger(__name__)

class CommandPriority(Enum):
    """Command priority levels - lower numbers execute first"""
    EMERGENCY = 1      # Emergency stop, safety (immediate)
    REALTIME = 2       # Joystick control, live input (< 10ms)
    NORMAL = 3         # Regular servo commands (< 100ms)
    LOW = 4           # Position reads, status (< 1s)
    BACKGROUND = 5     # Diagnostics, housekeeping (when idle)

@dataclass
class SharedSerialCommand:
    """Command for shared serial device communication"""
    device_id: str                    # e.g., "maestro1", "maestro2"
    device_number: int               # Pololu device number (12, 13, etc.)
    command_type: str                # "set_target", "get_position", etc.
    data: Dict[str, Any]            # Command parameters
    priority: CommandPriority
    callback: Optional[Callable] = None
    timeout: float = 1.0
    retry_count: int = 0
    max_retries: int = 3
    timestamp: float = field(default_factory=time.time)
    command_id: str = field(default_factory=lambda: f"cmd_{int(time.time()*1000000)}")
    expects_response: bool = False   # Whether this command expects a response

    def __lt__(self, other):
        """Enable priority queue sorting"""
        if self.priority.value != other.priority.value:
            return self.priority.value < other.priority.value
        return self.timestamp < other.timestamp

@dataclass
class PendingResponse:
    """Tracks pending responses for commands that expect them"""
    command: SharedSerialCommand
    start_time: float
    response_data: Optional[Any] = None
    completed: bool = False

class SharedSerialPortManager:
    """
    Manages a single serial connection shared between multiple Maestro controllers.
    Handles command routing, response correlation, and thread safety.
    """
    
    def __init__(self, port: str, baud_rate: int = 9600):
        self.port = port
        self.baud_rate = baud_rate
        
        # Serial connection
        self.serial_conn = None
        self.connected = False
        self.connection_lock = threading.Lock()
        
        # Command processing
        self.command_queue = queue.PriorityQueue()
        self.worker_thread = None
        self.running = False
        
        # Device registration
        self.registered_devices = {}  # device_id -> MaestroControllerShared
        self.device_numbers = {}      # device_number -> device_id
        
        # Response handling
        self.pending_responses = {}   # command_id -> PendingResponse
        self.response_lock = threading.Lock()
        
        # Statistics
        self.stats = {
            "commands_processed": 0,
            "commands_failed": 0,
            "responses_matched": 0,
            "responses_timeout": 0,
            "connection_attempts": 0,
            "last_error": None,
            "uptime_start": time.time()
        }
        
        logger.info(f"🔧 Created shared serial port manager for {port} @ {baud_rate}")
    
    def register_device(self, device_id: str, device_number: int, controller_ref):
        """Register a Maestro controller with this shared port manager"""
        with self.connection_lock:
            if device_number in self.device_numbers:
                existing_device = self.device_numbers[device_number]
                logger.warning(f"⚠️ Device number {device_number} already registered to {existing_device}")
                return False
            
            self.registered_devices[device_id] = weakref.ref(controller_ref)
            self.device_numbers[device_number] = device_id
            
            logger.info(f"📋 Registered {device_id} (device #{device_number}) with shared port {self.port}")
            return True
    
    def unregister_device(self, device_id: str):
        """Unregister a Maestro controller"""
        with self.connection_lock:
            if device_id in self.registered_devices:
                # Find and remove device number mapping
                device_number_to_remove = None
                for dev_num, dev_id in self.device_numbers.items():
                    if dev_id == device_id:
                        device_number_to_remove = dev_num
                        break
                
                if device_number_to_remove is not None:
                    del self.device_numbers[device_number_to_remove]
                
                del self.registered_devices[device_id]
                logger.info(f"📋 Unregistered {device_id} from shared port {self.port}")
    
    def start(self) -> bool:
        """Start the shared serial port manager"""
        if self.running:
            logger.warning(f"Shared port manager for {self.port} already running")
            return True
        
        try:
            self.running = True
            self.worker_thread = threading.Thread(
                target=self._communication_loop,
                name=f"SharedSerial-{self.port.split('/')[-1]}",
                daemon=True
            )
            self.worker_thread.start()
            logger.info(f"✅ Started shared serial port manager for {self.port}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to start shared port manager for {self.port}: {e}")
            self.running = False
            return False
    
    def stop(self) -> bool:
        """Stop the shared serial port manager"""
        if not self.running:
            return True
        
        logger.info(f"🛑 Stopping shared port manager for {self.port}...")
        self.running = False
        
        # Add stop command to wake up thread
        stop_cmd = SharedSerialCommand(
            device_id="__system__",
            device_number=0,
            command_type="__stop__",
            data={},
            priority=CommandPriority.EMERGENCY
        )
        self.command_queue.put(stop_cmd)
        
        # Wait for thread to finish
        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)
            if self.worker_thread.is_alive():
                logger.warning(f"⚠️ Shared port manager thread for {self.port} did not stop gracefully")
        
        self._disconnect()
        logger.info(f"✅ Stopped shared port manager for {self.port}")
        return True
    
    def send_command(self, command: SharedSerialCommand) -> bool:
        """Send command to the shared queue"""
        if not self.running:
            logger.warning(f"Shared port manager for {self.port} not running - command ignored")
            return False
        
        try:
            # Validate device is registered
            if command.device_id not in self.registered_devices:
                logger.error(f"Device {command.device_id} not registered with shared port {self.port}")
                return False
            
            self.command_queue.put(command)
            logger.debug(f"📤 Queued {command.command_type} for {command.device_id} "
                        f"(device #{command.device_number}, priority: {command.priority.name})")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to queue command for {command.device_id}: {e}")
            return False
    
    def send_command_sync(self, command: SharedSerialCommand, timeout: float = 2.0) -> Any:
        """Send command and wait for response (blocking)"""
        result_queue = queue.Queue()
        
        def callback(response):
            result_queue.put(response)
        
        command.callback = callback
        command.expects_response = True
        
        if not self.send_command(command):
            return None
        
        try:
            return result_queue.get(timeout=timeout)
        except queue.Empty:
            logger.warning(f"⏰ Sync command timeout for {command.device_id}")
            return None
    
    def _communication_loop(self):
        """Main communication loop for shared serial port"""
        logger.info(f"🔄 Communication loop started for shared port {self.port}")
        
        # Initial connection
        self._connect()
        
        while self.running:
            try:
                # Get next command with timeout
                try:
                    command = self.command_queue.get(timeout=0.1)
                except queue.Empty:
                    self._housekeeping()
                    continue
                
                # Check for stop command
                if command.command_type == "__stop__":
                    logger.info(f"🛑 Stop command received for shared port {self.port}")
                    break
                
                # Execute command
                self._execute_command_with_stats(command)
                
                # Mark task as done
                self.command_queue.task_done()
                
                # Small delay for device stability
                time.sleep(0.001)
                
            except Exception as e:
                logger.error(f"💥 Communication loop error for shared port {self.port}: {e}")
                time.sleep(0.1)
        
        logger.info(f"🔄 Communication loop ended for shared port {self.port}")
    
    def _execute_command_with_stats(self, command: SharedSerialCommand):
        """Execute command and update statistics"""
        start_time = time.time()
        
        try:
            # Ensure connection
            if not self.connected:
                self._connect()
            
            if not self.connected:
                self._handle_command_failure(command, "Not connected")
                return
            
            # Track pending response if expected
            if command.expects_response:
                with self.response_lock:
                    self.pending_responses[command.command_id] = PendingResponse(
                        command=command,
                        start_time=start_time
                    )
            
            # Execute the command
            response = self._execute_maestro_command(command)
            
            # Update statistics
            execution_time = time.time() - start_time
            self.stats["commands_processed"] += 1
            
            # Handle response
            if command.expects_response and response is not None:
                self._handle_command_response(command, response)
            elif command.callback and not command.expects_response:
                # For commands that don't expect responses but have callbacks
                try:
                    command.callback(response)
                except Exception as e:
                    logger.error(f"💥 Callback error for {command.device_id}: {e}")
            
            logger.debug(f"✅ Executed {command.command_type} for {command.device_id} "
                        f"in {execution_time*1000:.1f}ms")
            
        except Exception as e:
            self._handle_command_failure(command, str(e))
    
    def _handle_command_response(self, command: SharedSerialCommand, response: Any):
        """Handle response for commands that expect them"""
        with self.response_lock:
            if command.command_id in self.pending_responses:
                pending = self.pending_responses[command.command_id]
                pending.response_data = response
                pending.completed = True
                
                # Call callback if provided
                if command.callback:
                    try:
                        command.callback(response)
                    except Exception as e:
                        logger.error(f"💥 Response callback error for {command.device_id}: {e}")
                
                # Clean up
                del self.pending_responses[command.command_id]
                self.stats["responses_matched"] += 1
    
    def _handle_command_failure(self, command: SharedSerialCommand, error_msg: str):
        """Handle command execution failure"""
        self.stats["commands_failed"] += 1
        self.stats["last_error"] = error_msg
        
        logger.error(f"❌ Command failed for {command.device_id}: {error_msg}")
        
        # Clean up pending response
        with self.response_lock:
            if command.command_id in self.pending_responses:
                del self.pending_responses[command.command_id]
        
        # Retry logic
        if command.retry_count < command.max_retries:
            command.retry_count += 1
            logger.info(f"🔄 Retrying command for {command.device_id} "
                       f"(attempt {command.retry_count}/{command.max_retries})")
            self.command_queue.put(command)
        else:
            logger.error(f"💀 Command permanently failed for {command.device_id}")
            if command.callback:
                try:
                    command.callback(None)
                except:
                    pass
    
    def _connect(self):
        """Connect to shared serial port"""
        if self.connected:
            return
        
        with self.connection_lock:
            try:
                if self.serial_conn:
                    self.serial_conn.close()
                
                logger.debug(f"🔌 Connecting to shared port {self.port} @ {self.baud_rate}")
                
                self.serial_conn = serial.Serial(
                    self.port,
                    baudrate=self.baud_rate,
                    timeout=0.05,  # 50ms read timeout
                    write_timeout=0.02,  # 20ms write timeout
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    bytesize=serial.EIGHTBITS
                )
                
                # Test connection
                time.sleep(0.1)  # Let connection stabilize
                
                self.connected = True
                self.stats["connection_attempts"] += 1
                logger.info(f"✅ Connected to shared port {self.port}")
                
            except Exception as e:
                logger.warning(f"⚠️ Failed to connect to shared port {self.port}: {e}")
                self.connected = False
                if self.serial_conn:
                    try:
                        self.serial_conn.close()
                    except:
                        pass
                    self.serial_conn = None
    
    def _disconnect(self):
        """Disconnect from shared serial port"""
        with self.connection_lock:
            self.connected = False
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except:
                    pass
                self.serial_conn = None
            logger.debug(f"🔌 Disconnected from shared port {self.port}")
    
    def _execute_maestro_command(self, command: SharedSerialCommand) -> Any:
        """Execute Maestro-specific commands on shared port"""
        cmd_type = command.command_type
        data = command.data
        device_num = command.device_number
        
        try:
            if cmd_type == "set_target":
                channel = data["channel"]
                target = data["target"]
                
                # Pololu protocol: Set Target
                target_bytes = target * 4  # Convert to quarter-microseconds
                target_low = target_bytes & 0x7F
                target_high = (target_bytes >> 7) & 0x7F
                
                cmd_bytes = bytes([0xAA, device_num, 0x04, channel, target_low, target_high])
                self.serial_conn.write(cmd_bytes)
                return True
                
            elif cmd_type == "set_speed":
                channel = data["channel"]
                speed = data["speed"]
                
                # Pololu protocol: Set Speed
                speed_low = speed & 0x7F
                speed_high = (speed >> 7) & 0x7F
                
                cmd_bytes = bytes([0xAA, device_num, 0x07, channel, speed_low, speed_high])
                self.serial_conn.write(cmd_bytes)
                return True
                
            elif cmd_type == "set_acceleration":
                channel = data["channel"]
                accel = data["acceleration"]
                
                # Pololu protocol: Set Acceleration
                accel_low = accel & 0x7F
                accel_high = (accel >> 7) & 0x7F
                
                cmd_bytes = bytes([0xAA, device_num, 0x09, channel, accel_low, accel_high])
                self.serial_conn.write(cmd_bytes)
                return True
                
            elif cmd_type == "get_position":
                channel = data["channel"]
                
                # Pololu protocol: Get Position
                cmd_bytes = bytes([0xAA, device_num, 0x10, channel])
                self.serial_conn.write(cmd_bytes)
                
                # Wait for response
                time.sleep(0.01)
                response = self.serial_conn.read(2)
                
                if len(response) == 2:
                    position = ((response[1] << 8) | response[0]) // 4
                    return position
                else:
                    logger.debug(f"Invalid position response from device {device_num}: {len(response)} bytes")
                    return None
                    
            elif cmd_type == "get_errors":
                # Get error flags
                cmd_bytes = bytes([0xAA, device_num, 0x11])
                self.serial_conn.write(cmd_bytes)
                time.sleep(0.01)
                response = self.serial_conn.read(2)
                
                if len(response) == 2:
                    error_flags = (response[1] << 8) | response[0]
                    return error_flags
                return None
                
            elif cmd_type == "get_script_status":
                # Get script status
                cmd_bytes = bytes([0xAA, device_num, 0x0A])
                self.serial_conn.write(cmd_bytes)
                time.sleep(0.01)
                response = self.serial_conn.read(1)
                
                if len(response) == 1:
                    return response[0]
                return None
                
            elif cmd_type == "get_moving_state":
                # Check if any servos are moving
                cmd_bytes = bytes([0xAA, device_num, 0x13])
                self.serial_conn.write(cmd_bytes)
                time.sleep(0.01)
                response = self.serial_conn.read(1)
                
                if len(response) == 1:
                    return response[0] != 0
                return None
                
            else:
                logger.warning(f"Unknown Maestro command: {cmd_type}")
                return None
                
        except Exception as e:
            logger.error(f"Maestro command execution error: {e}")
            raise
    
    def _housekeeping(self):
        """Periodic housekeeping tasks"""
        current_time = time.time()
        
        # Clean up timed-out responses
        with self.response_lock:
            timeout_commands = []
            for cmd_id, pending in self.pending_responses.items():
                if current_time - pending.start_time > pending.command.timeout:
                    timeout_commands.append(cmd_id)
            
            for cmd_id in timeout_commands:
                pending = self.pending_responses.pop(cmd_id)
                self.stats["responses_timeout"] += 1
                logger.warning(f"⏰ Response timeout for {pending.command.device_id}")
                
                # Call callback with None to indicate timeout
                if pending.command.callback:
                    try:
                        pending.command.callback(None)
                    except:
                        pass
        
        # Try to reconnect if disconnected
        if not self.connected and self.running:
            self._connect()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics for the shared port"""
        current_time = time.time()
        uptime = current_time - self.stats["uptime_start"]
        
        with self.response_lock:
            pending_count = len(self.pending_responses)
        
        return {
            "port": self.port,
            "baud_rate": self.baud_rate,
            "connected": self.connected,
            "uptime_seconds": round(uptime, 1),
            "registered_devices": list(self.registered_devices.keys()),
            "device_numbers": dict(self.device_numbers),
            "commands_processed": self.stats["commands_processed"],
            "commands_failed": self.stats["commands_failed"],
            "success_rate": round(
                (self.stats["commands_processed"] - self.stats["commands_failed"]) / 
                max(1, self.stats["commands_processed"]) * 100, 2
            ),
            "responses_matched": self.stats["responses_matched"],
            "responses_timeout": self.stats["responses_timeout"],
            "pending_responses": pending_count,
            "queue_size": self.command_queue.qsize(),
            "last_error": self.stats["last_error"],
            "connection_attempts": self.stats["connection_attempts"]
        }


class MaestroControllerShared:
    """
    Enhanced Maestro controller that uses a shared serial port manager.
    Multiple instances can share the same serial port.
    """
    
    def __init__(self, device_id: str, device_number: int, shared_manager: SharedSerialPortManager):
        self.device_id = device_id
        self.device_number = device_number
        self.shared_manager = shared_manager
        
        # Status tracking
        self.connected = False
        self.channel_count = 18  # Default, will be detected
        self.last_error_flags = 0
        self.last_script_status = 0
        
        # Register with shared manager
        if self.shared_manager.register_device(device_id, device_number, self):
            logger.info(f"🎛️ Created shared Maestro controller: {device_id} (device #{device_number})")
        else:
            logger.error(f"❌ Failed to register {device_id} with shared manager")
    
    def start(self) -> bool:
        """Start the controller (shared manager handles actual communication)"""
        self.connected = self.shared_manager.connected
        if self.connected:
            self.detect_channel_count()
        return True
    
    def stop(self) -> bool:
        """Stop the controller and unregister from shared manager"""
        self.shared_manager.unregister_device(self.device_id)
        self.connected = False
        return True
    
    def detect_channel_count(self) -> int:
        """Detect number of channels by testing channel access"""
        logger.info(f"🔍 Detecting channels for {self.device_id}...")
        
        # Test channels to find the maximum available
        test_channels = [23, 17, 11, 5]  # Test for 24, 18, 12, 6 channel models
        
        for channel in test_channels:
            try:
                # Try to get position from this channel
                position = self.get_position_sync(channel, timeout=0.5)
                if position is not None:
                    if channel >= 23:
                        detected = 24
                    elif channel >= 17:
                        detected = 18
                    elif channel >= 11:
                        detected = 12
                    else:
                        detected = 6
                    
                    logger.info(f"✅ {self.device_id} detected: {detected} channels")
                    self.channel_count = detected
                    return detected
            except:
                continue
        
        logger.warning(f"⚠️ Channel detection failed for {self.device_id}, using 18")
        self.channel_count = 18
        return 18
    def detect_channel_count(self) -> int:
        """Detect number of channels by testing channel access"""
        logger.info(f"🔍 Detecting channels for {self.device_id}...")
        
        # Test channels to find the maximum available
        test_channels = [23, 17, 11, 5]  # Test for 24, 18, 12, 6 channel models
        
        for channel in test_channels:
            try:
                # Try to get position from this channel
                position = self.get_position_sync(channel, timeout=0.5)
                if position is not None:
                    if channel >= 23:
                        detected = 24
                    elif channel >= 17:
                        detected = 18
                    elif channel >= 11:
                        detected = 12
                    else:
                        detected = 6
                    
                    logger.info(f"✅ {self.device_id} detected: {detected} channels")
                    self.channel_count = detected
                    return detected
            except:
                continue
        
        logger.warning(f"⚠️ Channel detection failed for {self.device_id}, using 18")
        self.channel_count = 18
        return 18
    
    def set_target(self, channel: int, target: int, 
                   priority: CommandPriority = CommandPriority.NORMAL,
                   callback: Optional[Callable] = None) -> bool:
        """Set servo target position"""
        command = SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="set_target",
            data={"channel": channel, "target": target},
            priority=priority,
            callback=callback,
            expects_response=False
        )
        return self.shared_manager.send_command(command)
    
    def set_speed(self, channel: int, speed: int,
                  priority: CommandPriority = CommandPriority.NORMAL) -> bool:
        """Set servo speed"""
        command = SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="set_speed",
            data={"channel": channel, "speed": speed},
            priority=priority,
            expects_response=False
        )
        return self.shared_manager.send_command(command)
    
    def set_acceleration(self, channel: int, acceleration: int,
                        priority: CommandPriority = CommandPriority.NORMAL) -> bool:
        """Set servo acceleration"""
        command = SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="set_acceleration",
            data={"channel": channel, "acceleration": acceleration},
            priority=priority,
            expects_response=False
        )
        return self.shared_manager.send_command(command)
    
    def get_position(self, channel: int,
                     priority: CommandPriority = CommandPriority.LOW,
                     callback: Optional[Callable] = None) -> bool:
        """Get servo position (non-blocking)"""
        command = SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="get_position",
            data={"channel": channel},
            priority=priority,
            callback=callback,
            expects_response=True
        )
        return self.shared_manager.send_command(command)
    
    def get_position_sync(self, channel: int, timeout: float = 1.0) -> Optional[int]:
        """Get servo position (blocking)"""
        command = SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="get_position",
            data={"channel": channel},
            priority=CommandPriority.LOW,
            expects_response=True
        )
        return self.shared_manager.send_command_sync(command, timeout)
    
    def get_all_positions_batch(self, callback: Optional[Callable] = None) -> bool:
        """Get all servo positions in batch (non-blocking)"""
        if callback is None:
            return False
        
        positions = {}
        successful_reads = 0
        total_channels = self.channel_count
        
        def position_callback(channel):
            def cb(position):
                nonlocal successful_reads
                if position is not None:
                    positions[channel] = position
                    successful_reads += 1
                
                # Check if all positions received
                if len(positions) >= total_channels or successful_reads >= total_channels:
                    callback(positions)
            return cb
        
        # Request all positions
        for channel in range(self.channel_count):
            self.get_position(channel, callback=position_callback(channel))
        
        return True
    
    def get_error_flags(self, callback: Optional[Callable] = None) -> bool:
        """Get error flags (non-blocking)"""
        command = SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="get_errors",
            data={},
            priority=CommandPriority.LOW,
            callback=callback,
            expects_response=True
        )
        return self.shared_manager.send_command(command)
    
    def get_script_status(self, callback: Optional[Callable] = None) -> bool:
        """Get script status (non-blocking)"""
        command = SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="get_script_status",
            data={},
            priority=CommandPriority.LOW,
            callback=callback,
            expects_response=True
        )
        return self.shared_manager.send_command(command)
    
    def get_moving_state(self, callback: Optional[Callable] = None) -> bool:
        """Check if any servos are moving (non-blocking)"""
        command = SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="get_moving_state",
            data={},
            priority=CommandPriority.LOW,
            callback=callback,
            expects_response=True
        )
        return self.shared_manager.send_command(command)
    
    def emergency_stop(self) -> bool:
        """Emergency stop all servos (highest priority)"""
        success = True
        for channel in range(self.channel_count):
            cmd = SharedSerialCommand(
                device_id=self.device_id,
                device_number=self.device_number,
                command_type="set_target",
                data={"channel": channel, "target": 1500},  # Center position
                priority=CommandPriority.EMERGENCY,
                expects_response=False
            )
            success &= self.shared_manager.send_command(cmd)
        return success
    
    def get_status_dict(self) -> Dict[str, Any]:
        """Get comprehensive status as dictionary"""
        return {
            "device_id": self.device_id,
            "device_number": self.device_number,
            "connected": self.connected,
            "channel_count": self.channel_count,
            "shared_manager_stats": self.shared_manager.get_stats(),
            "error_flags": {
                "raw": self.last_error_flags,
                "has_errors": self.last_error_flags != 0
            },
            "script_status": {
                "raw": self.last_script_status
            }
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get device statistics"""
        return {
            "device_id": self.device_id,
            "device_number": self.device_number,
            "channel_count": self.channel_count,
            "connected": self.connected,
            "shared_port": self.shared_manager.port,
            "shared_manager_stats": self.shared_manager.get_stats()
        }


# Global registry for shared serial port managers
_shared_managers: Dict[str, SharedSerialPortManager] = {}
_manager_lock = threading.Lock()

def get_shared_manager(port: str, baud_rate: int = 9600) -> SharedSerialPortManager:
    """Get or create a shared serial port manager for the specified port"""
    global _shared_managers, _manager_lock
    
    with _manager_lock:
        if port not in _shared_managers:
            manager = SharedSerialPortManager(port, baud_rate)
            manager.start()
            _shared_managers[port] = manager
            logger.info(f"🏭 Created new shared manager for port {port}")
        else:
            logger.debug(f"🏭 Reusing existing shared manager for port {port}")
        
        return _shared_managers[port]

def cleanup_shared_managers():
    """Clean up all shared managers (call on shutdown)"""
    global _shared_managers, _manager_lock
    
    with _manager_lock:
        for port, manager in _shared_managers.items():
            logger.info(f"🧹 Cleaning up shared manager for {port}")
            manager.stop()
        _shared_managers.clear()

