package com.compinche.wearables

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject

/** Local PCM socket; API credentials remain in the Compinche server. */
class LivePcmStream(
    private val liveId: String,
    private val speaker: String,
    private val role: String,
    private val metadata: () -> JSONObject,
    private val onFailure: (String) -> Unit,
) {
    private val readyResult = CompletableDeferred<Boolean>()
    private val client = OkHttpClient.Builder().connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS).pingInterval(20, TimeUnit.SECONDS).build()
    private var socket: WebSocket? = null
    @Volatile var ready: Boolean = false
        private set
    @Volatile private var closed = false
    @Volatile private var reportedFailure = false

    suspend fun open(): Boolean {
        require(liveId.matches(Regex("[a-zA-Z0-9_-]{1,120}")))
        val request = Request.Builder().url("ws://127.0.0.1:8017/api/live/$liveId/audio-stream").build()
        socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject().put("origin", "microphone").put("speaker_name", speaker)
                    .put("speaker_role", role).put("sample_rate", 24000).put("source_seconds", 0)
                    .put("scene_epoch", 0).toString())
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                val event = runCatching { JSONObject(text) }.getOrNull() ?: return
                when (event.optString("type")) {
                    "ready" -> if (!closed) { ready = true; readyResult.complete(true) }
                    "error" -> {
                        val message = event.optString("message", "El canal continuo no pudo seguir.")
                        if (event.optBoolean("fallback", true)) failed(message) else onFailure(message)
                    }
                }
            }
            override fun onFailure(webSocket: WebSocket, error: Throwable, response: Response?) {
                failed("La voz continua se interrumpió. Tras una pausa, repite la última explicación; las siguientes se enviarán por turnos.")
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
                if (!closed) failed("El canal continuo se cerró. Tras una pausa, se usará el envío por turnos.")
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { ready = false }
        })
        val connected = withTimeoutOrNull(6000) { readyResult.await() } ?: false
        if (!connected) close()
        return connected
    }

    fun send(pcm: ByteArray, sourceSeconds: Double, quarantined: Boolean): Boolean {
        val current = socket ?: return false
        if (!ready || closed) return false
        // Never accumulate seconds of speech behind a slow local transport.
        if (current.queueSize() > 96_000) {
            failed("La voz se retrasó. Tras una pausa, repite la explicación; se usará el envío por turnos.")
            return false
        }
        val clock = metadata().put("type", "clock").put("source_seconds", sourceSeconds)
            .put("scene_epoch", 0)
        val raw = if (quarantined) ByteArray(pcm.size) else pcm
        val queued = current.send(clock.toString()) && current.send(raw.toByteString())
        if (!queued) failed("La voz continua no pudo enviarse; tras una pausa se usará el envío por turnos.")
        return queued
    }

    private fun failed(message: String) {
        val wasReady = ready
        ready = false
        readyResult.complete(false)
        if (!closed && wasReady && !reportedFailure) { reportedFailure = true; onFailure(message) }
        socket?.cancel()
    }

    fun close() {
        closed = true; ready = false
        socket?.send(JSONObject().put("type", "end").toString())
        socket?.close(1000, "session ended")
        socket = null
        readyResult.complete(false)
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
    }
}
