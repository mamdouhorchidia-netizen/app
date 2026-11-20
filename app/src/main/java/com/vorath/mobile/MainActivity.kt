package com.vorath.mobile

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LifecycleResumeEffect
import com.vorath.mobile.ui.SignInScreen
import com.vorath.mobile.ui.TodayScreen
import com.vorath.mobile.ui.VorathTheme
import com.vorath.mobile.ui.WebScreen
import com.vorath.mobile.ui.forgetWebSession
import com.vorath.mobile.work.Notifier

class MainActivity : ComponentActivity() {
    private val model: AppModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Notifier.channels(this)
        if (savedInstanceState == null) openFrom(intent)
        setContent {
            VorathTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    val context = LocalContext.current
                    val session = model.session
                    var notificationsOn by remember { mutableStateOf(Notifier.allowed(context)) }
                    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { notificationsOn = it }

                    val web = model.webPath
                    // The quiet 30-second reads run only while this screen is resumed and the in-app Vorath page is closed.
                    LifecycleResumeEffect(session, web) {
                        notificationsOn = Notifier.allowed(context)
                        model.startLoop()
                        onPauseOrDispose { model.stopLoop() }
                    }
                    // Not asked at sign-in: the Today page explains why next to the alarms, and asks when the person taps
                    // "Allow". The first tap shows Android's dialog (remembered across restarts); later taps open the settings.
                    val allow = {
                        if (Build.VERSION.SDK_INT >= 33 && !model.notifyAsked) {
                            model.markNotifyAsked()
                            ask.launch(Manifest.permission.POST_NOTIFICATIONS)
                        } else {
                            startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
                        }
                    }

                    when {
                        session == null -> SignInScreen(model.lastBase, model.lastUser, model.signingIn, model.signInProblem, model::signIn)
                        web != null -> WebScreen(session, web, onClose = { model.webPath = null }, onSessionEnded = model::sessionEnded)
                        else -> TodayScreen(
                            session = session,
                            state = model.today,
                            notificationsOn = notificationsOn,
                            onRefresh = model::refresh,
                            onOpen = { model.webPath = it },
                            onAllowNotifications = allow,
                            onSignOut = { forgetWebSession(); model.signOut() },
                            offerNotifications = !model.notifyDeclined,
                            onNotNow = model::declineNotify,
                        )
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openFrom(intent)
    }

    /** A notification opens the page its alarm lives on, or the Today page (closing the in-app Vorath page if open). */
    private fun openFrom(intent: Intent?) {
        if (intent == null || model.session == null) return
        val path = intent.getStringExtra(Notifier.EXTRA_PATH)
        when {
            path != null -> if (path.startsWith("/")) model.webPath = path
            intent.getBooleanExtra(Notifier.EXTRA_TODAY, false) -> model.webPath = null
        }
    }
}
