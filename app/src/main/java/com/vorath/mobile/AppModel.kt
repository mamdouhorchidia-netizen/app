package com.vorath.mobile

import android.app.Application
import android.net.ConnectivityManager
import android.net.Network
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.vorath.mobile.data.Cached
import com.vorath.mobile.data.Cadence
import com.vorath.mobile.data.Deck
import com.vorath.mobile.data.Freshness
import com.vorath.mobile.data.Keystore
import com.vorath.mobile.data.Money
import com.vorath.mobile.data.Parse
import com.vorath.mobile.data.Section
import com.vorath.mobile.data.Session
import com.vorath.mobile.data.SessionStore
import com.vorath.mobile.data.Signals
import com.vorath.mobile.data.Snapshot
import com.vorath.mobile.data.TodayCache
import com.vorath.mobile.data.VorathApi
import com.vorath.mobile.data.VorathError
import com.vorath.mobile.work.AlarmWorker
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject

/** What the Today page shows. Each section keeps its own data, read time and error (data.Section). */
data class TodayState(
    val money: Section<Money> = Section(),
    val deck: Section<Deck> = Section(),
    /** The pull-to-refresh spinner: only for a pull, or the first read when there is nothing to show yet. */
    val spinning: Boolean = false,
    /** When the read in flight started (wall clock), or null. The 30-second reads set this but never [spinning]. */
    val busySince: Long? = null,
) {
    val answered: Boolean get() = money.answered || deck.answered
    val hasData: Boolean get() = money.value != null || deck.value != null

    /** The latest read's problem, if one of the sections failed. */
    val problem: VorathError? get() = money.error ?: deck.error

    fun freshness(now: Long): Freshness = Freshness.of(listOf(money, deck), busySince, now)
}

class AppModel(app: Application) : AndroidViewModel(app) {
    private val store = SessionStore(app)
    private val cache = TodayCache(app.noBackupFilesDir, Keystore)
    private val disk = Dispatchers.IO.limitedParallelism(1)   // cache reads and writes, in order

    var session by mutableStateOf(store.load())
        private set
    var today by mutableStateOf(TodayState())
        private set
    var signingIn by mutableStateOf(false)
        private set
    var signInProblem by mutableStateOf<String?>(null)
        private set

    /** The page of the full Vorath open inside the app, or null. While it is open the 30-second reads pause. */
    var webPath by mutableStateOf<String?>(null)

    val lastBase get() = store.lastBase
    val lastUser get() = store.lastUser

    /** One client per session, so connections and the servers' ETags are reused. */
    private var client: VorathApi? = null
    private var clientOf: Session? = null
    private var snapshot: Snapshot? = null
    private var opening: Job? = null
    private var reading: Deferred<Boolean>? = null

