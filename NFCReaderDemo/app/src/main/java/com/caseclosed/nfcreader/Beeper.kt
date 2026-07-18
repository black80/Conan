package com.caseclosed.nfcreader

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import kotlin.math.PI
import kotlin.math.roundToInt
import kotlin.math.sin

/** Cached PCM tones matching the reader success/error sound contract. */
object Beeper {
    private val lock = Any()
    private var singleBeep: AudioTrack? = null
    private var doubleBeep: AudioTrack? = null

    fun playSingleBeep() {
        restart(getBeep(), "single 1500Hz read-success beep")
    }

    fun playDoubleBeep() {
        restart(getDoubleBeep(), "double 750Hz read-error beep")
    }

    fun getBeep(): AudioTrack? = synchronized(lock) {
        singleBeep ?: generateTone(
            frequencyHz = SINGLE_BEEP_FREQUENCY_HZ,
            durationSeconds = SINGLE_BEEP_DURATION_SECONDS,
            bursts = listOf(Burst(startSeconds = 0f, endSeconds = 0.1f)),
        ).also { singleBeep = it }
    }

    fun getDoubleBeep(): AudioTrack? = synchronized(lock) {
        doubleBeep ?: generateTone(
            frequencyHz = DOUBLE_BEEP_FREQUENCY_HZ,
            durationSeconds = DOUBLE_BEEP_DURATION_SECONDS,
            bursts = listOf(
                Burst(startSeconds = 0f, endSeconds = 0.1f),
                Burst(startSeconds = 0.2f, endSeconds = 0.3f),
            ),
        ).also { doubleBeep = it }
    }

    fun release() = synchronized(lock) {
        singleBeep?.releaseSafely()
        doubleBeep?.releaseSafely()
        singleBeep = null
        doubleBeep = null
    }

    private fun restart(track: AudioTrack?, description: String) = synchronized(lock) {
        if (track == null || track.state != AudioTrack.STATE_INITIALIZED) {
            NfcReadLog.warning("Unable to play $description; AudioTrack is unavailable")
            return@synchronized
        }

        runCatching {
            if (track.playState != AudioTrack.PLAYSTATE_STOPPED) track.stop()
            track.setPlaybackHeadPosition(0)
            track.play()
            NfcReadLog.debug("Playing $description")
        }.onFailure {
            NfcReadLog.warning("Unable to play $description", it)
        }
    }

    private fun generateTone(
        frequencyHz: Int,
        durationSeconds: Float,
        bursts: List<Burst>,
    ): AudioTrack? = runCatching {
        val sampleCount = (SAMPLE_RATE_HZ * durationSeconds).roundToInt()
        val samples = ShortArray(sampleCount)
        bursts.forEach { burst ->
            writeBurst(samples, frequencyHz, burst)
        }

        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(SAMPLE_RATE_HZ)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(samples.size * Short.SIZE_BYTES)
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setSessionId(AudioManager.AUDIO_SESSION_ID_GENERATE)
            .build()

        try {
            check(
                track.state == AudioTrack.STATE_NO_STATIC_DATA ||
                    track.state == AudioTrack.STATE_INITIALIZED,
            ) { "AudioTrack creation failed with state=${track.state}" }
            val writtenSamples = track.write(samples, 0, samples.size, AudioTrack.WRITE_BLOCKING)
            check(writtenSamples == samples.size) {
                "AudioTrack wrote $writtenSamples of ${samples.size} samples"
            }
            check(track.state == AudioTrack.STATE_INITIALIZED) {
                "AudioTrack did not initialize after PCM write; state=${track.state}"
            }
            track.setVolume(OUTPUT_VOLUME)
            track
        } catch (error: Exception) {
            track.release()
            throw error
        }
    }.onFailure {
        NfcReadLog.warning("Unable to generate POS beep", it)
    }.getOrNull()

    private fun writeBurst(samples: ShortArray, frequencyHz: Int, burst: Burst) {
        val start = (burst.startSeconds * SAMPLE_RATE_HZ).roundToInt().coerceIn(samples.indices)
        val endExclusive = (burst.endSeconds * SAMPLE_RATE_HZ)
            .roundToInt()
            .coerceIn(start + 1, samples.size)
        val fadeSamples = (EDGE_FADE_SECONDS * SAMPLE_RATE_HZ).roundToInt()

        for (sampleIndex in start until endExclusive) {
            val burstIndex = sampleIndex - start
            val remaining = endExclusive - sampleIndex - 1
            val envelope = minOf(
                1f,
                burstIndex.toFloat() / fadeSamples,
                remaining.toFloat() / fadeSamples,
            )
            val phase = 2.0 * PI * frequencyHz * burstIndex / SAMPLE_RATE_HZ
            samples[sampleIndex] =
                (sin(phase) * Short.MAX_VALUE * AMPLITUDE * envelope).roundToInt().toShort()
        }
    }

    private fun AudioTrack.releaseSafely() {
        runCatching {
            if (playState != AudioTrack.PLAYSTATE_STOPPED) stop()
            release()
        }
    }

    private data class Burst(
        val startSeconds: Float,
        val endSeconds: Float,
    )

    private const val SAMPLE_RATE_HZ = 44_100
    private const val SINGLE_BEEP_FREQUENCY_HZ = 1_500
    private const val DOUBLE_BEEP_FREQUENCY_HZ = 750
    private const val SINGLE_BEEP_DURATION_SECONDS = 0.1f
    private const val DOUBLE_BEEP_DURATION_SECONDS = 0.3f
    private const val EDGE_FADE_SECONDS = 0.005f
    private const val AMPLITUDE = 0.7f
    private const val OUTPUT_VOLUME = 0.85f
}
