"""
The fan filter, measured rather than assumed.

A fan or a cooler in the baby's room is steady, broadband and loud enough to
bury a cry. These checks hold the filter to three promises: it must leave the
baby's own sound alone, it must pull the fan down under it, and with the fan
running it must still let the crying alert fire - and not fire on the fan.
"""

import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))

from denoise import NoiseSuppressor          # noqa: E402
from detect import SoundDetector             # noqa: E402

RATE = 16000


def fan(seconds, level=0.05, seed=1):
    """Steady broadband hum: that is what a fan or a cooler sounds like."""
    rng = np.random.default_rng(seed)
    n = int(RATE * seconds)
    noise = rng.standard_normal(n).astype(np.float32)
    # Most of a fan's energy sits low, so tilt it that way.
    kernel = np.ones(24, dtype=np.float32) / 24.0
    noise = np.convolve(noise, kernel, mode="same")
    noise /= max(float(np.abs(noise).max()), 1e-9)
    return (noise * level).astype(np.float32)


def cry(seconds, level=0.08, start=0.0):
    """A baby's cry: a wobbling tone around 500 Hz with its harmonics."""
    n = int(RATE * seconds)
    t = np.arange(n, dtype=np.float32) / RATE
    wobble = 500.0 + 60.0 * np.sin(2 * np.pi * 4.0 * t)
    phase = 2 * np.pi * np.cumsum(wobble) / RATE
    wave = (np.sin(phase) + 0.5 * np.sin(2 * phase)
            + 0.25 * np.sin(3 * phase)).astype(np.float32)
    wave /= max(float(np.abs(wave).max()), 1e-9)
    out = wave * level
    if start > 0:
        out[:int(RATE * start)] = 0.0
    return out.astype(np.float32)


def run(suppressor, signal, block=256):
    """Push a signal through in realistic little blocks."""
    out = []
    for i in range(0, signal.size, block):
        out.append(suppressor.process(signal[i:i + block]))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


