# Getting the app onto a phone

## Where the APK comes from

GitHub Actions (`.github/workflows/android.yml`) builds every pull request and every push to the default branch:

1. **Unit tests and signed APK**: the unit tests, then a minified release APK signed with the release key, checked
   with `apksigner verify`. The APK, its SHA-256 and R8's `mapping.txt` are kept as the run's `vorath-apk` artifact.
2. **On an emulator** (Android 8.0 / API 26, the oldest the app supports, and Android 14 / API 34): the on-device tests
   in `app/src/androidTest` against the debug build, then that same signed release APK installed the way a phone
   installs a downloaded file (`pm install`, no test flags), opened, and checked to draw the sign-in screen without a
   crash. Screenshots, the screen's layout dump and logcat are kept as `emulator-api-NN`.
3. **Publish** (pushes to the default branch, or *Run workflow* by hand): a GitHub Release named
   "Vorath for Android 0.1.0 (build N)" with `vorath.apk` attached, marked latest.

## Installing on the phone

On the phone, open (in a browser signed in to GitHub, while the repository is private)

    https://github.com/mamdouhorchidia-netizen/app/releases/latest

tap **vorath.apk**, then **Open**. The first time, Android asks to allow installs from that browser
("Install unknown apps"); allow it and go back. Play Protect may say it does not know the developer; choose
**Install anyway**.

What does not work, and was likely why the earlier APK did not install from a link:
- A run's **artifact** is always a `.zip`, even with one APK in it; Android cannot install a zip.
- A link to `release/…apk` on github.com opens a web page about the file, not the file.

## The release key

Android installs an update only when it is signed with the same key as the app already on the phone. CI reads the
key from four repository secrets (Settings → Secrets and variables → Actions → New repository secret):

| Secret | Value |
| --- | --- |
| `VORATH_KEYSTORE_B64` | the keystore file, base64 (`base64 -w0 vorath-release.jks`) |
| `VORATH_KEYSTORE_PASSWORD` | the keystore's password |
| `VORATH_KEY_ALIAS` | the key's alias (`vorath`) |
| `VORATH_KEY_PASSWORD` | the key's password (the same as the keystore's when made as below) |

Without them CI still builds, tests and publishes, but signs with a key made for that run alone and says so in a
warning and in the release notes: each such APK needs the previous one uninstalled first.

To make a key (once; keep the file and its password safe and backed up, because a lost key means the app can never be
updated, only uninstalled and installed again):

    keytool -genkeypair -keystore vorath-release.jks -storetype PKCS12 -alias vorath \
      -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Vorath"

Locally, the same four values as environment variables (`VORATH_KEYSTORE` is the file's path) sign
`./gradlew :app:assembleRelease`; without them the release APK is left unsigned.

Each CI build's `versionCode` is its run number, so a newer build always installs over an older one.
