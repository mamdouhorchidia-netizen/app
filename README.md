# Vorath for Android

The phone app for [Vorath](https://github.com/mamdouhorchidia-netizen/vorath). It signs in to a Vorath server
(`python -m fleet`) and shows what the web Today page and the Autopilot show, sized for a phone.

- **Sign in** with the server address, user name, password and 2-step code. The phone keeps the session
  (encrypted with an Android Keystore key), never the password. Password managers offer the user name and password,
  and the keyboard offers the 2-step code (Compose's autofill content types); Show / Hide reveals the password; Next
  moves down the form and Done on the code signs in. The address is remembered once a Vorath has answered at it, also
  after a wrong password. A problem with one field is said under it (no address, plain `http://`, a code that is not 6
  digits); the server's answer (wrong password, no Vorath at the address, can't reach it, too many tries) in a box
  above the button. While signing in the button says "Signing in…" and nothing can be sent twice. A sign-in that works
  is one request (`/api/auth/login`, then `/api/me`); `/healthz` is asked only when the sign-in fails, to tell "no
  Vorath here" from a wrong password.
- **Today**: the client's name with how current the page is beneath it; the Autopilot's one answer with what needs you
  inside the same card (each a full-width row with a severity icon and its action); the alarms that are firing, with
  every other alarm one tap away under "All alarms (N)"; the four numbers against the previous period; cash in per
  day; and the Autopilot's plan for the day as a timeline. From 600 dp wide two columns (needs and alarms | numbers,
  cash, plan), never wider than 840 dp. The first read with nothing kept shows grey shapes of the page; a section
  your role does not open shows a quiet note in its place.
