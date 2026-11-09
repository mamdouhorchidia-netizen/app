package com.vorath.mobile.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/** Vorath's own tokens (face/web/assets/suite.css), so the phone reads as the same product. */
data class Tones(
    val ok: Color, val okBg: Color, val warn: Color, val warnBg: Color, val bad: Color, val badBg: Color,
    val muted: Color, val line: Color, val ghost: Color,
)

private val LightTones = Tones(
    ok = Color(0xFF067647), okBg = Color(0xFFECFDF3), warn = Color(0xFFB54708), warnBg = Color(0xFFFFFAEB),
    bad = Color(0xFFC01F14), badBg = Color(0xFFFEF3F2), muted = Color(0xFF667085), line = Color(0xFFE4E7EC), ghost = Color(0xFFCDD2DA),
)
private val DarkTones = Tones(
    ok = Color(0xFF47CD89), okBg = Color(0xFF0D2E20), warn = Color(0xFFF5B950), warnBg = Color(0xFF33260C),
    bad = Color(0xFFF97066), badBg = Color(0xFF3A1714), muted = Color(0xFF8A94A3), line = Color(0xFF2B333E), ghost = Color(0xFF3A4350),
)

val LocalTones = staticCompositionLocalOf { LightTones }

private val Light = lightColorScheme(
    primary = Color(0xFF007A7D), onPrimary = Color.White, primaryContainer = Color(0xFFDCEFEE), onPrimaryContainer = Color(0xFF00605F),
    secondary = Color(0xFFE8590C), onSecondary = Color.White,
    background = Color(0xFFF3F5F8), onBackground = Color(0xFF101828),
    surface = Color.White, onSurface = Color(0xFF101828), surfaceVariant = Color(0xFFF2F4F7), onSurfaceVariant = Color(0xFF475467),
    outline = Color(0xFFE4E7EC), outlineVariant = Color(0xFFEEF0F3), error = Color(0xFFC01F14),
)
private val Dark = darkColorScheme(
    primary = Color(0xFF2CCFC4), onPrimary = Color(0xFF05080C), primaryContainer = Color(0xFF123634), onPrimaryContainer = Color(0xFF7FD1CC),
    secondary = Color(0xFFE8590C), onSecondary = Color.White,
    background = Color(0xFF05080C), onBackground = Color(0xFFEAF0F6),
    surface = Color(0xFF0B1016), onSurface = Color(0xFFEAF0F6), surfaceVariant = Color(0xFF161B22), onSurfaceVariant = Color(0xFFA8B3C2),
    outline = Color(0xFF2B333E), outlineVariant = Color(0xFF222932), error = Color(0xFFF97066),
)

@Composable
fun VorathTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    CompositionLocalProvider(LocalTones provides if (dark) DarkTones else LightTones) {
        MaterialTheme(colorScheme = if (dark) Dark else Light, content = content)
    }
}
