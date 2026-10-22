"""
Changing the microphone's sample rate without wrecking the sound.

Most devices will not give us 16 kHz, so what they do give has to be
converted. Doing that by simply taking every third sample is the obvious
approach and it is wrong: everything above half the new rate folds back down
into the audible band as a ghost of itself. A 12 kHz hiss arrives as a 4 kHz
whine, speech turns harsh and metallic, and the cry detector sees energy in
the voice band that nobody actually made.

So the signal is low-pass filtered first, then read at the new rate. The
filter and the read position both carry across blocks, because resetting
either one every block puts a click at every boundary.
"""

import numpy as np


def design_lowpass(cutoff_hz, rate, taps=65):
    """A windowed-sinc low-pass. Odd tap count keeps it symmetric."""
    if taps % 2 == 0:
        taps += 1
    n = np.arange(taps) - (taps - 1) / 2.0
    fc = float(cutoff_hz) / float(rate)
    h = 2.0 * fc * np.sinc(2.0 * fc * n)
    h *= np.hamming(taps)
    total = h.sum()
    if total != 0:
        h /= total
    return h.astype(np.float32)


class Resampler:
    """Converts a stream from one rate to another, block by block."""

    def __init__(self, src_rate, dst_rate, taps=65):
        self.src = int(src_rate)
        self.dst = int(dst_rate)
        self.step = self.src / float(self.dst)

        # Only needed when going down; going up cannot alias.
        self.h = None
        if self.dst < self.src:
            # A little under half the target rate, to leave the filter room
            # to roll off before anything can fold.
            self.h = design_lowpass(0.45 * self.dst, self.src, taps)

        self._hist = np.zeros(0, dtype=np.float32)   # filter overlap
        self._buf = np.zeros(0, dtype=np.float32)    # filtered, not yet read
        self._pos = 0.0                              # read position in _buf

    @property
    def passthrough(self):
        return self.src == self.dst

    def process(self, block):
        """Feed samples at the source rate, get samples at the target rate."""
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        if self.passthrough or x.size == 0:
            return x

        if self.h is not None:
            buf = np.concatenate([self._hist, x])
            if buf.size < self.h.size:
                self._hist = buf
                return np.zeros(0, dtype=np.float32)
            filtered = np.convolve(buf, self.h, mode="valid")
            # Keep the tail the next convolution needs to continue smoothly.
            self._hist = buf[filtered.size:]
        else:
            filtered = x

        self._buf = np.concatenate([self._buf, filtered])
        if self._buf.size < 2:
            return np.zeros(0, dtype=np.float32)

        last_index = self._buf.size - 1
        n_out = int(np.floor((last_index - self._pos) / self.step)) + 1
        if n_out <= 0:
            return np.zeros(0, dtype=np.float32)

        idx = self._pos + self.step * np.arange(n_out, dtype=np.float64)
        out = np.interp(idx, np.arange(self._buf.size, dtype=np.float64),
                        self._buf.astype(np.float64)).astype(np.float32)

        # Carry the fractional position so there is no seam next block.
        next_pos = idx[-1] + self.step
        drop = int(np.floor(next_pos))
        drop = min(drop, self._buf.size)
        self._buf = self._buf[drop:]
        self._pos = next_pos - drop
        return out
