// WALL-E XIAO ESP32S3 Sense - MJPEG Camera Stream Server
// Seeed Studio XIAO ESP32S3 Sense + OV3660
//
// IMPORTANT - Arduino IDE settings before flashing:
//   Board      : Seeed Studio XIAO ESP32S3
//   PSRAM      : OPI PSRAM   <-- must be enabled or camera will fail
//   Partition  : Huge APP (3MB No OTA/1MB SPIFFS)
//   USB CDC    : Enabled     <-- required for Serial monitor

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <Preferences.h>

Preferences prefs;

// WiFi credentials
const char* ssid     = "Walle";
const char* password = "EVEROCKS2025";

// ─── Pin definitions for Seeed XIAO ESP32S3 Sense ────────────────────────────
#define PWDN_GPIO_NUM    -1
#define RESET_GPIO_NUM   -1
#define XCLK_GPIO_NUM    10
#define SIOD_GPIO_NUM    40
#define SIOC_GPIO_NUM    39
#define Y9_GPIO_NUM      48
#define Y8_GPIO_NUM      11
#define Y7_GPIO_NUM      12
#define Y6_GPIO_NUM      14
#define Y5_GPIO_NUM      16
#define Y4_GPIO_NUM      18
#define Y3_GPIO_NUM      17
#define Y2_GPIO_NUM      15
#define VSYNC_GPIO_NUM   38
#define HREF_GPIO_NUM    47
#define PCLK_GPIO_NUM    13

// ─── Runtime camera settings ─────────────────────────────────────────────────
struct CameraSettings {
  framesize_t resolution = FRAMESIZE_VGA;
  int         quality    = 10;
  int         brightness = 1;   // OV3660 default
  int         contrast   = 0;
  int         saturation = -2;  // OV3660 default
  bool        h_mirror   = false;
  bool        v_flip     = true; // OV3660 default
} settings;

WebServer server(81);

// ─── Stream state ─────────────────────────────────────────────────────────────
static bool          streaming         = false;
static unsigned long stream_start_time = 0;
static unsigned long frames_sent       = 0;

// ─── NVS settings persistence ────────────────────────────────────────────────

void saveSettings() {
  prefs.begin("cam", false);
  prefs.putInt("resolution", settings.resolution);
  prefs.putInt("quality",    settings.quality);
  prefs.putInt("brightness", settings.brightness);
  prefs.putInt("contrast",   settings.contrast);
  prefs.putInt("saturation", settings.saturation);
  prefs.putBool("h_mirror",  settings.h_mirror);
  prefs.putBool("v_flip",    settings.v_flip);
  prefs.end();
  Serial.println("Settings saved to NVS");
}

void loadSettings() {
  prefs.begin("cam", true);
  if (prefs.isKey("quality")) {
    settings.resolution = (framesize_t)prefs.getInt("resolution", settings.resolution);
    settings.quality    = prefs.getInt("quality",    settings.quality);
    settings.brightness = prefs.getInt("brightness", settings.brightness);
    settings.contrast   = prefs.getInt("contrast",   settings.contrast);
    settings.saturation = prefs.getInt("saturation", settings.saturation);
    settings.h_mirror   = prefs.getBool("h_mirror",  settings.h_mirror);
    settings.v_flip     = prefs.getBool("v_flip",    settings.v_flip);
    Serial.println("Settings loaded from NVS");
  } else {
    Serial.println("No saved settings found, using defaults");
  }
  prefs.end();
}

// ─── Camera init ──────────────────────────────────────────────────────────────

