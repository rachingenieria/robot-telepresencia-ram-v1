# -*- coding: utf-8 -*-

import sys
print(sys.executable)


import pyrealsense2 as rs
import numpy as np
import threading
import time
import cv2
from flask import Flask, Response, request
import paho.mqtt.client as mqtt
import serial
import time
import os



# Variables de estado del robot
current_speed = 0
current_direction = 0
current_mode_move = 1
current_can_move = True 
running = True
status_message = ""
status_message_color = (0, 255, 0)

status_color = (0, 0, 255)  # Rojo si hay un obstáculo

min_distance = 0
min_floor = 0
max_floor = 0

# Configuración del puerto serial
SERIAL_PORT = '/dev/ttyTHS1'
BAUD_RATE = 115200

print("Iniciando configuración del puerto serial RACH ROBOT ASISTENTE...")
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Puerto serial abierto en {SERIAL_PORT} a {BAUD_RATE} baudios.")
except serial.SerialException as e:
    print(f"Error al abrir el puerto serial: {e}")
    exit()

# ====== DETECCIÓN DE OBSTÁCULOS ======
class ObstacleDetector:
    def __init__(self):
        self.pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        self.pipeline.start(config)

        self.cframe = None  # Frame RGB para transmisión
        self.dframe = None  # Frame RGB para transmisión

        self.safety_threshold = 0.3
        self.max_distance = 2.0

        self.image_height = 480
        self.floor_y_end = self.image_height
        self.floor_y_start = self.floor_y_end - 100
        self.floor_x_center = 320
        self.floor_x_width = 400

        self.FLOOR_MIN_THRESHOLD = 0.1
        self.FLOOR_MAX_THRESHOLD = 1.3

        self.can_move_forward = True
        self.running = True
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def get_frames(self):
        try:
            frames = self.pipeline.wait_for_frames()
            depth_frame = frames.get_depth_frame()
            color_frame = frames.get_color_frame()

            if not depth_frame or not color_frame:
                return None, None

            depth_array = np.asanyarray(depth_frame.get_data()) * 0.001  # Convertir a metros
            image = np.asanyarray(color_frame.get_data())  # Imagen RGB

            return depth_array, image
        except RuntimeError as e:
            print(f"[ERROR] No se pudo obtener frame: {e}")
            return None, None
            
    

    def process_depth_frame(self, depth_array):
        """ Convierte la imagen de profundidad en una imagen visualizable. """

        #depth_normalized = cv2.normalize(depth_array, None, 0, 255, cv2.NORM_MINMAX)
        #depth_colormap = cv2.applyColorMap(cv2.convertScaleAbs(depth_normalized), cv2.COLORMAP_JET)


        #depth_colormap = cv2.applyColorMap(cv2.convertScaleAbs(depth_array, alpha=0.03), cv2.COLORMAP_JET)

        """ Convierte la imagen de profundidad en una imagen visualizable con rango ajustable. """
        min_depth = 0.2  # Cambia este valor para ajustar el mínimo (en metros)
        max_depth = 2.0  # Cambia este valor para ajustar el máximo (en metros)

        depth_clipped = np.clip(depth_array, min_depth, max_depth)
        depth_scaled = (255 - (depth_clipped - min_depth) / (max_depth - min_depth) * 255).astype(np.uint8)
        depth_colormap = cv2.applyColorMap(depth_scaled, cv2.COLORMAP_JET)

        return depth_colormap


    def detect_obstacles(self):
        depth_array, _ = self.get_frames()
        if depth_array is None:
            return self.max_distance * 1000 

        valid_values = depth_array[depth_array > 0]
        min_distance = np.min(valid_values) if valid_values.size > 0 else self.max_distance
        return min_distance * 1000

    def detect_floor(self):
        depth_array, _ = self.get_frames()
        if depth_array is None:
            return "SAFE", 0, 0

        x1 = self.floor_x_center - self.floor_x_width // 2
        x2 = self.floor_x_center + self.floor_x_width // 2
        floor_region = depth_array[self.floor_y_start:self.floor_y_end, x1:x2]
        valid_values = floor_region[floor_region > 0]

        if valid_values.size == 0:
            return "SAFE", 0, 0

        min_distance_piso = np.min(valid_values) * 1000
        max_distance_piso = np.max(valid_values) * 1000

        if min_distance_piso < self.FLOOR_MIN_THRESHOLD * 1000 or max_distance_piso > self.FLOOR_MAX_THRESHOLD * 1000:
            return "STOP", min_distance_piso, max_distance_piso
        return "SAFE", min_distance_piso, max_distance_piso

    def update(self):
        while self.running:
            min_distance = self.detect_obstacles()
            floor_status, min_distance_piso, max_distance_piso = self.detect_floor()
            self.can_move_forward = not (min_distance < self.safety_threshold * 1000 or floor_status == "STOP")
            

            # Obtener el frame RGB y guardarlo
            depth_frame, color_frame = self.get_frames()

            if color_frame is not None:
                _, jpeg = cv2.imencode('.jpg', color_frame)

            self.cframe = jpeg.tobytes()

            if depth_frame is not None:
                depth_colormap = self.process_depth_frame(depth_frame)  # Convertir a imagen visualizable
                _, jpeg = cv2.imencode('.jpg', depth_colormap)
           
            self.dframe = jpeg.tobytes()


        time.sleep(0.1)


    def can_move(self):
        return self.can_move_forward

    def stop(self):
        self.running = False
        self.thread.join()
        self.pipeline.stop()


