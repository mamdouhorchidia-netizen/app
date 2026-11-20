@file:OptIn(ExperimentalLayoutApi::class)

package com.vorath.mobile.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
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
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.vorath.mobile.data.Alarm
import com.vorath.mobile.data.Changes
import com.vorath.mobile.data.Deck
import com.vorath.mobile.data.Figure
import com.vorath.mobile.data.Money
import com.vorath.mobile.data.Need
import com.vorath.mobile.data.Planned
import com.vorath.mobile.data.RowKeys

// ---- The Autopilot: its word, what needs you ------------------------------------------------------------------------

@Composable
internal fun StatusCard(d: Deck, onOpen: (String) -> Unit) {
    val t = LocalTones.current
    val (fg, bg, icon) = when (d.state) {
        "trouble" -> Triple(t.bad, t.badBg, Icons.Filled.Warning)
        "attention" -> Triple(t.warn, t.warnBg, Icons.Filled.Info)
        "flying" -> Triple(t.ok, t.okBg, Icons.Filled.CheckCircle)
        else -> Triple(t.muted, MaterialTheme.colorScheme.surfaceVariant, Icons.Filled.Info)
    }
    val keys = remember(d) { RowKeys.needs(d.needs) }
    Panel {
        Row(
            Modifier.fillMaxWidth().background(bg).flash(Changes.STATUS).padding(Space.l).semantics(mergeDescendants = true) { heading() },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(icon, contentDescription = null, tint = fg, modifier = Modifier.size(28.dp))
            Spacer(Modifier.width(Space.m))
            Column {
                Text(d.word.ifBlank { "Autopilot" }, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, color = fg)
                val n = d.needs.size
                Text(
                    if (n == 0) "Nothing needs you right now." else if (n == 1) "1 thing needs you" else "$n things need you",
                    color = MaterialTheme.colorScheme.onSurface, style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
        d.needs.forEachIndexed { i, n -> NeedRow(n, keys[i], onOpen) }
    }
}

/** One need: the whole row is the button (one focus stop), at least 48 dp tall, the severity as an icon and a word. */
@Composable
private fun NeedRow(n: Need, key: String, onOpen: (String) -> Unit) {
    val t = LocalTones.current
    val (tone, icon, word) = when (n.level) {
        "bad" -> Triple(t.bad, Icons.Filled.Warning, "Urgent")
        "warn" -> Triple(t.warn, Icons.Filled.Info, "Warning")
        else -> Triple(t.muted, Icons.Filled.Info, "Note")
    }
    val go = n.href.isNotBlank()
    val label = n.label.ifBlank { "Open" }
    // At large font sizes the action goes under the text, so the text keeps the row's width.
    val stacked = LocalDensity.current.fontScale >= 1.5f
    Row(
        Modifier.fillMaxWidth().topLine(true, t.line).flash(key)
            .then(if (go) Modifier.clickable(role = Role.Button, onClickLabel = label) { onOpen(n.href) } else Modifier.semantics(mergeDescendants = true) {})
            .heightIn(min = Space.touch).padding(start = Space.l, end = Space.s, top = Space.m, bottom = Space.m),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = word, tint = tone, modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(Space.m))
        if (stacked) Column(Modifier.weight(1f)) {
            Text(n.text, style = MaterialTheme.typography.bodyMedium)
            if (go) Row(Modifier.padding(top = Space.s), verticalAlignment = Alignment.CenterVertically) {
                Text(label, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelLarge)
                Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            }
        } else Text(n.text, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        if (go && !stacked) {
            Spacer(Modifier.width(Space.s))
            Text(label, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelLarge)
            Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
        }
    }
}

// ---- Alarms ---------------------------------------------------------------------------------------------------------

/** The firing alarms in full; the quiet and switched-off ones behind "All alarms (N)", one tap away. */
@Composable
internal fun AlarmsCard(d: Deck, startOpen: Boolean) {
    if (d.alarms.isEmpty()) return
    val t = LocalTones.current
    val firing = d.alarms.filter { it.firing && it.on }
    val rest = d.alarms.filterNot { it.firing && it.on }
    var open by rememberSaveable { mutableStateOf(startOpen) }
    Panel {
        Row(Modifier.fillMaxWidth().padding(horizontal = Space.l, vertical = Space.m), verticalAlignment = Alignment.CenterVertically) {
            Text("Alarms", Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            Text(
                if (firing.isEmpty()) "none firing" else "${firing.size} firing",
                color = if (firing.isEmpty()) t.muted else t.bad, style = VorathType.numberSmall, fontWeight = FontWeight.Medium,
            )
        }
        if (firing.isEmpty()) {
            Row(Modifier.fillMaxWidth().topLine(true, t.line).padding(horizontal = Space.l, vertical = Space.m), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Filled.CheckCircle, contentDescription = null, tint = t.ok, modifier = Modifier.size(20.dp))
                Spacer(Modifier.width(Space.m))
                Text("No alarm is firing.", style = MaterialTheme.typography.bodyMedium)
            }
        }
        firing.forEach { AlarmRow(it) }
        if (rest.isNotEmpty()) {
            val off = rest.count { !it.on }
            val summary = listOfNotNull(
                (rest.size - off).takeIf { it > 0 }?.let { "$it quiet" },
                off.takeIf { it > 0 }?.let { "$it off" },
            ).joinToString(" · ")
            Row(
                Modifier.fillMaxWidth().topLine(true, t.line)
                    .clickable(role = Role.Button, onClickLabel = if (open) "Hide" else "Show") { open = !open }
                    .semantics { stateDescription = if (open) "Expanded" else "Collapsed" }
                    .heightIn(min = Space.touch).padding(start = Space.l, end = Space.m, top = Space.s, bottom = Space.s),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("All alarms (${d.alarms.size})", Modifier.weight(1f), color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelLarge)
                Text(summary, color = t.muted, style = VorathType.numberSmall)
                Spacer(Modifier.width(Space.xs))
                Icon(if (open) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown, contentDescription = null, tint = t.muted)
            }
            if (open) rest.forEach { AlarmRow(it) }
        }
    }
}

@Composable
private fun AlarmRow(a: Alarm) {
    val t = LocalTones.current
    val (tone, icon, word) = when {
        !a.on -> Triple(t.muted, Icons.Filled.Clear, "Off")
        a.firing -> Triple(t.bad, Icons.Filled.Warning, "Firing")
        else -> Triple(t.ok, Icons.Filled.CheckCircle, "Quiet")
    }
    Row(
        Modifier.fillMaxWidth().topLine(true, t.line).flash(Changes.alarmKey(a)).semantics(mergeDescendants = true) {}
            .heightIn(min = Space.touch).padding(horizontal = Space.l, vertical = Space.m),
    ) {
        Icon(icon, contentDescription = null, tint = tone, modifier = Modifier.padding(top = 2.dp).size(20.dp))
        Spacer(Modifier.width(Space.m))
        Column(Modifier.weight(1f)) {
            Row {
                Text(a.title, Modifier.weight(1f), fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.width(Space.s))
                Text(word, color = if (a.firing && a.on) t.bad else t.muted, style = MaterialTheme.typography.labelMedium)
            }
            if (a.on) Text(a.text, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
        }
    }
}

/** Asked here, next to the alarms it is for, rather than straight after sign-in. */
@Composable
internal fun NotifyCard(onAllow: () -> Unit, onNotNow: () -> Unit) {
    Panel {
        Column(Modifier.padding(start = Space.l, end = Space.s, top = Space.l, bottom = Space.s)) {
            Row(Modifier.padding(end = Space.s)) {
                Icon(Icons.Filled.Notifications, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(24.dp))
                Spacer(Modifier.width(Space.m))
                Column(verticalArrangement = Arrangement.spacedBy(Space.xs)) {
                    Text("Hear when an alarm fires", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                    Text(
                        "Allow notifications and the phone tells you when an alarm starts firing. It checks about every 15 minutes, even with Vorath closed.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
            Row(Modifier.fillMaxWidth().padding(top = Space.s), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onNotNow) { Text("Not now") }
                Spacer(Modifier.width(Space.s))
                Button(onClick = onAllow) { Text("Allow") }
            }
        }
    }
}

// ---- The numbers ----------------------------------------------------------------------------------------------------

@Composable
internal fun WindowHeading(m: Money) {
    Row(Modifier.fillMaxWidth().padding(top = Space.s), verticalAlignment = Alignment.Bottom) {
        Text("Last ${m.days} days", Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.width(Space.s))
        Text("${Format.day(m.from)} – ${Format.day(m.to)}", color = LocalTones.current.muted, style = VorathType.numberSmall)
    }
}

/**
 * 2×2 on a phone; 1×4 when four tiles of at least 150 dp fit side by side; one per row when even two do not (the
 * tile's minimum grows with the font size, so at 200 % a phone shows the figures one under the other).
 */
@Composable
internal fun Four(m: Money) {
    val fontScale = LocalDensity.current.fontScale.coerceAtLeast(1f)
    BoxWithConstraints {
        val tile = 150.dp * fontScale
        val fits = ((maxWidth + Space.m) / (tile + Space.m)).toInt()
        val perRow = when {
            fits >= 4 -> 4
            fits >= 2 -> 2
            else -> 1
        }
        val rows = m.four.chunked(perRow)
        Column(verticalArrangement = Arrangement.spacedBy(Space.m)) {
            rows.forEach { row ->
                Row(Modifier.height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(Space.m)) {
                    row.forEach { FigureTile(it, Modifier.weight(1f).fillMaxHeight()) }
                    repeat(perRow - row.size) { Spacer(Modifier.weight(1f)) }
                }
            }
        }
    }
}

/** One figure. TalkBack reads it as one item: "Cash in, EGP 350,778, down 28 percent versus previous period, …". */
@Composable
private fun FigureTile(f: Figure, modifier: Modifier) {
    val t = LocalTones.current
    val ch = f.changePct
    val countSub = (f.count?.let { Format.number(it) + " " } ?: "") + f.sub
    val spoken = listOfNotNull(
        f.label, Format.moneySpoken(f.egp), ch?.let(Format::changeSpoken), "estimate".takeIf { f.estimated }, countSub.takeIf { it.isNotBlank() },
    ).joinToString(", ")
    Panel(modifier) {
        Column(
            Modifier.fillMaxHeight().flash(Changes.figureKey(f)).clearAndSetSemantics { contentDescription = spoken }.padding(Space.m + 2.dp),
            verticalArrangement = Arrangement.spacedBy(Space.xs),
        ) {
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalArrangement = Arrangement.spacedBy(Space.xs)) {
                Text(f.label, Modifier.padding(end = Space.s), color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelLarge)
                if (f.estimated) Text(
                    "estimate", color = t.warn, style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(t.warnBg).padding(horizontal = 6.dp, vertical = 2.dp),
                )
            }
            FitText(Format.money(f.egp), VorathType.figure, MaterialTheme.colorScheme.onSurface)
            ch?.let { ChangeChip(it) }
            if (countSub.isNotBlank()) Text(countSub, color = t.muted, style = VorathType.numberSmall)
        }
    }
}

/** The change as an arrow icon and the percent, in a pill; never a "▲" character a screen reader would spell out. */
@Composable
private fun ChangeChip(pct: Int) {
    val t = LocalTones.current
    val (fg, bg) = when {
        pct > 0 -> t.ok to t.okBg
        pct < 0 -> t.bad to t.badBg
        else -> t.muted to MaterialTheme.colorScheme.surfaceVariant
    }
    FlowRow(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(
            Modifier.padding(end = 6.dp).align(Alignment.CenterVertically).clip(RoundedCornerShape(50)).background(bg).padding(start = 4.dp, end = Space.s, top = 1.dp, bottom = 1.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (pct != 0) Icon(if (pct > 0) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown, contentDescription = null, tint = fg, modifier = Modifier.size(16.dp))
            Text("${Math.abs(pct)}%", color = fg, style = VorathType.numberSmall, fontWeight = FontWeight.SemiBold)
        }
        Text("vs previous", Modifier.align(Alignment.CenterVertically), color = t.muted, style = MaterialTheme.typography.bodySmall)
    }
}

/**
 * One line that shrinks (to 60 %) rather than clip, and wraps if even that does not fit (very large font sizes on a
 * narrow tile). Shrinking takes a frame or two after the first layout.
 */
@Composable
private fun FitText(text: String, style: TextStyle, color: Color) {
    var scale by remember(text, style) { mutableFloatStateOf(1f) }
    var wrap by remember(text, style) { mutableStateOf(false) }
    Text(
        text,
        color = color,
        style = style.copy(fontSize = style.fontSize * scale, lineHeight = style.lineHeight * scale),
        maxLines = if (wrap) Int.MAX_VALUE else 1,
        softWrap = wrap,
        onTextLayout = { r ->
            if (!wrap && r.didOverflowWidth) {
                if (scale > 0.61f) scale = (scale - 0.1f).coerceAtLeast(0.6f) else wrap = true
            }
        },
    )
}

/** Cash in per day, drawn from the server's series; TalkBack hears the period and the first and last day. */
@Composable
internal fun CashChart(m: Money) {
    val t = LocalTones.current
    val line = MaterialTheme.colorScheme.primary
    val said = "Cash in per day, ${Format.day(m.from)} to ${Format.day(m.to)}. First day ${Format.moneySpoken(m.cash.first())}, " +
        "last day ${Format.moneySpoken(m.cash.last())}." + if (m.cashPrevious.size > 1) " The grey line is the previous period." else ""
    Panel(Modifier.clearAndSetSemantics { contentDescription = said }) {
        Column(Modifier.padding(Space.m + 2.dp)) {
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalArrangement = Arrangement.spacedBy(Space.xs)) {
                Text("Cash in per day", Modifier.padding(end = Space.m), color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelLarge)
                Row(Modifier.align(Alignment.CenterVertically), verticalAlignment = Alignment.CenterVertically) {
                    Legend(line, "this period")
                    Spacer(Modifier.width(10.dp))
                    Legend(t.ghost, "previous")
                }
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
                Text(Format.day(m.from), Modifier.weight(1f), color = t.muted, style = VorathType.numberSmall)
                Text(Format.day(m.to), color = t.muted, style = VorathType.numberSmall)
            }
        }
    }
}

@Composable
private fun Legend(color: Color, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(Space.xs))
        Text(text, color = LocalTones.current.muted, style = MaterialTheme.typography.bodySmall)
    }
}

// ---- The plan -------------------------------------------------------------------------------------------------------

/** The Autopilot's plan for today as a timeline: time, a dot on a line (filled when done), what it is. */
@Composable
internal fun Plan(items: List<Planned>) {
    val t = LocalTones.current
    val dotDone = t.ok
    val dotTodo = MaterialTheme.colorScheme.primary
    Panel {
        Text(
            "Autopilot today", Modifier.padding(start = Space.l, end = Space.l, top = Space.m, bottom = Space.xs).semantics { heading() },
            style = MaterialTheme.typography.titleMedium,
        )
        Column(Modifier.padding(bottom = Space.s)) {
            items.forEachIndexed { i, p ->
                Row(
                    Modifier.fillMaxWidth().height(IntrinsicSize.Min).semantics(mergeDescendants = true) {}.padding(horizontal = Space.l),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(p.at, Modifier.widthIn(min = 48.dp), color = t.muted, style = VorathType.numberMedium, softWrap = false)
                    Canvas(Modifier.width(Space.xl).fillMaxHeight()) {
                        val cx = size.width / 2
                        val cy = size.height / 2
                        val r = 5.dp.toPx()
                        val w = 2.dp.toPx()
                        if (i > 0) drawLine(t.line, Offset(cx, 0f), Offset(cx, cy - r), w)
                        if (i < items.size - 1) drawLine(t.line, Offset(cx, cy + r), Offset(cx, size.height), w)
                        if (p.done) drawCircle(dotDone, r, Offset(cx, cy))
                        else drawCircle(dotTodo, r - w / 2, Offset(cx, cy), style = Stroke(w))
                    }
                    Text(p.label, Modifier.weight(1f).padding(vertical = 10.dp), style = MaterialTheme.typography.bodyMedium)
                    if (p.done) {
                        Icon(Icons.Filled.Check, contentDescription = null, tint = t.ok, modifier = Modifier.size(16.dp))
                        Text("Done", color = t.ok, style = MaterialTheme.typography.labelMedium)
                    }
                }
            }
        }
    }
}

// ---- States: first read, problems, refusals -------------------------------------------------------------------------

/** The status card and the alarms, as grey shapes, while the first read is under way. */
@Composable
internal fun DeckSkeleton() {
    Panel(Modifier.clearAndSetSemantics { contentDescription = "Loading what needs you" }) {
        Row(Modifier.padding(Space.l), verticalAlignment = Alignment.CenterVertically) {
            Bone(Modifier.size(28.dp), CircleShape)
            Spacer(Modifier.width(Space.m))
            Column(verticalArrangement = Arrangement.spacedBy(Space.s)) {
                Bone(Modifier.width(150.dp).height(22.dp))
                Bone(Modifier.width(110.dp).height(14.dp))
            }
        }
        repeat(2) {
            Row(Modifier.fillMaxWidth().topLine(true, LocalTones.current.line).padding(Space.l), verticalAlignment = Alignment.CenterVertically) {
                Bone(Modifier.size(20.dp), CircleShape)
                Spacer(Modifier.width(Space.m))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Space.s)) {
                    Bone(Modifier.fillMaxWidth().height(14.dp))
                    Bone(Modifier.fillMaxWidth(0.6f).height(14.dp))
                }
            }
        }
    }
    Panel(Modifier.clearAndSetSemantics { }) {
        Column(Modifier.padding(Space.l), verticalArrangement = Arrangement.spacedBy(Space.m)) {
            Bone(Modifier.width(80.dp).height(18.dp))
            Bone(Modifier.fillMaxWidth().height(14.dp))
            Bone(Modifier.fillMaxWidth(0.4f).height(14.dp))
        }
    }
}

/** The four tiles and the chart, as grey shapes. */
@Composable
internal fun MoneySkeleton() {
    Column(Modifier.clearAndSetSemantics { contentDescription = "Loading the numbers" }, verticalArrangement = Arrangement.spacedBy(Space.m)) {
        Bone(Modifier.padding(top = Space.s).width(120.dp).height(20.dp))
        repeat(2) {
            Row(horizontalArrangement = Arrangement.spacedBy(Space.m)) {
                repeat(2) {
                    Panel(Modifier.weight(1f)) {
                        Column(Modifier.padding(Space.m + 2.dp), verticalArrangement = Arrangement.spacedBy(Space.s)) {
                            Bone(Modifier.width(64.dp).height(14.dp))
                            Bone(Modifier.fillMaxWidth(0.85f).height(26.dp))
                            Bone(Modifier.width(90.dp).height(12.dp))
                            Bone(Modifier.fillMaxWidth().height(12.dp))
                        }
                    }
                }
            }
        }
        Panel {
            Column(Modifier.padding(Space.m + 2.dp), verticalArrangement = Arrangement.spacedBy(Space.m)) {
                Bone(Modifier.width(110.dp).height(14.dp))
                Bone(Modifier.fillMaxWidth().height(96.dp))
            }
        }
    }
}

@Composable
private fun Bone(modifier: Modifier, shape: androidx.compose.ui.graphics.Shape = RoundedCornerShape(6.dp)) {
    Box(modifier.clip(shape).background(LocalTones.current.skeleton))
}

/** Nothing on screen and the read failed. Not a clickable row around a button: one focus stop, the button. */
@Composable
internal fun ProblemPanel(title: String, text: String, onRetry: () -> Unit) {
    val t = LocalTones.current
    Panel {
        Column(Modifier.fillMaxWidth().padding(Space.xl), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(Space.s)) {
            Icon(Icons.Filled.Warning, contentDescription = null, tint = t.bad, modifier = Modifier.size(36.dp))
            Text(title, Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
            Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
            Spacer(Modifier.height(Space.xs))
            Button(onClick = onRetry) { Text("Try again") }
        }
    }
}

/**
 * A message with one action. Only the button is clickable, so TalkBack has one stop for the text and one for the
 * action. At large font sizes the button goes under the text.
 */
@Composable
internal fun Notice(text: String, fg: Color, bg: Color, action: String, onAction: () -> Unit) {
    val stacked = LocalDensity.current.fontScale >= 1.5f
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(bg).padding(start = Space.l - 2.dp, end = Space.xs, top = Space.xs, bottom = Space.xs),
        verticalAlignment = if (stacked) Alignment.Top else Alignment.CenterVertically,
    ) {
        Icon(Icons.Filled.Info, contentDescription = null, tint = fg, modifier = Modifier.padding(top = if (stacked) Space.m else 0.dp).size(20.dp))
        Spacer(Modifier.width(Space.m))
        if (stacked) Column(Modifier.weight(1f).padding(top = Space.s)) {
            Text(text, color = fg, style = MaterialTheme.typography.bodyMedium)
            TextButton(onClick = onAction, contentPadding = PaddingValues(horizontal = 0.dp)) { Text(action, color = fg, fontWeight = FontWeight.SemiBold) }
        } else {
            Text(text, Modifier.weight(1f), color = fg, style = MaterialTheme.typography.bodyMedium)
            TextButton(onClick = onAction) { Text(action, color = fg, fontWeight = FontWeight.SemiBold) }
        }
    }
}

/** A slim problem line above data that is still shown (the button under the text at large font sizes). */
@Composable
internal fun Slim(text: String, fg: Color, bg: Color, action: String, onAction: () -> Unit) {
    val stacked = LocalDensity.current.fontScale >= 1.5f
    val shape = RoundedCornerShape(10.dp)
    if (stacked) Column(Modifier.fillMaxWidth().clip(shape).background(bg).padding(start = Space.m, end = Space.m, top = Space.s)) {
        Text(text, color = fg, style = MaterialTheme.typography.bodySmall)
        TextButton(onClick = onAction, contentPadding = PaddingValues(horizontal = 0.dp)) { Text(action, color = fg, style = MaterialTheme.typography.labelLarge) }
    } else Row(Modifier.fillMaxWidth().clip(shape).background(bg).padding(start = Space.m, end = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text, Modifier.weight(1f), color = fg, style = MaterialTheme.typography.bodySmall, maxLines = 3, overflow = TextOverflow.Ellipsis)
        TextButton(onClick = onAction) { Text(action, color = fg, style = MaterialTheme.typography.labelLarge) }
    }
}

/** Where a section would be when the role does not open it. */
@Composable
internal fun RoleNote(text: String) {
    val t = LocalTones.current
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).border(1.dp, t.line, RoundedCornerShape(12.dp)).padding(Space.m),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Filled.Lock, contentDescription = null, tint = t.muted, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(Space.m))
        Text(text, color = t.muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
internal fun Quiet(text: String) {
    Text(text, Modifier.padding(top = Space.xs), color = LocalTones.current.muted, style = MaterialTheme.typography.bodySmall)
}

@Composable
internal fun Panel(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val shape = RoundedCornerShape(16.dp)
    Box(modifier.fillMaxWidth().clip(shape).background(MaterialTheme.colorScheme.surface).border(1.dp, LocalTones.current.line, shape)) {
        Column { content() }
    }
}

internal fun Modifier.topLine(show: Boolean, color: Color): Modifier =
    if (!show) this else drawBehind { drawLine(color, Offset(0f, 0f), Offset(size.width, 0f), 1.dp.toPx()) }

/**
 * A ~1 s background flash when the value under [key] changed since the last read (data.Changes). None when the
 * system's animations are off, and none for a row that only comes into view later.
 */
@Composable
internal fun Modifier.flash(key: String): Modifier {
    val c = LocalChanged.current
    val reduce = LocalReduceMotion.current
    val color = LocalTones.current.flash
    val a = remember { Animatable(0f) }
    val hit = key in c.keys
    LaunchedEffect(c.stamp, hit) {
        if (hit && !reduce && System.currentTimeMillis() - c.at < FLASH_MS) {
            a.snapTo(1f)
            a.animateTo(0f, tween(FLASH_MS.toInt(), easing = LinearOutSlowInEasing))
        }
    }
    return drawBehind { if (a.value > 0f) drawRect(color.copy(alpha = color.alpha * a.value)) }
}

private const val FLASH_MS = 1_000L
