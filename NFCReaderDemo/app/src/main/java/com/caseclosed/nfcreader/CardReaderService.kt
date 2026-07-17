package com.caseclosed.nfcreader

import android.nfc.Tag
import android.nfc.tech.IsoDep
import com.github.devnied.emvnfccard.parser.EmvTemplate
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Locale

data class CardDetails(
    val pan: String,
    val expiry: String,
    val scheme: String = "UNKNOWN",
    val cardState: String = "UNKNOWN",
    val cardholderName: String? = null,
    val bic: String? = null,
    val iban: String? = null,
    val atr: String? = null,
    val atrDescriptions: List<String> = emptyList(),
    val applications: List<EmvApplicationDetails> = emptyList(),
    val serviceCode: EmvServiceCodeDetails? = null,
    val track1Available: Boolean = false,
    val track2Available: Boolean = false,
)

data class EmvApplicationDetails(
    val aid: String,
    val label: String?,
    val priority: Int,
    val readingStep: String,
)

data class EmvServiceCodeDetails(
    val interchange: String,
    val authorizationProcessing: String,
    val allowedServices: String,
)

class CardReaderService {
    suspend fun readCard(tag: Tag): CardDetails = withContext(Dispatchers.IO) {
        NfcReadLog.info(
            "Starting EMV read; technologies=${tag.techList.joinToString()}",
        )
        val isoDep = IsoDep.get(tag) ?: throw CardReadException("Card does not support IsoDep")

        try {
            NfcReadLog.debug("Connecting IsoDep transport")
            isoDep.connect()
            isoDep.timeout = TRANSCEIVE_TIMEOUT_MILLIS
            NfcReadLog.debug(
                "IsoDep connected; timeoutMs=${isoDep.timeout}, " +
                    "maxTransceiveBytes=${isoDep.maxTransceiveLength}, " +
                    "extendedApdu=${isoDep.isExtendedLengthApduSupported}",
            )

            val config = EmvTemplate.Config()
                .setContactLess(true)
                .setReadAllAids(true)
                .setReadTransactions(false)
                .setReadAt(true)
                .setReadCplc(false)
            NfcReadLog.debug(
                "EMV parser configured; contactless=true, readAllAids=true, " +
                    "readTransactions=false, readAtr=true",
            )

            NfcReadLog.info("Reading payment applications and EMV records")
            val card = EmvTemplate.Builder()
                .setProvider(NfcProvider(isoDep))
                .setConfig(config)
                .build()
                .readEmvCard()

            val pan = card.cardNumber
                ?.filter(Char::isDigit)
                ?.takeIf { it.length in MIN_PAN_LENGTH..MAX_PAN_LENGTH }
                ?: throw CardReadException("No valid PAN was found on the card")
            val expiryDate = card.expireDate
                ?: throw CardReadException("No expiry date was found on the card")
            val formattedExpiry = SimpleDateFormat(EXPIRY_FORMAT, Locale.US).format(expiryDate)
            val holderName = listOfNotNull(
                card.holderFirstname.cleanOrNull(),
                card.holderLastname.cleanOrNull(),
            ).joinToString(" ").ifBlank { null }
            val applications = card.applications.orEmpty().map { application ->
                EmvApplicationDetails(
                    aid = application.aid?.toHexString().orEmpty(),
                    label = application.applicationLabel.cleanOrNull(),
                    priority = application.priority,
                    readingStep = application.readingStep?.name ?: "UNKNOWN",
                )
            }
            val track1 = card.track1
            val track2 = card.track2
            val service = track2?.service ?: track1?.service
            val serviceCode = service?.let {
                EmvServiceCodeDetails(
                    interchange = it.serviceCode1?.name ?: "UNKNOWN",
                    authorizationProcessing = it.serviceCode2?.name ?: "UNKNOWN",
                    allowedServices = it.serviceCode3?.name ?: "UNKNOWN",
                )
            }
            NfcReadLog.info(
                "EMV data parsed; scheme=${card.type?.name ?: "unknown"}, " +
                    "pan=${maskPan(pan)}, expiry=$formattedExpiry, " +
                    "applications=${applications.size}, state=${card.state?.name ?: "unknown"}",
            )

            CardDetails(
                pan = pan,
                expiry = formattedExpiry,
                scheme = card.type?.name ?: "UNKNOWN",
                cardState = card.state?.name ?: "UNKNOWN",
                cardholderName = holderName,
                bic = card.bic.cleanOrNull(),
                iban = card.iban.cleanOrNull(),
                atr = card.at.cleanOrNull(),
                atrDescriptions = card.atrDescription.orEmpty()
                    .mapNotNull { it.cleanOrNull() },
                applications = applications,
                serviceCode = serviceCode,
                track1Available = track1 != null,
                track2Available = track2 != null,
            )
        } catch (error: CardReadException) {
            NfcReadLog.warning("EMV read rejected: ${error.message}")
            throw error
        } catch (error: Exception) {
            NfcReadLog.error("EMV read failed", error)
            throw CardReadException("Unable to read EMV card. Hold it steady and try again", error)
        } finally {
            runCatching { isoDep.close() }
                .onSuccess { NfcReadLog.debug("IsoDep connection closed") }
                .onFailure { NfcReadLog.warning("IsoDep close failed", it) }
        }
    }

    private companion object {
        const val TRANSCEIVE_TIMEOUT_MILLIS = 5_000
        const val MIN_PAN_LENGTH = 12
        const val MAX_PAN_LENGTH = 19
        const val EXPIRY_FORMAT = "MM/yy"
    }

    private fun String?.cleanOrNull(): String? = this?.trim()?.takeIf(String::isNotEmpty)

    private fun ByteArray.toHexString(): String = joinToString(separator = "") { byte ->
        (byte.toInt() and 0xFF).toString(16).uppercase().padStart(2, '0')
    }
}

class CardReadException(message: String, cause: Throwable? = null) : Exception(message, cause)
