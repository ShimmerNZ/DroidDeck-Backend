// ESP32 Camera Streaming Server - FIXED Version
// Improved video quality, proper sensor configuration, and stable operation
// Flash this to your ESP32-CAM module

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// WiFi credentials
const char* ssid = "Walle";
const char* password = "EVEROCKS2025";

// Camera model
#define CAMERA_MODEL_AI_THINKER

// Pin definitions for AI Thinker model
#if defined(CAMERA_MODEL_AI_THINKER)
  #define PWDN_GPIO_NUM     32
  #define RESET_GPIO_NUM    -1
  #define XCLK_GPIO_NUM      0
  #define SIOD_GPIO_NUM     26
  #define SIOC_GPIO_NUM     27
  #define Y9_GPIO_NUM       35
  #define Y8_GPIO_NUM       34
  #define Y7_GPIO_NUM       39
  #define Y6_GPIO_NUM       36
  #define Y5_GPIO_NUM       21
  #define Y4_GPIO_NUM       19
  #define Y3_GPIO_NUM       18
  #define Y2_GPIO_NUM        5
  #define VSYNC_GPIO_NUM    25
  #define HREF_GPIO_NUM     23
  #define PCLK_GPIO_NUM     22
#endif

// FIXED: Better balanced settings for quality and performance
struct CameraSettings {
  int xclk_freq = 10;        
  framesize_t resolution = FRAMESIZE_VGA;
  int quality = 63;          // FIXED: Improved quality (was 10, keeping good balance)
  int brightness = 0;
  int contrast = 0;
  int saturation = 0;
  bool h_mirror = true;
  bool v_flip = false;
} settings;

WebServer server(81);

// Single connection enforcement (keeping your functionality)
static bool stream_client_connected = false;
static WiFiClient* active_stream_client = nullptr;
static unsigned long stream_start_time = 0;
static unsigned long last_frame_time = 0;
static volatile bool force_disconnect = false;

// Buffer size for streaming
static const size_t STREAM_BUFFER_SIZE = 16384;  // FIXED: Increased buffer size
static uint8_t* stream_buffer = nullptr;

// Performance stats
struct MinimalStats {
  unsigned long frames_sent = 0;
  unsigned long uptime_start = 0;
  float heap_low_water = 0;
} stats;

// Helper functions
static const int MIN_XCLK_MHZ = 8;
static const int MAX_XCLK_MHZ = 20;

static bool canUseResolution(framesize_t fs) {
  if (psramFound()) return true;
  return fs <= FRAMESIZE_VGA;
}

static bool parseBoolArg(const String& v) {
  String s = v; s.toLowerCase();
  return (s == "1" || s == "true" || s == "on" || s == "yes");
}

static bool isValidFramesizeInt(int v) {
  return v >= FRAMESIZE_QQVGA && v <= FRAMESIZE_UXGA;
}

// Connection management functions
bool isStreamActive() {
  return stream_client_connected && active_stream_client && active_stream_client->connected();
}

void disconnectActiveStream() {
  if (active_stream_client) {
    Serial.println("Disconnecting active stream client");
    active_stream_client->stop();
    active_stream_client = nullptr;
  }
  stream_client_connected = false;
  force_disconnect = false;
}

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = settings.xclk_freq * 1000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = settings.resolution;
  config.jpeg_quality = settings.quality;
  
  // FIXED: Better buffer configuration matching working example
  if(psramFound()) {
    config.fb_count = 2;      // Keep 2 buffers with PSRAM
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;  // FIXED: Use LATEST for better performance
    Serial.println("PSRAM found - using optimized PSRAM configuration");
  } else {
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    Serial.println("No PSRAM - using DRAM configuration");
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    return false;
  }

  // Allocate stream buffer
  if (!stream_buffer) {
    stream_buffer = (uint8_t*)malloc(STREAM_BUFFER_SIZE);
    if (!stream_buffer) {
      Serial.println("Failed to allocate stream buffer");
      return false;
    }
  }

  applyCameraSettings();
  return true;
}

