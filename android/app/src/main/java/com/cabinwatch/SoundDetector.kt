package com.cabinwatch

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Turns microphone samples into one of three things: a baby crying, a thud,
 * or ordinary noise. Ported from agent/detect.py so both sides behave alike.
 *
 * The thresholds are relative to a baseline the detector learns from the quiet
 * stretches, which is what makes it work in a silent nursery and a noisy room
 * without anybody tuning numbers. A ceiling fan is low-frequency but quiet and
 * steady, so it fails both the loudness and the frequency tests and stays
 * silent all night - that mattered more than anything else here, because a
 * detector that cries wolf gets ignored.
 */
class SoundDetector(
    private val rate: Int = Protocol.AUDIO_RATE,
    var sensitivity: Float = 1.0f,
    private val crySeconds: Float = 1.2f,
    private val quietFloor: Float = 0.0008f
) {
    var baseline: Float = 0.004f
        private set
    var level: Float = 0f
        private set
    var loud: Boolean = false
        private set

    private var loudSince = 0L
    private var lastImpact = 0L

    /** What a block of audio turned out to be, if anything. */
    enum class Event { IMPACT, CRY, SOUND }

    /**
     * Feed a block of mono samples in -1..1. Returns an event or null.
     * Call it as the audio arrives: the cry test is about how long a sound
     * has been going on in real time.
     */
    fun feed(samples: FloatArray, count: Int = samples.size): Event? {
        if (count <= 0) return null

        var sum = 0.0
        for (i in 0 until count) sum += (samples[i] * samples[i]).toDouble()
        val rms = sqrt(sum / count).toFloat() + 1e-12f
        level = rms

        // Learn what quiet sounds like here, but only from quiet blocks.
        if (rms < baseline * 2f) baseline = baseline * 0.995f + rms * 0.005f
        if (baseline < quietFloor) baseline = quietFloor

        val sens = sensitivity.coerceAtLeast(0.2f)
        val trigger = maxOf(baseline * 4.5f / sens, 0.010f)
        loud = rms > trigger
        val now = System.currentTimeMillis()

        if (!loud) {
            loudSince = 0L
            return null
        }
        if (loudSince == 0L) loudSince = now

        // Where the energy sits tells a cry from a bang from a hum.
        val bands = bandEnergy(samples, count)
        val low = bands[0]
        val voice = bands[1]

        // A fall: very sudden, very loud, dominated by the low end.
        if (rms > maxOf(baseline * 14f, 0.085f) && low > 0.45f &&
            now - lastImpact > 5000
        ) {
            lastImpact = now
            loudSince = 0L
            return Event.IMPACT
        }

        val heldMs = now - loudSince
        if (heldMs >= (crySeconds * 1000).toLong() && voice > 0.42f) {
            loudSince = 0L
            return Event.CRY
        }
        if (heldMs >= 2500) {
            loudSince = 0L
            return Event.SOUND
        }
        return null
    }

    /** Returns [below 250 Hz, 250-2500 Hz] as fractions of total energy. */
    private fun bandEnergy(samples: FloatArray, count: Int): FloatArray {
        val n = largestPowerOfTwo(count).coerceAtMost(2048)
        if (n < 64) return floatArrayOf(0f, 0f)

        val re = DoubleArray(n)
        val im = DoubleArray(n)
        for (i in 0 until n) {
            // Hann window, so leakage does not smear energy across the bands.
            val wnd = 0.5 - 0.5 * cos(2.0 * Math.PI * i / (n - 1))
            re[i] = samples[i].toDouble() * wnd
        }
        fft(re, im)

        val binHz = rate.toDouble() / n
        var low = 0.0
        var voice = 0.0
        var total = 0.0
        for (k in 1 until n / 2) {
            val power = re[k] * re[k] + im[k] * im[k]
            total += power
            val hz = k * binHz
            when {
                hz < 250 -> low += power
                hz < 2500 -> voice += power
            }
        }
        if (total <= 0.0) return floatArrayOf(0f, 0f)
        return floatArrayOf((low / total).toFloat(), (voice / total).toFloat())
    }

    private fun largestPowerOfTwo(v: Int): Int {
        var n = 1
        while (n * 2 <= v) n *= 2
        return n
    }

    /** In-place iterative radix-2 FFT. n must be a power of two. */
    private fun fft(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) {
                j = j xor bit
                bit = bit shr 1
            }
            j = j or bit
            if (i < j) {
                var t = re[i]; re[i] = re[j]; re[j] = t
                t = im[i]; im[i] = im[j]; im[j] = t
            }
        }
        var len = 2
        while (len <= n) {
            val ang = -2.0 * Math.PI / len
            val wRe = cos(ang)
            val wIm = sin(ang)
            var i = 0
            while (i < n) {
                var curRe = 1.0
                var curIm = 0.0
                for (k in 0 until len / 2) {
                    val uRe = re[i + k]
                    val uIm = im[i + k]
                    val vRe = re[i + k + len / 2] * curRe - im[i + k + len / 2] * curIm
                    val vIm = re[i + k + len / 2] * curIm + im[i + k + len / 2] * curRe
                    re[i + k] = uRe + vRe
                    im[i + k] = uIm + vIm
                    re[i + k + len / 2] = uRe - vRe
                    im[i + k + len / 2] = uIm - vIm
                    val nextRe = curRe * wRe - curIm * wIm
                    curIm = curRe * wIm + curIm * wRe
                    curRe = nextRe
                }
                i += len
            }
            len = len shl 1
        }
    }
}