bool initCamera() {
  loadSettings();

  camera_config_t config;
  config.ledc_channel  = LEDC_CHANNEL_0;
  config.ledc_timer    = LEDC_TIMER_0;
  config.pin_d0        = Y2_GPIO_NUM;
  config.pin_d1        = Y3_GPIO_NUM;
  config.pin_d2        = Y4_GPIO_NUM;
  config.pin_d3        = Y5_GPIO_NUM;
  config.pin_d4        = Y6_GPIO_NUM;
  config.pin_d5        = Y7_GPIO_NUM;
  config.pin_d6        = Y8_GPIO_NUM;
  config.pin_d7        = Y9_GPIO_NUM;
  config.pin_xclk      = XCLK_GPIO_NUM;
  config.pin_pclk      = PCLK_GPIO_NUM;
  config.pin_vsync     = VSYNC_GPIO_NUM;
  config.pin_href      = HREF_GPIO_NUM;
  config.pin_sscb_sda  = SIOD_GPIO_NUM;
  config.pin_sscb_scl  = SIOC_GPIO_NUM;
  config.pin_pwdn      = PWDN_GPIO_NUM;
  config.pin_reset     = RESET_GPIO_NUM;
  config.xclk_freq_hz  = 20000000;
  config.pixel_format  = PIXFORMAT_JPEG;
  config.frame_size    = FRAMESIZE_UXGA;  // Init at max so buffers are large enough
  config.jpeg_quality  = 10;
  config.fb_count      = 2;
  config.fb_location   = CAMERA_FB_IN_PSRAM;
  config.grab_mode     = CAMERA_GRAB_LATEST;

  if (!psramFound()) {
    Serial.println("ERROR: PSRAM not found - check Tools > PSRAM > OPI PSRAM in Arduino IDE");
    return false;
  }

  Serial.printf("PSRAM size: %d bytes\n", ESP.getPsramSize());

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  // Apply sensor settings
  sensor_t* s = esp_camera_sensor_get();
  if (!s) {
    Serial.println("Failed to get camera sensor");
    return false;
  }

  Serial.printf("Sensor PID: 0x%x\n", s->id.PID);

  // OV3660 baseline — these are Espressif's recommended defaults for this sensor
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s,      1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }

  // Drop to QVGA initially for fastest first-frame delivery.
  // Settings API can set a higher resolution once streaming starts.
  s->set_framesize(s, FRAMESIZE_VGA);
  s->set_quality(s,    settings.quality);
  s->set_brightness(s, settings.brightness);
  s->set_saturation(s, settings.saturation);
  s->set_hmirror(s,    settings.h_mirror ? 1 : 0);
  s->set_vflip(s,      settings.v_flip   ? 1 : 0);

  Serial.printf("Camera ready - sensor: %s, quality: %d\n",
    s->id.PID == OV3660_PID ? "OV3660" : "other", settings.quality);

  return true;
}

// ─── Apply sensor settings at runtime ────────────────────────────────────────

void applySensorSettings() {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) return;
  s->set_framesize(s,  settings.resolution);
  s->set_quality(s,    settings.quality);
  s->set_brightness(s, settings.brightness);
  s->set_contrast(s,   settings.contrast);
  s->set_saturation(s, settings.saturation);
  s->set_hmirror(s,    settings.h_mirror ? 1 : 0);
  s->set_vflip(s,      settings.v_flip   ? 1 : 0);
}

// ─── MJPEG stream ─────────────────────────────────────────────────────────────

