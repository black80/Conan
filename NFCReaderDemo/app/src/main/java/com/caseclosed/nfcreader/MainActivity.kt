package com.caseclosed.nfcreader

import android.content.Intent
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Nfc
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import java.math.BigDecimal
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : ComponentActivity(), NfcAdapter.ReaderCallback {
    private var nfcAdapter: NfcAdapter? = null
    private val cardReaderService = CardReaderService()
    private val readInProgress = AtomicBoolean(false)

    private var scanState by mutableStateOf<ScanState>(ScanState.Ready)
    private var isInjecting by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        nfcAdapter = NfcAdapter.getDefaultAdapter(this)
        scanState = initialScanState()
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(BACKGROUND_COLOR_ARGB),
            navigationBarStyle = SystemBarStyle.dark(BACKGROUND_COLOR_ARGB),
        )
        setContent {
            CaseClosedTheme {
                CaseClosedScreen(
                    scanState = scanState,
                    isInjecting = isInjecting,
                    onInject = ::injectTransaction,
                )
            }
        }
        handleNfcIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        enableNfcReaderMode()
    }

    override fun onPause() {
        nfcAdapter?.disableReaderMode(this)
        super.onPause()
    }

    override fun onDestroy() {
        Beeper.release()
        super.onDestroy()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleNfcIntent(intent)
    }

    override fun onTagDiscovered(tag: Tag) {
        NfcReadLog.debug("Reader mode delivered an NFC tag")
        readTag(tag)
    }

    private fun enableNfcReaderMode() {
        val adapter = nfcAdapter
        when {
            adapter == null -> {
                NfcReadLog.warning("NFC adapter is unavailable")
                scanState = ScanState.Unavailable
            }
            !adapter.isEnabled -> {
                NfcReadLog.warning("NFC adapter is disabled")
                scanState = ScanState.Disabled
            }
            else -> {
                adapter.enableReaderMode(
                    this,
                    this,
                    NfcAdapter.FLAG_READER_NFC_A or
                        NfcAdapter.FLAG_READER_NFC_B or
                        NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK,
                    Bundle().apply {
                        putInt(NfcAdapter.EXTRA_READER_PRESENCE_CHECK_DELAY, 250)
                    },
                )
                NfcReadLog.debug("NFC reader mode enabled for NFC-A and NFC-B")
                if (scanState is ScanState.Disabled || scanState is ScanState.Unavailable) {
                    scanState = ScanState.Ready
                }
            }
        }
    }

    private fun handleNfcIntent(intent: Intent?) {
        if (intent?.action != NfcAdapter.ACTION_TECH_DISCOVERED) return
        NfcReadLog.debug("TECH_DISCOVERED intent received")
        intent.tagExtra()?.let(::readTag)
    }

    private fun readTag(tag: Tag) {
        if (!readInProgress.compareAndSet(false, true)) {
            NfcReadLog.debug("Ignoring duplicate tag while an EMV read is active")
            return
        }
        lifecycleScope.launch {
            scanState = ScanState.Reading
            try {
                val details = cardReaderService.readCard(tag)
                scanState = ScanState.CardRead(details)
                NfcReadLog.info("Card read completed and UI state updated")
                Beeper.playSingleBeep()
            } catch (error: Exception) {
                NfcReadLog.warning("Card read could not complete: ${error.message}")
                scanState = ScanState.Error(error.message ?: "Unable to read this card")
                Beeper.playDoubleBeep()
            } finally {
                readInProgress.set(false)
            }
        }
    }

    private suspend fun injectTransaction(
        endpoint: String,
        amount: BigDecimal,
    ): Result<InjectionResult> {
        val card = (scanState as? ScanState.CardRead)?.details
            ?: return Result.failure(IllegalStateException("Scan a card before injecting"))
        if (isInjecting) return Result.failure(IllegalStateException("Injection already in progress"))

        isInjecting = true
        return try {
            TransactionInjector.injectTransaction(
                cardDetails = card,
                endpoint = endpoint,
                amount = amount,
                deviceFingerprint = Build.FINGERPRINT.ifBlank { "unknown" },
            )
        } finally {
            isInjecting = false
        }
    }

    private fun initialScanState(): ScanState = when {
        nfcAdapter == null -> ScanState.Unavailable
        nfcAdapter?.isEnabled == false -> ScanState.Disabled
        else -> ScanState.Ready
    }

    @Suppress("DEPRECATION")
    private fun Intent.tagExtra(): Tag? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        getParcelableExtra(NfcAdapter.EXTRA_TAG, Tag::class.java)
    } else {
        getParcelableExtra(NfcAdapter.EXTRA_TAG)
    }

    private companion object {
        const val BACKGROUND_COLOR_ARGB = 0xFF121212.toInt()
    }
}

