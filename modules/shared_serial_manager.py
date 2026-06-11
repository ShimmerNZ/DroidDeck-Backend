#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared Serial Manager with Batch Command Support

Manages a single serial port shared by multiple Pololu Maestro controllers
on a daisy chain, with priority-based command queueing, batched multi-servo
target commands, realtime command coalescing, and automatic reconnection
if the port is lost at boot or during operation.
"""

import threading
import time
import queue
import serial
import logging
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pololu Maestro protocol constants (Pololu Compact/Pololu protocol)
# Reference: https://www.pololu.com/docs/0J40 section 5.e "Serial Servo Commands"
# All commands below use the Pololu protocol form: 0xAA, device, command, ...
# ---------------------------------------------------------------------------
POLOLU_START_BYTE      = 0xAA
CMD_SET_TARGET         = 0x04  # channel, target low7, target high7 (quarter-us)
CMD_SET_SPEED          = 0x07  # channel, speed low7, speed high7
CMD_SET_ACCELERATION   = 0x09  # channel, accel low7, accel high7
CMD_GET_POSITION       = 0x10  # channel -> 2 byte response (quarter-us)
CMD_SET_MULTIPLE       = 0x1F  # count, first channel, then low7/high7 pairs
CMD_GET_FIRMWARE       = 0x21  # -> firmware/device info response
CMD_RESTART_SCRIPT     = 0xA7  # subroutine low7, subroutine high7

# Queue and timing tuning
COMMAND_QUEUE_MAXSIZE  = 200    # ~4 seconds of 50Hz mixer output
QUEUE_POLL_TIMEOUT     = 0.01   # Worker wake interval; bounds realtime latency
READ_TIMEOUT           = 0.05   # Per-read serial timeout for position responses
DEVICE_INFO_TIMEOUT    = 0.15   # Longer timeout for the firmware info response
RECONNECT_DELAY_MIN    = 1.0    # First reconnect attempt delay
RECONNECT_DELAY_MAX    = 30.0   # Backoff cap between reconnect attempts


class CommandPriority(Enum):
    """Command priority levels - lower numbers execute first"""
    EMERGENCY = 1      # Emergency stop, safety (immediate)
    REALTIME = 2       # Joystick control, live input (< 10ms)
    NORMAL = 3         # Regular servo commands (< 100ms)
    LOW = 4            # Position reads, status (< 1s)
    BACKGROUND = 5     # Diagnostics, housekeeping (when idle)


@dataclass
class BatchServoTarget:
    """Individual servo target within a batch command"""
    channel: int
    target: float  # microseconds (0.25us resolution float; converted to quarter-us ints at serial boundary)
    speed: Optional[int] = None
    acceleration: Optional[int] = None


@dataclass
class SharedSerialCommand:
    """Command supporting both individual and batch operations"""
    device_id: str
    device_number: int
    command_type: str
    data: Dict[str, Any]
    priority: CommandPriority
    callback: Optional[Callable] = None
    timeout: float = 1.0
    timestamp: float = field(default_factory=time.time)
    expects_response: bool = False

    batch_targets: List[BatchServoTarget] = field(default_factory=list)
    is_batch_command: bool = False

    def __lt__(self, other):
        """Enable priority queue sorting"""
        if self.priority.value != other.priority.value:
            return self.priority.value < other.priority.value
        return self.timestamp < other.timestamp


class BatchCommandBuilder:
    """Helper class to build efficient batch commands"""

    def __init__(self, device_id: str, device_number: int):
        self.device_id = device_id
        self.device_number = device_number
        self.targets: List[BatchServoTarget] = []
        self.priority = CommandPriority.NORMAL
        self.callback = None

    def add_target(self, channel: int, target: float, speed: Optional[int] = None,
                 acceleration: Optional[int] = None) -> 'BatchCommandBuilder':
        """Add a servo target to the batch"""
        self.targets.append(BatchServoTarget(
            channel=channel,
            target=target,
            speed=speed,
            acceleration=acceleration
        ))
        return self

    def set_priority(self, priority: CommandPriority) -> 'BatchCommandBuilder':
        """Set priority for the entire batch"""
        self.priority = priority
        return self

    def set_callback(self, callback: Callable) -> 'BatchCommandBuilder':
        """Set callback for batch completion"""
        self.callback = callback
        return self

    def build(self) -> SharedSerialCommand:
        """Build the final batch command"""
        if not self.targets:
            raise ValueError("Batch command must have at least one target")

        return SharedSerialCommand(
            device_id=self.device_id,
            device_number=self.device_number,
            command_type="set_multiple_targets",
            data={"targets": self.targets},
            priority=self.priority,
            callback=self.callback,
            batch_targets=self.targets.copy(),
            is_batch_command=True,
            expects_response=False
        )


class EnhancedSharedSerialPortManager:
    """
    Shared serial port manager with priority queueing, batch commands,
    realtime coalescing, and automatic reconnection.

    Command routing:
      - EMERGENCY commands go to a dedicated deque processed before
        everything else.
      - REALTIME position commands without callbacks are coalesced into a
        per-device "latest target per channel" slot. Only the newest target
        per channel is ever sent, so a stalled bus can never replay stale
        joystick positions when it recovers.
      - All other commands flow through a bounded priority queue. When the
        queue is full the command is dropped (the next mixer tick supplies a
        fresh position within 20ms, so dropping is correct for motion data).
    """

    def __init__(self, port: str, baud_rate: int = 9600):
        self.port = port
        self.baud_rate = baud_rate

        # Serial connection
        self.serial_conn = None
        self.connected = False
        self.connection_lock = threading.Lock()

        # Reconnection state
        self._next_reconnect_time = 0.0
        self._reconnect_delay = RECONNECT_DELAY_MIN
        self._was_connected = False

        # Command processing
        self.command_queue = queue.PriorityQueue(maxsize=COMMAND_QUEUE_MAXSIZE)
        self.worker_thread = None
        self.running = False
        self._last_drop_log = 0.0

        # Emergency fast path - always processed first
        self._emergency_commands: deque = deque()
        self._emergency_lock = threading.Lock()

        # Realtime coalescing - device_number -> {channel: BatchServoTarget}
        self._realtime_pending: Dict[int, Dict[int, BatchServoTarget]] = {}
        self._realtime_device_ids: Dict[int, str] = {}
        self._realtime_lock = threading.Lock()

        # Device registration
        self.registered_devices = {}  # device_id -> controller object
        self.device_numbers = {}      # device_number -> device_id

        # Statistics
        self.stats = {
            "commands_processed": 0,
            "commands_failed": 0,
            "commands_dropped": 0,
            "batch_commands_sent": 0,
            "servos_moved_in_batches": 0,
            "connection_attempts": 0,
            "reconnections": 0,
            "last_error": None,
            "uptime_start": time.time(),
        }

        logger.info(f"Serial manager created: {port} @ {baud_rate}")

    def create_batch_builder(self, device_id: str, device_number: int) -> BatchCommandBuilder:
        """Create a new batch command builder"""
        return BatchCommandBuilder(device_id, device_number)

    def send_batch_command(self, builder: BatchCommandBuilder) -> bool:
        """Send a batch command built with BatchCommandBuilder"""
        command = builder.build()
        return self.send_command(command)

    # ---- Connection management ----

    def start(self) -> bool:
        """
        Start the shared serial manager.

        The worker thread is started even if the initial port open fails;
        the worker retries the connection with backoff so a robot powered
        before its Maestros recovers automatically once they appear.

        Returns:
            bool: True if the initial connection succeeded
        """
        logger.info(f"Starting serial manager: {self.port}")

        initial_connected = self._open_port()
        if not initial_connected:
            logger.warning(
                f"Initial serial connection failed for {self.port} - "
                f"worker will retry in the background"
            )

        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

        logger.info("Serial manager started")
        return initial_connected

    def _open_port(self) -> bool:
        """Attempt to open the serial port. Returns True on success."""
        self.stats["connection_attempts"] += 1
        try:
            with self.connection_lock:
                conn = serial.Serial(
                    port=self.port,
                    baudrate=self.baud_rate,
                    timeout=READ_TIMEOUT,
                    write_timeout=1.0
                )
                conn.reset_input_buffer()
                self.serial_conn = conn
                self.connected = True

            if self._was_connected:
                self.stats["reconnections"] += 1
                logger.info(f"Serial connection re-established: {self.port}")
            else:
                logger.info(f"Serial connection established: {self.port}")

            self._was_connected = True
            self._reconnect_delay = RECONNECT_DELAY_MIN
            return True

        except (serial.SerialException, OSError) as e:
            self.stats["last_error"] = str(e)
            with self.connection_lock:
                self.serial_conn = None
                self.connected = False
            return False

    def _handle_connection_loss(self, error: Exception):
        """Mark the connection as lost and schedule an immediate retry."""
        logger.error(f"Serial connection lost on {self.port}: {error}")
        self.stats["last_error"] = str(error)

        with self.connection_lock:
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except (serial.SerialException, OSError):
                    pass
                self.serial_conn = None
            self.connected = False

        # Retry immediately on first loss; backoff grows from there
        self._next_reconnect_time = time.monotonic()
        self._reconnect_delay = RECONNECT_DELAY_MIN

    def _try_reconnect(self):
        """Attempt reconnection, respecting the backoff schedule."""
        now = time.monotonic()
        if now < self._next_reconnect_time:
            return

        logger.info(f"Attempting serial reconnect: {self.port}")
        if self._open_port():
            return

        self._next_reconnect_time = now + self._reconnect_delay
        logger.warning(
            f"Serial reconnect failed for {self.port} - "
            f"next attempt in {self._reconnect_delay:.0f}s"
        )
        self._reconnect_delay = min(self._reconnect_delay * 2, RECONNECT_DELAY_MAX)

    def stop(self):
        """Stop the shared serial manager"""
        logger.info("Stopping serial manager")

        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)

        with self.connection_lock:
            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.close()
                    logger.info("Serial connection closed")
                except (serial.SerialException, OSError) as e:
                    logger.warning(f"Error closing serial connection: {e}")
            self.connected = False

    # ---- Device registration ----

    def register_device(self, device_id: str, device_number: int, device_ref) -> bool:
        """Register a device with this manager"""
        if device_id in self.registered_devices:
            logger.warning(f"Device {device_id} already registered")
            return False

        if device_number in self.device_numbers:
            existing_id = self.device_numbers[device_number]
            logger.warning(f"Device number {device_number} already used by {existing_id}")
            return False

        self.registered_devices[device_id] = device_ref
        self.device_numbers[device_number] = device_id

        logger.info(f"Registered device: {device_id} (#{device_number})")
        return True

    # ---- Command submission ----

    def send_command(self, command: SharedSerialCommand) -> bool:
        """
        Submit a command for execution (non-blocking).

        EMERGENCY commands bypass the queue. REALTIME position commands
        without callbacks are coalesced so only the newest target per
        channel is sent. Everything else goes through the bounded priority
        queue and is dropped (with a warning) if the queue is full.
        """
        if not self.running:
            logger.warning("Cannot send command - manager not running")
            return False

        if command.priority == CommandPriority.EMERGENCY:
            with self._emergency_lock:
                self._emergency_commands.append(command)
            return True

        if (command.priority == CommandPriority.REALTIME
                and command.callback is None
                and command.command_type in ("set_target", "set_multiple_targets")):
            self._merge_realtime(command)
            return True

        try:
            self.command_queue.put_nowait(command)
            return True
        except queue.Full:
            self.stats["commands_dropped"] += 1
            # Rate-limit the warning so a wedged bus cannot flood the journal
            now = time.monotonic()
            if now - self._last_drop_log > 1.0:
                self._last_drop_log = now
                logger.warning(
                    f"Command queue full - dropping {command.command_type} "
                    f"for device #{command.device_number} "
                    f"({self.stats['commands_dropped']} dropped total)"
                )
            return False

    def _merge_realtime(self, command: SharedSerialCommand):
        """Merge a realtime position command into the per-device latest slot."""
        with self._realtime_lock:
            slot = self._realtime_pending.setdefault(command.device_number, {})

            if command.command_type == "set_multiple_targets":
                for target in command.batch_targets:
                    slot[target.channel] = target
            else:
                channel = command.data["channel"]
                slot[channel] = BatchServoTarget(
                    channel=channel,
                    target=command.data["target"]
                )

            # Remember the device_id so the drained batch carries it
            self._realtime_device_ids[command.device_number] = command.device_id

    def _drain_realtime(self) -> List[SharedSerialCommand]:
        """Swap out pending realtime targets and build batch commands."""
        with self._realtime_lock:
            if not self._realtime_pending:
                return []
            pending = self._realtime_pending
            self._realtime_pending = {}
            device_ids = dict(self._realtime_device_ids)

        commands = []
        for device_number, targets in pending.items():
            target_list = list(targets.values())
            commands.append(SharedSerialCommand(
                device_id=device_ids.get(device_number, f"device_{device_number}"),
                device_number=device_number,
                command_type="set_multiple_targets",
                data={"targets": target_list},
                priority=CommandPriority.REALTIME,
                batch_targets=target_list,
                is_batch_command=True,
                expects_response=False
            ))
        return commands

    def _fail_waiting_commands(self):
        """
        While disconnected, fail queued commands quickly so callers are not
        left waiting, and discard pending realtime targets (the mixer
        supplies fresh ones every tick).
        """
        with self._realtime_lock:
            self._realtime_pending.clear()

        with self._emergency_lock:
            emergency = list(self._emergency_commands)
            self._emergency_commands.clear()

        drained = []
        while True:
            try:
                drained.append(self.command_queue.get_nowait())
                self.command_queue.task_done()
            except queue.Empty:
                break

        for command in emergency + drained:
            self._invoke_callback(command, None)

    # ---- Worker loop ----

    def _worker_loop(self):
        """Main worker loop for processing commands"""
        logger.info("Serial worker loop started")

        while self.running:
            try:
                if not self.connected:
                    self._try_reconnect()
                    if not self.connected:
                        self._fail_waiting_commands()
                        time.sleep(0.1)
                        continue

                # 1. Emergency commands always run first
                emergency = None
                with self._emergency_lock:
                    if self._emergency_commands:
                        emergency = self._emergency_commands.popleft()
                if emergency:
                    self._execute_command(emergency)
                    continue

                # 2. Realtime coalesced targets next
                realtime = self._drain_realtime()
                if realtime:
                    for command in realtime:
                        self._execute_command(command)
                    continue

                # 3. Regular queue (NORMAL / LOW / BACKGROUND)
                try:
                    command = self.command_queue.get(timeout=QUEUE_POLL_TIMEOUT)
                except queue.Empty:
                    continue

                self._execute_command(command)
                self.command_queue.task_done()

            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                self.stats["commands_failed"] += 1

        logger.info("Serial worker loop stopped")

    def _invoke_callback(self, command: SharedSerialCommand, result: Any):
        """Run a command callback safely, outside the connection lock."""
        if not command.callback:
            return
        try:
            command.callback(result)
        except Exception as e:
            logger.error(f"Command callback error ({command.command_type}): {e}")

    def _execute_command(self, command: SharedSerialCommand):
        """Execute a single command and dispatch its callback."""
        result = None
        failed = False

        try:
            with self.connection_lock:
                if not self.connected or not self.serial_conn or not self.serial_conn.is_open:
                    logger.warning("Serial connection not available")
                    failed = True
                else:
                    result = self._execute_maestro_command(command)

            if not failed:
                self.stats["commands_processed"] += 1
                if command.is_batch_command:
                    self.stats["batch_commands_sent"] += 1
                    self.stats["servos_moved_in_batches"] += len(command.batch_targets)

        except (serial.SerialException, OSError) as e:
            self._handle_connection_loss(e)
            self.stats["commands_failed"] += 1
            failed = True
        except Exception as e:
            logger.error(f"Command execution error: {e}")
            self.stats["commands_failed"] += 1
            failed = True

        self._invoke_callback(command, None if failed else result)

    def _execute_maestro_command(self, command: SharedSerialCommand) -> Any:
        """
        Execute a Maestro command on the serial port.

        Caller holds connection_lock. Serial-level errors propagate to
        _execute_command which handles connection loss.
        """
        cmd_type = command.command_type
        data = command.data
        device_num = command.device_number

        if cmd_type == "set_multiple_targets":
            targets = command.batch_targets
            if not targets:
                logger.warning("Batch command with no targets")
                return False

            # Sort by channel number
            targets.sort(key=lambda t: t.channel)

            # Send speed/acceleration for any target that specifies them
            for target in targets:
                if target.speed is not None:
                    self._send_speed_command(device_num, target.channel, target.speed)
                if target.acceleration is not None:
                    self._send_acceleration_command(device_num, target.channel, target.acceleration)

            # Check whether channels form a contiguous block
            first_ch = targets[0].channel
            is_contiguous = all(
                targets[i].channel == first_ch + i for i in range(len(targets))
            )

            if is_contiguous:
                # Set Multiple Targets (0x1F):
                # 0xAA, device, 0x1F, count, first_channel, lo, hi, lo, hi, ...
                # Only the first channel number appears; subsequent channels are implied.
                cmd_bytes = [POLOLU_START_BYTE, device_num, CMD_SET_MULTIPLE,
                             len(targets), first_ch]
                for target in targets:
                    # Convert float us to integer quarter-us (Maestro native resolution)
                    target_quarter_us = int(round(target.target * 4))
                    cmd_bytes.append(target_quarter_us & 0x7F)
                    cmd_bytes.append((target_quarter_us >> 7) & 0x7F)
                self.serial_conn.write(bytes(cmd_bytes))
                logger.debug(f"Sent Set Multiple Targets: {len(targets)} contiguous channels from ch{first_ch} to device #{device_num}")
            else:
                # Non-contiguous channels: send individual Set Target commands
                for target in targets:
                    # Convert float us to integer quarter-us (Maestro native resolution)
                    target_quarter_us = int(round(target.target * 4))
                    cmd_bytes = bytes([
                        POLOLU_START_BYTE, device_num, CMD_SET_TARGET, target.channel,
                        target_quarter_us & 0x7F,
                        (target_quarter_us >> 7) & 0x7F
                    ])
                    self.serial_conn.write(cmd_bytes)
                logger.debug(f"Sent {len(targets)} individual Set Target commands (non-contiguous) to device #{device_num}")

            return True

        elif cmd_type == "set_target":
            channel = data["channel"]
            target = data["target"]

            # Convert float us to integer quarter-us (Maestro native resolution)
            target_quarter_us = int(round(target * 4))
            cmd_bytes = bytes([
                POLOLU_START_BYTE, device_num, CMD_SET_TARGET, channel,
                target_quarter_us & 0x7F,
                (target_quarter_us >> 7) & 0x7F
            ])

            self.serial_conn.write(cmd_bytes)
            return True

        elif cmd_type == "get_all_positions":
            channels = data.get("channels", [])
            positions = {}

            # Clear any stale bytes so responses pair with the right request
            self.serial_conn.reset_input_buffer()

            for channel in channels:
                try:
                    cmd_bytes = bytes([POLOLU_START_BYTE, device_num,
                                       CMD_GET_POSITION, channel])
                    self.serial_conn.write(cmd_bytes)

                    # read() waits up to the port read timeout for 2 bytes
                    response = self.serial_conn.read(2)

                    if len(response) == 2:
                        position = ((response[1] << 8) | response[0]) // 4
                        positions[channel] = position
                    else:
                        positions[channel] = None
                        logger.debug(f"Invalid position response for channel {channel}")
                        # Discard any late partial byte before the next request
                        self.serial_conn.reset_input_buffer()

                except (serial.SerialException, OSError):
                    raise
                except Exception as e:
                    logger.error(f"Error reading position for channel {channel}: {e}")
                    positions[channel] = None

            logger.debug(f"Read {len(positions)} positions from device #{device_num}")
            return positions

        elif cmd_type == "set_speed":
            speed = data["speed"]
            channel = data["channel"]
            return self._send_speed_command(device_num, channel, speed)

        elif cmd_type == "set_acceleration":
            acceleration = data["acceleration"]
            channel = data["channel"]
            return self._send_acceleration_command(device_num, channel, acceleration)

        elif cmd_type == "get_position":
            channel = data["channel"]

            # Clear stale bytes so the response pairs with this request
            self.serial_conn.reset_input_buffer()

            cmd_bytes = bytes([POLOLU_START_BYTE, device_num,
                               CMD_GET_POSITION, channel])
            self.serial_conn.write(cmd_bytes)

            response = self.serial_conn.read(2)

            if len(response) == 2:
                position = ((response[1] << 8) | response[0]) // 4
                return position
            else:
                logger.debug(f"Invalid position response from device {device_num}: {len(response)} bytes")
                self.serial_conn.reset_input_buffer()
                return None

        elif cmd_type == "restart_script":
            # Restart script at subroutine number
            subroutine = data.get("subroutine", 0)
            cmd_bytes = bytes([POLOLU_START_BYTE, device_num, CMD_RESTART_SCRIPT,
                               subroutine & 0x7F, (subroutine >> 7) & 0x7F])
            self.serial_conn.write(cmd_bytes)
            logger.debug(f"Started script #{subroutine} on device #{device_num}")
            return True

        else:
            logger.warning(f"Unknown Maestro command: {cmd_type}")
            return None

    def _send_speed_command(self, device_num: int, channel: int, speed: int) -> bool:
        """Send a Set Speed command"""
        speed_low = speed & 0x7F
        speed_high = (speed >> 7) & 0x7F
        cmd_bytes = bytes([POLOLU_START_BYTE, device_num, CMD_SET_SPEED,
                           channel, speed_low, speed_high])
        self.serial_conn.write(cmd_bytes)
        return True

    def _send_acceleration_command(self, device_num: int, channel: int, acceleration: int) -> bool:
        """Send a Set Acceleration command"""
        accel_low = acceleration & 0x7F
        accel_high = (acceleration >> 7) & 0x7F
        cmd_bytes = bytes([POLOLU_START_BYTE, device_num, CMD_SET_ACCELERATION,
                           channel, accel_low, accel_high])
        self.serial_conn.write(cmd_bytes)
        return True

    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics"""
        uptime = time.time() - self.stats["uptime_start"]
        batch_commands = self.stats["batch_commands_sent"]
        batch_servos = self.stats["servos_moved_in_batches"]

        stats = self.stats.copy()
        stats.update({
            "uptime_seconds": uptime,
            "commands_per_second": self.stats["commands_processed"] / uptime if uptime > 0 else 0,
            "batch_efficiency": (
                batch_servos / max(1, self.stats["commands_processed"])
            ) * 100,
            "average_batch_size": batch_servos / batch_commands if batch_commands > 0 else 0.0,
            "registered_devices": len(self.registered_devices),
            "queue_size": self.command_queue.qsize(),
            "connected": self.connected,
        })

        return stats