# ====== INICIALIZACIÓN DEL DETECTOR DE OBSTÁCULOS ======
print("Iniciando detector de obstáculos (Intel RealSense)...")

try:
    obstacle_detector = ObstacleDetector()
    print("✅ Detector de obstáculos inicializado correctamente.")
except Exception as e:
    print(f"⚠️ No se pudo iniciar el detector de obstáculos: {e}")
    print("Continuando sin detección de obstáculos...")

    # Clase simulada (dummy) para reemplazar el detector
    class DummyObstacleDetector:
        def __init__(self):
            self.can_move_forward = True
            self.dframe = None
            self.cframe = None
        def detect_obstacles(self): return 2000  # 2m
        def detect_floor(self): return "SAFE", 1000, 1200
        def can_move(self): return True
        def stop(self): pass

    obstacle_detector = DummyObstacleDetector()

# ====== FUNCIÓN PARA CONTROL CENTRALIZADO DE MOTORES ======
def motor_control_loop():
    global status_color, current_can_move, current_speed, current_direction, current_mode_move, status_message ,min_distance , min_floor, max_floor
    last_speed = None
    last_direction = None
    last_mode = None
    pause_active = False  # Bandera para la pausa de seguridad

    print("Motor Control Init")
    
    while running:
        serial_command = None  # Inicializamos sin comando

        # Obtener datos de distancia desde el detector de obstáculos
        can_move = obstacle_detector.can_move()
        min_distance = obstacle_detector.detect_obstacles()  
        _, min_floor, max_floor = obstacle_detector.detect_floor()  


        if (current_can_move != can_move) or (current_speed != last_speed) or (current_direction != last_direction) or (current_mode_move != last_mode):

            print(f"[DEBUG] Estado cambiado - can_move: {can_move}, speed: {current_speed}, direction: {current_direction}, mode: {current_mode_move}")

            if can_move:  # Espacio Libre
                print("[INFO] Camino libre. Restaurando control total.")
                pause_active = False  # Desactivar la pausa
                status_message = "ESPACIO DESPEJADO"  # Mostrar en pantalla
                status_color = (0, 255, 0)  # Rojo si hay un obstáculo

                serial_command = f"sm -m {current_mode_move} -v {current_speed} -d {current_direction}\r"
                if serial_command:
                    ser.write(serial_command.encode() + b'\n')
                    print(f"Enviado comando serial (Motores): {serial_command}")

            else:  # Si hay obstáculo

                if not pause_active:  # Primera vez que se detecta
                    
                    current_speed = 0
                    pause_active = True  # Se activa la pausa de seguridad
                    status_message = "OBSTACULO DETECTADO"  # Mostrar en pantalla
                    status_color = (0, 0, 255)  # Rojo si hay un obstáculo
                    serial_command = f"sm -m 1 -v 0 -d 0\r"
                    if serial_command:
                        ser.write(serial_command.encode() + b'\n')
                        print(f"Enviado comando serial (Motores): {serial_command}")
                    print("[AUTO-STOP] Obstáculo detectado. Deteniendo motores.")
                    #time.sleep(2)  # Pausa obligatoria antes de permitir movimientos restringidos
                    print("[AUTO-STOP] Obstáculo detectado. Tiempo de seguridad")
                    serial_command = f"sm -m 1 -v 0 -d 0\r"
                    if serial_command:
                        ser.write(serial_command.encode() + b'\n')
                        print(f"Enviado comando serial (Motores): {serial_command}")


                else: 
                    speed = current_speed
                    if current_speed > 0:
                        speed = 0  # Bloqueamos avance

                    print("[INFO] PUEDE GIRAR O DAR REVERSA")
                    status_message = "PUEDE GIRAR O DAR REVERSA"  # Mostrar en pantalla
                    status_color = (0, 0, 255)  # Rojo si hay un obstáculo
                    serial_command = f"sm -m {current_mode_move} -v {speed} -d {current_direction}\r"
                    if serial_command:
                        ser.write(serial_command.encode() + b'\n')
                        print(f"Enviado comando serial (Motores): {serial_command}")

            last_speed = current_speed
            last_direction = current_direction
            last_mode = current_mode_move
            current_can_move = can_move

        time.sleep(0.2)  # Intervalo de chequeo

