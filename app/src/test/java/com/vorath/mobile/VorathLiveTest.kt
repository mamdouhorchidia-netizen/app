package com.vorath.mobile

import com.vorath.mobile.data.VorathApi
import com.vorath.mobile.data.VorathError
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The app's client against a real Vorath server (python -m fleet). Skipped unless both are given:
 *   ./gradlew :app:testDebugUnitTest -Dvorath.url=http://127.0.0.1:8790 -Dvorath.login=/path/login.json
 * login.json: {"user", "password", "totp_secret"} of a person whose 2-step code is set up.
 */
class VorathLiveTest {
    private val url = System.getProperty("vorath.url").orEmpty()
    private val login = System.getProperty("vorath.login").orEmpty()

    @Test
    fun signInReadTodayAndSignOut() {
        assumeTrue(url.isNotEmpty() && login.isNotEmpty())
        val who = JSONObject(File(login).readText())
        val base = VorathApi.address(url, allowHttp = true)!!
        VorathApi(base).health()

        try {
            VorathApi(base).signIn(who.getString("user"), who.getString("password"), null)
            fail("signed in without the 2-step code")
        } catch (e: VorathError.SignedOut) {
            assertTrue(e.message!!.isNotBlank())
        }
        try {
            VorathApi(base, "not-a-session").money()
            fail("read the numbers without a session")
        } catch (e: VorathError.SignedOut) {
        }

        val s = VorathApi(base).signIn(who.getString("user"), who.getString("password"), totp(who.getString("totp_secret")))
        val api = VorathApi(base, s.sessionId)
        assertEquals(who.getString("user"), api.me().user)
        val m = api.money()
        assertEquals(4, m.four.size)
        assertEquals(m.days, m.cash.size)
        val d = api.deck()
        assertTrue(d.word.isNotBlank())
        assertTrue(d.alarms.isNotEmpty())

        api.signOut()
        try {
            api.me()
            fail("the session still opens after sign-out")
        } catch (e: VorathError.SignedOut) {
        }
    }

    private fun totp(secret: String): String {
        val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        val bits = secret.trimEnd('=').uppercase().map { alphabet.indexOf(it).toString(2).padStart(5, '0') }.joinToString("")
        val key = ByteArray(bits.length / 8) { bits.substring(it * 8, it * 8 + 8).toInt(2).toByte() }
        val mac = Mac.getInstance("HmacSHA1").apply { init(SecretKeySpec(key, "HmacSHA1")) }
        val h = mac.doFinal(ByteBuffer.allocate(8).putLong(System.currentTimeMillis() / 1000 / 30).array())
        val o = h.last().toInt() and 0x0f
        val n = ((h[o].toInt() and 0x7f) shl 24) or ((h[o + 1].toInt() and 0xff) shl 16) or ((h[o + 2].toInt() and 0xff) shl 8) or (h[o + 3].toInt() and 0xff)
        return (n % 1_000_000).toString().padStart(6, '0')
    }
}
