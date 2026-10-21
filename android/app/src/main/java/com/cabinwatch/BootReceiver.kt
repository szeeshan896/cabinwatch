package com.cabinwatch

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/**
 * Brings the camera back after the phone restarts.
 *
 * This matters more than it sounds: a phone left charging in a child's room
 * will reboot for an update sooner or later, and a camera that silently does
 * not come back is worse than no camera, because you think you are covered.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != "android.intent.action.QUICKBOOT_POWERON"
        ) return

        val settings = Settings(context)
        if (!settings.startOnBoot || !settings.running || !settings.isConfigured()) {
            return
        }

        Log.i("CabinWatchBoot", "restarting the camera after boot")
        val service = Intent(context, CameraService::class.java)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(service)
            } else {
                context.startService(service)
            }
        } catch (e: Exception) {
            Log.w("CabinWatchBoot", "could not restart: ${e.message}")
        }
    }
}
