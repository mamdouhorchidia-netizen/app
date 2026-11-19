package com.vorath.mobile.data

import com.vorath.mobile.ui.Format
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FreshnessTest {
    private val t = 1_800_000_000_000L
    private val offline = VorathError.Offline("Cannot reach the server.")

    @Test
    fun theLineSaysHowOldTheDataIs() {
        val ok = listOf(Section("m", fetchedAt = t), Section("d", fetchedAt = t + 3_000))
        assertEquals(Freshness.Updated(t), Freshness.of(ok, null, t + 42_000))   // the older of the two
        assertEquals("Updated 42 s ago", Format.freshness(Freshness.Updated(t), t + 42_000))
        assertEquals("Updated just now", Format.freshness(Freshness.Updated(t), t + 2_000))
        assertEquals("Updated 5 min ago", Format.freshness(Freshness.Updated(t), t + 5 * 60_000 + 10_000))
        assertTrue(Format.freshness(Freshness.Updated(t), t + 2 * 3_600_000).startsWith("Updated at "))
    }

    @Test
    fun aQuietReadShowsAsUpdatingOnlyWhenItTakesAWhile() {
        val ok = listOf(Section("m", fetchedAt = t))
        assertEquals(Freshness.Updated(t), Freshness.of(ok, busySince = t + 30_000, now = t + 30_400))
        assertEquals(Freshness.Updating, Freshness.of(ok, busySince = t + 30_000, now = t + 31_200))
        assertEquals(Freshness.Updating, Freshness.of(listOf(Section<String>()), busySince = t, now = t))   // first read: at once
        assertEquals(Freshness.Never, Freshness.of(listOf(Section<String>()), null, t))
        assertEquals("Updating…", Format.freshness(Freshness.Updating, t))
    }

    @Test
    fun aFailedReadKeepsTheOldDataAndSaysSo() {
        val sections = listOf(Section("m", fetchedAt = t, error = offline), Section("d", fetchedAt = t + 60_000))
        val f = Freshness.of(sections, null, t + 600_000)
        assertEquals(Freshness.Unreachable(t), f)
        assertEquals("Can't reach Vorath · showing ${Format.clock(t)}", Format.freshness(f, t + 600_000))
        assertEquals("Can't reach Vorath", Format.freshness(Freshness.of(listOf(Section<String>(error = offline)), null, t), t))
    }

    @Test
    fun aRefusedSectionIsAnAnswerNotAFailure() {
        val f = Freshness.of(listOf(Section("m", fetchedAt = t), Section<String>(refused = "no", fetchedAt = t)), null, t + 1_000)
        assertEquals(Freshness.Updated(t), f)
    }

    @Test
    fun theLoopReadsEvery30sSlowsForASlowServerAndBacksOff() {
        assertEquals(30_000L, Cadence.next(0, 200))
        assertEquals(80_000L, Cadence.next(0, 8_000))                // 10 x the answer time
        assertEquals(listOf(30_000L, 60_000L, 120_000L, 300_000L, 300_000L), (1..5).map { Cadence.next(it, 100) })
        assertEquals(150_000L, Cadence.next(1, 15_000))              // the slow-server rule holds during backoff too
    }

    @Test
    fun needKeysStayWithTheirRowAndNeverRepeat() {
        val a = Need("bad", "3 parcels refused since 2026-11-09", "/orders", "Open")
        val b = Need("warn", "Ads spend is up", "/ads", "Open")
        val first = RowKeys.needs(listOf(a, b))
        val later = RowKeys.needs(listOf(b, a.copy(text = "5 parcels refused since 2026-11-10")))
        assertEquals(first[0], later[1])
        assertEquals(first[1], later[0])
        val twice = RowKeys.needs(listOf(b, b))
        assertEquals(2, twice.toSet().size)
    }
}
