package com.cabinwatch

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/**
 * Noticing that the camera has stopped, and starting it again.
 *
 * START_STICKY asks Android to bring the service back, and it usually
 * does - but "usually" is doing a lot of work in that sentence. Swipe the
 * app off the recents list, let a battery optimiser decide it has been
 * running too long, let a manufacturer's own cleaner have an opinion, and
 * the camera is simply gone. From the other end it looks like the room
 * stopped being watched at some point in the night, with nothing to say
 * when or why.
 *
 * So the phone checks on itself. An alarm every quarter of an hour asks
 * one question - should this be running? - and starts the service if the
 * answer is yes. It costs nothing while the service is alive, because
 * starting something already started does nothing.
 *
 * This is also the honest limit of what can be done without Firebase:
 * the phone can bring itself back, but nothing from outside can reach a
 * stopped app to tell it to.
 */
class Watchdog : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        val settings = Settings(context)
        schedule(context)                 // keep the chain going either way

        if (!settings.running || !settings.isConfigured()) return
        if (CameraService.isRunning) return

        Log.i(TAG, "the camera should be running and is not - starting it")
        val service = Intent(context, CameraService::class.java)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(service)
            } else {
                context.startService(service)
            }
        } catch (e: Exception) {
            // Android will not let a service start from the background on
            // newer versions unless it is already allowed to; the boot
            // receiver and the app itself cover that case.
            Log.w(TAG, "could not start it from here: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "CabinWatchWatchdog"
        private const val EVERY_MS = 15 * 60 * 1000L

        private fun pending(context: Context): PendingIntent {
            val intent = Intent(context, Watchdog::class.java)
            var flags = PendingIntent.FLAG_UPDATE_CURRENT
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                flags = flags or PendingIntent.FLAG_IMMUTABLE
            }
            return PendingIntent.getBroadcast(context, 0, intent, flags)
        }

        /** Ask to be woken in a quarter of an hour, and keep asking. */
        fun schedule(context: Context) {
            val alarms = context.getSystemService(Context.ALARM_SERVICE)
                    as? AlarmManager ?: return
            val at = System.currentTimeMillis() + EVERY_MS
            try {
                // Inexact on purpose: this is a safety net, not a stopwatch,
                // and an exact alarm every quarter hour would cost battery
                // for no benefit.
                alarms.set(AlarmManager.RTC_WAKEUP, at, pending(context))
            } catch (e: Exception) {
                Log.w(TAG, "could not set the check: ${e.message}")
            }
        }

        fun cancel(context: Context) {
            val alarms = context.getSystemService(Context.ALARM_SERVICE)
                    as? AlarmManager ?: return
            try {
                alarms.cancel(pending(context))
            } catch (_: Exception) {
            }
        }
    }
}
