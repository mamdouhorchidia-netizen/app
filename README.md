# Vorath for Android

The phone app for [Vorath](https://github.com/mamdouhorchidia-netizen/vorath). It signs in to a Vorath server
(`python -m fleet`) and shows what the web Today page and the Autopilot show, sized for a phone.

- **Sign in** with the server address, user name, password and 2-step code. The phone keeps the session
  (encrypted with an Android Keystore key), never the password.
- **Today**: the Autopilot's one answer, what needs you, the four numbers against the previous period, cash in per
  day, every alarm, and the Autopilot's plan for the day.
- **Notifications**: about once an hour the phone reads the Autopilot and tells you when an alarm starts firing.
- **The full Vorath** opens inside the app, already signed in.

The app only reads (`/api/me`, `/api/state`, `/api/autopilot/deck`) and never computes money: every figure is the
server's. Vorath itself is not changed by this project.

## Build

Needs JDK 17+ and the Android SDK (platform 35).

```
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest      # parsing and alarm rules, from recorded answers
```

Two more tests are skipped unless asked for:

```
# the client against a running Vorath (sign-in with 2-step, read, sign-out)
./gradlew :app:testDebugUnitTest -Dvorath.url=http://127.0.0.1:8790 -Dvorath.login=/path/login.json
# pictures of the screens, drawn without a phone
./gradlew :app:testDebugUnitTest -Dvorath.shots=/path/folder
```

## Layout

```
app/src/main/java/com/vorath/mobile/
  data/   VorathApi (the server's routes), Models (parsing), Signals (what is worth a notification), SessionStore
  work/   AlarmWorker (hourly check), Notifier
  ui/     SignInScreen, TodayScreen, WebScreen, Theme (Vorath's own colours)
```

## A Vorath to develop against

`tools/vorath_devserver.py` starts a Vorath with sign-in on 127.0.0.1:8790 over Vorath's own test data, with an owner
whose 2-step code is already set up, and writes that sign-in to `$VORATH_HOME/config/dev-login.json` for the live test:

```
pip install -r ../vorath/requirements.txt
VORATH_HOME=/tmp/vhome VORATH_SECRET_KEY=dev-only python tools/vorath_devserver.py ../vorath
./gradlew :app:testDebugUnitTest -Dvorath.url=http://127.0.0.1:8790 -Dvorath.login=/tmp/vhome/config/dev-login.json
```

## Not done yet

- Never run on a real phone or emulator: notifications, the encrypted session and the in-app pages are unproven.
- The APK in `release/` is a debug build signed with a throwaway key; a permanent signing key is needed for updates.
- Alarms are checked hourly; instant push needs a change in Vorath. English only. File downloads inside the in-app pages are not handled.
