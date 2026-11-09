package com.vorath.mobile.work

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.vorath.mobile.data.SessionStore
import com.vorath.mobile.data.Signals
import com.vorath.mobile.data.VorathApi
import com.vorath.mobile.data.VorathError
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

/** About once an hour: read the Autopilot deck and tell the person what started needing them since the last check. */
class AlarmWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val store = SessionStore(applicationContext)
        val s = store.load() ?: return@withContext Result.success()
        try {
            val now = Signals.of(VorathApi(s.base, s.sessionId).deck())
            Signals.fresh(now, store.told).forEach { Notifier.tell(applicationContext, it) }
            store.told = now.map { it.id }.toSet()
            Result.success()
        } catch (e: VorathError.SignedOut) {
            if (!store.toldSignedOut) {
                Notifier.signedOut(applicationContext)
                store.toldSignedOut = true
            }
            Result.success()
        } catch (e: VorathError.Refused) {
            Result.success()                                   // the role does not open the Autopilot: nothing to watch
        } catch (e: VorathError) {
            Result.retry()
        }
    }

    companion object {
        private const val NAME = "vorath-alarms"

        fun start(context: Context) {
            val work = PeriodicWorkRequestBuilder<AlarmWorker>(1, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, work)
        }

        fun stop(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(NAME)
        }
    }
}
