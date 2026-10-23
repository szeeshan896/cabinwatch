package com.cabinwatch

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.audiofx.NoiseSuppressor
import android.media.AudioTrack
import android.media.MediaRecorder
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Base64
import android.util.Log
import android.util.Size
import android.view.Surface
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min

/**
 * The camera, running as a foreground service.
 *
 * This is the whole point of the app rather than the browser page: a service
 * with the camera and microphone foreground types keeps capturing with the
 * screen off and the app in the background, and comes back by itself after the
 * phone restarts. A browser tab cannot do any of that.
 *
 * What it does, all on the phone:
 *   - records to the phone's own storage, one file a minute, oldest recycled
 *   - watches for movement and listens for crying or a thud
 *   - sends video frames up only while somebody is watching the dashboard
 *   - plays your voice out of the speaker when you press Talk
 *   - hands over a single recording when you open it
 */
class CameraService : LifecycleService(), WsClient.Events {

    private lateinit var settings: Settings
    private lateinit var clips: ClipStore
    private var ws: WsClient? = null

    private var provider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private var analysis: ImageAnalysis? = null
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeRecording: Recording? = null
    private var currentClipName: String? = null
    private var canRecord = true
    private var recordingFailReason: String? = null

    private val motion = MotionDetector()
    private val sound = SoundDetector()

    private var analysisExec: ExecutorService? = null
    private var audioThread: Thread? = null
    private var audioRunning = AtomicBoolean(false)
    private var audioTrack: AudioTrack? = null
    private var wakeLock: PowerManager.WakeLock? = null

    // The phone watching itself. A camera quietly turned to face a wall
    // protects nobody and says nothing, so being picked up is its own alarm,
    // and where it ended up matters if it is carried off.
    private var sensors: SensorManager? = null
    private var restForce = 9.81f
    private var movingSince = 0L
    private var lastMovedAt = 0L
    private var lastLocation: Location? = null
    private var batteryWarned = false

    private val handler = Handler(Looper.getMainLooper())

    // What the dashboard has asked for right now.
    @Volatile private var streamWidth = 0
    @Volatile private var streamFps = 0f
    @Volatile private var streamQuality = 55
    @Volatile private var streaming = false
    @Volatile private var sendMic = false

    private var lastFrameSent = 0L
    private var lastDetect = 0L
    private var motionOn = false
    private var lastMotionAt = 0L
    private var lastSoundAt = 0L
    private var manualKeep = false
    // Recording runs around the clock, so the dashboard button pauses it
    // rather than starting it. Without this the button changed nothing.
    private var paused = false
    private val lastEventAt = HashMap<String, Long>()
    private var lensLabels = JSONArray()
    private var statusText = "starting"
    private val cancelledXfers = HashSet<Int>()

    @Volatile private var latestBitmap: Bitmap? = null

