package com.cabinwatch

import android.graphics.Bitmap

/**
 * Movement detection, same idea as the Python agent: compare a tiny
 * greyscale version of each frame with the one before it.
 *
 * Working at 64x48 is cheap enough to run all night on a phone battery, and
 * blurring away the detail means sensor noise and slow light changes do not
 * register, while a baby turning over does.
 */
class MotionDetector(
    /** Above 1.0 notices smaller movements; below 1.0 needs bigger ones. */
    var sensitivity: Float = 1.0f
) {
    private val w = 64
    private val h = 48
    private var previous: IntArray? = null
    private val pixels = IntArray(w * h)
    private var scaled: Bitmap? = null

    /** Fraction of the frame that changed, last time we looked. */
    var lastChanged: Float = 0f
        private set

    fun reset() {
        previous = null
    }

    /** Returns true when enough of the picture changed to call it movement. */
    fun detect(frame: Bitmap): Boolean {
        val small = scaleTo(frame)
        small.getPixels(pixels, 0, w, 0, 0, w, h)

        val grey = IntArray(w * h)
        for (i in pixels.indices) {
            val p = pixels[i]
            // Standard luma weights; the shift is a cheap divide by 1000.
            grey[i] = ((p shr 16 and 0xFF) * 299 +
                       (p shr 8 and 0xFF) * 587 +
                       (p and 0xFF) * 114) / 1000
        }

        val prev = previous
        previous = grey
        if (prev == null) {
            lastChanged = 0f
            return false
        }

        // A pixel has to move a fair amount to count, which filters noise.
        val pixelThreshold = (22 / sensitivity.coerceAtLeast(0.2f)).toInt()
        var changed = 0
        for (i in grey.indices) {
            if (kotlin.math.abs(grey[i] - prev[i]) > pixelThreshold) changed++
        }

        lastChanged = changed.toFloat() / grey.size
        // Roughly 1.5% of the frame, scaled by how sensitive the user wants it.
        val needed = 0.015f / sensitivity.coerceAtLeast(0.2f)
        return lastChanged > needed
    }

    private fun scaleTo(frame: Bitmap): Bitmap {
        val existing = scaled
        if (existing != null && !existing.isRecycled) {
            // Bitmap.createScaledBitmap allocates; reuse a canvas instead.
            val c = android.graphics.Canvas(existing)
            val src = android.graphics.Rect(0, 0, frame.width, frame.height)
            val dst = android.graphics.Rect(0, 0, w, h)
            c.drawBitmap(frame, src, dst, null)
            return existing
        }
        val fresh = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val c = android.graphics.Canvas(fresh)
        c.drawBitmap(frame,
            android.graphics.Rect(0, 0, frame.width, frame.height),
            android.graphics.Rect(0, 0, w, h), null)
        scaled = fresh
        return fresh
    }
}
