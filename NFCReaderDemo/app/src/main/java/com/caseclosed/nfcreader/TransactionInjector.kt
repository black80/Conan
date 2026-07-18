package com.caseclosed.nfcreader

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.math.BigDecimal
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.TimeUnit

object TransactionInjector {
    val DEFAULT_ENDPOINT =
        "${BuildConfig.BACKEND_BASE_URL.trimEnd('/')}/api/nfc/transactions"

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    suspend fun injectTransaction(
        cardDetails: CardDetails,
        endpoint: String,
        amount: BigDecimal,
        deviceFingerprint: String,
    ): Result<InjectionResult> = withContext(Dispatchers.IO) {
        runCatching {
            val url = endpoint.trim().toHttpUrlOrNull()
                ?: throw IllegalArgumentException("Enter a valid HTTP or HTTPS backend URL")
            val payload = buildPayload(cardDetails, amount, deviceFingerprint)
            val request = Request.Builder()
                .url(url)
                .post(payload.toString().toRequestBody(jsonMediaType))
                .build()

            client.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    val detail = runCatching {
                        JSONObject(body).opt("detail")?.toString()
                    }.getOrNull()
                    throw IOException(
                        detail?.takeIf(String::isNotBlank)
                            ?: "Backend returned HTTP ${response.code}",
                    )
                }
                val result = parseAcceptedResponse(body)
                NfcReadLog.info(
                    "NFC transaction accepted; id=${result.transactionId}, " +
                        "status=${result.status}, duplicate=${result.duplicate}",
                )
                result
            }
        }
    }

    internal fun parseAcceptedResponse(body: String): InjectionResult {
        val json = try {
            JSONObject(body)
        } catch (error: Exception) {
            throw IOException("Backend returned an invalid response", error)
        }
        val transactionId = json.optString("transaction_id")
        val status = json.optString("status")
        if (transactionId.isBlank() || status.isBlank()) {
            throw IOException("Backend response is missing transaction details")
        }
        return InjectionResult(
            transactionId = transactionId,
            status = status,
            duplicate = json.optBoolean("duplicate", false),
        )
    }

    internal fun buildPayload(
        cardDetails: CardDetails,
        amount: BigDecimal,
        deviceFingerprint: String,
        transactionId: UUID = UUID.randomUUID(),
        transactionTime: Date = Date(),
    ): JSONObject = JSONObject().apply {
        put("transaction_id", transactionId.toString())
        put("timestamp", transactionTime.toDemoTimestamp())
        put(
            "real_card_data",
            JSONObject().apply {
                put("pan", cardDetails.pan)
                put("expiry", cardDetails.expiry)
                put("scheme", cardDetails.scheme)
                put("card_state", cardDetails.cardState)
                putNullable("cardholder_name", cardDetails.cardholderName)
                putNullable("bic", cardDetails.bic)
                putNullable("iban", cardDetails.iban)
                putNullable("atr", cardDetails.atr)
                put("atr_descriptions", JSONArray(cardDetails.atrDescriptions))
                put("track1_available", cardDetails.track1Available)
                put("track2_available", cardDetails.track2Available)
                put(
                    "applications",
                    JSONArray().apply {
                        cardDetails.applications.forEach { application ->
                            put(
                                JSONObject().apply {
                                    put("aid", application.aid)
                                    putNullable("label", application.label)
                                    put("priority", application.priority)
                                    put("reading_step", application.readingStep)
                                },
                            )
                        }
                    },
                )
                put(
                    "service_code",
                    cardDetails.serviceCode?.let { serviceCode ->
                        JSONObject().apply {
                            put("interchange", serviceCode.interchange)
                            put(
                                "authorization_processing",
                                serviceCode.authorizationProcessing,
                            )
                            put("allowed_services", serviceCode.allowedServices)
                        }
                    } ?: JSONObject.NULL,
                )
            },
        )
        put(
            "anomaly_context",
            JSONObject().apply {
                put("amount", amount)
                put("currency", "USD")
                put("merchant_name", "HighEnd Electronics Online")
                put("merchant_category", "electronics")
                put("country", "SA")
                put("terminal_ip", "103.24.12.5")
                put("ip_country_geolocation", "Saudi Arabia")
                put("device_fingerprint", deviceFingerprint)
                put("card_present", true)
                put("declined", false)
            },
        )
    }

    private fun Date.toDemoTimestamp(): String = SimpleDateFormat(
        "'2022-09-11T'HH:mm:ss'Z'",
        Locale.US,
    ).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }.format(this)

    private fun JSONObject.putNullable(key: String, value: String?) {
        put(key, value ?: JSONObject.NULL)
    }
}

data class InjectionResult(
    val transactionId: String,
    val status: String,
    val duplicate: Boolean,
)
