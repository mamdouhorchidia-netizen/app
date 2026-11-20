package com.vorath.mobile.ui

import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsBottomHeight
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.vorath.mobile.TodayState
import com.vorath.mobile.data.Changes
import com.vorath.mobile.data.Deck
import com.vorath.mobile.data.Freshness
import com.vorath.mobile.data.Money
import com.vorath.mobile.data.Section
import com.vorath.mobile.data.Session
import com.vorath.mobile.data.VorathError
import kotlinx.coroutines.delay

/**
 * The Today page. On a phone one column: what the Autopilot says and what needs you, the alarms, then the numbers,
 * cash in per day and the plan. From 600 dp two columns (needs and alarms | numbers, cash, plan), never wider than
 * 840 dp. Every figure is the server's; the page only lays them out.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TodayScreen(
    session: Session,
    state: TodayState,
    notificationsOn: Boolean,
    onRefresh: () -> Unit,
    onOpen: (path: String) -> Unit,
    onAllowNotifications: () -> Unit,
    onSignOut: () -> Unit,
    /** A fixed clock for the pictures of the screens; null: the freshness line ticks with the real time. */
    clock: (() -> Long)? = null,
    /** false once the person chose "Not now" on the notifications card; the menu still offers to turn them on. */
    offerNotifications: Boolean = true,
    onNotNow: () -> Unit = {},
    /** Whether "All alarms" starts open (the pictures of the screens). */
    alarmsOpen: Boolean = false,
) {
    val context = LocalContext.current
    // Animator duration scale 0 (Settings › Accessibility › Remove animations): no flashes at all.
    val reduceMotion = remember {
        runCatching { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }.getOrDefault(false)
    }
    val money = state.money.value
    val deck = state.deck.value
    val changed = rememberChanged(money, deck)

    CompositionLocalProvider(LocalChanged provides changed, LocalReduceMotion provides reduceMotion) {
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
            TopBar(session, state, clock, notificationsOn, onRefresh, onOpen, onAllowNotifications, onSignOut)
            // Only alarms that just started firing are told to TalkBack, politely (after what it is saying).
            Box(Modifier.size(1.dp).semantics {
                liveRegion = LiveRegionMode.Polite
                changed.announce?.let { contentDescription = it }
            })
            PullToRefreshBox(
                // The first read shows the shapes of the page instead of a spinner over nothing.
                isRefreshing = state.spinning && state.hasData,
                onRefresh = onRefresh,
                modifier = Modifier.weight(1f).fillMaxWidth().windowInsetsPadding(WindowInsets.navigationBars.only(WindowInsetsSides.Horizontal)),
            ) {
                BoxWithConstraints(Modifier.fillMaxSize()) {
                    val wide = maxWidth >= Space.twoColumns
                    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()), horizontalAlignment = Alignment.CenterHorizontally) {
                        Column(
                            Modifier.widthIn(max = Space.maxContent).fillMaxWidth().padding(horizontal = Space.l).padding(top = Space.xs, bottom = Space.xl),
                            verticalArrangement = Arrangement.spacedBy(Space.m),
                        ) {
                            Body(state, wide, notificationsOn, offerNotifications, alarmsOpen, onRefresh, onOpen, onAllowNotifications, onNotNow)
                        }
                        Spacer(Modifier.windowInsetsBottomHeight(WindowInsets.navigationBars))
                    }
                }
            }
        }
    }
}

@Composable
private fun Body(
    state: TodayState,
    wide: Boolean,
    notificationsOn: Boolean,
    offerNotifications: Boolean,
    alarmsOpen: Boolean,
    onRefresh: () -> Unit,
    onOpen: (String) -> Unit,
    onAllowNotifications: () -> Unit,
    onNotNow: () -> Unit,
) {
    val t = LocalTones.current
    val problem = state.problem
    if (problem != null && !state.hasData && !state.answered) {
        // Nothing to show and the read failed: the whole page says so, with a way to try again.
        ErrorPanel(problem, onRefresh)
        return
    }
    // With data on screen a failed read is one slim line; the page stays as it was last read.
    if (problem != null) Slim(problem.message ?: "Vorath did not answer.", t.warn, t.warnBg, "Try again", onRefresh)

    val left: @Composable () -> Unit = {
        AttentionColumn(state.deck, offerNotifications && !notificationsOn, alarmsOpen, onOpen, onAllowNotifications, onNotNow)
    }
    val right: @Composable () -> Unit = { NumbersColumn(state.money, state.deck, onOpen) }
    if (wide) {
        Row(horizontalArrangement = Arrangement.spacedBy(Space.l)) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.m)) { left() }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.m)) { right() }
        }
    } else {
        left()
        right()
    }
    if (state.deck.value != null) Quiet("In the background the phone checks the alarms about every 15 minutes.")
}

/** What the Autopilot says, what needs you, the alarms, and (in context) the offer of notifications. */
@Composable
private fun AttentionColumn(
    deck: Section<Deck>,
    offer: Boolean,
    alarmsOpen: Boolean,
    onOpen: (String) -> Unit,
    onAllow: () -> Unit,
    onNotNow: () -> Unit,
) {
    val d = deck.value
    when {
        d != null -> {
            StatusCard(d, onOpen)
            AlarmsCard(d, alarmsOpen)
            if (offer) NotifyCard(onAllow, onNotNow)
        }
        deck.refused != null -> RoleNote("Your role does not open the Autopilot, so what needs you and the alarms are not shown here.")
        deck.error == null -> DeckSkeleton()
    }
}

