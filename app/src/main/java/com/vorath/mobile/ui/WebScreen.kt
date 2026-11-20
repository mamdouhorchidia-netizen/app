package com.vorath.mobile.ui

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Environment
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.net.toUri
import com.vorath.mobile.data.Session
import com.vorath.mobile.data.VorathApi
import com.vorath.mobile.data.WebLinks
import java.net.URLConnection

/** Why the page did not open, as the in-app error says it. */
data class WebProblem(val title: String, val text: String)

/**
 * The full Vorath inside the app, already signed in: the phone's session goes into the web view as the server's own
 * cookie, and the page is opened only once the cookie is stored. Only this Vorath opens here; other sites, mail and
 * phone links go to the phone's apps (data.WebLinks). Back goes back through the pages before it closes. When the
 * server sends the page to its sign-in page (or answers 401), the session is over: [onSessionEnded] signs out the app
 * the same way a 401 on the Today page does. Files the page offers are saved by Android's download manager, with the
 * session, into Downloads.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun WebScreen(session: Session, path: String, onClose: () -> Unit, onSessionEnded: () -> Unit) {
    var progress by remember { mutableIntStateOf(0) }
    var problem by remember { mutableStateOf<WebProblem?>(null) }
    var retrying by remember { mutableStateOf(false) }
    var view by remember { mutableStateOf<WebView?>(null) }
    val ended by rememberUpdatedState(onSessionEnded)
    val base = session.base
    val start = base + (if (path.startsWith("/")) path else "/$path")

    BackHandler {
        val v = view
        if (v != null && v.canGoBack() && problem == null) v.goBack() else onClose()
    }
    DisposableEffect(Unit) {
        onDispose {
            view?.apply {
                stopLoading()
                destroy()
            }
            view = null
        }
    }

    WebFrame(
        title = session.client.ifBlank { "Vorath" },
        progress = progress,
        problem = problem,
        retrying = retrying,
        onClose = onClose,
        onRetry = {
            retrying = true
            val v = view
            when {
                v == null -> retrying = false
                v.url.isNullOrEmpty() || v.url == "about:blank" -> openSignedIn(v, session, start) { failed -> if (failed) { problem = cookieProblem; retrying = false } }
                else -> v.reload()
            }
        },
    ) {
        AndroidView(modifier = Modifier.fillMaxSize(), factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = true                  // Vorath's pages are built by their scripts
                settings.domStorageEnabled = true
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                var failed = false                                 // the page being loaded failed (set before onPageFinished)
                var over = false                                   // the session ended: nothing more happens here
                fun sessionOver(v: WebView) {
                    if (over) return
                    over = true
                    v.stopLoading()
                    ended()
                }
                webChromeClient = object : WebChromeClient() {
                    override fun onProgressChanged(v: WebView, newProgress: Int) {
                        progress = newProgress
                    }
                }
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest): Boolean {
                        val url = request.url.toString()
                        val kind = WebLinks.classify(url, base)
                        if (!request.isForMainFrame) return kind == WebLinks.Kind.BLOCKED   // frames inside a page load as they are
                        return when (kind) {
                            WebLinks.Kind.INSIDE -> false
                            WebLinks.Kind.SIGN_IN -> true.also { sessionOver(v) }
                            WebLinks.Kind.OUTSIDE -> true.also { openOutside(v.context, request.url) }
                            WebLinks.Kind.BLOCKED -> true
                        }
                    }

                    override fun onPageStarted(v: WebView, url: String, favicon: Bitmap?) {
                        failed = false
                        progress = 0
                        if (WebLinks.classify(url, base) == WebLinks.Kind.SIGN_IN) sessionOver(v)
                    }

                    override fun doUpdateVisitedHistory(v: WebView, url: String, isReload: Boolean) {
                        // Also the pages a script moves to without loading (history.pushState).
                        if (WebLinks.classify(url, base) == WebLinks.Kind.SIGN_IN) sessionOver(v)
                    }

                    override fun onPageFinished(v: WebView, url: String) {
                        progress = 100
                        if (!failed) problem = null
                        retrying = false
                    }

                    override fun onReceivedError(v: WebView, request: WebResourceRequest, error: WebResourceError) {
                        if (!request.isForMainFrame) return
                        failed = true
                        problem = problemOf(error.errorCode)
                    }

                    override fun onReceivedHttpError(v: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                        if (!request.isForMainFrame || !WebLinks.sameOrigin(request.url.toString(), base)) return
                        when {
                            response.statusCode == 401 -> sessionOver(v)
                            response.statusCode >= 500 -> {
                                failed = true
                                problem = WebProblem("Vorath had a problem", "It answered with an error (${response.statusCode}). Try again in a moment.")
                            }
                        }
                    }

                    override fun onReceivedSslError(v: WebView, handler: SslErrorHandler, error: SslError) {
                        handler.cancel()                           // never proceed past a bad certificate
                        if (WebLinks.sameOrigin(error.url, base)) {
                            failed = true
                            problem = WebProblem("Not a secure connection", "Vorath's certificate could not be checked, so the page was not opened.")
                        }
                    }
                }
                setDownloadListener { url, userAgent, disposition, mimeType, _ -> download(context, session, url, userAgent, disposition, mimeType) }
                view = this
                openSignedIn(this, session, start) { cookieFailed -> if (cookieFailed) problem = cookieProblem }
            }
        })
    }
}

private val cookieProblem = WebProblem("Vorath did not open", "The app could not hand your session to the page. Try again.")

/** Store the session cookie, wait until it is stored, then open the page. [done] gets true when it could not be stored. */
private fun openSignedIn(v: WebView, session: Session, url: String, done: (failed: Boolean) -> Unit) {
    val cookies = CookieManager.getInstance()
    cookies.setAcceptCookie(true)
    cookies.setCookie(session.base, "${VorathApi.COOKIE}=${session.sessionId}; Path=/; Secure; HttpOnly; SameSite=Strict") { stored ->
        if (stored != true) {
            done(true)
            return@setCookie
        }
        cookies.flush()
        v.loadUrl(url)
        done(false)
    }
}

