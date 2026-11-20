package com.vorath.mobile.data

import com.vorath.mobile.data.WebLinks.Kind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WebLinksTest {
    private val base = "https://vorath.example.com"

    @Test
    fun pagesOfThisVorathOpenInside() {
        assertEquals(Kind.INSIDE, WebLinks.classify("https://vorath.example.com/autopilot", base))
        assertEquals(Kind.INSIDE, WebLinks.classify("https://VORATH.example.com/", base))
        assertEquals(Kind.INSIDE, WebLinks.classify("https://vorath.example.com:443/today?x=1#y", base))
        assertEquals(Kind.INSIDE, WebLinks.classify("https://vorath.example.com/login-help", base))
        assertEquals(Kind.INSIDE, WebLinks.classify("https://vorath.example.com/api/login", base))
    }

    @Test
    fun anotherHostPortOrSchemeIsOutside() {
        assertEquals(Kind.OUTSIDE, WebLinks.classify("https://example.com/", base))
        assertEquals(Kind.OUTSIDE, WebLinks.classify("https://evil.vorath.example.com/", base))
        assertEquals(Kind.OUTSIDE, WebLinks.classify("https://vorath.example.com.evil.io/", base))
        assertEquals(Kind.OUTSIDE, WebLinks.classify("https://vorath.example.com:8443/", base))
        assertEquals(Kind.OUTSIDE, WebLinks.classify("http://vorath.example.com/", base))
        assertEquals(Kind.OUTSIDE, WebLinks.classify("mailto:help@example.com", base))
        assertEquals(Kind.OUTSIDE, WebLinks.classify("tel:+201000000000", base))
    }

    @Test
    fun aPortOnTheServerAddressIsPartOfIt() {
        val dev = "https://127.0.0.1:8790"
        assertEquals(Kind.INSIDE, WebLinks.classify("https://127.0.0.1:8790/autopilot", dev))
        assertEquals(Kind.OUTSIDE, WebLinks.classify("https://127.0.0.1/autopilot", dev))
    }

    @Test
    fun otherSchemesAreNotFollowed() {
        assertEquals(Kind.BLOCKED, WebLinks.classify("javascript:alert(1)", base))
        assertEquals(Kind.BLOCKED, WebLinks.classify("file:///sdcard/x", base))
        assertEquals(Kind.BLOCKED, WebLinks.classify("intent://scan/#Intent;scheme=zxing;end", base))
        assertEquals(Kind.BLOCKED, WebLinks.classify("blob:https://vorath.example.com/1234", base))
        assertEquals(Kind.BLOCKED, WebLinks.classify("not a url", base))
    }

    @Test
    fun theSignInPageMeansTheSessionEnded() {
        assertEquals(Kind.SIGN_IN, WebLinks.classify("https://vorath.example.com/login", base))
        assertEquals(Kind.SIGN_IN, WebLinks.classify("https://vorath.example.com/login/", base))
        assertEquals(Kind.SIGN_IN, WebLinks.classify("https://vorath.example.com/login?next=%2Fautopilot", base))
        assertEquals(Kind.SIGN_IN, WebLinks.classify("https://vorath.example.com/LOGIN", base))
        // Another site's sign-in page is just another site.
        assertEquals(Kind.OUTSIDE, WebLinks.classify("https://accounts.example.com/login", base))
    }

    @Test
    fun onlyTheSameOriginGetsTheCookie() {
        assertTrue(WebLinks.sameOrigin("https://vorath.example.com/export.csv", base))
        assertFalse(WebLinks.sameOrigin("https://cdn.example.com/export.csv", base))
        assertFalse(WebLinks.sameOrigin("http://vorath.example.com/export.csv", base))
    }

    @Test
    fun theServersFileNameComesFirst() {
        assertEquals("sales march.csv", WebLinks.downloadName("$base/api/export?id=3", "attachment; filename=\"sales march.csv\"", "text/csv"))
        assertEquals("report.pdf", WebLinks.downloadName("$base/x", "attachment; filename=report.pdf", null))
        // RFC 6266: filename* (UTF-8, percent-encoded) wins over the plain one.
        assertEquals("مبيعات.csv", WebLinks.downloadName("$base/x", "attachment; filename=\"sales.csv\"; filename*=UTF-8''%D9%85%D8%A8%D9%8A%D8%B9%D8%A7%D8%AA.csv", null))
    }

    @Test
    fun withoutAHeaderTheAddressNamesTheFile() {
        assertEquals("orders-2026-11.csv", WebLinks.downloadName("$base/exports/orders-2026-11.csv?sig=abc", null, "text/csv"))
        assertEquals("cash in.xlsx", WebLinks.downloadName("$base/exports/cash%20in.xlsx", "", null))
    }

    @Test
    fun aMissingExtensionComesFromTheType() {
        assertEquals("export.csv", WebLinks.downloadName("$base/api/export", null, "text/csv; charset=utf-8"))
        assertEquals("vorath-download.pdf", WebLinks.downloadName("$base/", null, "application/pdf"))
        assertEquals("vorath-download", WebLinks.downloadName("$base/", null, null))
        assertEquals("export", WebLinks.downloadName("$base/api/export", null, "application/octet-stream"))
    }

    @Test
    fun aFileNameCannotEscapeOrHoldBadCharacters() {
        assertEquals("passwd", WebLinks.downloadName("$base/x", "attachment; filename=\"../../etc/passwd\"", null))
        assertEquals("a_b_c.csv", WebLinks.downloadName("$base/x", "attachment; filename=\"a:b*c.csv\"", null))
        assertEquals("vorath-download.csv", WebLinks.downloadName("$base/x", "attachment; filename=\"..\"", "text/csv"))
        val long = WebLinks.downloadName("$base/x", "attachment; filename=\"${"a".repeat(300)}.csv\"", null)
        assertEquals(120, long.length)
        assertTrue(long.endsWith(".csv"))
    }
}