- **What changed**: a figure, alarm or need that differs from the previous read flashes for about a second (not when
  the phone's animations are off); an alarm that just started firing is told to TalkBack, politely.
- **Readable at any size**: up to Android's 200 % font size nothing is cut off (figures shrink, then wrap; the tiles
  go one per row), TalkBack reads each figure as one item ("Cash in, EGP 350,778, down 28 percent versus previous
  period, …") and the chart as its period and first and last day, and every touch target is at least 48 dp.
- **Opens on the last answers**: the phone keeps the last answers from Vorath (encrypted with the same Keystore key,
  in a file that is never backed up, for that server and person only, deleted on sign-out or when the session ends),
  so the Today page shows them at once and then updates.
- **Says how current it is**: a line under the title reads "Updated 12 s ago", "Updating…" or
  "Can't reach Vorath · showing 14:05" ("Vorath did not answer · …" when it answered, but with an error). When a
  read fails the page keeps what it last read and shows a slim line with the reason; the numbers and the Autopilot
  are read separately, so one failing does not blank the other.
- **Stays current while open**: while the Today page is on screen the phone reads again quietly every 30 seconds
  (no spinner; the spinner is for pull-to-refresh and the first read). A slow server is read at most once per ten of
  its answer times; failures back off to 60 s, 120 s, then every 5 minutes, and retry as soon as the network comes
  back. These reads stop when the app leaves the screen or the full Vorath page opens (a read already under way
  finishes); only the 15-minute alarm check below runs in the background.
- **Light on the server**: one connection is kept open and reused; if Vorath sends ETag or Last-Modified, an
  unchanged answer comes back as an empty 304 (Vorath does not need to: it works the same without them).
- **Notifications**: asked for on the Today page, in a small card after the alarms that says what they are for (not
  straight after sign-in); the phone remembers that it asked, and after that the card or the menu opens the settings.
  About every 15 minutes (Android's shortest background period; it may wait longer when the phone
  sleeps) the phone reads the Autopilot and tells you when an alarm starts firing. Two channels: **Alarms** (high:
  heads-up and sound; the id `alarms` it always had, so existing settings stay) and **Account** (default: "Sign in to
  Vorath again"). Each notification shows its time; several alarms at once are grouped under one "3 alarms started"
  summary that alone makes the sound and opens the Today page. A single alarm opens its page.
- **The full Vorath** opens inside the app, already signed in: the session cookie is stored before the page is opened;
  a bar shows each page's load progress; a page that does not open shows why with Retry; Back goes back through the
  pages before closing. If the server sends the page to its sign-in page (`/login`) or answers 401, the session is
  over: the app signs out and clears its cache, as on a 401 on the Today page. Files the page offers are saved by
  Android's download manager into Downloads (with the session; named from the server's Content-Disposition, else the
  address). Links to other sites, mail and phone open in the phone's apps; only this Vorath gets the cookie.

The app only reads (`/api/me`, `/api/state`, `/api/autopilot/deck`) and never computes money: every figure is the
server's. Vorath itself is not changed by this project.

## Install on a phone

Open <https://github.com/mamdouhorchidia-netizen/app/releases/latest> on the phone, tap **vorath.apk**, then Open.
CI builds, tests on emulators, signs and publishes it: see [docs/RELEASING.md](docs/RELEASING.md), which also covers
the release key.

## Build

Needs JDK 17+ and the Android SDK (platform 35).

```
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest      # parsing, alarm rules, cache, freshness, changes, the client's 304 / keep-alive,
                                      # sign-in (health only on failure, messages), the form checks, link and download
                                      # rules of the in-app page, and the notification channels and grouping
```

On an emulator or a phone (adb), the tests in `app/src/androidTest`: the real Keystore (session and cache sealed,
key not exportable), Android's notification manager (channels, grouping, "sign in again"), the background check
run by WorkManager against a Vorath stand-in on 127.0.0.1 and its 15-minute schedule, and the app's screens
(sign-in form, Today read from the stand-in, an ended session back to sign-in). Debug builds only may use plain
http to 127.0.0.1, for that stand-in (`src/debug`):

```
./gradlew :app:connectedDebugAndroidTest
tools/emulator-check.sh <release.apk> <out folder>   # those tests, then the release APK installed and opened
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
  data/   VorathApi (the server's routes), Models (parsing), Signals (what is worth a notification), SessionStore,
          TodayCache (the last answers on the phone), Keystore (the encryption), Freshness (sections, the 30 s pacing),
          Changes (what differs from the previous read, by key), WebLinks (inside / sign-in / outside links, file names)
  AppModel  the session, the cache and the quiet foreground reads; SignInCheck the form's own checks
  work/   AlarmWorker (15-minute check), Notifier
  ui/     SignInScreen, TodayScreen (layout, top bar, states), TodayParts (the cards), WebScreen,
          Theme (Vorath's own colours, the 4/8/12/16/24 spacing and the type scale with tabular figures)
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

- Run on emulators in CI (API 26 and 34), not yet on a real phone. Proven there: the signed, minified release APK
  installs like a downloaded file and opens on the sign-in screen without a crash; the Keystore; the notifications
  and their channels; the background check and its schedule; the sign-in form's own checks; the Today page reading
  a server; an ended session returning to sign-in. Still unproven until a real phone: the sign-in against a real
  Vorath over https (with autofill and the keyboard), the 30-second loop over real networks, the 15-minute check
  in Doze, the in-app Vorath page (cookie, downloads, redirects), touch and scrolling, TalkBack, and the flash with
  the phone's animations off.
- The screens have been drawn (`ScreensTest` and `ChangedScreenTest`, with Robolectric's native graphics, not on a
  phone): sign-in, Today in light and dark (full length and phone height), a small phone, the first read as grey
  shapes (phone and tablet), a quiet update, offline with the last answers (light and dark), one section failing,
  offline with nothing to show, a role refusal, all alarms open, the notifications card, 200 % font size (two widths),
  an 840 dp tablet and a wide screen in two columns, a frame of the changed-value flash, the sign-in form filled (password
  hidden and shown), with a field error, refused and signing in, and the in-app page's progress bar and error with Retry
  (the web view itself cannot be drawn without a phone). They are pictures of the layout only; fonts, system bars, the
  pull gesture and real-device colours can still differ.
- Release builds are minified with R8 and resource shrinking (about 1.3 MB against 7.4 MB without); the library keep
  rules cover what is made by class name, but the minified build has not been run on a device either. Keep
  `app/build/outputs/mapping/release/mapping.txt` from each release to read its crash reports.
- Updates install over each other only once the release key is in the repository's secrets (docs/RELEASING.md);
  until then each build is signed with its own throwaway key. The old debug APK in `release/` is gone.
- Alarms are checked every 15 minutes at best; instant push needs a change in Vorath. English only.
- In the in-app page: the sign-in page is recognised by its path `/login` (where the app already sends people to set
  up 2-step codes); files a page makes in itself (`blob:` / `data:`) cannot be saved from the app and say so; before
  Android 10 downloads go to the app's own Downloads folder (the public one needs a storage permission). The page is
  not kept alive between openings (it would have to outlive the screen that owns it and be torn down on sign-out and
  rotation); it reloads each time it opens. Autofill, the downloads, the redirect handling and the error page are
  unproven until run on a phone.
