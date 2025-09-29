// Global application state and configuration
let socket = null;
let currentTheme = 'modern';
let currentScreen = 'home';
let currentMaestro = 1;
let scenes = [];
let categories = [];
let currentCategoryIndex = 0;
let currentSceneIndex = 0;
let filteredScenes = [];
let controllerMappings = {};
let editingMapping = null;
let autoReconnect = true;
let reconnectAttempts = 0;
let maxReconnectAttempts = 10;
let updateInterval = 5000;
let enableAnimations = true;
let showGridView = false;
let nemaEnabled = false;
let scenePlayingLocked = false;
let currentlyPlayingScene = null;

// Initialize the application
document.addEventListener('DOMContentLoaded', function() {
    loadSettings();
    initializeSocket();
    initializeControllerMappings();
    
    // Handle navigation with keyboard
    document.addEventListener('keydown', handleKeyboardNavigation);
    
    // Start periodic updates
    setInterval(periodicUpdate, updateInterval);
});

// Settings management
function loadSettings() {
    const savedTheme = localStorage.getItem('droidDeckTheme') || 'modern';
    const savedWsUrl = localStorage.getItem('droidDeckWsUrl') || 'ws://10.1.1.230:8766';
    const savedAutoReconnect = localStorage.getItem('droidDeckAutoReconnect') !== 'false';
    const savedUpdateInterval = localStorage.getItem('droidDeckUpdateInterval') || '5000';
    const savedAnimations = localStorage.getItem('droidDeckAnimations') !== 'false';
    
    currentTheme = savedTheme;
    autoReconnect = savedAutoReconnect;
    updateInterval = parseInt(savedUpdateInterval);
    enableAnimations = savedAnimations;
    
    // Apply theme
    document.body.className = getThemeClass(savedTheme);
    
    // Update UI elements
    if (document.getElementById('themeSelect')) {
        document.getElementById('themeSelect').value = savedTheme;
        document.getElementById('wsUrl').value = savedWsUrl;
        document.getElementById('autoReconnect').checked = savedAutoReconnect;
        document.getElementById('updateInterval').value = savedUpdateInterval;
        document.getElementById('enableAnimations').checked = savedAnimations;
        document.getElementById('themeText').textContent = getThemeDisplayName(savedTheme);
    }
}

function saveSettings() {
    const wsUrl = document.getElementById('wsUrl').value;
    const autoReconnectSetting = document.getElementById('autoReconnect').checked;
    const intervalSetting = document.getElementById('updateInterval').value;
    const animationsSetting = document.getElementById('enableAnimations').checked;
    
    localStorage.setItem('droidDeckWsUrl', wsUrl);
    localStorage.setItem('droidDeckAutoReconnect', autoReconnectSetting);
    localStorage.setItem('droidDeckUpdateInterval', intervalSetting);
    localStorage.setItem('droidDeckAnimations', animationsSetting);
    
    autoReconnect = autoReconnectSetting;
    updateInterval = parseInt(intervalSetting);
    enableAnimations = animationsSetting;
    
    showToast('Settings saved', 'success');
}

function resetToDefaults() {
    if (confirm('Are you sure you want to reset all settings to defaults?')) {
        localStorage.removeItem('droidDeckTheme');
        localStorage.removeItem('droidDeckWsUrl');
        localStorage.removeItem('droidDeckAutoReconnect');
        localStorage.removeItem('droidDeckUpdateInterval');
        localStorage.removeItem('droidDeckAnimations');
        
        loadSettings();
        showToast('Settings reset to defaults', 'success');
    }
}

// Theme management
function toggleTheme() {
    const themes = ['modern', 'wall-e', 'star-wars'];
    const currentIndex = themes.indexOf(currentTheme);
    const nextTheme = themes[(currentIndex + 1) % themes.length];
    changeThemeToValue(nextTheme);
}

function changeTheme() {
    const selectedTheme = document.getElementById('themeSelect').value;
    changeThemeToValue(selectedTheme);
}

