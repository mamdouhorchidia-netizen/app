package com.vorath.mobile.data

import java.net.URI
import java.net.URLDecoder

/**
 * What the in-app Vorath page (ui.WebScreen) does with a link, a redirect or a download. Plain JVM code (java.net.URI,
 * not android.net.Uri), so it is tested without a phone.
 */
object WebLinks {
    enum class Kind {
        /** A page of this Vorath: opens in the app. */
        INSIDE,

        /** This Vorath's sign-in page: the session has ended (the server sends a signed-out visitor there). */
        SIGN_IN,

        /** Another site, mail or phone: handed to the phone's browser or app. */
        OUTSIDE,

        /** Anything else (javascript:, file:, intent:, data: …): not followed. */
        BLOCKED,
    }

    /** Vorath's sign-in page, where it sends a visitor without a session (also named in AppModel's sign-in messages). */
    const val SIGN_IN_PATH = "/login"

    fun classify(url: String, base: String): Kind {
        val u = parse(url) ?: return Kind.BLOCKED
        return when (u.scheme?.lowercase()) {
            "https", "http" -> when {
                !sameOrigin(u, base) -> Kind.OUTSIDE
                isSignIn(u) -> Kind.SIGN_IN
                else -> Kind.INSIDE
            }
            "mailto", "tel", "sms", "geo" -> Kind.OUTSIDE
            else -> Kind.BLOCKED
        }
    }

    /** The same scheme, host and port as the signed-in server: only those get the session cookie. */
    fun sameOrigin(url: String, base: String): Boolean = parse(url)?.let { sameOrigin(it, base) } ?: false

    private fun sameOrigin(u: URI, base: String): Boolean {
        val b = parse(base) ?: return false
        return u.scheme.equals(b.scheme, true) && u.host != null && u.host.equals(b.host, true) && port(u) == port(b)
    }

    private fun isSignIn(u: URI): Boolean {
        val path = (u.path ?: "").trimEnd('/')
        return path.equals(SIGN_IN_PATH, true)
    }

    private fun port(u: URI): Int = when {
        u.port > 0 -> u.port
        u.scheme.equals("https", true) -> 443
        else -> 80
    }

    private fun parse(url: String): URI? = try {
        URI(url.trim())
    } catch (e: Exception) {
        null
    }

    /**
     * The name a download is saved under: the server's Content-Disposition name (RFC 6266 `filename*` before
     * `filename`), else the last part of the address, else "vorath-download"; with an extension from the type when it
     * has none, and without characters a file name cannot hold.
     */
    fun downloadName(url: String, contentDisposition: String?, mimeType: String?): String {
        val fromHeader = contentDisposition?.let(::dispositionName)
        val fromUrl = parse(url)?.path?.substringAfterLast('/')?.takeIf { it.isNotBlank() }
        var name = clean(fromHeader ?: fromUrl ?: "")
        if (name.isEmpty() || name.all { it == '.' }) name = "vorath-download"
        if (!name.contains('.') || name.endsWith('.')) {
            extensionOf(mimeType)?.let { name = name.trimEnd('.') + "." + it }
        }
        if (name.length > MAX_NAME) {
            val ext = name.substringAfterLast('.', "").takeIf { it.isNotEmpty() && it.length <= 8 }
            name = if (ext != null) name.take(MAX_NAME - ext.length - 1).trimEnd('.') + "." + ext else name.take(MAX_NAME)
        }
        return name
    }

    private fun dispositionName(header: String): String? {
        val parts = header.split(';').map { it.trim() }
        parts.firstOrNull { it.startsWith("filename*=", true) }?.let { p ->
            val v = p.substringAfter('=').trim().trim('"')
            val charset = v.substringBefore('\'', "UTF-8").ifBlank { "UTF-8" }
            val encoded = v.substringAfterLast('\'')
            runCatching { URLDecoder.decode(encoded.replace("+", "%2B"), charset) }.getOrNull()?.takeIf { it.isNotBlank() }?.let { return it }
        }
        return parts.firstOrNull { it.startsWith("filename=", true) }?.substringAfter('=')?.trim()?.trim('"')?.takeIf { it.isNotBlank() }
    }

    /** Only the last path part, without separators, control characters or the ones Android's storage refuses. */
    private fun clean(name: String): String {
        val decoded = runCatching { URLDecoder.decode(name.replace("+", "%2B"), "UTF-8") }.getOrDefault(name)
        return decoded.substringAfterLast('/').substringAfterLast('\\')
            .map { if (it.code < 32 || it in "\\/:*?\"<>|") '_' else it }
            .joinToString("").trim().trimStart('.')
    }

    private fun extensionOf(mimeType: String?): String? = when (mimeType?.substringBefore(';')?.trim()?.lowercase()) {
        "text/csv" -> "csv"
        "application/pdf" -> "pdf"
        "application/json" -> "json"
        "application/zip" -> "zip"
        "text/plain" -> "txt"
        "text/html" -> "html"
        "image/png" -> "png"
        "image/jpeg" -> "jpg"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" -> "xlsx"
        "application/vnd.ms-excel" -> "xls"
        else -> null
    }

    private const val MAX_NAME = 120
}
