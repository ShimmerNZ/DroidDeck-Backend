// WALL-E XIAO ESP32S3 Sense - MJPEG Camera Stream Server
// Seeed Studio XIAO ESP32S3 Sense + OV3660
//
// IMPORTANT - Arduino IDE settings before flashing:
//   Board      : Seeed Studio XIAO ESP32S3
//   PSRAM      : OPI PSRAM   <-- must be enabled or camera will fail
//   Partition  : any scheme WITH OTA, e.g. "Default with spiffs (3MB APP/1.5MB SPIFFS)"
//                Schemes marked "No OTA" will block wireless updates
//   USB CDC    : Enabled     <-- required for Serial monitor
//
// Wireless updates: after the first USB flash the camera shows up in the
// Arduino IDE under Tools > Port as a network port named "walle-cam".
// Upload to it like a normal port and enter OTA_PASSWORD when asked.
//
// Endpoints (port 81):
//   /stream    MJPEG stream, one client at a time; a new request takes over
//   /settings  GET current settings, POST to change them (applied live)
//   /status    GET health: WiFi, memory, frame rate, uptime
//   /          Simple status page with a preview

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <ArduinoOTA.h>
#include <Preferences.h>

// Works with ArduinoJson 6 and 7
#if ARDUINOJSON_VERSION_MAJOR >= 7
  #define JSON_DOC(name, capacity) JsonDocument name
#else
  #define JSON_DOC(name, capacity) DynamicJsonDocument name(capacity)
#endif

Preferences prefs;

// WiFi credentials
const char* ssid     = "Walle";
const char* password = "EVEROCKS2025";

// Network name, also used for the OTA port in the Arduino IDE
const char* HOSTNAME     = "walle-cam";
const char* OTA_PASSWORD = "droiddeck";

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

// ─── Resolutions ─────────────────────────────────────────────────────────────
// The settings API uses an index into this table rather than the camera
// library's framesize_t values, which have changed between library versions.
// The DroidDeck Camera screen lists the same sizes in the same order. SVGA is
// the largest because the Deck scales frames to 800 px wide for detection.
struct Resolution { framesize_t size; uint16_t width; uint16_t height; const char* name; };
const Resolution RESOLUTIONS[] = {
  { FRAMESIZE_QQVGA,  160,  120, "QQVGA" },
  { FRAMESIZE_QCIF,   176,  144, "QCIF"  },
  { FRAMESIZE_HQVGA,  240,  176, "HQVGA" },
  { FRAMESIZE_QVGA,   320,  240, "QVGA"  },
  { FRAMESIZE_CIF,    400,  296, "CIF"   },
  { FRAMESIZE_HVGA,   480,  320, "HVGA"  },
  { FRAMESIZE_VGA,    640,  480, "VGA"   },
  { FRAMESIZE_SVGA,   800,  600, "SVGA"  },
};
const int RESOLUTION_COUNT   = sizeof(RESOLUTIONS) / sizeof(RESOLUTIONS[0]);
const int DEFAULT_RESOLUTION = 6;  // VGA

// ─── Recovery limits ─────────────────────────────────────────────────────────
const int           MAX_FRAME_FAILURES  = 20;      // ~2 s of failed captures -> reboot
const unsigned long WIFI_LOST_REBOOT_MS = 60000;   // WiFi down this long -> reboot
const unsigned long STREAM_TAKEOVER_MS  = 2000;    // Wait for an old stream to stop

// ─── Runtime camera settings ─────────────────────────────────────────────────
// Defaults are the OV3660 values Espressif recommends for this sensor
struct CameraSettings {
  int         resolution = DEFAULT_RESOLUTION;  // index into RESOLUTIONS
  int         quality    = 10;                  // JPEG quality, 4-25, lower = sharper
  int         brightness = 1;
  int         contrast   = 0;
  int         saturation = -2;
  bool        h_mirror   = false;
  bool        v_flip     = true;
} settings;

WebServer server(81);

// ─── Stream state ─────────────────────────────────────────────────────────────
// The stream runs in its own task so the web server keeps answering
// /settings and /status while frames are being sent
static volatile bool streaming        = false;
static volatile bool stop_requested   = false;
static unsigned long stream_start_time = 0;
static volatile unsigned long frames_sent = 0;
static volatile float current_fps     = 0.0f;
static volatile unsigned long last_frame_bytes = 0;
static unsigned long wifi_lost_since  = 0;
static unsigned long wifi_reconnects  = 0;

// ─── NVS settings persistence ────────────────────────────────────────────────

void saveSettings() {
  prefs.begin("cam", false);
  prefs.putInt("res_index",  settings.resolution);
  prefs.putInt("quality",    settings.quality);
  prefs.putInt("brightness", settings.brightness);
  prefs.putInt("contrast",   settings.contrast);
  prefs.putInt("saturation", settings.saturation);
  prefs.putBool("h_mirror",  settings.h_mirror);
  prefs.putBool("v_flip",    settings.v_flip);
  prefs.end();
  Serial.println("Settings saved to NVS");
}

