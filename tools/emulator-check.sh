#!/usr/bin/env bash
# Runs on a booted emulator (CI's emulator job, or any device adb sees). Two parts:
#  1. the on-device tests (app/src/androidTest) against the debug build;
#  2. the signed release APK installed the way a phone installs a downloaded file (no test flags), opened, and checked:
#     the sign-in screen is drawn and the app did not crash.
# Usage: tools/emulator-check.sh <release.apk> <folder for screenshots and logs>
set -euo pipefail
apk="$1"
out="$2"
pkg=com.vorath.mobile
mkdir -p "$out"

adb wait-for-device
adb shell settings put global package_verifier_enable 0 || true

echo "== on-device tests (debug build)"
./gradlew --no-daemon :app:connectedDebugAndroidTest

echo "== the release APK, as a phone installs it"
adb uninstall "$pkg" >/dev/null 2>&1 || true          # the debug build has another key; a phone would have neither
adb push "$apk" /data/local/tmp/vorath.apk
adb shell pm install /data/local/tmp/vorath.apk        # no -t (test-only) and no -r: a first install from a file
adb shell dumpsys package "$pkg" | grep -E "versionCode|versionName|pkgFlags" | tee "$out/package.txt"
if grep -q TEST_ONLY "$out/package.txt"; then echo "The APK is marked test-only: a phone would refuse it."; exit 1; fi

adb logcat -c
adb shell am start -W -n "$pkg/.MainActivity" | tee "$out/start.txt"
grep -q "Status: ok" "$out/start.txt"

found=""
for i in $(seq 1 30); do
  adb shell uiautomator dump /data/local/tmp/ui.xml >/dev/null 2>&1 || true
  adb shell cat /data/local/tmp/ui.xml > "$out/ui.xml" 2>/dev/null || true
  if grep -q "Vorath address" "$out/ui.xml"; then found=yes; break; fi
  sleep 2
done
adb exec-out screencap -p > "$out/release-sign-in.png" || true
adb logcat -d > "$out/logcat.txt" || true

if [ -z "$(adb shell pidof "$pkg" | tr -d '\r')" ]; then echo "The app is not running after opening."; exit 1; fi
if grep -E "FATAL EXCEPTION|AndroidRuntime: Process: $pkg" "$out/logcat.txt"; then echo "The app crashed."; exit 1; fi
if [ -z "$found" ]; then echo "The sign-in screen did not appear."; exit 1; fi
echo "The release APK installed, opened and drew the sign-in screen."