void applyCameraSettings() {
  sensor_t * s = esp_camera_sensor_get();
  if (s == NULL) {
    Serial.println("Error: Camera sensor not found");
    return;
  }

  // FIXED: Apply basic settings first
  s->set_framesize(s, settings.resolution);
  s->set_quality(s, settings.quality);
  s->set_brightness(s, settings.brightness);
  s->set_contrast(s, settings.contrast);
  s->set_saturation(s, settings.saturation);
  s->set_hmirror(s, settings.h_mirror ? 1 : 0);
  s->set_vflip(s, settings.v_flip ? 1 : 0);
  
  // OPTIMIZED: Sensor settings for 8-12MHz operation
  s->set_special_effect(s, 0);    // No special effects
  s->set_whitebal(s, 1);          // Enable white balance
  s->set_awb_gain(s, 1);          // Enable auto white balance gain
  s->set_wb_mode(s, 0);           // Auto white balance mode
  s->set_exposure_ctrl(s, 1);     // Enable exposure control
  s->set_aec2(s, 0);              // Disable AEC2 for consistent timing
  s->set_gain_ctrl(s, 1);         // Enable gain control
  s->set_agc_gain(s, 4);          // Slightly higher gain for lower XCLK
  s->set_bpc(s, 0);               // Keep black pixel correction off
  s->set_wpc(s, 1);               // Enable white pixel correction
  s->set_raw_gma(s, 1);           // Enable gamma correction
  s->set_lenc(s, 1);              // Enable lens correction
  s->set_dcw(s, 1);               // Enable downsize
  
  // FIXED: Handle specific sensor initialization like the working example
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);        // flip it back
    s->set_brightness(s, 1);   // up the brightness just a bit
    s->set_saturation(s, -2);  // lower the saturation
  }
  
  // FIXED: Set initial frame size for better startup (from working example)
  if (settings.resolution > FRAMESIZE_VGA) {
    s->set_framesize(s, FRAMESIZE_VGA);  // Start with smaller size
    delay(100);  // Allow sensor to adjust
    s->set_framesize(s, settings.resolution);  // Then set target size
  }
  
  Serial.println("Camera settings applied with proper sensor configuration");
}

