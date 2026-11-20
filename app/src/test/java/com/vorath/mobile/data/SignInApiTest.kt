package com.vorath.mobile.data

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test
import java.util.Collections

/** Sign-in against a small server on this machine: the health check is asked only when the sign-in fails. */
class SignInApiTest {
    private val asked = Collections.synchronizedList(mutableListOf<String>())
    private var server: TinyHttpServer? = null
    private fun serve(login: () -> TinyHttpServer.Response, health: TinyHttpServer.Response = ok): String {
        val s = TinyHttpServer { x ->
            asked += x.path
            when (x.path) {
                "/api/auth/login" -> login()
                "/healthz" -> health
                else -> TinyHttpServer.Response(404)
            }
        }
        server = s
        return "http://127.0.0.1:${s.port}"
    }

    private val ok = TinyHttpServer.Response(200, """{"ok":true,"version":"9"}""".toByteArray())
    private fun json(status: Int, body: String, headers: Map<String, String> = emptyMap()) = TinyHttpServer.Response(status, body.toByteArray(), headers)

    @After
    fun stop() {
        server?.close()
    }

    @Test
    fun aSignInThatWorksIsOneRequest() {
        val base = serve({ json(200, """{"ok":true}""", mapOf("Set-Cookie" to "vorath_sid=abc; Path=/; HttpOnly")) })
        assertEquals("abc", VorathApi(base).signInChecked("owner", "pw", "123456").sessionId)
        assertEquals(listOf("/api/auth/login"), asked.toList())
    }

    @Test
    fun aWrongPasswordSaysTheServersWordsWithoutAHealthCheck() {
        val base = serve({ json(401, """{"error":"Wrong password or code."}""") })
        expect<VorathError.SignedOut>("Wrong password or code.") { VorathApi(base).signInChecked("owner", "pw", null) }
        assertEquals(listOf("/api/auth/login"), asked.toList())
    }

    @Test
    fun aWrongPasswordWithoutWordsIsSaidPlainly() {
        val base = serve({ TinyHttpServer.Response(401) })
        expect<VorathError.SignedOut>(VorathApi.WRONG_SIGN_IN) { VorathApi(base).signInChecked("owner", "pw", null) }
    }

    @Test
    fun noVorathAtTheAddressIsFoundByTheHealthCheckAfterTheSignInFails() {
        val base = serve({ TinyHttpServer.Response(404, "<html>nope</html>".toByteArray()) }, health = TinyHttpServer.Response(404))
        expect<VorathError.NotVorath>("No Vorath at this address.") { VorathApi(base).signInChecked("owner", "pw", null) }
        assertEquals(listOf("/api/auth/login", "/healthz"), asked.toList())
    }

    @Test
    fun aVorathThatFailsTheSignInKeepsItsOwnError() {
        val base = serve({ TinyHttpServer.Response(429) })
        expect<VorathError.NotVorath>("Too many requests to Vorath. Wait a minute, then try again.") { VorathApi(base).signInChecked("owner", "pw", null) }
        assertEquals(listOf("/api/auth/login", "/healthz"), asked.toList())
    }

    @Test
    fun nothingThereIsOffline() {
        val base = serve({ ok })
        server!!.close()
        expect<VorathError.Offline>("Cannot reach the server. Check the connection and the address.") { VorathApi(base).signInChecked("owner", "pw", null) }
    }

    private inline fun <reified E : VorathError> expect(message: String, block: () -> Unit) {
        try {
            block()
            fail("expected ${E::class.simpleName}")
        } catch (e: VorathError) {
            if (e !is E) fail("expected ${E::class.simpleName}, got ${e::class.simpleName}: ${e.message}")
            assertEquals(message, e.message)
        }
    }
}