class EnhancedMaestroControllerShared:
    """
    Maestro controller using a shared serial manager, with batch command support
    """

    def __init__(self, device_id: str, device_number: int, shared_manager: EnhancedSharedSerialPortManager):
        self.device_id = device_id
        self.device_number = device_number
        self.shared_manager = shared_manager

        # Status tracking
        self.connected = False
        self.channel_count = 0

        # Register with shared manager
        if self.shared_manager.register_device(device_id, device_number, self):
            logger.info(f"Maestro controller created: {device_id} (device #{device_number})")
        else:
            logger.error(f"Failed to register {device_id} with shared manager")

    def start(self) -> bool:
        """Start the controller"""
        self.connected = True
        logger.info(f"Maestro controller started: {self.device_id}")
        return True

    def stop(self):
        """Stop the controller"""
        self.connected = False
        logger.info(f"Maestro controller stopped: {self.device_id}")

    def set_multiple_targets(self, targets: List[Tuple[int, int]],
                           priority: CommandPriority = CommandPriority.NORMAL,
                           callback: Optional[Callable] = None) -> bool:
        """
        Set multiple servo targets efficiently with one command

        Args:
            targets: List of (channel, target) tuples
            priority: Command priority level
            callback: Optional completion callback

        Returns:
            bool: True if command queued successfully
        """
        builder = self.shared_manager.create_batch_builder(self.device_id, self.device_number)

        for channel, target in targets:
            builder.add_target(channel, target)

        builder.set_priority(priority)
        if callback:
            builder.set_callback(callback)

        return self.shared_manager.send_batch_command(builder)

    def detect_channel_count_advanced(self) -> int:
        """Auto-detection using device info response"""
        logger.debug(f"Device info detection: {self.device_id} (device #{self.device_number})")

        try:
            if not self.connected:
                return 0

            with self.shared_manager.connection_lock:
                if self.shared_manager.connected and self.shared_manager.serial_conn:
                    conn = self.shared_manager.serial_conn

                    # Use a longer read timeout for the device info exchange,
                    # restoring the standard timeout afterwards
                    original_timeout = conn.timeout
                    conn.timeout = DEVICE_INFO_TIMEOUT

                    try:
                        # Flush any stale data from input buffer
                        conn.reset_input_buffer()

                        # Get device info command
                        cmd_bytes = bytes([POLOLU_START_BYTE, self.device_number,
                                           CMD_GET_FIRMWARE])
                        logger.debug(f"  Sending command: {cmd_bytes.hex()}")
                        conn.write(cmd_bytes)

                        response = conn.read(16)
                        logger.debug(f"  Response length: {len(response)} bytes")
                        logger.debug(f"  Raw response bytes: {[hex(b) for b in response]}")

                        detected_channels = None

                        if len(response) >= 2:
                            device_info = response.hex()
                            logger.debug(f"  Device info: {device_info}")

                            # The Maestro returns firmware version in first two bytes
                            # Byte 0: Minor firmware version
                            # Byte 1: Major firmware version
                            minor_fw = response[0]
                            major_fw = response[1]
                            logger.debug(f"  Firmware version: {major_fw}.{minor_fw}")

                            # Documented patterns:
                            # 1100 = 24-channel Maestro
                            # 0100 = 18-channel Maestro
                            if device_info.startswith('1100'):
                                detected_channels = 24
                                logger.debug("  Device info indicates 24-channel Maestro")
                            elif device_info.startswith('0100'):
                                detected_channels = 18
                                logger.debug("  Device info indicates 18-channel Maestro")

                            # If device info didn't give us an answer, try alternative detection
                            if detected_channels is None:
                                logger.debug("  Unknown device info, trying alternative detection")

                                # Try reading position from a high channel to determine max channels
                                for test_channel in [23, 17, 11]:
                                    try:
                                        conn.reset_input_buffer()

                                        test_cmd = bytes([POLOLU_START_BYTE, self.device_number,
                                                          CMD_GET_POSITION, test_channel])
                                        conn.write(test_cmd)

                                        test_response = conn.read(2)
                                        if len(test_response) == 2:
                                            logger.debug(f"  Channel {test_channel} responded - detected {test_channel + 1} channels")
                                            if test_channel >= 23:
                                                detected_channels = 24
                                            elif test_channel >= 17:
                                                detected_channels = 18
                                            else:
                                                detected_channels = 12
                                            break
                                    except (serial.SerialException, OSError):
                                        raise
                                    except Exception as probe_error:
                                        logger.debug(f"  Channel {test_channel} probe failed: {probe_error}")
                                        continue

                            # If still no detection, use fallback
                            if detected_channels is None:
                                logger.debug("  All detection methods failed, using fallback")
                                detected_channels = self._guess_channel_count()
                        else:
                            logger.debug("  No device info response, using fallback")
                            detected_channels = self._guess_channel_count()

                    finally:
                        conn.timeout = original_timeout

                    self.channel_count = detected_channels
                    logger.info(f"Channel detection: {self.device_id} = {detected_channels} channels")
                    return detected_channels

            # Manager not connected - use fallback without touching the port
            fallback = self._guess_channel_count()
            self.channel_count = fallback
            return fallback

        except Exception as e:
            logger.error(f"Channel detection error: {self.device_id} - {e}")
            fallback = self._guess_channel_count()
            self.channel_count = fallback
            return fallback

    def _guess_channel_count(self) -> int:
        """Fallback method to guess channel count based on device number"""
        # Common Maestro configurations: 6, 12, 18, 24 channels
        if self.device_number == 12:  # Maestro 1
            return 18
        elif self.device_number == 13:  # Maestro 2
            return 24
        else:
            return 18  # Safe default

    def get_all_positions_batch(self, callback: Optional[Callable] = None,
                            priority: CommandPriority = CommandPriority.LOW) -> bool:
        """
        Get all servo positions using individual requests (fallback method)
        """
        try:
            positions = {}
            positions_received = 0
            total_channels = self.channel_count

            def position_callback(channel):
                def inner_callback(position):
                    nonlocal positions_received
                    positions[channel] = position
                    positions_received += 1

                    # When all positions received, call the main callback
                    if positions_received >= total_channels:
                        if callback:
                            # Ensure callback gets a proper dictionary
                            clean_positions = {k: v for k, v in positions.items() if v is not None}
                            callback(clean_positions)
                return inner_callback

            # Request all positions individually
            success_count = 0
            for channel in range(total_channels):
                success = self.get_position(channel, position_callback(channel))
                if success:
                    success_count += 1

            # If no individual requests succeeded, call callback immediately
            if success_count == 0:
                if callback:
                    callback({})
                return False

            return True

        except Exception as e:
            logger.error(f"Get all positions batch error: {e}")
            if callback:
                callback({})
            return False

    def set_multiple_targets_with_settings(self, servo_configs: List[Dict[str, Any]],
                                         priority: CommandPriority = CommandPriority.NORMAL,
                                         callback: Optional[Callable] = None) -> bool:
        """
        Set multiple servos with individual speed/acceleration settings

        Args:
            servo_configs: List of dicts with 'channel', 'target', optional 'speed', 'acceleration'
            priority: Command priority level
            callback: Optional completion callback

        Example:
            servo_configs = [
                {"channel": 0, "target": 1500, "speed": 50},
                {"channel": 1, "target": 1200, "speed": 30, "acceleration": 20},
                {"channel": 2, "target": 1800}
            ]
        """
        builder = self.shared_manager.create_batch_builder(self.device_id, self.device_number)

        for config in servo_configs:
            builder.add_target(
                channel=config["channel"],
                target=config["target"],
                speed=config.get("speed"),
                acceleration=config.get("acceleration")
            )

        builder.set_priority(priority)
        if callback:
            builder.set_callback(callback)

        return self.shared_manager.send_batch_command(builder)

    def set_target(self, channel: int, target: int,
                   priority: CommandPriority = CommandPriority.NORMAL,
                   callback: Optional[Callable] = None) -> bool:
        """Set a single servo target"""
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

    def get_position(self, channel: int, callback: Callable,
                    priority: CommandPriority = CommandPriority.LOW) -> bool:
        """Get servo position asynchronously"""
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

    def get_status_dict(self) -> Dict[str, Any]:
        """Get controller status as dictionary"""
        return {
            "device_id": self.device_id,
            "device_number": self.device_number,
            "connected": self.connected,
            "channel_count": self.channel_count,
            "batch_commands_supported": True,
            "shared_manager_stats": self.shared_manager.get_stats()
        }


