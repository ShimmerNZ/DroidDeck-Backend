import time
import psutil
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# Constants for ACS758LCB-100B
ZERO_CURRENT_VOLTAGE = 2.58  # Volts
SENSITIVITY = 0.02          # Volts per Amp (20mV/A)

# Initialize I2C bus and ADS1115 ADC
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)

# Create analog input channels
chan0 = AnalogIn(ads, ADS.P0)
chan1 = AnalogIn(ads, ADS.P1)

def voltage_to_current(voltage):
    return (voltage - ZERO_CURRENT_VOLTAGE) / SENSITIVITY


def get_temperature():
    try:
        # Read from the thermal zone file for Raspberry Pi
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_str = f.read().strip()
            # The temperature is reported in millidegrees Celsius, so divide by 1000
            temperature_celsius = float(temp_str) / 1000.0
            return temperature_celsius
    except FileNotFoundError:
        print("Warning: Could not find thermal zone file. CPU temperature may not be available directly.")
        return None
    except Exception as e:
        print(f"An error occurred while reading CPU temperature: {e}")
        return None


print("Starting telemetry monitoring...\n")

try:
    while True:
        # CPU and memory telemetry
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        temperature = get_temperature()


        # Current sensor telemetry
        voltage0 = chan0.voltage
        voltage1 = chan1.voltage
        current0 = voltage_to_current(voltage0)
        current1 = voltage_to_current(voltage1)

        # Timestamp
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        # Print telemetry data


        print(f"[{timestamp}] CPU: {cpu_percent:.1f}% | Mem: {memory_percent:.1f}% | Temp: {temperature:.1f}°C | "
              f"A0: {voltage0:.3f} V → {current0:.2f} A | A1: {voltage1:.3f} V → {current1:.2f} A")

        time.sleep(1)

except KeyboardInterrupt:
    print("\nTelemetry monitoring stopped.")
