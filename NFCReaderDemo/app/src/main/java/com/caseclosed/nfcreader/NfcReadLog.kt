package com.caseclosed.nfcreader

import android.util.Log

internal object NfcReadLog {
    const val TAG = "CaseClosedNFC"

    fun debug(message: String) {
        if (BuildConfig.DEBUG) runCatching { Log.d(TAG, message) }
    }

    fun info(message: String) {
        if (BuildConfig.DEBUG) runCatching { Log.i(TAG, message) }
    }

    fun warning(message: String, error: Throwable? = null) {
        if (!BuildConfig.DEBUG) return
        runCatching {
            if (error == null) Log.w(TAG, message) else Log.w(TAG, message, error)
        }
    }

    fun error(message: String, error: Throwable) {
        if (BuildConfig.DEBUG) runCatching { Log.e(TAG, message, error) }
    }
}