int clampInt(int value, int lo, int hi) {
  return value < lo ? lo : (value > hi ? hi : value);
}

void loadSettings() {
  prefs.begin("cam", true);
  if (prefs.isKey("quality")) {
    settings.resolution = prefs.getInt("res_index",  settings.resolution);
    if (settings.resolution < 0 || settings.resolution >= RESOLUTION_COUNT) {
      settings.resolution = DEFAULT_RESOLUTION;
    }
    settings.quality    = clampInt(prefs.getInt("quality", settings.quality), 4, 25);
    settings.brightness = clampInt(prefs.getInt("brightness", settings.brightness), -2, 2);
    settings.contrast   = clampInt(prefs.getInt("contrast", settings.contrast), -2, 2);
    settings.saturation = clampInt(prefs.getInt("saturation", settings.saturation), -2, 2);
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
  config.pin_sccb_sda  = SIOD_GPIO_NUM;
  config.pin_sccb_scl  = SIOC_GPIO_NUM;
  config.pin_pwdn      = PWDN_GPIO_NUM;
  config.pin_reset     = RESET_GPIO_NUM;
  config.xclk_freq_hz  = 20000000;
  config.pixel_format  = PIXFORMAT_JPEG;
  // JPEG frame buffers are sized from this frame size. UXGA leaves plenty
  // of room for detailed SVGA frames at the sharpest quality setting, which
  // would otherwise overflow the buffer and be dropped. PSRAM has room.
  config.frame_size    = FRAMESIZE_UXGA;
  config.jpeg_quality  = settings.quality;
  config.fb_count      = 2;
  config.fb_location   = CAMERA_FB_IN_PSRAM;
  config.grab_mode     = CAMERA_GRAB_LATEST;

  if (!psramFound()) {
    Serial.println("ERROR: PSRAM not found - check Tools > PSRAM > OPI PSRAM in Arduino IDE");
    return false;
  }

  Serial.printf("PSRAM size: %u bytes\n", (unsigned)ESP.getPsramSize());

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (!s) {
    Serial.println("Failed to get camera sensor");
    return false;
  }

  Serial.printf("Sensor PID: 0x%x\n", s->id.PID);

  // Saved settings, including resolution, apply from boot
  applySensorSettings(true);

  const Resolution& res = RESOLUTIONS[settings.resolution];
  Serial.printf("Camera ready - sensor: %s, %s %dx%d, quality: %d\n",
    s->id.PID == OV3660_PID ? "OV3660" : "other",
    res.name, res.width, res.height, settings.quality);

  return true;
}

// ─── Apply sensor settings ───────────────────────────────────────────────────

// Changing the frame size reconfigures the sensor and drops a frame or two,
// so it is only sent when the resolution actually changes
void applySensorSettings(bool include_framesize) {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) return;
  if (include_framesize) {
    s->set_framesize(s, RESOLUTIONS[settings.resolution].size);
  }
  s->set_quality(s,    settings.quality);
  s->set_brightness(s, settings.brightness);
  s->set_contrast(s,   settings.contrast);
  s->set_saturation(s, settings.saturation);
  s->set_hmirror(s,    settings.h_mirror ? 1 : 0);
  s->set_vflip(s,      settings.v_flip   ? 1 : 0);
}

// ─── MJPEG stream ─────────────────────────────────────────────────────────────

