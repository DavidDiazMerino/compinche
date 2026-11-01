#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
container=compinche-android-usb
test_apk="$task_root/artifacts/compinche-replay-test.apk"
test -f "$test_apk" || { printf '%s\n' 'Ejecuta build-container.sh para generar el APK de pruebas.'; exit 1; }
device_args=()
if test "$#" -gt 0; then device_args=(-s "$1"); fi
android_adb() { docker exec "$container" adb "${device_args[@]}" "$@"; }
android_adb get-state
android_adb reverse tcp:8017 tcp:8017
android_adb install -r /artifacts/compinche-replay-test.apk
result="$task_root/artifacts/family-replay-device-test.txt"
android_adb shell am instrument -w -r \
    -e class com.compinche.wearables.FamilyReplaySmokeTest \
    com.compinche.replay.test/androidx.test.runner.AndroidJUnitRunner | tee "$result"
if ! rg -q 'OK \(1 test\)' "$result"; then
    printf '%s\n' 'La prueba no terminó correctamente; revisa el resultado anterior.'
    exit 1
fi
