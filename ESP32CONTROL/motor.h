#ifndef MOTOR_H
#define MOTOR_H

#define DETENERSE 0
#define ADELANTE_ATRAS 1
#define GIRO 2
#define DIAGONAL 3
#define PULL_OVER 4


#include <Arduino.h>

class Motor {
  public:
    Motor(int pinA1, int pinA2, int pinB1, int pinB2, int pinC1, int pinC2, int pinD1, int pinD2, int pinPWM_A, int pinPWM_B, int pinPWM_C, int pinPWM_D);
    void setSpeed(int motor, float spd);
    void vectorMovement(float X, float Y, float W);
    void controlMovimiento(int opcion, int spx = 100 , int spy = 100);
    void updateMotors(void);
    

  private:
    int pinA1_, pinA2_, pinB1_, pinB2_, pinC1_, pinC2_, pinD1_, pinD2_;
    int pinPWM_A_, pinPWM_B_, pinPWM_C_, pinPWM_D_;
    float arms_size_;
    void smoothSetSpeed(int motor, float setpoint);
    int getPWMPin(int motor);

    // Array to store current speeds for each motor
    float current_speeds[4] = {0}; // Initialize all to 0
    float setpoints_speeds[4] = {0}; // Initialize all to 0
};

#endif