    // ======================================================================
    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
        clips = ClipStore(this, settings)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)

        if (intent?.action == ACTION_STOP) {
            settings.running = false
            stopEverything()
            stopSelf()
            return START_NOT_STICKY
        }

        goForeground()

        if (!settings.isConfigured()) {
            statusText = "not set up"
            updateNotification()
            return START_STICKY
        }

        settings.running = true
        motion.sensitivity = settings.motionSensitivity
        sound.sensitivity = settings.soundSensitivity

        acquireWakeLock()
        startWebsocket()
        startCamera()
        startMic()
        startTamperWatch()
        refreshLocation()
        handler.post(stateTicker)
        handler.postDelayed(sweeper, 60_000)

        // START_STICKY so Android brings the service back if it ever kills it.
        return START_STICKY
    }

    override fun onDestroy() {
        stopEverything()
        super.onDestroy()
    }

    private fun stopEverything() {
        handler.removeCallbacksAndMessages(null)
        try {
            sensors?.unregisterListener(motionWatcher)
        } catch (_: Exception) {
        }
        sensors = null
        setAlarm(false)
        stopMic()
        stopSegment()
        try {
            provider?.unbindAll()
        } catch (_: Exception) {
        }
        analysisExec?.shutdown()
        analysisExec = null
        ws?.stop()
        ws = null
        audioTrack?.let {
            try {
                it.stop(); it.release()
            } catch (_: Exception) {
            }
        }
        audioTrack = null
        releaseWakeLock()
    }

    // ======================================================================
    // Foreground notification - what keeps us alive
    // ======================================================================
    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val ch = NotificationChannel(
            CHANNEL, "Camera running",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Shown while this phone is acting as a camera."
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }

    private fun buildNotification(): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, CameraService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val name = settings.cameraName.ifEmpty { "Camera" }
        return NotificationCompat.Builder(this, CHANNEL)
            .setContentTitle("$name - CabinWatch")
            .setContentText(statusText)
            .setSmallIcon(R.drawable.ic_stat_cam)
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun goForeground() {
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA or
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else 0
        ServiceCompat.startForeground(this, NOTIF_ID, buildNotification(), type)
    }

    private fun updateNotification() {
        try {
            getSystemService(NotificationManager::class.java)
                .notify(NOTIF_ID, buildNotification())
        } catch (_: Exception) {
        }
    }

    private fun acquireWakeLock() {
        if (wakeLock != null) return
        val pm = getSystemService(PowerManager::class.java)
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "CabinWatch::camera")
            .also { it.setReferenceCounted(false); it.acquire() }
    }

    private fun releaseWakeLock() {
        try {
            wakeLock?.release()
        } catch (_: Exception) {
        }
        wakeLock = null
    }

    // ======================================================================
    // Camera
    // ======================================================================
    private fun startCamera() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED
        ) {
            statusText = "camera permission missing"
            updateNotification()
            return
        }

        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val p = future.get()
                provider = p
                bind(p)
            } catch (e: Exception) {
                Log.e(TAG, "camera provider failed", e)
                statusText = "camera unavailable"
                updateNotification()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun bind(p: ProcessCameraProvider) {
        val exec = analysisExec ?: Executors.newSingleThreadExecutor()
            .also { analysisExec = it }

        val infos = p.availableCameraInfos
        lensLabels = JSONArray().also { arr ->
            infos.forEachIndexed { i, info ->
                val facing = when (info.lensFacing) {
                    CameraSelector.LENS_FACING_FRONT -> "Front"
                    CameraSelector.LENS_FACING_BACK -> "Back"
                    else -> "Camera"
                }
                arr.put(JSONObject().apply {
                    put("id", i.toString())
                    put("label", if (infos.count { it.lensFacing == info.lensFacing } > 1)
                        "$facing camera ${i + 1}" else "$facing camera")
                })
            }
        }

        val index = settings.lensIndex.coerceIn(0, (infos.size - 1).coerceAtLeast(0))
        val selector = if (infos.isNotEmpty()) infos[index].cameraSelector
        else CameraSelector.DEFAULT_BACK_CAMERA

        val an = ImageAnalysis.Builder()
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .setTargetResolution(Size(1280, 720))
            .build()
        an.setAnalyzer(exec) { proxy -> onFrame(proxy) }
        analysis = an

        val recorder = Recorder.Builder()
            .setQualitySelector(
                QualitySelector.fromOrderedList(
                    listOf(Quality.HD, Quality.SD, Quality.LOWEST)
                )
            )
            .build()
        val vc = VideoCapture.withOutput(recorder)
        vc.targetRotation = targetRotationFor(settings.rotate)

        try {
            p.unbindAll()
            // Recording and frame analysis together is supported on anything
            // above the oldest camera hardware level.
            camera = p.bindToLifecycle(this, selector, an, vc)
            videoCapture = vc
            canRecord = true
            recordingFailReason = null
        } catch (e: Exception) {
            Log.w(TAG, "could not bind recording and analysis together: ${e.message}")
            // Fall back to watching only. Live view, movement and sound alerts
            // all keep working; this phone just cannot also save video.
            try {
                p.unbindAll()
                camera = p.bindToLifecycle(this, selector, an)
                videoCapture = null
                canRecord = false
                recordingFailReason = "this phone will not record and stream at once"
            } catch (e2: Exception) {
                Log.e(TAG, "camera bind failed entirely", e2)
                statusText = "camera busy - close other camera apps"
                updateNotification()
                return
            }
        }

        motion.reset()
        statusText = "live"
        updateNotification()
        if (canRecord) startSegment()
    }

    private fun switchLens(idString: String?) {
        val p = provider ?: return
        val idx = idString?.toIntOrNull() ?: return
        if (idx == settings.lensIndex) return
        settings.lensIndex = idx
        handler.post {
            stopSegment()
            bind(p)
            pushState()
        }
    }

    private fun setZoom(value: Double) {
        val cam = camera ?: return
        val state = cam.cameraInfo.zoomState.value ?: return
        val want = value.toFloat().coerceIn(state.minZoomRatio, state.maxZoomRatio)
        // A real zoom, not a crop: the phone has it, so use it.
        try {
            cam.cameraControl.setZoomRatio(want)
        } catch (e: Exception) {
            Log.w(TAG, "zoom refused: ${e.message}")
        }
    }

    // ======================================================================
    // Per-frame work
    // ======================================================================
    private fun onFrame(proxy: ImageProxy) {
        try {
            val now = System.currentTimeMillis()
            val needDetect = now - lastDetect >= 250
            val interval = if (streamFps > 0f) (1000f / streamFps).toLong() else 0L
            val needStream = streaming && interval > 0 && now - lastFrameSent >= interval

            if (!needDetect && !needStream) return

            val bitmap = proxy.toBitmap()

            if (needDetect) {
                lastDetect = now
                val moved = motion.detect(bitmap)
                if (moved) lastMotionAt = now
                if (moved && !motionOn && settings.armed) {
                    fireEvent(
                        "motion",
                        if (settings.babyMode) "Your baby moved."
                        else "Movement in view of this camera.",
                        bitmap, uprightDegrees(proxy.imageInfo.rotationDegrees)
                    )
                }
                motionOn = moved
                latestBitmap = bitmap
            }

            if (needStream) {
                lastFrameSent = now
                // Do not pile frames up behind a slow link.
                if ((ws?.queuedBytes() ?: 0L) < 1_500_000L) {
                    val jpeg = encodeJpeg(
                        bitmap, uprightDegrees(proxy.imageInfo.rotationDegrees),
                        streamWidth, streamQuality, stamp = true
                    )
                    if (jpeg != null) ws?.sendBinary(Protocol.packVideo(jpeg))
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "frame failed: ${e.message}")
        } finally {
            proxy.close()
        }
    }

    /** Rotate, scale, burn in the name and time, and compress. */
    /**
     * The camera's own sensor rotation, plus however the phone is mounted.
     */
    private fun uprightDegrees(sensor: Int) = (sensor + settings.rotate) % 360

    /**
     * CameraX is told where the top of the recording should end up, which is
     * the opposite way round from how far we want the picture turned: asking
     * for a target rotation of 270 turns the picture itself by 90.
     */
    private fun targetRotationFor(turn: Int) = when (((turn % 360) + 360) % 360) {
        90 -> Surface.ROTATION_270
        180 -> Surface.ROTATION_180
        270 -> Surface.ROTATION_90
        else -> Surface.ROTATION_0
    }

    private fun encodeJpeg(
        src: Bitmap, rotation: Int, width: Int, quality: Int, stamp: Boolean
    ): ByteArray? {
        try {
            val targetW = if (width > 0) min(width, src.width) else src.width
            val scale = targetW.toFloat() / src.width
            val m = Matrix()
            m.postScale(scale, scale)
            if (rotation != 0) m.postRotate(rotation.toFloat())
            val out = Bitmap.createBitmap(src, 0, 0, src.width, src.height, m, true)

            if (stamp) {
                val c = Canvas(out)
                val h = out.height
                val barH = (h * 0.055f).coerceAtLeast(20f)
                val bar = Paint().apply { color = Color.argb(140, 0, 0, 0) }
                c.drawRect(0f, h - barH, out.width.toFloat(), h.toFloat(), bar)
                val text = Paint().apply {
                    color = Color.WHITE
                    textSize = barH * 0.66f
                    isAntiAlias = true
                }
                val when_ = SimpleDateFormat("dd MMM yyyy  HH:mm:ss", Locale.getDefault())
                    .format(Date())
                val name = settings.cameraName.ifEmpty { "Camera" }
                c.drawText("$name  |  $when_", 8f, h - barH * 0.25f, text)
            }

            val bos = ByteArrayOutputStream()
            out.compress(Bitmap.CompressFormat.JPEG, quality.coerceIn(20, 95), bos)
            if (out != src) out.recycle()
            return bos.toByteArray()
        } catch (e: Exception) {
            Log.w(TAG, "encode failed: ${e.message}")
            return null
        }
    }

    // ======================================================================
    // Recording to this phone
    // ======================================================================
    private fun startSegment() {
        val vc = videoCapture ?: return
        if (activeRecording != null || paused) return
        try {
            val file = clips.newClipFile()
            var pending = vc.output.prepareRecording(
                this, FileOutputOptions.Builder(file).build()
            )
            // Sound in the saved file costs us the microphone, and with it the
            // crying alerts, so it is opt-in.
            if (settings.recordAudio && hasMicPermission()) {
                pending = pending.withAudioEnabled()
            }
            activeRecording = pending.start(
                ContextCompat.getMainExecutor(this)
            ) { event ->
                if (event is VideoRecordEvent.Finalize && event.hasError()) {
                    Log.w(TAG, "recording error ${event.error}")
                }
            }
            currentClipName = file.name
            if (manualKeep) clips.markKeep(file.name)
            handler.postDelayed(segmentRoller, SEGMENT_MS)
        } catch (e: Exception) {
            Log.e(TAG, "could not start recording: ${e.message}")
            canRecord = false
            recordingFailReason = e.message
        }
    }

    private val segmentRoller = Runnable {
        // One file per minute, so each one plays on its own and the oldest can
        // be deleted without touching the rest.
        stopSegment()
        if (settings.running && canRecord && !paused) {
            handler.postDelayed({ startSegment() }, 200)
        }
    }

    private fun stopSegment() {
        handler.removeCallbacks(segmentRoller)
        val finished = currentClipName
        try {
            activeRecording?.stop()
        } catch (_: Exception) {
        }
        activeRecording = null
        currentClipName = null
        if (finished != null) offerClip(finished)
    }

    /**
     * Tell the server about a finished clip that an alert was raised during.
     *
     * The recording is on this phone, which is no use at all if the phone is
     * what gets taken. The server decides whether it wants a copy and pulls
     * it the usual way; nothing is pushed uninvited.
     *
     * The file takes a moment to be closed and written after stop() returns,
     * so the offer waits for it rather than racing it.
     */
    private fun offerClip(name: String) {
        if (!clips.isKept(name)) return
        handler.postDelayed({
            ws?.sendJson(JSONObject().apply {
                put("t", "clip_ready")
                put("file", name)
                put("event", true)
            })
        }, 2500)
    }

    private val sweeper = object : Runnable {
        override fun run() {
            clips.sweep(currentClipName)
            handler.postDelayed(this, 60_000)
        }
    }

    // ======================================================================
    // Microphone and speaker
    // ======================================================================
    private fun hasMicPermission() = ContextCompat.checkSelfPermission(
        this, Manifest.permission.RECORD_AUDIO
    ) == PackageManager.PERMISSION_GRANTED

    private fun startMic() {
        if (!hasMicPermission() || audioRunning.get()) return
        audioRunning.set(true)
        audioThread = Thread {
            var rec: AudioRecord? = null
            var suppressor: NoiseSuppressor? = null
            try {
                val minBuf = AudioRecord.getMinBufferSize(
                    Protocol.AUDIO_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
                )
                // The voice-call source is the one the phone cleans up: it
                // strips a fan or cooler and cancels our own speaker, so
                // talking does not feed back. Raw MIC gives the room as it is.
                val source = if (settings.noiseFilter) {
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION
                } else {
                    MediaRecorder.AudioSource.MIC
                }
                rec = try {
                    AudioRecord(
                        source,
                        Protocol.AUDIO_RATE,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT,
                        minBuf.coerceAtLeast(4096) * 2
                    ).takeIf { it.state == AudioRecord.STATE_INITIALIZED }
                } catch (e: Exception) {
                    Log.w(TAG, "microphone source $source refused: ${e.message}")
                    null
                } ?: AudioRecord(
                    // Not every phone offers the voice-call source; fall back
                    // rather than lose the microphone altogether.
                    MediaRecorder.AudioSource.MIC,
                    Protocol.AUDIO_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    minBuf.coerceAtLeast(4096) * 2
                )
                if (rec.state != AudioRecord.STATE_INITIALIZED) {
                    Log.w(TAG, "microphone not available")
                    return@Thread
                }
                suppressor = openSuppressor(rec, settings.noiseFilter)
                rec.startRecording()
                Log.i(TAG, "microphone open (filter=${settings.noiseFilter})")

                val shorts = ShortArray(1024)
                val floats = FloatArray(1024)
                while (audioRunning.get()) {
                    val n = rec.read(shorts, 0, shorts.size)
                    if (n <= 0) continue
                    for (i in 0 until n) floats[i] = shorts[i] / 32768f

                    val event = sound.feed(floats, n)
                    if (sound.loud) lastSoundAt = System.currentTimeMillis()

                    if (event != null && settings.armed) {
                        handleSoundEvent(event)
                    }

                    if (sendMic) {
                        val bytes = ByteArray(n * 2)
                        for (i in 0 until n) {
                            val s = shorts[i].toInt()
                            bytes[i * 2] = (s and 0xFF).toByte()
                            bytes[i * 2 + 1] = (s shr 8 and 0xFF).toByte()
                        }
                        ws?.sendBinary(Protocol.packAudio(bytes))
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "microphone loop ended: ${e.message}")
            } finally {
                try {
                    suppressor?.release()
                } catch (_: Exception) {
                }
                suppressor = null
                try {
                    rec?.stop(); rec?.release()
                } catch (_: Exception) {
                }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    /**
     * Some phones expose the noise cleaner as an effect on top of the
     * recording instead of in the source, so ask for that too. Missing is
     * normal and not worth a complaint in the log.
     */
    private fun openSuppressor(rec: AudioRecord, wanted: Boolean): NoiseSuppressor? {
        if (!wanted) return null
        return try {
            if (!NoiseSuppressor.isAvailable()) return null
            NoiseSuppressor.create(rec.audioSessionId)?.also { it.enabled = true }
        } catch (e: Exception) {
            Log.i(TAG, "no noise suppressor effect: ${e.message}")
            null
        }
    }

    private fun stopMic() {
        audioRunning.set(false)
        val thread = audioThread
        audioThread = null
        thread?.interrupt()
        // A read in flight is not interruptible, so wait for it to hand the
        // microphone back. Opening a second one on top would fail instead.
        try {
            thread?.join(1000)
        } catch (_: InterruptedException) {
        }
    }

    private fun handleSoundEvent(event: SoundDetector.Event) {
        var kind = when (event) {
            SoundDetector.Event.IMPACT -> "impact"
            SoundDetector.Event.CRY -> "cry"
            SoundDetector.Event.SOUND -> "sound"
        }
        // A cry only means anything if this camera is watching a baby.
        if (kind == "cry" && !settings.babyMode) kind = "sound"
        val detail = when (kind) {
            "impact" -> "A sudden thud - check right away."
            "cry" -> "Crying or fussing - sounds awake."
            else -> "Continuous sound in the room."
        }
        fireEvent(kind, detail, latestBitmap, 0)
    }

    // ======================================================================
    // The siren
    //
    // Somebody standing in the cabin at two in the morning does not expect
    // the phone on the shelf to start screaming at them with its torch
    // going. It is a deterrent, and it stops by itself - a siren nobody is
    // there to turn off is one that runs the battery flat and wakes the
    // neighbours until it does.
    // ======================================================================
    private var sirenThread: Thread? = null
    private val sirenOn = java.util.concurrent.atomic.AtomicBoolean(false)

    private fun setAlarm(on: Boolean) {
        if (on == sirenOn.get()) return
        sirenOn.set(on)
        torch(on)
        if (on) startSiren() else sirenThread = null
        pushState()
    }

    private fun startSiren() {
        handler.postDelayed({ if (sirenOn.get()) setAlarm(false) }, SIREN_MAX_MS)
        sirenThread = Thread {
            val rate = 22050
            val track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(rate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build()
                )
                .setBufferSizeInBytes(
                    AudioTrack.getMinBufferSize(
                        rate, AudioFormat.CHANNEL_OUT_MONO,
                        AudioFormat.ENCODING_PCM_16BIT
                    ).coerceAtLeast(4096) * 2
                )
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
            try {
                track.play()
                val block = ShortArray(rate / 10)
                var phase = 0.0
                var step = 0
                while (sirenOn.get()) {
                    // Two tones alternating - much harder to ignore than one.
                    val hz = if ((step / 3) % 2 == 0) 740.0 else 980.0
                    for (i in block.indices) {
                        phase += 2.0 * Math.PI * hz / rate
                        block[i] = (Math.sin(phase) * 12000).toInt().toShort()
                    }
                    track.write(block, 0, block.size)
                    step++
                }
            } catch (e: Exception) {
                Log.w(TAG, "siren failed: ${e.message}")
            } finally {
                try {
                    track.stop(); track.release()
                } catch (_: Exception) {
                }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    /** The torch, where this camera has one. A front camera will not. */
    private fun torch(on: Boolean) {
        try {
            val cam = camera ?: return
            if (cam.cameraInfo.hasFlashUnit()) cam.cameraControl.enableTorch(on)
        } catch (e: Exception) {
            Log.i(TAG, "no torch here: ${e.message}")
        }
    }

    private fun ensureAudioTrack(): AudioTrack? {
        audioTrack?.let { return it }
        return try {
            val minBuf = AudioTrack.getMinBufferSize(
                Protocol.AUDIO_RATE,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            ).coerceAtLeast(4096)
            val track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(Protocol.AUDIO_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build()
                )
                .setBufferSizeInBytes(minBuf * 2)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
            track.play()
            // Make sure your voice comes out of the loudspeaker, not the
            // earpiece, so it can be heard across the room.
            try {
                getSystemService(AudioManager::class.java).isSpeakerphoneOn = true
            } catch (_: Exception) {
            }
            audioTrack = track
            track
        } catch (e: Exception) {
            Log.w(TAG, "speaker unavailable: ${e.message}")
            null
        }
    }

    override fun onTalkAudio(pcm: ByteArray) {
        val track = ensureAudioTrack() ?: return
        try {
            track.write(pcm, 0, pcm.size)
        } catch (e: Exception) {
            Log.w(TAG, "playback failed: ${e.message}")
        }
    }

    // ======================================================================
    // Watching the device itself
    // ======================================================================
    private val motionWatcher = object : SensorEventListener {
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

        override fun onSensorChanged(event: SensorEvent) {
            val x = event.values[0]
            val y = event.values[1]
            val z = event.values[2]
            val force = kotlin.math.sqrt(x * x + y * y + z * z)

            // Learn what sitting still weighs, so it works whichever way up
            // the phone is propped.
            if (kotlin.math.abs(force - restForce) < 1.2f) {
                restForce = restForce * 0.98f + force * 0.02f
            }

            val now = System.currentTimeMillis()
            if (kotlin.math.abs(force - restForce) > 2.5f) {
                if (movingSince == 0L) movingSince = now
                // Held for a moment, so a slammed door does not count.
                if (now - movingSince > 900) {
                    movingSince = 0L
                    lastMovedAt = now
                    refreshLocation()
                    fireEvent("moved",
                        "This phone has been picked up or moved from where "
                            + "you left it.", latestBitmap, 0)
                }
            } else {
                movingSince = 0L
            }
        }
    }

    private fun startTamperWatch() {
        if (sensors != null) return
        try {
            val sm = getSystemService(SensorManager::class.java) ?: return
            val accel = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
            if (accel == null) {
                Log.i(TAG, "no accelerometer; cannot tell if this is moved")
                return
            }
            sm.registerListener(motionWatcher, accel,
                SensorManager.SENSOR_DELAY_NORMAL)
            sensors = sm
        } catch (e: Exception) {
            Log.w(TAG, "could not watch for movement: ${e.message}")
        }
    }

    private fun hasLocationPermission() =
        ContextCompat.checkSelfPermission(
            this, Manifest.permission.ACCESS_COARSE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED

    private fun refreshLocation() {
        if (!hasLocationPermission()) return
        try {
            val lm = getSystemService(LocationManager::class.java) ?: return
            // Last known is enough and costs no battery; this only has to
            // say roughly where the phone is, not navigate to it.
            for (p in listOf(LocationManager.GPS_PROVIDER,
                             LocationManager.NETWORK_PROVIDER)) {
                val fix = try {
                    lm.getLastKnownLocation(p)
                } catch (e: SecurityException) {
                    null
                }
                if (fix != null &&
                    (lastLocation == null || fix.time > lastLocation!!.time)
                ) {
                    lastLocation = fix
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "could not read the location: ${e.message}")
        }
    }

    private fun checkBattery() {
        val pct = batteryPercent() ?: return
        val charging = isCharging()
        if (!charging && pct <= 15 && !batteryWarned) {
            batteryWarned = true
            fireEvent("battery_low",
                "This camera is down to $pct% and is not charging. It will "
                    + "stop recording when it dies.", latestBitmap, 0)
        }
        if (charging || pct > 25) batteryWarned = false
    }

    // ======================================================================
    // Events
    // ======================================================================
    private fun fireEvent(kind: String, detail: String, frame: Bitmap?, rotation: Int) {
        val now = System.currentTimeMillis()
        // These are rare and serious, so they get their own, longer gap
        // rather than being throttled by whatever else is happening.
        val gap = when (kind) {
            "moved" -> 120_000L
            "battery_low" -> 1_800_000L
            else -> EVENT_MIN_GAP
        }
        if (now - (lastEventAt[kind] ?: 0L) < gap) return
        lastEventAt[kind] = now

        clips.markKeep(currentClipName)

        var thumb: String? = null
        if (frame != null && !frame.isRecycled) {
            val jpeg = encodeJpeg(frame, rotation, 360, 62, stamp = false)
            if (jpeg != null) thumb = Base64.encodeToString(jpeg, Base64.NO_WRAP)
        }

        Log.i(TAG, "event: $kind - $detail")
        ws?.sendJson(JSONObject().apply {
            put("t", "event")
            put("kind", kind)
            put("detail", detail)
            if (thumb != null) put("thumb", thumb)
            currentClipName?.let { put("clip", it) }
        })
    }

    // ======================================================================
    // Server connection
    // ======================================================================
    private fun startWebsocket() {
        if (ws != null) return
        ws = WsClient(
            settings.serverUrl, settings.token,
            settings.cameraId, settings.cameraName.ifEmpty { "Phone camera" },
            this
        ).also { it.start() }
    }

    override fun onConnected() {
        statusText = "connected"
        updateNotification()
        pushState()
    }

    override fun onDisconnected(reason: String) {
        streaming = false
        sendMic = false
        statusText = "reconnecting"
        updateNotification()
    }

    override fun onRefused(code: Int, explanation: String) {
        statusText = explanation
        updateNotification()
        Log.e(TAG, "refused ($code): $explanation")
    }

    override fun onCommand(msg: JSONObject) {
        when (msg.optString("t")) {
            "stream" -> {
                streaming = msg.optBoolean("on")
                if (streaming) {
                    streamWidth = msg.optInt("width", 480)
                    streamFps = msg.optDouble("fps", 4.0).toFloat()
                    streamQuality = msg.optInt("quality", 55)
                }
            }
            "audio" -> sendMic = msg.optBoolean("on")
            "talk" -> if (msg.optBoolean("on")) ensureAudioTrack()
            "alarm" -> handler.post { setAlarm(msg.optBoolean("on")) }
            "record" -> {
                val on = msg.optBoolean("on")
                paused = !on
                handler.post {
                    if (paused) {
                        manualKeep = false
                        stopSegment()
                        Log.i(TAG, "recording paused")
                    } else {
                        manualKeep = true
                        clips.markKeep(currentClipName)
                        if (canRecord && activeRecording == null) startSegment()
                        Log.i(TAG, "recording resumed")
                    }
                    pushState()
                }
            }
            // The filter is chosen when the microphone is opened, so it has
            // to be reopened for the change to take effect.
            // Only whole quarter turns, and only on a new segment - the
            // one being written keeps the shape it started with.
            "rotate" -> {
                settings.rotate = msg.optInt("value", 0)
                handler.post {
                    videoCapture?.targetRotation = targetRotationFor(settings.rotate)
                    motion.reset()
                    if (activeRecording != null) {
                        stopSegment()
                        if (canRecord) startSegment()
                    }
                    pushState()
                }
            }
            "noise_filter" -> {
                settings.noiseFilter = msg.optBoolean("on")
                stopMic()
                startMic()
                pushState()
            }
            "baby_mode" -> {
                settings.babyMode = msg.optBoolean("on"); pushState()
            }
            "arm" -> {
                settings.armed = msg.optBoolean("on"); pushState()
            }
            "lens" -> switchLens(msg.optString("id", null))
            "zoom" -> setZoom(msg.optDouble("value", 1.0))
            "config" -> applyConfig(msg.optJSONObject("values"))
            "list_clips" -> ws?.sendJson(JSONObject().apply {
                put("t", "clips")
                put("items", clips.list(excluding = currentClipName))
            })
            "get_clip" -> sendClip(msg.optString("file"), msg.optInt("xfer"))
            "cancel_clip" -> synchronized(cancelledXfers) {
                cancelledXfers.add(msg.optInt("xfer"))
            }
            "restart" -> handler.post {
                stopEverything()
                startService(Intent(this, CameraService::class.java))
            }
        }
    }

    private fun applyConfig(values: JSONObject?) {
        if (values == null) return
        if (values.has("retention_hours")) {
            settings.retentionHours = values.optInt("retention_hours")
        }
        if (values.has("max_mb")) settings.maxStorageMb = values.optInt("max_mb")
        if (values.has("sound_sensitivity")) {
            settings.soundSensitivity = values.optDouble("sound_sensitivity").toFloat()
            sound.sensitivity = settings.soundSensitivity
        }
        if (values.has("motion_sensitivity")) {
            settings.motionSensitivity = values.optDouble("motion_sensitivity").toFloat()
            motion.sensitivity = settings.motionSensitivity
        }
        pushState()
    }

    /** Stream one recording off this phone, in chunks, only when asked. */
    private fun sendClip(name: String?, xfer: Int) {
        Thread {
            val file: File? = clips.resolve(name)
            if (file == null) {
                ws?.sendJson(JSONObject().apply {
                    put("t", "clip_error"); put("xfer", xfer)
                    put("detail", "not found")
                })
                return@Thread
            }
            try {
                ws?.sendJson(JSONObject().apply {
                    put("t", "clip_start"); put("xfer", xfer)
                    put("size", file.length()); put("type", "video/mp4")
                })
                file.inputStream().use { input ->
                    val buf = ByteArray(48 * 1024)
                    while (true) {
                        synchronized(cancelledXfers) {
                            if (cancelledXfers.contains(xfer)) return@use
                        }
                        val n = input.read(buf)
                        if (n <= 0) break
                        // Back off when the socket is congested so a download
                        // never starves the live view.
                        while ((ws?.queuedBytes() ?: 0L) > 1_500_000L) {
                            Thread.sleep(60)
                        }
                        if (ws?.sendBinary(Protocol.packClip(xfer, buf, n)) != true) break
                    }
                }
                ws?.sendJson(JSONObject().apply {
                    put("t", "clip_end"); put("xfer", xfer)
                })
            } catch (e: Exception) {
                ws?.sendJson(JSONObject().apply {
                    put("t", "clip_error"); put("xfer", xfer)
                    put("detail", e.message ?: "failed")
                })
            } finally {
                synchronized(cancelledXfers) { cancelledXfers.remove(xfer) }
            }
        }.also { it.isDaemon = true }.start()
    }

    // ======================================================================
    // Heartbeat
    // ======================================================================
    private val stateTicker = object : Runnable {
        private var ticks = 0

        override fun run() {
            pushState()
            checkBattery()
            // Position changes slowly and costs battery to ask for, so it
            // is refreshed every few minutes rather than every tick.
            if (ticks++ % 150 == 0) refreshLocation()
            handler.postDelayed(this, 2000)
        }
    }

    private fun pushState() {
        val now = System.currentTimeMillis()
        val battery = batteryPercent()
        val status = when {
            recordingFailReason != null && statusText == "connected" -> "live, not recording"
            else -> statusText
        }
        ws?.sendJson(JSONObject().apply {
            put("t", "state")
            put("name", settings.cameraName.ifEmpty { "Phone camera" })
            put("status", status)
            put("recording", activeRecording != null)
            put("paused", paused)
            put("motion", motionOn)
            put("sound", sound.loud)
            put("audio_level", Math.round(sound.level * 1000.0) / 1000.0)
            put("baby_mode", settings.babyMode)
            put("noise_filter", settings.noiseFilter)
            put("rotate", settings.rotate)
            put("alarm", sirenOn.get())
            put("has_siren", true)
            put("armed", settings.armed)
            put("clips", clips.count())
            put("disk_free_gb", clips.freeGb())
            put("retention_hours", settings.retentionHours)
            put("quiet_for_s", if (lastMotionAt > 0) (now - lastMotionAt) / 1000 else JSONObject.NULL)
            put("silent_for_s", if (lastSoundAt > 0) (now - lastSoundAt) / 1000 else JSONObject.NULL)
            put("battery", battery ?: JSONObject.NULL)
            put("charging", isCharging())
            put("moved_ago_s",
                if (lastMovedAt > 0) (now - lastMovedAt) / 1000 else JSONObject.NULL)
            put("location", lastLocation?.let {
                JSONObject().apply {
                    put("lat", it.latitude)
                    put("lon", it.longitude)
                    put("accuracy_m", it.accuracy.toDouble())
                    put("at", it.time / 1000)
                }
            } ?: JSONObject.NULL)
            put("lenses", lensLabels)
            put("active_lens", settings.lensIndex.toString())
            put("zoom", (camera?.cameraInfo?.zoomState?.value?.zoomRatio
                ?: 1.0f).toDouble())
            put("has_audio", hasMicPermission())
            put("has_speaker", true)
            put("ptz", false)
        })
    }

    private fun batteryPercent(): Int? = try {
        getSystemService(android.os.BatteryManager::class.java)
            ?.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY)
            ?.takeIf { it in 0..100 }
    } catch (e: Exception) {
        null
    }

    private fun isCharging(): Boolean = try {
        getSystemService(android.os.BatteryManager::class.java)?.isCharging ?: false
    } catch (e: Exception) {
        false
    }

    companion object {
        private const val TAG = "CabinWatchService"
        private const val CHANNEL = "cabinwatch_camera"
        private const val NOTIF_ID = 1001
        private const val SEGMENT_MS = 60_000L

        /** A siren nobody turns off flattens the battery. Two minutes. */
        private const val SIREN_MAX_MS = 120_000L
        private const val EVENT_MIN_GAP = 25_000L
        const val ACTION_STOP = "com.cabinwatch.STOP"
    }
}
