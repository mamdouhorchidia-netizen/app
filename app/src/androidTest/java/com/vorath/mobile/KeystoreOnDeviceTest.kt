package com.vorath.mobile

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.vorath.mobile.data.Cached
import com.vorath.mobile.data.Keystore
import com.vorath.mobile.data.Session
import com.vorath.mobile.data.SessionStore
import com.vorath.mobile.data.Snapshot
import com.vorath.mobile.data.TodayCache
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.security.KeyStore
import javax.crypto.SecretKey

/** The real Android Keystore (Robolectric has none): the session id and the cached answers are sealed with a key that never leaves it. */
@RunWith(AndroidJUnit4::class)
class KeystoreOnDeviceTest {
    @Before
    fun clean() = Device.fresh()

    @Test
    fun sealsAndOpensWithAKeyThatCannotBeExported() {
        val plain = "vorath_sid=abc123".toByteArray()
        val sealed = Keystore.seal(plain)
        assertFalse(String(sealed, Charsets.ISO_8859_1).contains("abc123"))
        assertArrayEquals(plain, Keystore.open(sealed))
        assertFalse(Keystore.seal(plain).contentEquals(sealed))               // a fresh IV each time

        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        assertTrue(ks.containsAlias("vorath_session"))
        assertNull((ks.getKey("vorath_session", null) as SecretKey).encoded)   // the key material stays in the Keystore
    }

    @Test
    fun aTamperedValueDoesNotOpen() {
        val sealed = Keystore.seal("secret".toByteArray())
        sealed[sealed.size - 1] = (sealed[sealed.size - 1].toInt() xor 1).toByte()
        assertNull(Keystore.open(sealed))
    }

    @Test
    fun theSessionIsKeptSealedAndSurvivesANewStore() {
        val s = Session("https://vorath.example.com", "sid-0123456789", "owner", "Owner", "ANJA")
        SessionStore(Device.app).save(s)
        val raw = Device.app.getSharedPreferences("vorath", Context.MODE_PRIVATE).getString("sid", null)
        assertNotNull(raw)
        assertFalse(raw!!.contains("sid-0123456789"))
        assertEquals(s, SessionStore(Device.app).load())                     // as after the app restarts
        SessionStore(Device.app).clear()
        assertNull(SessionStore(Device.app).load())
        assertEquals("https://vorath.example.com", SessionStore(Device.app).lastBase)
    }

    @Test
    fun theCacheIsSealedOnDiskAndBoundToItsOwner() {
        val cache = TodayCache(Device.app.noBackupFilesDir, Keystore)
        val owner = TodayCache.owner("https://vorath.example.com", "owner")
        val snap = Snapshot(owner, deck = Cached("/api/autopilot/deck", FakeVorath.recorded("deck.json"), 1_800_000_000_000))
        assertTrue(cache.save(snap, cache.token()))
        val onDisk = Device.app.noBackupFilesDir.resolve("today.cache").readBytes().toString(Charsets.ISO_8859_1)
        assertFalse(onDisk.contains("Needs you now"))
        assertEquals(snap, TodayCache(Device.app.noBackupFilesDir, Keystore).load(owner))
        assertNull(TodayCache(Device.app.noBackupFilesDir, Keystore).load(TodayCache.owner("https://vorath.example.com", "someone")))
    }
}
