package com.cabinwatch

import android.content.Context
import android.content.SharedPreferences

/**
 * Everything the user typed in, kept on the phone.
 *
 * The server token lives here and nowhere else - it is never written to the
 * repository and never leaves this phone except to authenticate with your own
 * server.
 */
class Settings(context: Context) {

    private val sp: SharedPreferences =
        context.getSharedPreferences("cabinwatch", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString(K_SERVER, "") ?: ""
        set(v) = sp.edit().putString(K_SERVER, v.trim().trimEnd('/')).apply()

    var token: String
        get() = sp.getString(K_TOKEN, "") ?: ""
        set(v) = sp.edit().putString(K_TOKEN, v.trim()).apply()

    var cameraName: String
        get() = sp.getString(K_NAME, "") ?: ""
        set(v) = sp.edit().putString(K_NAME, v.trim()).apply()

    /** Stable id, generated once, so the dashboard recognises this phone again. */
    var cameraId: String
        get() {
            var id = sp.getString(K_ID, "") ?: ""
            if (id.isEmpty()) {
                id = slug(cameraName.ifEmpty { "phone" }) + "-" +
                        java.util.UUID.randomUUID().toString().take(4)
                sp.edit().putString(K_ID, id).apply()
            }
            return id
        }
        set(v) = sp.edit().putString(K_ID, v).apply()

    /** Index into the camera list CameraX reports. */
    var lensIndex: Int
        get() = sp.getInt(K_LENS, 0)
        set(v) = sp.edit().putInt(K_LENS, v).apply()

    /** True = watching a baby, so sounds raise alerts as well as movement. */
    var babyMode: Boolean
        get() = sp.getBoolean(K_BABY, true)
        set(v) = sp.edit().putBoolean(K_BABY, v).apply()

    var armed: Boolean
        get() = sp.getBoolean(K_ARMED, true)
        set(v) = sp.edit().putBoolean(K_ARMED, v).apply()

    var retentionHours: Int
        get() = sp.getInt(K_RETENTION, 24)
        set(v) = sp.edit().putInt(K_RETENTION, v).apply()

    var maxStorageMb: Int
        get() = sp.getInt(K_MAXMB, 4000)
        set(v) = sp.edit().putInt(K_MAXMB, v).apply()

    /**
     * Put a sound track in the saved recordings.
     *
     * Off by default, and that is deliberate: Android will not reliably give
     * the microphone to the recorder and to our own listener at the same time,
     * so turning this on can cost you the crying alerts and the Listen button.
     * Video-only recordings keep both working.
     */
    var recordAudio: Boolean
        get() = sp.getBoolean(K_RECAUDIO, false)
        set(v) = sp.edit().putBoolean(K_RECAUDIO, v).apply()

    var soundSensitivity: Float
        get() = sp.getFloat(K_SENS, 1.0f)
        set(v) = sp.edit().putFloat(K_SENS, v).apply()

    var motionSensitivity: Float
        get() = sp.getFloat(K_MOTION, 1.0f)
        set(v) = sp.edit().putFloat(K_MOTION, v).apply()

    /** Should the service come back by itself after the phone restarts. */
    var startOnBoot: Boolean
        get() = sp.getBoolean(K_BOOT, true)
        set(v) = sp.edit().putBoolean(K_BOOT, v).apply()

    /** Set while the camera is meant to be running, so boot knows to resume. */
    var running: Boolean
        get() = sp.getBoolean(K_RUNNING, false)
        set(v) = sp.edit().putBoolean(K_RUNNING, v).apply()

    fun isConfigured() = serverUrl.isNotEmpty() && token.isNotEmpty()

    companion object {
        private const val K_SERVER = "server_url"
        private const val K_TOKEN = "token"
        private const val K_NAME = "camera_name"
        private const val K_ID = "camera_id"
        private const val K_LENS = "lens_index"
        private const val K_BABY = "baby_mode"
        private const val K_ARMED = "armed"
        private const val K_RETENTION = "retention_hours"
        private const val K_MAXMB = "max_storage_mb"
        private const val K_RECAUDIO = "record_audio"
        private const val K_SENS = "sound_sensitivity"
        private const val K_MOTION = "motion_sensitivity"
        private const val K_BOOT = "start_on_boot"
        private const val K_RUNNING = "running"

        fun slug(s: String): String {
            val out = s.lowercase().map { if (it.isLetterOrDigit()) it else '-' }
                .joinToString("").trim('-').replace(Regex("-+"), "-")
            return if (out.isEmpty()) "cam" else out.take(40)
        }
    }
}
