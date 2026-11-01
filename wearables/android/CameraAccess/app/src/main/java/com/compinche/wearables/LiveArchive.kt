package com.compinche.wearables

import android.content.Context
import android.net.Uri
import android.util.AtomicFile
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** Durable copy first; a failed transfer never discards the continuous SDK original. */
object LiveArchive {
    suspend fun save(context: Context, uri: Uri, sessionId: String, source: String) = withContext(Dispatchers.IO) {
        require(sessionId.matches(Regex("[a-zA-Z0-9_-]{1,120}")))
        require(source in setOf("meta_dat_mock", "meta_dat_device"))
        val directory = File(context.filesDir, "live-originals").apply { mkdirs() }
        val id = UUID.randomUUID().toString()
        val file = File(directory, "$id.mp4")
        val partial = File(directory, "$id.part")
        try {
            checkNotNull(context.contentResolver.openInputStream(uri)).use { input ->
                partial.outputStream().use { output -> input.copyTo(output) }
            }
            check(partial.length() > 0 && partial.renameTo(file)) { "No se pudo conservar el original continuo." }
            val metadata = File(directory, "$id.json")
            val body = JSONObject().put("session_id", sessionId).put("capture_source", source)
                .put("file", file.name).put("uploaded", false)
            writeMetadata(metadata, body)
            upload(file, metadata, body)
        } finally { partial.delete() }
    }

    suspend fun retryPending(context: Context): Int = withContext(Dispatchers.IO) {
        var count = 0
        File(context.filesDir, "live-originals").listFiles()?.filter { it.extension == "json" }?.forEach { metadata ->
            val body = JSONObject(metadata.readText())
            if (!body.optBoolean("uploaded")) {
                val filename = body.getString("file")
                require(filename.matches(Regex("[a-zA-Z0-9-]+\\.mp4")))
                upload(File(metadata.parentFile, filename), metadata, body)
                count++
            }
        }
        count
    }

    private fun writeMetadata(file: File, body: JSONObject) {
        val atomic = AtomicFile(file)
        val output = atomic.startWrite()
        try { output.write(body.toString().toByteArray(Charsets.UTF_8)); atomic.finishWrite(output) }
        catch (error: Exception) { atomic.failWrite(output); throw error }
    }

    private fun upload(file: File, metadata: File, body: JSONObject) {
        check(file.length() in 1..(150L * 1024 * 1024)) {
            "El original está conservado en el teléfono, pero supera los 150 MiB admitidos para una subida."
        }
        val sid = body.getString("session_id")
        require(sid.matches(Regex("[a-zA-Z0-9_-]{1,120}")))
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(65536)
            while (true) { val count = input.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) }
        }
        val key = "native-original-" + digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
        val boundary = "compinche-${UUID.randomUUID()}"
        val fields = linkedMapOf("idempotency_key" to key, "capture_source" to body.getString("capture_source"),
            "note" to "Continuous original recorded by the SDK during live learning. SDK preview-copy timestamps are approximate; an exact source-file offset is not inferred.")
        val prefix = buildString {
            fields.forEach { (name, value) -> append("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n") }
            append("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"native-live-original.mp4\"\r\nContent-Type: video/mp4\r\n\r\n")
        }.toByteArray(Charsets.UTF_8)
        val suffix = "\r\n--$boundary--\r\n".toByteArray()
        val connection = URI("${CompincheClient.BASE}/api/sessions/$sid/evidence").toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"; connection.doOutput = true
            connection.connectTimeout = 5000; connection.readTimeout = 120000; connection.instanceFollowRedirects = false
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            connection.setFixedLengthStreamingMode(prefix.size + file.length() + suffix.size)
            connection.outputStream.use { output -> output.write(prefix); file.inputStream().use { it.copyTo(output) }; output.write(suffix) }
            check(connection.responseCode == 201) { "El original está conservado en el teléfono; no se pudo subir (${connection.responseCode}). Usa Reintentar copias locales." }
            body.put("uploaded", true)
            writeMetadata(metadata, body)
        } finally { connection.disconnect() }
    }
}
