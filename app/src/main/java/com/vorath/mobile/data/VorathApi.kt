package com.vorath.mobile.data

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL

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

    fun me(): Me = Parse.me(call("GET", "/api/me").json)

    fun money(days: Int = 30): Money = Parse.money(call("GET", "/api/state?days=$days").json)

    fun deck(): Deck = Parse.deck(call("GET", "/api/autopilot/deck").json)

    private class Answer(val json: JSONObject, val cookies: List<String>)

    private fun call(method: String, path: String, body: JSONObject? = null, withSession: Boolean = true): Answer {
        val c = try {
            URL(base + path).openConnection() as HttpURLConnection
        } catch (e: Exception) {
            throw VorathError.Offline("That address cannot be opened.")
        }
        try {
            c.requestMethod = method
            c.connectTimeout = 15_000
            c.readTimeout = 60_000
            c.instanceFollowRedirects = false
            c.setRequestProperty("Accept", "application/json")
            c.setRequestProperty("User-Agent", agent)
            if (withSession && sessionId != null) c.setRequestProperty("Cookie", "$COOKIE=$sessionId")
            if (body != null) {
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.setFixedLengthStreamingMode(bytes.size)
                c.outputStream.use { it.write(bytes) }
            }
            val status = c.responseCode
            val text = (if (status < 400) c.inputStream else c.errorStream)?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
            val json = try {
                JSONObject(text)
            } catch (e: Exception) {
                null
            }
            val said = json?.optString("error").orEmpty()
            return when {
                status == 401 -> throw VorathError.SignedOut(said.ifBlank { "Sign in first." })
                status == 403 -> throw VorathError.Refused(said.ifBlank { "Your role cannot open this." })
                status in 200..299 && json != null -> Answer(json, c.headerFields.filterKeys { it.equals("Set-Cookie", true) }.values.flatten())
                else -> throw VorathError.NotVorath(said.ifBlank { "Vorath did not answer as expected ($status)." })
            }
        } catch (e: IOException) {
            throw VorathError.Offline("Cannot reach the server. Check the connection and the address.")
        } finally {
            c.disconnect()
        }
    }

    companion object {
        const val COOKIE = "vorath_sid"

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
