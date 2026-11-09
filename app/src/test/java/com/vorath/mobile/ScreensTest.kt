package com.vorath.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import com.github.takahirom.roborazzi.captureRoboImage
import com.vorath.mobile.data.Parse
import com.vorath.mobile.data.Session
import com.vorath.mobile.ui.SignInScreen
import com.vorath.mobile.ui.TodayScreen
import com.vorath.mobile.ui.VorathTheme
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Draws the screens from the recorded answers into PNG files, to look at them without a phone. Skipped unless -Dvorath.shots=<folder>. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ScreensTest {
    private val folder = System.getProperty("vorath.shots").orEmpty()
    private fun recorded(name: String) = JSONObject(javaClass.getResource("/$name")!!.readText())
    private val session = Session("https://vorath.example.com", "sid", "owner", "Owner", "ANJA")
    private val today get() = TodayState(money = Parse.money(recorded("state.json")), deck = Parse.deck(recorded("deck.json")), checkedAt = 1_800_000_000_000)

    private fun shot(name: String, content: @Composable () -> Unit) {
        assumeTrue(folder.isNotEmpty())
        captureRoboImage("$folder/$name.png") { VorathTheme { Surface(color = MaterialTheme.colorScheme.background) { content() } } }
    }

    @Composable
    private fun Today(state: TodayState, notificationsOn: Boolean = true) = TodayScreen(session, state, notificationsOn, {}, {}, {}, {})

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h2200dp-xxhdpi")
    fun todayLight() = shot("today-light") { Today(today) }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h2200dp-night-xxhdpi")
    fun todayDark() = shot("today-dark") { Today(today) }

    @Test
    @Config(sdk = [34], qualifiers = "w360dp-h740dp-xhdpi")
    fun todaySmallPhone() = shot("today-small") { Today(today, notificationsOn = false) }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
    fun todayOffline() = shot("today-offline") { Today(today.copy(problem = "Cannot reach the server. Check the connection and the address.")) }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
    fun signIn() = shot("sign-in") { SignInScreen("", "", false, null) { _, _, _, _ -> } }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-night-xxhdpi")
    fun signInRefused() = shot("sign-in-refused") { SignInScreen("https://vorath.example.com", "owner", false, "Wrong user name, password or code.") { _, _, _, _ -> } }
}
