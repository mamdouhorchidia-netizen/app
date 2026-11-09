package com.vorath.mobile

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.vorath.mobile.data.Deck
import com.vorath.mobile.data.Money
import com.vorath.mobile.data.Session
import com.vorath.mobile.data.SessionStore
import com.vorath.mobile.data.Signals
import com.vorath.mobile.data.VorathApi
import com.vorath.mobile.data.VorathError
import com.vorath.mobile.work.AlarmWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class TodayState(
    val loading: Boolean = false,
    val money: Money? = null,
    val moneyNote: String? = null,
    val deck: Deck? = null,
    val deckNote: String? = null,
    val problem: String? = null,
    val checkedAt: Long = 0,
)

class AppModel(app: Application) : AndroidViewModel(app) {
    private val store = SessionStore(app)

    var session by mutableStateOf(store.load())
        private set
    var today by mutableStateOf(TodayState())
        private set
    var signingIn by mutableStateOf(false)
        private set
    var signInProblem by mutableStateOf<String?>(null)
        private set

    /** The page of the full Vorath open inside the app, or null. */
    var webPath by mutableStateOf<String?>(null)

    val lastBase get() = store.lastBase
    val lastUser get() = store.lastUser

    init {
        if (session != null) AlarmWorker.start(app)
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
                val s = withContext(Dispatchers.IO) {
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
                    val me = api.me()
                    Session(base, r.sessionId, me.user, me.name, me.client)
                }
                store.save(s)
                session = s
                today = TodayState()
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
        AlarmWorker.stop(getApplication())
        store.clear()
        session = null
        webPath = null
        today = TodayState()
        viewModelScope.launch(Dispatchers.IO) { runCatching { VorathApi(s.base, s.sessionId).signOut() } }
    }

    fun refreshIfOld() {
        if (System.currentTimeMillis() - today.checkedAt > 5 * 60_000) refresh()
    }

    fun refresh() {
        val s = session ?: return
        if (today.loading) return
        today = today.copy(loading = true, problem = null)
        viewModelScope.launch {
            val api = VorathApi(s.base, s.sessionId)
            val money = async(Dispatchers.IO) { runCatching { api.money() } }
            val deck = async(Dispatchers.IO) { runCatching { api.deck() } }
            val m = money.await()
            val d = deck.await()
            val errors = listOfNotNull(m.exceptionOrNull(), d.exceptionOrNull())
            if (errors.any { it is VorathError.SignedOut }) {
                AlarmWorker.stop(getApplication())
                store.clear()
                session = null
                webPath = null
                today = TodayState()
                signInProblem = "Your session ended. Sign in again."
                return@launch
            }
            d.getOrNull()?.let { store.told = Signals.of(it).map { x -> x.id }.toSet() }   // seen on screen: no notification for these
            today = today.copy(
                loading = false,
                money = m.getOrNull() ?: today.money,
                moneyNote = (m.exceptionOrNull() as? VorathError.Refused)?.message,
                deck = d.getOrNull() ?: today.deck,
                deckNote = (d.exceptionOrNull() as? VorathError.Refused)?.let { "Your role does not open the Autopilot, so alarms are not shown here." },
                problem = errors.firstOrNull { it !is VorathError.Refused }?.message,
                checkedAt = if (errors.all { it is VorathError.Refused }) System.currentTimeMillis() else today.checkedAt,
            )
        }
    }
}
