// UI Update and Display Functions

// Scene Updates
function updateScenes(sceneData) {
    scenes = sceneData;
    
    const categorySet = new Set(['All']);
    scenes.forEach(scene => {
        if (scene.categories && Array.isArray(scene.categories)) {
            scene.categories.forEach(category => categorySet.add(category));
        }
    });
    
    categories = Array.from(categorySet);
    currentCategoryIndex = 0;
    currentSceneIndex = 0;
    
    if (currentScreen === 'home') {
        updateCurrentCategory();
    }
    
    if (showGridView) {
        updateSceneGrid();
    }
}

function updateCurrentCategory() {
    if (categories.length === 0) return;
    
    const currentCategory = categories[currentCategoryIndex];
    updateCategoryPills();
    
    filteredScenes = scenes.filter(scene => {
        if (currentCategory === 'All') return true;
        return scene.categories && scene.categories.includes(currentCategory);
    });
    
    updateCurrentScene();
}

function updateCurrentScene() {
    if (filteredScenes.length === 0) {
        updateSceneList();
        return;
    }
    
    if (currentSceneIndex >= filteredScenes.length) {
        currentSceneIndex = 0;
    }
    
    updateSceneList();
    updateSceneCounter();
}

function updateSceneList() {
    const sceneList = document.getElementById('sceneList');
    if (!sceneList) return;
    
    sceneList.innerHTML = '';
    
    if (filteredScenes.length === 0) {
        const emptyItem = document.createElement('div');
        emptyItem.className = 'scene-list-item';
        emptyItem.innerHTML = `
            <div class="scene-item-emoji">🎭</div>
            <div class="scene-item-details">
                <div class="scene-item-title">No scenes in this category</div>
                <div class="scene-item-meta">Try selecting a different category</div>
            </div>
        `;
        sceneList.appendChild(emptyItem);
        return;
    }
    
    filteredScenes.forEach((scene, index) => {
        const sceneItem = document.createElement('div');
        sceneItem.className = `scene-list-item ${index === currentSceneIndex ? 'selected' : ''}`;
        sceneItem.onclick = () => selectScene(index);
        
        const features = [];
        if (scene.audio_enabled) features.push('Audio');
        if (scene.script_enabled) features.push('Script');
        if (scene.servo_count > 0) features.push(`${scene.servo_count} Servos`);
        
        const metaText = features.length > 0 ? features.join(' • ') : 'Basic scene';
        
        sceneItem.innerHTML = `
            <div class="scene-item-emoji">${scene.emoji || '🎭'}</div>
            <div class="scene-item-details">
                <div class="scene-item-title">${scene.label}</div>
                <div class="scene-item-meta">${metaText}</div>
            </div>
            <div class="scene-item-duration">${scene.duration}s</div>
        `;
        
        sceneList.appendChild(sceneItem);
    });
}

function updateCategoryPills() {
    const categoryPills = document.getElementById('categoryPills');
    if (!categoryPills) return;
    
    categoryPills.innerHTML = '';
    
    categories.forEach((category, index) => {
        const pill = document.createElement('div');
        pill.className = `category-pill ${index === currentCategoryIndex ? 'active' : ''}`;
        pill.textContent = category;
        pill.onclick = () => selectCategory(index);
        categoryPills.appendChild(pill);
    });
}

function updateSceneCounter() {
    const counterElement = document.getElementById('sceneCounter');
    if (counterElement) {
        if (filteredScenes.length > 0) {
            counterElement.textContent = `${currentSceneIndex + 1} / ${filteredScenes.length}`;
        } else {
            counterElement.textContent = '0 / 0';
        }
    }
}

function selectScene(index) {
    currentSceneIndex = index;
    updateSceneList();
    updateSceneCounter();
}

function selectCategory(index) {
    currentCategoryIndex = index;
    currentSceneIndex = 0;
    updateCurrentCategory();
}

function toggleSceneGrid() {
    showGridView = !showGridView;
    const gridContainer = document.getElementById('sceneGridContainer');
    
    if (showGridView) {
        gridContainer.style.display = 'block';
        updateSceneGrid();
    } else {
        gridContainer.style.display = 'none';
    }
}

