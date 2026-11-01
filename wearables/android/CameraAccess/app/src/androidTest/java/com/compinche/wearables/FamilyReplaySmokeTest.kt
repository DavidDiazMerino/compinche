package com.compinche.wearables

import android.media.MediaExtractor
import android.media.MediaFormat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Requires USB reverse and the real local demo. Never calls Spark or confirms recipe claims. */
@RunWith(AndroidJUnit4::class)
class FamilyReplaySmokeTest {
    @Test
    fun realFamilyVideoProducesSdkFrames() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val feed = CompincheClient.familyFeed(context)
        val file = File(checkNotNull(feed.path))
        val extractor = MediaExtractor()
        try {
            extractor.setDataSource(file.path)
            assertTrue((0 until extractor.trackCount).any {
                extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME) == "video/hevc"
            })
        } finally { extractor.release() }

        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val replay = CompincheReplay(context, scope)
        val firstFrame = CompletableDeferred<CompincheReplay.Frame>()
        try {
            withTimeout(45_000) {
                replay.start(feed, onFrame = { frame ->
                    if (frame.bytes.isNotEmpty() && frame.width > 0 && frame.height > 0) firstFrame.complete(frame)
                }, onError = { message -> firstFrame.completeExceptionally(IllegalStateException(message)) })
                val frame = firstFrame.await()
                assertTrue(frame.bytes.isNotEmpty())
                assertTrue(frame.width > 0 && frame.height > 0)
            }
        } finally {
            try { replay.stop() } finally { scope.cancel(); file.delete() }
        }
    }
}
