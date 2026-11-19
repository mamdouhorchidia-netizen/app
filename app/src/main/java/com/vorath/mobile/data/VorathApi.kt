package com.vorath.mobile.data

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.concurrent.ConcurrentHashMap

/** Why a call to Vorath did not answer with data. */
sealed class VorathError(message: String) : Exception(message) {
    /** No session, or the server no longer accepts it (401). */
    class SignedOut(message: String) : VorathError(message)

    /** Signed in, but the role or the plan does not open this (403). */
    class Refused(message: String) : VorathError(message)

    /** The address answered, but not like a Vorath server. */
    class NotVorath(message: String) : VorathError(message)

    /** The server could not be reached. */
    class Offline(message: String) : VorathError(message)
}

class SignIn(val sessionId: String, val setup: Boolean, val mustChange: Boolean)

/**
 * Vorath's server API (fleet.server and face.app), as the phone uses it. Read-only apart from sign-in and sign-out.
 * The session is the server's own `vorath_sid` cookie; the app keeps that, never the password.
 *
 * Keep one client per session: it remembers each GET's body with the server's ETag / Last-Modified and sends them
 * back as If-None-Match / If-Modified-Since, so an unchanged answer can come back as an empty 304. It is safe to call
 * from several threads at once.
 */
class VorathApi(val base: String, private val sessionId: String? = null, private val agent: String = "VorathMobile/0.1") {

    fun health(): String {
        val o = try {
            call("GET", "/healthz", null, withSession = false).json
        } catch (e: VorathError.Offline) {
            throw e
        } catch (e: VorathError) {
            throw VorathError.NotVorath("No Vorath at this address.")
        }
        if (!o.optBoolean("ok")) throw VorathError.NotVorath("No Vorath at this address.")
        return o.optString("version")
    }

    fun signIn(user: String, password: String, code: String?): SignIn {
        val body = JSONObject().put("user", user.trim()).put("password", password)
        if (!code.isNullOrBlank()) body.put("code", code.trim())
        val r = call("POST", "/api/auth/login", body, withSession = false)
        val sid = r.cookies.firstNotNullOfOrNull { cookieValue(it, COOKIE) }?.takeIf { it.isNotEmpty() }
            ?: throw VorathError.NotVorath("The server did not start a session.")
        return SignIn(sid, r.json.optBoolean("setup"), r.json.optBoolean("must_change"))
    }

    fun signOut() {
        call("POST", "/api/auth/logout", JSONObject())
    }

    fun me(): Me = Parse.me(meRaw().json)

    fun money(days: Int = 30): Money = Parse.money(stateRaw(days).json)

    fun deck(): Deck = Parse.deck(deckRaw().json)

    /** The answers as the server sent them, for the cache; [Raw.unchanged] says the body is the one this client last saw. */
    fun meRaw(): Raw = read(ME)

    fun stateRaw(days: Int = 30): Raw = read(state(days))

    fun deckRaw(): Raw = read(DECK)

    /**
     * Give the client a body it got earlier (from the cache), with the server's validators for it, so the next read of
     * [path] can ask "changed since?" and take a 304. A server that sends no ETag or Last-Modified simply answers 200.
     */
    fun remember(path: String, body: String, etag: String?, lastModified: String?) {
        seen[path] = Seen(body, etag, lastModified)
    }

    /** One answer of a GET: the body text, and whether it is the one this client already had (304, or the same bytes). */
    class Raw(val path: String, val body: String, val unchanged: Boolean, val etag: String?, val lastModified: String?) {
        val json: JSONObject get() = JSONObject(body)
    }

    private class Seen(val body: String, val etag: String?, val lastModified: String?)

    private val seen = ConcurrentHashMap<String, Seen>()

    private fun read(path: String): Raw {
        val before = seen[path]
        val r = call("GET", path, conditional = before)
        if (r.status == 304) {
            if (before == null) throw VorathError.NotVorath("Vorath did not answer as expected (304).")
            return Raw(path, before.body, true, before.etag, before.lastModified)
        }
        val etag = r.header("ETag")
        val lastModified = r.header("Last-Modified")
        seen[path] = Seen(r.text, etag, lastModified)          // also without validators: an identical body still counts as unchanged
        return Raw(path, r.text, before?.body == r.text, etag, lastModified)
    }

