package com.vorath.mobile.work

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.vorath.mobile.MainActivity
import com.vorath.mobile.R
import com.vorath.mobile.data.Signal

/**
 * The app's notifications, on two channels: "Alarms" (high importance: heads-up, sound) for what starts needing the
 * person, and "Account" (default importance) for the session ending. The alarms channel keeps the id it always had
 * ("alarms"), so a phone that already has it keeps the person's settings for it; nothing is renamed or orphaned.
 */
object Notifier {
    const val CHANNEL_ALARMS = "alarms"
    const val CHANNEL_ACCOUNT = "account"
    const val GROUP_ALARMS = "com.vorath.mobile.ALARMS"

    /** The page of the full Vorath a notification opens; without it a tap opens the Today page. */
    const val EXTRA_PATH = "path"
    const val EXTRA_TODAY = "today"

    const val ID_SIGNED_OUT = 1
    const val ID_SUMMARY = 2

    fun channels(context: Context) {
        val alarms = NotificationChannelCompat.Builder(CHANNEL_ALARMS, NotificationManagerCompat.IMPORTANCE_HIGH)
            .setName(context.getString(R.string.channel_alarms))
            .setDescription(context.getString(R.string.channel_alarms_about))
            .build()
        val account = NotificationChannelCompat.Builder(CHANNEL_ACCOUNT, NotificationManagerCompat.IMPORTANCE_DEFAULT)
            .setName(context.getString(R.string.channel_account))
            .setDescription(context.getString(R.string.channel_account_about))
            .build()
        NotificationManagerCompat.from(context).createNotificationChannelsCompat(listOf(alarms, account))
    }

    fun allowed(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /**
     * The alarms that started since the last check. One is one notification; several are grouped under a summary
     * ("3 alarms started") that alone makes the sound, so the phone does not ring once per alarm.
     */
    fun tell(context: Context, signals: List<Signal>, now: Long = System.currentTimeMillis()) {
        if (signals.isEmpty() || !allowed(context)) return
        channels(context)
        val grouped = signals.size > 1
        val m = NotificationManagerCompat.from(context)
        signals.forEach { s ->
            val b = builder(context, CHANNEL_ALARMS, s.title, s.text, now, open(context, s.id.hashCode(), s.href))
                .setGroup(GROUP_ALARMS)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_STATUS)
            if (grouped) b.setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_SUMMARY)
            post(m, s.id.hashCode(), b)
        }
        if (grouped) {
            val title = context.resources.getQuantityString(R.plurals.alarms_started, signals.size, signals.size)
            val inbox = NotificationCompat.InboxStyle().setBigContentTitle(title)
            signals.take(5).forEach { inbox.addLine(it.title) }
            if (signals.size > 5) inbox.setSummaryText(context.resources.getQuantityString(R.plurals.alarms_more, signals.size - 5, signals.size - 5))
            val summary = builder(context, CHANNEL_ALARMS, title, signals.joinToString(" · ") { it.title }, now, open(context, ID_SUMMARY, null))
                .setStyle(inbox)
                .setGroup(GROUP_ALARMS)
                .setGroupSummary(true)
                .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_SUMMARY)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_STATUS)
            post(m, ID_SUMMARY, summary)
        }
    }

    fun signedOut(context: Context, now: Long = System.currentTimeMillis()) {
        if (!allowed(context)) return
        channels(context)
        val b = builder(
            context, CHANNEL_ACCOUNT, context.getString(R.string.signed_out_title), context.getString(R.string.signed_out_text),
            now, open(context, ID_SIGNED_OUT, null),
        ).setPriority(NotificationCompat.PRIORITY_DEFAULT)
        post(NotificationManagerCompat.from(context), ID_SIGNED_OUT, b)
    }

    /** A tap opens the app: on the alarm's page of the full Vorath when it has one, else on the Today page. */
    private fun open(context: Context, requestCode: Int, path: String?): PendingIntent {
        val i = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        if (path != null) i.putExtra(EXTRA_PATH, path) else i.putExtra(EXTRA_TODAY, true)
        return PendingIntent.getActivity(context, requestCode, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun builder(context: Context, channel: String, title: String, text: String, now: Long, tap: PendingIntent) =
        NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_stat_vorath)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setWhen(now)
            .setShowWhen(true)
            .setContentIntent(tap)
            .setAutoCancel(true)

    private fun post(m: NotificationManagerCompat, id: Int, b: NotificationCompat.Builder) {
        try {
            m.notify(id, b.build())
        } catch (e: SecurityException) {
            // The permission was taken away between the check and the post.
        }
    }
}
