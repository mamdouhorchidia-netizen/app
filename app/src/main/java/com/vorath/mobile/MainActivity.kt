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
import androidx.compose.runtime.LaunchedEffect
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
        Notifier.channel(this)
        if (savedInstanceState == null) openFrom(intent)
        setContent {
            VorathTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    val context = LocalContext.current
                    val session = model.session
                    var notificationsOn by remember { mutableStateOf(Notifier.allowed(context)) }
                    var asked by remember { mutableStateOf(false) }
                    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { notificationsOn = it }

                    LifecycleResumeEffect(session) {
                        notificationsOn = Notifier.allowed(context)
                        model.refreshIfOld()
                        onPauseOrDispose { }
                    }
                    LaunchedEffect(session) {                    // once signed in, ask once; after a refusal the Today page offers the settings
                        if (session != null && !notificationsOn && !asked && Build.VERSION.SDK_INT >= 33) {
                            asked = true
                            ask.launch(Manifest.permission.POST_NOTIFICATIONS)
                        }
                    }

                    val web = model.webPath
                    when {
                        session == null -> SignInScreen(model.lastBase, model.lastUser, model.signingIn, model.signInProblem, model::signIn)
                        web != null -> WebScreen(session, web, onClose = { model.webPath = null; model.refresh() })
                        else -> TodayScreen(
                            session = session,
                            state = model.today,
                            notificationsOn = notificationsOn,
                            onRefresh = model::refresh,
                            onOpen = { model.webPath = it },
                            onAllowNotifications = {
                                startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
                            },
                            onSignOut = { forgetWebSession(); model.signOut() },
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

    /** A notification opens the page its alarm lives on. */
    private fun openFrom(intent: Intent?) {
        val path = intent?.getStringExtra(Notifier.EXTRA_PATH) ?: return
        if (model.session != null && path.startsWith("/")) model.webPath = path
    }
}
