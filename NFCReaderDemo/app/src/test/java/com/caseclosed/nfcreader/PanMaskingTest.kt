package com.caseclosed.nfcreader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PanMaskingTest {
    @Test
    fun masksAllButLastFourDigits() {
        assertEquals("**** **** **** 4532", maskPan("5412 7512 3412 4532"))
    }

    @Test
    fun keepsFinalGroupIntactForVariableLengthPan() {
        assertEquals("**** **** *** 1234", maskPan("123456789011234"))
    }

    @Test
    fun parsesValidSarAmount() {
        assertEquals("15000.00", parseTransactionAmount("15000").toString())
        assertEquals("125.50", parseTransactionAmount("125.50").toString())
    }

    @Test
    fun rejectsInvalidSarAmount() {
        assertNull(parseTransactionAmount("0"))
        assertNull(parseTransactionAmount("12.345"))
        assertNull(parseTransactionAmount("not a number"))
    }
}
