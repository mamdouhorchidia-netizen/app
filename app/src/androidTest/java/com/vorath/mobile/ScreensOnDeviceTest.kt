package com.vorath.mobile

import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** The app itself on the emulator: the sign-in form, and the Today page read from a Vorath stand-in. */
@OptIn(ExperimentalTestApi::class)
@RunWith(AndroidJUnit4::class)
class ScreensOnDeviceTest {
    @get:Rule
    val compose = createEmptyComposeRule()
    private val server = FakeVorath()
    private var scenario: ActivityScenario<MainActivity>? = null

    @Before
    fun clean() = Device.fresh()

    @After
    fun tearDown() {
        scenario?.close()
        server.close()
        Device.fresh()
    }

    @Test
    fun withNoSessionTheSignInFormOpensAndChecksItsFields() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        compose.waitUntilAtLeastOneExists(hasText("Vorath address") and hasSetTextAction(), 15_000)
        compose.onNodeWithText("Sign in").performClick()
        compose.waitUntilAtLeastOneExists(hasText("Enter your Vorath address", substring = true), 5_000)
    }

    @Test
    fun signedInTheTodayPageShowsTheServersAnswers() {
        Device.signedInTo(server)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        compose.waitUntilAtLeastOneExists(hasText("ANJA"), 15_000)
        compose.waitUntilAtLeastOneExists(hasText("Needs you now", substring = true), 20_000)   // the deck's word
        assertTrue(server.requests.any { it.startsWith("/api/state") })
        assertTrue(server.requests.contains("/api/autopilot/deck"))
    }

    @Test
    fun anEndedSessionGoesBackToSignIn() {
        Device.signedInTo(server)
        server.status = 401
        scenario = ActivityScenario.launch(MainActivity::class.java)
        compose.waitUntilAtLeastOneExists(hasText("Vorath address") and hasSetTextAction(), 20_000)
    }
}
