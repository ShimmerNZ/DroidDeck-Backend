// ESP32 Camera Streaming Server with Controls
// Simplified version for WALL-E project
// Flash this to your ESP32-CAM module

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>

// WiFi credentials
const char* ssid = "Walle";
const char* password = "EVEROCKS2025";

// Camera model - Change this based on your board
#define CAMERA_MODEL_AI_THINKER // Most common ESP32-CAM board
//#define CAMERA_MODEL_WROVER_KIT
//#define CAMERA_MODEL_M5STACK_PSRAM

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

// Global camera settings
struct CameraSettings {
  int xclk_freq = 10;        // XCLK frequency in MHz (8-20)
  framesize_t resolution = FRAMESIZE_VGA;  // Default VGA
  int quality = 12;          // JPEG quality (4-63, lower is better)
  int brightness = 0;        // (-2 to 2)
  int contrast = 0;          // (-2 to 2)
  int saturation = 0;        // (-2 to 2)
  bool h_mirror = false;     // Horizontal mirror
  bool v_flip = false;       // Vertical flip
} settings;

WebServer server(81);

// Initialize camera with current settings
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
  config.fb_count = 2;  // Double buffer for smoother streaming

  // Initialize camera
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    return false;
  }

  // Apply sensor settings
  applyCameraSettings();
  
  return true;
}

// Apply camera settings to sensor
void applyCameraSettings() {
  sensor_t * s = esp_camera_sensor_get();
  if (s == NULL) {
    Serial.println("Error: Camera sensor not found");
    return;
  }

  // Apply settings
  s->set_framesize(s, settings.resolution);
  s->set_quality(s, settings.quality);
  s->set_brightness(s, settings.brightness);
  s->set_contrast(s, settings.contrast);
  s->set_saturation(s, settings.saturation);
  s->set_hmirror(s, settings.h_mirror ? 1 : 0);
  s->set_vflip(s, settings.v_flip ? 1 : 0);
  
  // Additional optimizations for streaming
  s->set_special_effect(s, 0);  // No special effect
  s->set_whitebal(s, 1);        // Enable white balance
  s->set_awb_gain(s, 1);        // Enable auto white balance gain
  s->set_wb_mode(s, 0);         // Auto white balance mode
  s->set_exposure_ctrl(s, 1);   // Enable exposure control
  s->set_aec2(s, 1);            // Enable AEC DSP
  s->set_gain_ctrl(s, 1);       // Enable gain control
  s->set_agc_gain(s, 0);        // Auto gain
  s->set_bpc(s, 1);             // Enable bad pixel correction
  s->set_wpc(s, 1);             // Enable white pixel correction
  s->set_raw_gma(s, 1);         // Enable gamma correction
  s->set_lenc(s, 1);            // Enable lens correction
  s->set_dcw(s, 1);             // Enable downsize
  
  Serial.println("Camera settings applied");
}

// Stream handler
void handleStream() {
  WiFiClient client = server.client();
  String response = "HTTP/1.1 200 OK\r\n";
  response += "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n";
  server.sendContent(response);

  while (client.connected()) {
    camera_fb_t * fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera capture failed");
      continue;
    }

    client.write("--frame\r\n");
    client.write("Content-Type: image/jpeg\r\n");
    String header = "Content-Length: " + String(fb->len) + "\r\n\r\n";
    client.write(header.c_str());
    client.write(fb->buf, fb->len);
    client.write("\r\n");

    
    esp_camera_fb_return(fb);
    
    // Small delay to control frame rate
    delay(10);
  }
}

// Get current settings as JSON
void handleGetSettings() {
  StaticJsonDocument<512> doc;
  doc["xclk_freq"] = settings.xclk_freq;
  doc["resolution"] = settings.resolution;
  doc["quality"] = settings.quality;
  doc["brightness"] = settings.brightness;
  doc["contrast"] = settings.contrast;
  doc["saturation"] = settings.saturation;
  doc["h_mirror"] = settings.h_mirror;
  doc["v_flip"] = settings.v_flip;
  
  // Add available resolutions
  JsonArray resolutions = doc.createNestedArray("available_resolutions");
  resolutions.add("QQVGA(160x120)");   // 0
  resolutions.add("QCIF(176x144)");    // 1
  resolutions.add("HQVGA(240x176)");   // 2
  resolutions.add("QVGA(320x240)");    // 3
  resolutions.add("CIF(400x296)");     // 4
  resolutions.add("VGA(640x480)");     // 5
  resolutions.add("SVGA(800x600)");    // 6
  resolutions.add("XGA(1024x768)");    // 7
  resolutions.add("SXGA(1280x1024)");  // 8
  resolutions.add("UXGA(1600x1200)");  // 9
  
  String jsonResponse;
  serializeJson(doc, jsonResponse);
  server.send(200, "application/json", jsonResponse);
}

