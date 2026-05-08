#include <Arduino.h>
#include <stdio.h>

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif

#include "motor.h"
#include "CLI.h"

// Pines servos cabeza
const int servoPinPan  = 14;
const int servoPinTilt = 26;

// PWM servo 50 Hz, resolución 16 bits.
#define SERVO_PWM_FREQ 50
#define SERVO_PWM_RES  16
#define MIN_SERVO 3276   // ~1.0 ms en 16 bits a 50 Hz
#define MAX_SERVO 6553   // ~2.0 ms en 16 bits a 50 Hz

// Límites mecánicos internos en grados relativos al centro.
const int SERVO_MIN_POS = -90;
const int SERVO_MAX_POS =  90;
const int SERVO_DEADBAND = 8;  // Zona muerta del joystick: -8..8 sostiene posición.

// Pines motores
#define MOTOR_A_AINA 15
#define MOTOR_A_AINB 2
#define MOTOR_A_PWM  4

#define MOTOR_B_AINA 19
#define MOTOR_B_AINB 5
#define MOTOR_B_PWM  18

#define MOTOR_C_AINA 12
#define MOTOR_C_AINB 27
#define MOTOR_C_PWM  13

#define MOTOR_D_AINA 25
#define MOTOR_D_AINB 32
#define MOTOR_D_PWM  33

// Potencia / batería
#define BATTERY_VOLTAGE_PIN  34
#define CHARGER_DETECTED_PIN 35
#define KILL_BATTERY         23
#define CONNECT_CHARGER      22

const float BATTERY_LOW_CUTOFF_V = 7.0;
const float CHARGER_PRESENT_V    = 13.0;
const float ADC_SCALE_FACTOR     = 11.0 * (3.3 / 4096.0);

// Filtro de batería/cargador:
// Se mide cada 100 ms y se aplica un filtro exponencial con tau ~= 5 s.
// Esto evita que un pico por arranque/movimiento de motores cambie bruscamente el valor reportado.
const uint32_t POWER_SAMPLE_PERIOD_MS = 100;
const float POWER_FILTER_TAU_MS       = 5000.0;
const float POWER_FILTER_ALPHA        = (float)POWER_SAMPLE_PERIOD_MS / (POWER_FILTER_TAU_MS + POWER_SAMPLE_PERIOD_MS);

Motor motores(MOTOR_A_AINA, MOTOR_A_AINB,
              MOTOR_B_AINA, MOTOR_B_AINB,
              MOTOR_C_AINA, MOTOR_C_AINB,
              MOTOR_D_AINA, MOTOR_D_AINB,
              MOTOR_A_PWM, MOTOR_B_PWM, MOTOR_C_PWM, MOTOR_D_PWM);

int modo_mov = 0, vel = 0, dif = 0;
int timeout_mov = 0;

// st t <tilt> p <pan>: ahora son comandos relativos del joystick (-100..100).
// 0 significa no seguir incrementando; mantiene la última posición alcanzada.
int servo_pan = 0;
int servo_tilt = 0;

// Posición objetivo interna acumulada.
int servo_pan_target = 0;
int servo_tilt_target = 0;

// Posición física enviada al servo; se acerca suavemente al target.
int servo_pan_motor = 0;
int servo_tilt_motor = 0;

// Configurable por comando: ss i <1..10>
int servo_step = 1;

// pw p <0|1>: 1 = conecta batería al robot, 0 = desconecta batería del robot.
// Se deja en 1 para conservar el comportamiento anterior de arranque con batería conectada.
int powerON = 1;
int batteryRelayConnected = 1;
int chargerRelayConnected = 0;

// Valores filtrados usados para reporte y decisión estable de potencia.
float batteryVoltage = 0.0;
float chargerVoltage = 0.0;

// Valores instantáneos, útiles para diagnóstico.
float batteryVoltageRaw = 0.0;
float chargerVoltageRaw = 0.0;

bool powerMeasurementsInitialized = false;
unsigned long lastPowerSampleMs = 0;

hw_timer_t *timer = NULL;
portMUX_TYPE timerMux = portMUX_INITIALIZER_UNLOCKED;
volatile bool motor_tick = false;

unsigned long lastServoUpdateMs = 0;
const uint32_t SERVO_UPDATE_PERIOD_MS = 30;

void ARDUINO_ISR_ATTR onTimer() {
  portENTER_CRITICAL_ISR(&timerMux);
  motor_tick = true;
  portEXIT_CRITICAL_ISR(&timerMux);
}

static int servoToDuty(int pos) {
  pos = constrain(pos, SERVO_MIN_POS, SERVO_MAX_POS);
  return map(pos, SERVO_MIN_POS, SERVO_MAX_POS, MIN_SERVO, MAX_SERVO);
}

static int directionFromJoystick(int value) {
  if (value > SERVO_DEADBAND) return 1;
  if (value < -SERVO_DEADBAND) return -1;
  return 0;
}

void updateServoAxis(int joystickCmd, int &targetPos, int &motorPos, int servoPin) {
  // 1) Acumula posición objetivo según la dirección del joystick.
  int dir = directionFromJoystick(joystickCmd);
  if (dir != 0) {
    targetPos += dir * servo_step;
    targetPos = constrain(targetPos, SERVO_MIN_POS, SERVO_MAX_POS);
  }

  // 2) Mueve el servo físico suavemente hacia el objetivo acumulado.
  if (motorPos < targetPos) {
    motorPos++;
  } else if (motorPos > targetPos) {
    motorPos--;
  }

  ledcWrite(servoPin, servoToDuty(motorPos));
}

