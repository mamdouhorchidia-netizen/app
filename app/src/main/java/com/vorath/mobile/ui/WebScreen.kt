package com.vorath.mobile.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.vorath.mobile.data.Session
import com.vorath.mobile.data.VorathApi

/**
 * The full Vorath inside the app, already signed in: the phone's session goes into the web view as the server's own
 * cookie. Only the Vorath server opens here; any other link goes to the browser.
 */
@SuppressLint("SetJavaScriptEnabled")
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WebScreen(session: Session, path: String, onClose: () -> Unit) {
    var loading by remember { mutableStateOf(true) }
    var view by remember { mutableStateOf<WebView?>(null) }
    val host = remember(session.base) { Uri.parse(session.base).host }

    BackHandler { view?.takeIf { it.canGoBack() }?.goBack() ?: onClose() }
    DisposableEffect(Unit) { onDispose { view?.destroy() } }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text(session.client.ifBlank { "Vorath" }) },
            navigationIcon = { IconButton(onClick = onClose) { Icon(Icons.Filled.Close, contentDescription = "Close") } },
        )
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize()) {
            if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
            AndroidView(modifier = Modifier.fillMaxSize(), factory = { context ->
                val cookies = CookieManager.getInstance()
                cookies.setAcceptCookie(true)
                cookies.setCookie(session.base, "${VorathApi.COOKIE}=${session.sessionId}; Path=/; Secure; HttpOnly; SameSite=Strict")
                WebView(context).apply {
                    settings.javaScriptEnabled = true                  // Vorath's pages are built by their scripts
                    settings.domStorageEnabled = true
                    settings.allowFileAccess = false
                    settings.allowContentAccess = false
                    webViewClient = object : WebViewClient() {
                        override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest): Boolean {
                            val u = request.url
                            if (u.scheme == "https" && u.host == host) return false
                            runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, u).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                            return true
                        }

                        override fun onPageFinished(v: WebView, url: String) {
                            loading = false
                        }
                    }
                    loadUrl(session.base + (if (path.startsWith("/")) path else "/$path"))
                    view = this
                }
            })
        }
    }
}

/** When the person signs out, the web view's copy of the session goes too. */
fun forgetWebSession() {
    runCatching {
        CookieManager.getInstance().removeAllCookies(null)
        CookieManager.getInstance().flush()
    }
}
