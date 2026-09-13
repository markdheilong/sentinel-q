/*
 * Sentinel-Q  --  driver interface, standalone
 * =============================================
 *
 * The pre-trip inspection sequence as the driver experiences it, running
 * entirely on the STM32U585 with no Linux side and no Bridge RPC.
 *
 * WHY THIS EXISTS SEPARATELY FROM sketch.ino
 *
 *   sketch.ino is the real thing: the MCU owns the button and the pixels, and
 *   Python on the Qualcomm side drives them over Bridge RPC while it sweeps the
 *   sensors. That is the correct architecture and it is what ships.
 *
 *   But it cannot run without the host service, which is not built yet. And
 *   every dependency is a thing that can fail to compile at 8 PM.
 *
 *   So this sketch demonstrates the driver interface on its own. Two includes
 *   fewer, no RPC, no Python. If the matrix lights up, the MCU half works. If it
 *   doesn't, the fault is in eleven lines of pin setup rather than somewhere in
 *   a stack we cannot see.
 *
 *   Be precise about what it proves: this is the MCU half running standalone,
 *   with the sequence timed on the MCU rather than driven by a real sweep. It is
 *   not the integrated system. Say so.
 *
 * WIRING
 *
 *   Switch between D2 and GND. INPUT_PULLUP, so closed reads LOW.
 *
 *   D2 is deliberate. D0/D1 are UART, D4/D5 are FDCAN1 and are reserved for the
 *   CAN work, D10-D13 are SPI, D18-D21 are I2C. D2 is a plain GPIO.
 *
 *   The board's own button (JBTN1, marked POWER) is wired to the Qualcomm PM4125
 *   PMIC on KYPDPWR_N. It is a power key, not a GPIO, and a sketch cannot read
 *   it at all. That is why there is an external switch.
 *
 * THE SWITCH IS A TOGGLE, NOT A BUTTON
 *
 *   The part to hand is an ALCO MTA-106D miniature toggle, which stays where you
 *   put it. So we trigger on ANY stable transition rather than on a falling edge
 *   -- each flip starts one inspection, in either direction. A momentary button
 *   would work with this same code; it would simply produce two transitions per
 *   press, and the guard below absorbs the second.
 */

#include <Arduino_LED_Matrix.h>

ArduinoLEDMatrix matrix;

// ---------------------------------------------------------------------------
// Hardware
// ---------------------------------------------------------------------------

static const int  PTI_SWITCH_PIN = 2;
static const unsigned long DEBOUNCE_MS = 40;

// The UNO Q matrix is 8 rows by 13 columns.
static const uint8_t ROWS = 8;
static const uint8_t COLS = 13;

static uint8_t frame[ROWS][COLS];

// ---------------------------------------------------------------------------
// 5x7 font, column-major, bit 0 = top row
//
// Only the characters the driver sequence actually uses. A missing glyph draws
// as a blank column rather than crashing, which is the right failure on a
// display a driver is relying on.
// ---------------------------------------------------------------------------

struct Glyph { char c; uint8_t col[5]; };

static const Glyph FONT[] = {
  {' ', {0x00,0x00,0x00,0x00,0x00}},
  {'.', {0x00,0x60,0x60,0x00,0x00}},
  {'0', {0x3E,0x51,0x49,0x45,0x3E}},
  {'1', {0x00,0x42,0x7F,0x40,0x00}},
  {'2', {0x42,0x61,0x51,0x49,0x46}},
  {'3', {0x21,0x41,0x45,0x4B,0x31}},
  {'4', {0x18,0x14,0x12,0x7F,0x10}},
  {'5', {0x27,0x45,0x45,0x45,0x39}},
  {'6', {0x3C,0x4A,0x49,0x49,0x30}},
  {'7', {0x01,0x71,0x09,0x05,0x03}},
  {'8', {0x36,0x49,0x49,0x49,0x36}},
  {'9', {0x06,0x49,0x49,0x29,0x1E}},
  {'A', {0x7E,0x11,0x11,0x11,0x7E}},
  {'B', {0x7F,0x49,0x49,0x49,0x36}},
  {'C', {0x3E,0x41,0x41,0x41,0x22}},
  {'D', {0x7F,0x41,0x41,0x22,0x1C}},
  {'E', {0x7F,0x49,0x49,0x49,0x41}},
  {'H', {0x7F,0x08,0x08,0x08,0x7F}},
  {'I', {0x00,0x41,0x7F,0x41,0x00}},
  {'K', {0x7F,0x08,0x14,0x22,0x41}},
  {'L', {0x7F,0x40,0x40,0x40,0x40}},
  {'M', {0x7F,0x02,0x04,0x02,0x7F}},
  {'N', {0x7F,0x04,0x08,0x10,0x7F}},
  {'O', {0x3E,0x41,0x41,0x41,0x3E}},
  {'P', {0x7F,0x09,0x09,0x09,0x06}},
  {'R', {0x7F,0x09,0x19,0x29,0x46}},
  {'S', {0x46,0x49,0x49,0x49,0x31}},
  {'T', {0x01,0x01,0x7F,0x01,0x01}},
  {'U', {0x3F,0x40,0x40,0x40,0x3F}},
};

static const uint8_t FONT_COUNT = sizeof(FONT) / sizeof(FONT[0]);

static const uint8_t* glyphFor(char c) {
  for (uint8_t i = 0; i < FONT_COUNT; i++) {
    if (FONT[i].c == c) return FONT[i].col;
  }
  return FONT[0].col;   // unknown character -> blank
}