motor_thread = threading.Thread(target=motor_control_loop, daemon=True)
motor_thread.start()

# Configuración de la cámara
camera_index = 4
print("Iniciando configuración de la cámara...")
cap = cv2.VideoCapture(camera_index)
if not cap.isOpened():
    print(f"Error: No se pudo abrir la cámara en /dev/video{camera_index}")
    exit()
print(f"Cámara abierta correctamente en /dev/video{camera_index}.")

# Crear la aplicación Flask
app = Flask(__name__)
# ✅ Variable global para seleccionar la cámara activa
selected_camera = "web"  # Opciones: "web", "rgb", "depth"
print("Aplicación Flask creada.")

# Función para capturar y transmitir video con información de distancia
def generate_video_stream():
    global status_color, current_can_move, status_message ,min_distance , min_floor, max_floor
    print("Iniciando transmisión de video...")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Error: No se pudo leer el cuadro de la cámara.")
            break

        # Redimensionar y rotar la imagen
        target_width = 320
        target_height = 240
        resized_frame = cv2.resize(frame, (target_width, target_height))
        rotated_frame = cv2.rotate(resized_frame, cv2.ROTATE_180)

        # Agregar texto en la imagen con distancias
        overlay_text = [
            f"Distancia Min: {min_distance:.0f} mm",
            f"Piso Min: {min_floor:.0f} mm",
            f"Piso Max: {max_floor:.0f} mm"
        ]

        # Dibujar los textos en la imagen
        for i, text in enumerate(overlay_text):
            cv2.putText(rotated_frame, text, (10, 30 + i * 30), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 0, 0), 1)

        # Dibujar mensaje de alerta
        cv2.putText(rotated_frame, status_message, (10, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.4, status_color, 1)

        # Convertir la imagen a formato JPEG para transmisión
        _, buffer = cv2.imencode('.jpg', rotated_frame)
        frame_data = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n')

def generateDepth():
        while True:
            frame = obstacle_detector.dframe
            if frame:
                yield (b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n')

# Ruta para la transmisión de video en Flask. """
def generateRGB():
        while True:
            frame = obstacle_detector.cframe
            if frame:
                yield (b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n')


@app.route('/set_camera')
def set_camera():
    global selected_camera
    cam_type = request.args.get("cam", "web")  # Obtiene el parámetro "cam"

    if cam_type in ["web", "rgb", "depth"]:
        selected_camera = cam_type  # Se actualiza la cámara activa
        print(f"✅ Cámara cambiada a: {selected_camera}")
        return f"Cámara cambiada a {selected_camera}, Presionar REFRESH", 200
    else:
        return "❌ Cámara no válida. Usa ?cam=web, ?cam=rgb o ?cam=depth", 400

# Endpoint para la transmisión de video
@app.route('/video_feed')
def video_feed():
    print(f"🔴 Transmitiendo: {selected_camera}")

    if selected_camera == "web":
        return Response(generate_video_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')
    elif selected_camera == "rgb":
        return Response(generateRGB(), mimetype='multipart/x-mixed-replace; boundary=frame')
    elif selected_camera == "depth":
        return Response(generateDepth(), mimetype='multipart/x-mixed-replace; boundary=frame')
    else:
        return "Error: Cámara no válida", 400

# Función para procesar comandos de motores
def process_motor_command(command):
    global current_speed, current_direction, current_mode_move

    try:
        current_speed = -40 * (int(command[2]) - 5)
        current_direction = 40 * (int(command[1]) - 5)
        current_mode_move = 1
        print(f"[INFO] Nueva velocidad: {current_speed}, Dirección: {current_direction}")

    except (IndexError, ValueError) as e:
        print(f"Error procesando comando Xxy: {e}")

def process_special_command(command):
    global current_speed, current_direction, current_mode_move

    try:
        current_speed = -40 * (int(command[2]) - 5)
        current_direction = 40 * (int(command[1]) - 5)
        current_mode_move = 2

        print(f"[INFO] Nueva velocidad: {current_speed}, Dirección: {current_direction}")
    except (IndexError, ValueError) as e:
        print(f"Error procesando comando Zxy: {e}")

def process_tilt_pan_command(command):
    print(f"Procesando comando tilt/pan: {command}")
    try:
        tilt = 20 * (int(command[2]) - 5)
        pan = -20 * (int(command[1]) - 5)
        serial_command = f"st -t {tilt} -p {pan}\r"
        ser.write(serial_command.encode() + b'\n')
        print(f"Enviado comando serial (Tilt/Pan): {serial_command}")
    except (IndexError, ValueError) as e:
        print(f"Error procesando comando Yxy: {e}")



# Funciones MQTT
def on_connect(client, userdata, flags, rc):
    print("Conectado al broker MQTT con código de resultado " + str(rc))
    client.subscribe("robots/clarissa")

def on_message(client, userdata, msg):
    command = msg.payload.decode().strip()
    print(f"Comando recibido por MQTT: {command}")
    if command.startswith("X"):
        process_motor_command(command)
    elif command.startswith("Y"):
        process_tilt_pan_command(command)
    elif command.startswith("Z"):
        process_special_command(command)
    else:
        print(f"Comando desconocido: {command}")
        client.publish("robots/clarissa/error", f"Comando no reconocido: {command}")

# Configuración MQTT
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
MQTT_PORT = 1883

try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    #print(f"Conectado al broker MQTT en {MQTT_BROKER}:{MQTT_PORT}.")
except Exception as e:
    print(f"Error conectando al broker MQTT: {e}")
    ser.close()
    exit()

# Iniciar Flask
if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("Interrupción por teclado detectada. Cerrando...")
    finally:
        print("Deteniendo cliente MQTT y liberando recursos.")
        client.loop_stop()
        client.disconnect()
        ser.close()
        cap.release()
        obstacle_detector.stop()
