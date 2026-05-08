#include <Arduino.h>
#include "motor.h"

// Arduino-ESP32 core 3.x LEDC API: channels are assigned automatically by pin.
const int freq = 10000;
const int resolution = 8;

// Define el incremento/decremento suave de la velocidad
#define RAMP_RATE 5
#define MAX_SPEED 100

Motor::Motor(int pinA1, int pinA2, int pinB1, int pinB2, int pinC1, int pinC2, int pinD1, int pinD2,
             int pinPWM_A, int pinPWM_B, int pinPWM_C, int pinPWM_D) {
  pinA1_ = pinA1;
  pinA2_ = pinA2;
  pinB1_ = pinB1;
  pinB2_ = pinB2;
  pinC1_ = pinC1;
  pinC2_ = pinC2;
  pinD1_ = pinD1;
  pinD2_ = pinD2;
  pinPWM_A_ = pinPWM_A;
  pinPWM_B_ = pinPWM_B;
  pinPWM_C_ = pinPWM_C;
  pinPWM_D_ = pinPWM_D;

  pinMode(pinA1_, OUTPUT);
  pinMode(pinA2_, OUTPUT);
  pinMode(pinB1_, OUTPUT);
  pinMode(pinB2_, OUTPUT);
  pinMode(pinC1_, OUTPUT);
  pinMode(pinC2_, OUTPUT);
  pinMode(pinD1_, OUTPUT);
  pinMode(pinD2_, OUTPUT);
  pinMode(pinPWM_A_, OUTPUT);
  pinMode(pinPWM_B_, OUTPUT);
  pinMode(pinPWM_C_, OUTPUT);
  pinMode(pinPWM_D_, OUTPUT);

  arms_size_ = 1;

  // Nuevo API LEDC: ledcAttach(pin, freq, resolution)
  ledcAttach(pinPWM_A_, freq, resolution);
  ledcAttach(pinPWM_B_, freq, resolution);
  ledcAttach(pinPWM_C_, freq, resolution);
  ledcAttach(pinPWM_D_, freq, resolution);
}

void Motor::setSpeed(int motor, float spd) {
  if (motor < 0 || motor >= 4) return;

  if (spd > MAX_SPEED) spd = MAX_SPEED;
  if (spd < -MAX_SPEED) spd = -MAX_SPEED;

  setpoints_speeds[motor] = spd;
}

void Motor::vectorMovement(float X, float Y, float W) {
  float speed_A =  (Y) + (arms_size_ * W);
  float speed_B = (-X) + (arms_size_ * W);
  float speed_C = (-Y) + (arms_size_ * W);
  float speed_D =  (X) + (arms_size_ * W);

  setSpeed(0, speed_A);
  setSpeed(1, speed_B);
  setSpeed(2, speed_C);
  setSpeed(3, speed_D);
}

void Motor::controlMovimiento(int opcion, int spx, int spy) {
  switch (opcion) {
    case 0: // Detenerse
      setSpeed(0, 0);
      setSpeed(1, 0);
      setSpeed(2, 0);
      setSpeed(3, 0);
      break;

    case 1: // Adelante/Atrás
      setSpeed(0, spx - spy);
      setSpeed(1, spx - spy);
      setSpeed(2, spx + spy);
      setSpeed(3, spx + spy);
      break;

    case 2: // Giro
      setSpeed(0, spx - spy);
      setSpeed(1, spx + spy);
      setSpeed(2, spx - spy);
      setSpeed(3, spx + spy);
      break;

    case 3: // Diagonal
      setSpeed(0, spx);
      setSpeed(1, 0);
      setSpeed(2, spx);
      setSpeed(3, 0);
      break;

    case 4: // Pull-over
      setSpeed(0, -spx);
      setSpeed(1, spx);
      setSpeed(2, -spx);
      setSpeed(3, spx);
      break;

    default:
      break;
  }
}

void Motor::updateMotors() {
  for (int m = 0; m < 4; m++) {
    float currentSpeed = current_speeds[m];
    float setpoint = setpoints_speeds[m];

    if (currentSpeed != setpoint) {
      if (setpoint > currentSpeed) {
        currentSpeed += RAMP_RATE;
        if (currentSpeed > setpoint) currentSpeed = setpoint;
      } else {
        currentSpeed -= RAMP_RATE;
        if (currentSpeed < setpoint) currentSpeed = setpoint;
      }

      current_speeds[m] = currentSpeed;
      int duty = abs((int)currentSpeed);

      switch (m) {
        case 0: ledcWrite(pinPWM_A_, duty); break;
        case 1: ledcWrite(pinPWM_B_, duty); break;
        case 2: ledcWrite(pinPWM_C_, duty); break;
        case 3: ledcWrite(pinPWM_D_, duty); break;
        default: break;
      }

      int spd = (int)currentSpeed;
      switch (m) {
        case 0:
          digitalWrite(pinA1_, spd > 0 ? HIGH : LOW);
          digitalWrite(pinA2_, spd < 0 ? HIGH : LOW);
          break;
        case 1:
          digitalWrite(pinB1_, spd > 0 ? HIGH : LOW);
          digitalWrite(pinB2_, spd < 0 ? HIGH : LOW);
          break;
        case 2:
          digitalWrite(pinC1_, spd > 0 ? HIGH : LOW);
          digitalWrite(pinC2_, spd < 0 ? HIGH : LOW);
          break;
        case 3:
          digitalWrite(pinD1_, spd > 0 ? HIGH : LOW);
          digitalWrite(pinD2_, spd < 0 ? HIGH : LOW);
          break;
        default:
          break;
      }
    }
  }
}