void updateServos() {
  unsigned long now = millis();
  if (now - lastServoUpdateMs < SERVO_UPDATE_PERIOD_MS) return;
  lastServoUpdateMs = now;

  updateServoAxis(servo_tilt, servo_tilt_target, servo_tilt_motor, servoPinTilt);
  updateServoAxis(servo_pan,  servo_pan_target,  servo_pan_motor,  servoPinPan);
}

void centerServos(void) {
  // Detiene el comando relativo del joystick y ordena retorno absoluto al centro.
  // El retorno sigue siendo suave porque servo_*_motor se acerca a target de a 1 paso.
  servo_tilt = 0;
  servo_pan = 0;
  servo_tilt_target = 0;
  servo_pan_target = 0;
}

void updatePowerMeasurements(void) {
  unsigned long now = millis();

  if (powerMeasurementsInitialized && (now - lastPowerSampleMs < POWER_SAMPLE_PERIOD_MS)) {
    return;
  }

  lastPowerSampleMs = now;

  int voltageADC = analogRead(BATTERY_VOLTAGE_PIN);
  batteryVoltageRaw = voltageADC * ADC_SCALE_FACTOR;

  voltageADC = analogRead(CHARGER_DETECTED_PIN);
  chargerVoltageRaw = voltageADC * ADC_SCALE_FACTOR;

  if (!powerMeasurementsInitialized) {
    batteryVoltage = batteryVoltageRaw;
    chargerVoltage = chargerVoltageRaw;
    powerMeasurementsInitialized = true;
  } else {
    batteryVoltage += POWER_FILTER_ALPHA * (batteryVoltageRaw - batteryVoltage);
    chargerVoltage += POWER_FILTER_ALPHA * (chargerVoltageRaw - chargerVoltage);
  }
}

void applyPowerControl(void) {
  bool chargerPresent = (chargerVoltage > CHARGER_PRESENT_V);
  bool batteryCritical = (!chargerPresent && (batteryVoltage < BATTERY_LOW_CUTOFF_V));

  if (chargerPresent) {
    digitalWrite(CONNECT_CHARGER, LOW);   // Conecta el cargador
    chargerRelayConnected = 1;
  } else {
    digitalWrite(CONNECT_CHARGER, HIGH);  // Desconecta el cargador
    chargerRelayConnected = 0;
  }

  if (batteryCritical || powerON == 0) {
    digitalWrite(KILL_BATTERY, HIGH);     // Desconecta batería del robot
    batteryRelayConnected = 0;
  } else {
    digitalWrite(KILL_BATTERY, LOW);      // Conecta batería al robot
    batteryRelayConnected = 1;
  }
}

void setup() {
  Serial.begin(115200);

  analogReadResolution(12);
  analogSetPinAttenuation(BATTERY_VOLTAGE_PIN, ADC_11db);
  analogSetPinAttenuation(CHARGER_DETECTED_PIN, ADC_11db);

  pinMode(KILL_BATTERY, OUTPUT);
  digitalWrite(KILL_BATTERY, LOW);

  pinMode(CONNECT_CHARGER, OUTPUT);
  digitalWrite(CONNECT_CHARGER, HIGH);

  // Arduino-ESP32 core 3.x Timer API:
  // timerBegin(frequency_hz), timerAttachInterrupt(timer, callback), timerAlarm(timer, alarm_us, autoreload, reload_count)
  timer = timerBegin(1000000);          // 1 MHz = 1 tick/us
  timerAttachInterrupt(timer, &onTimer);
  timerAlarm(timer, 1000, true, 0);     // interrupción cada 1000 us = 1 ms

  delay(1000);
  motores.controlMovimiento(DETENERSE, 0, 0);
  printf("LISTO PARA CONECTARSE....\n");

  // Nuevo API LEDC: ledcAttach(pin, freq, resolution)
  ledcAttach(servoPinPan,  SERVO_PWM_FREQ, SERVO_PWM_RES);
  ledcAttach(servoPinTilt, SERVO_PWM_FREQ, SERVO_PWM_RES);

  servo_pan = 0;
  servo_tilt = 0;
  servo_pan_target = 0;
  servo_tilt_target = 0;
  servo_pan_motor = 0;
  servo_tilt_motor = 0;

  ledcWrite(servoPinTilt, servoToDuty(servo_tilt_motor));
  ledcWrite(servoPinPan,  servoToDuty(servo_pan_motor));

  updatePowerMeasurements();
  applyPowerControl();

  cli_init();
}

void loop() {
  Cli_Interface();

  // Actualización de motores fuera de la ISR para evitar trabajo pesado dentro de interrupción.
  if (motor_tick) {
    portENTER_CRITICAL(&timerMux);
    motor_tick = false;
    portEXIT_CRITICAL(&timerMux);
    motores.updateMotors();
  }

  updatePowerMeasurements();
  applyPowerControl();

  motores.controlMovimiento(modo_mov, vel, dif);
  updateServos();
}
