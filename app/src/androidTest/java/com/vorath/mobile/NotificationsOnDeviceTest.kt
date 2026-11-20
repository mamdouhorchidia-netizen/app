package com.vorath.mobile

import android.app.NotificationManager
import androidx.core.app.NotificationCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.vorath.mobile.data.Signal
import com.vorath.mobile.work.Notifier
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** Android's own notification manager: the channels, one alarm, several alarms under a summary, and "sign in again". */
@RunWith(AndroidJUnit4::class)
class NotificationsOnDeviceTest {
    @Before
    fun allow() {
        Device.fresh()
        Device.allowNotifications()
    }

    @After
    fun tidy() = Device.notifications.cancelAll()

    @Test
    fun theTwoChannelsExist() {
        Notifier.channels(Device.app)
        assertTrue(Notifier.allowed(Device.app))
        assertEquals(NotificationManager.IMPORTANCE_HIGH, Device.notifications.getNotificationChannel(Notifier.CHANNEL_ALARMS).importance)
        assertEquals(NotificationManager.IMPORTANCE_DEFAULT, Device.notifications.getNotificationChannel(Notifier.CHANNEL_ACCOUNT).importance)
    }

    @Test
    fun oneAlarmIsPostedOnTheAlarmsChannel() {
        Notifier.tell(Device.app, listOf(Signal("alarm:cash", "Cash in is low", "EGP 2,000 today", "/autopilot")))
        val shown = Device.shade().single()
        assertEquals(Notifier.CHANNEL_ALARMS, shown.notification.channelId)
        assertEquals("Cash in is low", shown.notification.extras.getCharSequence(NotificationCompat.EXTRA_TITLE).toString())
        assertTrue(shown.notification.contentIntent != null)
    }

    @Test
    fun severalAlarmsAreGroupedUnderOneSummary() {
        Notifier.tell(Device.app, (1..3).map { Signal("alarm:$it", "Alarm $it", "text $it", "/autopilot") })
        val shown = Device.shade { it.size >= 4 }
        assertEquals(4, shown.size)
        assertTrue(shown.any { it.id == Notifier.ID_SUMMARY && it.notification.flags and android.app.Notification.FLAG_GROUP_SUMMARY != 0 })
        assertTrue(shown.all { it.notification.group == Notifier.GROUP_ALARMS })
    }

    @Test
    fun signedOutGoesOnTheAccountChannel() {
        Notifier.signedOut(Device.app)
        val shown = Device.shade().single { it.id == Notifier.ID_SIGNED_OUT }
        assertEquals(Notifier.CHANNEL_ACCOUNT, shown.notification.channelId)
    }
}
