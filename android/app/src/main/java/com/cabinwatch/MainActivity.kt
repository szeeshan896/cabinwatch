package com.cabinwatch

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings as AndroidSettings
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.core.CameraSelector
import androidx.core.content.ContextCompat
import com.cabinwatch.databinding.ActivityMainBinding

/**
 * The setup screen. You fill this in once per phone, press Start, and then
 * you can forget the app is there - the service carries on in the background
 * and comes back after a restart.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var b: ActivityMainBinding
    private lateinit var settings: Settings
    private var lenses: List<Pair<String, String>> = emptyList()

    private val retentionChoices = listOf(6, 12, 24, 48, 72)
    private val storageChoices = listOf(500, 1000, 2000, 4000, 8000, 16000)

    private val askPermissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { granted ->
        if (granted[Manifest.permission.CAMERA] == true) {
            loadLenses()
            startCamera()
        } else {
            toast("CabinWatch cannot work without camera access.")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)
        settings = Settings(this)

        b.spRetention.adapter = simpleAdapter(
            retentionChoices.map { if (it < 48) "$it hours" else "${it / 24} days" })
        b.spStorage.adapter = simpleAdapter(
            storageChoices.map { if (it < 1000) "$it MB" else "${it / 1000} GB" })

        restore()
        loadLenses()

        b.btnStart.setOnClickListener { onStart_() }
        b.btnStop.setOnClickListener { onStop_() }
        b.btnBattery.setOnClickListener { askBatteryExemption() }

        refreshRunningState()
    }

    override fun onResume() {
        super.onResume()
        refreshRunningState()
    }

    private fun simpleAdapter(items: List<String>) =
        ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, items)

    // ---- the form ----------------------------------------------------------
    private fun restore() {
        b.etServer.setText(settings.serverUrl)
        b.etToken.setText(settings.token)
        b.etName.setText(settings.cameraName)
        b.swBaby.isChecked = settings.babyMode
        b.swRecordAudio.isChecked = settings.recordAudio
        b.swBoot.isChecked = settings.startOnBoot
        b.spRetention.setSelection(
            retentionChoices.indexOf(settings.retentionHours).coerceAtLeast(1))
        b.spStorage.setSelection(
            storageChoices.indexOf(settings.maxStorageMb).coerceAtLeast(3))
    }

    private fun save(): Boolean {
        val server = b.etServer.text.toString().trim().trimEnd('/')
        val token = b.etToken.text.toString().trim()
        val name = b.etName.text.toString().trim()

        if (server.isEmpty() || token.isEmpty()) {
            toast("Enter your server address and token first.")
            return false
        }
        if (!server.startsWith("http://") && !server.startsWith("https://")) {
            toast("The server address must start with https://")
            return false
        }
        if (name.isEmpty()) {
            toast("Give this camera a name, so you know which one it is.")
            return false
        }

        settings.serverUrl = server
        settings.token = token
        settings.cameraName = name
        // Reading cameraId mints one on first use and then keeps it, so a
        // rename shows up as the same camera on the dashboard rather than a
        // second one appearing.
        settings.cameraId
        settings.babyMode = b.swBaby.isChecked
        settings.recordAudio = b.swRecordAudio.isChecked
        settings.startOnBoot = b.swBoot.isChecked
        settings.retentionHours = retentionChoices[b.spRetention.selectedItemPosition]
        settings.maxStorageMb = storageChoices[b.spStorage.selectedItemPosition]
        if (lenses.isNotEmpty()) {
            settings.lensIndex = b.spLens.selectedItemPosition
        }
        return true
    }

    // ---- cameras on this phone --------------------------------------------
    private fun loadLenses() {
        if (!has(Manifest.permission.CAMERA)) {
            b.spLens.adapter = simpleAdapter(listOf("Allow camera access first"))
            return
        }
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val infos = future.get().availableCameraInfos
                lenses = infos.mapIndexed { i, info ->
                    val facing = when (info.lensFacing) {
                        CameraSelector.LENS_FACING_FRONT -> "Front"
                        CameraSelector.LENS_FACING_BACK -> "Back"
                        else -> "Camera"
                    }
                    val many = infos.count { it.lensFacing == info.lensFacing } > 1
                    i.toString() to if (many) "$facing camera ${i + 1}" else "$facing camera"
                }
                b.spLens.adapter = simpleAdapter(lenses.map { it.second })
                b.spLens.setSelection(
                    settings.lensIndex.coerceIn(0, (lenses.size - 1).coerceAtLeast(0)))
                b.tvLensNote.text = if (lenses.size > 1)
                    "This phone has ${lenses.size} cameras. You can switch " +
                        "between them from the dashboard too, without coming back here."
                else
                    "This phone reports one camera."
            } catch (e: Exception) {
                b.spLens.adapter = simpleAdapter(listOf("Default camera"))
            }
        }, ContextCompat.getMainExecutor(this))
    }

    // ---- start / stop ------------------------------------------------------
    private fun onStart_() {
        val needed = buildList {
            if (!has(Manifest.permission.CAMERA)) add(Manifest.permission.CAMERA)
            if (!has(Manifest.permission.RECORD_AUDIO)) add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                !has(Manifest.permission.POST_NOTIFICATIONS)
            ) add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (!save()) return
        if (needed.isNotEmpty()) {
            askPermissions.launch(needed.toTypedArray())
            return
        }
        startCamera()
    }

    private fun startCamera() {
        if (!settings.isConfigured()) return
        settings.running = true
        val intent = Intent(this, CameraService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        refreshRunningState()
        toast("Camera started. You can close this app - it keeps running.")
        if (!batteryExempt()) {
            b.tvBatteryNote.text =
                "Android may still put this app to sleep. Tap the button below " +
                "once so it is left alone - without it, recording can stop " +
                "after a few hours."
        }
    }

    private fun onStop_() {
        settings.running = false
        startService(Intent(this, CameraService::class.java)
            .setAction(CameraService.ACTION_STOP))
        refreshRunningState()
        toast("Camera stopped.")
    }

    private fun refreshRunningState() {
        val on = settings.running
        b.tvStatus.text = if (on)
            "Running. This phone is acting as a camera, and will keep going " +
                "with the screen off."
        else
            "Not running."
        b.btnStart.isEnabled = !on
        b.btnStop.isEnabled = on
        b.btnBattery.visibility =
            if (batteryExempt()) android.view.View.GONE else android.view.View.VISIBLE
        if (batteryExempt()) b.tvBatteryNote.text = ""
    }

    // ---- being left alone by Android --------------------------------------
    private fun batteryExempt(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isIgnoringBatteryOptimizations(packageName)
    }

    private fun askBatteryExemption() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        try {
            startActivity(
                Intent(
                    AndroidSettings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName")
                )
            )
        } catch (e: Exception) {
            // Some phones hide that screen; send them to the app's settings.
            startActivity(
                Intent(
                    AndroidSettings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:$packageName")
                )
            )
        }
    }

    private fun has(p: String) =
        ContextCompat.checkSelfPermission(this, p) == PackageManager.PERMISSION_GRANTED

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()
}
