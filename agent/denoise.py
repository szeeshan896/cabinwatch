"""
Taking the fan out, so the baby can be heard.

A cooler or a ceiling fan is loud, and it never stops. That breaks this in
two separate ways. Live, it buries the child - you listen in and hear a
motor. And for alerts it is worse than it sounds: the detector learns the
room's noise level, the fan lifts that level, and a cry then has to be far
louder than it should to count. A quiet grumble never registers at all.

What makes it removable is that a fan is *steady*. Its sound sits in the
same frequency bins minute after minute, while a voice moves around and
comes and goes. So the steady part of each frequency is measured and
subtracted, and what is left is whatever is not the fan.

Measuring the steady part takes two steps, and the second one is there
because of a real hole in the first version of this.

The level is only measured from frames that sound like the room on its own,
otherwise a baby crying for a minute gets learned as part of the fan and
then subtracted away. But "the room on its own" was decided by comparing
against the level already measured - so when the cooler was switched on
*after* this started, every frame looked too loud to learn from, the
profile stayed at the quiet it began with, and the filter sat there doing
nothing about the cooler. Which is precisely when it is wanted.

So the quietest each frequency has been over the last second is tracked
separately. That follows a cooler being switched on within a second, with
no opinion about what is noise, and the "is this just the room" question is
then asked against *that*. The subtracted profile is still an average of
noise-only frames, because an average is what a noise actually sits at -
its quietest moment sits well below that, and subtracting the quietest
moment would leave most of the fan behind.

The usual trap is handled too. Subtracting too hard leaves musical noise -
little tones warbling in the gaps - so a floor is kept under each bin
rather than taking it to zero.
"""

from collections import deque

import numpy as np