void streamTask(void* arg) {
  WiFiClient* client = static_cast<WiFiClient*>(arg);

  // Each part header starts with the CRLF that ends the previous frame, so
  // every frame goes out as one header write plus one data write
  static char header_buf[96];
  bool first = true;
  int fails = 0;
  unsigned long window_start  = millis();
  unsigned long window_frames = 0;

  while (!stop_requested && client->connected()) {
    camera_fb_t* fb = esp_camera_fb_get();

    if (!fb) {
      if (++fails >= MAX_FRAME_FAILURES) {
        Serial.println("Camera stopped delivering frames - rebooting");
        delay(100);
        ESP.restart();
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    fails = 0;

    if (fb->format != PIXFORMAT_JPEG) {
      esp_camera_fb_return(fb);
      continue;
    }

    int hlen = snprintf(header_buf, sizeof(header_buf),
      "%s--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
      first ? "" : "\r\n", (unsigned)fb->len);
    first = false;

    size_t frame_len = fb->len;
    bool ok = client->write((const uint8_t*)header_buf, hlen) == (size_t)hlen
           && client->write(fb->buf, frame_len) == frame_len;

    esp_camera_fb_return(fb);

    if (!ok) break;

    frames_sent++;
    window_frames++;
    last_frame_bytes = frame_len;

    unsigned long now = millis();
    if (now - window_start >= 2000) {
      current_fps   = window_frames * 1000.0f / (now - window_start);
      window_start  = now;
      window_frames = 0;
    }

    if (frames_sent % 300 == 0) {
      Serial.printf("Stream: %lu frames, %.1f fps, last frame %lu bytes, heap: %u\n",
        (unsigned long)frames_sent, (float)current_fps, (unsigned long)frame_len, (unsigned)ESP.getFreeHeap());
    }
  }

  client->stop();
  delete client;

  Serial.printf("Stream ended - %lu frames in %lus\n",
    (unsigned long)frames_sent, (millis() - stream_start_time) / 1000);

  current_fps = 0.0f;
  streaming   = false;
  vTaskDelete(NULL);
}

// Stop the running stream, if any, and wait for its task to finish
bool stopStream() {
  if (!streaming) return true;
  stop_requested = true;
  unsigned long start = millis();
  while (streaming && millis() - start < STREAM_TAKEOVER_MS) {
    delay(10);
  }
  return !streaming;
}

void handleStream() {
  // A new request replaces the old stream, so a proxy that restarted or
  // lost its connection can always get the camera back
  if (streaming) {
    Serial.println("New stream request - stopping the current stream");
    if (!stopStream()) {
      server.send(503, "text/plain", "Previous stream did not stop");
      return;
    }
  }

  WiFiClient* client = new WiFiClient(server.client());
  if (!client->connected()) {
    delete client;
    return;
  }

  // Send each write straight away instead of waiting to batch small packets,
  // which otherwise holds every frame header back for an ACK
  client->setNoDelay(true);

  client->print("HTTP/1.1 200 OK\r\n"
                "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                "Cache-Control: no-cache\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "\r\n");

  Serial.printf("Stream started for %s\n", client->remoteIP().toString().c_str());

  stop_requested    = false;
  streaming         = true;
  stream_start_time = millis();
  frames_sent       = 0;

  if (xTaskCreatePinnedToCore(streamTask, "mjpeg", 8192, client, 2, NULL, 1) != pdPASS) {
    Serial.println("Could not start stream task");
    streaming = false;
    client->stop();
    delete client;
  }
}

// ─── Settings endpoints ───────────────────────────────────────────────────────

void addSettings(JsonDocument& doc) {
  const Resolution& res = RESOLUTIONS[settings.resolution];
  doc["resolution"] = settings.resolution;
  doc["width"]      = res.width;
  doc["height"]     = res.height;
  doc["quality"]    = settings.quality;
  doc["brightness"] = settings.brightness;
  doc["contrast"]   = settings.contrast;
  doc["saturation"] = settings.saturation;
  doc["h_mirror"]   = settings.h_mirror;
  doc["v_flip"]     = settings.v_flip;
}

void sendJson(JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

void handleGetSettings() {
  JSON_DOC(doc, 512);
  addSettings(doc);
  doc["streaming"]  = (bool)streaming;
  doc["free_heap"]  = ESP.getFreeHeap();
  doc["psram_size"] = ESP.getPsramSize();
  doc["free_psram"] = ESP.getFreePsram();
  sendJson(doc);
}

void handlePostSettings() {
  bool changed = false;
  bool resolution_changed = false;

  auto getInt = [&](const char* key, int lo, int hi, int& target) -> bool {
    if (server.hasArg(key)) {
      int v = server.arg(key).toInt();
      if (v >= lo && v <= hi && v != target) { target = v; changed = true; return true; }
    }
    return false;
  };

  auto getBool = [&](const char* key, bool& target) {
    if (server.hasArg(key)) {
      String v = server.arg(key); v.toLowerCase();
      bool b = (v == "1" || v == "true");
      if (b != target) { target = b; changed = true; }
    }
  };

  resolution_changed = getInt("resolution", 0, RESOLUTION_COUNT - 1, settings.resolution);
  getInt("quality",    4,  25, settings.quality);
  getInt("brightness", -2,  2, settings.brightness);
  getInt("contrast",   -2,  2, settings.contrast);
  getInt("saturation", -2,  2, settings.saturation);
  getBool("h_mirror", settings.h_mirror);
  getBool("v_flip",   settings.v_flip);

  if (changed) {
    applySensorSettings(resolution_changed);
    saveSettings();
  }

  JSON_DOC(doc, 512);
  doc["status"]  = "ok";
  doc["changed"] = changed;
  addSettings(doc);
  sendJson(doc);
}

// ─── Status endpoint ──────────────────────────────────────────────────────────

void handleStatus() {
  sensor_t* s = esp_camera_sensor_get();

  JSON_DOC(doc, 768);
  doc["status"]          = "online";
  doc["hostname"]        = HOSTNAME;
  doc["ip"]              = WiFi.localIP().toString();
  doc["rssi"]            = WiFi.RSSI();
  doc["wifi_reconnects"] = wifi_reconnects;
  doc["streaming"]       = (bool)streaming;
  doc["fps"]             = roundf((float)current_fps * 10.0f) / 10.0f;
  doc["frames_sent"]     = (unsigned long)frames_sent;
  doc["last_frame_bytes"] = (unsigned long)last_frame_bytes;
  doc["free_heap"]       = ESP.getFreeHeap();
  doc["psram_size"]      = ESP.getPsramSize();
  doc["free_psram"]      = ESP.getFreePsram();
  doc["cpu_mhz"]         = ESP.getCpuFreqMHz();
  doc["uptime_ms"]       = millis();
  doc["sensor_pid"]      = s ? s->id.PID : 0;
  addSettings(doc);
  sendJson(doc);
}

// ─── Root page ────────────────────────────────────────────────────────────────

void handleRoot() {
  String html = F("<!DOCTYPE html><html><head><meta charset='UTF-8'>"
    "<title>WALL-E Camera</title>"
    "<style>body{font-family:sans-serif;margin:20px;background:#111;color:#eee;}"
    "a{color:#4af;}img{max-width:100%;border:1px solid #444;display:block;margin:10px 0;}"
    "p{margin:6px 0;}</style></head><body>"
    "<h2>WALL-E XIAO ESP32S3 Camera</h2>");
  const Resolution& res = RESOLUTIONS[settings.resolution];
  html += "<p>Status: " + String(streaming ? "Streaming" : "Available");
  if (streaming) html += " at " + String((float)current_fps, 1) + " fps";
  html += "</p>";
  html += "<p>Resolution: " + String(res.name) + " " + String(res.width) + "x" + String(res.height)
       +  " | Quality: " + String(settings.quality) + "</p>";
  html += "<p>WiFi: " + String(WiFi.RSSI()) + " dBm | Uptime: " + String(millis() / 60000) + " min</p>";
  html += "<p>Heap: " + String(ESP.getFreeHeap()) + " | PSRAM free: " + String(ESP.getFreePsram()) + "</p>";
  html += "<p><a href='/stream'>Stream</a> | <a href='/status'>Status JSON</a> | <a href='/settings'>Settings JSON</a></p>";
  if (streaming) {
    html += "<p>The preview is hidden while another client is streaming; opening the stream link will take it over.</p>";
  } else {
    html += "<img src='/stream'>";
  }
  html += "</body></html>";
  server.send(200, "text/html", html);
}

// ─── OTA ──────────────────────────────────────────────────────────────────────

void setupOta() {
  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);

  ArduinoOTA.onStart([]() {
    Serial.println("OTA update starting - stopping stream");
    stopStream();
  });
  ArduinoOTA.onEnd([]() {
    Serial.println("OTA update complete - rebooting");
  });
  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA error %u\n", error);
  });

  ArduinoOTA.begin();
}

