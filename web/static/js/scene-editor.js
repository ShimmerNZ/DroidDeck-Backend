// Enhanced Scene Management Functions for Droid Deck Web UI
// Integrates with existing socket-handler.js and ui-functions.js

// Scene Editor State
let currentEditingScene = null;
let allAudioFiles = [];
const availableCategories = ['Happy', 'Sad', 'Excited', 'Angry', 'Surprised', 'Confused', 'Positive', 'Negative', 'Emotion', 'Energetic', 'Calm'];
const commonEmojis = ['😊', '😢', '🤩', '😡', '😮', '😕', '🤔', '❤️', '💋', '👍', '🎭', '🎵', '⚡', '🌟', '🎉', '🤖', '💚', '💙', '🔥', '⭐'];

// Initialize Scene Editor
function initializeSceneEditor() {
    if (currentScreen === 'scenes') {
        renderSceneEditor();
        requestAudioFileList();
    }
}

// Render the complete scene editor interface
function renderSceneEditor() {
    const scenesList = document.getElementById('scenesList');
    if (!scenesList) return;

    scenesList.innerHTML = `
        <div class="scene-editor-container">
            <!-- Scene List Panel -->
            <div class="scene-list-panel">
                <div class="scene-list-header">
                    <h3>Scene Library</h3>
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn btn-small" onclick="addNewSceneEditor()">+ New Scene</button>
                        <button class="btn btn-secondary btn-small" onclick="importScenesFromBackend()">Import</button>
                    </div>
                </div>
                <div id="sceneEditorList" class="scene-editor-list"></div>
            </div>

            <!-- Editor Panel -->
            <div class="editor-panel-container">
                <div id="sceneEditorContent" class="scene-editor-content">
                    <div class="empty-state">
                        <div class="empty-state-icon">🎭</div>
                        <h3>Select a Scene to Edit</h3>
                        <p>Choose a scene from the library or create a new one to get started</p>
                        <button class="btn" onclick="addNewSceneEditor()" style="margin-top: 1rem;">
                            Create New Scene
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    updateSceneEditorList();
}

// Update the scene list in the editor
function updateSceneEditorList() {
    const listContainer = document.getElementById('sceneEditorList');
    if (!listContainer) return;

    if (scenes.length === 0) {
        listContainer.innerHTML = `
            <div class="empty-list-state">
                <p>No scenes available</p>
                <button class="btn btn-small" onclick="addNewSceneEditor()">Create First Scene</button>
            </div>
        `;
        return;
    }

    listContainer.innerHTML = scenes.map(scene => `
        <div class="scene-list-item ${currentEditingScene?.label === scene.label ? 'active' : ''}" 
             onclick="selectSceneForEdit('${scene.label}')">
            <div class="scene-item-header">
                <span class="scene-item-emoji">${scene.emoji || '🎭'}</span>
                <span class="scene-item-title">${scene.label}</span>
            </div>
            <div class="scene-item-meta">
                <span class="scene-badge">${scene.duration}s</span>
                ${scene.audio_enabled ? '<span class="scene-badge audio">Audio</span>' : ''}
                ${(scene.script_maestro1 !== null && scene.script_maestro1 !== undefined) || 
                  (scene.script_maestro2 !== null && scene.script_maestro2 !== undefined) ? 
                  '<span class="scene-badge script">Script</span>' : ''}
                ${scene.servo_count > 0 ? `<span class="scene-badge">${scene.servo_count} servos</span>` : ''}
            </div>
        </div>
    `).join('');
}

// Select a scene for editing
function selectSceneForEdit(sceneLabel) {
    currentEditingScene = scenes.find(s => s.label === sceneLabel);
    if (!currentEditingScene) {
        // Create a new scene object if not found
        currentEditingScene = {
            label: sceneLabel,
            emoji: '🎭',
            categories: [],
            audio_enabled: false,
            audio_file: '',
            script_maestro1: null,
            script_maestro2: null,
            duration: 2.0,
            delay: 0,
            servos: {}
        };
    }
    
    updateSceneEditorList();
    renderSceneEditorForm();
}

// Render the scene editor form
function renderSceneEditorForm() {
    const editorContent = document.getElementById('sceneEditorContent');
    if (!editorContent || !currentEditingScene) return;

    const servoCount = currentEditingScene.servos ? Object.keys(currentEditingScene.servos).length : 0;

    editorContent.innerHTML = `
        <div class="editor-header">
            <h2>Edit Scene: ${currentEditingScene.label}</h2>
            <div class="editor-actions">
                <button class="btn btn-success btn-small" onclick="testCurrentScene()">
                    <span>â–¶</span> Test
                </button>
                <button class="btn btn-small" onclick="saveCurrentScene()">
                    💾 Save
                </button>
                <button class="btn btn-danger btn-small" onclick="deleteCurrentScene()">
                    🗑️ Delete
                </button>
            </div>
        </div>

        <!-- Basic Information -->
        <div class="form-section">
            <div class="form-section-title">Basic Information</div>
            <div class="form-grid form-grid-2">
                <div class="form-group">
                    <label class="form-label">Scene Name</label>
                    <input type="text" class="form-input" id="edit_scene_name" 
                           value="${currentEditingScene.label}" 
                           onchange="updateEditingSceneProperty('label', this.value)">
                </div>
                <div class="form-group">
                    <label class="form-label">Duration (seconds)</label>
                    <input type="number" class="form-input" id="edit_duration" 
                           value="${currentEditingScene.duration}" 
                           min="0.1" step="0.1"
                           onchange="updateEditingSceneProperty('duration', parseFloat(this.value))">
                </div>
            </div>
            
            <div class="form-group">
                <label class="form-label">Emoji (Select or enter custom)</label>
                <input type="text" class="form-input" id="edit_emoji" 
                       value="${currentEditingScene.emoji || '🎭'}" 
                       maxlength="2" style="width: 100px; font-size: 1.5rem; text-align: center;"
                       onchange="updateEditingSceneProperty('emoji', this.value)">
                <div class="emoji-picker">
                    ${commonEmojis.map(emoji => `
                        <div class="emoji-option ${emoji === currentEditingScene.emoji ? 'selected' : ''}" 
                             onclick="selectEmoji('${emoji}')">
                            ${emoji}
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="form-group">
                <label class="form-label">Categories (Click to toggle)</label>
                <div class="category-selector">
                    ${availableCategories.map(cat => `
                        <div class="category-chip ${(currentEditingScene.categories || []).includes(cat) ? 'selected' : ''}"
                             onclick="toggleEditCategory('${cat}')">
                            ${cat}
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>

        <!-- Audio Configuration -->
        <div class="form-section">
            <div class="form-section-title">Audio Configuration</div>
            <div class="checkbox-wrapper">
                <div class="toggle-switch ${currentEditingScene.audio_enabled ? 'active' : ''}" 
                     onclick="toggleEditAudio()" id="edit_audio_toggle"></div>
                <span>Enable Audio</span>
            </div>
            <div id="audioConfigSection" style="display: ${currentEditingScene.audio_enabled ? 'block' : 'none'}">
                <div class="form-grid form-grid-2" style="margin-top: 1rem;">
                    <div class="form-group">
                        <label class="form-label">Audio File</label>
                        <select class="form-input" id="edit_audio_file" 
                                onchange="updateEditingSceneProperty('audio_file', this.value)">
                            <option value="">Select audio file...</option>
                            ${allAudioFiles.map(file => `
                                <option value="${file}" ${file === currentEditingScene.audio_file ? 'selected' : ''}>
                                    ${file}
                                </option>
                            `).join('')}
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label">Audio Delay (seconds)</label>
                        <input type="number" class="form-input" id="edit_delay" 
                               value="${currentEditingScene.delay || 0}" 
                               min="0" step="0.1"
                               onchange="updateEditingSceneProperty('delay', parseFloat(this.value))">
                    </div>
                </div>
            </div>
        </div>

        <!-- Script Configuration -->
        <div class="form-section">
            <div class="form-section-title">Script Configuration</div>
            <p style="color: var(--text-secondary); margin-bottom: 1rem;">
                Specify script numbers for each Maestro controller. Leave blank to not run a script.
            </p>
            <div class="form-group" style="margin-bottom: 1rem;">
                <label class="form-label">Maestro 1 Script Number</label>
                <input type="number" class="form-input" id="edit_script_maestro1" 
                       value="${currentEditingScene.script_maestro1 !== null && currentEditingScene.script_maestro1 !== undefined ? currentEditingScene.script_maestro1 : ''}" 
                       min="0" step="1" placeholder="None"
                       onchange="updateEditingSceneProperty('script_maestro1', this.value === '' ? null : parseInt(this.value))">
                <small style="color: var(--text-tertiary); display: block; margin-top: 0.5rem;">
                    Script to run on Maestro 1 controller
                </small>
            </div>
            <div class="form-group">
                <label class="form-label">Maestro 2 Script Number</label>
                <input type="number" class="form-input" id="edit_script_maestro2" 
                       value="${currentEditingScene.script_maestro2 !== null && currentEditingScene.script_maestro2 !== undefined ? currentEditingScene.script_maestro2 : ''}" 
                       min="0" step="1" placeholder="None"
                       onchange="updateEditingSceneProperty('script_maestro2', this.value === '' ? null : parseInt(this.value))">
                <small style="color: var(--text-tertiary); display: block; margin-top: 0.5rem;">
                    Script to run on Maestro 2 controller
                </small>
            </div>
        </div>

        <!-- Servo Configuration -->
        <div class="form-section">
            <div class="form-section-title">Servo Configuration</div>
            <p style="color: var(--text-secondary); margin-bottom: 1rem;">
                Configure servo positions and movements for this scene. Each servo can have target position, speed, and acceleration.
            </p>
            <div id="servoConfigList" class="servo-config">
                ${renderEditServoConfig()}
            </div>
            <div style="display: flex; gap: 0.5rem; margin-top: 1rem;">
                <button class="btn btn-secondary btn-small" onclick="showAddServoDialog()">
                    + Add Servo
                </button>
                ${servoCount > 0 ? `
                    <button class="btn btn-secondary btn-small" onclick="clearAllServos()">
                        Clear All
                    </button>
                ` : ''}
            </div>
        </div>

        <!-- Timeline Preview -->
        <div class="form-section">
            <div class="form-section-title">Timeline Preview</div>
            ${renderEditTimeline()}
        </div>

        <!-- Scene Information -->
        <div class="form-section">
            <div class="form-section-title">Scene Information</div>
            <div class="info-grid">
                <div class="info-item">
                    <span class="info-label">Total Duration:</span>
                    <span class="info-value">${currentEditingScene.duration}s</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Servo Count:</span>
                    <span class="info-value">${servoCount}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Categories:</span>
                    <span class="info-value">${(currentEditingScene.categories || []).join(', ') || 'None'}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Audio:</span>
                    <span class="info-value">${currentEditingScene.audio_enabled ? currentEditingScene.audio_file : 'Disabled'}</span>
                </div>
            </div>
        </div>
    `;
}

// Render servo configuration
function renderEditServoConfig() {
    const servos = currentEditingScene.servos || {};
    
    if (Object.keys(servos).length === 0) {
        return `
            <div class="empty-servo-state">
                <p>No servos configured for this scene</p>
                <p style="color: var(--text-tertiary); font-size: 0.875rem;">
                    Click "Add Servo" to configure servo movements
                </p>
            </div>
        `;
    }

    return Object.entries(servos).map(([servoId, config]) => `
        <div class="servo-item">
            <div class="servo-item-info">
                <span class="servo-label">${servoId}</span>
                <div class="servo-control">
                    <label class="servo-control-label">Target</label>
                    <input type="number" class="form-input servo-input" 
                           value="${config.target || 1500}" min="1000" max="2000"
                           onchange="updateEditServoProperty('${servoId}', 'target', parseInt(this.value))">
                </div>
                <div class="servo-control">
                    <label class="servo-control-label">Speed</label>
                    <input type="number" class="form-input servo-input" 
                           value="${config.speed || 50}" min="0" max="100"
                           onchange="updateEditServoProperty('${servoId}', 'speed', parseInt(this.value))">
                </div>
                <div class="servo-control">
                    <label class="servo-control-label">Accel</label>
                    <input type="number" class="form-input servo-input" 
                           value="${config.acceleration || 30}" min="0" max="100"
                           onchange="updateEditServoProperty('${servoId}', 'acceleration', parseInt(this.value))">
                </div>
            </div>
            <button class="btn btn-danger btn-small" onclick="removeEditServo('${servoId}')">Ã—</button>
        </div>
    `).join('');
}

// Render timeline preview
function renderEditTimeline() {
    const duration = currentEditingScene.duration;
    const delay = currentEditingScene.delay || 0;
    const servoCount = currentEditingScene.servos ? Object.keys(currentEditingScene.servos).length : 0;

    return `
        <div class="timeline">
            <div class="timeline-track">
                <div class="timeline-label">Total Duration</div>
                <div class="timeline-bar">
                    <div class="timeline-segment" style="left: 0; width: 100%; background: rgba(0, 212, 255, 0.2); border: 1px solid #00d4ff;">
                        ${duration}s
                    </div>
                </div>
            </div>
            ${currentEditingScene.audio_enabled && currentEditingScene.audio_file ? `
                <div class="timeline-track">
                    <div class="timeline-label">Audio</div>
                    <div class="timeline-bar">
                        ${delay > 0 ? `
                            <div class="timeline-segment" style="left: 0; width: ${(delay / duration) * 100}%; background: rgba(255, 255, 255, 0.05);">
                                ${delay}s delay
                            </div>
                        ` : ''}
                        <div class="timeline-segment audio" 
                             style="left: ${(delay / duration) * 100}%; width: ${((duration - delay) / duration) * 100}%;">
                            ${currentEditingScene.audio_file}
                        </div>
                    </div>
                </div>
            ` : ''}
            ${servoCount > 0 ? `
                <div class="timeline-track">
                    <div class="timeline-label">Servos</div>
                    <div class="timeline-bar">
                        <div class="timeline-segment servo" style="left: 0; width: 100%;">
                            ${servoCount} servo${servoCount !== 1 ? 's' : ''}
                        </div>
                    </div>
                </div>
            ` : ''}
            ${currentEditingScene.script_maestro1 !== null && currentEditingScene.script_maestro1 !== undefined ? `
                <div class="timeline-track">
                    <div class="timeline-label">M1 Script</div>
                    <div class="timeline-bar">
                        <div class="timeline-segment script" style="left: 0; width: 100%;">
                            Script #${currentEditingScene.script_maestro1}
                        </div>
                    </div>
                </div>
            ` : ''}
            ${currentEditingScene.script_maestro2 !== null && currentEditingScene.script_maestro2 !== undefined ? `
                <div class="timeline-track">
                    <div class="timeline-label">M2 Script</div>
                    <div class="timeline-bar">
                        <div class="timeline-segment script" style="left: 0; width: 100%;">
                            Script #${currentEditingScene.script_maestro2}
                        </div>
                    </div>
                </div>
            ` : ''}
        </div>
    `;
}

// Update scene property
function updateEditingSceneProperty(property, value) {
    if (currentEditingScene) {
        currentEditingScene[property] = value;
        
        // Update the corresponding scene in the scenes array
        const sceneIndex = scenes.findIndex(s => s.label === currentEditingScene.label);
        if (sceneIndex !== -1) {
            scenes[sceneIndex][property] = value;
        }
        
        // Re-render timeline to show changes
        if (property === 'duration' || property === 'delay') {
            const timelineContainer = document.querySelector('.timeline');
            if (timelineContainer) {
                timelineContainer.outerHTML = renderEditTimeline();
            }
        }
    }
}

// Update servo property
function updateEditServoProperty(servoId, property, value) {
    if (currentEditingScene && currentEditingScene.servos && currentEditingScene.servos[servoId]) {
        currentEditingScene.servos[servoId][property] = value;
        
        // Update in scenes array
        const sceneIndex = scenes.findIndex(s => s.label === currentEditingScene.label);
        if (sceneIndex !== -1 && scenes[sceneIndex].servos) {
            scenes[sceneIndex].servos[servoId][property] = value;
        }
    }
}

// Toggle category
function toggleEditCategory(category) {
    if (!currentEditingScene) return;
    
    if (!currentEditingScene.categories) {
        currentEditingScene.categories = [];
    }
    
    const index = currentEditingScene.categories.indexOf(category);
    if (index > -1) {
        currentEditingScene.categories.splice(index, 1);
    } else {
        currentEditingScene.categories.push(category);
    }
    
    // Update in scenes array
    const sceneIndex = scenes.findIndex(s => s.label === currentEditingScene.label);
    if (sceneIndex !== -1) {
        scenes[sceneIndex].categories = [...currentEditingScene.categories];
    }
    
    renderSceneEditorForm();
}

// Select emoji
function selectEmoji(emoji) {
    updateEditingSceneProperty('emoji', emoji);
    document.getElementById('edit_emoji').value = emoji;
    
    // Update visual selection
    document.querySelectorAll('.emoji-option').forEach(el => {
        el.classList.remove('selected');
    });
    event.target.classList.add('selected');
}

// Toggle audio
function toggleEditAudio() {
    if (!currentEditingScene) return;
    
    currentEditingScene.audio_enabled = !currentEditingScene.audio_enabled;
    
    // Update in scenes array
    const sceneIndex = scenes.findIndex(s => s.label === currentEditingScene.label);
    if (sceneIndex !== -1) {
        scenes[sceneIndex].audio_enabled = currentEditingScene.audio_enabled;
    }
    
    const toggle = document.getElementById('edit_audio_toggle');
    const section = document.getElementById('audioConfigSection');
    
    if (toggle) {
        toggle.classList.toggle('active');
    }
    if (section) {
        section.style.display = currentEditingScene.audio_enabled ? 'block' : 'none';
    }
    
    // Re-render timeline
    const timelineContainer = document.querySelector('.timeline');
    if (timelineContainer) {
        timelineContainer.outerHTML = renderEditTimeline();
    }
}

// Show add servo dialog
function showAddServoDialog() {
    const servoId = prompt('Enter servo ID (e.g., m1_ch0 for Maestro 1, Channel 0):');
    if (!servoId) return;
    
    // Validate servo ID format
    if (!/^m[12]_ch\d+$/.test(servoId)) {
        showToast('Invalid servo ID format. Use format: m1_ch0 or m2_ch5', 'error');
        return;
    }
    
    if (!currentEditingScene.servos) {
        currentEditingScene.servos = {};
    }
    
    if (currentEditingScene.servos[servoId]) {
        showToast('Servo already exists in this scene', 'warning');
        return;
    }
    
    currentEditingScene.servos[servoId] = {
        target: 1500,
        speed: 50,
        acceleration: 30
    };
    
    // Update in scenes array
    const sceneIndex = scenes.findIndex(s => s.label === currentEditingScene.label);
    if (sceneIndex !== -1) {
        scenes[sceneIndex].servos = { ...currentEditingScene.servos };
        scenes[sceneIndex].servo_count = Object.keys(currentEditingScene.servos).length;
    }
    
    renderSceneEditorForm();
    showToast(`Added servo ${servoId}`, 'success');
}

// Remove servo
function removeEditServo(servoId) {
    if (!currentEditingScene || !currentEditingScene.servos) return;
    
    if (confirm(`Remove servo ${servoId} from this scene?`)) {
        delete currentEditingScene.servos[servoId];
        
        // Update in scenes array
        const sceneIndex = scenes.findIndex(s => s.label === currentEditingScene.label);
        if (sceneIndex !== -1) {
            delete scenes[sceneIndex].servos[servoId];
            scenes[sceneIndex].servo_count = Object.keys(scenes[sceneIndex].servos).length;
        }
        
        renderSceneEditorForm();
        showToast(`Removed servo ${servoId}`, 'success');
    }
}

// Clear all servos
function clearAllServos() {
    if (!currentEditingScene) return;
    
    if (confirm('Remove all servos from this scene?')) {
        currentEditingScene.servos = {};
        
        // Update in scenes array
        const sceneIndex = scenes.findIndex(s => s.label === currentEditingScene.label);
        if (sceneIndex !== -1) {
            scenes[sceneIndex].servos = {};
            scenes[sceneIndex].servo_count = 0;
        }
        
        renderSceneEditorForm();
        showToast('All servos removed', 'success');
    }
}

// Add new scene
function addNewSceneEditor() {
    const sceneName = prompt('Enter new scene name:');
    if (!sceneName || !sceneName.trim()) return;
    
    // Check if scene already exists
    if (scenes.find(s => s.label === sceneName)) {
        showToast('Scene with this name already exists', 'error');
        return;
    }
    
    const newScene = {
        label: sceneName,
        emoji: '🎭',
        categories: [],
        audio_enabled: false,
        audio_file: '',
        script_maestro1: null,
        script_maestro2: null,
        duration: 2.0,
        delay: 0,
        servos: {},
        servo_count: 0
    };
    
    scenes.push(newScene);
    selectSceneForEdit(sceneName);
    showToast(`Created new scene: ${sceneName}`, 'success');
}

// Test current scene
function testCurrentScene() {
    if (!currentEditingScene) return;
    playScene(currentEditingScene.label);
    showToast(`Testing scene: ${currentEditingScene.label}`, 'info');
}

// Save current scene
function saveCurrentScene() {
    if (!currentEditingScene) return;
    
    // Send to backend to save
    sendWebSocketMessage({
        type: 'save_scene',
        scene_data: currentEditingScene
    });
    
    showToast(`Saving scene: ${currentEditingScene.label}`, 'success');
}

// Delete current scene
function deleteCurrentScene() {
    if (!currentEditingScene) return;
    
    if (confirm(`Delete scene "${currentEditingScene.label}"? This action cannot be undone.`)) {
        scenes = scenes.filter(s => s.label !== currentEditingScene.label);
        
        // Send to backend to delete
        sendWebSocketMessage({
            type: 'delete_scene',
            scene_name: currentEditingScene.label
        });
        
        showToast(`Deleted scene: ${currentEditingScene.label}`, 'warning');
        
        currentEditingScene = null;
        updateSceneEditorList();
        
        const editorContent = document.getElementById('sceneEditorContent');
        if (editorContent) {
            editorContent.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🎭</div>
                    <h3>Scene Deleted</h3>
                    <p>Select another scene or create a new one</p>
                </div>
            `;
        }
    }
}

// Request audio file list from backend
function requestAudioFileList() {
    sendWebSocketMessage({ type: 'get_audio_files' });
}

// Handle audio files response
function handleAudioFilesResponse(data) {
    if (data.files && Array.isArray(data.files)) {
        allAudioFiles = data.files;
        if (currentEditingScene && currentScreen === 'scenes') {
            renderSceneEditorForm();
        }
    }
}

// Initialize when scenes screen is shown
function onScenesScreenShown() {
    renderSceneEditor();
    requestAudioFileList();
}