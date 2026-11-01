package com.compinche.wearables

import android.content.Context
import android.media.MediaPlayer
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Base64
import com.meta.wearable.dat.mockdevice.MockDeviceKit
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.util.Locale
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

data class LiveBridgeState(
    val starting: Boolean = false, val active: Boolean = false, val paused: Boolean = false,
    val liveId: String? = null, val sessionId: String? = null,
    val question: String? = null, val questionId: String? = null,
    val sourceSpeaking: Boolean = false, val agentSpeaking: Boolean = false,
    val framesSent: Long = 0, val sdkPresentationTimeUs: Long? = null,
    val cards: Int = 0, val metrics: String = "", val message: String = "Listo para acompañar la captura.",
)

/** Foreground live learning over the SAME SDK preview/recorder as CameraAccess.
 * Sends only actual decoded frames and captured microphone utterances, never the
 * mock input file as if it were a frame. Transport is loopback via adb reverse.
 */
class LiveBridge(
    private val context: Context,
    private val scope: CoroutineScope,
    private val snapshot: suspend () -> PreviewSnapshot?,
    private val onStarted: () -> Unit,
    private val onStopping: () -> Unit,
    private val onStopped: suspend (String, String) -> Unit,
) {
    private val mutableState = MutableStateFlow(LiveBridgeState())
    val state = mutableState.asStateFlow()
    private var captureJob: Job? = null
    private var pollJob: Job? = null
    private var sendJob: Job? = null
    private var audioJob: Job? = null
    private var microphone: LiveAudio? = null
    @Volatile private var pcmStream: LivePcmStream? = null
    private var observations = Channel<JSONObject>(Channel.CONFLATED)
    private var utterances = Channel<LiveUtterance>(4)
    private var startedElapsedMs = 0L
    private var generation = 0L
    private var speakerName = ""
    private var speakerRole = "observer"
    private var sourceKind = "meta_dat_mock"
    @Volatile private var questionAtSpeechStart: String? = null
    @Volatile private var microphoneUtteranceId: String? = null
    @Volatile private var microphoneSpeechStartedAt: Double? = null
    private val pendingWav = AtomicInteger(0)
    private var player: MediaPlayer? = null
    @Volatile private var voiceReady = false
    @Volatile private var outputActive = false
    @Volatile private var outputQuestionId: String? = null
    @Volatile private var quarantineUntilMs = 0L
    private val spokenIds = mutableSetOf<String>()
    private val tts = TextToSpeech(context.applicationContext) { status ->
        voiceReady = status == TextToSpeech.SUCCESS
    }

    init {
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) { if (utteranceId == outputQuestionId) utteranceId?.let { playbackStarted(it) } }
            override fun onDone(utteranceId: String?) { if (utteranceId == outputQuestionId) playbackEnded() }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                if (utteranceId != outputQuestionId) return
                playbackEnded()
                update { it.copy(message = "No se pudo leer la pregunta; sigue disponible en pantalla.") }
            }
            override fun onStop(utteranceId: String?, interrupted: Boolean) { if (utteranceId == outputQuestionId) playbackEnded() }
        })
    }

    private fun update(change: (LiveBridgeState) -> LiveBridgeState) {
        synchronized(mutableState) { mutableState.value = change(mutableState.value) }
    }

    fun start(name: String, role: String, teacher: String) {
        if (state.value.starting || state.value.active) return
        require(name.isNotBlank() && role in setOf("cook", "wearer", "observer"))
        val epoch = ++generation
        update { LiveBridgeState(starting = true, message = "Conectando la sesión de aprendizaje…") }
        scope.launch {
            var createdId: String? = null
            try {
                sourceKind = if (MockDeviceKit.getInstance(context).isEnabled) "meta_dat_mock" else "meta_dat_device"
                val created = json("/api/live", JSONObject().put("consent", true)
                    .put("source_kind", sourceKind).put("teacher", teacher.ifBlank { "Persona que cocina" })
                    .put("speaker_name", name.trim()).put("speaker_role", role).put("mode", "record"))
                createdId = safeId(created.getString("id"))
                val sessionId = safeId(created.getString("session_id"))
                if (generation != epoch) {
                    withContext(NonCancellable) { json("/api/live/$createdId/control", JSONObject().put("action", "stop")) }
                    return@launch
                }
                speakerName = name.trim(); speakerRole = role
                val stream = LivePcmStream(createdId, speakerName, speakerRole,
                    metadata = { JSONObject().put("answer_to", questionAtSpeechStart)
                        .put("utterance_id", microphoneUtteranceId).put("speech_started_at", microphoneSpeechStartedAt)
                        .put("agent_speaking", outputActive).put("human_speaking", state.value.sourceSpeaking) },
                    onFailure = { message -> update { it.copy(message = message) } })
                pcmStream = stream
                val realtime = stream.open()
                if (generation != epoch) {
                    stream.close()
                    withContext(NonCancellable) { json("/api/live/$createdId/control", JSONObject().put("action", "stop")) }
                    return@launch
                }
                startedElapsedMs = SystemClock.elapsedRealtime()
                spokenIds.clear()
                pendingWav.set(0)
                microphoneUtteranceId = null; microphoneSpeechStartedAt = null; questionAtSpeechStart = null
                observations = Channel(Channel.CONFLATED)
                utterances = Channel(4)
                update { LiveBridgeState(active = true, liveId = createdId, sessionId = sessionId,
                    message = if (realtime) "Observando el SDK y transcribiendo voz continua. El original se graba en el teléfono."
                    else "Observando el SDK y escuchando por turnos. La voz continua no está conectada; el original se graba en el teléfono.") }
                onStarted()
                startLanes(createdId, epoch)
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                val currentRun = generation == epoch
                if (currentRun) stop()
                createdId?.let { id -> runCatching { json("/api/live/$id/control", JSONObject().put("action", "stop")) } }
                if (currentRun)
                    update { it.copy(starting = false, active = false, message = error.message ?: "No se pudo iniciar la sesión.") }
            }
        }
    }

    private fun startLanes(id: String, epoch: Long) {
        sendJob = scope.launch {
            for (payload in observations) {
                try {
                    json("/api/live/$id/observe", payload)
                    update { it.copy(framesSent = it.framesSent + payload.getJSONArray("frames").length()) }
                } catch (error: Exception) { if (error is CancellationException) throw error else report(error) }
            }
        }
        audioJob = scope.launch {
            for (utterance in utterances) {
                try {
                    var failure: Exception? = null
                    for (attempt in 0..2) {
                        try { uploadUtterance(id, utterance); failure = null; break }
                        catch (error: Exception) {
                            if (error is CancellationException) throw error
                            failure = error
                            if (error is LocalHttpFailure && error.status !in setOf(408, 409, 429, 500, 502, 503, 504)) break
                            delay(250L * (attempt + 1))
                        }
                    }
                    failure?.let { throw it }
                }
                catch (error: Exception) { if (error is CancellationException) throw error else report(error) }
                finally {
                    if (generation == epoch && pendingWav.decrementAndGet() <= 0 && !state.value.sourceSpeaking)
                        control("speaking", JSONObject().put("speaking", false).put("channel", "human"))
                }
            }
        }
        captureJob = scope.launch {
            val recent = ArrayDeque<JSONObject>()
            var sequence = 0L
            var lastSend = 0L
            while (isActive && generation == epoch) {
                if (!state.value.paused) {
                    val frame = snapshot()
                    if (frame != null) {
                        val seconds = (frame.capturedAtElapsedMs - startedElapsedMs) / 1000.0
                        val item = JSONObject().put("jpeg_base64", Base64.encodeToString(frame.jpeg, Base64.NO_WRAP))
                            .put("source_seconds", maxOf(0.0, seconds))
                        frame.sdkPresentationTimeUs?.let { item.put("sdk_presentation_time_us", it) }
                        recent.addLast(item)
                        while (recent.size > 4) recent.removeFirst()
                        update { it.copy(sdkPresentationTimeUs = frame.sdkPresentationTimeUs) }
                        if (SystemClock.elapsedRealtime() - lastSend >= 1500) {
                            observations.trySend(JSONObject().put("sequence", ++sequence).put("scene_epoch", 0)
                                .put("frames", JSONArray(recent.toList()))
                                .put("source_speaking", state.value.sourceSpeaking)
                                .put("agent_speaking", outputActive))
                            lastSend = SystemClock.elapsedRealtime()
                        }
                    }
                } else recent.clear()
                delay(750)
            }
        }
        microphone = LiveAudio(scope, startedElapsedMs,
            outputQuarantined = { outputActive || state.value.paused || SystemClock.elapsedRealtime() < quarantineUntilMs },
            onSpeaking = { speaking ->
                update { it.copy(sourceSpeaking = speaking) }
                if (!speaking) { microphoneUtteranceId = null; microphoneSpeechStartedAt = null }
                if (speaking || pendingWav.get() == 0)
                    control("speaking", JSONObject().put("speaking", speaking).put("channel", "human"))
            },
            onSpeechStart = { utteranceId, startedAt ->
                questionAtSpeechStart = state.value.questionId
                microphoneUtteranceId = utteranceId; microphoneSpeechStartedAt = startedAt
            },
            onPcm = { pcm, seconds, quarantined -> pcmStream?.send(pcm, seconds, quarantined) ?: false },
            onUtterance = { utterance ->
                if (!utterance.usedRealtime) {
                    pendingWav.incrementAndGet()
                    if (!utterances.trySend(utterance.copy(answerTo = questionAtSpeechStart)).isSuccess) {
                        pendingWav.decrementAndGet()
                        update { it.copy(message = "El envío de audio está ocupado. La intervención no se ha marcado como respondida; vuelve a intentarlo.") }
                    }
                }
            }, onError = { message -> update { it.copy(message = message) } }).also { it.start() }
        pollJob = scope.launch {
            var after = 0
            while (isActive && generation == epoch) {
                try {
                    val snapshot = json("/api/live/$id?after=$after")
                    val events = snapshot.optJSONArray("events") ?: JSONArray()
                    for (i in 0 until events.length()) {
                        val event = events.getJSONObject(i)
                        after = maxOf(after, event.optInt("id"))
                        if (event.optString("type") == "speech_cancel") stopOutput()
                    }
                    val paused = snapshot.optString("status") == "paused"
                    update { it.copy(paused = paused) }
                    if (paused) stopOutput()
                    val question = snapshot.optJSONObject("question")
                    val qid = question?.optString("id")?.takeIf { it.isNotBlank() }
                    val qtext = question?.optString("text")?.takeIf { it.isNotBlank() }
                    val status = question?.optString("status")
                    update { it.copy(question = qtext, questionId = qid,
                        cards = snapshot.optJSONArray("cards")?.length() ?: 0,
                        metrics = snapshot.optJSONObject("metrics")?.toString()?.take(1200) ?: "") }
                    if (qid != null && qtext != null && status in setOf("pending", "ready") &&
                        !state.value.paused && !state.value.sourceSpeaking && spokenIds.add(qid)) speak(id, qid, qtext)
                    if (question == null || status in setOf("cancelled", "expired", "answered", "postponed")) stopOutput()
                    if (snapshot.optString("status") in setOf("stopped", "closed", "expired")) {
                        stop(); return@launch
                    }
                } catch (error: Exception) { if (error is CancellationException) throw error else report(error) }
                delay(800)
            }
        }
    }

    private fun speak(id: String, questionId: String, text: String) {
        stopOutput()
        val epoch = generation
        outputActive = true // Quarantine preparation too; no echo can enter an utterance.
        outputQuestionId = questionId
        val media = MediaPlayer()
        player = media
        media.apply {
            setOnPreparedListener { ready ->
                if (generation != epoch || !state.value.active || player !== ready ||
                    state.value.questionId != questionId || state.value.paused) {
                    ready.release(); return@setOnPreparedListener
                }
                try { ready.start(); playbackStarted(questionId) }
                catch (_: Exception) { ready.release(); player = null; speakFallback(questionId, text) }
            }
            setOnCompletionListener { completed ->
                if (player === completed) { player = null; playbackEnded() }
                completed.release()
            }
            setOnErrorListener { failed, _, _ ->
                val current = player === failed
                failed.release()
                if (current) {
                    player = null
                    if (generation == epoch && state.value.active) speakFallback(questionId, text)
                    else if (generation == epoch) playbackEnded()
                }
                true
            }
            try {
                setDataSource("${CompincheClient.BASE}/api/live/${safeId(id)}/questions/${safeId(questionId)}/voice")
                prepareAsync()
            } catch (_: Exception) { release(); player = null; speakFallback(questionId, text) }
        }
    }

    private fun speakFallback(questionId: String, text: String) {
        if (!state.value.active || state.value.paused || state.value.questionId != questionId) { playbackEnded(); return }
        if (!voiceReady) { playbackEnded(); update { it.copy(message = "La voz del teléfono no está disponible. La pregunta sigue en pantalla.") }; return }
        tts.language = Locale.forLanguageTag("es-ES")
        outputActive = true
        if (tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, questionId) == TextToSpeech.ERROR) playbackEnded()
    }

    private fun playbackStarted(questionId: String) {
        outputActive = true
        update { it.copy(agentSpeaking = true) }
        val id = state.value.liveId ?: return
        scope.launch {
            try {
                json("/api/live/$id/control", JSONObject().put("action", "spoken").put("question_id", questionId))
                if (outputActive && outputQuestionId == questionId)
                    json("/api/live/$id/control", JSONObject().put("action", "speaking").put("channel", "agent").put("speaking", true))
            } catch (error: Exception) { if (error is CancellationException) throw error else report(error) }
        }
    }

    private fun playbackEnded() {
        outputActive = false
        quarantineUntilMs = SystemClock.elapsedRealtime() + 650
        update { it.copy(agentSpeaking = false) }
        control("speaking", JSONObject().put("speaking", false).put("channel", "agent"))
    }

    private fun stopOutput() {
        outputQuestionId = null
        player?.let { runCatching { it.stop() }; runCatching { it.release() } }; player = null
        tts.stop()
        if (outputActive) playbackEnded()
    }

    fun interrupt() { stopOutput(); control("barge_in") }
    fun postpone() { stopOutput(); control("postpone", JSONObject().put("question_id", state.value.questionId)) }
    fun pauseOrResume() {
        val pause = !state.value.paused
        if (pause) stopOutput()
        update { it.copy(paused = pause) }
        control(if (pause) "pause" else "resume")
    }

    fun stop() {
        val before = state.value
        if (!before.active && !before.starting) return
        generation++
        onStopping()
        microphone?.stop(); microphone = null
        pcmStream?.close(); pcmStream = null
        captureJob?.cancel(); pollJob?.cancel(); sendJob?.cancel(); audioJob?.cancel()
        observations.close(); utterances.close()
        stopOutput()
        update { it.copy(starting = false, active = false, sourceSpeaking = false,
            agentSpeaking = false, message = "Sesión detenida. Conservando la grabación original…") }
        val stoppedSource = sourceKind
        before.liveId?.let { id -> scope.launch {
            val serverStop = async { runCatching { json("/api/live/$id/control", JSONObject().put("action", "stop")) } }
            before.sessionId?.let { sid ->
                try { onStopped(sid, stoppedSource); update { it.copy(message = "Sesión terminada. El original y las fuentes quedan disponibles para revisar.") } }
                catch (error: Exception) { if (error is CancellationException) throw error else report(error) }
            }
            serverStop.await()
        } }
    }

    fun retryArchives() {
        scope.launch {
            try {
                val count = LiveArchive.retryPending(context)
                update { it.copy(message = "Copias originales guardadas en Compinche: $count.") }
            } catch (error: Exception) { if (error is CancellationException) throw error else report(error) }
        }
    }

    fun close() {
        stop()
        microphone?.stop()
        player?.release(); player = null
        tts.shutdown()
    }

    fun control(action: String, extra: JSONObject = JSONObject()) {
        val id = state.value.liveId ?: return
        scope.launch {
            try { json("/api/live/$id/control", JSONObject(extra.toString()).put("action", action)) }
            catch (error: Exception) { if (error is CancellationException) throw error else if (state.value.active) report(error) }
        }
    }

    private fun report(error: Exception) = update { it.copy(message = error.message ?: "La conexión local se interrumpió; la cámara no se ha inventado una respuesta.") }

    private fun safeId(id: String): String {
        require(id.matches(Regex("[a-zA-Z0-9_-]{1,120}")))
        return id
    }

    private fun connect(path: String): HttpURLConnection {
        require(path.startsWith("/api/live") && !path.contains(".."))
        return (URI(CompincheClient.BASE + path).toURL().openConnection() as HttpURLConnection).apply {
            connectTimeout = 5000; readTimeout = 15000; instanceFollowRedirects = false
        }
    }

    private class LocalHttpFailure(val status: Int, message: String) : IllegalStateException(message)

    private fun read(connection: HttpURLConnection): JSONObject {
        val status = connection.responseCode
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val output = ByteArrayOutputStream()
        stream?.use { input ->
            val buffer = ByteArray(8192)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                check(output.size() + count <= 2_000_000) { "Respuesta local demasiado grande." }
                output.write(buffer, 0, count)
            }
        }
        val result = runCatching { JSONObject(output.toString("UTF-8")) }.getOrDefault(JSONObject())
        if (status !in 200..299) throw LocalHttpFailure(status,
            result.optJSONObject("detail")?.optString("message")?.takeIf { it.isNotBlank() }
                ?: "Compinche respondió $status. Comprueba npm run demo y adb reverse.")
        return result
    }

    private suspend fun json(path: String, body: JSONObject? = null): JSONObject = withContext(Dispatchers.IO) {
        val connection = connect(path)
        try {
            if (body != null) {
                connection.requestMethod = "POST"; connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                connection.setFixedLengthStreamingMode(bytes.size)
                connection.outputStream.use { it.write(bytes) }
            }
            read(connection)
        } finally { connection.disconnect() }
    }

    private suspend fun uploadUtterance(id: String, utterance: LiveUtterance) = withContext(Dispatchers.IO) {
        val boundary = "compinche-${UUID.randomUUID()}"
        val fields = linkedMapOf("utterance_id" to utterance.id, "start_seconds" to utterance.startSeconds.toString(),
            "end_seconds" to utterance.endSeconds.toString(), "speaker_name" to speakerName,
            "speaker_role" to speakerRole, "origin" to "microphone",
            "capture_started_at" to utterance.captureStartedAt.toString(),
            "speech_started_at" to utterance.speechStartedAt.toString(), "scene_epoch" to "0")
        utterance.answerTo?.let { fields["answer_to"] = it }
        val header = buildString {
            for ((key, value) in fields) append("--$boundary\r\nContent-Disposition: form-data; name=\"$key\"\r\n\r\n$value\r\n")
            append("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"utterance.wav\"\r\nContent-Type: audio/wav\r\n\r\n")
        }.toByteArray(Charsets.UTF_8)
        val tail = "\r\n--$boundary--\r\n".toByteArray()
        val connection = connect("/api/live/$id/audio")
        try {
            connection.requestMethod = "POST"; connection.doOutput = true
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            connection.setFixedLengthStreamingMode(header.size + utterance.wav.size + tail.size)
            connection.outputStream.use { it.write(header); it.write(utterance.wav); it.write(tail) }
            read(connection)
        } finally { connection.disconnect() }
    }
}
