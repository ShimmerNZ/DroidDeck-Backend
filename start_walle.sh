#!/bin/bash
# Enhanced WALL-E Startup Script with Camera Proxy Support

cd "$(dirname "$0")"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

echo "🤖 Starting WALL-E System..."

# Initialize pyenv if available
if command -v pyenv >/dev/null 2>&1; then
    eval "$(pyenv init -)"
fi

# Check Python version
PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
if [[ "$PYTHON_VERSION" != "3.9.13" ]]; then
    print_warning "Expected Python 3.9.13, found: $PYTHON_VERSION"
    print_warning "Try: source ~/.bashrc && cd $(pwd)"
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    print_error "Virtual environment not found. Run install.sh first."
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Verify Python version in venv
VENV_PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
print_status "🐍 Using Python $VENV_PYTHON_VERSION in virtual environment"

# Function to check if OpenCV is working
check_opencv() {
    python -c "import cv2" 2>/dev/null
    return $?
}

# Function to start camera proxy
start_camera_proxy() {
    if [ ! -f "modules/camera_proxy.py" ]; then
        print_warning "Camera proxy not found at modules/camera_proxy.py"
        return 1
    fi
    
    if check_opencv; then
        print_step "Starting camera proxy..."
        python modules/camera_proxy.py &
        CAMERA_PID=$!
        
        # Give it time to start
        sleep 3
        
        # Check if it's still running
        if kill -0 $CAMERA_PID 2>/dev/null; then
            print_status "✅ Camera proxy started (PID: $CAMERA_PID)"
            echo $CAMERA_PID > camera_proxy.pid
            
            # Get IP address for display
            IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")
            print_status "📷 Camera stream: http://$IP_ADDRESS:8081/stream"
            return 0
        else
            print_error "❌ Camera proxy failed to start"
            return 1
        fi
    else
        print_warning "OpenCV not available, skipping camera proxy"
        return 1
    fi
}

# Function to stop camera proxy
stop_camera_proxy() {
    if [ -f "camera_proxy.pid" ]; then
        CAMERA_PID=$(cat camera_proxy.pid)
        if kill -0 $CAMERA_PID 2>/dev/null; then
            print_status "Stopping camera proxy (PID: $CAMERA_PID)..."
            kill $CAMERA_PID
            wait $CAMERA_PID 2>/dev/null
        fi
        rm -f camera_proxy.pid
    fi
}

# Function for cleanup on exit
cleanup() {
    print_step "Shutting down WALL-E services..."
    stop_camera_proxy
    print_status "✅ Cleanup complete"
    exit 0
}

# Set up signal handlers for graceful shutdown
trap cleanup SIGINT SIGTERM

# Parse command line arguments
START_CAMERA=true
SHOW_HELP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-camera)
            START_CAMERA=false
            shift
            ;;
        --camera-only)
            print_status "Starting camera proxy only..."
            start_camera_proxy
            if [ $? -eq 0 ]; then
                print_status "Camera proxy running. Press Ctrl+C to stop."
                # Wait for camera proxy process
                if [ -f "camera_proxy.pid" ]; then
                    CAMERA_PID=$(cat camera_proxy.pid)
                    wait $CAMERA_PID 2>/dev/null
                fi
            else
                print_error "Failed to start camera proxy"
                exit 1
            fi
            exit 0
            ;;
        --help|-h)
            SHOW_HELP=true
            break
            ;;
        *)
            print_error "Unknown option: $1"
            SHOW_HELP=true
            break
            ;;
    esac
done

# Show help if requested or invalid option
if [ "$SHOW_HELP" = true ]; then
    echo ""
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --no-camera     Start WALL-E without camera proxy"
    echo "  --camera-only   Start only the camera proxy"
    echo "  --help, -h      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0              # Start WALL-E with camera proxy"
    echo "  $0 --no-camera # Start WALL-E without camera"
    echo "  $0 --camera-only # Start only camera proxy"
    echo ""
    exit 0
fi

# Check if main.py exists
if [ ! -f "main.py" ]; then
    print_error "main.py not found. Make sure all files are in place."
    exit 1
fi

# Start camera proxy if enabled
CAMERA_STARTED=false
if [ "$START_CAMERA" = true ]; then
    if start_camera_proxy; then
        CAMERA_STARTED=true
    fi
fi

# Show startup status
echo ""
print_step "WALL-E Service Status:"
echo "  🤖 Core System: Starting..."
if [ "$CAMERA_STARTED" = true ]; then
    echo "  📷 Camera Proxy: ✅ Running"
else
    echo "  📷 Camera Proxy: ❌ Disabled"
fi
echo ""

# Get network information
HOSTNAME=$(hostname)
IP_ADDRESS=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")

# Display connection information
print_status "🚀 Starting WALL-E core backend..."
echo ""
echo "🌐 Network Access Information:"
echo "  📡 WebSocket Server: ws://$IP_ADDRESS:8766"
echo "  🌐 SMB File Shares: \\\\$HOSTNAME\\walle"
if [ "$CAMERA_STARTED" = true ]; then
    echo "  📷 Camera Stream: http://$IP_ADDRESS:8081/stream"
    echo "  📊 Camera Stats: http://$IP_ADDRESS:8081/stats"
    echo "  🏥 Camera Health: http://$IP_ADDRESS:8081/health"
fi
echo ""
print_status "Press Ctrl+C to stop all services"
echo ""

# Start main WALL-E system (this will block until stopped)
python main.py