private fun problemOf(code: Int): WebProblem = when (code) {
    WebViewClient.ERROR_HOST_LOOKUP, WebViewClient.ERROR_CONNECT, WebViewClient.ERROR_TIMEOUT, WebViewClient.ERROR_IO ->
        WebProblem("Can't reach Vorath", "Check the phone's connection, then try again.")
    WebViewClient.ERROR_FAILED_SSL_HANDSHAKE ->
        WebProblem("Not a secure connection", "The secure connection to Vorath could not be made, so the page was not opened.")
    else -> WebProblem("This page did not open", "Something went wrong loading it. Try again.")
}

private fun openOutside(context: Context, url: Uri) {
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, url).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        .onFailure { Toast.makeText(context, "No app on this phone opens that link.", Toast.LENGTH_SHORT).show() }
}

/**
 * A file the page offers: from this Vorath, saved by the download manager with the session cookie (it downloads
 * outside the web view, so it needs it); from another site, opened in the browser (no cookie goes there).
 */
private fun download(context: Context, session: Session, url: String, userAgent: String?, disposition: String?, mimeType: String?) {
    when (WebLinks.classify(url, session.base)) {
        WebLinks.Kind.INSIDE, WebLinks.Kind.SIGN_IN -> Unit
        WebLinks.Kind.OUTSIDE -> return openOutside(context, url.toUri())
        WebLinks.Kind.BLOCKED -> {
            // blob: and data: files exist only inside the page; the download manager cannot fetch them.
            Toast.makeText(context, "This file can only be saved from Vorath in the browser.", Toast.LENGTH_LONG).show()
            return
        }
    }
    val name = WebLinks.downloadName(url, disposition, mimeType)
    try {
        val sid = "${VorathApi.COOKIE}=${session.sessionId}"
        val cookie = CookieManager.getInstance().getCookie(url)?.takeIf { it.contains("${VorathApi.COOKIE}=") } ?: sid
        val type = mimeType?.takeIf { it.isNotBlank() && it != "application/octet-stream" } ?: URLConnection.guessContentTypeFromName(name)
        val request = DownloadManager.Request(url.toUri())
            .setTitle(name)
            .setDescription(session.base.toUri().host)
            .addRequestHeader("Cookie", cookie)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
        if (!userAgent.isNullOrBlank()) request.addRequestHeader("User-Agent", userAgent)
        if (type != null) request.setMimeType(type)
        // Downloads, where the Files app shows it. Before Android 10 that folder needs a storage permission the app
        // does not ask for, so there the file goes to the app's own Downloads folder (the notification still opens it).
        if (Build.VERSION.SDK_INT >= 29) request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
        else request.setDestinationInExternalFilesDir(context, Environment.DIRECTORY_DOWNLOADS, name)
        context.getSystemService(DownloadManager::class.java).enqueue(request)
        Toast.makeText(context, "Saving $name to Downloads…", Toast.LENGTH_SHORT).show()
    } catch (e: Exception) {
        Toast.makeText(context, "Could not save $name.", Toast.LENGTH_LONG).show()
    }
}

/**
 * The frame around the web view without the web view, so its states can be drawn (ScreensTest): the top bar, the
 * page's load progress as a determinate bar, and, when the page did not open, an error with Retry in its place.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WebFrame(
    title: String,
    progress: Int,
    problem: WebProblem?,
    retrying: Boolean,
    onClose: () -> Unit,
    onRetry: () -> Unit,
    content: @Composable () -> Unit,
) {
    Scaffold(topBar = {
        TopAppBar(
            title = { Text(title) },
            navigationIcon = { IconButton(onClick = onClose) { Icon(Icons.Filled.Close, contentDescription = "Close") } },
        )
    }) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            content()
            if (problem != null) {
                WebError(problem, retrying, onRetry, Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background))
            } else if (progress < 100) {
                // At least a sliver, so "opening" shows before the first byte arrives.
                LinearProgressIndicator(
                    progress = { progress.coerceAtLeast(5) / 100f }, modifier = Modifier.fillMaxWidth(),
                    trackColor = MaterialTheme.colorScheme.primaryContainer,
                )
            }
        }
    }
}

@Composable
private fun WebError(problem: WebProblem, retrying: Boolean, onRetry: () -> Unit, modifier: Modifier) {
    Box(modifier.verticalScroll(rememberScrollState()).padding(Space.xl), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.widthIn(max = 420.dp).fillMaxWidth().padding(top = 48.dp).semantics { liveRegion = LiveRegionMode.Polite },
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(Space.m),
        ) {
            Icon(Icons.Filled.Warning, contentDescription = null, tint = LocalTones.current.warn, modifier = Modifier.size(40.dp))
            Text(problem.title, style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center, modifier = Modifier.semantics { heading() })
            Text(problem.text, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
            Spacer(Modifier.height(Space.s))
            Button(onClick = onRetry, enabled = !retrying, modifier = Modifier.height(Space.touch)) {
                if (retrying) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = LocalContentColor.current)
                    Spacer(Modifier.size(Space.s))
                    Text("Trying again…")
                } else {
                    Text("Retry")
                }
            }
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
