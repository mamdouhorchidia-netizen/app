package com.vorath.mobile.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.vorath.mobile.TodayState
import com.vorath.mobile.data.Alarm
import com.vorath.mobile.data.Deck
import com.vorath.mobile.data.Figure
import com.vorath.mobile.data.Money
import com.vorath.mobile.data.Need
import com.vorath.mobile.data.Session

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
) {
    var menu by remember { mutableStateOf(false) }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(session.client.ifBlank { "Vorath" }, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text("Vorath · ${session.name}", style = MaterialTheme.typography.bodySmall, color = LocalTones.current.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                },
                actions = {
                    IconButton(onClick = onRefresh, enabled = !state.loading) { Icon(Icons.Filled.Refresh, contentDescription = "Refresh") }
                    IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, contentDescription = "More") }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(text = { Text("Open the full Vorath") }, onClick = { menu = false; onOpen("/") })
                        DropdownMenuItem(text = { Text("Sign out") }, onClick = { menu = false; onSignOut() })
                    }
                },
            )
        },
    ) { pad ->
        PullToRefreshBox(isRefreshing = state.loading, onRefresh = onRefresh, modifier = Modifier.padding(pad).fillMaxSize()) {
            LazyColumn(
                Modifier.fillMaxSize(),
                contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 32.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                state.problem?.let { item("problem") { Notice(it, LocalTones.current.bad, LocalTones.current.badBg, "Try again", onRefresh) } }
                state.deck?.let { d ->
                    item("status") { Status(d) }
                    items(d.needs.size, key = { "need$it" }) { NeedRow(d.needs[it], onOpen) }
                }
                state.money?.let { m ->
                    item("window") { Heading("Last ${m.days} days", "${Format.day(m.from)} – ${Format.day(m.to)}") }
                    item("four") { Four(m) }
                    if (m.cash.size > 1) item("cash") { CashLine(m) }
                    if (m.missingInputs > 0) item("missing") {
                        Notice("${m.missingInputs} costs are not entered yet, so net profit is an estimate.", LocalTones.current.warn, LocalTones.current.warnBg, "Enter them") { onOpen("/money#inputs") }
                    }
                }
                state.moneyNote?.let { item("moneyNote") { Quiet(it) } }
                state.deck?.let { d ->
                    if (d.alarms.isNotEmpty()) {
                        item("alarmsHead") { Heading("Alarms", "${d.alarms.count { it.firing && it.on }} firing") }
                        item("alarms") { Panel { d.alarms.forEachIndexed { i, a -> AlarmRow(a, first = i == 0) } } }
                    }
                    if (d.today.isNotEmpty()) {
                        item("planHead") { Heading("Autopilot today", "") }
                        item("plan") {
                            Panel {
                                d.today.forEachIndexed { i, p ->
                                    Row(Modifier.fillMaxWidth().topLine(i > 0, LocalTones.current.line).padding(horizontal = 14.dp, vertical = 11.dp), verticalAlignment = Alignment.CenterVertically) {
                                        Text(p.at, Modifier.width(56.dp), color = LocalTones.current.muted)
                                        Text(p.label, Modifier.weight(1f))
                                        if (p.done) Text("done", color = LocalTones.current.ok, style = MaterialTheme.typography.bodySmall)
                                    }
                                }
                            }
                        }
                    }
                }
                state.deckNote?.let { item("deckNote") { Quiet(it) } }
                if (state.deck != null && !notificationsOn) item("notify") {
                    Notice("Notifications are off, so the phone cannot tell you when an alarm starts.", LocalTones.current.warn, LocalTones.current.warnBg, "Turn on", onAllowNotifications)
                }
                if (state.checkedAt > 0) item("foot") {
                    Quiet("Read at ${Format.clock(state.checkedAt)}. In the background the phone checks the alarms about once an hour.")
                }
            }
        }
    }
}

@Composable
private fun Status(d: Deck) {
    val t = LocalTones.current
    val (fg, bg) = when (d.state) {
        "trouble" -> t.bad to t.badBg
        "attention" -> t.warn to t.warnBg
        "flying" -> t.ok to t.okBg
        else -> t.muted to MaterialTheme.colorScheme.surfaceVariant
    }
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(bg).padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(12.dp).clip(CircleShape).background(fg))
        Spacer(Modifier.width(12.dp))
        Column {
            Text(d.word, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, color = fg)
            val n = d.needs.size
            Text(if (n == 0) "Nothing needs you right now." else if (n == 1) "1 thing needs you." else "$n things need you.", color = MaterialTheme.colorScheme.onSurface)
        }
    }
}

