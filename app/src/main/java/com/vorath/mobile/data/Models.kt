package com.vorath.mobile.data

import org.json.JSONArray
import org.json.JSONObject

/** The signed-in person, from GET /api/me. */
data class Me(
    val user: String,
    val name: String,
    val roleLabel: String,
    val client: String,
    val setup: Boolean,
    val mustChange: Boolean,
)

/** One of the four numbers on Vorath's Today page. Every figure is the server's; the app only formats it. */
data class Figure(
    val key: String,
    val label: String,
    val egp: Double,
    val count: Int?,
    val sub: String,
    val estimated: Boolean,
    val previous: Double?,
) {
    /** Whole-percent change against the previous period, the way the web page shows it; null when there is nothing to compare. */
    val changePct: Int?
        get() = previous?.takeIf { it != 0.0 }?.let { Math.round(100 * (egp - it) / Math.abs(it)).toInt() }
}

/** GET /api/state: the four numbers and the cash line for the window. */
data class Money(
    val today: String,
    val days: Int,
    val from: String,
    val to: String,
    val four: List<Figure>,
    val cash: List<Double>,
    val cashPrevious: List<Double>,
    val missingInputs: Int,
)

/** Something that needs the person, from the Autopilot deck. level: bad | warn | other. */
data class Need(val level: String, val text: String, val href: String, val label: String)

data class Alarm(val key: String, val title: String, val text: String, val firing: Boolean, val on: Boolean, val level: String)

data class Planned(val at: String, val label: String, val done: Boolean)

/** GET /api/autopilot/deck. state: trouble | attention | flying | off | stopped. */
data class Deck(
    val state: String,
    val word: String,
    val needs: List<Need>,
    val alarms: List<Alarm>,
    val today: List<Planned>,
)

object Parse {
    fun me(o: JSONObject) = Me(
        user = o.optString("user"),
        name = o.optString("name").ifBlank { o.optString("user") },
        roleLabel = o.optString("role_label"),
        client = o.optString("client"),
        setup = o.optBoolean("setup"),
        mustChange = o.optBoolean("must_change"),
    )

    fun money(o: JSONObject): Money {
        val prev = o.optJSONObject("previous")
        val window = o.optJSONArray("window")
        val series = o.optJSONObject("series")
        return Money(
            today = o.optString("today"),
            days = o.optInt("days"),
            from = window?.optString(0).orEmpty(),
            to = window?.optString(1).orEmpty(),
            four = objects(o.optJSONArray("four")).map { f ->
                val key = f.optString("key")
                Figure(
                    key = key,
                    label = f.optString("label"),
                    egp = f.optDouble("egp", 0.0),
                    count = if (f.isNull("count")) null else f.optInt("count"),
                    sub = f.optString("sub"),
                    estimated = f.optBoolean("estimated"),
                    previous = if (prev == null || prev.isNull(key)) null else prev.optDouble(key),
                )
            },
            cash = numbers(series?.optJSONArray("cash")),
            cashPrevious = numbers(series?.optJSONArray("cash_previous")),
            missingInputs = o.optJSONArray("missing_inputs")?.length() ?: 0,
        )
    }

    fun deck(o: JSONObject) = Deck(
        state = o.optString("state"),
        word = o.optString("word"),
        needs = objects(o.optJSONArray("needs")).map {
            Need(it.optString("level"), it.optString("text"), it.optString("href"), it.optString("label"))
        },
        alarms = objects(o.optJSONArray("alarms")).map {
            Alarm(
                key = it.optString("key"),
                title = it.optString("title"),
                text = it.optString("text"),
                firing = it.optBoolean("firing"),
                on = it.optBoolean("on", true),
                level = it.optString("level"),
            )
        },
        today = objects(o.optJSONArray("today")).map {
            Planned(it.optString("at"), it.optString("label"), it.optBoolean("done"))
        },
    )

    private fun objects(a: JSONArray?): List<JSONObject> =
        if (a == null) emptyList() else (0 until a.length()).mapNotNull { a.optJSONObject(it) }

    private fun numbers(a: JSONArray?): List<Double> =
        if (a == null) emptyList() else (0 until a.length()).map { a.optDouble(it, 0.0) }
}
