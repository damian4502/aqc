/*
    ESP-NOW LED matrix receiver
    Based on the ESP-NOW Broadcast Slave example (Lucas Saavedra Vaz, 2024).

    Receives broadcast payloads and shows them on a 4-module FC-16 MAX7219
    matrix (32 x 8). JSON objects/arrays are flattened into readable
    "key: value" lines and shown one field at a time at a comfortable reading
    pace. Non-JSON payloads are shown as-is. Long lines scroll; short lines
    are centred and held.

    Libraries: ESP32 Arduino core 3.x (ESP32_NOW), MD_MAX72XX.
    No extra JSON library is required.
*/

#include <Arduino.h>
#include "ESP32_NOW.h"
#include "WiFi.h"
#include <MD_MAX72xx.h>
#include <esp_mac.h>
#include <vector>
#include <ctype.h>
#include <string.h>
#include <stdlib.h>

/* Hardware */

#define HARDWARE_TYPE MD_MAX72XX::FC16_HW
#define MAX_DEVICES 4
#define CLK_PIN 18
#define DATA_PIN 23
#define CS_PIN 5
#define CHAR_SPACING 1

MD_MAX72XX mx = MD_MAX72XX(HARDWARE_TYPE, CS_PIN, MAX_DEVICES);

#define ESPNOW_WIFI_CHANNEL 3
#define DISPLAY_COLS (MAX_DEVICES * COL_SIZE)

/* Timing — tuned so a field stays on screen long enough to read */

#define ITEM_HOLD_MS 2400
#define SCROLL_MS_PER_COL 50
#define ITEM_GAP_MS 400
#define SCROLL_LEAD_COLS 8
#define SCROLL_TRAIL_COLS 8

/* Buffers */

#define MSG_MAX_LEN 512
#define ITEM_MAX_LEN 80
#define MAX_ITEMS 24
#define KEY_MAX_LEN 40
#define JSON_MAX_DEPTH 8

/* Incoming message (filled from the ESP-NOW callback, consumed in loop) */

static portMUX_TYPE msgMux = portMUX_INITIALIZER_UNLOCKED;
static volatile bool hasNewMessage = false;
static char pendingMsg[MSG_MAX_LEN];

/* Parsed display queue */

static char displayItems[MAX_ITEMS][ITEM_MAX_LEN];
static uint8_t itemCount = 0;
static uint8_t itemIndex = 0;
static bool itemsAreJson = false;

enum DisplayPhase {
  PHASE_IDLE,
  PHASE_HOLD,
  PHASE_SCROLL,
  PHASE_GAP
};

static DisplayPhase phase = PHASE_IDLE;
static unsigned long phaseStarted = 0;

struct ScrollState {
  const char *msg;
  uint8_t cBuf[8];
  uint8_t charWidth;
  uint8_t colInChar;
  int16_t padLeft;
  int16_t padRight;
  bool inChar;
  bool done;
};

static ScrollState scroll;

/* JSON parser cursor */

static const char *jp = nullptr;

/* ---------- UTF-8 folding (LED font is 7-bit ASCII) ---------- */

static char mapCodepoint(unsigned int cp) {
  if (cp < 0x80) return (char)cp;
  switch (cp) {
    case 0x00B0: return ' '; /* degree */
    case 0x00B2: return '2';
    case 0x00B3: return '3';
    case 0x00B5: case 0x03BC: return 'u';
    case 0x00E0: case 0x00E1: case 0x00E2: case 0x00E3: case 0x00E4: case 0x00E5:
      return 'a';
    case 0x00C0: case 0x00C1: case 0x00C2: case 0x00C3: case 0x00C4: case 0x00C5:
      return 'A';
    case 0x00E8: case 0x00E9: case 0x00EA: case 0x00EB: return 'e';
    case 0x00C8: case 0x00C9: case 0x00CA: case 0x00CB: return 'E';
    case 0x00EC: case 0x00ED: case 0x00EE: case 0x00EF: return 'i';
    case 0x00CC: case 0x00CD: case 0x00CE: case 0x00CF: return 'I';
    case 0x00F2: case 0x00F3: case 0x00F4: case 0x00F5: case 0x00F6: return 'o';
    case 0x00D2: case 0x00D3: case 0x00D4: case 0x00D5: case 0x00D6: return 'O';
    case 0x00F9: case 0x00FA: case 0x00FB: case 0x00FC: return 'u';
    case 0x00D9: case 0x00DA: case 0x00DB: case 0x00DC: return 'U';
    case 0x00F1: return 'n';
    case 0x00D1: return 'N';
    case 0x00DF: return 's';
    case 0x010D: case 0x0107: return 'c'; /* č ć */
    case 0x010C: case 0x0106: return 'C';
    case 0x0161: return 's'; /* š */
    case 0x0160: return 'S';
    case 0x017E: return 'z'; /* ž */
    case 0x017D: return 'Z';
    case 0x0111: return 'd'; /* đ */
    case 0x0110: return 'D';
    default: return '?';
  }
}

