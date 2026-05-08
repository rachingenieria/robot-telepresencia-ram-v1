# Telepresence Stack

Primera base de telepresencia para el robot con Jetson Nano.

## Qué incluye

- WebRTC entre celular y Jetson
- dos videos saliendo desde la Jetson:
  - frontal: `/dev/video1`
  - inferior: `/dev/video0`
- canal de control por `DataChannel`
- bridge serial al ESP32 actual usando los mismos comandos:
  - `sm -m <modo> -v <vel> -d <dif>`
  - `st -t <tilt> -p <pan>`
  - `sc`
  - `pw -p <valor>`
  - `b`
- página separada para el monitor del robot:
  - `/robot-display`

## Arranque

```bash
cd /home/jetson/robot/telepresence
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py --host 0.0.0.0 --port 8088 --serial-port /dev/ttyTHS1 --front-camera /dev/video1 --down-camera /dev/video0 --https --cert-file certs/cert.pem --key-file certs/key.pem
```

O usando el script:

```bash
cd /home/jetson/robot/telepresence
chmod +x start_telepresence.sh start_robot_display.sh
./start_telepresence.sh
```

Para dejar disponible el asistente local cuando no hay operador conectado:

```bash
cd /home/jetson/robot/telepresence
IDLE_ASSISTANT=1 ./start_telepresence.sh
```

El asistente queda separado del flujo de telepresencia. Se pausa cuando entra un operador y vuelve a escuchar cuando el operador sale. Usa los assets locales de `be-more-agent`: `whisper.cpp` para transcribir, Ollama para responder y Piper para hablar.

La pantalla del robot muestra un botón `AI` para iniciar/pausar el asistente y un enlace `Setup` para revisar estado. Si el servidor no se arrancó con `IDLE_ASSISTANT=1`, el botón aparece deshabilitado y la telepresencia sigue funcionando normal. Para que la IA arranque escuchando automáticamente:

```bash
IDLE_ASSISTANT=1 IDLE_ASSISTANT_AUTOSTART=1 ./start_telepresence.sh
```

## URLs

- operador/celular: `https://<IP-ZEROTIER-JETSON>:8088/`
- monitor del robot: `https://127.0.0.1:8088/robot-display`
- setup local: `https://127.0.0.1:8088/setup`
- estado/API local: `https://127.0.0.1:8088/api/status`

Si arrancas con `start_telepresence.sh` y existen `certs/cert.pem` y `certs/key.pem`, el servidor usa HTTPS automáticamente. Para forzar HTTP solo para diagnóstico:

```bash
TELEPRESENCE_HTTP=1 ./start_telepresence.sh
```

Un error como `SSL_ERROR_RX_RECORD_TOO_LONG` significa que el navegador intentó HTTPS contra un servidor HTTP. Un error como `Invalid method encountered: b'\x16\x03\x01'` significa lo contrario visto desde el servidor.

Para el monitor local en modo kiosco:

```bash
cd /home/jetson/robot/telepresence
./start_robot_display.sh
```

## Notas

- Esta primera versión deja listo el flujo WebRTC, el bridge serial y la UI móvil.
- El control ya es compatible con el firmware actual del ESP32.
- El siguiente salto natural es mover la captura/encode hacia un pipeline GStreamer/NVENC específico para Jetson.
- El operador entra por defecto en modo `audio-only` para no cargar la Jetson con video ascendente.
- Si más adelante quieres probar video desde el celular al robot, abre el operador con `?video=1`, por ejemplo:
  - `https://<IP-ZEROTIER-JETSON>:8088/?video=1`
