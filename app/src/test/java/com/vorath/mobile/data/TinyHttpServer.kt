package com.vorath.mobile.data

import java.io.BufferedInputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import kotlin.concurrent.thread

/**
 * A minimal HTTP/1.1 server for the client tests, built on plain sockets: unit tests compile against android.jar,
 * which has no com.sun.net.httpserver. It keeps each connection open for further requests (keep-alive).
 */
class TinyHttpServer(private val handle: (Request) -> Response) : AutoCloseable {
    class Request(val method: String, val path: String, val headers: Map<String, String>, val remotePort: Int) {
        fun header(name: String): String? = headers[name.lowercase()]
    }

    class Response(val status: Int, val body: ByteArray = ByteArray(0), val headers: Map<String, String> = emptyMap())

    private val socket = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    private val open = mutableListOf<Socket>()
    private var closed = false                               // guarded by open
    val port: Int get() = socket.localPort

    init {
        thread(isDaemon = true, name = "tiny-http") {
            while (!socket.isClosed) {
                val s = try { socket.accept() } catch (e: SocketException) { break }
                // ServerSocket.close() can be deferred while accept() is blocked, so a connection may still arrive
                // after close(): refuse it, or a closed server would go on answering.
                synchronized(open) {
                    if (closed) s.close() else open += s
                }
                if (s.isClosed) break
                thread(isDaemon = true) { serve(s) }
            }
        }
    }

    private fun serve(s: Socket) = s.use {
        val input = BufferedInputStream(s.getInputStream())
        val out = s.getOutputStream()
        try {
            while (true) {
                val start = readLine(input) ?: return
                if (start.isEmpty()) continue
                val parts = start.split(' ')
                val headers = mutableMapOf<String, String>()
                while (true) {
                    val line = readLine(input) ?: return
                    if (line.isEmpty()) break
                    val i = line.indexOf(':')
                    if (i > 0) headers[line.substring(0, i).trim().lowercase()] = line.substring(i + 1).trim()
                }
                headers["content-length"]?.toIntOrNull()?.let { n -> input.readNBytes(n) }
                val r = handle(Request(parts[0], parts.getOrElse(1) { "/" }, headers, s.port))
                val head = StringBuilder("HTTP/1.1 ${r.status} X\r\n")
                r.headers.forEach { (k, v) -> head.append("$k: $v\r\n") }
                if (r.status != 304) head.append("Content-Length: ${r.body.size}\r\n")
                head.append("\r\n")
                out.write(head.toString().toByteArray(Charsets.ISO_8859_1))
                if (r.status != 304) out.write(r.body)
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

    override fun close() {
        synchronized(open) { closed = true }
        socket.close()
        synchronized(open) { open.forEach { runCatching { it.close() } } }
    }
}
