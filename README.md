# ROBOT Telepresencia RAM V1 by rachingenieria

Repositorio principal del robot de telepresencia **RAM**, construido sobre **Jetson Nano + ESP32**, con video, audio, control remoto, asistente local/cloud y pantalla expresiva para interacción familiar y telepresencia.

Este árbol está pensado para dos cosas:

1. mantener el sistema operativo del robot organizado
2. poder **recuperar el sistema más adelante** si hace falta reconstruirlo o moverlo a otra Jetson

## Resumen

RAM es un robot de telepresencia que:

- usa una **Jetson Nano** como cerebro
- usa una **ESP32** como corazón/controlador de bajo nivel
- transmite video y audio por **WebRTC**
- permite control remoto de base y cabeza desde celular o PC
- incluye una **IA local/remota** para conversación
- vive en **Bogotá, Colombia**
- convive con la **familia Cervantes Hernandez**

## Componentes principales

### 1. Telepresence

Ruta:

```text
telepresence/
```

Incluye:

- servidor WebRTC con `aiohttp + aiortc`
- interfaz del operador
- pantalla del robot (`/robot-display`)
- bridge serial hacia la ESP32
- asistente inactivo/idle assistant integrado

Funciones principales:

- video robot -> operador
- audio operador -> robot
- control base por joystick
- control cabeza por joystick
- modo asistente local cuando no hay operador conectado

### 2. Be More Agent

Ruta:

```text
be-more-agent/
```

Incluye:

- lógica base del asistente
- STT
- LLM
- TTS
- memoria
- perfil local del robot

Actualmente RAM puede usar proveedores cloud como OpenAI para mejorar velocidad de:

- transcripción
- respuesta conversacional

### 3. Firmware ESP32

Ruta:

```text
ESP32CONTROL/
```

Archivo principal actual:

```text
ESP32CONTROL/ASISITENTE_JTV1.ino
```

Este firmware controla:

- motores omnidireccionales
- pan / tilt de cabeza o pantalla
- power de batería

### 4. Referencias de control anteriores

Ruta:

```text
control/
```

Se conserva como referencia histórica para evolución del robot.

## Estructura del repositorio

```text
robot/
├── README.md
├── .gitignore
├── telepresence/
│   ├── server.py
│   ├── robot_bridge.py
│   ├── idle_assistant.py
│   ├── start_telepresence.sh
│   ├── start_telepresence_desktop.sh
│   ├── start_robot_display.sh
│   ├── certs/
│   └── web/
├── be-more-agent/
│   ├── agent.py
│   ├── config.jetson.json
│   ├── robot_profile.json
│   ├── memory.json
│   ├── .env
│   ├── setup.sh
│   └── whisper.cpp/
├── ESP32CONTROL/
│   ├── ASISITENTE_JTV1.ino
│   ├── CLI.cpp
│   ├── CLI.h
│   ├── motor.cpp
│   └── motor.h
└── control/
```

## Hardware y roles

### Jetson Nano

Responsable de:

- servidor de telepresencia
- WebRTC
- cámaras
- audio del operador
- interfaz web
- lógica de IA

### ESP32

Responsable de:

- motores
- servos de cabeza
- lógica de movimiento de bajo nivel
- comandos seriales

### Cámaras

Asignación actual esperada:

- frontal: `/dev/video1`
- inferior: `/dev/video0`

En la interfaz del operador existe un botón `Swap Cameras` para corregir cambios de orden tras reinicios.

### Puerto serial ESP32

Puerto actual:

```text
/dev/ttyTHS1
```

## Comandos seriales importantes

### Base / motores

Se mantienen sin cambio:

```text
sm -m <modo> -v <velocidad> -d <direccion>
```

### Cabeza / joystick

Se mantiene sin cambio:

```text
st -t <tilt> -p <pan>
```

### Centrar cabeza

Comando actual:

```text
sc
```

### Power

```text
pw -p 1
pw -p 0
```

## Telepresencia

### Arranque principal

Desde terminal:

```bash
cd /home/jetson/robot/telepresence
IDLE_ASSISTANT=1 ./start_telepresence.sh
```

### Acceso directo de escritorio

El acceso directo del escritorio ya está configurado para arrancar por defecto con IA.

### URLs importantes

Operador:

```text
https://<IP-ZEROTIER-JETSON>:8088/
```

Pantalla del robot:

```text
https://127.0.0.1:8088/robot-display
```

Setup:

```text
https://127.0.0.1:8088/setup
```

Estado:

```text
https://127.0.0.1:8088/api/status
```

## IA y contexto local

RAM tiene una ficha local en:

```text
be-more-agent/robot_profile.json
```

Allí se guarda información como:

- nombre del robot
- ciudad
- familia
- cómo está construido
- contexto local permanente

Esto permite que RAM responda mejor preguntas como:

- cómo te llamas
- dónde vives
- con quién vives
- cómo estás construido

### API key

La clave del proveedor cloud no debe guardarse en el JSON de configuración.

Se guarda en:

```text
be-more-agent/.env
```

Ejemplo:

```bash
OPENAI_API_KEY=tu_api_key
```

## Recuperación del sistema

Si más adelante necesitas reconstruir el robot, este repo debería conservar:

- código Python
- firmware ESP32
- scripts de arranque
- configuración
- identidad/contexto del robot

### Checklist de respaldo recomendable

Conservar además:

- este repositorio completo
- certificados HTTPS de `telepresence/certs/`
- `.env` del agente
- `config.jetson.json`
- `robot_profile.json`
- cualquier voice model o assets personalizados no públicos

### Lo que conviene documentar fuera del repo

- usuario y contraseña de la Jetson
- red ZeroTier
- credenciales de API
- mapeo físico de puertos USB/cámaras
- versión de Ubuntu / JetPack

## Git y publicación

Este repositorio puede versionarse localmente y luego subirse a GitHub.

Perfil destino:

```text
https://github.com/rachingenieria
```

Cuando quieras publicarlo, un flujo típico sería:

```bash
cd /home/jetson/robot
git init
git add .
git commit -m "Initial commit: ROBOT Telepresencia RAM V1"
git branch -M main
git remote add origin https://github.com/rachingenieria/robot-telepresencia-ram-v1.git
git push -u origin main
```

## Estado actual del proyecto

Hoy la base estable del sistema es:

- telepresencia funcional
- control de base funcional
- control de cabeza funcional
- asistente con transcripción rápida
- respuesta textual y voz del robot funcional
- pantalla del robot con cara animada por estado

## Autor

**rachingenieria**

Proyecto:

**ROBOT Telepresencia RAM V1**
