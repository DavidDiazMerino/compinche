#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
docker build -t compinche-android-build:local "$task_root"
mkdir -p "$task_root/.cache/sdk" "$task_root/.cache/gradle" "$task_root/artifacts"
docker run --rm --user "$(id -u):$(id -g)" \
    -e HOME=/gradle-cache \
    -v "$task_root/.cache/sdk:/android-sdk" \
    -v "$task_root/.cache/gradle:/gradle-cache" \
    -v "$task_root/CameraAccess:/work" \
    compinche-android-build:local bash -lc '
      set -euo pipefail
      if ! test -f /android-sdk/cmdline-tools/latest/bin/sdkmanager; then
        curl --fail --location --retry 3 https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip -o /android-sdk/tools.zip
        echo "4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583  /android-sdk/tools.zip" | sha256sum -c -
        mkdir -p /android-sdk/cmdline-tools
        unzip -q /android-sdk/tools.zip -d /android-sdk/cmdline-tools
        mv /android-sdk/cmdline-tools/cmdline-tools /android-sdk/cmdline-tools/latest
        rm /android-sdk/tools.zip
      fi
      /android-sdk/cmdline-tools/latest/bin/sdkmanager --sdk_root=/android-sdk "platforms;android-36" "build-tools;35.0.0"
      test -f /android-sdk/platforms/android-36/android.jar || { echo "Falta Android SDK 36; completa la instalación/licencia de SDK Manager."; exit 1; }
      extra=()
      if test "$(uname -m)" = aarch64; then extra+=(-Pandroid.aapt2FromMavenOverride=/opt/aapt2/aapt2); fi
      bash ./gradlew --no-daemon --max-workers=4 "${extra[@]}" assembleDebug assembleDebugAndroidTest lintDebug
    '
cp "$task_root/CameraAccess/app/build/outputs/apk/debug/app-debug.apk" "$task_root/artifacts/compinche-replay-debug.apk"
cp "$task_root/CameraAccess/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk" "$task_root/artifacts/compinche-replay-test.apk"
sha256sum "$task_root/artifacts/compinche-replay-debug.apk" "$task_root/artifacts/compinche-replay-test.apk"
