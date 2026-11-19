package com.vorath.mobile.data

/**
 * One part of the Today page (the numbers, or the Autopilot deck): what it shows, when that was read, and how the
 * latest read went. A failed read keeps [value] and [fetchedAt] and only sets [error], so old data stays on screen.
 */
data class Section<T>(
    val value: T? = null,
    /** When [value] was read from the server, or last confirmed unchanged; 0 when never. */
    val fetchedAt: Long = 0,
    /** The latest read failed (offline, or the server answered badly). null after a good read. */
    val error: VorathError? = null,
    /** The server said the role does not open this (403). Counts as an answer, not a failure. */
    val refused: String? = null,
) {
    /** The server has answered this section at least once (with data or with "not for your role"). */
    val answered: Boolean get() = value != null || refused != null
}

/** What the line at the top of the Today page says about how current the page is. */
sealed class Freshness {
    /** Nothing read yet and nothing being read. */
    data object Never : Freshness()

    data object Updating : Freshness()

    /** Every section's latest read worked; [at] is the oldest of their read times. */
    data class Updated(val at: Long) : Freshness()

    /** The latest read failed; the page shows what was read at [showing] (0: nothing to show). */
    data class Unreachable(val showing: Long) : Freshness()

    companion object {
        /** A quiet read shows as "Updating…" only once it has taken this long, so the line does not flicker every 30 s. */
        const val SHOW_UPDATING_AFTER_MS = 1_000L

        /** [busySince]: when the read in flight started, or null. */
        fun of(sections: List<Section<*>>, busySince: Long?, now: Long): Freshness {
            val shown = sections.filter { it.answered }.minOfOrNull { it.fetchedAt } ?: 0L
            return when {
                busySince != null && (shown == 0L || now - busySince >= SHOW_UPDATING_AFTER_MS) -> Updating
                sections.any { it.error != null } -> Unreachable(shown)
                shown > 0 -> Updated(shown)
                else -> Never
            }
        }
    }
}

/** How long the foreground loop waits before the next quiet read. */
object Cadence {
    const val EVERY_MS = 30_000L
    private val BACKOFF_MS = longArrayOf(30_000, 60_000, 120_000, 300_000)

    /**
     * [failures]: reads in a row that failed (0 after a good one). [lastTookMs]: how long the last read took. A slow
     * server is read at most once per ten of its answer times; failures back off 30 s, 60 s, 120 s, then every 5 min.
     */
    fun next(failures: Int, lastTookMs: Long): Long {
        val paced = maxOf(EVERY_MS, 10 * lastTookMs.coerceAtLeast(0))
        if (failures <= 0) return paced
        return maxOf(paced, BACKOFF_MS[minOf(failures, BACKOFF_MS.size) - 1])
    }
}

/** Keys for the Today list that stay with the same row when rows above it come and go. */
object RowKeys {
    /** A need keeps its key while only the dates and counts in its text move (as data.Signals does); repeats get #2, #3. */
    fun needs(needs: List<Need>): List<String> {
        val count = HashMap<String, Int>()
        return needs.map { n ->
            val k = "need:${n.level}:${n.href}:" + n.text.filterNot(Char::isDigit)
            val i = (count[k] ?: 0) + 1
            count[k] = i
            if (i == 1) k else "$k#$i"
        }
    }
}
