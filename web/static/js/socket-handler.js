// Socket.IO connection and message handling

function initializeSocket() {
    socket = io();
    
    socket.on('connect', function() {
        console.log('Connected to web server');
        updateConnectionStatus('ws', true);
        hideConnectionOverlay();
        showToast('Connected to Droid Deck Web Server', 'success');
        reconnectAttempts = 0;
        setTimeout(() => {
            requestInitialData();
        }, 1000); 
    });
    
    socket.on('disconnect', function() {
        console.log('Disconnected from web server');
        updateConnectionStatus('ws', false);
        updateConnectionStatus('system', false);
        updateConnectionStatus('controller', false);
        
        if (autoReconnect) {
            showConnectionOverlay();
            showToast('Connection lost', 'error');
        }
    });
    
    socket.on('backend_connected', function(data) {
        updateConnectionStatus('ws', data.connected);
        if (data.connected) {
            showToast('Backend connected', 'success');
            requestInitialData();
        } else {
            showToast('Backend disconnected', 'error');
            updateConnectionStatus('system', false);
            updateConnectionStatus('controller', false);
        }
    });
    
    socket.on('backend_message', function(data) {
        handleBackendMessage(data);
    });
    
    socket.on('error', function(error) {
        console.error('Socket.IO error:', error);
        showToast('Connection error', 'error');
    });
}

function connectWebSocket() {
    if (socket && !socket.connected) {
        socket.connect();
    }
}

function sendWebSocketMessage(message) {
    if (socket && socket.connected) {
        socket.emit('backend_command', message);
        return true;
    } else {
        // Silently fail on startup - don't show error toast
        console.log('WebSocket not ready for:', message.type);
        return false;
    }
}

function handleBackendMessage(data) {
    switch (data.type) {
        case 'telemetry':
            updateHealthData(data);
            break;
        case 'scene_list':
            updateScenes(data.scenes);
            break;
        case 'scene_started':
            showToast(`Scene started: ${data.scene_name}`, 'success');
            scenePlayingLocked = true;
            break;
            
        case 'scene_completed':
            showToast(`Scene completed: ${data.scene_name}`, 'success');
            scenePlayingLocked = false;
            currentlyPlayingScene = null;
            const playingItem = document.querySelector('.scene-list-item.playing');
            if (playingItem) {
                playingItem.classList.remove('playing');
            }
            break;
            
        case 'scene_error':
            showToast(`Scene error: ${data.error}`, 'error');
            scenePlayingLocked = false;
            currentlyPlayingScene = null;
            
            // Remove playing class
            const errorItem = document.querySelector('.scene-list-item.playing');
            if (errorItem) {
                errorItem.classList.remove('playing');
            }
            break;
        case 'controller_info':
            updateControllerInfo(data);
            break;
        case 'navigation':
            handleNavigationCommand(data.action);
            break;
        case 'servo_position':
            updateServoPosition(data.channel, data.position);
            break;
        case 'navigation':
            if (currentScreen === 'home') {
                handleNavigationCommand(data.action);
            }
            break;
        case 'all_servo_positions':
            updateAllServoPositions(data);
            break;
        case 'nema_status':
            updateNemaStatus(data.status);
            break;
        case 'nema_position_update':
            updateNemaPosition(data.position_cm);
            break;
        case 'nema_sweep_status':
            updateNemaSweepStatus(data);
            break;
        case 'system_status':
            updateSystemStatus(data);
            break;
        case 'controller_config_saved':
            showToast('Controller configuration saved', 'success');
            break;
        case 'calibration_mode_started':
            showToast('Calibration mode started', 'success');
            break;
        case 'calibration_mode_stopped':
            showToast('Calibration mode stopped', 'info');
            break;
        case 'calibration_data':
            handleCalibrationData(data);
            break;
        case 'error':
            showToast(data.message, 'error');
            break;
        case 'audio_files':
            handleAudioFilesResponse(data);
            break;
        case 'scene_saved':
            showToast('Scene saved successfully', 'success');
            loadScenes();
            break;
        case 'scene_deleted':
            showToast('Scene deleted successfully', 'success');
            loadScenes();
            break;
        case 'controller_config':
            if (data.config) {
                controllerMappings = data.config;
                updateMappingList();
                showToast(`Loaded ${Object.keys(data.config).length} controller mappings`, 'success');
            }
            break;
        default:
            console.log('Unhandled message type:', data.type);
    }
}

function requestInitialData() {
    // Give backend a moment to connect
    setTimeout(() => {
        requestSystemStatus();
        loadScenes();
        requestControllerInfo();
        getAllServoPositions();
        getNemaStatus();
    }, 500); // 500ms delay
}


// Scene Management
function loadScenes() {
    const success = sendWebSocketMessage({ type: 'get_scenes' });
    if (!success) {
        console.log('Waiting for connection to load scenes...');
    }
}

function playScene(sceneName) {
    sendWebSocketMessage({
        type: 'scene',
        emotion: sceneName
    });
}

function stopCurrentScene() {
    sendWebSocketMessage({ type: 'scene_stop' });
}

function refreshScenes() {
    loadScenes();
    showToast('Scenes refreshed', 'success');
}

function importScenesFromBackend() {
    sendWebSocketMessage({ type: 'import_scenes' });
    showToast('Importing scenes from backend...', 'info');
}

