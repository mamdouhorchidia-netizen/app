package com.vorath.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.autofill.ContentType
import androidx.compose.ui.platform.LocalAutofillManager
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.contentType
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.vorath.mobile.SignInField
import com.vorath.mobile.SignInProblem

/** What is typed into the sign-in form. The password and the code live only here (and in the saved screen state). */
data class SignInFields(
    val address: String = "",
    val user: String = "",
    val password: String = "",
    val code: String = "",
    val showPassword: Boolean = false,
)

private val FieldsSaver = listSaver<SignInFields, Any>(
    save = { listOf(it.address, it.user, it.password, it.code, it.showPassword) },
    restore = { SignInFields(it[0] as String, it[1] as String, it[2] as String, it[3] as String, it[4] as Boolean) },
)

/** The 2-step code is offered by the phone's autofill as a one-time code (SMS or an authenticator app). */
private val OneTimeCode = ContentType.SmsOtpCode + ContentType("2faAppOTPCode")

@Composable
fun SignInScreen(
    lastAddress: String,
    lastUser: String,
    busy: Boolean,
    problem: SignInProblem?,
    onSignIn: (address: String, user: String, password: String, code: String) -> Unit,
) {
    var fields by rememberSaveable(stateSaver = FieldsSaver) {
        mutableStateOf(SignInFields(address = lastAddress.removePrefix("https://"), user = lastUser))
    }
    SignInForm(fields, busy, problem, onChange = { fields = it }, onSubmit = { onSignIn(fields.address, fields.user, fields.password, fields.code) })
}

/**
 * The sign-in form without its state, so each state can be drawn (ScreensTest). Every field says to the phone's
 * autofill what it holds (password managers offer the user name and password, the keyboard offers the 2-step code);
 * Next moves down the form and Done on the code signs in. While signing in the fields and the button are disabled,
 * so a second tap cannot send it twice.
 */
@Composable
fun SignInForm(
    fields: SignInFields,
    busy: Boolean,
    problem: SignInProblem?,
    onChange: (SignInFields) -> Unit,
    onSubmit: () -> Unit,
) {
    val focus = LocalFocusManager.current
    val autofill = LocalAutofillManager.current
    // A problem is shown until the person changes what it is about (or signs in again).
    var edited by remember(problem) { mutableStateOf(false) }
    val shown = problem.takeUnless { edited }
    val change = { f: SignInFields ->
        if (f.address != fields.address || f.user != fields.user || f.password != fields.password || f.code != fields.code) edited = true
        onChange(f)
    }
    val go = {
        if (!busy) {
            focus.clearFocus()
            autofill?.commit()                                     // lets a password manager offer to save the sign-in
            onSubmit()
        }
    }
    fun error(field: SignInField) = shown?.field == field
    fun errorText(field: SignInField): (@Composable () -> Unit)? = if (error(field)) ({ Text(shown!!.text) }) else null

    Box(Modifier.fillMaxSize().systemBarsPadding().imePadding(), contentAlignment = Alignment.Center) {
        Column(
            Modifier.widthIn(max = 420.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(Space.xl),
            verticalArrangement = Arrangement.spacedBy(Space.m),
        ) {
            Text("Vorath", style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
            Text("Sign in to your Vorath to see today's numbers and what needs you.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(Space.xs))
            OutlinedTextField(
                value = fields.address, onValueChange = { change(fields.copy(address = it.trim())) }, label = { Text("Vorath address") },
                placeholder = { Text("vorath.yourbrand.com") }, singleLine = true, enabled = !busy, modifier = Modifier.fillMaxWidth(),
                isError = error(SignInField.ADDRESS), supportingText = errorText(SignInField.ADDRESS),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next, autoCorrectEnabled = false,
                    capitalization = KeyboardCapitalization.None,
                ),
            )
            OutlinedTextField(
                value = fields.user, onValueChange = { change(fields.copy(user = it)) }, label = { Text("User name") }, singleLine = true,
                enabled = !busy, isError = error(SignInField.USER), supportingText = errorText(SignInField.USER),
                modifier = Modifier.fillMaxWidth().semantics { contentType = ContentType.Username },
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Email, imeAction = ImeAction.Next, autoCorrectEnabled = false,
                    capitalization = KeyboardCapitalization.None,
                ),
            )
            OutlinedTextField(
                value = fields.password, onValueChange = { change(fields.copy(password = it)) }, label = { Text("Password") }, singleLine = true,
                enabled = !busy, isError = error(SignInField.PASSWORD), supportingText = errorText(SignInField.PASSWORD),
                visualTransformation = if (fields.showPassword) VisualTransformation.None else PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth().semantics { contentType = ContentType.Password },
                keyboardOptions = KeyboardOptions(
                    keyboardType = if (fields.showPassword) KeyboardType.Text else KeyboardType.Password,
                    imeAction = ImeAction.Next, autoCorrectEnabled = false, capitalization = KeyboardCapitalization.None,
                ),
                trailingIcon = {
                    TextButton(
                        onClick = { onChange(fields.copy(showPassword = !fields.showPassword)) }, enabled = !busy,
                        modifier = Modifier.heightIn(min = Space.touch).semantics {
                            contentDescription = if (fields.showPassword) "Hide password" else "Show password"
                            stateDescription = if (fields.showPassword) "Password shown" else "Password hidden"
                        },
                    ) { Text(if (fields.showPassword) "Hide" else "Show") }
                },
            )
            OutlinedTextField(
                value = fields.code, onValueChange = { change(fields.copy(code = it.filter(Char::isDigit).take(6))) },
                label = { Text("2-step code") },
                supportingText = errorText(SignInField.CODE)
                    ?: { Text("The 6 digits from your authenticator app. Leave empty if you have no 2-step code.") },
                isError = error(SignInField.CODE), singleLine = true, enabled = !busy,
                modifier = Modifier.fillMaxWidth().semantics { contentType = OneTimeCode },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { go() }),
            )
            if (shown != null && shown.field == null) ProblemBox(shown.text)
            Button(
                onClick = go, enabled = !busy, modifier = Modifier.fillMaxWidth().height(52.dp),
                // Still in Vorath's colour while signing in (it is busy, not unavailable), slightly faded.
                colors = ButtonDefaults.buttonColors(
                    disabledContainerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.7f),
                    disabledContentColor = MaterialTheme.colorScheme.onPrimary,
                ),
            ) {
                if (busy) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = LocalContentColor.current)
                    Spacer(Modifier.size(Space.s))
                    Text("Signing in…")
                } else {
                    Text("Sign in")
                }
            }
            Text(
                "The phone keeps your session, never your password.",
                style = MaterialTheme.typography.bodySmall, color = LocalTones.current.muted,
            )
        }
    }
}

/** A problem with the sign-in as a whole (wrong password, no Vorath at the address, offline), read out by TalkBack. */
@Composable
private fun ProblemBox(text: String) {
    val tones = LocalTones.current
    Row(
        Modifier.fillMaxWidth().background(tones.badBg, RoundedCornerShape(Space.s)).padding(Space.m)
            .semantics { liveRegion = LiveRegionMode.Polite },
        horizontalArrangement = Arrangement.spacedBy(Space.s),
    ) {
        Icon(Icons.Filled.Warning, contentDescription = null, tint = tones.bad, modifier = Modifier.size(20.dp))
        Text(text, color = tones.bad, style = MaterialTheme.typography.bodyMedium)
    }
}
