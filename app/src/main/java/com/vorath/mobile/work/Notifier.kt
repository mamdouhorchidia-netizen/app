package com.vorath.mobile.work

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.vorath.mobile.MainActivity
import com.vorath.mobile.R
import com.vorath.mobile.data.Signal

object Notifier {
    private const val CHANNEL = "alarms"
    const val EXTRA_PATH = "path"

    fun channel(context: Context) {
        val ch = NotificationChannel(CHANNEL, context.getString(R.string.channel_alarms), NotificationManager.IMPORTANCE_HIGH)
        ch.description = context.getString(R.string.channel_alarms_about)
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }

    fun allowed(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    fun tell(context: Context, s: Signal) = post(context, s.id.hashCode(), s.title, s.text, s.href)

    fun signedOut(context: Context) =
        post(context, 1, context.getString(R.string.signed_out_title), context.getString(R.string.signed_out_text), null)

    private fun post(context: Context, id: Int, title: String, text: String, path: String?) {
        if (!allowed(context)) return
        channel(context)
        val open = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        if (path != null) open.putExtra(EXTRA_PATH, path)
        val tap = PendingIntent.getActivity(context, id, open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_vorath)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(id, n)
        } catch (e: SecurityException) {
            // The permission was taken away between the check and the post.
        }
    }
}