void handleStream() {
  if (streaming) {
    server.send(429, "text/plain", "Stream already active");
    return;
  }

  WiFiClient client = server.client();
  if (!client) return;

  streaming         = true;
  stream_start_time = millis();
  frames_sent       = 0;

  Serial.println("Stream started");

  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
  client.println("Cache-Control: no-cache");
  client.println("Access-Control-Allow-Origin: *");
  client.println();

  static uint8_t header_buf[64];
  int fails = 0;

  while (client.connected()) {
    camera_fb_t* fb = esp_camera_fb_get();

    if (!fb) {
      if (++fails > 5) {
        Serial.println("Too many frame failures, ending stream");
        break;
      }
      delay(100);
      continue;
    }

    fails = 0;

    if (fb->format != PIXFORMAT_JPEG) {
      Serial.println("Non-JPEG frame, skipping");
      esp_camera_fb_return(fb);
      continue;
    }

    int hlen = snprintf((char*)header_buf, sizeof(header_buf),
      "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);

    bool ok = (client.write(header_buf, hlen) == (size_t)hlen);

    if (ok) {
      size_t sent = 0;
      while (sent < fb->len && client.connected()) {
        size_t chunk = min((size_t)8192, fb->len - sent);
        size_t wrote = client.write(fb->buf + sent, chunk);
        if (wrote == 0) { delay(1); continue; }
        sent += wrote;
      }
      if (client.connected()) client.write("\r\n", 2);
    }

    esp_camera_fb_return(fb);

    if (!ok || !client.connected()) break;

    frames_sent++;

    if (frames_sent % 100 == 0) {
      unsigned long secs = (millis() - stream_start_time) / 1000;
      float fps = secs > 0 ? (float)frames_sent / secs : 0;
      Serial.printf("Stream: %lu frames, %.1f fps, heap: %d\n",
        frames_sent, fps, ESP.getFreeHeap());
    }

    yield();
  }

  streaming = false;
  Serial.printf("Stream ended - %lu frames\n", frames_sent);
}

// ─── Settings endpoints ───────────────────────────────────────────────────────

void handleGetSettings() {
  DynamicJsonDocument doc(512);
  doc["resolution"] = settings.resolution;
  doc["quality"]    = settings.quality;
  doc["brightness"] = settings.brightness;
  doc["contrast"]   = settings.contrast;
  doc["saturation"] = settings.saturation;
  doc["h_mirror"]   = settings.h_mirror;
  doc["v_flip"]     = settings.v_flip;
  doc["streaming"]  = streaming;
  doc["free_heap"]  = ESP.getFreeHeap();
  doc["psram_size"] = ESP.getPsramSize();
  doc["free_psram"] = ESP.getFreePsram();

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

void handlePostSettings() {
  if (streaming) {
    server.send(423, "application/json", "{\"error\":\"Cannot change settings while streaming\"}");
    return;
  }

  bool changed = false;

  auto getInt = [&](const char* key, int lo, int hi, int& target) {
    if (server.hasArg(key)) {
      int v = server.arg(key).toInt();
      if (v >= lo && v <= hi && v != target) { target = v; changed = true; }
    }
  };

  if (server.hasArg("resolution")) {
    int r = server.arg("resolution").toInt();
    if (r >= FRAMESIZE_QQVGA && r <= FRAMESIZE_UXGA) {
      settings.resolution = static_cast<framesize_t>(r);
      changed = true;
    }
  }

  getInt("quality",    4,  25, settings.quality);
  getInt("brightness", -2,  2, settings.brightness);
  getInt("contrast",   -2,  2, settings.contrast);
  getInt("saturation", -2,  2, settings.saturation);

  if (server.hasArg("h_mirror")) {
    String v = server.arg("h_mirror"); v.toLowerCase();
    bool b = (v == "1" || v == "true");
    if (b != settings.h_mirror) { settings.h_mirror = b; changed = true; }
  }
  if (server.hasArg("v_flip")) {
    String v = server.arg("v_flip"); v.toLowerCase();
    bool b = (v == "1" || v == "true");
    if (b != settings.v_flip) { settings.v_flip = b; changed = true; }
  }

  if (changed) {
    applySensorSettings();
    saveSettings();
  }

  DynamicJsonDocument doc(512);
  doc["status"]     = "ok";
  doc["changed"]    = changed;
  doc["resolution"] = settings.resolution;
  doc["quality"]    = settings.quality;
  doc["brightness"] = settings.brightness;
  doc["contrast"]   = settings.contrast;
  doc["saturation"] = settings.saturation;
  doc["h_mirror"]   = settings.h_mirror;
  doc["v_flip"]     = settings.v_flip;

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

// ─── Status endpoint ──────────────────────────────────────────────────────────

void handleStatus() {
  sensor_t* s = esp_camera_sensor_get();

  DynamicJsonDocument doc(512);
  doc["status"]       = "online";
  doc["ip"]           = WiFi.localIP().toString();
  doc["rssi"]         = WiFi.RSSI();
  doc["streaming"]    = streaming;
  doc["frames_sent"]  = frames_sent;
  doc["free_heap"]    = ESP.getFreeHeap();
  doc["psram_size"]   = ESP.getPsramSize();
  doc["free_psram"]   = ESP.getFreePsram();
  doc["cpu_mhz"]      = ESP.getCpuFreqMHz();
  doc["uptime_ms"]    = millis();
  doc["resolution"]   = settings.resolution;
  doc["quality"]      = settings.quality;
  doc["sensor_pid"]   = s ? s->id.PID : 0;

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

// ─── Root page ────────────────────────────────────────────────────────────────

void handleRoot() {
  String html = F("<!DOCTYPE html><html><head><meta charset='UTF-8'>"
    "<title>WALL-E Camera</title>"
    "<style>body{font-family:sans-serif;margin:20px;background:#111;color:#eee;}"
    "a{color:#4af;}img{max-width:100%;border:1px solid #444;display:block;margin:10px 0;}"
    "p{margin:6px 0;}</style></head><body>"
    "<h2>WALL-E XIAO ESP32S3 Camera</h2>");
  html += "<p>Status: "  + String(streaming ? "Streaming" : "Available") + "</p>";
  html += "<p>Resolution: " + String(settings.resolution) + " | Quality: " + String(settings.quality) + "</p>";
  html += "<p>Heap: " + String(ESP.getFreeHeap()) + " | PSRAM free: " + String(ESP.getFreePsram()) + "</p>";
  html += "<p><a href='/stream'>Stream</a> | <a href='/status'>Status JSON</a> | <a href='/settings'>Settings JSON</a></p>";
  if (!streaming) html += "<img src='/stream'>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

// ─── Setup / loop ─────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\nWALL-E XIAO ESP32S3 Camera");

  setCpuFrequencyMhz(240);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(ssid, password);
  Serial.print("WiFi connecting");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts++ < 40) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nWiFi failed - restarting");
    ESP.restart();
  }
  Serial.printf("\nConnected - IP: %s  RSSI: %d dBm\n",
    WiFi.localIP().toString().c_str(), WiFi.RSSI());

  if (!initCamera()) {
    Serial.println("Camera failed - restarting in 3s");
    delay(3000);
    ESP.restart();
  }

  server.on("/",        HTTP_GET,  handleRoot);
  server.on("/stream",  HTTP_GET,  handleStream);
  server.on("/status",  HTTP_GET,  handleStatus);
  server.on("/settings", HTTP_GET,  handleGetSettings);
  server.on("/settings", HTTP_POST, handlePostSettings);
  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });

  server.begin();
  Serial.printf("Ready - http://%s:81/stream\n", WiFi.localIP().toString().c_str());
}

void loop() {
  server.handleClient();
  delay(1);
}