function exportSceneConfig() {
    const dataStr = JSON.stringify(scenes, null, 2);
    const dataBlob = new Blob([dataStr], {type: 'application/json'});
    const url = URL.createObjectURL(dataBlob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'scenes_config.json';
    link.click();
    URL.revokeObjectURL(url);
    showToast('Scene configuration exported', 'success');
}

// Servo Control
function setServoPosition(channel, position) {
    sendWebSocketMessage({
        type: 'servo',
        channel: channel,
        pos: parseInt(position)
    });
}

function getAllServoPositions() {
    sendWebSocketMessage({
        type: 'get_all_servo_positions',
        maestro: currentMaestro
    });
}

function homeAllServos() {
    for (let channel = 0; channel < 12; channel++) {
        const channelKey = `m${currentMaestro}_ch${channel}`;
        setServoPosition(channelKey, 1500);
    }
    showToast('Homing all servos', 'success');
}

function homeServo(channel) {
    setServoPosition(channel, 1500);
    showToast(`Homing servo ${channel}`, 'success');
}

function sweepServo(channel) {
    showToast(`Starting sweep test for ${channel}`, 'info');
}

function sweepAllServos() {
    showToast('Starting sweep test for all servos', 'info');
}

function stopAllServos() {
    sendWebSocketMessage({ type: 'emergency_stop' });
    showToast('Stopping all servo movement', 'warning');
}

// NEMA Stepper Control
function moveNemaToPosition() {
    const position = parseFloat(document.getElementById('nemaTargetPos').value);
    sendWebSocketMessage({
        type: 'nema_move_to_position',
        position_cm: position
    });
    showToast(`Moving NEMA to ${position}cm`, 'info');
}

function homeNema() {
    sendWebSocketMessage({ type: 'nema_home' });
    showToast('Homing NEMA stepper', 'info');
}

function startNemaSweep() {
    const minPos = parseFloat(document.getElementById('nemaSweepMin').value);
    const maxPos = parseFloat(document.getElementById('nemaSweepMax').value);
    const speed = parseInt(document.getElementById('nemaSpeed').value);
    const accel = parseInt(document.getElementById('nemaAccel').value);
    
    sendWebSocketMessage({
        type: 'nema_start_sweep',
        min_cm: minPos,
        max_cm: maxPos,
        normal_speed: speed,
        acceleration: accel
    });
    showToast(`Starting sweep: ${minPos}cm to ${maxPos}cm`, 'info');
}

function stopNemaSweep() {
    sendWebSocketMessage({ type: 'nema_stop_sweep' });
    showToast('Stopping NEMA sweep', 'warning');
}

function updateNemaConfig() {
    const speed = parseInt(document.getElementById('nemaSpeed').value);
    const accel = parseInt(document.getElementById('nemaAccel').value);
    
    sendWebSocketMessage({
        type: 'nema_config_update',
        config: {
            normal_speed: speed,
            acceleration: accel
        }
    });
    showToast('NEMA configuration updated', 'success');
}

// Find these two functions in socket-handler.js and replace them:

function enableNema() {
    sendWebSocketMessage({
        type: 'stepper',
        command: 'enable'
    });
    showToast('NEMA motor enabled', 'success');
    nemaEnabled = true;
    updateNemaButtons();
}

function disableNema() {
    sendWebSocketMessage({
        type: 'stepper',
        command: 'disable'
    });
    showToast('NEMA motor disabled', 'warning');
    nemaEnabled = false;
    updateNemaButtons();
}

function getNemaStatus() {
    sendWebSocketMessage({ type: 'nema_get_status' });
}

// Controller Management
function requestControllerInfo() {
    sendWebSocketMessage({ type: 'get_controller_info' });
}

function startCalibration() {
    sendWebSocketMessage({ type: 'start_calibration_mode' });
    showToast('Starting controller calibration...', 'info');
}

function stopCalibration() {
    sendWebSocketMessage({ type: 'stop_calibration_mode' });
    showToast('Stopping controller calibration...', 'info');
}

function saveControllerConfig() {
    if (Object.keys(controllerMappings).length === 0) {
        showToast('No mappings to save', 'warning');
        return;
    }
    sendWebSocketMessage({
        type: 'save_controller_config',
        config: controllerMappings
    });
    showToast('Saving controller configuration...', 'info');
}

function loadControllerConfig() {
    sendWebSocketMessage({ type: 'get_controller_config' });
    showToast('Loading controller configuration...', 'info');
}

function resetCalibration() {
    if (confirm('Are you sure you want to reset controller calibration?')) {
        sendWebSocketMessage({ type: 'reset_controller_calibration' });
        showToast('Controller calibration reset', 'warning');
    }
}

// System Controls
function requestSystemStatus() {
    sendWebSocketMessage({ type: 'system_status' });
}

function emergencyStop() {
    if (confirm('Are you sure you want to trigger an emergency stop?')) {
        sendWebSocketMessage({ type: 'emergency_stop' });
        showToast('Emergency stop activated', 'error');
    }
}

function testConnection() {
    showToast('Testing connection...', 'info');
    if (socket && socket.connected) {
        showToast('Connection test successful', 'success');
    } else {
        showToast('Connection test failed', 'error');
        connectWebSocket();
    }
}

// Helper handlers
function handleCalibrationData(data) {
    if (currentScreen === 'controller') {
        console.log('Calibration data received:', data);
    }
}

function updateNemaSweepStatus(data) {
    const indicator = document.getElementById('nemaIndicator');
    if (indicator) {
        if (data.sweeping) {
            indicator.classList.add('moving');
            showToast(`NEMA sweeping: ${data.min_cm}cm to ${data.max_cm}cm`, 'info');
        } else {
            indicator.classList.remove('moving');
            showToast('NEMA sweep stopped', 'warning');
        }
    }
}