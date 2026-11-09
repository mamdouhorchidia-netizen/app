package com.vorath.mobile.ui

import java.text.NumberFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object Format {
    private val whole = NumberFormat.getIntegerInstance(Locale.US)

    fun number(n: Number): String = whole.format(Math.round(n.toDouble()))

    fun money(egp: Double): String = (if (egp < 0) "−" else "") + "EGP " + whole.format(Math.round(Math.abs(egp)))

    /** "2026-10-10" -> "10 Oct"; anything else comes back as it is. */
    fun day(iso: String): String = try {
        SimpleDateFormat("d MMM", Locale.ENGLISH).format(SimpleDateFormat("yyyy-MM-dd", Locale.US).parse(iso)!!)
    } catch (e: Exception) {
        iso
    }

    fun clock(millis: Long): String = SimpleDateFormat("HH:mm", Locale.US).format(Date(millis))
}
