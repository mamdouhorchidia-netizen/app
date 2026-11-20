package com.vorath.mobile.ui

import com.vorath.mobile.data.Freshness
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

    /** "14:05" for a time today, "10 Nov 14:05" for an earlier day. */
    fun stamp(millis: Long, now: Long): String {
        val dayOf = SimpleDateFormat("yyyyMMdd", Locale.US)
        return if (dayOf.format(Date(millis)) == dayOf.format(Date(now))) clock(millis)
        else SimpleDateFormat("d MMM HH:mm", Locale.ENGLISH).format(Date(millis))
    }

    /** The line at the top of the Today page. */
    fun freshness(f: Freshness, now: Long): String = when (f) {
        Freshness.Never -> ""
        Freshness.Updating -> "Updating…"
        is Freshness.Updated -> {
            val s = ((now - f.at) / 1000).coerceAtLeast(0)
            when {
                s < 5 -> "Updated just now"
                s < 60 -> "Updated $s s ago"
                s < 3600 -> "Updated ${s / 60} min ago"
                else -> "Updated at ${stamp(f.at, now)}"
            }
        }
        is Freshness.Unreachable -> {
            val what = if (f.reached) "Vorath did not answer" else "Can't reach Vorath"
            if (f.showing > 0) "$what · showing ${stamp(f.showing, now)}" else what
        }
    }
}
