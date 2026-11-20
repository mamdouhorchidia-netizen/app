package com.vorath.mobile.data

/**
 * What changed between two reads of the Today page, by stable key, so the screen can flash the rows that moved and
 * tell TalkBack about alarms that just started firing. Nothing is computed from the money: values are only compared.
 */
object Changes {
    const val STATUS = "status"

    fun figureKey(f: Figure) = "figure:${f.key}"
    fun alarmKey(a: Alarm) = "alarm:${a.key}"

    /**
     * Keys of the figures, alarms, needs (data.RowKeys) and the status that differ from the previous read. A section
     * with no previous value (the first read, or after a refusal) reports nothing: there is nothing to compare with.
     */
    fun between(oldMoney: Money?, newMoney: Money?, oldDeck: Deck?, newDeck: Deck?): Set<String> {
        val out = HashSet<String>()
        if (oldMoney != null && newMoney != null && oldMoney !== newMoney) {
            val before = oldMoney.four.associateBy { it.key }
            newMoney.four.forEach { f -> if (before[f.key] != f) out += figureKey(f) }
        }
        if (oldDeck != null && newDeck != null && oldDeck !== newDeck) {
            if (oldDeck.state != newDeck.state || oldDeck.word != newDeck.word) out += STATUS
            val before = oldDeck.alarms.associateBy { it.key }
            newDeck.alarms.forEach { a ->
                val b = before[a.key]
                if (b == null || b.firing != a.firing || b.on != a.on || b.title != a.title || b.text != a.text) out += alarmKey(a)
            }
            val oldNeeds = RowKeys.needs(oldDeck.needs).zip(oldDeck.needs).toMap()
            RowKeys.needs(newDeck.needs).zip(newDeck.needs).forEach { (k, n) -> if (oldNeeds[k] != n) out += k }
        }
        return out
    }

    /** Alarms firing now that were not firing in the previous read. None on the first read. */
    fun newlyFiring(old: Deck?, new: Deck?): List<Alarm> {
        if (old == null || new == null || old === new) return emptyList()
        val was = old.alarms.filter { it.firing && it.on }.map { it.key }.toSet()
        return new.alarms.filter { it.firing && it.on && it.key !in was }
    }
}
