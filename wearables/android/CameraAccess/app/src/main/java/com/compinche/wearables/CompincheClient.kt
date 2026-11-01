package com.compinche.wearables

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import com.meta.wearable.dat.externalsampleapps.cameraaccess.camera.CapturePreview
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** Local USB bridge. No API keys, cloud uploads or automatic Spark calls. */
object CompincheClient {
    const val BASE = "http://127.0.0.1:8017"
    private val uploadLock = Mutex()

    private fun connect(path: String): HttpURLConnection {
        require(path.startsWith("/api/") && !path.contains(".."))
        return (URI(BASE + path).toURL().openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 90_000
            instanceFollowRedirects = false
        }
    }

    private fun json(path: String, body: JSONObject? = null): JSONObject {
        val connection = connect(path)
        try {
            if (body != null) {
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            check(connection.responseCode in 200..299) { "Compinche respondió ${connection.responseCode}. Comprueba npm run demo y adb reverse." }
            return connection.inputStream.bufferedReader().use { reader ->
                val text = reader.readText()
                check(text.length <= 1_000_000) { "Respuesta local demasiado grande" }
                JSONObject(text)
            }
        } finally { connection.disconnect() }
    }

    suspend fun familyFeed(context: Context): Uri = withContext(Dispatchers.IO) {
        val demo = json("/api/demo")
        val path = demo.optString("sdk_replay_url")
        check(path.matches(Regex("/api/media/[a-zA-Z0-9-]+"))) { "Prepara primero el vídeo familiar en Compinche." }
        val temporary = File.createTempFile("family-", ".mp4", context.cacheDir)
        val connection = connect(path)
        var complete = false
        try {
            check(connection.responseCode == 200) { "No se pudo descargar el vídeo local (${connection.responseCode})" }
            check(connection.contentLengthLong <= 40L * 1024 * 1024) { "El vídeo supera 40 MiB" }
            connection.inputStream.use { input -> temporary.outputStream().use { output ->
                val buffer = ByteArray(64 * 1024)
                var total = 0L
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    total += count
                    check(total <= 40L * 1024 * 1024) { "El vídeo supera 40 MiB" }
                    output.write(buffer, 0, count)
                }
                check(total > 0) { "El vídeo está vacío" }
            } }
            complete = true
            Uri.fromFile(temporary)
        } finally {
            connection.disconnect()
            if (!complete) temporary.delete()
        }
    }

    suspend fun upload(context: Context, preview: CapturePreview): String = uploadLock.withLock {
        withContext(Dispatchers.IO) {
            val demo = json("/api/demo")
            val sourceId = demo.getString("session_id")
            val preferences = context.getSharedPreferences("compinche", Context.MODE_PRIVATE)
            var sessionId = preferences.getString("learner:$sourceId", null)
            if (sessionId != null) {
                val connection = connect("/api/sessions/$sessionId")
                try {
                    val status = connection.responseCode
                    check(status == 200 || status == 404) { "No se pudo comprobar la sesión local ($status)." }
                    if (status == 404) sessionId = null
                } finally { connection.disconnect() }
            }
            if (sessionId == null) {
                sessionId = json("/api/sessions", JSONObject().put("title", "Dad’s recipe · Android replay")
                    .put("teacher", "Dad").put("mode", "learn").put("source_session_id", sourceId)).getString("id")
                check(preferences.edit().putString("learner:$sourceId", sessionId).commit()) { "No se pudo guardar la sesión" }
            }
            val file = File.createTempFile("sdk-capture-", if (preview is CapturePreview.Photo) ".jpg" else ".mp4", context.cacheDir)
            try {
                file.outputStream().use { output ->
                    when (preview) {
                        is CapturePreview.Photo -> check(preview.bitmap.compress(Bitmap.CompressFormat.JPEG, 90, output))
                        is CapturePreview.Video -> {
                            val input = checkNotNull(context.contentResolver.openInputStream(preview.uri))
                            input.use {
                                val buffer = ByteArray(64 * 1024)
                                var total = 0L
                                while (true) {
                                    val count = it.read(buffer)
                                    if (count < 0) break
                                    total += count
                                    check(total <= 150L * 1024 * 1024) { "La captura supera 150 MiB" }
                                    output.write(buffer, 0, count)
                                }
                            }
                        }
                    }
                }
                coroutineScope { CompincheReplay(context, this).uploadEvidence(file, sessionId) }
                "$BASE/#live/$sessionId"
            } finally { file.delete() }
        }
    }

    fun open(context: Context, url: String = BASE) {
        require(url.startsWith("$BASE/" ) || url == BASE)
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    }
}