internal sealed interface ScanState {
    data object Ready : ScanState
    data object Reading : ScanState
    data object Disabled : ScanState
    data object Unavailable : ScanState
    data class CardRead(val details: CardDetails) : ScanState
    data class Error(val message: String) : ScanState
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CaseClosedScreen(
    scanState: ScanState,
    isInjecting: Boolean,
    onInject: suspend (String, BigDecimal) -> Result<InjectionResult>,
) {
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    var endpoint by rememberSaveable { mutableStateOf(TransactionInjector.DEFAULT_ENDPOINT) }
    var amountText by rememberSaveable { mutableStateOf("") }
    var amountValidationAttempted by rememberSaveable { mutableStateOf(false) }
    val cardDetails = (scanState as? ScanState.CardRead)?.details

    Scaffold(
        containerColor = CaseClosedColors.background,
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "CaseClosed NFC Reader",
                        color = CaseClosedColors.onBackground,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = CaseClosedColors.background,
                ),
            )
        },
    ) { contentPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(contentPadding)
                .navigationBarsPadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = if (cardDetails == null) "Tap Card to Read" else "Card Read Successfully",
                color = CaseClosedColors.onBackground,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = statusText(scanState),
                color = statusColor(scanState),
                fontSize = 15.sp,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(36.dp))

            NfcIndicator(scanState)
            Spacer(Modifier.height(36.dp))

            AnimatedVisibility(visible = cardDetails != null) {
                cardDetails?.let { details ->
                    CardDetailsPanel(details)
                }
            }

            Spacer(Modifier.height(24.dp))
            OutlinedTextField(
                value = amountText,
                onValueChange = {
                    amountText = it
                    amountValidationAttempted = false
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = !isInjecting,
                label = { Text("Transaction amount") },
                suffix = { Text("USD") },
                singleLine = true,
                isError = amountValidationAttempted && parseTransactionAmount(amountText) == null,
                supportingText = if (amountValidationAttempted && parseTransactionAmount(amountText) == null) {
                    { Text("Enter a positive amount with up to two decimals") }
                } else {
                    null
                },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = CaseClosedColors.danger,
                    unfocusedBorderColor = CaseClosedColors.outline,
                    focusedLabelColor = CaseClosedColors.danger,
                    unfocusedLabelColor = CaseClosedColors.muted,
                    cursorColor = CaseClosedColors.danger,
                    errorBorderColor = CaseClosedColors.danger,
                    errorLabelColor = CaseClosedColors.danger,
                    errorSupportingTextColor = CaseClosedColors.danger,
                ),
            )

            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = endpoint,
                onValueChange = { endpoint = it },
                modifier = Modifier.fillMaxWidth(),
                enabled = !isInjecting,
                label = { Text("Backend URL") },
                singleLine = false,
                minLines = 2,
                textStyle = MaterialTheme.typography.bodySmall.copy(
                    color = CaseClosedColors.onBackground,
                    fontFamily = FontFamily.Monospace,
                ),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = CaseClosedColors.danger,
                    unfocusedBorderColor = CaseClosedColors.outline,
                    focusedLabelColor = CaseClosedColors.danger,
                    unfocusedLabelColor = CaseClosedColors.muted,
                    cursorColor = CaseClosedColors.danger,
                ),
            )

            AnimatedVisibility(visible = cardDetails != null) {
                Column(modifier = Modifier.fillMaxWidth()) {
                    Spacer(Modifier.height(20.dp))
                    Button(
                        onClick = {
                            scope.launch {
                                amountValidationAttempted = true
                                val amount = parseTransactionAmount(amountText)
                                if (amount == null) {
                                    snackbarHostState.showSnackbar(
                                        "Enter a valid transaction amount in USD",
                                    )
                                    return@launch
                                }
                                val result = onInject(endpoint, amount)
                                val message = result.fold(
                                    onSuccess = { accepted ->
                                        val suffix = if (accepted.duplicate) " (already accepted)" else ""
                                        "Accepted ${accepted.transactionId} " +
                                            "(${accepted.status})$suffix"
                                    },
                                    onFailure = { it.message ?: "Transaction injection failed" },
                                )
                                snackbarHostState.showSnackbar(message)
                            }
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(56.dp),
                        enabled = !isInjecting,
                        shape = RoundedCornerShape(8.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = CaseClosedColors.danger,
                            contentColor = Color.White,
                            disabledContainerColor = CaseClosedColors.danger.copy(alpha = 0.45f),
                        ),
                    ) {
                        if (isInjecting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(22.dp),
                                color = Color.White,
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Icon(Icons.AutoMirrored.Filled.Send, contentDescription = null)
                            Spacer(Modifier.size(10.dp))
                            Text(
                                text = "Inject Anomalous Transaction",
                                fontWeight = FontWeight.Bold,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun NfcIndicator(scanState: ScanState) {
    val transition = rememberInfiniteTransition(label = "nfcPulse")
    val pulse by transition.animateFloat(
        initialValue = 0.92f,
        targetValue = 1.06f,
        animationSpec = infiniteRepeatable(
            animation = tween(1_100),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "nfcPulseScale",
    )
    val color = statusColor(scanState)

    Box(
        modifier = Modifier
            .size(152.dp)
            .scale(if (scanState is ScanState.Ready) pulse else 1f)
            .background(color.copy(alpha = 0.14f), CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Surface(
            modifier = Modifier.size(112.dp),
            color = CaseClosedColors.surface,
            shape = CircleShape,
            border = androidx.compose.foundation.BorderStroke(2.dp, color),
        ) {
            Box(contentAlignment = Alignment.Center) {
                if (scanState is ScanState.Reading) {
                    CircularProgressIndicator(color = color, strokeWidth = 3.dp)
                } else {
                    Icon(
                        imageVector = Icons.Default.Nfc,
                        contentDescription = "NFC reader status",
                        modifier = Modifier.size(52.dp),
                        tint = color,
                    )
                }
            }
        }
    }
}

@Composable
private fun CardDetailsPanel(details: CardDetails) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = CaseClosedColors.surface,
        shape = RoundedCornerShape(8.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, CaseClosedColors.outline),
    ) {
        Column(modifier = Modifier.padding(18.dp)) {
            Text(
                text = "EMV CARD",
                color = CaseClosedColors.success,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(16.dp))
            DetailRow(label = "PAN", value = maskPan(details.pan))
            Spacer(Modifier.height(10.dp))
            DetailRow(label = "Expiry", value = details.expiry)
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = label, color = CaseClosedColors.muted, fontSize = 14.sp)
        Text(
            text = value,
            color = CaseClosedColors.onBackground,
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Medium,
            fontSize = 15.sp,
        )
    }
}

internal fun maskPan(pan: String): String {
    val digits = pan.filter(Char::isDigit)
    if (digits.length <= 4) return digits
    val maskedGroups = "*".repeat(digits.length - 4).chunked(4)
    return (maskedGroups + digits.takeLast(4)).joinToString(" ")
}

internal fun parseTransactionAmount(input: String): BigDecimal? {
    val amount = input.trim().toBigDecimalOrNull() ?: return null
    if (amount.signum() <= 0 || amount.scale() > 2) return null
    return amount.setScale(2)
}

private fun statusText(state: ScanState): String = when (state) {
    ScanState.Ready -> "Ready to Scan..."
    ScanState.Reading -> "Card detected. Reading EMV data..."
    ScanState.Disabled -> "NFC is turned off. Enable it to continue."
    ScanState.Unavailable -> "NFC hardware is not available on this device."
    is ScanState.CardRead -> "PAN and expiry extracted securely"
    is ScanState.Error -> state.message
}

private fun statusColor(state: ScanState): Color = when (state) {
    is ScanState.CardRead -> CaseClosedColors.success
    is ScanState.Error -> CaseClosedColors.danger
    ScanState.Disabled -> CaseClosedColors.warning
    ScanState.Unavailable -> CaseClosedColors.muted
    else -> CaseClosedColors.accent
}

private object CaseClosedColors {
    val background = Color(0xFF121212)
    val surface = Color(0xFF1E1E1E)
    val onBackground = Color(0xFFF4F4F5)
    val muted = Color(0xFFA1A1AA)
    val outline = Color(0xFF3F3F46)
    val accent = Color(0xFF5CB8E6)
    val success = Color(0xFF4CAF78)
    val warning = Color(0xFFF2B84B)
    val danger = Color(0xFFE5484D)
}

@Composable
private fun CaseClosedTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = CaseClosedColors.danger,
            secondary = CaseClosedColors.accent,
            background = CaseClosedColors.background,
            surface = CaseClosedColors.surface,
            onBackground = CaseClosedColors.onBackground,
            onSurface = CaseClosedColors.onBackground,
        ),
        content = content,
    )
}
