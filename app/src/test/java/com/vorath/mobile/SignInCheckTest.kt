package com.vorath.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SignInCheckTest {
    private fun field(a: String, u: String = "owner", p: String = "pw", c: String = "") = SignInCheck.of(a, u, p, c)?.field

    @Test
    fun aCompleteFormGoesToTheServer() {
        assertNull(SignInCheck.of("vorath.example.com", "owner", "pw", ""))
        assertNull(SignInCheck.of("https://vorath.example.com/", "owner", "pw", "123456"))
    }

    @Test
    fun eachMissingPieceIsNamedOnItsField() {
        assertEquals(SignInField.ADDRESS, field(""))
        assertEquals(SignInField.ADDRESS, field("not an address"))
        assertEquals(SignInField.ADDRESS, field("http://vorath.example.com"))
        assertEquals(SignInField.USER, field("vorath.example.com", u = " "))
        assertEquals(SignInField.PASSWORD, field("vorath.example.com", p = ""))
        assertEquals(SignInField.CODE, field("vorath.example.com", c = "123"))
    }

    @Test
    fun plainHttpIsExplained() {
        assertEquals(
            "Vorath needs a secure address: use https:// or just the name, like vorath.yourbrand.com.",
            SignInCheck.of("http://vorath.example.com", "owner", "pw", "")?.text,
        )
    }
}
