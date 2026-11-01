package com.compinche.wearables

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.PixelCopy
import android.view.Surface
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext

/** A copy of the actual SDK-decoded preview, never a fabricated JPEG from HEVC bytes.
 * SDK PTS is the most recently rendered timestamp at copy completion, so it is
 * explicitly approximate; capturedAtElapsedMs is the shared native capture clock.
 */
data class PreviewSnapshot(
    val jpeg: ByteArray,
    val capturedAtElapsedMs: Long,
    val sdkPresentationTimeUs: Long?,
)

suspend fun copySdkPreview(
    surface: Surface,
    width: Int,
    height: Int,
    presentationTimeUs: () -> Long?,
): PreviewSnapshot? {
    if (!surface.isValid || width <= 0 || height <= 0) return null
    val scale = minOf(1.0, 640.0 / maxOf(width, height))
    val bitmap = Bitmap.createBitmap(maxOf(2, (width * scale).toInt()),
        maxOf(2, (height * scale).toInt()), Bitmap.Config.ARGB_8888)
    val copied = suspendCancellableCoroutine<Bitmap?> { continuation ->
        try {
            PixelCopy.request(surface, bitmap, { result ->
                if (result == PixelCopy.SUCCESS && continuation.isActive) {
                    continuation.resume(bitmap, onCancellation = { _, value, _ -> value?.recycle() })
                } else {
                    bitmap.recycle()
                    if (continuation.isActive) continuation.resume(null, onCancellation = null)
                }
            }, Handler(Looper.getMainLooper()))
        } catch (_: IllegalArgumentException) {
            bitmap.recycle()
            if (continuation.isActive) continuation.resume(null, onCancellation = null)
        }
    } ?: return null
    val copiedAt = SystemClock.elapsedRealtime()
    val pts = presentationTimeUs()
    return try {
        withContext(Dispatchers.Default) {
            val output = ByteArrayOutputStream()
            check(copied.compress(Bitmap.CompressFormat.JPEG, 72, output))
            PreviewSnapshot(output.toByteArray(), copiedAt, pts)
        }
    } finally { copied.recycle() }
}