class NoiseSuppressor:
    """Removes the steady part of a sound, keeping what changes.

    Feed it blocks of mono float samples; it returns blocks of the same
    length, delayed by one frame. The delay is inherent - a frequency cannot
    be measured without hearing a frame of it first.
    """

    def __init__(self, rate=16000, frame=512, hop=256,
                 strength=2.0, floor=0.08, learn_seconds=1.5):
        self.rate = int(rate)
        self.frame = int(frame)
        self.hop = int(hop)
        # How much of the measured noise to take out. Above about 2.5 the
        # gaps start to warble; below 1.5 the fan is still obvious.
        self.strength = float(strength)
        # Never take a bin below this fraction of itself. This is what stops
        # the silences sounding like bubbling water.
        self.floor = float(floor)

        self.window = np.hanning(self.frame + 1)[:-1].astype(np.float32)

        # How fast the profile follows a change in the room.
        frames_per_second = self.rate / self.hop
        self._alpha = float(np.exp(-1.0 / (learn_seconds * frames_per_second)))

        # The quietest level lately, tracked over two half-second stretches
        # so that it follows the room up as well as down.
        self._stretch = max(1, int(round(0.5 * frames_per_second)))
        self._mins = deque(maxlen=2)
        self._cur_min = None
        self._in_stretch = 0
        self._floor = None          # quietest lately, per frequency

        self._noise = None          # measured steady level, per frequency
        self._seen = 0              # frames used to build it so far

        self._in = np.zeros(0, dtype=np.float32)     # waiting to be framed
        self._out = np.zeros(0, dtype=np.float32)    # frames added together
        self._norm = np.zeros(0, dtype=np.float32)   # windows added together
        self._write = 0             # where the next frame starts in _out

        # What the last block looked like, for whoever wants to show it.
        self.noise_level = 0.0
        self.voice_level = 0.0

    @property
    def ready(self):
        """True once there is enough of a profile to subtract."""
        return self._seen >= 8

    def reset(self):
        self._noise = None
        self._seen = 0
        self._mins.clear()
        self._cur_min = None
        self._in_stretch = 0
        self._floor = None
        self._in = np.zeros(0, dtype=np.float32)
        self._out = np.zeros(0, dtype=np.float32)
        self._norm = np.zeros(0, dtype=np.float32)
        self._write = 0

    def process(self, block):
        """Returns the same number of samples, with the steady part removed."""
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return x

        self._in = np.concatenate([self._in, x])
        while self._in.size >= self.frame:
            self._step()

        want = x.size
        # A sample is finished once every frame that overlaps it has been
        # added, which is true for everything before the next frame's start.
        if self._write < want:
            return np.zeros(want, dtype=np.float32)   # still filling up

        norm = np.maximum(self._norm[:want], 1e-8)
        out = (self._out[:want] / norm).astype(np.float32)
        self._out = self._out[want:]
        self._norm = self._norm[want:]
        self._write -= want
        return out

    def _step(self):
        frame = self._in[:self.frame] * self.window
        self._in = self._in[self.hop:]

        spectrum = np.fft.rfft(frame)
        magnitude = np.abs(spectrum)

        self._track_floor(magnitude)
        if self._noise is None or self._room_changed():
            self._noise = magnitude.copy()
            self._seen = 1
        elif self._is_noise_only(magnitude):
            # Only learn from frames that sound like the room alone. Learning
            # during a cry would teach it that the cry is part of the fan.
            self._noise = (self._alpha * self._noise
                           + (1.0 - self._alpha) * magnitude)
            self._seen += 1

        cleaned = magnitude - self.strength * self._noise
        np.maximum(cleaned, self.floor * magnitude, out=cleaned)

        # Keep the original phase; the ear does not miss what we changed.
        with np.errstate(invalid="ignore", divide="ignore"):
            gain = np.where(magnitude > 1e-9, cleaned / magnitude, 0.0)
        result = (np.fft.irfft(spectrum * gain, n=self.frame)
                  * self.window).astype(np.float32)

        end = self._write + self.frame
        if self._out.size < end:
            grow = end - self._out.size
            self._out = np.concatenate([self._out, np.zeros(grow, np.float32)])
            self._norm = np.concatenate([self._norm, np.zeros(grow, np.float32)])
        self._out[self._write:end] += result
        self._norm[self._write:end] += self.window ** 2
        self._write += self.hop

        self._measure(magnitude, cleaned)

    def _track_floor(self, magnitude):
        """Follow the quietest each frequency has been over the last second."""
        if self._cur_min is None:
            self._cur_min = magnitude.copy()
        else:
            np.minimum(self._cur_min, magnitude, out=self._cur_min)

        self._in_stretch += 1
        if self._in_stretch >= self._stretch:
            self._mins.append(self._cur_min)
            self._cur_min = magnitude.copy()
            self._in_stretch = 0

        pool = list(self._mins) + [self._cur_min]
        quietest = pool[0]
        for m in pool[1:]:
            quietest = np.minimum(quietest, m)
        self._floor = quietest

    def _room_changed(self):
        """Has the room itself got louder, rather than something happening?

        This is the cooler being switched on after we started. The quietest
        the room has been in the last second is now louder than everything
        we believe about it, right across the frequency range - a voice
        cannot do that, it only lifts the few frequencies it occupies. When
        it happens there is nothing worth keeping in the old profile.
        """
        if self._floor is None or self._noise is None:
            return False
        risen = self._floor > np.maximum(self._noise, 1e-9) * 2.0
        return float(np.count_nonzero(risen)) / risen.size > 0.35

    def _is_noise_only(self, magnitude):
        """Does this frame look like just the room, or is something in it?"""
        if self._noise is None:
            return True
        louder = magnitude > (self._noise * 2.0)
        # A voice lights up a run of neighbouring frequencies at once; a fan
        # wobbling lights up one or two.
        return float(np.count_nonzero(louder)) / louder.size < 0.08

    def _measure(self, magnitude, cleaned):
        if self._noise is None:
            return
        freqs = np.fft.rfftfreq(self.frame, 1.0 / self.rate)
        voice = (freqs >= 250) & (freqs < 3000)
        self.noise_level = float(np.sqrt(np.mean(self._noise[voice] ** 2)))
        self.voice_level = float(np.sqrt(np.mean(cleaned[voice] ** 2)))
