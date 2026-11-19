package com.vorath.mobile.data

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.Collections

/** The client against a small HTTP server on this machine: conditional reads, 304, keep-alive. */
class VorathApiTest {
    private lateinit var server: TinyHttpServer
    private val ports = Collections.synchronizedList(mutableListOf<Int>())
    private val conditional = Collections.synchronizedList(mutableListOf<String?>())
    @Volatile private var deck = """{"state":"flying","word":"Flying","needs":[],"alarms":[],"today":[]}"""
    @Volatile private var withEtag = true
    private val base get() = "http://127.0.0.1:${server.port}"

    @Before
    fun start() {
        server = TinyHttpServer { x ->
            if (x.path != "/api/autopilot/deck") return@TinyHttpServer TinyHttpServer.Response(404)
            ports += x.remotePort
            val tag = "\"" + deck.hashCode() + "\""
            val asked = x.header("If-None-Match")
            conditional += asked
            if (x.header("Cookie") != "vorath_sid=sid") {
                TinyHttpServer.Response(401, """{"error":"Sign in first."}""".toByteArray())
            } else if (withEtag && asked == tag) {
                TinyHttpServer.Response(304)
            } else {
                val h = mutableMapOf("Content-Type" to "application/json")
                if (withEtag) h["ETag"] = tag
                TinyHttpServer.Response(200, deck.toByteArray(), h)
            }
        }
    }

    @After
    fun stop() = server.close()

    @Test
    fun anUnchangedAnswerComesBackAs304WithTheBodyItHad() {
        val api = VorathApi(base, "sid")
        val first = api.deckRaw()
        assertFalse(first.unchanged)
        val second = api.deckRaw()
        assertTrue(second.unchanged)
        assertEquals(first.body, second.body)
        assertEquals(listOf(null, first.etag), conditional.toList())
        assertEquals("Flying", Parse.deck(second.json).word)

        deck = """{"state":"trouble","word":"Needs you now","needs":[],"alarms":[],"today":[]}"""
        val third = api.deckRaw()
        assertFalse(third.unchanged)
        assertEquals("Needs you now", api.deck().word)
    }

    @Test
    fun aBodyFromTheCacheIsAskedForConditionally() {
        val warm = VorathApi(base, "sid").deckRaw()
        val api = VorathApi(base, "sid")
        api.remember(warm.path, warm.body, warm.etag, warm.lastModified)
        assertTrue(api.deckRaw().unchanged)
    }

    @Test
    fun withoutAnEtagTheSameBytesStillCountAsUnchanged() {
        withEtag = false
        val api = VorathApi(base, "sid")
        assertFalse(api.deckRaw().unchanged)
        assertTrue(api.deckRaw().unchanged)
        assertEquals(listOf<String?>(null, null), conditional.toList())
    }

    @Test
    fun theConnectionIsKeptForTheNextCall() {
        val api = VorathApi(base, "sid")
        repeat(3) { api.deckRaw() }
        assertEquals(1, ports.toSet().size)                         // one socket for all three reads
    }

    @Test
    fun anEndedSessionIsStillSignedOut() {
        try {
            VorathApi(base, "old").deckRaw()
            fail("read without a session")
        } catch (e: VorathError.SignedOut) {
            assertEquals("Sign in first.", e.message)
        }
    }

    @Test
    fun aServerThatIsNotThereIsOffline() {
        val gone = base
        server.close()
        try {
            VorathApi(gone, "sid").deckRaw()
            fail("read from nothing")
        } catch (e: VorathError.Offline) {
        }
    }
}
