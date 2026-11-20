package com.vorath.mobile

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.service.notification.StatusBarNotification
import androidx.test.core.app.ApplicationProvider
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkManager
import com.vorath.mobile.data.Keystore
import com.vorath.mobile.data.SessionStore
import com.vorath.mobile.data.TodayCache

/** What the on-device tests share: a clean app, the notification permission, and waiting for what Android posts. */
object Device {
    val app: Context get() = ApplicationProvider.getApplicationContext()
    val notifications: NotificationManager get() = app.getSystemService(NotificationManager::class.java)

    /** No session, no cached answers, no scheduled check, no notifications: as just installed (the Keystore key may stay). */
    fun fresh() {
        WorkManager.getInstance(app).cancelAllWork().result.get()
        app.getSharedPreferences("vorath", Context.MODE_PRIVATE).edit().clear().commit()
        TodayCache(app.noBackupFilesDir, Keystore).clear()
        notifications.cancelAll()
    }

    fun allowNotifications() {
        if (Build.VERSION.SDK_INT >= 33) {
            InstrumentationRegistry.getInstrumentation().uiAutomation.grantRuntimePermission(app.packageName, Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    fun signedInTo(server: FakeVorath, sid: String = "sid-on-device") =
        SessionStore(app).save(com.vorath.mobile.data.Session(server.base, sid, "owner", "Owner", "ANJA"))

    /** The app's notifications on the shade, waiting up to [ms] for [until] to hold. */
    fun shade(ms: Long = 5_000, until: (List<StatusBarNotification>) -> Boolean = { it.isNotEmpty() }): List<StatusBarNotification> {
        val end = SystemClock.uptimeMillis() + ms
        while (true) {
            val now = notifications.activeNotifications.toList()
            if (until(now) || SystemClock.uptimeMillis() > end) return now
            SystemClock.sleep(100)
        }
    }
}