function updateSceneGrid() {
    const sceneGrid = document.getElementById('sceneGrid');
    if (!sceneGrid) return;
    
    sceneGrid.innerHTML = '';
    
    filteredScenes.forEach(scene => {
        const sceneButton = document.createElement('div');
        sceneButton.className = 'scene-button';
        sceneButton.onclick = () => playScene(scene.label);
        
        sceneButton.innerHTML = `
            <div class="scene-emoji">${scene.emoji || '🎭'}</div>
            <div class="scene-label">${scene.label}</div>
            <div class="scene-duration">${scene.duration}s</div>
        `;
        
        sceneGrid.appendChild(sceneButton);
    });
}

function loadDetailedScenes() {
    const scenesList = document.getElementById('scenesList');
    scenesList.innerHTML = '';

    scenes.forEach(scene => {
        const sceneCard = document.createElement('div');
        sceneCard.className = 'control-panel';
        
        sceneCard.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
                <div>
                    <h4>${scene.emoji || '🎭'} ${scene.label}</h4>
                    <div style="color: var(--text-secondary); font-size: 0.875rem;">
                        Duration: ${scene.duration}s | Categories: ${scene.categories ? scene.categories.join(', ') : 'None'}
                    </div>
                </div>
                <button class="btn" onclick="playScene('${scene.label}')">Play</button>
            </div>
            <div style="font-size: 0.875rem; color: var(--text-tertiary);">
                Audio: ${scene.audio_enabled ? 'Yes' : 'No'} | 
                Servos: ${scene.servo_count || 0} | 
                Script: ${scene.script_enabled ? 'Yes' : 'No'}
            </div>
        `;
        
        scenesList.appendChild(sceneCard);
    });
}

function playCurrentScene() {
    if (filteredScenes.length > 0 && !scenePlayingLocked) {
        const scene = filteredScenes[currentSceneIndex];
        
        // Lock navigation
        scenePlayingLocked = true;
        currentlyPlayingScene = scene.label;
        
        // Visual feedback - add playing class to selected item
        const selectedItem = document.querySelector('.scene-list-item.selected');
        if (selectedItem) {
            selectedItem.classList.add('playing');
        }
        
        // Show toast
        showToast(`Playing scene: ${scene.label}`, 'info');
        
        // Play the scene
        playScene(scene.label);
        
        // Auto-unlock after scene duration + buffer
        const sceneDuration = (scene.duration || 2.0) * 1000;
        const bufferTime = 2000; // 2 second buffer
        
        setTimeout(() => {
            scenePlayingLocked = false;
            currentlyPlayingScene = null;
            
            // Remove playing class
            const playingItem = document.querySelector('.scene-list-item.playing');
            if (playingItem) {
                playingItem.classList.remove('playing');
            }
        }, sceneDuration + bufferTime);
    }
}

// Navigation Handling
function handleNavigationCommand(action) {
    if (currentScreen === 'home') {
        handleHomeNavigation(action);
    } else {
        const buttonMap = {
            'up': 'navUp',
            'down': 'navDown',
            'left': 'navLeft',
            'right': 'navRight',
            'select': 'navSelect'
        };
        
        const buttonId = buttonMap[action];
        if (buttonId) {
            const button = document.getElementById(buttonId);
            if (button) {
                button.style.background = 'var(--accent-primary)';
                setTimeout(() => {
                    button.style.background = '';
                }, 200);
            }
            
            document.getElementById('lastNavigation').textContent = `Last navigation: ${action.toUpperCase()}`;
        }
    }
}

function handleHomeNavigation(action) {
    if (scenePlayingLocked) {
        console.log('Navigation locked - scene playing');
        return;
    }


    if (categories.length === 0 || filteredScenes.length === 0) {
        return;
    }

    switch (action) {
        case 'left':
            currentCategoryIndex = (currentCategoryIndex - 1 + categories.length) % categories.length;
            currentSceneIndex = 0;
            updateCurrentCategory();
            break;
            
        case 'right':
            currentCategoryIndex = (currentCategoryIndex + 1) % categories.length;
            currentSceneIndex = 0;
            updateCurrentCategory();
            break;
            
        case 'up':
            if (filteredScenes.length > 0) {
                currentSceneIndex = (currentSceneIndex - 1 + filteredScenes.length) % filteredScenes.length;
                updateCurrentScene();
            }
            break;
            
        case 'down':
            if (filteredScenes.length > 0) {
                currentSceneIndex = (currentSceneIndex + 1) % filteredScenes.length;
                updateCurrentScene();
            }
            break;
            
        case 'select':
            playCurrentScene();
            break;
    }
}

function updateHealthData(data) {
    updateConnectionStatus('system', true);
    
    // Update basic metrics
    updateHealthMetric('cpu', data.cpu, '%');
    updateHealthMetric('memory', data.memory, '%');
    updateHealthMetric('battery', data.battery_voltage, 'V', 2);
    updateHealthMetric('temp', data.temperature, '°C');
    console.log('maestro1:', data.maestro1);
    console.log('maestro2:', data.maestro2);
    console.log('audio_system:', data.audio_system);
    
    // Update hardware status - FIX: maestro1 and maestro2 are direct properties
    if (data.maestro1) {
        updateHardwareStatus('maestro1', data.maestro1);
    }
    if (data.maestro2) {
        updateHardwareStatus('maestro2', data.maestro2);
    }
    
    // Update audio system status
    if (data.audio_system) {
        updateHardwareStatus('audio', data.audio_system);
    }
    
    // Update network status
    const networkElement = document.getElementById('networkStats');
    if (networkElement) {
        const networkStatus = data.connected_clients > 0 ? 'ONLINE' : 'UNKNOWN';
        networkElement.textContent = networkStatus;
        networkElement.className = `health-status ${data.connected_clients > 0 ? 'online' : 'offline'}`;
    }
}

function updateHealthMetric(name, value, unit = '', decimals = 0) {
    const valueElement = document.getElementById(`${name}Value`);
    const statusElement = document.getElementById(`${name}Status`);
    
    if (valueElement) {
        if (value !== undefined && value !== null) {
            valueElement.textContent = (decimals > 0 ? value.toFixed(decimals) : value) + unit;
        } else {
            valueElement.textContent = '--';
        }
    }
    
    if (statusElement) {
        let status = 'Unknown';
        let className = '';
        
        switch (name) {
            case 'cpu':
                if (value < 50) { status = 'Normal'; className = 'online'; }
                else if (value < 80) { status = 'High'; className = 'warning'; }
                else { status = 'Critical'; className = 'offline'; }
                break;
            case 'memory':
                if (value < 70) { status = 'Normal'; className = 'online'; }
                else if (value < 90) { status = 'High'; className = 'warning'; }
                else { status = 'Critical'; className = 'offline'; }
                break;
            case 'battery':
                if (value > 14.0) { status = 'Good'; className = 'online'; }
                else if (value > 13.2) { status = 'OK'; className = 'online'; }
                else if (value > 12.0) { status = 'Low'; className = 'warning'; }
                else { status = 'Critical'; className = 'offline'; }
                break;
            case 'temp':
                if (value < 60) { status = 'Normal'; className = 'online'; }
                else if (value < 75) { status = 'Warm'; className = 'warning'; }
                else { status = 'Hot'; className = 'offline'; }
                break;
        }
        
        statusElement.textContent = status;
        statusElement.className = `health-status ${className}`;
    }
}


function updateHardwareStatus(component, data) {
    const statusElement = document.getElementById(`${component}Status`);
    if (!statusElement) return;
    
    // Handle different data structures
    const connected = data.connected || false;
    
    if (component === 'maestro1' || component === 'maestro2') {
        // Maestro-specific formatting
        if (connected) {
            const channels = data.channel_count || 0;
            const hasErrors = data.error_flags && data.error_flags.has_errors;
            const moving = data.moving || false;
            const scriptStatus = data.script_status && data.script_status.status ? data.script_status.status : 'unknown';
            
            if (hasErrors) {
                const errorDetails = data.error_flags.details || {};
                const errorList = Object.entries(errorDetails)
                    .filter(([key, value]) => value)
                    .map(([key]) => key.replace('_error', ''))
                    .slice(0, 2);
                const errorText = errorList.join(', ');
                statusElement.textContent = `${channels}ch - Errors: ${errorText}`;
                statusElement.className = 'health-status warning';
            } else {
                const moveText = moving ? 'Moving' : 'Idle';
                statusElement.textContent = `${channels}ch - ${scriptStatus} - ${moveText}`;
                statusElement.className = 'health-status online';
            }
        } else {
            statusElement.textContent = 'OFFLINE';
            statusElement.className = 'health-status offline';
        }
    } else if (component === 'audio') {
        // Audio system status
        statusElement.textContent = connected ? 'ONLINE' : 'OFFLINE';
        statusElement.className = `health-status ${connected ? 'online' : 'offline'}`;
    }
}

function updateSystemStatus(data) {
    updateConnectionStatus('system', true);
    
    // Update any additional system info if needed
    if (data.hardware_status) {
        updateHealthData(data);
    }
}

// Servo Controls
function loadServoControls() {
    const servoControls = document.getElementById('servoControls');
    servoControls.innerHTML = '';

    for (let channel = 0; channel < 12; channel++) {
        const servoControl = document.createElement('div');
        servoControl.className = 'control-panel';
        
        const channelKey = `m${currentMaestro}_ch${channel}`;
        
        servoControl.innerHTML = `
            <h4>Channel ${channel}</h4>
            <div class="control-group">
                <div class="control-label">
                    <span>Position:</span>
                    <span class="control-value" id="position_${channelKey}">1500</span>
                </div>
                <div class="slider-container">
                    <input type="range" class="slider" 
                           min="1000" max="2000" value="1500" 
                           id="slider_${channelKey}"
                           onchange="setServoPosition('${channelKey}', this.value)"
                           oninput="updateServoDisplay('${channelKey}', this.value)">
                </div>
                <div style="display: flex; gap: 0.5rem; margin-top: 0.75rem;">
                    <button class="btn btn-small" onclick="homeServo('${channelKey}')">Home</button>
                    <button class="btn btn-small btn-secondary" onclick="sweepServo('${channelKey}')">Sweep</button>
                </div>
            </div>
        `;
        
        servoControls.appendChild(servoControl);
    }
}

function updateServoDisplay(channel, position) {
    const display = document.getElementById(`position_${channel}`);
    if (display) {
        display.textContent = position;
    }
}

function updateServoPosition(channel, position) {
    const slider = document.getElementById(`slider_${channel}`);
    const display = document.getElementById(`position_${channel}`);
    
    if (slider) slider.value = position;
    if (display) display.textContent = position;
}

function updateAllServoPositions(data) {
    if (data.maestro === currentMaestro && data.positions) {
        Object.entries(data.positions).forEach(([channel, position]) => {
            updateServoPosition(channel, position);
        });
    }
}

function switchMaestro() {
    currentMaestro = parseInt(document.getElementById('maestroSelect').value);
    loadServoControls();
    getAllServoPositions();
}

// NEMA Status Updates
function updateNemaStatus(status) {
    const indicator = document.getElementById('nemaIndicator');
    const state = document.getElementById('nemaState');
    const info = document.getElementById('nemaInfo');

    nemaEnabled = status.enabled || false;
    
    if (indicator && state && info) {
        state.textContent = status.state || 'Unknown';
        
        indicator.classList.remove('enabled', 'moving');
        if (status.enabled) {
            indicator.classList.add('enabled');
        }
        if (status.state === 'moving') {
            indicator.classList.add('moving');
        }
        
        const statusText = [];
        if (status.homed) statusText.push('Homed');
        if (status.enabled) statusText.push('Enabled');
        if (status.safe_position) statusText.push('Safe Position');
        
        info.textContent = statusText.length > 0 ? statusText.join(' | ') : 'Standby';
    }
    
    if (status.position_cm !== undefined) {
        updateNemaPosition(status.position_cm);
    }
}

function updateNemaButtons() {
    const enableBtn = document.querySelector('button[onclick="enableNema()"]');
    const disableBtn = document.querySelector('button[onclick="disableNema()"]');
    
    if (enableBtn && disableBtn) {
        if (nemaEnabled) {
            // Motor is enabled - highlight disable button
            enableBtn.classList.remove('btn-success');
            enableBtn.classList.add('btn-secondary');
            disableBtn.classList.remove('btn-secondary');
            disableBtn.classList.add('btn-danger');
        } else {
            // Motor is disabled - highlight enable button
            enableBtn.classList.remove('btn-secondary');
            enableBtn.classList.add('btn-success');
            disableBtn.classList.remove('btn-danger');
            disableBtn.classList.add('btn-secondary');
        }
    }
}

function updateNemaPosition(position) {
    const positionDisplay = document.getElementById('nemaPosition');
    if (positionDisplay) {
        positionDisplay.textContent = position.toFixed(1);
    }
}

// Controller Info Updates
function updateControllerInfo(data) {
    const connected = data.connected || false;
    updateConnectionStatus('controller', connected);
    
    const indicator = document.getElementById('controllerIndicator');
    const nameElement = document.getElementById('controllerName');
    const infoElement = document.getElementById('controllerInfo');
    
    if (indicator) {
        indicator.classList.toggle('enabled', connected);
    }
    
    if (nameElement && infoElement) {
        if (connected) {
            nameElement.textContent = data.controller_name || 'Unknown Controller';
            infoElement.textContent = `Type: ${data.controller_type || 'Unknown'} | Calibrated: ${data.calibrated ? 'Yes' : 'No'}`;
        } else {
            nameElement.textContent = 'No Controller Connected';
            infoElement.textContent = 'Connect a controller to the backend';
        }
    }
    
    updateCalibrationStatus(data);
}

function updateCalibrationStatus(data) {
    const statusElement = document.getElementById('calibrationStatus');
    if (statusElement) {
        let statusHtml = '<div class="control-group">';
        
        if (data.connected) {
            statusHtml += `
                <div class="setting-item">
                    <label>Controller Type:</label>
                    <span>${data.controller_type || 'Unknown'}</span>
                </div>
                <div class="setting-item">
                    <label>Calibrated:</label>
                    <span class="health-status ${data.calibrated ? 'online' : 'offline'}">
                        ${data.calibrated ? 'Yes' : 'No'}
                    </span>
                </div>
            `;
            
            if (data.optimization_status) {
                statusHtml += `
                    <div class="setting-item">
                        <label>D-pad Rate:</label>
                        <span>${data.optimization_status.dpad_rate_hz}Hz</span>
                    </div>
                    <div class="setting-item">
                        <label>Analog Rate:</label>
                        <span>${data.optimization_status.analog_rate_hz}Hz</span>
                    </div>
                `;
            }
        } else {
            statusHtml += '<p>No controller connected</p>';
        }
        
        statusHtml += '</div>';
        statusElement.innerHTML = statusHtml;
    }
}

// Controller Mapping Management
function updateMappingList() {
    const mappingList = document.getElementById('mappingList');
    if (!mappingList) return;
    
    mappingList.innerHTML = '';
    
    if (Object.keys(controllerMappings).length === 0) {
        const emptyItem = document.createElement('div');
        emptyItem.className = 'mapping-item';
        emptyItem.innerHTML = `
            <div class="mapping-info">
                <div class="mapping-input-name">No mappings configured</div>
                <div class="mapping-behavior">Add controller mappings to get started</div>
            </div>
        `;
        mappingList.appendChild(emptyItem);
        return;
    }
    
    Object.entries(controllerMappings).forEach(([inputName, mapping]) => {
        const mappingItem = document.createElement('div');
        mappingItem.className = 'mapping-item';
        mappingItem.onclick = () => editMapping(inputName);
        
        const behaviorText = getBehaviorDisplayText(mapping);
        
        mappingItem.innerHTML = `
            <div class="mapping-info">
                <div class="mapping-input-name">${inputName}</div>
                <div class="mapping-behavior">${behaviorText}</div>
            </div>
        `;
        
        mappingList.appendChild(mappingItem);
    });
}

function getBehaviorDisplayText(mapping) {
    const behavior = mapping.behavior;
    switch (behavior) {
        case 'direct_servo':
            return `Direct Servo → ${mapping.target || 'Unknown'}`;
        case 'joystick_pair':
            return `Joystick Pair → X: ${mapping.x_servo || 'None'}, Y: ${mapping.y_servo || 'None'}`;
        case 'differential_tracks':
            return `Differential Tracks → L: ${mapping.left_servo || 'None'}, R: ${mapping.right_servo || 'None'}`;
        case 'scene_trigger':
            return `Scene Trigger → ${mapping.scene || 'Unknown'}`;
        case 'toggle_scenes':
            return `Toggle Scenes → ${mapping.scene_1 || 'None'} / ${mapping.scene_2 || 'None'}`;
        case 'nema_stepper':
            return `NEMA Stepper → ${mapping.nema_behavior || 'Unknown'}`;
        case 'system_control':
            return `System Control → ${mapping.system_action || 'Unknown'}`;
        default:
            return 'Unknown behavior';
    }
}

function addNewMapping() {
    editingMapping = null;
    showMappingEditor();
}

function editMapping(inputName) {
    editingMapping = inputName;
    const mapping = controllerMappings[inputName];
    showMappingEditor(mapping);
}

function showMappingEditor(mapping = null) {
    const editor = document.getElementById('mappingEditor');
    const inputSelect = document.getElementById('mappingInput');
    const behaviorSelect = document.getElementById('mappingBehavior');
    
    if (mapping) {
        inputSelect.value = editingMapping;
        behaviorSelect.value = mapping.behavior;
        updateMappingOptions();
        populateMappingOptions(mapping);
    } else {
        inputSelect.value = '';
        behaviorSelect.value = '';
        updateMappingOptions();
    }
    
    editor.style.display = 'block';
}

function hideMappingEditor() {
    const editor = document.getElementById('mappingEditor');
    editor.style.display = 'none';
    editingMapping = null;
}

function updateMappingOptions() {
    const behavior = document.getElementById('mappingBehavior').value;
    const optionsContainer = document.getElementById('mappingOptions');
    
    let optionsHTML = '';
    
    switch (behavior) {
        case 'direct_servo':
            optionsHTML = `
                <div class="option-group">
                    <label class="control-label">
                        <span>Target Servo:</span>
                        <select id="option_target" class="setting-input">
                            <option value="">Select Servo</option>
                            ${generateServoOptions()}
                        </select>
                    </label>
                </div>
                <div class="option-group">
                    <label class="control-label">
                        <span>Invert:</span>
                        <input type="checkbox" id="option_invert">
                    </label>
                </div>
                <div class="option-group">
                    <label class="control-label">
                        <span>Sensitivity:</span>
                        <input type="number" id="option_sensitivity" class="setting-input" value="1.0" min="0.1" max="2.0" step="0.1">
                    </label>
                </div>
            `;
            break;
            
        case 'joystick_pair':
            optionsHTML = `
                <div class="option-group">
                    <label class="control-label">
                        <span>X-Axis Servo:</span>
                        <select id="option_x_servo" class="setting-input">
                            <option value="">Select Servo</option>
                            ${generateServoOptions()}
                        </select>
                    </label>
                </div>
                <div class="option-group">
                    <label class="control-label">
                        <span>Y-Axis Servo:</span>
                        <select id="option_y_servo" class="setting-input">
                            <option value="">Select Servo</option>
                            ${generateServoOptions()}
                        </select>
                    </label>
                </div>
            `;
            break;
            
        case 'scene_trigger':
            optionsHTML = `
                <div class="option-group">
                    <label class="control-label">
                        <span>Scene:</span>
                        <select id="option_scene" class="setting-input">
                            <option value="">Select Scene</option>
                            ${generateSceneOptions()}
                        </select>
                    </label>
                </div>
            `;
            break;
    }
    
    optionsContainer.innerHTML = optionsHTML;
}

function generateServoOptions() {
    let options = '';
    for (let maestro = 1; maestro <= 2; maestro++) {
        for (let channel = 0; channel < 12; channel++) {
            options += `<option value="m${maestro}_ch${channel}">Maestro ${maestro} Ch ${channel}</option>`;
        }
    }
    return options;
}

function generateSceneOptions() {
    let options = '';
    scenes.forEach(scene => {
        options += `<option value="${scene.label}">${scene.label}</option>`;
    });
    return options;
}

function populateMappingOptions(mapping) {
    Object.entries(mapping).forEach(([key, value]) => {
        if (key === 'behavior') return;
        
        const element = document.getElementById(`option_${key}`);
        if (element) {
            if (element.type === 'checkbox') {
                element.checked = value;
            } else {
                element.value = value;
            }
        }
    });
}

function saveMappingEdit() {
    const inputName = document.getElementById('mappingInput').value;
    const behavior = document.getElementById('mappingBehavior').value;
    
    if (!inputName || !behavior) {
        showToast('Please select input and behavior', 'warning');
        return;
    }
    
    const mapping = { behavior: behavior };
    
    const optionsContainer = document.getElementById('mappingOptions');
    const inputs = optionsContainer.querySelectorAll('input, select');
    
    inputs.forEach(input => {
        const key = input.id.replace('option_', '');
        if (input.type === 'checkbox') {
            mapping[key] = input.checked;
        } else if (input.type === 'number') {
            mapping[key] = parseFloat(input.value);
        } else {
            mapping[key] = input.value;
        }
    });
    
    controllerMappings[inputName] = mapping;
    
    updateMappingList();
    hideMappingEditor();
    
    showToast(`Mapping saved for ${inputName}`, 'success');
}

function cancelMappingEdit() {
    hideMappingEditor();
}

function deleteMappingEdit() {
    if (editingMapping && confirm(`Delete mapping for ${editingMapping}?`)) {
        delete controllerMappings[editingMapping];
        updateMappingList();
        hideMappingEditor();
        showToast(`Mapping deleted for ${editingMapping}`, 'warning');
    }
}

function clearAllMappings() {
    if (confirm('Are you sure you want to clear all controller mappings?')) {
        controllerMappings = {};
        updateMappingList();
        showToast('All mappings cleared', 'warning');
    }
}

function loadDefaultMappings() {
    if (confirm('Load default controller mappings? This will replace current mappings.')) {
        controllerMappings = {
            "left_stick_x": {
                "behavior": "differential_tracks",
                "left_servo": "m2_ch0",
                "right_servo": "m2_ch1",
                "turn_sensitivity": 0.8
            },
            "right_stick_x": {
                "behavior": "direct_servo",
                "target": "m1_ch0",
                "sensitivity": 0.8
            },
            "button_a": {
                "behavior": "scene_trigger",
                "scene": "Happy",
                "trigger_timing": "on_press"
            }
        };
        
        updateMappingList();
        showToast('Default mappings loaded', 'success');
    }
}

// UI Utilities
function updateConnectionStatus(type, connected) {
    const statusDot = document.getElementById(type + 'Status');
    if (statusDot) {
        statusDot.classList.toggle('connected', connected);
    }
    
    if (type === 'ws') {
        const connInfo = document.getElementById('connectionInfo');
        if (connInfo) {
            connInfo.textContent = connected ? 'Connected' : 'Disconnected';
        }
        const lastUpdate = document.getElementById('lastUpdate');
        if (lastUpdate) {
            lastUpdate.textContent = new Date().toLocaleTimeString();
        }
    }
}

function showConnectionOverlay() {
    document.getElementById('connectionOverlay').classList.remove('hidden');
}

function hideConnectionOverlay() {
    document.getElementById('connectionOverlay').classList.add('hidden');
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    
    document.getElementById('toastContainer').appendChild(toast);
    
    setTimeout(() => {
        toast.remove();
    }, 3000);
}

function updateSystemStatus(data) {
    updateConnectionStatus('system', true);
    
    // Update settings page if visible
    const activeClientsElement = document.getElementById('activeClients');
    if (activeClientsElement) {
        activeClientsElement.textContent = data.connected_clients || '0';
    }
    
    const lastUpdateElement = document.getElementById('lastUpdate');
    if (lastUpdateElement) {
        lastUpdateElement.textContent = new Date().toLocaleTimeString();
    }
    
    // If the data contains telemetry info, update health displays
    if (data.hardware_status || data.type === 'telemetry') {
        updateHealthData(data);
    }
}