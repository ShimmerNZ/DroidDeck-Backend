#!/usr/bin/env python3
"""
GPIO Compatibility Layer for WALL-E Robot Control System
Supports both RPi.GPIO (Pi 4 and older) and gpiozero (Pi 5 recommended)
FIXED VERSION - Handles pin reuse properly
"""

import logging
import time
from typing import Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)

class GPIOLibrary(Enum):
    NONE = "none"
    RPI_GPIO = "rpi_gpio"
    GPIOZERO = "gpiozero"

class GPIOWrapper:
    """
    Unified GPIO wrapper that works with multiple GPIO libraries
    Automatically detects and uses the best available library for your Pi
    """
    
    def __init__(self):
        self.library = GPIOLibrary.NONE
        self.available = False
        self._gpio_objects = {}
        self._setup_gpio_library()
    
    def _setup_gpio_library(self):
        """Auto-detect and setup the best available GPIO library"""
        
        # Try gpiozero first (Pi 5 recommended)
        try:
            import gpiozero
            from gpiozero import LED, Button, OutputDevice, InputDevice
            self.gpiozero = gpiozero
            self.LED = LED
            self.Button = Button
            self.OutputDevice = OutputDevice
            self.InputDevice = InputDevice
            self.library = GPIOLibrary.GPIOZERO
            self.available = True
            logger.info("✅ Using gpiozero library (Pi 5 compatible)")
            return
        except ImportError:
            pass
        
        # Fallback to RPi.GPIO (Pi 4 and older)
        try:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            self.library = GPIOLibrary.RPI_GPIO
            self.available = True
            logger.info("✅ Using RPi.GPIO library (Pi 4 compatible)")
            return
        except ImportError:
            pass
        

        
        logger.warning("⚠️ No GPIO library available - GPIO features disabled")
    
    def is_pin_configured(self, pin: int) -> bool:
        """Check if a pin is already configured"""
        return pin in self._gpio_objects
    
    def release_pin(self, pin: int) -> bool:
        """Release a specific GPIO pin"""
        if pin in self._gpio_objects:
            try:
                device = self._gpio_objects[pin]
                if hasattr(device, 'close'):
                    device.close()
                del self._gpio_objects[pin]
                logger.debug(f"🔓 Released GPIO pin {pin}")
                return True
            except Exception as e:
                logger.error(f"❌ Failed to release pin {pin}: {e}")
                return False
        return True  # Pin wasn't in use anyway
    
    def setup_output_pin(self, pin: int, initial_state: bool = False) -> bool:
        """Setup an output pin - reuse existing if available"""
        if not self.available:
            return False
        
        try:
            if self.library == GPIOLibrary.GPIOZERO:
                # Check if pin is already setup
                if pin in self._gpio_objects:
                    existing_device = self._gpio_objects[pin]
                    # If it's already an OutputDevice, reuse it
                    if hasattr(existing_device, 'on') and hasattr(existing_device, 'off'):
                        logger.debug(f"🔄 Reusing existing output pin {pin}")
                        # Set initial state
                        if initial_state:
                            existing_device.on()
                        else:
                            existing_device.off()
                        return True
                    else:
                        # Wrong type, close and recreate
                        logger.debug(f"🔄 Replacing pin {pin} (wrong type)")
                        existing_device.close()
                        del self._gpio_objects[pin]
                
                # Create new device
                device = self.OutputDevice(pin, initial_value=initial_state)
                self._gpio_objects[pin] = device
                logger.debug(f"📌 Setup output pin {pin} with gpiozero")
                return True
                
            elif self.library == GPIOLibrary.RPI_GPIO:
                # Using RPi.GPIO
                if not hasattr(self, '_gpio_mode_set'):
                    self.GPIO.setmode(self.GPIO.BCM)
                    self._gpio_mode_set = True
                
                initial = self.GPIO.HIGH if initial_state else self.GPIO.LOW
                self.GPIO.setup(pin, self.GPIO.OUT, initial=initial)
                logger.debug(f"📌 Setup output pin {pin} with RPi.GPIO")
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to setup output pin {pin}: {e}")
            return False
    
    def setup_input_pin(self, pin: int, pull_up: bool = False, pull_down: bool = False) -> bool:
        """Setup an input pin with pull resistors - reuse existing if available"""
        if not self.available:
            return False
        
        try:
            if self.library == GPIOLibrary.GPIOZERO:
                # Check if pin is already setup
                if pin in self._gpio_objects:
                    existing_device = self._gpio_objects[pin]
                    # If it's already an InputDevice with correct pull, reuse it
                    if hasattr(existing_device, 'is_active'):
                        logger.debug(f"🔄 Reusing existing input pin {pin}")
                        return True
                    else:
                        # Wrong type, close and recreate
                        logger.debug(f"🔄 Replacing pin {pin} (wrong type)")
                        existing_device.close()
                        del self._gpio_objects[pin]
                
                # Create new device
                if pull_up:
                    device = self.InputDevice(pin, pull_up=True)
                elif pull_down:
                    device = self.InputDevice(pin, pull_up=False)
                else:
                    device = self.InputDevice(pin, pull_up=None)
                
                self._gpio_objects[pin] = device
                logger.debug(f"📌 Setup input pin {pin} with gpiozero")
                return True
                
            elif self.library == GPIOLibrary.RPI_GPIO:
                # Using RPi.GPIO
                if not hasattr(self, '_gpio_mode_set'):
                    self.GPIO.setmode(self.GPIO.BCM)
                    self._gpio_mode_set = True
                
                if pull_up:
                    pull = self.GPIO.PUD_UP
                elif pull_down:
                    pull = self.GPIO.PUD_DOWN
                else:
                    pull = self.GPIO.PUD_OFF
                
                self.GPIO.setup(pin, self.GPIO.IN, pull_up_down=pull)
                logger.debug(f"📌 Setup input pin {pin} with RPi.GPIO")
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to setup input pin {pin}: {e}")
            return False
    
    def set_output(self, pin: int, state: bool) -> bool:
        """Set output pin state"""
        if not self.available:
            return False
        
        try:
            if self.library == GPIOLibrary.GPIOZERO:
                device = self._gpio_objects.get(pin)
                if device:
                    if state:
                        device.on()
                    else:
                        device.off()
                    return True
                    
            elif self.library == GPIOLibrary.RPI_GPIO:
                gpio_state = self.GPIO.HIGH if state else self.GPIO.LOW
                self.GPIO.output(pin, gpio_state)
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to set pin {pin} to {state}: {e}")
            return False
    
    def read_input(self, pin: int) -> Optional[bool]:
        """Read input pin state"""
        if not self.available:
            return None
        
        try:
            if self.library == GPIOLibrary.GPIOZERO:
                device = self._gpio_objects.get(pin)
                if device:
                    return device.is_active
                    
            elif self.library == GPIOLibrary.RPI_GPIO:
                return bool(self.GPIO.input(pin))
                
        except Exception as e:
            logger.error(f"❌ Failed to read pin {pin}: {e}")
            return None
    
    def pulse_pin(self, pin: int, duration_us: int = 5) -> bool:
        """Send a pulse to a pin (useful for stepper step signals)"""
        if not self.available:
            return False
        
        try:
            if self.library == GPIOLibrary.GPIOZERO:
                device = self._gpio_objects.get(pin)
                if device:
                    device.on()
                    time.sleep(duration_us / 1_000_000)  # Convert microseconds to seconds
                    device.off()
                    return True
                    
            elif self.library == GPIOLibrary.RPI_GPIO:
                self.GPIO.output(pin, self.GPIO.HIGH)
                time.sleep(duration_us / 1_000_000)
                self.GPIO.output(pin, self.GPIO.LOW)
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to pulse pin {pin}: {e}")
            return False
    
    def setup_button_callback(self, pin: int, callback: Callable, edge: str = "falling") -> bool:
        """Setup callback for button/switch events"""
        if not self.available:
            return False
        
        try:
            if self.library == GPIOLibrary.GPIOZERO:
                # For gpiozero, we'll use Button which handles debouncing
                button = self.Button(pin)
                if edge == "falling":
                    button.when_pressed = callback
                elif edge == "rising":
                    button.when_released = callback
                else:  # both
                    button.when_pressed = callback
                    button.when_released = callback
                
                self._gpio_objects[f"button_{pin}"] = button
                return True
                
            elif self.library == GPIOLibrary.RPI_GPIO:
                gpio_edge = self.GPIO.FALLING if edge == "falling" else self.GPIO.RISING
                if edge == "both":
                    gpio_edge = self.GPIO.BOTH
                
                self.GPIO.add_event_detect(pin, gpio_edge, callback=callback, bouncetime=200)
                return True
                
        except Exception as e:
            logger.error(f"❌ Failed to setup callback for pin {pin}: {e}")
            return False
    
    def cleanup(self):
        """Clean up GPIO resources"""
        if not self.available:
            return
        
        try:
            if self.library == GPIOLibrary.GPIOZERO:
                # Close all gpiozero devices
                for device in self._gpio_objects.values():
                    if hasattr(device, 'close'):
                        device.close()
                self._gpio_objects.clear()
                logger.info("🧹 gpiozero cleanup complete")
                
            elif self.library == GPIOLibrary.RPI_GPIO:
                self.GPIO.cleanup()
                logger.info("🧹 RPi.GPIO cleanup complete")
                
        except Exception as e:
            logger.error(f"❌ GPIO cleanup error: {e}")

