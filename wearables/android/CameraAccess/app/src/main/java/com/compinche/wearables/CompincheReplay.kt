package com.compinche.wearables

import android.content.Context
import android.net.Uri
import com.meta.wearable.dat.camera.Camera
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.VideoQuality
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.mockdevice.MockDeviceKit
import com.meta.wearable.dat.mockdevice.api.GlassesModel
import com.meta.wearable.dat.mockdevice.api.MockGlasses
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.util.UUID
import java.security.MessageDigest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

/** DAT 1.0.1 camera replay, explicitly simulated and independent of Meta API keys.
 * Integrate into an Android host (for example Meta's CameraAccess sample).
 * onFrame receives a COPY of the actual SDK buffer, not an invented preview.
 */
class CompincheReplay(context: Context, private val scope: CoroutineScope) {
    data class Frame(val bytes: ByteArray, val width: Int, val height: Int,
                     val presentationTimeUs: Long, val compressed: Boolean, val codecConfig: Boolean)

    private val kit = MockDeviceKit.getInstance(context.applicationContext)
    private var glasses: MockGlasses? = null
    private var session: DeviceSession? = null
    private var camera: Camera? = null
    private var frames: Job? = null
    private var errors: Job? = null

    suspend fun start(video: Uri, onFrame: (Frame) -> Unit, onError: (String) -> Unit) {
        check(session == null) { "Stop the previous replay first" }
        try {
            kit.enable()
            val mock = kit.pairGlasses(GlassesModel.RAYBAN_META).getOrThrow()
            glasses = mock
            mock.powerOn()
            mock.unfold()
            mock.don()
            mock.services.camera.setCameraFeed(video)
            val created = Wearables.createSession(AutoDeviceSelector()).getOrThrow()
            session = created
            errors = scope.launch { created.errors.collect { onError(it.description) } }
            coroutineScope {
                val started = async(start = CoroutineStart.UNDISPATCHED) {
                    withTimeout(20_000) { created.state.first { it == DeviceSessionState.STARTED } }
                }
                created.start()
                started.await()
            }
            val attached = created.addCamera(StreamConfiguration(videoQuality = VideoQuality.MEDIUM, frameRate = 7)).getOrThrow()
            camera = attached
            frames = scope.launch(Dispatchers.Default, start = CoroutineStart.UNDISPATCHED) {
                attached.stream.videoStream.collect { frame ->
                    val buffer = frame.buffer.asReadOnlyBuffer()
                    val copy = ByteArray(buffer.remaining())
                    buffer.get(copy)
                    onFrame(Frame(copy, frame.width, frame.height, frame.presentationTimeUs,
                                  frame.isCompressed, frame.isCodecConfig))
                }
            }
            attached.stream.start().getOrThrow()
        } catch (error: Exception) {
            try { stop() } catch (_: Exception) { /* Preserve the original failure. */ }
            throw error
        }
    }

    suspend fun stop(): Unit = withContext(NonCancellable) {
        frames?.cancelAndJoin()
        errors?.cancelAndJoin()
        frames = null
        errors = null
        try { camera?.stop() } finally {
            camera = null
            try { session?.stop() } finally {
                session = null
                try { glasses?.let { kit.unpairDevice(it) } } finally {
                    glasses = null
                    kit.disable()
                }
            }
        }
    }

    /** Explicit upload of a JPEG or recording produced by the Android host.
     * This stores evidence locally; it does NOT call Spark or confirm a claim.
     * For an emulator/USB phone use adb reverse tcp:8017 tcp:8017.
     */
    suspend fun uploadEvidence(file: File, sessionId: String, baseUrl: String = "http://127.0.0.1:8017"): String = withContext(Dispatchers.IO) {
        val base = URI(baseUrl)
        require(base.scheme == "http" && base.host in listOf("127.0.0.1", "localhost") &&
                base.userInfo == null && base.query == null && base.fragment == null && base.path in listOf("", "/"))
        require(sessionId.matches(Regex("[a-zA-Z0-9-]+")))
        require(file.isFile && file.length() in 1..(150L * 1024 * 1024))
        val hash = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val bytes = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(bytes)
                if (count < 0) break
                hash.update(bytes, 0, count)
            }
        }
        val uploadKey = "android-" + hash.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
        val boundary = "compinche-${UUID.randomUUID()}"
        val mime = if (file.extension.lowercase() in listOf("jpg", "jpeg")) "image/jpeg" else "video/mp4"
        val prefix = ("--$boundary\r\nContent-Disposition: form-data; name=\"idempotency_key\"\r\n\r\n$uploadKey\r\n" +
            "--$boundary\r\nContent-Disposition: form-data; name=\"capture_source\"\r\n\r\nmeta_dat_mock\r\n" +
            "--$boundary\r\nContent-Disposition: form-data; name=\"note\"\r\n\r\nExplicit evidence from a Ray-Ban Meta MockDeviceKit replay; exact source time is not inferred.\r\n" +
            "--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"replay.${if (mime == "image/jpeg") "jpg" else "mp4"}\"\r\nContent-Type: $mime\r\n\r\n").toByteArray()
        val suffix = "\r\n--$boundary--\r\n".toByteArray()
        val connection = URI(baseUrl.trimEnd('/') + "/api/sessions/$sessionId/evidence").toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.connectTimeout = 10_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            connection.setFixedLengthStreamingMode(prefix.size + file.length() + suffix.size)
            connection.outputStream.use { output ->
                output.write(prefix)
                file.inputStream().use { it.copyTo(output) }
                output.write(suffix)
            }
            check(connection.responseCode == 201) { "Local evidence upload failed (${connection.responseCode})" }
            connection.inputStream.bufferedReader().use { it.readText() }
        } finally { connection.disconnect() }
    }
}
