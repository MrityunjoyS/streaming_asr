#!/usr/bin/env python

import socket
import sys
import threading

import pyaudio

FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 8000
CHUNK = 512

RECORD_SECONDS = 100

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((sys.argv[1], int(sys.argv[2])))
audio = pyaudio.PyAudio()


def callback(data, frame_count, time_info, status):
    print('data :', data, threading.currentThread().getName())
    # s.send(data)
    return (None, pyaudio.paContinue)


stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
# stream.start_stream()

s.send(b'This is the header data')

for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
    data = stream.read(CHUNK)
    print('data...', i)
    s.send(data)

print("finished recording")

print('Shutting down')
# s.close()
stream.stop_stream()
stream.close()
audio.terminate()