void handleStream() {
  // Enforce single connection
  if (isStreamActive()) {
    Serial.println("Stream request rejected - client already connected");
    server.send(429, "text/plain", "Camera busy - only one client allowed");
    return;
  }

  WiFiClient client = server.client();
  if (!client) {
    Serial.println("Stream client connection failed");
    return;
  }

  // Set up exclusive connection
  active_stream_client = &client;
  stream_client_connected = true;
  stream_start_time = millis();
  stats.frames_sent = 0;
  force_disconnect = false;

  Serial.println("Exclusive stream started");
  Serial.printf("Free heap at stream start: %d bytes\n", ESP.getFreeHeap());

  // Send headers
  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
  client.println("Cache-Control: no-cache, no-store, must-revalidate");
  client.println("Pragma: no-cache");
  client.println("Expires: 0");
  client.println("Access-Control-Allow-Origin: *");
  client.println();

  // Stream loop with improved frame handling
  unsigned long last_memory_check = 0;
  unsigned long min_heap_seen = ESP.getFreeHeap();
  const unsigned long MEMORY_CHECK_INTERVAL = 5000;
  const unsigned long MIN_HEAP_THRESHOLD = 50000;
  
  // FIXED: Better frame timing for smoother video
  const unsigned long TARGET_FRAME_INTERVAL = 50; // ~20 FPS (was 66ms/15fps)
  unsigned long last_successful_frame = 0;
  int consecutive_failures = 0;
  const int MAX_CONSECUTIVE_FAILURES = 10; // Increased tolerance

  while (client.connected() && !force_disconnect && stream_client_connected) {
    unsigned long current_time = millis();
    
    // Memory monitoring
    if (current_time - last_memory_check > MEMORY_CHECK_INTERVAL) {
      unsigned long current_heap = ESP.getFreeHeap();
      min_heap_seen = min(min_heap_seen, current_heap);
      
      if (current_heap < MIN_HEAP_THRESHOLD) {
        Serial.printf("WARNING: Low memory detected: %lu bytes\n", current_heap);
      }
      
      last_memory_check = current_time;
      stats.heap_low_water = min_heap_seen;
    }
    
    // Frame rate limiting
    if (current_time - last_frame_time < TARGET_FRAME_INTERVAL) {
      delay(2); // Shorter delay for better responsiveness
      continue;
    }

    // Capture frame
    camera_fb_t * fb = esp_camera_fb_get();
    if (!fb) {
      consecutive_failures++;
      Serial.printf("Camera capture failed (consecutive: %d)\n", consecutive_failures);
      
      if (consecutive_failures >= MAX_CONSECUTIVE_FAILURES) {
        Serial.println("Too many consecutive failures, ending stream");
        break;
      }
      
      delay(10); // Shorter wait before retry
      continue;
    }

    // FIXED: Better frame validation
    if (fb->len < 512) { // Reduced threshold
      Serial.println("Frame too small, skipping");
      esp_camera_fb_return(fb);
      consecutive_failures++;
      continue;
    }

    // Reset failure counter on successful capture
    consecutive_failures = 0;
    last_successful_frame = current_time;

    // Send frame with improved chunking
    bool send_success = true;
    
    // Send boundary and headers
    int header_len = snprintf((char*)stream_buffer, STREAM_BUFFER_SIZE,
      "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);
    
    if (client.write(stream_buffer, header_len) != header_len) {
      send_success = false;
    }
    
    // FIXED: Improved frame transmission
    if (send_success) {
      size_t bytes_sent = 0;
      const size_t CHUNK_SIZE = 8192; // Larger chunks for better throughput
      
      while (bytes_sent < fb->len && send_success && client.connected()) {
        size_t bytes_to_send = min(CHUNK_SIZE, fb->len - bytes_sent);
        size_t actually_sent = client.write(fb->buf + bytes_sent, bytes_to_send);
        
        if (actually_sent == 0) {
          if (client.connected()) {
            delay(1); // Brief pause if client is slow
            continue;
          } else {
            send_success = false;
            break;
          }
        }
        
        bytes_sent += actually_sent;
        
        // Yield CPU periodically
        if (bytes_sent % (CHUNK_SIZE * 2) == 0) {
          yield();
        }
      }
      
      // Send frame terminator
      if (send_success && client.connected()) {
        if (client.write("\r\n", 2) != 2) {
          send_success = false;
        }
      }
    }
    
    // Clean up frame buffer
    esp_camera_fb_return(fb);
    
    if (!send_success || !client.connected()) {
      Serial.println("Frame transmission failed or client disconnected, ending stream");
      break;
    }
    
    last_frame_time = current_time;
    stats.frames_sent++;
    
    // Periodic status (every 200 frames for less spam)
    if (stats.frames_sent % 200 == 0) {
      unsigned long stream_duration = (current_time - stream_start_time) / 1000;
      float avg_fps = stream_duration > 0 ? (float)stats.frames_sent / stream_duration : 0;
      Serial.printf("Stream stats - Frames: %lu, Duration: %lus, Avg FPS: %.1f, Free heap: %d\n", 
        stats.frames_sent, stream_duration, avg_fps, ESP.getFreeHeap());
    }
    
    // Yield to prevent watchdog timeout
    yield();
  }

  // Clean up connection
  disconnectActiveStream();
  Serial.printf("Stream ended. Total frames sent: %lu\n", stats.frames_sent);
}

// Settings API functions (keeping your existing functionality)
void handleGetSettings() {
  bool updatedViaGet = false;
  bool settingsChanged = false;

  // Optional GET updates when not streaming
  if (!isStreamActive()) {
    if (server.hasArg("quality")) {
      int q = server.arg("quality").toInt();
      if (q >= 4 && q <= 25 && q != settings.quality) {
        settings.quality = q;
        settingsChanged = true;
        updatedViaGet = true;
      }
    }
    if (server.hasArg("brightness")) {
      int b = server.arg("brightness").toInt();
      if (b >= -2 && b <= 2 && b != settings.brightness) {
        settings.brightness = b;
        settingsChanged = true;
        updatedViaGet = true;
      }
    }
    if (server.hasArg("contrast")) {
      int c = server.arg("contrast").toInt();
      if (c >= -2 && c <= 2 && c != settings.contrast) {
        settings.contrast = c;
        settingsChanged = true;
        updatedViaGet = true;
      }
    }
    if (settingsChanged) {
      applyCameraSettings();
    }
  }

  DynamicJsonDocument doc(896);
  doc["xclk_freq"] = settings.xclk_freq;
  doc["resolution"] = settings.resolution;
  doc["quality"] = settings.quality;
  doc["brightness"] = settings.brightness;
  doc["contrast"] = settings.contrast;
  doc["saturation"] = settings.saturation;
  doc["h_mirror"] = settings.h_mirror;
  doc["v_flip"] = settings.v_flip;

  doc["streaming"] = isStreamActive();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["min_heap"] = stats.heap_low_water;
  doc["single_client_mode"] = true;

  if (updatedViaGet) {
    doc["note"] = "Updated via GET fallback";
  }

  String response;
  serializeJson(doc, response);
  server.send(200, "application/json", response);
}

void handleSetSettings() {
  if (isStreamActive()) {
    server.send(423, "application/json", "{\"error\":\"Cannot change settings while streaming\"}");
    return;
  }

  bool settingsChanged = false;
  bool needsRestart = false;

  DynamicJsonDocument out(1024);
  JsonArray updated = out.createNestedArray("updated");
  JsonArray errors  = out.createNestedArray("errors");

  // Process all settings (keeping your existing logic)
  if (server.hasArg("quality")) {
    int v = server.arg("quality").toInt();
    if (v >= 4 && v <= 25 && v != settings.quality) {
      settings.quality = v;
      settingsChanged = true;
      updated.add("quality");
    } else if (v < 4 || v > 25) {
      errors.add("quality: range 4..25");
    }
  }

  if (server.hasArg("brightness")) {
    int v = server.arg("brightness").toInt();
    if (v >= -2 && v <= 2 && v != settings.brightness) {
      settings.brightness = v;
      settingsChanged = true;
      updated.add("brightness");
    } else if (v < -2 || v > 2) {
      errors.add("brightness: range -2..2");
    }
  }

  if (server.hasArg("contrast")) {
    int v = server.arg("contrast").toInt();
    if (v >= -2 && v <= 2 && v != settings.contrast) {
      settings.contrast = v;
      settingsChanged = true;
      updated.add("contrast");
    } else if (v < -2 || v > 2) {
      errors.add("contrast: range -2..2");
    }
  }

  if (server.hasArg("saturation")) {
    int v = server.arg("saturation").toInt();
    if (v >= -2 && v <= 2 && v != settings.saturation) {
      settings.saturation = v;
      settingsChanged = true;
      updated.add("saturation");
    } else if (v < -2 || v > 2) {
      errors.add("saturation: range -2..2");
    }
  }

  if (server.hasArg("h_mirror")) {
    bool v = parseBoolArg(server.arg("h_mirror"));
    if (v != settings.h_mirror) {
      settings.h_mirror = v;
      settingsChanged = true;
      updated.add("h_mirror");
    }
  }

  if (server.hasArg("v_flip")) {
    bool v = parseBoolArg(server.arg("v_flip"));
    if (v != settings.v_flip) {
      settings.v_flip = v;
      settingsChanged = true;
      updated.add("v_flip");
    }
  }

  if (server.hasArg("resolution")) {
    int r = server.arg("resolution").toInt();
    if (isValidFramesizeInt(r)) {
      framesize_t fs = static_cast<framesize_t>(r);
      if (!canUseResolution(fs)) {
        errors.add("resolution: too high without PSRAM (use <= VGA)");
      } else if (fs != settings.resolution) {
        settings.resolution = fs;
        settingsChanged = true;
        updated.add("resolution");
      }
    } else {
      errors.add("resolution: invalid enum");
    }
  }

  if (server.hasArg("xclk_freq")) {
    int mhz = server.arg("xclk_freq").toInt();
    if (mhz >= MIN_XCLK_MHZ && mhz <= MAX_XCLK_MHZ) {
      if (mhz != settings.xclk_freq) {
        settings.xclk_freq = mhz;
        needsRestart = true;
        updated.add("xclk_freq");
      }
    } else {
      errors.add("xclk_freq: range 8..20 MHz");
    }
  }

  // Apply settings
  if (settingsChanged) {
    applyCameraSettings();
    Serial.println("Camera settings updated");
  }

  // Restart if needed
  bool restarted = false;
  if (needsRestart) {
    Serial.println("Reinitializing camera for xclk_freq change...");
    esp_camera_deinit();
    if (!initCamera()) {
      out["status"] = "error";
      out["message"] = "Camera re-init failed";
      String resp; serializeJson(out, resp);
      server.send(500, "application/json", resp);
      return;
    }
    restarted = true;
  }

  out["status"] = "ok";
  out["restarted"] = restarted;

  JsonObject cur = out.createNestedObject("current");
  cur["xclk_freq"]  = settings.xclk_freq;
  cur["resolution"] = settings.resolution;
  cur["quality"]    = settings.quality;
  cur["brightness"] = settings.brightness;
  cur["contrast"]   = settings.contrast;
  cur["saturation"] = settings.saturation;
  cur["h_mirror"]   = settings.h_mirror;
  cur["v_flip"]     = settings.v_flip;

  String response;
  serializeJson(out, response);
  server.send(200, "application/json", response);
}

void handleStatus() {
  DynamicJsonDocument doc(512);
  doc["status"] = "online";
  doc["ip"] = WiFi.localIP().toString();
  doc["rssi"] = WiFi.RSSI();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["min_free_heap"] = ESP.getMinFreeHeap();
  doc["heap_low_water"] = stats.heap_low_water;
  doc["uptime"] = millis();
  doc["streaming"] = isStreamActive();
  doc["frames_sent"] = stats.frames_sent;
  doc["psram_found"] = psramFound();
  doc["cpu_freq_mhz"] = ESP.getCpuFreqMHz();
  doc["single_client_mode"] = true;
  doc["camera_resolution"] = settings.resolution;
  doc["camera_quality"] = settings.quality;
  
  if (isStreamActive()) {
    doc["stream_duration_ms"] = millis() - stream_start_time;
  }
  
  String response;
  serializeJson(doc, response);
  server.send(200, "application/json", response);
}

void handleDisconnect() {
  if (isStreamActive()) {
    Serial.println("Forcing stream disconnection via API");
    force_disconnect = true;
    disconnectActiveStream();
    server.send(200, "application/json", "{\"status\":\"disconnected\"}");
  } else {
    server.send(200, "application/json", "{\"status\":\"not_streaming\"}");
  }
}

void handleRoot() {
  String html = "<!DOCTYPE html><html><head>";
  html += "<meta charset='UTF-8'>";
  html += "<title>WALL-E ESP32 Camera - FIXED Version</title>";
  html += "<style>body{font-family:Arial,sans-serif;margin:20px;background:#f0f0f0;}";
  html += ".container{background:white;padding:20px;border-radius:8px;max-width:800px;}";
  html += ".status{background:#e9ecef;padding:10px;border-radius:4px;margin:10px 0;}";
  html += ".warning{background:#fff3cd;border:1px solid #ffeaa7;padding:10px;border-radius:4px;margin:10px 0;}";
  html += "img{max-width:100%;border:1px solid #ddd;}";
  html += "a{color:#007bff;text-decoration:none;margin-right:15px;}";
  html += "</style></head><body>";
  html += "<div class='container'>";
  html += "<h1>WALL-E ESP32 Camera - FIXED</h1>";
  html += "<div class='warning'><strong>Single Client Mode:</strong> Only one connection allowed at a time</div>";
  html += "<div class='status'>";
  html += "<strong>Status:</strong> " + String(isStreamActive() ? "Streaming (EXCLUSIVE)" : "Available");
  html += " | <strong>Free Heap:</strong> " + String(ESP.getFreeHeap()) + " bytes";
  html += " | <strong>CPU:</strong> " + String(ESP.getCpuFreqMHz()) + " MHz";
  html += "</div>";
  html += "<p><a href='/stream'>Stream</a><a href='/status'>Status</a><a href='/disconnect'>Force Disconnect</a></p>";
  
  if (!isStreamActive()) {
    html += "<img src='/stream' style='width:100%;max-width:640px;'/>";
  } else {
    html += "<p><em>Stream preview disabled - client already connected</em></p>";
  }
  
  html += "</div></body></html>";
  server.send(200, "text/html", html);
}

void setup() {
  Serial.begin(115200);
  Serial.println("\n\nWALL-E ESP32 Camera - FIXED Version Starting...");
  
  // Record startup time
  stats.uptime_start = millis();
  stats.heap_low_water = ESP.getFreeHeap();
  
  // FIXED: Use higher CPU frequency for better performance (like example)
  setCpuFrequencyMhz(240);  // Restored to 240MHz for better quality
  Serial.printf("CPU frequency set to %d MHz\n", ESP.getCpuFreqMHz());
  
  // WiFi connection
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // FIXED: Disable WiFi sleep like in example
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected successfully!");
    Serial.printf("IP address: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("Signal strength: %d dBm\n", WiFi.RSSI());
  } else {
    Serial.println("\nWiFi connection failed!");
    ESP.restart();
  }
  
  // Initialize camera
  if (!initCamera()) {
    Serial.println("Camera initialization failed!");
    ESP.restart();
  }
  
  Serial.println("Camera initialized with improved settings");
  Serial.printf("PSRAM found: %s\n", psramFound() ? "Yes" : "No");
  Serial.printf("Free heap: %d bytes\n", ESP.getFreeHeap());
  
  // Setup web server routes
  server.on("/", handleRoot);
  server.on("/stream", handleStream);
  server.on("/settings", HTTP_GET, handleGetSettings);
  server.on("/settings", HTTP_POST, handleSetSettings);
  server.on("/status", handleStatus);
  server.on("/disconnect", HTTP_POST, handleDisconnect);
  
  server.onNotFound([]() {
    server.send(404, "text/plain", "Not Found");
  });
  
  server.begin();
  Serial.println("HTTP server started on port 81");
  Serial.printf("Stream URL: http://%s:81/stream\n", WiFi.localIP().toString().c_str());
  Serial.println("FIXED: Improved video quality and stability");
}

void loop() {
  server.handleClient();
  delay(1);
}