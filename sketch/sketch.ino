/*
 * Sentinel-Q  --  STM32U585 side
 *
 * The MCU owns exactly two things: the button and the pixels. The inspection
 * sequence itself lives in Python on the Qualcomm side (sentinelq/procedure.py),
 * because the hold window has to stay synchronised with reading the sensors and
 * because timings and wording should be config, not a reflash.
 *
 * BUTTON -- D2, external, to GND, INPUT_PULLUP, pressed = LOW.
 *   The board's only physical button (JBTN1, marked POWER) is wired to the
 *   Qualcomm PMIC as the power key -- a 5 second press reboots Linux. It is not
 *   reachable from a sketch, and the Zephyr board definition has no gpio-keys
 *   node or sw0 alias. So the PTI button is an external momentary switch.
 *
 *   D2 is deliberate: D0/D1 are UART, D4/D5 are FDCAN1 (reserved for the CAN
 *   work), D10-D13 are SPI, D18-D21 are I2C. D2 is a plain GPIO.
 *
 * DISPLAY -- the onboard 8x13 LED matrix, driven by Arduino_LED_Matrix from the
 *   Zephyr core. Do not install that library separately; it ships with the core.
 *
 * SERIAL -- note that `Serial` on the UNO Q is the UART on pins 0/1, NOT USB.
 *   Debug output goes through Monitor from Arduino_RouterBridge, which appears
 *   in the App Lab console and the IDE Serial Monitor.
 */

#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>

ArduinoLEDMatrix matrix;

static const int PTI_BUTTON_PIN = 2;
static const unsigned long DEBOUNCE_MS = 40;

// 8 rows x 13 columns, as the Python side sends it.
static const uint8_t MATRIX_ROWS = 8;
static const uint8_t MATRIX_COLS = 13;
static uint8_t frameBuffer[MATRIX_ROWS][MATRIX_COLS];

static bool     buttonLatched   = false;  // set on press, cleared when read
static int      lastReading     = HIGH;
static int      stableState     = HIGH;
static unsigned long lastChange = 0;

/*
 * draw(): called from Python over Bridge with 13 column words. Bit 0 of each
 * word is the top row. One call per frame -- at a 50 ms scroll step that is
 * 20 calls a second, which the Bridge handles comfortably.
 */
void draw(String columns) {
  int col = 0;
  int start = 0;
  while (col < MATRIX_COLS && start <= (int)columns.length()) {
    int comma = columns.indexOf(',', start);
    String token = (comma < 0) ? columns.substring(start)
                               : columns.substring(start, comma);
    uint16_t bits = (uint16_t)token.toInt();
    for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
      frameBuffer[row][col] = (bits >> row) & 0x01;
    }
    col++;
    if (comma < 0) break;
    start = comma + 1;
  }
  for (; col < MATRIX_COLS; col++) {
    for (uint8_t row = 0; row < MATRIX_ROWS; row++) frameBuffer[row][col] = 0;
  }
  matrix.renderBitmap(frameBuffer, MATRIX_ROWS, MATRIX_COLS);
}

void clearMatrix() {
  memset(frameBuffer, 0, sizeof(frameBuffer));
  matrix.renderBitmap(frameBuffer, MATRIX_ROWS, MATRIX_COLS);
}

/*
 * buttonPressed(): returns 1 exactly once per physical press, then clears.
 *
 * Latching matters. Python polls this a few times a second, and a press that
 * happened between polls must not be lost -- a driver pressing the button and
 * seeing nothing happen will press it again, and again.
 */
int buttonPressed() {
  if (buttonLatched) {
    buttonLatched = false;
    return 1;
  }
  return 0;
}

int buttonState() {
  return (stableState == LOW) ? 1 : 0;   // 1 while physically held down
}

void setup() {
  pinMode(PTI_BUTTON_PIN, INPUT_PULLUP);

  matrix.begin();
  clearMatrix();

  Bridge.begin();
  Bridge.provide("draw", draw);
  Bridge.provide("clear_matrix", clearMatrix);
  Bridge.provide("button_pressed", buttonPressed);
  Bridge.provide("button_state", buttonState);

  Monitor.begin();
  Monitor.println("Sentinel-Q MCU ready: button D2, 8x13 matrix");
}

void loop() {
  int reading = digitalRead(PTI_BUTTON_PIN);

  if (reading != lastReading) {
    lastChange = millis();
    lastReading = reading;
  }

  if ((millis() - lastChange) > DEBOUNCE_MS && reading != stableState) {
    stableState = reading;
    if (stableState == LOW) {          // falling edge = pressed
      buttonLatched = true;
      Monitor.println("PTI button pressed");
    }
  }

  Bridge.update();
}