# Global GPIO wrapper instance
gpio_wrapper = GPIOWrapper()

# Convenience functions for easy migration
def setup_output_pin(pin: int, initial_state: bool = False) -> bool:
    return gpio_wrapper.setup_output_pin(pin, initial_state)

def setup_input_pin(pin: int, pull_up: bool = False, pull_down: bool = False) -> bool:
    return gpio_wrapper.setup_input_pin(pin, pull_up, pull_down)

def set_output(pin: int, state: bool) -> bool:
    return gpio_wrapper.set_output(pin, state)

def read_input(pin: int) -> Optional[bool]:
    return gpio_wrapper.read_input(pin)

def pulse_pin(pin: int, duration_us: int = 5) -> bool:
    return gpio_wrapper.pulse_pin(pin, duration_us)

def setup_button_callback(pin: int, callback: Callable, edge: str = "falling") -> bool:
    return gpio_wrapper.setup_button_callback(pin, callback, edge)

def cleanup_gpio():
    gpio_wrapper.cleanup()

def is_gpio_available() -> bool:
    return gpio_wrapper.available

def get_gpio_library() -> str:
    return gpio_wrapper.library.value

def is_pin_configured(pin: int) -> bool:
    return gpio_wrapper.is_pin_configured(pin)

def release_pin(pin: int) -> bool:
    return gpio_wrapper.release_pin(pin)