static void foldUtf8(const char *in, char *out, size_t cap) {
  if (!out || cap == 0) return;
  size_t o = 0;
  const unsigned char *p = (const unsigned char *)in;
  while (p && *p && o + 1 < cap) {
    unsigned char c = *p;
    unsigned int cp = 0;
    int adv = 1;
    if (c < 0x80) {
      cp = c;
    } else if ((c & 0xE0) == 0xC0 && p[1]) {
      cp = ((c & 0x1F) << 6) | (p[1] & 0x3F);
      adv = 2;
    } else if ((c & 0xF0) == 0xE0 && p[1] && p[2]) {
      cp = ((c & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F);
      adv = 3;
    } else if ((c & 0xF8) == 0xF0 && p[1] && p[2] && p[3]) {
      cp = ((c & 0x07) << 18) | ((p[1] & 0x3F) << 12) | ((p[2] & 0x3F) << 6) | (p[3] & 0x3F);
      adv = 4;
    } else {
      p++;
      continue;
    }
    p += adv;
    char ch = mapCodepoint(cp);
    if (ch == ' ' && o > 0 && out[o - 1] == ' ') continue;
    out[o++] = ch;
  }
  out[o] = '\0';
}

static void humanizeKey(const char *in, char *out, size_t cap) {
  size_t o = 0;
  for (const char *p = in; p && *p && o + 1 < cap; p++) {
    char c = *p;
    if (c == '_' || c == '-' || c == '/') c = ' ';
    if (c == ' ' && o > 0 && out[o - 1] == ' ') continue;
    out[o++] = c;
  }
  out[o] = '\0';
}

/* ---------- Display queue ---------- */

static void clearItems() {
  itemCount = 0;
  itemIndex = 0;
}

static void addItem(const char *text) {
  if (itemCount >= MAX_ITEMS || !text || !text[0]) return;
  strncpy(displayItems[itemCount], text, ITEM_MAX_LEN - 1);
  displayItems[itemCount][ITEM_MAX_LEN - 1] = '\0';
  itemCount++;
}

static void emitPair(const char *key, const char *value) {
  char foldedVal[ITEM_MAX_LEN];
  foldUtf8(value ? value : "", foldedVal, sizeof(foldedVal));
  if (!foldedVal[0] && !(value && value[0] == '0')) {
    /* keep explicit empty / zero values */
  }

  char line[ITEM_MAX_LEN];
  if (key && key[0]) {
    char pretty[KEY_MAX_LEN];
    char foldedKey[KEY_MAX_LEN];
    foldUtf8(key, foldedKey, sizeof(foldedKey));
    humanizeKey(foldedKey, pretty, sizeof(pretty));
    snprintf(line, sizeof(line), "%s: %s", pretty, foldedVal);
  } else {
    snprintf(line, sizeof(line), "%s", foldedVal);
  }
  addItem(line);
}

/* ---------- Minimal JSON walker ---------- */

static void skipWs() {
  while (jp && *jp && isspace((unsigned char)*jp)) jp++;
}

static bool parseValue(const char *key, int depth);
static bool parseObject(int depth);
static bool parseArray(const char *parentKey, int depth);

static int hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

static bool parseStringInto(char *out, size_t cap) {
  skipWs();
  if (*jp != '"') return false;
  jp++;
  size_t o = 0;
  while (*jp && *jp != '"') {
    unsigned char c = (unsigned char)*jp;
    if (c == '\\') {
      jp++;
      if (!*jp) return false;
      char esc = *jp++;
      char mapped = 0;
      switch (esc) {
        case '"': case '\\': case '/': mapped = esc; break;
        case 'b': case 'f': case 'n': case 'r': case 't': mapped = ' '; break;
        case 'u': {
          unsigned int cp = 0;
          for (int i = 0; i < 4; i++) {
            int h = hexVal(*jp);
            if (h < 0) return false;
            cp = (cp << 4) | (unsigned int)h;
            jp++;
          }
          mapped = mapCodepoint(cp);
          break;
        }
        default: mapped = esc; break;
      }
      if (o + 1 < cap) out[o++] = mapped;
    } else {
      jp++;
      if (c < 0x80) {
        if (o + 1 < cap) out[o++] = (char)c;
      } else {
        /* consume the rest of the UTF-8 sequence and fold */
        unsigned int cp = 0;
        if ((c & 0xE0) == 0xC0 && (*jp)) {
          cp = ((c & 0x1F) << 6) | ((unsigned char)*jp & 0x3F);
          jp++;
        } else if ((c & 0xF0) == 0xE0 && jp[0] && jp[1]) {
          cp = ((c & 0x0F) << 12) | ((jp[0] & 0x3F) << 6) | (jp[1] & 0x3F);
          jp += 2;
        } else if ((c & 0xF8) == 0xF0 && jp[0] && jp[1] && jp[2]) {
          jp += 3;
          cp = 0x3F; /* skip 4-byte; map to '?' */
        }
        if (o + 1 < cap) out[o++] = mapCodepoint(cp);
      }
    }
  }
  if (*jp != '"') return false;
  jp++;
  if (cap) out[o < cap ? o : cap - 1] = '\0';
  return true;
}

static bool parseObject(int depth) {
  if (depth > JSON_MAX_DEPTH) return false;
  skipWs();
  if (*jp != '{') return false;
  jp++;
  skipWs();
  if (*jp == '}') {
    jp++;
    return true;
  }
  while (*jp) {
    char key[KEY_MAX_LEN];
    if (!parseStringInto(key, sizeof(key))) return false;
    skipWs();
    if (*jp != ':') return false;
    jp++;
    skipWs();
    if (!parseValue(key, depth + 1)) return false;
    skipWs();
    if (*jp == ',') {
      jp++;
      continue;
    }
    if (*jp == '}') {
      jp++;
      return true;
    }
    return false;
  }
  return false;
}

static bool parseArray(const char *parentKey, int depth) {
  if (depth > JSON_MAX_DEPTH) return false;
  skipWs();
  if (*jp != '[') return false;
  jp++;
  skipWs();
  if (*jp == ']') {
    jp++;
    return true;
  }
  while (*jp) {
    skipWs();
    if (!parseValue(parentKey, depth + 1)) return false;
    skipWs();
    if (*jp == ',') {
      jp++;
      continue;
    }
    if (*jp == ']') {
      jp++;
      return true;
    }
    return false;
  }
  return false;
}

static bool parseNumber(char *out, size_t cap) {
  const char *start = jp;
  if (*jp == '-') jp++;
  if (!isdigit((unsigned char)*jp)) return false;
  while (isdigit((unsigned char)*jp)) jp++;
  if (*jp == '.') {
    jp++;
    if (!isdigit((unsigned char)*jp)) return false;
    while (isdigit((unsigned char)*jp)) jp++;
  }
  if (*jp == 'e' || *jp == 'E') {
    jp++;
    if (*jp == '+' || *jp == '-') jp++;
    if (!isdigit((unsigned char)*jp)) return false;
    while (isdigit((unsigned char)*jp)) jp++;
  }
  size_t n = (size_t)(jp - start);
  if (n >= cap) n = cap - 1;
  memcpy(out, start, n);
  out[n] = '\0';
  return true;
}

static bool parseValue(const char *key, int depth) {
  if (depth > JSON_MAX_DEPTH) return false;
  skipWs();
  if (*jp == '{') return parseObject(depth);
  if (*jp == '[') return parseArray(key, depth);
  if (*jp == '"') {
    char s[ITEM_MAX_LEN];
    if (!parseStringInto(s, sizeof(s))) return false;
    emitPair(key, s);
    return true;
  }
  if (strncmp(jp, "true", 4) == 0 && !isalnum((unsigned char)jp[4]) && jp[4] != '_') {
    jp += 4;
    emitPair(key, "true");
    return true;
  }
  if (strncmp(jp, "false", 5) == 0 && !isalnum((unsigned char)jp[5]) && jp[5] != '_') {
    jp += 5;
    emitPair(key, "false");
    return true;
  }
  if (strncmp(jp, "null", 4) == 0 && !isalnum((unsigned char)jp[4]) && jp[4] != '_') {
    jp += 4;
    emitPair(key, "null");
    return true;
  }
  char num[32];
  if (!parseNumber(num, sizeof(num))) return false;
  emitPair(key, num);
  return true;
}

static bool parseJsonMessage(const char *src) {
  if (!src) return false;
  jp = src;
  skipWs();
  if (*jp != '{' && *jp != '[' && *jp != '"' && *jp != '-' &&
      !isdigit((unsigned char)*jp) &&
      strncmp(jp, "true", 4) != 0 &&
      strncmp(jp, "false", 5) != 0 &&
      strncmp(jp, "null", 4) != 0) {
    return false;
  }
  clearItems();
  bool ok = false;
  if (*jp == '{') ok = parseObject(0);
  else if (*jp == '[') ok = parseArray("", 0);
  else ok = parseValue("", 0);
  skipWs();
  if (!ok || *jp != '\0' || itemCount == 0) {
    clearItems();
    return false;
  }
  return true;
}

static void loadMessage(const char *raw) {
  char folded[MSG_MAX_LEN];
  foldUtf8(raw ? raw : "", folded, sizeof(folded));

  if (parseJsonMessage(folded)) {
    itemsAreJson = true;
    Serial.printf("JSON message: %u field(s)\n", (unsigned)itemCount);
    for (uint8_t i = 0; i < itemCount; i++) {
      Serial.printf("  [%u] %s\n", (unsigned)i, displayItems[i]);
    }
  } else {
    itemsAreJson = false;
    clearItems();
    addItem(folded[0] ? folded : raw);
    Serial.printf("Plain message: %s\n", displayItems[0]);
  }
  itemIndex = 0;
}

/* ---------- Matrix drawing ---------- */

static uint16_t measureText(const char *p) {
  uint16_t w = 0;
  uint8_t tmp[8];
  if (!p) return 0;
  while (*p) {
    w += mx.getChar(*p++, sizeof(tmp) / sizeof(tmp[0]), tmp);
    w += CHAR_SPACING;
  }
  return w;
}

static void printText(uint8_t modStart, uint8_t modEnd, const char *pMsg, int16_t leftPad) {
  uint8_t state = 0;
  uint8_t curLen = 0;
  uint16_t showLen = 0;
  uint8_t cBuf[8];
  int16_t col = ((modEnd + 1) * COL_SIZE) - 1;
  const char *p = pMsg ? pMsg : "";

  if (leftPad > 0) {
    state = 3;
    showLen = (uint16_t)leftPad;
    curLen = 0;
  }

  mx.control(modStart, modEnd, MD_MAX72XX::UPDATE, MD_MAX72XX::OFF);

  do {
    switch (state) {
      case 0:
        if (*p == '\0') {
          showLen = col - (modEnd * COL_SIZE);
          state = 2;
          break;
        }
        showLen = mx.getChar(*p++, sizeof(cBuf) / sizeof(cBuf[0]), cBuf);
        curLen = 0;
        state = 1;
        /* fall through */
      case 1:
        mx.setColumn(col--, cBuf[curLen++]);
        if (curLen == showLen) {
          showLen = CHAR_SPACING;
          state = 2;
        }
        break;
      case 2:
        curLen = 0;
        state = 3;
        /* fall through */
      case 3:
        mx.setColumn(col--, 0);
        curLen++;
        if (curLen == showLen) state = 0;
        break;
      default:
        col = -1;
    }
  } while (col >= (int16_t)(modStart * COL_SIZE));

  mx.control(modStart, modEnd, MD_MAX72XX::UPDATE, MD_MAX72XX::ON);
}

static void showStatic(const char *msg) {
  mx.clear();
  uint16_t w = measureText(msg);
  int16_t pad = 0;
  if (w < DISPLAY_COLS) pad = (int16_t)((DISPLAY_COLS - w) / 2);
  printText(0, MAX_DEVICES - 1, msg, pad);
}

static void scrollLoadChar() {
  if (scroll.msg && *scroll.msg) {
    scroll.charWidth = mx.getChar(*scroll.msg++, sizeof(scroll.cBuf) / sizeof(scroll.cBuf[0]), scroll.cBuf);
    scroll.colInChar = 0;
    scroll.inChar = true;
  } else {
    scroll.inChar = false;
    if (scroll.padRight < 0) scroll.padRight = SCROLL_TRAIL_COLS;
  }
}

static void startScroll(const char *msg) {
  scroll.msg = msg;
  scroll.padLeft = SCROLL_LEAD_COLS;
  scroll.padRight = -1;
  scroll.inChar = false;
  scroll.done = false;
  mx.clear();
  scrollLoadChar();
}

static void scrollStep() {
  uint8_t col = 0;
  if (scroll.padLeft > 0) {
    scroll.padLeft--;
    col = 0;
  } else if (scroll.inChar) {
    if (scroll.colInChar < scroll.charWidth) {
      col = scroll.cBuf[scroll.colInChar++];
    } else {
      col = 0; /* inter-character space */
      scrollLoadChar();
    }
  } else if (scroll.padRight > 0) {
    scroll.padRight--;
    col = 0;
    if (scroll.padRight == 0) scroll.done = true;
  } else {
    scroll.done = true;
  }

  mx.transform(MD_MAX72XX::TSL);
  mx.setColumn(0, col);
}

static void beginItem(uint8_t idx) {
  if (itemCount == 0) {
    phase = PHASE_IDLE;
    mx.clear();
    return;
  }
  const char *msg = displayItems[idx % itemCount];
  uint16_t w = measureText(msg);
  phaseStarted = millis();
  if (w <= DISPLAY_COLS) {
    showStatic(msg);
    phase = PHASE_HOLD;
  } else {
    startScroll(msg);
    phase = PHASE_SCROLL;
  }
}

static void advanceItem() {
  if (itemCount == 0) {
    phase = PHASE_IDLE;
    return;
  }
  itemIndex = (itemIndex + 1) % itemCount;
  mx.clear();
  phase = PHASE_GAP;
  phaseStarted = millis();
}

static void resetMatrix(void) {
  mx.control(MD_MAX72XX::INTENSITY, MAX_INTENSITY / 2);
  mx.control(MD_MAX72XX::UPDATE, MD_MAX72XX::ON);
  mx.clear();
}

/* ---------- ESP-NOW ---------- */

class ESP_NOW_Peer_Class : public ESP_NOW_Peer {
public:
  ESP_NOW_Peer_Class(const uint8_t *mac_addr, uint8_t channel, wifi_interface_t iface, const uint8_t *lmk)
      : ESP_NOW_Peer(mac_addr, channel, iface, lmk) {}

  ~ESP_NOW_Peer_Class() {}

  bool add_peer() {
    if (!add()) {
      log_e("Failed to register the broadcast peer");
      return false;
    }
    return true;
  }

  void onReceive(const uint8_t *data, size_t len, bool broadcast) {
    Serial.printf("Received a message from master " MACSTR " (%s)\n", MAC2STR(addr()),
                  broadcast ? "broadcast" : "unicast");
    queueIncoming(data, len);
  }

  static void queueIncoming(const uint8_t *data, size_t len);
};

std::vector<ESP_NOW_Peer_Class *> masters;

void ESP_NOW_Peer_Class::queueIncoming(const uint8_t *data, size_t len) {
  if (!data || len == 0) return;
  size_t n = len;
  if (n >= MSG_MAX_LEN) n = MSG_MAX_LEN - 1;

  portENTER_CRITICAL(&msgMux);
  memcpy(pendingMsg, data, n);
  pendingMsg[n] = '\0';
  /* Drop a trailing newline that some senders append */
  while (n > 0 && (pendingMsg[n - 1] == '\n' || pendingMsg[n - 1] == '\r')) {
    pendingMsg[--n] = '\0';
  }
  hasNewMessage = true;
  portEXIT_CRITICAL(&msgMux);
}

void register_new_master(const esp_now_recv_info_t *info, const uint8_t *data, int len, void *arg) {
  if (memcmp(info->des_addr, ESP_NOW.BROADCAST_ADDR, 6) == 0) {
    Serial.printf("Unknown peer " MACSTR " sent a broadcast message\n", MAC2STR(info->src_addr));
    Serial.println("Registering the peer as a master");

    ESP_NOW_Peer_Class *new_master =
        new ESP_NOW_Peer_Class(info->src_addr, ESPNOW_WIFI_CHANNEL, WIFI_IF_STA, nullptr);
    if (!new_master->add_peer()) {
      Serial.println("Failed to register the new master");
      delete new_master;
      return;
    }
    masters.push_back(new_master);
    Serial.printf("Successfully registered master " MACSTR " (total masters: %lu)\n",
                  MAC2STR(new_master->addr()), (unsigned long)masters.size());

    /* Keep the first payload — it arrived before the peer existed. */
    if (data && len > 0) {
      ESP_NOW_Peer_Class::queueIncoming(data, (size_t)len);
    }
  } else {
    log_v("Received a unicast message from " MACSTR, MAC2STR(info->src_addr));
    log_v("Ignoring the message");
  }
}

/* ---------- Setup / loop ---------- */

void setup() {
  mx.begin();
  resetMatrix();
  Serial.begin(115200);

  WiFi.mode(WIFI_STA);
  WiFi.setChannel(ESPNOW_WIFI_CHANNEL);
  while (!WiFi.STA.started()) {
    delay(100);
  }

  Serial.println("ESP-NOW LED receiver");
  Serial.println("Wi-Fi parameters:");
  Serial.println("  Mode: STA");
  Serial.println("  MAC Address: " + WiFi.macAddress());
  Serial.printf("  Channel: %u\n", ESPNOW_WIFI_CHANNEL);

  if (!ESP_NOW.begin()) {
    Serial.println("Failed to initialize ESP-NOW");
    Serial.println("Rebooting in 5 seconds...");
    delay(5000);
    ESP.restart();
  }

  Serial.printf("ESP-NOW version: %d, max data length: %d\n", ESP_NOW.getVersion(), ESP_NOW.getMaxDataLen());

  ESP_NOW.onNewPeer(register_new_master, nullptr);

  loadMessage("READY");
  beginItem(0);

  Serial.println("Setup complete. Waiting for a master to broadcast a message...");
}

void loop() {
  if (hasNewMessage) {
    char local[MSG_MAX_LEN];
    portENTER_CRITICAL(&msgMux);
    strncpy(local, pendingMsg, MSG_MAX_LEN - 1);
    local[MSG_MAX_LEN - 1] = '\0';
    hasNewMessage = false;
    portEXIT_CRITICAL(&msgMux);

    Serial.printf("  Message: %s\n", local);
    loadMessage(local);
    beginItem(0);
  }

  unsigned long now = millis();

  switch (phase) {
    case PHASE_HOLD:
      if (now - phaseStarted >= ITEM_HOLD_MS) {
        if (itemCount <= 1) {
          phaseStarted = now; /* keep showing a single field */
        } else {
          advanceItem();
        }
      }
      break;
    case PHASE_SCROLL:
      if (now - phaseStarted >= SCROLL_MS_PER_COL) {
        phaseStarted = now;
        scrollStep();
        if (scroll.done) advanceItem();
      }
      break;
    case PHASE_GAP:
      if (now - phaseStarted >= ITEM_GAP_MS) {
        beginItem(itemIndex);
      }
      break;
    case PHASE_IDLE:
    default:
      break;
  }

  static unsigned long last_debug = 0;
  if (now - last_debug > 10000) {
    last_debug = now;
    Serial.printf("Registered masters: %lu | queue: %u item(s) (%s)\n",
                  (unsigned long)masters.size(), (unsigned)itemCount,
                  itemsAreJson ? "json" : "text");
    for (size_t i = 0; i < masters.size(); i++) {
      if (masters[i]) {
        Serial.printf("  Master %lu: " MACSTR "\n", (unsigned long)i, MAC2STR(masters[i]->addr()));
      }
    }
  }
}