@Composable
private fun NeedRow(n: Need, onOpen: (String) -> Unit) {
    val t = LocalTones.current
    val tone = when (n.level) { "bad" -> t.bad; "warn" -> t.warn; else -> t.muted }
    Panel {
        Row(Modifier.height(IntrinsicSize.Min)) {
            Box(Modifier.width(4.dp).fillMaxHeight().background(tone))
            Column(Modifier.weight(1f).padding(start = 12.dp, end = 6.dp, top = 12.dp, bottom = 4.dp)) {
                Text(n.text)
                if (n.href.isNotBlank()) TextButton(onClick = { onOpen(n.href) }, contentPadding = PaddingValues(horizontal = 0.dp)) {
                    Text(n.label.ifBlank { "Open" })
                }
            }
        }
    }
}

@Composable
private fun Four(m: Money) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        m.four.chunked(2).forEach { pair ->
            Row(Modifier.height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                pair.forEach { FigureTile(it, Modifier.weight(1f).fillMaxHeight()) }
                if (pair.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun FigureTile(f: Figure, modifier: Modifier) {
    val t = LocalTones.current
    Panel(modifier) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(f.label, Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelLarge)
                if (f.estimated) Text(
                    "estimate", color = t.warn, style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(t.warnBg).padding(horizontal = 6.dp, vertical = 2.dp),
                )
            }
            Text(Format.money(f.egp), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, maxLines = 1)
            f.changePct?.let { ch ->
                Text(
                    (if (ch > 0) "▲ " else if (ch < 0) "▼ " else "") + "${Math.abs(ch)}% vs previous period",
                    color = if (ch > 0) t.ok else if (ch < 0) t.bad else t.muted, style = MaterialTheme.typography.bodySmall,
                )
            }
            Text(
                (f.count?.let { Format.number(it) + " " } ?: "") + f.sub,
                color = t.muted, style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun CashLine(m: Money) {
    val t = LocalTones.current
    val line = MaterialTheme.colorScheme.primary
    Panel {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Cash in per day", Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelLarge)
                Legend(line, "this period")
                Spacer(Modifier.width(10.dp))
                Legend(t.ghost, "previous")
            }
            Spacer(Modifier.height(10.dp))
            val top = (m.cash + m.cashPrevious).maxOrNull()?.takeIf { it > 0 } ?: 1.0
            Canvas(Modifier.fillMaxWidth().height(96.dp)) {
                fun draw(values: List<Double>, color: Color, width: Float) {
                    if (values.size < 2) return
                    val p = Path()
                    values.forEachIndexed { i, v ->
                        val x = size.width * i / (values.size - 1)
                        val y = size.height - (size.height - width) * (v.coerceAtLeast(0.0) / top).toFloat() - width / 2
                        if (i == 0) p.moveTo(x, y) else p.lineTo(x, y)
                    }
                    drawPath(p, color, style = Stroke(width, cap = StrokeCap.Round, join = StrokeJoin.Round))
                }
                drawLine(t.line, Offset(0f, size.height), Offset(size.width, size.height), 1.dp.toPx())
                draw(m.cashPrevious, t.ghost, 1.5.dp.toPx())
                draw(m.cash, line, 2.5.dp.toPx())
            }
            Row(Modifier.fillMaxWidth().padding(top = 6.dp)) {
                Text(Format.day(m.from), Modifier.weight(1f), color = t.muted, style = MaterialTheme.typography.bodySmall)
                Text(Format.day(m.to), color = t.muted, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun Legend(color: Color, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(4.dp))
        Text(text, color = LocalTones.current.muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun AlarmRow(a: Alarm, first: Boolean) {
    val t = LocalTones.current
    val tone = if (!a.on) t.ghost else if (a.firing) t.bad else t.ok
    Row(Modifier.fillMaxWidth().topLine(!first, t.line).padding(horizontal = 14.dp, vertical = 12.dp)) {
        Box(Modifier.padding(top = 6.dp).size(9.dp).clip(CircleShape).background(tone))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(a.title, Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
                Text(if (!a.on) "off" else if (a.firing) "firing" else "quiet", color = if (a.firing && a.on) t.bad else t.muted, style = MaterialTheme.typography.bodySmall)
            }
            if (a.on) Text(a.text, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun Heading(title: String, aside: String) {
    Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.Bottom) {
        Text(title, Modifier.weight(1f), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        Text(aside, color = LocalTones.current.muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun Notice(text: String, fg: Color, bg: Color, action: String, onAction: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(bg).clickable(onClick = onAction).padding(start = 14.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, Modifier.weight(1f), color = fg, style = MaterialTheme.typography.bodyMedium)
        TextButton(onClick = onAction) { Text(action, color = fg, fontWeight = FontWeight.SemiBold) }
    }
}

@Composable
private fun Quiet(text: String) {
    Text(text, color = LocalTones.current.muted, style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun Panel(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val shape = RoundedCornerShape(16.dp)
    Box(modifier.fillMaxWidth().clip(shape).background(MaterialTheme.colorScheme.surface).border(1.dp, LocalTones.current.line, shape)) {
        Column { content() }
    }
}

private fun Modifier.topLine(show: Boolean, color: Color): Modifier =
    if (!show) this else drawBehind { drawLine(color, Offset(0f, 0f), Offset(size.width, 0f), 1.dp.toPx()) }
