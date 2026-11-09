package com.vorath.mobile

import com.vorath.mobile.data.Parse
import com.vorath.mobile.data.Signals
import com.vorath.mobile.data.VorathApi
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The answers in src/test/resources were recorded from a Vorath server running its golden fixtures. */
class ParseTest {
    private fun recorded(name: String) = JSONObject(javaClass.getResource("/$name")!!.readText())

    @Test
    fun theFourNumbersComeThroughAsTheServerSentThem() {
        val m = Parse.money(recorded("state.json"))
        assertEquals(listOf("placed", "cash_in", "net_sales", "net_profit"), m.four.map { it.key })
        val placed = m.four[0]
        assertEquals(461241.0, placed.egp, 0.0)
        assertEquals(388, placed.count)
        assertEquals(651147.0, placed.previous!!, 0.0)
        assertEquals(-29, placed.changePct)                      // the web page's rule: round(100 * (now - prev) / |prev|)
        val profit = m.four[3]
        assertNull(profit.count)
        assertTrue(profit.estimated)
        assertEquals(30, m.days)
        assertEquals("2026-10-10", m.from)
        assertEquals("2026-11-08", m.to)
        assertEquals(30, m.cash.size)
        assertEquals(12, m.missingInputs)
    }

    @Test
    fun theDeckSaysWhatNeedsThePerson() {
        val d = Parse.deck(recorded("deck.json"))
        assertEquals("trouble", d.state)
        assertEquals("Needs you now", d.word)
        assertEquals(2, d.needs.size)
        assertEquals(listOf("no_orders"), d.alarms.filter { it.firing }.map { it.key })
        assertEquals(6, d.alarms.size)
        assertEquals("04:00", d.today.first().at)
    }

    @Test
    fun aFiringAlarmIsToldOnceAndAgainOnlyAfterItCleared() {
        val d = Parse.deck(recorded("deck.json"))
        val now = Signals.of(d)
        assertEquals(listOf("alarm:no_orders"), now.map { it.id })   // the "No orders: ..." need repeats the alarm; the other need is only a warning
        assertEquals(1, Signals.fresh(now, emptySet()).size)
        assertTrue(Signals.fresh(now, now.map { it.id }.toSet()).isEmpty())
        val cleared = Signals.of(d.copy(alarms = d.alarms.map { it.copy(firing = false) }, needs = emptyList()))
        assertTrue(cleared.isEmpty())
        assertEquals(1, Signals.fresh(now, cleared.map { it.id }.toSet()).size)
    }

    @Test
    fun aBadNeedKeepsItsIdWhenOnlyItsNumbersMove() {
        val d = Parse.deck(recorded("deck.json"))
        fun withNeed(text: String) = d.copy(alarms = emptyList(), needs = listOf(d.needs[0].copy(level = "bad", text = text)))
        val a = Signals.of(withNeed("3 parcels refused since 2026-11-09 10:00"))
        val b = Signals.of(withNeed("5 parcels refused since 2026-11-10 14:30"))
        assertEquals(a.single().id, b.single().id)
    }

    @Test
    fun theSignedInPerson() {
        val me = Parse.me(recorded("me.json"))
        assertEquals("owner", me.user)
        assertEquals("ANJA", me.client)
        assertFalse(me.setup)
    }

    @Test
    fun theAddressIsHttpsOnly() {
        assertEquals("https://vorath.example.com", VorathApi.address(" vorath.example.com/ "))
        assertEquals("https://vorath.example.com", VorathApi.address("https://vorath.example.com/login?next=%2F"))
        assertEquals("https://vorath.example.com:8443", VorathApi.address("vorath.example.com:8443"))
        assertNull(VorathApi.address("http://vorath.example.com"))
        assertEquals("http://127.0.0.1:8790", VorathApi.address("http://127.0.0.1:8790", allowHttp = true))
        assertNull(VorathApi.address(""))
        assertNull(VorathApi.address("my vorath"))
    }

    @Test
    fun theSessionCookieIsReadFromSetCookie() {
        assertEquals("abc123", VorathApi.cookieValue("vorath_sid=abc123; Path=/; HttpOnly; SameSite=Strict; Max-Age=1209600", "vorath_sid"))
        assertNull(VorathApi.cookieValue("other=1; Path=/", "vorath_sid"))
    }
}
