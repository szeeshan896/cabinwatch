package com.cabinwatch

/**
 * The wire protocol, mirroring server/protocol.py and the browser camera page.
 *
 * Everything for one camera travels over a single WebSocket. Text messages are
 * JSON control messages; binary messages are tagged by their first byte so
 * video, audio and file transfers can share the socket.
 *
 *   0x01  <jpeg bytes>                    video frame
 *   0x02  <pcm16le mono 16 kHz>           audio, either direction
 *   0x03  <xfer:uint32 be> <chunk>        a slice of a recording being pulled
 *                                         off this phone
 */
object Protocol {
    const val BIN_VIDEO: Byte = 0x01
    const val BIN_AUDIO: Byte = 0x02
    const val BIN_CLIP: Byte = 0x03

    /** Raw PCM both ways: no codec needed on either end. ~32 KB/s. */
    const val AUDIO_RATE = 16000
    const val AUDIO_CHUNK_SAMPLES = AUDIO_RATE * 60 / 1000   // 60 ms

    fun packVideo(jpeg: ByteArray): ByteArray = tag(BIN_VIDEO, jpeg)

    fun packAudio(pcm: ByteArray): ByteArray = tag(BIN_AUDIO, pcm)

    fun packClip(xferId: Int, chunk: ByteArray, length: Int = chunk.size): ByteArray {
        val out = ByteArray(5 + length)
        out[0] = BIN_CLIP
        out[1] = (xferId ushr 24).toByte()
        out[2] = (xferId ushr 16).toByte()
        out[3] = (xferId ushr 8).toByte()
        out[4] = xferId.toByte()
        System.arraycopy(chunk, 0, out, 5, length)
        return out
    }

    private fun tag(t: Byte, payload: ByteArray): ByteArray {
        val out = ByteArray(1 + payload.size)
        out[0] = t
        System.arraycopy(payload, 0, out, 1, payload.size)
        return out
    }

    /** Pulls the payload out of a binary frame the server sent us. */
    fun payloadOf(data: ByteArray): ByteArray =
        if (data.size <= 1) ByteArray(0) else data.copyOfRange(1, data.size)
}
