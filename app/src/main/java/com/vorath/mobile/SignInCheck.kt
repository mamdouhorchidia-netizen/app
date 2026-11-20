package com.vorath.mobile

import com.vorath.mobile.data.VorathApi

/** The sign-in field a problem is about, so the form can mark it; null when it is about the whole sign-in. */
enum class SignInField { ADDRESS, USER, PASSWORD, CODE }

data class SignInProblem(val text: String, val field: SignInField?)

/** What the form can tell before asking the server. Plain JVM code, tested in SignInCheckTest. */
object SignInCheck {
    fun of(address: String, user: String, password: String, code: String): SignInProblem? = when {
        address.isBlank() -> SignInProblem("Enter your Vorath address, like vorath.yourbrand.com.", SignInField.ADDRESS)
        address.trim().startsWith("http://", ignoreCase = true) ->
            SignInProblem("Vorath needs a secure address: use https:// or just the name, like vorath.yourbrand.com.", SignInField.ADDRESS)
        VorathApi.address(address) == null -> SignInProblem("That is not a web address. Type it like vorath.yourbrand.com.", SignInField.ADDRESS)
        user.isBlank() -> SignInProblem("Enter your user name.", SignInField.USER)
        password.isEmpty() -> SignInProblem("Enter your password.", SignInField.PASSWORD)
        code.isNotEmpty() && code.length != 6 -> SignInProblem("The 2-step code has 6 digits.", SignInField.CODE)
        else -> null
    }
}
