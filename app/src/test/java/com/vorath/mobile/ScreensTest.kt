package com.vorath.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import com.github.takahirom.roborazzi.captureRoboImage
import com.vorath.mobile.data.Parse
import com.vorath.mobile.data.Section
import com.vorath.mobile.data.Session
import com.vorath.mobile.data.VorathError
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
    private val readAt = 1_800_000_000_000
    private val today get() = TodayState(
        money = Section(Parse.money(recorded("state.json")), fetchedAt = readAt),
        deck = Section(Parse.deck(recorded("deck.json")), fetchedAt = readAt),
    )

    private fun shot(name: String, content: @Composable () -> Unit) {
        assumeTrue(folder.isNotEmpty())
        captureRoboImage("$folder/$name.png") { VorathTheme { Surface(color = MaterialTheme.colorScheme.background) { content() } } }
    }

    @Composable
    private fun Today(state: TodayState, notificationsOn: Boolean = true, secondsLater: Long = 12, alarmsOpen: Boolean = false) =
        TodayScreen(session, state, notificationsOn, {}, {}, {}, {}, clock = { readAt + secondsLater * 1000 }, alarmsOpen = alarmsOpen)

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
    fun todayOffline() = shot("today-offline") {
        val offline = VorathError.Offline("Cannot reach the server. Check the connection and the address.")
        Today(today.let { it.copy(money = it.money.copy(error = offline), deck = it.deck.copy(error = offline)) }, secondsLater = 20 * 60)
    }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-night-xxhdpi")
    fun todayOfflineDark() = shot("today-offline-dark") {
        val offline = VorathError.Offline("Cannot reach the server. Check the connection and the address.")
        Today(today.let { it.copy(money = it.money.copy(error = offline), deck = it.deck.copy(error = offline)) }, secondsLater = 20 * 60)
    }

    /** Only the Autopilot read failed: the numbers stay, the slim line says why. */
    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
    fun todayDeckFailed() = shot("today-deck-failed") {
        Today(today.let { it.copy(deck = it.deck.copy(error = VorathError.NotVorath("Vorath did not answer as expected (502)."))) }, secondsLater = 45)
    }

    /** A quiet 30-second read under way over data already shown: "Updating…", no spinner. */
    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-night-xxhdpi")
    fun todayQuietUpdate() = shot("today-quiet-update") {
        Today(today.copy(busySince = readAt + 28_000), secondsLater = 30)
    }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
    fun todayFirstRead() = shot("today-first-read") { Today(TodayState(spinning = true, busySince = readAt)) }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-night-xxhdpi")
    fun todayNothingAndOffline() = shot("today-nothing-offline") {
        Today(TodayState(money = Section(error = VorathError.Offline("Cannot reach the server. Check the connection and the address."))))
    }

    /** The first read with nothing cached: grey shapes of the page, not a spinner over nothing. */
    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-night-xxhdpi")
    fun todaySkeleton() = shot("today-skeleton-dark") { Today(TodayState(spinning = true, busySince = readAt)) }

    @Test
    @Config(sdk = [34], qualifiers = "w840dp-h1300dp-xhdpi")
    fun todaySkeletonTablet() = shot("today-skeleton-tablet") { Today(TodayState(spinning = true, busySince = readAt)) }

    /** Android's largest font size (200 %): nothing clips, the figures shrink or wrap. */
    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h3600dp-xxhdpi")
    fun todayFontScale200() = shot("today-font-200") {
        CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, fontScale = 2f)) { Today(today, notificationsOn = false) }
    }

    @Test
    @Config(sdk = [34], qualifiers = "w360dp-h2400dp-night-xhdpi")
    fun todayFontScale200SmallDark() = shot("today-font-200-small-dark") {
        CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, fontScale = 2f)) { Today(today) }
    }

    /** A tablet: content capped at 840 dp, two columns (needs and alarms | numbers, cash, plan). */
    @Test
    @Config(sdk = [34], qualifiers = "w840dp-h1300dp-xhdpi")
    fun todayTablet() = shot("today-tablet") { Today(today, notificationsOn = false) }

    @Test
    @Config(sdk = [34], qualifiers = "w1280dp-h800dp-night-mdpi")
    fun todayTabletWideDark() = shot("today-tablet-wide-dark") { Today(today) }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h1500dp-xxhdpi")
    fun todayAlarmsOpen() = shot("today-alarms-open") { Today(today, alarmsOpen = true) }

    /** Notifications off: the card that explains why sits after the alarms. */
    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h1200dp-xxhdpi")
    fun todayNotifyCard() = shot("today-notify-card") { Today(today, notificationsOn = false) }

    /** The role does not open the Autopilot: a quiet note where it would be; the numbers still show. */
    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
    fun todayDeckRefused() = shot("today-deck-refused") {
        Today(today.copy(deck = Section(refused = "Your role does not open this.", fetchedAt = readAt)))
    }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
    fun todayLightPhone() = shot("today-main-light") { Today(today) }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-night-xxhdpi")
    fun todayDarkPhone() = shot("today-main-dark") { Today(today) }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
    fun signIn() = shot("sign-in") { SignInScreen("", "", false, null) { _, _, _, _ -> } }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-night-xxhdpi")
    fun signInRefused() = shot("sign-in-refused") { SignInScreen("https://vorath.example.com", "owner", false, "Wrong user name, password or code.") { _, _, _, _ -> } }
}