// Update camera settings
void handleSetSettings() {
  bool needsRestart = false;
  bool settingsChanged = false;
  
  // Handle each parameter if present
  if (server.hasArg("xclk_freq")) {
    int newXclk = server.arg("xclk_freq").toInt();
    if (newXclk >= 8 && newXclk <= 20 && newXclk != settings.xclk_freq) {
      settings.xclk_freq = newXclk;
      needsRestart = true; // XCLK change requires camera restart
    }
  }
  
  if (server.hasArg("resolution")) {
    int newRes = server.arg("resolution").toInt();
    if (newRes >= 0 && newRes <= 9 && newRes != settings.resolution) {
      settings.resolution = (framesize_t)newRes;
      settingsChanged = true;
    }
  }
  
  if (server.hasArg("quality")) {
    int newQuality = server.arg("quality").toInt();
    if (newQuality >= 4 && newQuality <= 63 && newQuality != settings.quality) {
      settings.quality = newQuality;
      settingsChanged = true;
    }
  }
  
  if (server.hasArg("brightness")) {
    int newBrightness = server.arg("brightness").toInt();
    if (newBrightness >= -2 && newBrightness <= 2 && newBrightness != settings.brightness) {
      settings.brightness = newBrightness;
      settingsChanged = true;
    }
  }
  
  if (server.hasArg("contrast")) {
    int newContrast = server.arg("contrast").toInt();
    if (newContrast >= -2 && newContrast <= 2 && newContrast != settings.contrast) {
      settings.contrast = newContrast;
      settingsChanged = true;
    }
  }
  
  if (server.hasArg("saturation")) {
    int newSaturation = server.arg("saturation").toInt();
    if (newSaturation >= -2 && newSaturation <= 2 && newSaturation != settings.saturation) {
      settings.saturation = newSaturation;
      settingsChanged = true;
    }
  }
  
  if (server.hasArg("h_mirror")) {
    settings.h_mirror = server.arg("h_mirror") == "true";
    settingsChanged = true;
  }
  
  if (server.hasArg("v_flip")) {
    settings.v_flip = server.arg("v_flip") == "true";
    settingsChanged = true;
  }
  
  // Apply changes
  if (needsRestart) {
    esp_camera_deinit();
    delay(100);
    initCamera();
  } else if (settingsChanged) {
    applyCameraSettings();
  }
  
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

// Status endpoint
void handleStatus() {
  StaticJsonDocument<256> doc;
  doc["status"] = "online";
  doc["ip"] = WiFi.localIP().toString();
  doc["rssi"] = WiFi.RSSI();
  doc["free_heap"] = ESP.getFreeHeap();
  doc["uptime"] = millis();
  
  String jsonResponse;
  serializeJson(doc, jsonResponse);
  server.send(200, "application/json", jsonResponse);
}

// Root endpoint
void handleRoot() {
  String html = "<html><body>";
  html += "<h1>WALL-E ESP32 Camera</h1>";
  html += "<p>Stream URL: <a href='/stream'>/stream</a></p>";
  html += "<p>Settings: <a href='/settings'>/settings</a></p>";
  html += "<p>Status: <a href='/status'>/status</a></p>";
  html += "<img src='/stream' style='width:640px; height:480px;'/>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

void setup() {
  Serial.begin(115200);
  Serial.println("\n\nWALL-E ESP32 Camera Starting...");
  
  // Connect to WiFi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected!");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi connection failed!");
    // Continue anyway - might work in AP mode
  }
  
  // Initialize camera
  if (!initCamera()) {
    Serial.println("Camera initialization failed!");
    ESP.restart();
  }
  
  Serial.println("Camera initialized successfully");
  
  // Setup web server routes
  server.on("/", handleRoot);
  server.on("/stream", handleStream);
  server.on("/settings", HTTP_GET, handleGetSettings);
  server.on("/settings", HTTP_POST, handleSetSettings);
  server.on("/status", handleStatus);
  
  // Start server
  server.begin();
  Serial.println("HTTP server started on port 81");
  Serial.print("Stream URL: http://");
  Serial.print(WiFi.localIP());
  Serial.println(":81/stream");
}

void loop() {
  server.handleClient();
  delay(1);
}