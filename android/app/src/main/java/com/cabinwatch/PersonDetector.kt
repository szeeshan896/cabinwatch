package com.cabinwatch

import android.graphics.Bitmap
import android.util.Log
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.label.ImageLabeling
import com.google.mlkit.vision.label.defaults.ImageLabelerOptions

/**
 * Telling a person from a curtain.
 *
 * Movement alone is a poor burglar alarm. A fan, a curtain over an open
 * window, headlights crossing a wall and the sun coming out all move, and
 * after a few nights of being woken by none of them you stop trusting the
 * phone - which is worse than having no alarm, because now you ignore the
 * real one too.
 *
 * In the cabin, then, movement is only worth waking somebody for if there
 * is a person in it. This asks the phone's own image labeller, which runs
 * on the device: no picture leaves the phone to answer the question, and
 * nothing is asked of the server.
 *
 * It is deliberately only consulted when something has already moved. The
 * labeller is far more expensive than comparing two tiny greyscale frames,
 * and a camera watching an empty room at night would otherwise spend the
 * night running it for nothing.
 */
class PersonDetector(
    /** How sure it has to be. Lower catches more, and more that is not there. */
    private val confidence: Float = 0.6f
) {

    /** What the labeller calls us, when it knows. */
    fun interface Answer {
        fun saw(person: Boolean)
    }

    private val labeller by lazy {
        try {
            ImageLabeling.getClient(
                ImageLabelerOptions.Builder()
                    .setConfidenceThreshold(confidence)
                    .build()
            )
        } catch (e: Throwable) {
            Log.w(TAG, "no image labeller on this phone: ${e.message}")
            null
        }
    }

    /** True once we know the phone can answer at all. */
    var available = true
        private set

    private var busy = false
    private var lastAsked = 0L

    /**
     * Ask whether this frame holds a person.
     *
     * Answers through the callback, on whatever thread ML Kit finishes on.
     * If it is already thinking about the previous frame, or was asked a
     * moment ago, this one is dropped rather than queued - an alert about
     * three seconds ago is no use.
     */
    fun ask(frame: Bitmap, minGapMs: Long = 700, answer: Answer) {
        val client = labeller
        if (client == null) {
            available = false
            answer.saw(true)          // cannot tell: do not swallow the alert
            return
        }
        val now = System.currentTimeMillis()
        if (busy || now - lastAsked < minGapMs) return
        busy = true
        lastAsked = now

        try {
            client.process(InputImage.fromBitmap(frame, 0))
                .addOnSuccessListener { labels ->
                    busy = false
                    val person = labels.any { looksLikeAPerson(it.text) }
                    answer.saw(person)
                }
                .addOnFailureListener { e ->
                    busy = false
                    Log.w(TAG, "could not look at the frame: ${e.message}")
                    answer.saw(true)   // again: do not lose a real alert
                }
        } catch (e: Throwable) {
            busy = false
            available = false
            Log.w(TAG, "person detection is not working here: ${e.message}")
            answer.saw(true)
        }
    }

    fun close() {
        try {
            labeller?.close()
        } catch (_: Throwable) {
        }
    }

    companion object {
        private const val TAG = "CabinWatchPerson"

        /**
         * The labels that mean somebody is there.
         *
         * The general-purpose labeller does not have one tidy "person"
         * label: it will say Person, but it also says Hair, Arm, Hand,
         * Smile or Standing when it can only see part of somebody - which
         * is the usual case for a camera up on a shelf. All of those mean
         * the same thing for our purposes.
         */
        private val PERSON_WORDS = setOf(
            "person", "people", "human", "face", "hair", "arm", "hand",
            "finger", "leg", "foot", "head", "neck", "shoulder", "smile",
            "standing", "sitting", "walking", "selfie", "beard", "glasses",
            "muscle", "jaw", "eyelash", "forehead", "chin", "thumb",
            "t-shirt", "shirt", "trousers", "jeans", "shoe", "footwear",
            "sleeve", "gesture"
        )

        fun looksLikeAPerson(label: String): Boolean {
            val word = label.trim().lowercase()
            return word in PERSON_WORDS
        }
    }
}
