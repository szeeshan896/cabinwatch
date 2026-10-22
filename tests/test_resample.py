#!/usr/bin/env python3
"""
Checks that changing the microphone's sample rate does not wreck the sound.

Most devices refuse 16 kHz, so most of the time the audio is converted. Doing
that naively - taking every Nth sample - folds everything above the new
Nyquist back into the audible band, which is what made speech sound harsh and
metallic and put energy in the voice band that nobody actually made.

    python tests/test_resample.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "agent"))
from resample import Resampler          # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))


def stream(src, dst, signal, block=1024):
    r = Resampler(src, dst)
    return np.concatenate([r.process(signal[i:i + block])
                           for i in range(0, len(signal), block)])


def tone(rate, hz, seconds, amp=0.5):
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * hz * t) * amp).astype("float32")


def peak_hz(x, rate):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1.0 / rate)[int(np.argmax(spec))])


def main():
    # --- a voice-range tone has to come through untouched -----------------
    for src in (48000, 44100, 32000, 22050):
        y = stream(src, 16000, tone(src, 440, 1.0))
        got = peak_hz(y, 16000)
        rms = float(np.sqrt((y ** 2).mean()))
        seconds = len(y) / 16000
        check(f"{src} Hz -> 16 kHz keeps a 440 Hz tone",
              abs(got - 440) < 8 and abs(rms - 0.354) < 0.02
              and abs(seconds - 1.0) < 0.02,
              f"{got:.0f} Hz, rms {rms:.3f}, {seconds:.2f}s")

    # --- and content above the new Nyquist must not fold down -------------
    y = stream(48000, 16000, tone(48000, 12000, 1.0))
    rms = float(np.sqrt((y ** 2).mean()))
    check("a 12 kHz tone is removed, not folded to 4 kHz",
          rms < 0.02, f"rms {rms:.5f} (input was 0.354)")

    # --- the realistic case: speech plus hiss -----------------------------
    src, dst = 48000, 16000
    t = np.arange(src) / src
    voice = sum(np.sin(2 * np.pi * f * t) for f in (300, 700, 1500)) * 0.12
    hiss = np.sin(2 * np.pi * 13000 * t) * 0.30
    y = stream(src, dst, (voice + hiss).astype("float32"))
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    freqs = np.fft.rfftfreq(len(y), 1.0 / dst)

    def band(lo, hi):
        return float(spec[(freqs >= lo) & (freqs < hi)].sum())

    ratio = band(2500, 5000) / band(250, 2500)
    check("hiss above the new Nyquist does not land on top of speech",
          ratio < 1e-3, f"ratio {ratio:.2e} (naive decimation gives about 1e+1)")

    # --- no click where one block ends and the next begins ----------------
    for hz, amp in ((440, 0.5), (1000, 0.4), (2000, 0.3)):
        y = stream(48000, 16000, tone(48000, hz, 0.5, amp))
        natural = amp * 2 * np.pi * hz / 16000      # that tone's own max slope
        worst = float(np.abs(np.diff(y)).max())
        check(f"no click between blocks at {hz} Hz",
              worst <= natural * 1.15,
              f"jump {worst:.5f} vs natural step {natural:.5f}")

    # --- same rate in and out should be left completely alone -------------
    x = tone(16000, 440, 0.2)
    y = stream(16000, 16000, x)
    check("no conversion when the rate already matches",
          len(y) == len(x) and np.allclose(y, x))

    # --- odd block sizes must not lose or duplicate audio -----------------
    for block in (160, 777, 4096):
        y = stream(48000, 16000, tone(48000, 440, 1.0), block=block)
        check(f"block size {block} still yields one second of audio",
              abs(len(y) / 16000 - 1.0) < 0.02, f"{len(y) / 16000:.3f}s")

    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
