package com.vorath.mobile

import androidx.core.app.NotificationCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.work.ListenableWorker
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.testing.TestListenableWorkerBuilder
import com.vorath.mobile.data.SessionStore
import com.vorath.mobile.data.VorathApi
import com.vorath.mobile.work.AlarmWorker
import com.vorath.mobile.work.Notifier
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.TimeUnit

/** The background check, run by WorkManager's test builder against a Vorath stand-in, and its real schedule. */
@RunWith(AndroidJUnit4::class)
class AlarmCheckOnDeviceTest {
    private val server = FakeVorath()

    @Before
    fun setUp() {
        Device.fresh()
        Device.allowNotifications()
        Device.signedInTo(server)
    }

    @After
    fun tearDown() {
        server.close()
        Device.fresh()
    }

    private fun check(): ListenableWorker.Result = runBlocking { TestListenableWorkerBuilder<AlarmWorker>(Device.app).build().doWork() }

    @Test
    fun aFiringAlarmIsToldOnceWithTheSession() {
        assertEquals(ListenableWorker.Result.success(), check())
        assertTrue(server.requests.contains("/api/autopilot/deck"))
        assertTrue(server.cookies.all { it == "${VorathApi.COOKIE}=sid-on-device" })
        val shown = Device.shade()
        assertEquals(1, shown.size)                                         // the recorded deck has one firing alarm
        assertEquals(Notifier.CHANNEL_ALARMS, shown.single().notification.channelId)
        assertTrue(shown.single().notification.extras.getCharSequence(NotificationCompat.EXTRA_TITLE).isNullOrBlank().not())
        assertTrue(SessionStore(Device.app).told.isNotEmpty())

        Device.notifications.cancelAll()
        assertEquals(ListenableWorker.Result.success(), check())            // nothing new since the last check
        assertTrue(Device.shade(ms = 1_500).isEmpty())
    }

    @Test
    fun anEndedSessionIsToldOnce() {
        server.status = 401
        assertEquals(ListenableWorker.Result.success(), check())
        assertEquals(listOf(Notifier.ID_SIGNED_OUT), Device.shade().map { it.id })
        assertTrue(SessionStore(Device.app).toldSignedOut)
        Device.notifications.cancelAll()
        check()
        assertTrue(Device.shade(ms = 1_500).isEmpty())
    }

    @Test
    fun aServerErrorIsRetriedLater() {
        server.status = 500
        assertEquals(ListenableWorker.Result.retry(), check())
    }

    @Test
    fun theCheckIsScheduledEvery15MinutesAndStopsOnSignOut() {
        AlarmWorker.start(Device.app)
        val work = WorkManager.getInstance(Device.app).getWorkInfosForUniqueWork("vorath-alarms").get(10, TimeUnit.SECONDS).single()
        assertEquals(WorkInfo.State.ENQUEUED, work.state)
        assertEquals(TimeUnit.MINUTES.toMillis(15), work.periodicityInfo!!.repeatIntervalMillis)
        AlarmWorker.stop(Device.app)
        val after = WorkManager.getInstance(Device.app).getWorkInfosForUniqueWork("vorath-alarms").get(10, TimeUnit.SECONDS).single()
        assertEquals(WorkInfo.State.CANCELLED, after.state)
    }
}