// ─── WiFi watchdog ───────────────────────────────────────────────────────────

void checkWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    if (wifi_lost_since != 0) {
      wifi_reconnects++;
      Serial.printf("WiFi reconnected after %lus - IP: %s\n",
        (millis() - wifi_lost_since) / 1000, WiFi.localIP().toString().c_str());
      wifi_lost_since = 0;
    }
    return;
  }

  if (wifi_lost_since == 0) {
    wifi_lost_since = millis();
    Serial.println("WiFi lost - waiting for reconnect");
  } else if (millis() - wifi_lost_since > WIFI_LOST_REBOOT_MS) {
    Serial.println("WiFi still down - rebooting");
    delay(100);
    ESP.restart();
  }
}

// ─── Setup / loop ─────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\nWALL-E XIAO ESP32S3 Camera");

  setCpuFrequencyMhz(240);

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
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

  // OTA starts before the camera so a sketch with a camera problem can
  // still be replaced wirelessly
  setupOta();

  if (!initCamera()) {
    Serial.println("Camera failed - waiting 30s for an OTA update, then restarting");
    unsigned long start = millis();
    while (millis() - start < 30000) {
      ArduinoOTA.handle();
      delay(10);
    }
    ESP.restart();
  }

  server.on("/",         HTTP_GET,  handleRoot);
  server.on("/stream",   HTTP_GET,  handleStream);
  server.on("/status",   HTTP_GET,  handleStatus);
  server.on("/settings", HTTP_GET,  handleGetSettings);
  server.on("/settings", HTTP_POST, handlePostSettings);
  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });

  server.begin();
  Serial.printf("Ready - http://%s:81/stream (OTA host: %s)\n",
    WiFi.localIP().toString().c_str(), HOSTNAME);
}

void loop() {
  server.handleClient();
  ArduinoOTA.handle();
  checkWifi();
  delay(1);
}