# ================== COMPATIBILITY LAYER ==================
# These aliases maintain backward compatibility with existing code

SharedSerialPortManager = EnhancedSharedSerialPortManager
MaestroControllerShared = EnhancedMaestroControllerShared

# Global manager registry for compatibility functions
_global_managers: Dict[Tuple[str, int], EnhancedSharedSerialPortManager] = {}
_manager_lock = threading.Lock()


def get_shared_manager(port: str, baud_rate: int = 9600) -> EnhancedSharedSerialPortManager:
    """
    Get or create a shared serial manager (compatibility function)

    Args:
        port: Serial port path
        baud_rate: Baud rate for communication

    Returns:
        Enhanced shared serial port manager instance
    """
    manager_key = (port, baud_rate)

    with _manager_lock:
        if manager_key not in _global_managers:
            logger.info(f"Creating shared manager: {port} @ {baud_rate}")
            manager = EnhancedSharedSerialPortManager(port, baud_rate)
            manager.start()
            _global_managers[manager_key] = manager
        else:
            logger.debug(f"Reusing shared manager: {port}")

        return _global_managers[manager_key]


def cleanup_shared_managers():
    """
    Clean up all global shared managers (compatibility function)
    """
    global _global_managers

    with _manager_lock:
        logger.info(f"Cleaning up {len(_global_managers)} shared managers")

        for manager_key, manager in _global_managers.items():
            try:
                port, baud_rate = manager_key
                logger.info(f"Stopping manager: {port}")
                manager.stop()
            except Exception as e:
                logger.error(f"Error stopping manager {manager_key}: {e}")

        _global_managers.clear()
