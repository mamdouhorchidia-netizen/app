package com.vorath.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import com.github.takahirom.roborazzi.captureRoboImage
import com.vorath.mobile.data.Parse
import com.vorath.mobile.data.Section
import com.vorath.mobile.data.Session
import com.vorath.mobile.ui.TodayScreen
import com.vorath.mobile.ui.VorathTheme
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * A picture of the changed-value flash, mid-animation, on the compose test clock. Its own class: a compose rule
 * in ScreensTest would sit over the activity captureRoboImage draws in. Skipped unless -Dvorath.shots=<folder>.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ChangedScreenTest {
    @get:Rule
    val compose = createComposeRule()

    private val folder = System.getProperty("vorath.shots").orEmpty()
    private fun recorded(name: String) = JSONObject(javaClass.getResource("/$name")!!.readText())
    private val session = Session("https://vorath.example.com", "sid", "owner", "Owner", "ANJA")
    private val readAt = 1_800_000_000_000
    private val today get() = TodayState(
        money = Section(Parse.money(recorded("state.json")), fetchedAt = readAt),
        deck = Section(Parse.deck(recorded("deck.json")), fetchedAt = readAt),
    )

    /** The recorded answers with one more alarm firing and other cash-in and net-profit figures: the next read. */
    private val nextRead get() = TodayState(
        money = Section(Parse.money(recorded("state.json").apply {
            val four = getJSONArray("four")
            for (i in 0 until four.length()) four.getJSONObject(i).let { f ->
                if (f.getString("key") == "cash_in" || f.getString("key") == "net_profit") f.put("egp", f.getDouble("egp") + 12_500)
            }
        }), fetchedAt = readAt + 30_000),
        deck = Section(Parse.deck(recorded("deck.json").apply {
            val alarms = getJSONArray("alarms")
            for (i in 0 until alarms.length()) alarms.getJSONObject(i).let { if (it.getString("key") == "refusal_spike") it.put("firing", true) }
        }), fetchedAt = readAt + 30_000),
    )

    /**
     * The next 30-second read brings changed figures and a newly firing alarm: those flash for about a second. The
     * picture is taken 150 ms in, on the test's own clock.
     */
    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h1500dp-xxhdpi")
    fun todayChangedFlash() {
        assumeTrue(folder.isNotEmpty())
        var state by mutableStateOf(today)
        compose.mainClock.autoAdvance = false
        compose.setContent { VorathTheme { Surface(color = MaterialTheme.colorScheme.background) { TodayScreen(session, state, true, {}, {}, {}, {}, clock = { readAt + 30_000 }) } } }
        compose.mainClock.advanceTimeBy(100)
        compose.runOnUiThread {
            state = nextRead
            Snapshot.sendApplyNotifications()
        }
        compose.mainClock.advanceTimeBy(150)
        compose.onNodeWithText("2 firing").assertExists()
        compose.onRoot().captureRoboImage("$folder/today-changed-flash.png")
    }

}
