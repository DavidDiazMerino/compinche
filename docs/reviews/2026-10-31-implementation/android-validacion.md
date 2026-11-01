# Host Android preparado para Compinche

Se descargó el repositorio oficial de Meta y se incorporó su CameraAccess al proyecto como un host Android completo. Commit de origen: `f4371bc9f90249d51eb07d7f8a61e8d829294f16`. Ver [origen y cambios](../../../wearables/android/CameraAccess/UPSTREAM.md) y [guía de instalación](../../../wearables/android/README.md).

El APK `com.compinche.replay` carga la copia HEVC del vídeo familiar por USB reverse desde el servidor local, la configura en unas Ray-Ban Meta de MockDeviceKit y usa el preview/recorder oficial del SDK. El envío explícito guarda la captura en un aprendizaje vinculado al padre; no la analiza con Spark automáticamente. El original privado no está incluido en los APK.

La máquina es Linux ARM64. Java y Kotlin se ejecutan nativamente en un contenedor; AAPT2 oficial de Google se ejecuta mediante QEMU con bibliotecas Ubuntu x86_64. Se comprobó que el AAPT2 antiguo de Ubuntu fallaba al leer los recursos del SDK 36 y se sustituyó en la compilación por el oficial actual. No se instaló un emulador x86 esperando aceleración incompatible con esta arquitectura.

## Resultados efectivos

- `assembleDebug`: APK de la app generado, aproximadamente 95 MiB.
- `assembleDebugAndroidTest`: APK de instrumentación generado, aproximadamente 6,9 MiB, incluida la prueba de frames del vídeo familiar.
- `lintDebug`: **0 errores, 20 avisos**. Principalmente versiones del ejemplo oficial, orientación, recursos y sugerencias de estilo. No se presentan como cero avisos.
- Firma APK v2 verificada con `apksigner`; ID, versión 0.2.0 y mínimo Android 12 verificados con `aapt2 dump badging`.
- Suite del servidor y cliente: **84 pruebas Python y 11 Node pasan**. La nueva regresión verifica un único medio por reintento, conflicto si cambia el contenido/metadatos, aislamiento entre sesiones y ausencia de archivos sobrantes.
- Instalador USB ejecutado sin teléfono: informa correctamente de la ausencia de dispositivo, conserva el servidor ADB en `127.0.0.1:5037` y no instala en ningún dispositivo ajeno. Se corrigió una incompatibilidad de la versión de ADB con el formato explícito de hostname de `-L`.
- Backend de demo reiniciado con los recibos de carga nuevos; vídeo familiar y copia HEVC disponibles en el mismo puerto 8017.

Las huellas SHA256 y tamaños exactos están en [android-validacion.json](android-validacion.json). Los binarios, las cachés de SDK/Gradle, la clave debug y los resultados de dispositivo quedan fuera de Git.

## Revisión adicional de Opus y Grok

[Opus 5.5](parte-android-opus.txt) y [Grok 4.7](parte-android-grok.txt) revisaron el código nativo sin ejecutarlo. Sus observaciones permitieron reforzar:

1. **Envíos interrumpidos o dobles:** operación en un ViewModel de Activity con bloqueo inmediato; clave estable por contenido y recibo idempotente en el servidor. La misma clave con otros bytes o metadatos responde 409.
2. **Sesión guardada que ya no existe:** se comprueba antes de reutilizarla y se crea otra si el servidor devuelve 404. La suposición de Opus de que la sesión familiar era determinista no es cierta en esta preparación; el control también cubre eliminaciones o restauraciones reales.
3. **Mocks y medios antiguos:** se desemparejan los dispositivos propios al desactivar y al iniciar un ViewModel nuevo. Cada descarga tiene archivo independiente; se elimina después de desemparejar o si falla la preparación. Se impide emparejar/desactivar en paralelo con la carga familiar.
4. **Orden:** se unificó powerOn → unfold → don → setCameraFeed.

Dos objeciones de Grok no se reproducen: la URL devuelta sí contiene la barra antes del fragmento (`BASE/#live/…`) y pasa la validación existente; `renameTo` sobre el mismo filesystem Linux puede sustituir el destino. Aun así, los archivos independientes evitan reemplazar un feed en uso.

## Pendiente de ejecutar al conectar el teléfono

David indicó que lo conectará después. No se ha ejecutado la app ni la instrumentación en Android y no se afirma haber comprobado decodificación, permisos o frames nativos. El instalador es `bash wearables/android/install-usb.sh`; la prueba automática posterior, `bash wearables/android/test-usb.sh`. Esta última exige que el HEVC real produzca un frame no vacío del SDK en 45 segundos y no consulta Spark ni guarda afirmaciones sobre la receta.
