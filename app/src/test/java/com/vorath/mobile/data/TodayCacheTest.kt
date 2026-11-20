package com.vorath.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

class TodayCacheTest {
    @get:Rule val tmp = TemporaryFolder()

    /** The same AES-GCM layout as data.Keystore, with a key in memory instead of the Android Keystore. */
    private class SoftSealer : Sealer {
        private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        override fun seal(plain: ByteArray): ByteArray {
            val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key) }
            return c.iv + c.doFinal(plain)
        }
        override fun open(sealed: ByteArray): ByteArray? = try {
            Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, sealed, 0, 12)) }
                .doFinal(sealed, 12, sealed.size - 12)
        } catch (e: Exception) {
            null
        }
    }

    private val sealer = SoftSealer()
    private val owner = TodayCache.owner("https://vorath.example.com", "owner")
    private fun recorded(name: String) = javaClass.getResource("/$name")!!.readText()
    private fun snapshot() = Snapshot(
        owner,
        me = Cached("/api/me", recorded("me.json"), 1L),
        state = Cached("/api/state?days=30", recorded("state.json"), 2L, etag = "\"s1\""),
        deck = Cached("/api/autopilot/deck", recorded("deck.json"), 3L, lastModified = "Thu, 19 Nov 2026 08:00:00 GMT"),
    )

    @Test
    fun theAnswersComeBackExactlyAsSaved() {
        val c = TodayCache(tmp.root, sealer)
        assertTrue(c.save(snapshot(), c.token()))
        assertEquals(snapshot(), TodayCache(tmp.root, sealer).load(owner))
        assertFalse(File(tmp.root, "today.cache.tmp").exists())
        // Encrypted on disk: no figure from the answers is readable in the file.
        assertFalse(File(tmp.root, "today.cache").readBytes().toString(Charsets.ISO_8859_1).contains("net_profit"))
    }

    @Test
    fun anotherServerOrPersonNeverSeesIt() {
        val c = TodayCache(tmp.root, sealer)
        c.save(snapshot(), c.token())
        assertNull(c.load(TodayCache.owner("https://vorath.example.com", "someone")))
        assertFalse(File(tmp.root, "today.cache").exists())         // and it is gone, not only hidden
        c.save(snapshot(), c.token())
        assertNull(c.load(TodayCache.owner("https://other.example.com", "owner")))
        assertEquals(owner, TodayCache.owner("HTTPS://Vorath.Example.com ", "owner"))
    }

    @Test
    fun aClearWinsOverASaveThatWasAlreadyUnderWay() {
        val c = TodayCache(tmp.root, sealer)
        val token = c.token()
        c.clear()                                                  // sign-out while a read was in flight
        assertFalse(c.save(snapshot(), token))
        assertNull(c.load(owner))
    }

    @Test
    fun aFileItCannotOpenIsDropped() {
        val c = TodayCache(tmp.root, sealer)
        c.save(snapshot(), c.token())
        assertNull(TodayCache(tmp.root, SoftSealer()).load(owner))   // another key: a reset keystore
        File(tmp.root, "today.cache").writeBytes(byteArrayOf(1, 2, 3))
        assertNull(c.load(owner))
        assertNull(c.load(owner))
    }

    @Test
    fun aMissingSectionStaysMissing() {
        val c = TodayCache(tmp.root, sealer)
        c.save(Snapshot(owner, state = snapshot().state), c.token())
        val back = c.load(owner)!!
        assertNull(back.deck)
        assertNull(back.me)
        assertEquals("\"s1\"", back.state!!.etag)
        assertNull(back.state!!.lastModified)
        // The kept body goes through the one parser like a fresh answer.
        assertEquals(4, Parse.money(org.json.JSONObject(back.state!!.body)).four.size)
    }
}