/** The four numbers, cash in per day and the Autopilot's plan for today. */
@Composable
private fun NumbersColumn(money: Section<Money>, deck: Section<Deck>, onOpen: (String) -> Unit) {
    val t = LocalTones.current
    val m = money.value
    when {
        m != null -> {
            WindowHeading(m)
            Four(m)
            if (m.missingInputs > 0) Notice(
                "${m.missingInputs} costs are not entered yet, so net profit is an estimate.", t.warn, t.warnBg, "Enter them",
            ) { onOpen("/money#inputs") }
            if (m.cash.size > 1) CashChart(m)
        }
        money.refused != null -> RoleNote(money.refused)
        money.error == null -> MoneySkeleton()
    }
    deck.value?.today?.takeIf { it.isNotEmpty() }?.let { Plan(it) }
}

@Composable
private fun TopBar(
    session: Session,
    state: TodayState,
    clock: (() -> Long)?,
    notificationsOn: Boolean,
    onRefresh: () -> Unit,
    onOpen: (String) -> Unit,
    onAllowNotifications: () -> Unit,
    onSignOut: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    // Not a TopAppBar: its fixed 64 dp height clips two lines of title at large font sizes.
    Row(
        Modifier.fillMaxWidth().windowInsetsPadding(WindowInsets.statusBars)
            .wrapContentWidth(Alignment.CenterHorizontally).widthIn(max = Space.maxContent).fillMaxWidth().heightIn(min = 64.dp)
            .padding(start = Space.l, end = Space.xs, top = Space.s, bottom = Space.s),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                session.client.ifBlank { "Vorath" }, Modifier.semantics { heading() },
                style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis,
            )
            FreshLine(state, clock)
        }
        IconButton(onClick = onRefresh, enabled = !state.spinning) { Icon(Icons.Filled.Refresh, contentDescription = "Refresh") }
        Box {
            IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, contentDescription = "More") }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                DropdownMenuItem(text = { Text("Signed in as ${session.name}") }, onClick = {}, enabled = false)
                DropdownMenuItem(text = { Text("Open the full Vorath") }, onClick = { menu = false; onOpen("/") })
                if (!notificationsOn) DropdownMenuItem(text = { Text("Turn on notifications") }, onClick = { menu = false; onAllowNotifications() })
                DropdownMenuItem(text = { Text("Sign out") }, onClick = { menu = false; onSignOut() })
            }
        }
    }
}

/**
 * "Updated 12 s ago" / "Updating…" / "Can't reach Vorath · showing 14:05", under the client's name. It reads the
 * clock itself, once a second, so only this line redraws as the seconds pass, not the page.
 */
@Composable
private fun FreshLine(state: TodayState, clock: (() -> Long)?) {
    val now = if (clock != null) clock() else ticking()
    val f = state.freshness(now)
    val t = LocalTones.current
    val tone = when (f) {
        is Freshness.Unreachable -> t.warn
        is Freshness.Updated -> t.ok
        else -> t.muted
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        if (f != Freshness.Never) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(tone))
            Spacer(Modifier.width(Space.s))
        }
        Text(
            Format.freshness(f, now),
            color = if (f is Freshness.Unreachable) t.warn else MaterialTheme.colorScheme.onSurfaceVariant,
            style = VorathType.numberSmall,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun ticking(): Long {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1_000)
            now = System.currentTimeMillis()
        }
    }
    return now
}

/** The full-page error when nothing has been read yet. */
@Composable
private fun ErrorPanel(problem: VorathError, onRetry: () -> Unit) {
    val title = if (problem is VorathError.Offline) "Can't reach Vorath" else "Vorath did not answer"
    ProblemPanel(title, problem.message ?: "Vorath did not answer.", onRetry)
}

/** What changed since the previous read (data.Changes), and when; read by [flash]. */
@Immutable
class Changed(val keys: Set<String>, val stamp: Int, val at: Long, val announce: String?) {
    companion object {
        val None = Changed(emptySet(), 0, 0, null)
    }
}

val LocalChanged = staticCompositionLocalOf { Changed.None }
val LocalReduceMotion = staticCompositionLocalOf { false }

private class Seen(var money: Money? = null, var deck: Deck? = null, var stamp: Int = 0, var last: Changed = Changed.None)

/**
 * Compares each new read with the one before (the parsed objects stay the same when nothing changed, so this runs
 * only when something did). The first read, from the cache or the server, flashes nothing.
 */
@Composable
private fun rememberChanged(money: Money?, deck: Deck?): Changed {
    val seen = remember { Seen() }
    return remember(money, deck) {
        val keys = Changes.between(seen.money, money, seen.deck, deck)
        val firing = Changes.newlyFiring(seen.deck, deck)
        if (money != null) seen.money = money
        if (deck != null) seen.deck = deck
        if (keys.isEmpty() && firing.isEmpty()) return@remember seen.last
        seen.stamp++
        val announce = when (firing.size) {
            0 -> seen.last.announce
            1 -> "Alarm started firing: ${firing[0].title}"
            else -> "${firing.size} alarms started firing: " + firing.joinToString(", ") { it.title }
        }
        Changed(keys, seen.stamp, System.currentTimeMillis(), announce).also { seen.last = it }
    }
}
