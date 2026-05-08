# -*- coding: utf-8 -*-
"""
Servidor HTTP mínimo para recibir comandos de joystick y enviarlos por serial.
Autor: Ricardo Cervantes
Fecha: 2025
"""

from flask import Flask, request
import serial
import time

# =========================
# 🔧 CONFIGURACIÓN SERIAL
# =========================
SERIAL_PORT = '/dev/ttyTHS1'  # Cambia según tu hardware
BAUD_RATE = 115200

print("Iniciando puerto serial...")
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"✅ Puerto serial abierto en {SERIAL_PORT} ({BAUD_RATE} baudios)")
except serial.SerialException as e:
    print(f"❌ Error al abrir el puerto serial: {e}")
    ser = None

# =========================
# 🚀 SERVIDOR FLASK
# =========================
app = Flask(__name__)

# Variables globales
current_speed = 0
current_direction = 0
last_command_time = time.time()

def send_serial_command(speed, direction):
    """Envía el comando serial con formato RACH"""
    if not ser:
        print("⚠️ Puerto serial no disponible.")
        return

    # Modo 1: movimiento normal
    serial_command = f"sm -m 1 -v {speed} -d {direction}\r"
    try:
        ser.write(serial_command.encode() + b'\n')
        print(f"➡️ Enviado por serial: {serial_command.strip()}")
    except Exception as e:
        print(f"❌ Error al enviar por serial: {e}")

@app.route('/')
def home():
    return "RACH Robot HTTP Serial Controller activo", 200

@app.route('/move')
def move():
    """
    Recibe: /move?x=0.45&y=-0.75
    X controla el giro, Y la velocidad.
    """
    global current_speed, current_direction, last_command_time

    try:
        x = float(request.args.get("x", 0))
        y = float(request.args.get("y", 0))
    except ValueError:
        return "❌ Parámetros inválidos", 400

    # Escala a rango útil
    direction = int(x * 100)   # Giro
    speed = int(y * 100)       # Velocidad

    # Solo envía si hay cambio
    if direction != current_direction or speed != current_speed:
        current_direction = direction
        current_speed = speed
        send_serial_command(speed, direction)
        last_command_time = time.time()

    return f"OK: speed={speed}, direction={direction}", 200

@app.route('/stop')
def stop():
    """Detiene el robot"""
    global current_speed, current_direction
    current_speed = 0
    current_direction = 0
    send_serial_command(0, 0)
    return "Robot detenido", 200

# =========================
# 🔚 MAIN LOOP
# =========================
if __name__ == "__main__":
    try:
        print("🚀 Servidor iniciado en http://0.0.0.0:8080")
        app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n🛑 Interrupción detectada, cerrando...")
    finally:
        if ser:
            ser.close()
        print("Puerto serial cerrado correctamente.")
