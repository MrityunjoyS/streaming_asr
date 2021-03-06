import os
import re
import socket
import sys
import threading
import time
# import thread module
from _thread import start_new_thread

from google.cloud import speech
from six.moves import queue

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/home/bikram/Speech/ASR/streaming_asr/google_speech.json"

# Audio recording parameters
STREAMING_LIMIT = 240000  # 4 minutes
SAMPLE_RATE = 8000
CHUNK_SIZE = int(SAMPLE_RATE / 10)  # 100ms
PORT = 12345


def get_current_time():
    """Return Current Time in MS."""

    return int(round(time.time() * 1000))


class NetworkAudioStream:
    """Opens a recording stream as a generator yielding the audio chunks."""

    def __init__(self, rate, chunk_size, c):
        self._rate = rate
        self.chunk_size = chunk_size
        self._num_channels = 1
        self._buff = queue.Queue()
        self.closed = True
        self.start_time = get_current_time()
        self.restart_counter = 0
        self.audio_input = []
        self.last_audio_input = []
        self.result_end_time = 0
        self.is_final_end_time = 0
        self.final_request_end_time = 0
        self.bridging_offset = 0
        self.last_transcript_was_final = False
        self.new_stream = True

    def __enter__(self):

        self.closed = False
        return self

    def __exit__(self, type, value, traceback):

        self.closed = True
        # Signal the generator to terminate so that the client's
        # streaming_recognize method will not block the process termination.
        self._buff.put(None)

    def fill_buffer(self, in_data):
        """Continuously collect data from the audio stream, into the buffer."""
        print('Filler Thread', threading.currentThread().getName())

        self._buff.put(in_data)
        # return None, pyaudio.paContinue

    def generator(self):
        """Stream Audio from microphone to API and to local buffer"""

        while not self.closed:
            print('Generator Thread', threading.currentThread().getName())
            data = []

            if self.new_stream and self.last_audio_input:

                chunk_time = STREAMING_LIMIT / len(self.last_audio_input)

                if chunk_time != 0:

                    if self.bridging_offset < 0:
                        self.bridging_offset = 0

                    if self.bridging_offset > self.final_request_end_time:
                        self.bridging_offset = self.final_request_end_time

                    chunks_from_ms = round((self.final_request_end_time -
                                            self.bridging_offset) / chunk_time)

                    self.bridging_offset = (round((
                                                          len(self.last_audio_input) - chunks_from_ms)
                                                  * chunk_time))

                    for i in range(chunks_from_ms, len(self.last_audio_input)):
                        data.append(self.last_audio_input[i])

                self.new_stream = False

            # Use a blocking get() to ensure there's at least one chunk of
            # data, and stop iteration if the chunk is None, indicating the
            # end of the audio stream.
            chunk = self._buff.get()
            self.audio_input.append(chunk)

            if chunk is None:
                return
            data.append(chunk)
            # Now consume whatever other data's still buffered.
            while True:
                try:
                    chunk = self._buff.get(block=False)

                    if chunk is None:
                        return
                    data.append(chunk)
                    self.audio_input.append(chunk)

                except queue.Empty:
                    break

            print('Generator Thread-yield', threading.currentThread().getName())
            yield b''.join(data)


def listen_print_loop(responses, stream):
    """Iterates through server responses and prints them.
    The responses passed is a generator that will block until a response
    is provided by the server.
    Each response may contain multiple results, and each result may contain
    multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
    print only the transcription for the top alternative of the top result.
    In this case, responses are provided for interim results as well. If the
    response is an interim one, print a line feed at the end of it, to allow
    the next result to overwrite it, until the response is a final one. For the
    final one, print a newline to preserve the finalized transcription.
    """

    try:
        for response in responses:

            print('Response Thread', threading.currentThread().getName())

            if get_current_time() - stream.start_time > STREAMING_LIMIT:
                stream.start_time = get_current_time()
                break

            if not response.results:
                continue

            result = response.results[0]

            if not result.alternatives:
                continue

            transcript = result.alternatives[0].transcript

            result_seconds = 0
            result_micros = 0

            if result.result_end_time.seconds:
                result_seconds = result.result_end_time.seconds

            if result.result_end_time.microseconds:
                result_micros = result.result_end_time.microseconds

            stream.result_end_time = int((result_seconds * 1000) + (result_micros / 1000))

            corrected_time = (stream.result_end_time - stream.bridging_offset
                              + (STREAMING_LIMIT * stream.restart_counter))
            # Display interim results, but with a carriage return at the end of the
            # line, so subsequent lines will overwrite them.

            if result.is_final:

                print('Final-'- + str(corrected_time) + ': ' + transcript + '\n')

                stream.is_final_end_time = stream.result_end_time
                stream.last_transcript_was_final = True

                # Exit recognition if any of the transcribed phrases could be
                # one of our keywords.
                if re.search(r'\b(exit|quit)\b', transcript, re.I):
                    print('Exiting...\n')
                    stream.closed = True
                    break

            else:
                print('Interm Result', result)
                stream.last_transcript_was_final = False
    except:
        print("Closing the stream - error")
        stream.closed = True


def read_network_stream(c, stream):
    while not stream.closed:
        data = c.recv(1024)  # Dummy Thread
        if data:
            stream.fill_buffer(data)


def socket_stream(c):
    with NetworkAudioStream(SAMPLE_RATE, CHUNK_SIZE, c) as stream:

        data = c.recv(1024)  # Dummy Thread
        print('Headers', data, len(data), threading.currentThread().getName())

        client = speech.SpeechClient()
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=SAMPLE_RATE,
            language_code='en-IN',
            max_alternatives=1)
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True)

        # Start data receiving thread to fill the buffer
        start_new_thread(read_network_stream, (c, stream,))

        while not stream.closed:
            stream.audio_input = []
            audio_generator = stream.generator()

            requests = (speech.StreamingRecognizeRequest(
                audio_content=content) for content in audio_generator)

            responses = client.streaming_recognize(streaming_config,
                                                   requests)
            # Now, put the transcription responses to use.
            listen_print_loop(responses, stream)

            if stream.result_end_time > 0:
                stream.final_request_end_time = stream.is_final_end_time
            stream.result_end_time = 0
            stream.last_audio_input = []
            stream.last_audio_input = stream.audio_input
            stream.audio_input = []
            stream.restart_counter = stream.restart_counter + 1

            if not stream.last_transcript_was_final:
                sys.stdout.write('final-\n')
            stream.new_stream = True
    c.close()


def main():
    host = ""
    # reverse a port on your computer
    # in our case it is 12345 but it
    # can be anything
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, PORT))
    print("socket binded to port", PORT)

    # put the socket into listening mode
    s.listen(2)
    print("socket is listening")

    # a forever loop until client wants to exit
    while True:
        # establish connection with client
        c, adder = s.accept()
        print('Connected to :', adder[0], ':', adder[1], threading.currentThread().getName())
        # Start a new thread and return its identifier
        start_new_thread(socket_stream, (c,))
    s.close()


if __name__ == '__main__':
    main()
