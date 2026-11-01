package com.compinche.wearables

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.SystemClock
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.math.sqrt

data class LiveUtterance(val id: String, val wav: ByteArray, val startSeconds: Double,
    val endSeconds: Double, val captureStartedAt: Double, val speechStartedAt: Double, val usedRealtime: Boolean, val answerTo: String? = null)

/** Local PCM/VAD fallback. The caller obtains RECORD_AUDIO permission first.
 * VAD detects speech activity, not speaker identity. TTS output is quarantined.
 */
class LiveAudio(
    private val scope: CoroutineScope,
    private val startedElapsedMs: Long,
    private val outputQuarantined: () -> Boolean,
    private val onSpeaking: (Boolean) -> Unit,
    private val onSpeechStart: (String, Double) -> Unit,
    private val onPcm: (ByteArray, Double, Boolean) -> Boolean,
    private val onUtterance: (LiveUtterance) -> Unit,
    private val onError: (String) -> Unit,
) {
    private var recorder: AudioRecord? = null
    private var echo: AcousticEchoCanceler? = null
    private var noise: NoiseSuppressor? = null
    private var job: Job? = null
    @Volatile private var active = false

    @SuppressLint("MissingPermission")
    fun start() {
        check(!active)
        val minimum = AudioRecord.getMinBufferSize(24000, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT)
        check(minimum > 0) { "El teléfono no admite PCM mono a 24 kHz." }
        val audio = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, 24000,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(minimum * 2, 7680))
        if (audio.state != AudioRecord.STATE_INITIALIZED) {
            audio.release()
            error("No se pudo iniciar el micrófono.")
        }
        recorder = audio
        if (AcousticEchoCanceler.isAvailable()) echo = AcousticEchoCanceler.create(audio.audioSessionId)?.apply { enabled = true }
        if (NoiseSuppressor.isAvailable()) noise = NoiseSuppressor.create(audio.audioSessionId)?.apply { enabled = true }
        val audioStartElapsedMs = SystemClock.elapsedRealtime()
        val audioStartWallSeconds = System.currentTimeMillis() / 1000.0
        audio.startRecording()
        active = true
        job = scope.launch(Dispatchers.IO) {
            val origin = (audioStartElapsedMs - startedElapsedMs) / 1000.0
            val buffer = ShortArray(960) // 40 ms, actual sample clock retained below.
            val preRoll = ArrayDeque<Pair<Long, ByteArray>>()
            var countSamples = 0L
            var noiseFloor = 120.0
            var warmup = 0
            var speaking = false
            var speechSamples = 0L
            var silenceSamples = 0L
            var utteranceStart = 0L
            var speechStart = 0L
            var utteranceId = ""
            var utteranceUsedRealtime = false
            var wasStreaming: Boolean? = null
            var discardUntilSilence = false
            var recoverySilence = 0L
            var utterance = ByteArrayOutputStream()
            var overflow = false
            try {
                while (isActive && active) {
                    val count = audio.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING)
                    if (count <= 0) {
                        if (active) error("El micrófono se interrumpió ($count).")
                        break
                    }
                    val at = countSamples
                    countSamples += count
                    val pcm = ByteBuffer.allocate(count * 2).order(ByteOrder.LITTLE_ENDIAN)
                    var squares = 0.0
                    for (i in 0 until count) { val sample = buffer[i]; squares += sample.toDouble() * sample; pcm.putShort(sample) }
                    val bytes = pcm.array()
                    val rms = sqrt(squares / count)
                    val quarantined = outputQuarantined()
                    if (!quarantined && !discardUntilSilence && warmup < 6) {
                        noiseFloor = noiseFloor * 0.5 + rms * 0.5; warmup++
                    }
                    val voiced = warmup >= 6 && rms > maxOf(350.0, noiseFloor * 2.7)
                    if (!quarantined && !discardUntilSilence && !speaking && voiced) {
                        utteranceId = UUID.randomUUID().toString()
                        onSpeechStart(utteranceId, audioStartWallSeconds + at / 24000.0)
                    }
                    val streamed = onPcm(bytes, origin + at / 24000.0, quarantined)
                    if (wasStreaming == true && !streamed) {
                        // Do not upload a partly streamed turn again: it may already have
                        // committed at the server. Fall back only AFTER a real silence.
                        discardUntilSilence = true
                        recoverySilence = 0
                        if (speaking) onSpeaking(false)
                        speaking = false
                        utterance.reset()
                        preRoll.clear()
                    }
                    wasStreaming = streamed
                    if (quarantined) {
                        if (speaking) onSpeaking(false)
                        speaking = false
                        speechSamples = 0
                        silenceSamples = 0
                        overflow = false
                        utterance.reset()
                        preRoll.clear()
                        continue
                    }
                    if (discardUntilSilence) {
                        recoverySilence = if (rms > maxOf(350.0, noiseFloor * 2.7)) 0 else recoverySilence + count
                        if (recoverySilence >= 24_000 * 0.65) discardUntilSilence = false
                        continue
                    }
                    if (!speaking) {
                        preRoll.addLast(at to bytes)
                        while (preRoll.size > 6) preRoll.removeFirst()
                        if (voiced) {
                            speaking = true
                            utteranceStart = preRoll.first().first
                            speechStart = at
                            utteranceUsedRealtime = streamed
                            preRoll.forEach { utterance.write(it.second) }
                            preRoll.clear()
                            speechSamples = count.toLong()
                            silenceSamples = 0
                            onSpeaking(true)
                        } else noiseFloor = noiseFloor * 0.985 + rms * 0.015
                    } else {
                        if (!overflow) utterance.write(bytes)
                        speechSamples += count
                        silenceSamples = if (voiced) 0 else silenceSamples + count
                        if (utterance.size() > 24_000 * 2 * 30) {
                            overflow = true
                            utterance.reset()
                        }
                        if (silenceSamples >= 24_000 * 0.65) {
                            if (overflow && !utteranceUsedRealtime) onError("La intervención superó 30 segundos. No se ha enviado una respuesta truncada; vuelve a explicarla en una intervención más breve.")
                            else if (!overflow && speechSamples - silenceSamples >= 24_000 * 0.25) {
                                val raw = utterance.toByteArray()
                                onUtterance(LiveUtterance(utteranceId, wav(raw),
                                    origin + utteranceStart / 24000.0,
                                    origin + utteranceStart / 24000.0 + raw.size / 48000.0,
                                    audioStartWallSeconds + utteranceStart / 24000.0,
                                    audioStartWallSeconds + speechStart / 24000.0, utteranceUsedRealtime))
                            }
                            onSpeaking(false)
                            speaking = false
                            overflow = false
                            speechSamples = 0
                            silenceSamples = 0
                            utterance.reset()
                        }
                    }
                }
            } catch (error: Exception) {
                if (active) onError(error.message ?: "Se interrumpió el micrófono.")
            } finally { onSpeaking(false) }
        }
    }

    fun stop() {
        active = false
        runCatching { recorder?.stop() }
        job?.cancel()
        job = null
        runCatching { recorder?.release() }
        recorder = null
        echo?.release(); echo = null
        noise?.release(); noise = null
    }

    companion object {
        fun wav(pcm: ByteArray): ByteArray = ByteBuffer.allocate(44 + pcm.size).order(ByteOrder.LITTLE_ENDIAN).apply {
            put("RIFF".toByteArray()); putInt(36 + pcm.size); put("WAVEfmt ".toByteArray()); putInt(16)
            putShort(1); putShort(1); putInt(24000); putInt(48000); putShort(2); putShort(16)
            put("data".toByteArray()); putInt(pcm.size); put(pcm)
        }.array()
    }
}
