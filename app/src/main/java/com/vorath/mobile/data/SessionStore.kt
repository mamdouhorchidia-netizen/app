package com.vorath.mobile.data

import android.content.Context
import android.util.Base64

data class Session(val base: String, val sessionId: String, val user: String, val name: String, val client: String)

/**
 * The sign-in kept on the phone: the server address and the session id, the id encrypted with a key that never
 * leaves the Android Keystore (data.Keystore). The password and the 2-step code are never stored.
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

    private fun seal(plain: String): String = Base64.encodeToString(Keystore.seal(plain.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)

    /** null when the stored value cannot be opened (a restored backup, a reset keystore): the person signs in again. */
    private fun open(sealed: String): String? = try {
        Keystore.open(Base64.decode(sealed, Base64.NO_WRAP))?.toString(Charsets.UTF_8)
    } catch (e: Exception) {
        null
    }
}