def rms(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def db(a, b):
    return 20.0 * np.log10(max(a, 1e-12) / max(b, 1e-12))


class FakeClock:
    """Eight seconds of crying, fed in instantly.

    The detector measures how long a sound has lasted against the clock, so a
    test that pushed samples in as fast as the CPU allows would look like one
    instant of noise. This moves the clock on by exactly as much audio as has
    been handed over.
    """

    def __init__(self, rate=RATE):
        self.rate = rate
        self.t = 1000.0

    def advance(self, samples):
        self.t += samples / float(self.rate)

    def __call__(self):
        return self.t


def listen(signal, block=256):
    """Run a signal past the alert detector in real-ish time."""
    clock = FakeClock()
    det = SoundDetector(rate=RATE, clock=clock)
    events = []
    for i in range(0, signal.size, block):
        chunk = signal[i:i + block]
        clock.advance(chunk.size)
        got = det.feed(chunk)
        if got:
            events.append(got)
    return events


def check(name, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + name
          + (("  -- " + detail) if detail else ""))
    return bool(condition)


def test_passes_sound_through_untouched():
    """With no subtraction asked for, what goes in must come out."""
    sig = cry(2.0)
    quiet = NoiseSuppressor(rate=RATE, strength=0.0, floor=1.0)
    out = run(quiet, sig)

    # The filter runs on overlapping windows, so the sound comes out a
    # fraction of a second late. Find that delay, then compare.
    skip = 2048
    best, delay = None, None
    for shift in range(0, 1025):
        a = sig[skip:out.size - shift]
        b = out[skip + shift:]
        if a.size < RATE // 2:
            break
        error = rms(a - b) / max(rms(a), 1e-9)
        if best is None or error < best:
            best, delay = error, shift

    ok = check("sound passes through unchanged when nothing is subtracted",
               best is not None and best < 0.02,
               f"error {best * 100:.2f}% at a {delay} sample delay")
    return ok and check("the delay it adds is under a fiftieth of a second",
                        delay < RATE // 50, f"{delay / RATE * 1000:.0f} ms")


def test_keeps_the_baby_and_cuts_the_fan():
    """The point of the whole thing: cry up, fan down."""
    seconds = 6.0
    noise = fan(seconds)
    voice = cry(seconds, start=2.0)      # two seconds of fan alone to learn from
    mixed = noise + voice

    sup = NoiseSuppressor(rate=RATE)
    cleaned = run(sup, mixed)

    # Measure over the part where the baby is actually crying.
    frm, to = int(RATE * 3.0), int(RATE * 5.5)
    before = db(rms(mixed[frm:to]), rms(noise[frm:to]))

    sup_ref = NoiseSuppressor(rate=RATE)
    only_noise = run(sup_ref, noise)
    after = db(rms(cleaned[frm:to]), rms(only_noise[frm:to]))

    gain = after - before
    ok = check("the baby stands out further above the fan",
               gain > 6.0, f"{before:+.1f} dB -> {after:+.1f} dB ({gain:+.1f})")

    quiet_to = int(RATE * 1.8)
    cut = db(rms(cleaned[int(RATE * 0.5):quiet_to]),
             rms(mixed[int(RATE * 0.5):quiet_to]))
    ok &= check("the fan alone is pulled down", cut < -6.0, f"{cut:+.1f} dB")
    return ok


def test_the_cry_still_raises_the_alert_over_a_fan():
    """A filter that silenced the alert would be worse than no filter."""
    seconds = 8.0
    mixed = fan(seconds) + cry(seconds, start=2.0)
    sup = NoiseSuppressor(rate=RATE)
    cleaned = run(sup, mixed)

    events = listen(cleaned)
    return check("crying over a fan still raises an alert",
                 "cry" in events, f"events {events or 'none'}")


def test_the_fan_on_its_own_raises_nothing():
    """Before the filter, a cooler alone was enough to wake him at night."""
    noise = fan(10.0)
    cleaned = run(NoiseSuppressor(rate=RATE), noise)
    ok = check("a fan on its own raises no alert",
               not listen(cleaned), f"events {listen(cleaned) or 'none'}")
    # Worth stating what it was like before: the same fan, unfiltered.
    print("      (unfiltered, the same fan gives: "
          + (", ".join(listen(noise)) or "nothing") + ")")
    return ok


def test_a_cooler_switched_on_later_is_still_learned():
    """The case the first version of this got wrong.

    If the filter only learns the room while the room is quiet, a cooler
    switched on after the camera started is never learned - every frame
    looks too loud to learn from, forever - and the filter does nothing
    about the one noise it was built for.
    """
    seconds = 12.0
    noise = fan(seconds)
    noise[:int(RATE * 2.0)] = 0.0          # switched on two seconds in
    cleaned = run(NoiseSuppressor(rate=RATE), noise)

    frm, to = int(RATE * 5.0), int(RATE * 12.0)
    cut = db(rms(cleaned[frm:to]), rms(noise[frm:to]))
    ok = check("a cooler switched on later is learned and cut",
               cut < -8.0, f"{cut:+.1f} dB, three seconds after it starts")

    # And the baby must still get through once it has settled.
    mixed = noise + cry(seconds, start=7.0)
    events = listen(run(NoiseSuppressor(rate=RATE), mixed))
    return ok and check("crying over that cooler still raises an alert",
                        "cry" in events, f"events {events or 'none'}")


def test_a_cry_is_not_mistaken_for_the_room():
    """A voice must never be learned as part of the background.

    A baby who cries without a pause for a minute is the hard case: the
    filter has to tell "the room got louder" from "something is happening
    in the room", or it quietly subtracts the child.
    """
    sig = cry(10.0, start=1.0)
    cleaned = run(NoiseSuppressor(rate=RATE), sig)
    frm, to = int(RATE * 4.0), int(RATE * 9.0)
    kept = db(rms(cleaned[frm:to]), rms(sig[frm:to]))
    ok = check("a long cry is not learned away", kept > -8.0,
               f"{kept:+.1f} dB of the baby left after ten seconds")
    events = listen(cleaned)
    return ok and check("and it still raises an alert",
                        "cry" in events, f"events {events or 'none'}")


def test_it_does_not_change_the_length_of_the_audio():
    """Listening and recording both break if samples go missing."""
    sig = fan(3.0) + cry(3.0, start=1.0)
    out = run(NoiseSuppressor(rate=RATE), sig, block=320)
    return check("every sample that goes in comes out",
                 out.size == sig.size, f"{sig.size} in, {out.size} out")


def test_odd_block_sizes_are_safe():
    """Microphones hand over whatever size they feel like."""
    sup = NoiseSuppressor(rate=RATE)
    sizes = [1, 7, 100, 256, 513, 2048, 0]
    total = 0
    for n in sizes:
        out = sup.process(fan(1.0)[:n])
        if out.size != n:
            return check("odd block sizes are handled", False,
                         f"{n} in, {out.size} out")
        total += n
    return check("odd block sizes are handled", True, f"{total} samples")


def main():
    tests = [
        test_passes_sound_through_untouched,
        test_keeps_the_baby_and_cuts_the_fan,
        test_the_cry_still_raises_the_alert_over_a_fan,
        test_the_fan_on_its_own_raises_nothing,
        test_a_cooler_switched_on_later_is_still_learned,
        test_a_cry_is_not_mistaken_for_the_room,
        test_it_does_not_change_the_length_of_the_audio,
        test_odd_block_sizes_are_safe,
    ]
    ok = True
    for t in tests:
        ok &= bool(t())
    print()
    print("noise filter: " + ("all checks pass" if ok else "SOMETHING FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
