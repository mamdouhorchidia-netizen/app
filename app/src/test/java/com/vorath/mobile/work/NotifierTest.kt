package com.vorath.mobile.work

import android.Manifest
import android.app.Application
import android.app.NotificationManager
import androidx.core.app.NotificationCompat
import org.robolectric.RuntimeEnvironment
import com.vorath.mobile.data.Signal
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NotifierTest {
    private val app: Application = RuntimeEnvironment.getApplication()
    private val manager = app.getSystemService(NotificationManager::class.java)
    private val at = 1_800_000_000_000

    @Before
    fun allow() = shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)

    @Test
    fun alarmsAndTheAccountHaveTheirOwnChannels() {
        Notifier.channels(app)
        val alarms = manager.getNotificationChannel(Notifier.CHANNEL_ALARMS)
        val account = manager.getNotificationChannel(Notifier.CHANNEL_ACCOUNT)
        assertEquals("alarms", alarms.id)                           // the id older installs already have
        assertEquals("Alarms", alarms.name)
        assertEquals(NotificationManager.IMPORTANCE_HIGH, alarms.importance)
        assertEquals("Account", account.name)
        assertEquals(NotificationManager.IMPORTANCE_DEFAULT, account.importance)
        assertEquals(2, manager.notificationChannels.size)
    }

    @Test
    fun oneAlarmIsOneNotificationWithItsTime() {
        Notifier.tell(app, listOf(Signal("alarm:cash", "Cash in is low", "EGP 2,000 today", "/autopilot")), now = at)
        val posted = shadowOf(manager).allNotifications
        assertEquals(1, posted.size)
        val n = posted.single()
        assertEquals(Notifier.CHANNEL_ALARMS, n.channelId)
        assertEquals(at, n.`when`)
        assertEquals(Notifier.GROUP_ALARMS, n.group)
        assertEquals("Cash in is low", n.extras.getCharSequence(NotificationCompat.EXTRA_TITLE).toString())
    }

    @Test
    fun severalAlarmsAreGroupedUnderASummaryThatOpensToday() {
        val three = (1..3).map { Signal("alarm:$it", "Alarm $it", "text $it", "/autopilot") }
        Notifier.tell(app, three, now = at)
        val shadow = shadowOf(manager)
        assertEquals(4, shadow.allNotifications.size)
        val summary = shadow.getNotification(Notifier.ID_SUMMARY)
        assertTrue(summary.flags and android.app.Notification.FLAG_GROUP_SUMMARY != 0)
        assertEquals("3 alarms started", summary.extras.getCharSequence(NotificationCompat.EXTRA_TITLE).toString())
        assertEquals(at, summary.`when`)
        // Only the summary makes a sound.
        shadow.allNotifications.filter { it !== summary }.forEach { assertEquals(NotificationCompat.GROUP_ALERT_SUMMARY, it.groupAlertBehavior) }
        val open = shadowOf(summary.contentIntent).savedIntent
        assertTrue(open.getBooleanExtra(Notifier.EXTRA_TODAY, false))
        assertNull(open.getStringExtra(Notifier.EXTRA_PATH))
    }

    @Test
    fun signedOutGoesToTheAccountChannel() {
        Notifier.signedOut(app, now = at)
        val n = shadowOf(manager).getNotification(Notifier.ID_SIGNED_OUT)
        assertEquals(Notifier.CHANNEL_ACCOUNT, n.channelId)
        assertEquals(at, n.`when`)
    }

    @Test
    fun nothingIsPostedWithoutThePermission() {
        shadowOf(app).denyPermissions(Manifest.permission.POST_NOTIFICATIONS)
        Notifier.tell(app, listOf(Signal("alarm:x", "X", "x", "/autopilot")), now = at)
        assertEquals(0, shadowOf(manager).allNotifications.size)
    }
}