function changeThemeToValue(theme) {
    currentTheme = theme;
    document.body.className = getThemeClass(theme);
    document.getElementById('themeText').textContent = getThemeDisplayName(theme);
    document.getElementById('themeSelect').value = theme;
    localStorage.setItem('droidDeckTheme', theme);
}

function getThemeClass(theme) {
    switch (theme) {
        case 'wall-e': return 'theme-wall-e';
        case 'star-wars': return 'theme-star-wars';
        default: return '';
    }
}

function getThemeDisplayName(theme) {
    switch (theme) {
        case 'wall-e': return 'WALL-E';
        case 'star-wars': return 'Star Wars';
        default: return 'Modern';
    }
}

function showScreen(screenName) {
    // Hide all screens
    document.querySelectorAll('.screen').forEach(screen => {
        screen.classList.remove('active');
    });
    
    // Show selected screen
    document.getElementById(screenName + 'Screen').classList.add('active');
    
    // Update navigation
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    event.target.classList.add('active');
    
    currentScreen = screenName;
    
    // Load screen-specific data
    switch (screenName) {
        case 'home':
            if (scenes.length > 0) {
                updateScenes(scenes);
            }
            break;
        case 'servo':
            loadServoControls();
            getAllServoPositions();
            break;
        case 'health':
            requestSystemStatus();
            break;
        case 'controller':
            requestControllerInfo();
            loadControllerConfig();
            updateMappingList();
            break;
        case 'scenes':
            // Update this case to include the scene editor
            initializeSceneEditor();  // ADD THIS LINE
            break;
        case 'nema':
            getNemaStatus();
            break;
    }
}

function handleKeyboardNavigation(event) {
    switch (event.key) {
        case 'ArrowUp':
            event.preventDefault();
            handleNavigationCommand('up');
            break;
        case 'ArrowDown':
            event.preventDefault();
            handleNavigationCommand('down');
            break;
        case 'ArrowLeft':
            event.preventDefault();
            handleNavigationCommand('left');
            break;
        case 'ArrowRight':
            event.preventDefault();
            handleNavigationCommand('right');
            break;
        case 'Enter':
            event.preventDefault();
            handleNavigationCommand('select');
            break;
    }
}

function periodicUpdate() {
    if (socket && socket.connected && currentScreen === 'health') {
        requestSystemStatus();
    }
    
    if (socket && socket.connected && currentScreen === 'nema') {
        getNemaStatus();
    }
    
    if (socket && socket.connected && currentScreen === 'controller') {
        requestControllerInfo();
    }
    const clientCount = document.getElementById('clientCount');
    if (clientCount) {
        clientCount.textContent = '1';
    }
    const activeClients = document.getElementById('activeClients');
    if (activeClients) {
        activeClients.textContent = '1';
    }
    const lastUpdate = document.getElementById('lastUpdate');
    if (lastUpdate) {
        lastUpdate.textContent = new Date().toLocaleTimeString();
    }
}

// Controller Mappings initialization
function initializeControllerMappings() {
    loadControllerConfig();
}

// Additional theme styles
function addThemeStyles() {
    const style = document.createElement('style');
    style.textContent = `
        .theme-wall-e {
            --bg-primary: #1a1a1a;
            --bg-secondary: #2a2a2a;
            --bg-tertiary: #333333;
            --accent-primary: #ffd500;
            --accent-secondary: #ffb700;
            --accent-tertiary: #ffe44d;
            --accent-gradient: linear-gradient(135deg, #ffd500, #ffb700);
            --success: #4ade80;
            --warning: #ffd700;
            --error: #ef4444;
        }
        
        .theme-star-wars {
            --bg-primary: #0c0c0c;
            --bg-secondary: #1a1a1a;
            --bg-tertiary: #262626;
            --accent-primary: #0088cc;  
            --accent-secondary: #0066aa;
            --accent-tertiary: #33aaee;  
            --accent-gradient: linear-gradient(135deg, #0088cc, #0066aa);
            --success: #00ff88;
            --warning: #ffaa00;
            --error: #ff4444;
        }
    `;
    document.head.appendChild(style);
}

// Initialize theme styles
addThemeStyles();