package com.vorath.mobile.data

/** Something worth a notification: an alarm that is firing, or a "needs you" line marked bad. */
data class Signal(val id: String, val title: String, val text: String, val href: String)

object Signals {
    /** What the deck says needs the person right now. A need that only repeats a firing alarm is left out. */
    fun of(deck: Deck): List<Signal> {
        val firing = deck.alarms.filter { it.firing && it.on }
        val alarms = firing.map { Signal("alarm:${it.key}", it.title, it.text, "/autopilot") }
        val needs = deck.needs
            .filter { it.level == "bad" && firing.none { a -> it.text.startsWith(a.title) } }
            // The texts carry dates and counts that move with every refresh: the same need keeps the same id.
            .map { Signal("need:" + it.text.filterNot(Char::isDigit).hashCode(), "Needs you", it.text, it.href.ifBlank { "/" }) }
        return alarms + needs
    }

    /** The signals not told yet. [told] is what the last check saw; it is replaced by this check's ids, so a signal that clears and comes back is told again. */
    fun fresh(now: List<Signal>, told: Set<String>): List<Signal> = now.filter { it.id !in told }
}
