#!/bin/bash
# Script: run_arducam.sh
# Purpose: Automates the execution of Arducam with a virtual environment in Ubuntu.

# Navigate to the working directory
cd ~/robot || { echo "Error: Directory not found"; exit 1; }

sudo modprobe v4l2loopback video_nr=3 card_label="MergedCam" exclusive_caps=1


# --- Duplicar /dev/video1 a /dev/video4 ---
echo "[INFO] Duplicando /dev/video1 a /dev/video4..."
#nohup ffmpeg -f v4l2 -i /dev/video1 -f v4l2 /dev/video4 > /home/jetson/robot/video_clone.log 2>&1 &

# --- Combinar video0 y video1 y enviar a video3 ---
echo "[INFO] Fusionando /dev/video0 y /dev/video1 en /dev/video3..."
nohup ffmpeg \
  -f v4l2 -input_format mjpeg -i /dev/video0 \
  -f v4l2 -input_format mjpeg -i /dev/video1 \
  -filter_complex "hstack=inputs=2,format=yuv420p,scale=1280:-1" \
  -pix_fmt yuv420p -f v4l2 /dev/video3 > /home/jetson/robot/video_merge.log 2>&1 &
       
# Activate the virtual environment
source new_env/bin/activate || { echo "Error: Failed to activate virtual environment"; exit 1; }

#python rachrobotasisitentev1.py
python robot_http_serial.py