// ---------------------------------------------------------------------------
// Rendering
//
// Every write to the panel goes through show(). If the LED matrix API differs
// on your core version, this is the ONE function to change.
// ---------------------------------------------------------------------------

static void show() {
  matrix.renderBitmap(frame, ROWS, COLS);
}

static void clearFrame() {
  memset(frame, 0, sizeof(frame));
}

static void blank() {
  clearFrame();
  show();
}

/*
 * Draw one window of a scrolling message.
 *
 * The message is treated as a virtual bitmap 6 columns wide per character (5 for
 * the glyph, 1 of spacing). `offset` is which virtual column maps to the
 * left-most physical column. Columns outside the message are simply blank, which
 * is what gives the text somewhere to scroll in from and out to.
 */
static void drawMessageWindow(const char* text, int offset) {
  clearFrame();
  const int len = strlen(text);
  const int virtualWidth = len * 6;

  for (uint8_t x = 0; x < COLS; x++) {
    const int v = offset + x;
    if (v < 0 || v >= virtualWidth) continue;

    const int charIndex = v / 6;
    const int colInChar = v % 6;
    if (colInChar == 5) continue;            // the spacing column

    const uint8_t bits = glyphFor(text[charIndex])[colInChar];
    for (uint8_t row = 0; row < 7; row++) {
      frame[row][x] = (bits >> row) & 0x01;
    }
  }
  show();
}

/*
 * Scroll a message once, right to left, all the way out.
 *
 * Blocking on purpose. This sketch has exactly one job at a time and a driver
 * standing at a wheel end is not multitasking either. The switch is re-read at
 * the end of the sequence, not during it -- a mid-sequence press should not
 * restart an inspection that is already running.
 */
static void scrollMessage(const char* text, unsigned long stepMs) {
  const int virtualWidth = strlen(text) * 6;
  for (int offset = -(int)COLS; offset <= virtualWidth; offset++) {
    drawMessageWindow(text, offset);
    delay(stepMs);
  }
}

/* A single character held in the centre of the panel -- used for the countdown. */
static void showCentred(char c, unsigned long holdMs) {
  clearFrame();
  const uint8_t* g = glyphFor(c);
  const uint8_t xStart = (COLS - 5) / 2;     // 4, for a 13-wide panel
  for (uint8_t i = 0; i < 5; i++) {
    for (uint8_t row = 0; row < 7; row++) {
      frame[row][xStart + i] = (g[i] >> row) & 0x01;
    }
  }
  show();
  delay(holdMs);
}

/*
 * A bar that empties left to right over the hold window.
 *
 * The driver is standing on a brake pedal for ten seconds with nothing else to
 * look at. A countdown that visibly moves is the difference between "it's
 * working" and "has it frozen".
 */
static void holdBar(unsigned long totalMs) {
  const unsigned long start = millis();
  unsigned long elapsed = 0;

  while (elapsed < totalMs) {
    elapsed = millis() - start;
    const uint8_t remaining = COLS - (uint8_t)((elapsed * COLS) / totalMs);

    clearFrame();
    for (uint8_t x = 0; x < remaining && x < COLS; x++) {
      frame[3][x] = 1;
      frame[4][x] = 1;
    }
    show();
    delay(50);
  }
}

// ---------------------------------------------------------------------------
// The switch
// ---------------------------------------------------------------------------

static int lastReading = HIGH;
static int stableState = HIGH;
static unsigned long lastChange = 0;
static bool triggered = false;

/* Debounce, and latch on any stable transition. See the header note on toggles. */
static void pollSwitch() {
  const int reading = digitalRead(PTI_SWITCH_PIN);

  if (reading != lastReading) {
    lastChange = millis();
    lastReading = reading;
  }

  if ((millis() - lastChange) > DEBOUNCE_MS && reading != stableState) {
    stableState = reading;
    triggered = true;
  }
}

static bool takeTrigger() {
  if (!triggered) return false;
  triggered = false;
  return true;
}

// ---------------------------------------------------------------------------
// The inspection sequence
//
// Timings match sentinelq/procedure.py so the standalone demonstration and the
// integrated system feel identical to a driver: 3 second arm, 10 second hold.
// ---------------------------------------------------------------------------

static void runInspection() {
  showCentred('3', 1000);
  showCentred('2', 1000);
  showCentred('1', 1000);

  scrollMessage("BRAKE ON 100 PSI", 45);
  holdBar(10000);
  scrollMessage("RELEASE", 45);

  blank();
  delay(400);

  // Standalone, so there is no sweep and no verdict. This reports that the
  // sequence completed, which is the honest thing for it to say.
  scrollMessage("CHECK COMPLETE", 45);
  blank();
  delay(600);
}

void setup() {
  pinMode(PTI_SWITCH_PIN, INPUT_PULLUP);

  matrix.begin();
  blank();

  // Settle the debouncer on whatever position the switch is already in, so we
  // do not fire an inspection the instant the board powers up.
  lastReading = stableState = digitalRead(PTI_SWITCH_PIN);
  lastChange = millis();
  triggered = false;
}

void loop() {
  // Idle: invite the driver, and re-check the switch between scroll steps so a
  // flip is never missed while a message is running.
  const char* idle = "PRESS TO START";
  const int virtualWidth = strlen(idle) * 6;

  for (int offset = -(int)COLS; offset <= virtualWidth; offset++) {
    pollSwitch();
    if (takeTrigger()) {
      runInspection();
      return;                  // back to the top of loop(), idle again
    }
    drawMessageWindow(idle, offset);
    delay(50);
  }
}
