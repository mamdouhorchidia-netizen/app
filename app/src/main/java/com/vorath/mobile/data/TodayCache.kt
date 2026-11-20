package com.vorath.mobile.data

import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

/** Encrypts and opens bytes kept on the phone. [open] gives null when they cannot be opened (another key, tampered). */
interface Sealer {
    fun seal(plain: ByteArray): ByteArray
    fun open(sealed: ByteArray): ByteArray?
}

/** One answer as the server sent it, when the phone read it, and the server's validators for it (if it sent any). */
data class Cached(val path: String, val body: String, val fetchedAt: Long, val etag: String? = null, val lastModified: String? = null)

/** The last answers the phone has for one person on one server. [owner] is [TodayCache.owner]. */
data class Snapshot(val owner: String, val me: Cached? = null, val state: Cached? = null, val deck: Cached? = null)

/**
 * The last-known answers, so the Today page has something to show the moment the app opens. One file in
 * noBackupFilesDir (never in a backup), encrypted with [sealer], replaced atomically (write a temporary file, sync,
 * rename), and bound to the server and the person: [load] for anyone else gives null and drops the file.
 * The raw bodies are kept, not parsed objects, so data.Parse stays the only parser.
 */
class TodayCache(dir: File, private val sealer: Sealer) {
    private val file = File(dir, "today.cache")
    private val temp = File(dir, "today.cache.tmp")
    private var generation = 0

    /** Taken before a read starts and given to [save]: a [clear] in between (sign-out, session ended) makes that save a no-op. */
    @Synchronized
    fun token(): Int = generation

    @Synchronized
    fun load(owner: String): Snapshot? {
        if (!file.exists()) return null
        val s = try {
            sealer.open(file.readBytes())?.let { decode(it.toString(Charsets.UTF_8)) }
        } catch (e: Exception) {
            null
        }
        if (s == null || s.owner != owner) {
            drop()
            return null
        }
        return s
    }

    /** false when nothing was written: the cache was cleared since [token], or the disk refused. */
    @Synchronized
    fun save(s: Snapshot, token: Int): Boolean {
        if (token != generation) return false
        return try {
            val bytes = sealer.seal(encode(s).toByteArray(Charsets.UTF_8))
            FileOutputStream(temp).use {
                it.write(bytes)
                it.fd.sync()
            }
            if (!temp.renameTo(file)) throw IOException("rename failed")
            true
        } catch (e: Exception) {
            temp.delete()
            false
        }
    }

    @Synchronized
    fun clear() {
        generation++
        drop()
    }

    private fun drop() {
        file.delete()
        temp.delete()
    }

    companion object {
        private const val VERSION = 1

        fun owner(base: String, user: String): String = base.trim().lowercase() + "\n" + user.trim()

        internal fun encode(s: Snapshot): String {
            fun one(c: Cached?) = c?.let {
                JSONObject().put("path", it.path).put("body", it.body).put("at", it.fetchedAt)
                    .put("etag", it.etag ?: JSONObject.NULL).put("last_modified", it.lastModified ?: JSONObject.NULL)
            } ?: JSONObject.NULL
            return JSONObject().put("v", VERSION).put("owner", s.owner)
                .put("me", one(s.me)).put("state", one(s.state)).put("deck", one(s.deck)).toString()
        }

        /** null for anything this version did not write. */
        internal fun decode(text: String): Snapshot? {
            val o = JSONObject(text)
            if (o.optInt("v") != VERSION) return null
            fun one(key: String): Cached? {
                val c = o.optJSONObject(key) ?: return null
                val body = c.optString("body").takeIf { it.isNotEmpty() } ?: return null
                return Cached(
                    path = c.optString("path"),
                    body = body,
                    fetchedAt = c.optLong("at"),
                    etag = if (c.isNull("etag")) null else c.optString("etag"),
                    lastModified = if (c.isNull("last_modified")) null else c.optString("last_modified"),
                )
            }
            val owner = o.optString("owner").takeIf { it.isNotEmpty() } ?: return null
            return Snapshot(owner, one("me"), one("state"), one("deck"))
        }
    }
}