    private var loop: Job? = null
    @Volatile private var failures = 0
    private var lastTookMs = 0L
    private var lastTriedAt = 0L                                   // SystemClock.elapsedRealtime()
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private val net = app.getSystemService(ConnectivityManager::class.java)
    private var watching = false
    private val netBack = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            if (failures > 0) wake.trySend(Unit)                 // back online after failed reads: try now, not at the end of the backoff
        }
    }

    init {
        session?.let {
            AlarmWorker.start(app)
            openCache(it)
        }
    }

    fun signIn(typedAddress: String, user: String, password: String, code: String) {
        val base = VorathApi.address(typedAddress)
        if (base == null) {
            signInProblem = "Enter your Vorath address, like vorath.yourbrand.com."
            return
        }
        if (user.isBlank() || password.isEmpty()) {
            signInProblem = "Enter your user name and password."
            return
        }
        signingIn = true
        signInProblem = null
        viewModelScope.launch {
            try {
                val (s, api, meRaw) = withContext(Dispatchers.IO) {
                    VorathApi(base).health()
                    val r = VorathApi(base).signIn(user, password, code)
                    val api = VorathApi(base, r.sessionId)
                    if (r.setup || r.mustChange) {
                        runCatching { api.signOut() }
                        throw VorathError.Refused(
                            if (r.mustChange) "Change your first password in the browser at $base/login, then sign in here."
                            else "Set up your 2-step code in the browser at $base/login, then sign in here."
                        )
                    }
                    val raw = api.meRaw()
                    val me = Parse.me(raw.json)
                    Triple(Session(base, r.sessionId, me.user, me.name, me.client), api, raw)
                }
                store.save(s)
                cache.clear()                                      // nothing of an earlier person or server carries over
                session = s
                client = api
                clientOf = s
                today = TodayState()
                failures = 0
                lastTriedAt = 0
                val first = Snapshot(TodayCache.owner(s.base, s.user), me = Cached(meRaw.path, meRaw.body, System.currentTimeMillis(), meRaw.etag, meRaw.lastModified))
                snapshot = first
                val token = cache.token()
                launch(disk) { cache.save(first, token) }
                AlarmWorker.start(getApplication())
                refresh()
            } catch (e: VorathError) {
                signInProblem = e.message
            } finally {
                signingIn = false
            }
        }
    }

    fun signOut() {
        val s = session ?: return
        val api = clientFor(s)
        end(null)
        viewModelScope.launch(Dispatchers.IO) { runCatching { api.signOut() } }
    }

    /** Pull-to-refresh and the refresh button: shows the spinner until the read is done. */
    fun refresh() {
        if (session == null) return
        today = today.copy(spinning = true)
        read()
    }

    /**
     * The quiet foreground reads: every 30 s while the Today page is in front (resumed), never while the in-app Vorath
     * page is open. Called on resume; [stopLoop] on pause. A slow server is read less often, failures back off.
     */
    fun startLoop() {
        if (loop?.isActive == true || session == null || webPath != null) return
        while (wake.tryReceive().isSuccess) Unit                   // an old wake-up is not a reason to read now
        watchNetwork(true)
        loop = viewModelScope.launch {
            while (isActive) {
                val wait = lastTriedAt + Cadence.next(failures, lastTookMs) - SystemClock.elapsedRealtime()
                if (wait > 0) {
                    val woken = withTimeoutOrNull(wait) { wake.receive() } != null
                    // A pull may have read meanwhile: wait the rest of the interval from that read.
                    if (!woken && lastTriedAt + Cadence.next(failures, lastTookMs) > SystemClock.elapsedRealtime()) continue
                }
                (read() ?: break).await()
            }
        }
    }

    fun stopLoop() {
        loop?.cancel()
        loop = null
        watchNetwork(false)
    }

    override fun onCleared() {
        stopLoop()
    }

    /** Start a read of the numbers and the deck, or join the one in flight. null when signed out. */
    private fun read(): Deferred<Boolean>? {
        val s = session ?: return null
        reading?.takeIf { it.isActive }?.let { return it }
        return viewModelScope.async { readNow(s) }.also { reading = it }
    }

    private suspend fun readNow(s: Session): Boolean {
        opening?.join()                                            // the cached bodies and their ETags first
        if (session !== s) return false
        val api = clientFor(s)
        val token = cache.token()
        today = today.copy(busySince = System.currentTimeMillis(), spinning = today.spinning || !today.answered)
        val started = SystemClock.elapsedRealtime()
        val (m, d) = coroutineScope {
            val m = async(Dispatchers.IO) { runCatching { api.stateRaw() } }
            val d = async(Dispatchers.IO) { runCatching { api.deckRaw() } }
            m.await() to d.await()
        }
        lastTriedAt = SystemClock.elapsedRealtime()
        lastTookMs = lastTriedAt - started
        if (session !== s) return false                            // signed out while reading: drop the answers

        if (m.exceptionOrNull() is VorathError.SignedOut || d.exceptionOrNull() is VorathError.SignedOut) {
            end("Your session ended. Sign in again.")
            return false
        }
        val now = System.currentTimeMillis()
        val money = section(today.money, m, now, Parse::money)
        val deck = section(today.deck, d, now, Parse::deck)
        if (deck.value != null && deck.value !== today.deck.value) {
            store.told = Signals.of(deck.value).map { it.id }.toSet()   // seen on screen: no notification for these
        }
        today = today.copy(money = money, deck = deck, busySince = null, spinning = false)
        val ok = money.error == null && deck.error == null
        failures = if (ok) 0 else failures + 1
        keep(s, token, m, d, now)
        return ok
    }

    /**
     * The section after one read. An unchanged body (304, or the same bytes) keeps the same parsed object, so the list
     * does not redraw; a failure keeps the old data and only records the error.
     */
    private fun <T> section(old: Section<T>, r: Result<VorathApi.Raw>, now: Long, parse: (JSONObject) -> T): Section<T> {
        val raw = r.getOrElse { e ->
            return when (e) {
                is VorathError.Refused -> Section(refused = e.message.orEmpty(), fetchedAt = now)
                is VorathError -> old.copy(error = e)
                else -> old.copy(error = VorathError.NotVorath("Vorath did not answer as expected."))
            }
        }
        if (raw.unchanged && old.value != null) return old.copy(fetchedAt = now, error = null, refused = null)
        val value = try {
            parse(JSONObject(raw.body))
        } catch (e: Exception) {
            return old.copy(error = VorathError.NotVorath("Vorath did not answer as expected."))
        }
        return Section(value = value, fetchedAt = now)
    }

    /** Write the answers to the cache when a body changed, or every few minutes to keep the read time honest. */
    private fun keep(s: Session, token: Int, m: Result<VorathApi.Raw>, d: Result<VorathApi.Raw>, now: Long) {
        val prev = snapshot ?: Snapshot(TodayCache.owner(s.base, s.user))
        fun entry(old: Cached?, r: Result<VorathApi.Raw>): Cached? = r.fold(
            { Cached(it.path, it.body, now, it.etag, it.lastModified) },
            { if (it is VorathError.Refused) null else old },
        )
        val next = prev.copy(state = entry(prev.state, m), deck = entry(prev.deck, d))
        val changed = listOf(m, d).any { it.getOrNull()?.unchanged == false || it.exceptionOrNull() is VorathError.Refused }
        val stale = listOfNotNull(prev.state, prev.deck).any { now - it.fetchedAt > CACHE_TOUCH_MS }
        if (next == prev || !(changed || stale)) return
        snapshot = next
        viewModelScope.launch(disk) { cache.save(next, token) }
    }

    /** Show the last-known answers at once, before the network answers. */
    private fun openCache(s: Session) {
        opening = viewModelScope.launch {
            val snap = withContext(disk) { cache.load(TodayCache.owner(s.base, s.user)) } ?: return@launch
            if (session !== s) return@launch
            snapshot = snap
            val api = clientFor(s)
            listOfNotNull(snap.me, snap.state, snap.deck).forEach { api.remember(it.path, it.body, it.etag, it.lastModified) }
            fun <T> cached(c: Cached?, parse: (JSONObject) -> T): Section<T> =
                c?.let { runCatching { Section(value = parse(JSONObject(it.body)), fetchedAt = it.fetchedAt) }.getOrNull() } ?: Section()
            today = today.copy(
                money = if (today.money.answered) today.money else cached(snap.state, Parse::money),
                deck = if (today.deck.answered) today.deck else cached(snap.deck, Parse::deck),
            )
        }
    }

    private fun clientFor(s: Session): VorathApi {
        client?.takeIf { clientOf === s }?.let { return it }
        return VorathApi(s.base, s.sessionId).also {
            client = it
            clientOf = s
        }
    }

    /** Sign-out, or the server ended the session: forget the session, the cache and the client. */
    private fun end(problem: String?) {
        stopLoop()
        AlarmWorker.stop(getApplication())
        store.clear()
        cache.clear()
        reading?.cancel()
        reading = null
        opening?.cancel()
        opening = null
        session = null
        client = null
        clientOf = null
        snapshot = null
        webPath = null
        today = TodayState()
        failures = 0
        lastTriedAt = 0
        lastTookMs = 0
        signInProblem = problem
    }

    private fun watchNetwork(on: Boolean) {
        if (on == watching || net == null) return
        watching = on
        runCatching { if (on) net.registerDefaultNetworkCallback(netBack) else net.unregisterNetworkCallback(netBack) }
    }

    private companion object {
        const val CACHE_TOUCH_MS = 5 * 60_000L
    }
}
