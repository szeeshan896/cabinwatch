package com.cabinwatch

import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The connection to your server.
 *
 * It dials OUT, which is the whole reason this phone can sit on any WiFi or on
 * mobile data with nothing forwarded on the router. If the connection drops -
 * the phone roams, the WiFi reconnects, the server redeploys - it backs off
 * and tries again on its own, forever, because a camera that gives up after a
 * blip is no camera at all.
 */
class WsClient(
    private val serverUrl: String,
    private val token: String,
    private val cameraId: String,
    private val cameraName: String,
    private val listener: Events
) {
    interface Events {
        fun onConnected()
        fun onDisconnected(reason: String)
        /** A JSON control message from the server. */
        fun onCommand(msg: JSONObject)
        /** Your voice, on its way to this phone's speaker. */
        fun onTalkAudio(pcm: ByteArray)
        /** Refused on the handshake: wrong token, or a bad camera id. */
        fun onRefused(code: Int, explanation: String)
    }

    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .connectTimeout(15, TimeUnit.SECONDS)
        .build()

    private var socket: WebSocket? = null
    private val stopped = AtomicBoolean(false)
    private var backoffMs = 2000L
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())

    @Volatile
    var connected = false
        private set

    fun url(): String {
        val base = when {
            serverUrl.startsWith("https://") -> "wss://" + serverUrl.removePrefix("https://")
            serverUrl.startsWith("http://") -> "ws://" + serverUrl.removePrefix("http://")
            serverUrl.startsWith("ws://") || serverUrl.startsWith("wss://") -> serverUrl
            else -> "wss://$serverUrl"
        }.trimEnd('/')
        return base + "/ws/camera" +
                "?token=" + enc(token) +
                "&id=" + enc(cameraId) +
                "&name=" + enc(cameraName) +
                "&kind=agent"
    }

    private fun enc(s: String) = java.net.URLEncoder.encode(s, "UTF-8")

    fun start() {
        stopped.set(false)
        connect()
    }

    fun stop() {
        stopped.set(true)
        handler.removeCallbacksAndMessages(null)
        try {
            socket?.close(1000, "stopping")
        } catch (_: Exception) {
        }
        socket = null
        connected = false
    }

    private fun connect() {
        if (stopped.get()) return
        val request = Request.Builder().url(url()).build()
        socket = http.newWebSocket(request, object : WebSocketListener() {

            override fun onOpen(ws: WebSocket, response: Response) {
                connected = true
                backoffMs = 2000L
                Log.i(TAG, "connected to $serverUrl")
                listener.onConnected()
            }

            override fun onMessage(ws: WebSocket, text: String) {
                try {
                    listener.onCommand(JSONObject(text))
                } catch (e: Exception) {
                    Log.w(TAG, "bad control message: ${e.message}")
                }
            }

            override fun onMessage(ws: WebSocket, bytes: ByteString) {
                val data = bytes.toByteArray()
                if (data.isNotEmpty() && data[0] == Protocol.BIN_AUDIO) {
                    listener.onTalkAudio(Protocol.payloadOf(data))
                }
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                connected = false
                val code = response?.code ?: 0
                if (code == 403) {
                    // Refused during the handshake, so there is no close code
                    // to read. Say which secret is wrong rather than looping
                    // in silence - "reconnecting" forever tells nobody
                    // anything.
                    listener.onRefused(403, REFUSED_TOKEN)
                } else if (code == 400 || code == 404) {
                    listener.onRefused(code, REFUSED_ID)
                } else {
                    listener.onDisconnected(t.message ?: "connection failed")
                }
                scheduleRetry()
            }

            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                connected = false
                listener.onDisconnected("closed ($code)")
                scheduleRetry()
            }
        })
    }

    private fun scheduleRetry() {
        if (stopped.get()) return
        val wait = backoffMs
        backoffMs = (backoffMs * 2).coerceAtMost(30_000L)
        handler.postDelayed({ connect() }, wait)
    }

    // ---- sending -----------------------------------------------------------
    fun sendJson(obj: JSONObject): Boolean =
        try {
            socket?.send(obj.toString()) ?: false
        } catch (e: Exception) {
            false
        }

    fun sendBinary(data: ByteArray): Boolean =
        try {
            socket?.send(data.toByteString()) ?: false
        } catch (e: Exception) {
            false
        }

    /** How much is waiting to go out, so we can back off when congested. */
    fun queuedBytes(): Long = socket?.queueSize() ?: 0L

    companion object {
        private const val TAG = "WsClient"
        const val REFUSED_TOKEN =
            "The server did not accept the token. It must match the " +
            "AGENT_TOKEN variable on your server exactly."
        const val REFUSED_ID =
            "The camera name produced an id the server will not take. " +
            "Use plain letters and numbers."
    }
}
