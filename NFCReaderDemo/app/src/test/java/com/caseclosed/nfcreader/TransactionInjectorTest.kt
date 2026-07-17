package com.caseclosed.nfcreader

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.math.BigDecimal
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

class TransactionInjectorTest {
    private lateinit var server: MockWebServer
    private val card = CardDetails(
        pan = "5412751234124532",
        expiry = "07/26",
        scheme = "VISA",
        cardState = "ACTIVE",
        cardholderName = "DEMO HOLDER",
        bic = "DEMOBIC",
        iban = "SA001234567890",
        atr = "3B8F8001",
        atrDescriptions = listOf("Contactless EMV card"),
        applications = listOf(
            EmvApplicationDetails(
                aid = "A0000000031010",
                label = "VISA CREDIT",
                priority = 1,
                readingStep = "READ",
            ),
        ),
        serviceCode = EmvServiceCodeDetails(
            interchange = "INTERNATIONAL",
            authorizationProcessing = "NORMAL",
            allowedServices = "GOODS_AND_SERVICES_ONLY",
        ),
        track1Available = false,
        track2Available = true,
    )
    private val amount = BigDecimal("8250.50")
    private val deviceFingerprint = "samsung/a15x/a15:16/TEST_BUILD"

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun payloadMatchesTransactionContract() {
        val transactionId = UUID.fromString("123e4567-e89b-12d3-a456-426614174000")
        val transactionTime = SimpleDateFormat(
            "yyyy-MM-dd'T'HH:mm:ss'Z'",
            Locale.US,
        ).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }.parse("2026-07-13T10:26:47Z")!!
        val payload = TransactionInjector.buildPayload(
            cardDetails = card,
            amount = amount,
            deviceFingerprint = deviceFingerprint,
            transactionId = transactionId,
            transactionTime = transactionTime,
        )

        assertEquals(transactionId.toString(), payload.getString("transaction_id"))
        assertEquals("2026-07-13T10:26:47Z", payload.getString("timestamp"))

        val cardData = payload.getJSONObject("real_card_data")
        assertEquals(card.pan, cardData.getString("pan"))
        assertEquals(card.expiry, cardData.getString("expiry"))
        assertEquals("VISA", cardData.getString("scheme"))
        assertEquals("ACTIVE", cardData.getString("card_state"))
        assertEquals("DEMO HOLDER", cardData.getString("cardholder_name"))
        assertEquals("DEMOBIC", cardData.getString("bic"))
        assertEquals("SA001234567890", cardData.getString("iban"))
        assertEquals("3B8F8001", cardData.getString("atr"))
        assertEquals(
            "Contactless EMV card",
            cardData.getJSONArray("atr_descriptions").getString(0),
        )
        assertEquals(false, cardData.getBoolean("track1_available"))
        assertEquals(true, cardData.getBoolean("track2_available"))
        val application = cardData.getJSONArray("applications").getJSONObject(0)
        assertEquals("A0000000031010", application.getString("aid"))
        assertEquals("VISA CREDIT", application.getString("label"))
        assertEquals(1, application.getInt("priority"))
        assertEquals("READ", application.getString("reading_step"))
        val serviceCode = cardData.getJSONObject("service_code")
        assertEquals("INTERNATIONAL", serviceCode.getString("interchange"))
        assertEquals("NORMAL", serviceCode.getString("authorization_processing"))
        assertEquals("GOODS_AND_SERVICES_ONLY", serviceCode.getString("allowed_services"))

        val anomaly = payload.getJSONObject("anomaly_context")
        assertEquals("8250.50", anomaly.get("amount").toString())
        assertEquals("SAR", anomaly.getString("currency"))
        assertEquals("HighEnd Electronics Online", anomaly.getString("merchant_name"))
        assertEquals("electronics", anomaly.getString("merchant_category"))
        assertEquals("SA", anomaly.getString("country"))
        assertEquals("103.24.12.5", anomaly.getString("terminal_ip"))
        assertEquals("Saudi Arabia", anomaly.getString("ip_country_geolocation"))
        assertEquals(deviceFingerprint, anomaly.getString("device_fingerprint"))
        assertEquals(true, anomaly.getBoolean("card_present"))
        assertEquals(false, anomaly.getBoolean("declined"))
    }

    @Test
    fun postsPayloadToConfiguredEndpoint() = runBlocking {
        val transactionId = "123e4567-e89b-12d3-a456-426614174000"
        server.enqueue(
            MockResponse().setResponseCode(201).setBody(
                """{"transaction_id":"$transactionId","status":"pending","duplicate":false}""",
            ),
        )
        val endpoint = server.url("/api/nfc/transactions").toString()

        val beforeRequest = System.currentTimeMillis()
        val result = TransactionInjector.injectTransaction(
            card,
            endpoint,
            amount,
            deviceFingerprint,
        )

        assertTrue(result.isSuccess)
        assertEquals(
            InjectionResult(transactionId, "pending", false),
            result.getOrThrow(),
        )
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/nfc/transactions", request.path)
        assertEquals("application/json; charset=utf-8", request.getHeader("Content-Type"))
        val body = JSONObject(request.body.readUtf8())
        assertEquals(card.pan, body.getJSONObject("real_card_data").getString("pan"))
        UUID.fromString(body.getString("transaction_id"))
        val emittedTime = SimpleDateFormat(
            "yyyy-MM-dd'T'HH:mm:ss'Z'",
            Locale.US,
        ).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }.parse(body.getString("timestamp"))!!.time
        assertTrue(emittedTime in (beforeRequest - 1_000)..System.currentTimeMillis())
        assertEquals(
            0,
            amount.compareTo(
                body.getJSONObject("anomaly_context").get("amount").toString().toBigDecimal(),
            ),
        )
    }

    @Test
    fun returnsFailureForNonSuccessfulResponse() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(422))

        val result = TransactionInjector.injectTransaction(
            card,
            server.url("/api/nfc/transactions").toString(),
            amount,
            deviceFingerprint,
        )

        assertTrue(result.isFailure)
        assertEquals("Backend returned HTTP 422", result.exceptionOrNull()?.message)
    }

    @Test
    fun returnsBackendErrorDetail() = runBlocking {
        server.enqueue(
            MockResponse()
                .setResponseCode(409)
                .setBody("{\"detail\":\"Transaction ID already exists\"}"),
        )

        val result = TransactionInjector.injectTransaction(
            card,
            server.url("/api/nfc/transactions").toString(),
            amount,
            deviceFingerprint,
        )

        assertTrue(result.isFailure)
        assertEquals("Transaction ID already exists", result.exceptionOrNull()?.message)
    }

    @Test
    fun rejectsMalformedSuccessResponse() {
        val error = runCatching {
            TransactionInjector.parseAcceptedResponse("{\"accepted\":true}")
        }.exceptionOrNull()

        assertEquals("Backend response is missing transaction details", error?.message)
    }
}
