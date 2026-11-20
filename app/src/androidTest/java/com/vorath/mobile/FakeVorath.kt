package com.vorath.mobile

import androidx.test.platform.app.InstrumentationRegistry
import java.io.BufferedInputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.util.Collections
import kotlin.concurrent.thread

/**
 * A Vorath stand-in on the emulator's 127.0.0.1 for the on-device tests: it answers /api/me, /api/state and
 * /api/autopilot/deck with the recorded answers in src/test/resources (the same files the unit tests read), or with
 * [status] for every route when it is set (401 to end the session). Plain HTTP; only debug builds may reach it
 * (src/debug's network security config).
 */
class FakeVorath : AutoCloseable {
    @Volatile var status: Int = 200
    val requests: MutableList<String> = Collections.synchronizedList(mutableListOf())
    val cookies: MutableList<String> = Collections.synchronizedList(mutableListOf())

    private val socket = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    val base: String get() = "http://127.0.0.1:${socket.localPort}"

    init {
        thread(isDaemon = true, name = "fake-vorath") {
            while (!socket.isClosed) {
                val s = try { socket.accept() } catch (e: SocketException) { break }
                thread(isDaemon = true) { serve(s) }
            }
        }
    }

    private fun body(path: String): String? {
        val name = when (path.substringBefore('?')) {
            "/api/me" -> "me.json"
            "/api/state" -> "state.json"
            "/api/autopilot/deck" -> "deck.json"
            "/healthz" -> return """{"ok": true, "version": "test"}"""
            else -> return null
        }
        return recorded(name)
    }

    private fun serve(s: Socket) = s.use {
        val input = BufferedInputStream(s.getInputStream())
        val out = s.getOutputStream()
        try {
            while (true) {
                val start = readLine(input) ?: return
                if (start.isEmpty()) continue
                val path = start.split(' ').getOrElse(1) { "/" }
                var length = 0
                while (true) {
                    val line = readLine(input) ?: return
                    if (line.isEmpty()) break
                    val name = line.substringBefore(':').trim().lowercase()
                    val value = line.substringAfter(':').trim()
                    if (name == "content-length") length = value.toIntOrNull() ?: 0
                    if (name == "cookie") cookies += value
                }
                repeat(length) { input.read() }
                requests += path
                val text = body(path)
                val code = if (status != 200) status else if (text == null) 404 else 200
                val bytes = (if (code == 200) text!! else """{"error": "test answer $code"}""").toByteArray(Charsets.UTF_8)
                val head = "HTTP/1.1 $code X\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\n\r\n"
                out.write(head.toByteArray(Charsets.ISO_8859_1))
                out.write(bytes)
                out.flush()
            }
        } catch (e: SocketException) {
        }
    }

    private fun readLine(input: InputStream): String? {
        val b = StringBuilder()
        while (true) {
            val c = input.read()
            if (c < 0) return if (b.isEmpty()) null else b.toString()
            if (c == '\n'.code) return b.toString().trimEnd('\r')
            b.append(c.toChar())
        }
    }

    override fun close() = socket.close()

    companion object {
        fun recorded(name: String): String =
            InstrumentationRegistry.getInstrumentation().context.assets.open(name).use { it.readBytes().toString(Charsets.UTF_8) }
    }
}
