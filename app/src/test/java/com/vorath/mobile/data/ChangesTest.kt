package com.vorath.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ChangesTest {
    private fun fig(key: String, egp: Double) = Figure(key, key, egp, null, "", false, null)
    private fun money(vararg f: Figure) = Money("2026-11-09", 30, "2026-10-10", "2026-11-08", f.toList(), emptyList(), emptyList(), 0)
    private fun alarm(key: String, firing: Boolean, on: Boolean = true) = Alarm(key, key, "text", firing, on, "")
    private fun deck(alarms: List<Alarm>, needs: List<Need> = emptyList(), word: String = "Flying") = Deck("flying", word, needs, alarms, emptyList())

    @Test
    fun firstReadChangesNothing() {
        assertEquals(emptySet<String>(), Changes.between(null, money(fig("cash_in", 1.0)), null, deck(listOf(alarm("a", true)))))
        assertEquals(emptyList<Alarm>(), Changes.newlyFiring(null, deck(listOf(alarm("a", true)))))
    }

    @Test
    fun sameValuesChangeNothing() {
        val d = deck(listOf(alarm("a", false)), listOf(Need("bad", "No orders since 2026-11-01", "/x", "Open")))
        assertEquals(emptySet<String>(), Changes.between(money(fig("a", 1.0)), money(fig("a", 1.0)), d, d.copy()))
    }

    @Test
    fun figuresAlarmsNeedsAndStatusByKey() {
        val oldNeed = Need("bad", "No orders since 2026-11-01", "/x", "Open")
        val newNeed = Need("bad", "No orders since 2026-11-02", "/x", "Open")       // same key (digits ignored), new text
        val added = Need("warn", "Add your phone", "/settings", "Add")
        val got = Changes.between(
            money(fig("placed", 1.0), fig("cash_in", 2.0)), money(fig("placed", 1.0), fig("cash_in", 3.0)),
            deck(listOf(alarm("a", false), alarm("b", false)), listOf(oldNeed)),
            deck(listOf(alarm("a", true), alarm("b", false)), listOf(newNeed, added), word = "Needs you"),
        )
        val needKeys = RowKeys.needs(listOf(newNeed, added))
        assertEquals(setOf("figure:cash_in", "alarm:a", Changes.STATUS) + needKeys, got)
    }

    @Test
    fun onlyNewlyFiringAlarmsAreTold() {
        val before = deck(listOf(alarm("a", true), alarm("b", false), alarm("c", false)))
        val after = deck(listOf(alarm("a", true), alarm("b", true), alarm("c", true, on = false), alarm("d", true)))
        assertEquals(listOf("b", "d"), Changes.newlyFiring(before, after).map { it.key })
        assertTrue(Changes.newlyFiring(after, before).isEmpty())
    }
}
