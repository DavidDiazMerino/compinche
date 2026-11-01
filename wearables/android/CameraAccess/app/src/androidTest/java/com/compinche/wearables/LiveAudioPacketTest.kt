package com.compinche.wearables

import android.media.MediaExtractor
import android.media.MediaFormat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Device-side interoperability check, not an assertion that ASR has heard a person. */
@RunWith(AndroidJUnit4::class)
class LiveAudioPacketTest {
    @Test
    fun pcmFallbackProducesDecodableMono24kWithActualSampleDuration() {
        val pcm = ByteBuffer.allocate(24_000 * 2).order(ByteOrder.LITTLE_ENDIAN)
        repeat(24_000) { index -> pcm.putShort((Math.sin(index * Math.PI * 440 / 12_000) * 2000).toInt().toShort()) }
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val file = File.createTempFile("live-audio-protocol-", ".wav", context.cacheDir)
        val extractor = MediaExtractor()
        try {
            file.writeBytes(LiveAudio.wav(pcm.array()))
            extractor.setDataSource(file.path)
            assertTrue(extractor.trackCount > 0)
            val format = extractor.getTrackFormat(0)
            assertEquals(24000, format.getInteger(MediaFormat.KEY_SAMPLE_RATE))
            assertEquals(1, format.getInteger(MediaFormat.KEY_CHANNEL_COUNT))
            val duration = format.getLong(MediaFormat.KEY_DURATION)
            assertTrue("The packet duration comes from PCM samples, not an unrelated replay playhead", duration in 990_000L..1_010_000L)
        } finally { extractor.release(); file.delete() }
    }
}
