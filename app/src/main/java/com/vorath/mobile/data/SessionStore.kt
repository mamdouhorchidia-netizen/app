package com.vorath.mobile.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class Session(val base: String, val sessionId: String, val user: String, val name: String, val client: String)

/**
 * The sign-in kept on the phone: the server address and the session id, the id encrypted with a key that never
 * leaves the Android Keystore. The password and the 2-step code are never stored.
 */
class SessionStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("vorath", Context.MODE_PRIVATE)

    fun load(): Session? {
        val base = prefs.getString("base", null) ?: return null
        val sid = prefs.getString("sid", null)?.let(::open) ?: return null
        return Session(base, sid, prefs.getString("user", "").orEmpty(), prefs.getString("name", "").orEmpty(), prefs.getString("client", "").orEmpty())
    }

    fun save(s: Session) {
        prefs.edit().putString("base", s.base).putString("sid", seal(s.sessionId)).putString("user", s.user)
            .putString("name", s.name).putString("client", s.client).remove("told").remove("told_signed_out").apply()
    }

    /** Forget the session; the address and the user name stay to fill the sign-in form. */
    fun clear() {
        prefs.edit().remove("sid").remove("told").apply()
    }

    val lastBase: String get() = prefs.getString("base", "").orEmpty()
    val lastUser: String get() = prefs.getString("user", "").orEmpty()

    /** The signal ids the last background check saw (data.Signals). */
    var told: Set<String>
        get() = prefs.getStringSet("told", emptySet()).orEmpty()
        set(v) = prefs.edit().putStringSet("told", v).apply()

    var toldSignedOut: Boolean
        get() = prefs.getBoolean("told_signed_out", false)
        set(v) = prefs.edit().putBoolean("told_signed_out", v).apply()

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val g = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        g.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return g.generateKey()
    }

    private fun seal(plain: String): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val out = c.iv + c.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    /** null when the stored value cannot be opened (a restored backup, a reset keystore): the person signs in again. */
    private fun open(sealed: String): String? = try {
        val raw = Base64.decode(sealed, Base64.NO_WRAP)
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, 12)) }
        c.doFinal(raw, 12, raw.size - 12).toString(Charsets.UTF_8)
    } catch (e: Exception) {
        null
    }

    private companion object {
        const val ALIAS = "vorath_session"
    }
}
