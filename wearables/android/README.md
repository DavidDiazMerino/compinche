# Compinche Replay para Android

Proyecto Android completo basado en el [CameraAccess oficial de Meta](https://github.com/meta/meta-wearables-dat-android/tree/main/samples/CameraAccess), con **Meta Wearables DAT / MockDeviceKit 1.0.1**. Su origen y cambios están en [UPSTREAM.md](CameraAccess/UPSTREAM.md). No necesita gafas físicas para reproducir el vídeo como entrada del SDK.

## Probar en el teléfono

Necesitas Android 12 o superior, depuración USB y un cable de datos. En este ordenador las herramientas y el APK ya están preparados. Desde `application`:

```bash
npm run demo
```

En otra terminal, con el teléfono conectado:

```bash
bash wearables/android/install-usb.sh
```

Acepta la clave RSA de depuración en el teléfono si aparece y vuelve a ejecutar el comando. El instalador comprueba la versión de Android, instala `artifacts/compinche-replay-debug.apk`, configura `adb reverse tcp:8017 tcp:8017` y abre **Compinche Replay**. Si tienes varios dispositivos, añade el número de serie como argumento. El puente queda en el contenedor `compinche-android-usb`, con ADB limitado a localhost; no se publica el servidor de Compinche en la red.

1. Acepta el permiso de dispositivos cercanos que necesita el servicio de streaming de Android.
2. Abre el icono de pruebas, en el lateral derecho.
3. Pulsa **Cargar el vídeo de papá**. La app descarga del servidor local la copia HEVC de 60 segundos, activa MockDeviceKit, empareja unas Ray-Ban Meta, simula encendido/desplegado/puestas y configura el vídeo como cámara.
4. Cierra el panel y pulsa **Start Session**, después **Preview**. La previsualización y los estados provienen del SDK real.
5. Abre **Compinche · aprender en directo**, indica quién cocina y quién habla por el micrófono, y selecciona su papel. Pulsa **Iniciar aprendizaje** y acepta el permiso de micrófono. Esta acción autoriza el análisis continuo de imágenes y voz de esta sesión.
6. Mantén la app en primer plano. Compinche observa imágenes reales del SDK, escucha las explicaciones y decide cuándo preguntar por detalles que faltan. La voz usa PCM continuo cuando el servidor tiene el proveedor conectado, con envío por turnos como respaldo. Las preguntas se leen por el teléfono; puedes **Interrumpir**, elegir **Ahora no**, **Pausar** o seguir explicando. La transcripción se guarda con su procedencia y hablante declarado; no confirma conocimiento por sí sola.
7. Pulsa **Terminar** para detener el aprendizaje y finalizar la grabación continua del SDK que inició este flujo. El original se conserva primero en el teléfono y se envía a la misma sesión que las imágenes y explicaciones; si falla la transferencia, usa **Reintentar copias locales**. La grabación original es vídeo sin micrófono y las intervenciones se guardan como evidencias de audio separadas.

Mantén el cable conectado y el servidor activo. El botón **Abrir Compinche** da acceso a la demo web. La voz del micrófono se atribuye al nombre y papel seleccionados: no se identifica automáticamente a las personas. Durante la voz del agente y los 650 ms posteriores se excluye el micrófono para evitar eco; la interrupción es explícita. No se ha validado interrupción acústica automática.

El aprendizaje funciona en primer plano: al abandonar la pantalla, perder el stream o pasar la Activity a segundo plano se detienen el micrófono y el análisis y se finaliza el original que inició el flujo. Si ya había una grabación manual, este flujo no la finaliza automáticamente. El vídeo del padre y las claves API se descargan o mantienen fuera del APK; no vienen incluidos.

### Captura manual secundaria

Para guardar una captura sin iniciar aprendizaje continuo, usa **Record**, espera unos segundos y detén la grabación; después pulsa **Enviar a Compinche** en su previsualización. El audio de micrófono de esa grabación está desactivado inicialmente. Se crea o recupera una sesión de aprendizaje propia vinculada a la receta del padre, con captura `meta_dat_mock` y procedencia declarada por el cliente. **Abrir mi aprendizaje** abre la sesión en el navegador; selecciona Ray-Ban Meta → **Select the latest SDK capture** para analizarla.

Guardar o reintentar esa captura manual no inicia por sí solo llamadas al modelo; el análisis se solicita después desde Compinche. El aprendizaje en directo sí inicia el análisis continuo al pulsar **Iniciar aprendizaje**. Los reintentos de una misma captura recuperan un único recibo del servidor, incluso tras un timeout ambiguo. El SDK usa una imagen mock independiente para Photo: verifica el vídeo elegido con Preview o Record.

Para cerrar el puente USB:

```bash
docker stop compinche-android-usb
```

## Prueba automática en Android

Después de instalar y con `npm run demo` activo:

```bash
bash wearables/android/test-usb.sh
```

Instala el APK de instrumentación y ejecuta `FamilyReplaySmokeTest`: descarga la copia HEVC real, verifica su pista de vídeo, la pasa a MockDeviceKit y exige recibir un frame no vacío del SDK en 45 segundos. No envía evidencias ni consulta Spark. El resultado queda en `artifacts/family-replay-device-test.txt`. Esta prueba requiere teléfono o runtime Android; generar el APK de pruebas no significa que se haya ejecutado.

## Volver a compilar

```bash
bash wearables/android/build-container.sh
```

Compila ambos APK y ejecuta Android Lint en un contenedor con JDK 21, SDK 36, Kotlin y Gradle fijados por el proyecto oficial. La primera preparación del SDK puede solicitar su licencia. Las descargas y claves de desarrollo permanecen en `.cache`, ignorado por Git; los APK y resultados, en `artifacts`, también ignorado.

Este ordenador es Linux ARM64. Las herramientas actuales de empaquetado de Google son x86_64: el contenedor ejecuta su AAPT2 con QEMU y bibliotecas oficiales de Ubuntu x86_64, manteniendo Java/Kotlin nativos. El AAPT2 antiguo de Ubuntu no entiende los recursos del SDK 36. No se ha instalado ni asumido un emulador x86 acelerado sobre ARM.

Para Android Studio en otro ordenador, abre [CameraAccess](CameraAccess), usa JDK 17 o superior y SDK 36. El ID de aplicación es `com.compinche.replay`. La app está preparada para MockDeviceKit de desarrollo, con aplicación/token `0`, y tiene desactivadas las analíticas y los informes de fallos del SDK. No es una configuración de distribución para gafas físicas.

## Adaptador reutilizable

[CompincheReplay.kt](CameraAccess/app/src/main/java/com/compinche/wearables/CompincheReplay.kt) mantiene la integración aislada. El enlace `wearables/android/CompincheReplay.kt` apunta al mismo archivo. Usa `GlassesModel.RAYBAN_META`, `setCameraFeed(Uri)`, espera `DeviceSessionState.STARTED` y copia los buffers reales del SDK. La limpieza usa `NonCancellable`. También puede comprobarse su compilación de tipos con `python3 wearables/android/check_sdk.py`.

La compilación y Lint se comprueban en este ordenador. La ejecución nativa, decodificación en el teléfono, permisos y prueba de frames se validarán al conectar el Android. No se ha certificado comportamiento con gafas físicas.
