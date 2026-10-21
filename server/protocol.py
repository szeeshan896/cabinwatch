"""
Wire protocol shared by the server, the laptop agent and the phone camera page.

Everything travels over one WebSocket per camera. Text messages are JSON
control messages; binary messages carry media and are tagged by their first
byte so video, audio and file transfers can share the same socket.

Binary frames
-------------
  0x01  <jpeg bytes>                      video frame, camera -> server -> viewer
  0x02  <pcm16le mono bytes>              audio, either direction
  0x03  <xfer:uint32 be> <chunk bytes>    a slice of a recorded clip being
                                          pulled off the device
"""

import struct

BIN_VIDEO = 0x01
BIN_AUDIO = 0x02
BIN_CLIP = 0x03

# Two-way audio runs at this rate in both directions. 16 kHz mono PCM16 is
# ~32 KB/s - plenty for voice, and it needs no codec on either side, so the
# phone page and the Python agent can both handle it with no extra libraries.
AUDIO_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_BYTES = 2

# Audio is sent in chunks of this many milliseconds.
AUDIO_CHUNK_MS = 60
AUDIO_CHUNK_SAMPLES = AUDIO_RATE * AUDIO_CHUNK_MS // 1000


def pack_video(jpeg: bytes) -> bytes:
    return bytes([BIN_VIDEO]) + jpeg


def pack_audio(pcm: bytes) -> bytes:
    return bytes([BIN_AUDIO]) + pcm


def pack_clip(xfer_id: int, chunk: bytes) -> bytes:
    return bytes([BIN_CLIP]) + struct.pack(">I", xfer_id) + chunk


def unpack(data: bytes):
    """Returns (tag, payload, xfer_id). xfer_id is None unless tag is BIN_CLIP."""
    if not data:
        return None, b"", None
    tag = data[0]
    if tag == BIN_CLIP:
        if len(data) < 5:
            return tag, b"", None
        (xfer,) = struct.unpack(">I", data[1:5])
        return tag, data[5:], xfer
    return tag, data[1:], None
