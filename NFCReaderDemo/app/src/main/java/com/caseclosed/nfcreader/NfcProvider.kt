package com.caseclosed.nfcreader

import android.nfc.tech.IsoDep
import android.os.SystemClock
import com.github.devnied.emvnfccard.parser.IProvider

/** Bridges EMV APDU commands to Android's IsoDep connection. */
class NfcProvider(private val isoDep: IsoDep) : IProvider {
    private var exchangeCount = 0

    override fun transceive(command: ByteArray): ByteArray {
        check(isoDep.isConnected) { "NFC card is no longer connected" }
        val exchangeNumber = ++exchangeCount
        val commandSummary = command.summary()
        val startedAt = SystemClock.elapsedRealtime()
        NfcReadLog.debug(
            "APDU[$exchangeNumber] -> $commandSummary, commandBytes=${command.size}",
        )

        return try {
            isoDep.transceive(command).also { response ->
                val elapsedMillis = SystemClock.elapsedRealtime() - startedAt
                NfcReadLog.debug(
                    "APDU[$exchangeNumber] <- responseBytes=${response.size}, " +
                        "statusWord=${response.statusWord()}, elapsedMs=$elapsedMillis",
                )
            }
        } catch (error: Exception) {
            val elapsedMillis = SystemClock.elapsedRealtime() - startedAt
            NfcReadLog.error(
                "APDU[$exchangeNumber] failed for $commandSummary after ${elapsedMillis}ms",
                error,
            )
            throw error
        }
    }

    override fun getAt(): ByteArray? {
        val answerToReset = isoDep.historicalBytes ?: isoDep.hiLayerResponse
        NfcReadLog.debug("ATR requested; bytes=${answerToReset?.size ?: 0}")
        return answerToReset
    }

    private fun ByteArray.summary(): String {
        if (size < 2) return "MALFORMED_APDU"
        val instruction = this[1].toInt() and 0xFF
        val name = when (instruction) {
            0xA4 -> "SELECT"
            0xA8 -> "GET_PROCESSING_OPTIONS"
            0xB2 -> "READ_RECORD"
            0xCA -> "GET_DATA"
            0x84 -> "GET_CHALLENGE"
            0x88 -> "INTERNAL_AUTHENTICATE"
            0xAE -> "GENERATE_APPLICATION_CRYPTOGRAM"
            else -> "INS_${instruction.hexByte()}"
        }
        val cla = (this[0].toInt() and 0xFF).hexByte()
        val p1 = getOrNull(2)?.toInt()?.and(0xFF)?.hexByte() ?: "--"
        val p2 = getOrNull(3)?.toInt()?.and(0xFF)?.hexByte() ?: "--"
        return "$name(cla=$cla,ins=${instruction.hexByte()},p1=$p1,p2=$p2)"
    }

    private fun ByteArray.statusWord(): String {
        if (size < 2) return "missing"
        return (this[size - 2].toInt() and 0xFF).hexByte() +
            (this[size - 1].toInt() and 0xFF).hexByte()
    }

    private fun Int.hexByte(): String = toString(16).uppercase().padStart(2, '0')
}