    private class Answer(val status: Int, val text: String, val json: JSONObject, private val headers: Map<String?, List<String>>) {
        val cookies: List<String> get() = headers.filterKeys { it.equals("Set-Cookie", true) }.values.flatten()
        fun header(name: String): String? = headers.entries.firstOrNull { it.key.equals(name, true) }?.value?.firstOrNull()?.takeIf { it.isNotBlank() }
    }

    /**
     * One request. The connection is not disconnected: reading the body to its end and closing the stream hands the
     * socket back to the platform's keep-alive pool, so the next call to the same server skips the TCP and TLS setup.
     */
    private fun call(method: String, path: String, body: JSONObject? = null, withSession: Boolean = true, conditional: Seen? = null): Answer {
        val c = try {
            URL(base + path).openConnection() as HttpURLConnection
        } catch (e: Exception) {
            throw VorathError.Offline("That address cannot be opened.")
        }
        try {
            c.requestMethod = method
            c.connectTimeout = CONNECT_TIMEOUT_MS
            c.readTimeout = READ_TIMEOUT_MS
            c.instanceFollowRedirects = false
            c.useCaches = false
            c.setRequestProperty("Accept", "application/json")
            c.setRequestProperty("User-Agent", agent)
            if (withSession && sessionId != null) c.setRequestProperty("Cookie", "$COOKIE=$sessionId")
            conditional?.etag?.let { c.setRequestProperty("If-None-Match", it) }
            conditional?.lastModified?.let { c.setRequestProperty("If-Modified-Since", it) }
            if (body != null) {
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.setFixedLengthStreamingMode(bytes.size)
                c.outputStream.use { it.write(bytes) }
            }
            val status = c.responseCode
            val text = (if (status < 400) c.inputStream else c.errorStream)?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
            if (status == 304) return Answer(status, "", JSONObject(), c.headerFields)
            val json = try {
                JSONObject(text)
            } catch (e: Exception) {
                null
            }
            val said = json?.optString("error").orEmpty()
            return when {
                status == 401 -> throw VorathError.SignedOut(said.ifBlank { "Sign in first." })
                status == 403 -> throw VorathError.Refused(said.ifBlank { "Your role cannot open this." })
                status in 200..299 && json != null -> Answer(status, text, json, c.headerFields)
                else -> throw VorathError.NotVorath(said.ifBlank { "Vorath did not answer as expected ($status)." })
            }
        } catch (e: IOException) {
            c.disconnect()                                     // a broken exchange: do not hand this socket back to the pool
            throw VorathError.Offline("Cannot reach the server. Check the connection and the address.")
        }
    }

    companion object {
        const val COOKIE = "vorath_sid"
        const val ME = "/api/me"
        const val DECK = "/api/autopilot/deck"
        fun state(days: Int = 30) = "/api/state?days=$days"

        const val CONNECT_TIMEOUT_MS = 8_000
        const val READ_TIMEOUT_MS = 15_000

        /**
         * The server address as the person typed it, made into "https://host[:port]". Plain http is accepted only when
         * [allowHttp] (a test server on this machine); null when it is not an address.
         */
        fun address(typed: String, allowHttp: Boolean = false): String? {
            var t = typed.trim().trimEnd('/')
            if (t.isEmpty() || t.any { it.isWhitespace() }) return null
            if (!t.contains("://")) t = "https://$t"
            val u = try {
                URI(t)
            } catch (e: Exception) {
                return null
            }
            val scheme = u.scheme?.lowercase()
            val host = u.host ?: return null
            if (scheme != "https" && !(scheme == "http" && allowHttp)) return null
            return "$scheme://$host" + (if (u.port > 0) ":${u.port}" else "")
        }

        internal fun cookieValue(setCookie: String, name: String): String? {
            val first = setCookie.substringBefore(';').trim()
            return if (first.startsWith("$name=")) first.substring(name.length + 1) else null
        }
    }
}
