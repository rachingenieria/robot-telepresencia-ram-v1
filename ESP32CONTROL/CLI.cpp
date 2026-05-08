#include <Arduino.h>
#include <SimpleCLI.h>

extern int modo_mov, vel, dif;
extern int timeout_mov;

// Ahora servo_pan y servo_tilt son comandos relativos del joystick, no posiciones absolutas.
extern int servo_pan, servo_tilt;
extern int servo_pan_target, servo_tilt_target;
extern int servo_pan_motor, servo_tilt_motor;
extern int servo_step;

extern int powerON;
extern int batteryRelayConnected;
extern int chargerRelayConnected;
extern float batteryVoltage;
extern float chargerVoltage;
extern float batteryVoltageRaw;
extern float chargerVoltageRaw;

void centerServos(void);
void updatePowerMeasurements(void);
void applyPowerControl(void);

SimpleCLI cli;

Command setMov;
Command setServo;
Command setServoSetup;
Command setServoCenter;
Command setPower;
Command getBatteryVoltage;

void setmov_callback(cmd* c);
void setServo_callback(cmd* c);
void setServoSetup_callback(cmd* c);
void setServoCenter_callback(cmd* c);
void setpower_callback(cmd* c);
void getBatteryVoltage_callback(cmd* c);

static int voltsToDecivolts(float voltage) {
  // Retorna voltaje en décimas de voltio. Ejemplo: 12.3 V => 123.
  if (voltage >= 0.0f) {
    return (int)(voltage * 10.0f + 0.5f);
  }
  return (int)(voltage * 10.0f - 0.5f);
}

void cli_init(void) {
  Serial2.begin(115200);

  setMov = cli.addCmd("sm", setmov_callback);
  setMov.addArg("m");
  setMov.addArg("v");
  setMov.addArg("d");

  // pw -p <0|1>
  // 1 = conecta batería al robot, 0 = desconecta batería del robot.
  setPower = cli.addCmd("pw", setpower_callback);
  setPower.addArg("p");

  // st -t <tilt_dir> -p <pan_dir>
  // Rango recomendado: -100 a 100. 0 = sostener posición actual.
  setServo = cli.addCmd("st", setServo_callback);
  setServo.addArg("t");
  setServo.addArg("p");

  // ss -i <incremento>
  // Define cuántos grados internos se agregan/restan a la posición objetivo por ciclo de servo.
  setServoSetup = cli.addCmd("ss", setServoSetup_callback);
  setServoSetup.addArg("i");

  // sc: servo center. Retorna la cabeza al centro absoluto de forma suave.
  setServoCenter = cli.addCmd("sc", setServoCenter_callback);

  // b, bv o bb: battery voltage. Retorna SOLO el voltaje filtrado de batería con un decimal.
  // No requiere argumentos. Ejemplo de respuesta: 12.3
  getBatteryVoltage = cli.addCmd("b,bv,bb", getBatteryVoltage_callback);
}

String input;

void Cli_Interface(void) {
  if (Serial2.available()) {
    char c = Serial2.read();
    input += c;
    if (c == '\r') {
      printf("\n");
      Serial.print(input);
      printf("\n");
      cli.parse(input);
      input = "";
    } else {
      printf("%c", c);
    }
  }

  if (Serial.available()) {
    char c = Serial.read();
    input += c;
    if (c == '\r') {
      printf("\n");
      Serial.print(input);
      printf("\n");
      cli.parse(input);
      input = "";
    } else {
      printf("%c", c);
    }
  }

  if (cli.errored()) {
    CommandError cmdError = cli.getError();

    String str = cmdError.toString();
    int str_len = str.length() + 1;
    char char_array[str_len];
    str.toCharArray(char_array, str_len);

    printf("ERR CLI %s\n", char_array);

    if (cmdError.hasCommand()) {
      String cmdStr = cmdError.getCommand().toString();
      int cmd_len = cmdStr.length() + 1;
      char cmd_array[cmd_len];
      cmdStr.toCharArray(cmd_array, cmd_len);
      printf("ERR CLI did_you_mean:%s\n", cmd_array);
    }
  }
}

void setmov_callback(cmd* c) {
  Command cmd(c);

  modo_mov = cmd.getArg("m").getValue().toInt();
  vel      = cmd.getArg("v").getValue().toInt();
  dif      = cmd.getArg("d").getValue().toInt();

  if (timeout_mov) {
    timeout_mov = 3000;
  } else {
    timeout_mov = 10000;
  }

  printf("OK SM m:%d v:%d d:%d servo_cmd_p:%d servo_cmd_t:%d servo_target_p:%d servo_target_t:%d servo_motor_p:%d servo_motor_t:%d\n",
         modo_mov, vel, dif,
         servo_pan, servo_tilt,
         servo_pan_target, servo_tilt_target,
         servo_pan_motor, servo_tilt_motor);
}

void setServo_callback(cmd* c) {
  Command cmd(c);

  // Estos valores ya no son posición absoluta. Son dirección/intención del joystick.
  servo_tilt = constrain(cmd.getArg("t").getValue().toInt(), -100, 100);
  servo_pan  = constrain(cmd.getArg("p").getValue().toInt(), -100, 100);

  printf("OK ST servo_cmd_p:%d servo_cmd_t:%d servo_target_p:%d servo_target_t:%d servo_motor_p:%d servo_motor_t:%d step:%d\n",
         servo_pan, servo_tilt,
         servo_pan_target, servo_tilt_target,
         servo_pan_motor, servo_tilt_motor,
         servo_step);
}

void setServoSetup_callback(cmd* c) {
  Command cmd(c);

  int new_step = cmd.getArg("i").getValue().toInt();
  servo_step = constrain(new_step, 1, 10);

  printf("OK SS step:%d\n", servo_step);
}

void setServoCenter_callback(cmd* c) {
  (void)c;

  centerServos();

  printf("OK SC servo_center target_p:%d target_t:%d motor_p:%d motor_t:%d\n",
         servo_pan_target, servo_tilt_target,
         servo_pan_motor, servo_tilt_motor);
}

void setpower_callback(cmd* c) {
  Command cmd(c);

  powerON = constrain(cmd.getArg("p").getValue().toInt(), 0, 1);
  updatePowerMeasurements();
  applyPowerControl();

  printf("OK PW power:%d battery_dV:%d battery_v:%.1f battery_raw_dV:%d battery_raw_v:%.1f charger_dV:%d charger_v:%.1f charger_raw_dV:%d charger_raw_v:%.1f battery_relay:%d charger_relay:%d\n",
         powerON,
         voltsToDecivolts(batteryVoltage),
         batteryVoltage,
         voltsToDecivolts(batteryVoltageRaw),
         batteryVoltageRaw,
         voltsToDecivolts(chargerVoltage),
         chargerVoltage,
         voltsToDecivolts(chargerVoltageRaw),
         chargerVoltageRaw,
         batteryRelayConnected,
         chargerRelayConnected);
}

void getBatteryVoltage_callback(cmd* c) {
  (void)c;

  updatePowerMeasurements();
  applyPowerControl();

  // Respuesta intencionalmente simple para el receptor:
  // solo voltaje filtrado de batería, con un decimal.
  // Ejemplo: 12.3
  printf("%.1f\n", batteryVoltage);
}
