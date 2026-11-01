# Cinco partes de Compinche y revisión con otros modelos

La demo local usa el vídeo real seleccionado por David y el agente `muse-spark-1.3`. El original dura 16:41 y conserva su sonido. La preparación generó nueve ventanas de trabajo, descripciones visuales reales y dos turnos del agente. Las siete propuestas de conocimiento permanecen sin confirmar; algunas se solapan entre ambos turnos. No se han inventado respuestas del padre, ingredientes confirmados ni un nombre de plato.

## Resultado por parte

| Parte | Resultado | Debates reales por CLI |
| --- | --- | --- |
| 1. Contexto del aprendiz | Ventanas equilibradas entre profesor y aprendiz, correcciones recientes y evidencias adjuntadas protegidas. | [Opus](parte-1-opus.txt), [Grok](parte-1-grok.txt) |
| 2. Validación y recuperación | Reglas sobre observaciones y cantidades, intención persistente, recibos idempotentes y reintento tras fallo ambiguo. | [Opus](parte-2-opus.txt), [Grok](parte-2-grok.txt) |
| 3. Fuentes y corrección | Vídeo o texto original desde cada propuesta, edición junto a la evidencia y conservación del texto del modelo cuando cambia. | [Opus](parte-3-opus.txt), [Grok](parte-3-grok.txt) |
| 4. Demo real y dos dispositivos | Receta con el original, momentos navegables, teléfono y replay de Ray-Ban sin pantalla; adaptador oficial MockDeviceKit. | [Opus](parte-4-opus.txt), [Grok](parte-4-grok.txt) |
| 5. Búsqueda y fragmentos | Búsqueda sobre datos reales y notas personales, previsualización local de intervalos y envío explícito del fragmento elegido. | [Opus](parte-5-opus.txt), [Grok](parte-5-grok.txt) |

Se consultó `claude-opus-5-5` y el alias CLI `grok-4.7` (el catálogo responde `grok-4.7-build`) después de cada parte. Son diez dictámenes efectivos, con sus metadatos JSON contiguos. Revisaron fragmentos de implementación sin herramientas de ejecución; sus opiniones no sustituyen las pruebas. Un primer intento de Grok en la parte 5 terminó sin dictamen y está etiquetado como incompleto. Las decisiones, correcciones y objeciones descartadas están en [resoluciones.txt](resoluciones.txt).

## Demo y alcance

Ejecutar `npm run demo` desde `application` y abrir [la receta real](http://127.0.0.1:8017/#recipe/d2a6f029-037d-4a2a-ac07-850d56e82bc6). Los medios privados están en `backend/.data/family-demo`, ignorados por Git. Otro equipo necesita aportar el vídeo y preparar esos datos siguiendo el README.

En teléfono se puede ver el original, abrir momentos, crear una sesión de aprendizaje y añadir el intento propio. En Ray-Ban el teléfono controla el replay: reproduce, pausa y permite seleccionar un fragmento de lo ya reproducido. Solo las explicaciones revisadas pueden leerse con la voz del navegador; no se ha clonado la voz del padre.

El audio se procesó aparte con `muse-voice-transcribe-1.0`, porque Spark no garantiza transcripción fiable del sonido de vídeo. Los nueve fragmentos devolvieron cero turnos de habla. Esto significa que el sistema no consiguió transcribir palabras; no demuestra que la grabación sea silenciosa. El sonido original sigue accesible y el conocimiento queda pendiente de revisión humana.

El [adaptador Android](../../../wearables/android/README.md) usa DAT 1.0.1, `GlassesModel.RAYBAN_META` y `setCameraFeed(Uri)` de MockDeviceKit. Compila contra los AAR oficiales. Requiere integrarse y ejecutarse en un host Android, por ejemplo CameraAccess; este repositorio no contiene una aplicación Android completa. La reproducción web funciona por sí sola. No se ha ejecutado Android, Bluetooth ni unas gafas físicas aquí.

## Verificación

- `npm run check`: todos los módulos JavaScript pasan la comprobación de sintaxis.
- `npm test`: **83 pruebas de backend y 11 de Node pasan**.
- `python3 wearables/android/check_sdk.py`: compilación contra dependencias oficiales fijadas, incluida limpieza al cancelar.
- [Smoke HTTP](http-smoke.json): configuración Spark, búsqueda real, caché de recortes y tiempos anidados. Un recorte desde 320 segundos y otro desde el segundo 12 del primero conserva el origen 332. Hash del original sin cambios.
- WebM real sin cabecera Duration: generado por pipe, recortado desde 0,5 durante 1 segundo; MP4 de 1 segundo válido y hash del WebM intacto. Sin volcado de paquetes.
- Navegador móvil: búsqueda «fogon» encuentra «fogón», abre su fuente y vídeo; el editor conserva el borrador al consultar la fuente, prueba cancelada sin confirmar contenido real. Replay reproduce y pausa el original de 1001,27 segundos. Un intervalo de cinco segundos muestra su previsualización y origen 01:00; cambiar duración deshabilita el envío hasta preparar de nuevo. Sin desbordamiento horizontal en la vista comprobada.

Límites restantes: contexto finito, filtros lingüísticos heurísticos, contenido visual pendiente de revisión y ejecución nativa Android pendiente. El servidor se mantiene en loopback; no se ha publicado el vídeo familiar.
