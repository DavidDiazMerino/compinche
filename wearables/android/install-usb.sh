#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
container=compinche-android-usb
apk="$task_root/artifacts/compinche-replay-debug.apk"
if ! test -f "$apk"; then
  printf '%s\n' 'Primero ejecuta bash wearables/android/build-container.sh'
  exit 1
fi
if ! docker image inspect compinche-android-build:local >/dev/null 2>&1; then
  printf '%s\n' 'Falta la imagen de compilación. Ejecuta build-container.sh.'
  exit 1
fi
if docker container inspect "$container" >/dev/null 2>&1; then
  owner="$(docker inspect --format '{{index .Config.Labels "com.compinche.android-usb"}}' "$container")"
  test "$owner" = true || { printf '%s\n' 'Ese nombre de contenedor pertenece a otro proceso.'; exit 1; }
  docker rm -f "$container" >/dev/null
fi
mkdir -p "$task_root/.cache/adb"
chmod 700 "$task_root/.cache/adb"
docker run -d --name "$container" --label com.compinche.android-usb=true \
  --network host --device /dev/bus/usb \
  -v "$task_root/.cache/adb:/root/.android" \
  -v "$task_root/artifacts:/artifacts:ro" \
  compinche-android-build:local adb -P 5037 server nodaemon >/dev/null
python3 - <<'CHECK_ADB'
import socket, time
for attempt in range(50):
    try:
        with socket.create_connection(("127.0.0.1", 5037), timeout=0.1):
            break
    except OSError:
        time.sleep(0.1)
else:
    raise SystemExit("El puente ADB no arrancó. Revisa docker logs compinche-android-usb.")
CHECK_ADB
test "$(docker inspect --format '{{.State.Running}}' "$container")" = true || { docker logs "$container"; exit 1; }
android_adb() { docker exec "$container" adb "$@"; }
device_args=()
if test "$#" -gt 0; then device_args=(-s "$1"); fi
android_adb devices -l
state="$(android_adb "${device_args[@]}" get-state 2>/dev/null || true)"
if test "$state" != device; then
  printf '%s\n' 'Conecta un Android 12+, activa depuración USB y acepta la clave RSA en el teléfono.' \
    'Después vuelve a ejecutar este comando. Si hay varios dispositivos, añade su número de serie.'
  exit 2
fi
sdk="$(android_adb "${device_args[@]}" shell getprop ro.build.version.sdk | tr -d '\r')"
if ! [[ "$sdk" =~ ^[0-9]+$ ]] || test "$sdk" -lt 31; then
  printf '%s\n' 'Esta app necesita Android 12 (API 31) o superior.'
  exit 1
fi
android_adb "${device_args[@]}" install -r /artifacts/compinche-replay-debug.apk
android_adb "${device_args[@]}" reverse tcp:8017 tcp:8017
android_adb "${device_args[@]}" shell am start -n \
  com.compinche.replay/com.meta.wearable.dat.externalsampleapps.cameraaccess.MainActivity
printf '%s\n' 'Compinche Replay instalada. Mantén el USB conectado y npm run demo activo.' \
  'Abre el icono de pruebas → Cargar el vídeo de papá → cierra el panel → Start Session → Preview.' \
  'Graba unos segundos, detén la grabación y pulsa Enviar a Compinche.' \
  'El puente ADB queda en compinche-android-usb, escuchando solo en localhost.'
