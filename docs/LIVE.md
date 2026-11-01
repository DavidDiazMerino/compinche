# Compinche: conversación durante la cocina

## Prueba de uso y criterios del evento · 1 de noviembre de 2026

La prueba que importa es abrir la aplicación, dejar pasar una entrada causal, oír una pregunta justificada, responder con una persona y recuperar una explicación utilizable por otra persona. El vídeo audiovisual de presentación no acredita ese recorrido completo.

La inspección del evento y de sus [reglas oficiales](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver) confirma esta ponderación: impacto 30 %, uso de herramientas Meta 25 %, profundidad y ejecución técnica 20 % y demo 25 %. Compinche encaja en «Build for Your World»: conservar un saber práctico familiar. Spark, Muse Voice Transcribe y SAM tienen funciones reales distintas; los revisores Opus/Grok y la narración de Drama 3 son ayudas de desarrollo/presentación. Las reglas consultadas no exigen que todas las herramientas sean Meta. La página pública señala entrega el 4 de noviembre a las 21:00 ET (5 de noviembre a las 03:00 en Madrid); también mostraba «Event Closed», así que no se infiere disponibilidad del portal a partir del calendario. El formulario de entrega redirigió a login: sus campos y límites no se han verificado ni se ha enviado nada.

La alineación técnica no acredita requisitos personales de participación ni derechos sobre materiales. Antes de entregar contenido familiar se debe contar con autorización para su difusión; los archivos de esta prueba siguen locales. Los aspectos aún no acreditados son el aprendizaje conversacional humano completo, la transferencia a otro cocinero, recetas independientes y la ejecución Android en un dispositivo.

### Qué significa reducir latencia

La [documentación de modelos Meta](https://dev.meta.ai/docs/models) sitúa Spark en cargas sensibles a latencia y Wonder en tareas complejas más largas. Se mantiene Spark 1.3 `minimal` en el camino visual. [Streaming](https://dev.meta.ai/docs/protocols/responses) permite recibir salida incremental, pero [no elimina el razonamiento inicial](https://dev.meta.ai/docs/reasoning). No se debe pronunciar JSON parcial sin validar ni atribuir a streaming una mejora que no se ha medido.

Orden de experimentos propuesto, sin afirmar que esté ya implementado:

1. Corregir esperas de interfaz y medir captura, admisión, cola, visión, elegibilidad de pregunta, síntesis e inicio de reproducción por separado. Los tiempos del vídeo no son reloj de servidor. La espera para no interrumpir también debe quedar visible.
2. Comparar muestreo por cambio de gesto con la cadencia actual, conservando una secuencia corta y ordenada. Cola acotada a lo más reciente; no intentar procesar cada fotograma atrasado. Evaluar simultáneamente si se pierden gestos.
3. Mantener ASR continuo y voz local cargada. Mantener SAM fuera del camino de la pregunta; separar carga fría, inferencia y generación de overlay. Pedir segmentación solo cuando aporte localización útil.
4. Probar como candidato un detector/observador local con [Muse Glimmer](https://dev.meta.ai/docs/muse-glimmer), modelo multimodal denso de 30B, con y sin [decodificación especulativa](https://dev.meta.ai/docs/muse-glimmer/spec-decode). No se ha descargado ni medido aquí; compartir memoria con SAM puede empeorar la latencia. Spark puede resolver las dudas que requieran mayor comprensión.
5. Usar un sistema de conversación audiovisual nativo, como [Gemini Live](https://ai.google.dev/gemini-api/docs/live-api), como referencia experimental fuera del recorrido principal del hackathon. Su API admite interacción bidireccional y audio nativo. Es un candidato para comparación de turnos y latencia, no una prueba de que aprenderá mejor una receta ni un cambio aplicado a Compinche.

Objetivo de producto por validar: que la pregunta llegue en una pausa natural antes de perder el gesto. Medir p50/p95 sobre suficientes oportunidades anotadas, no solo sobre las preguntas exitosas; registrar también omisiones, duplicados, errores, interrupciones y coste. No presentar los rangos de unas pocas llamadas como p95 de producción.

### Evaluación independiente

Congelar versión, prompts y política antes de una receta nueva. Reservar recetas/cocineros completos, no trozos del mismo vídeo. Hacer primero tres pruebas con gestos y criterios diferentes (masa, tortilla, guiso), después una receta que no haya servido para ajustes. Anotar cuándo el criterio se vuelve desconocido, cuándo existe una pausa para preguntar y qué explicación permite reproducirlo. Contrastar la guía con otra persona cocinando, no solo con la opinión de otro modelo.

Los clips de stock sirven para comprobar que otra imagen genera preguntas distintas y que la carga funciona; no equivalen a una receta completa reservada ni demuestran generalización pedagógica. Las respuestas de prueba y las negativas no se atribuyen al cocinero original.

### Resultado ejecutado y recurso disponible

Se probó la aplicación real en navegador: primero el replay familiar anterior con pausa, después 84 segundos de replay continuo y después una carga nueva del clip de masa `assets/stock/1669.mp4`. Cada prueba abrió una sesión aislada. El replay continuo recibió 20 ventanas: 19 observaciones aplicadas y un resultado descartado por cambio de generación; cero fallos del proveedor. Sus preguntas fueron «¿A qué fuego dejo la olla?» y «¿Cómo sé cuánto echar a la olla?». La respuesta escrita de prueba «No lo sé.» quedó como duda sin resolver, sin crear fichas ni reglas. No se aportó una respuesta humana por micrófono.

| Medida | Replay continuo familiar | Clip independiente de masa |
| --- | --- | --- |
| Admisión → pregunta elegible | 4,052 y 4,018 s | 3,823 s |
| Pregunta elegible → ACK de inicio de reproducción | 227 y 203 ms | 260 ms |
| Admisión → ACK de inicio de reproducción | 4,279 y 4,221 s | 4,083 s |
| Preparación del WAV | 149 y 114 ms | Ver diagnóstico |
| Pregunta | Fuego / criterio de cuánto verter | Criterio para dejar de espolvorear harina |

El total anterior excluye el tiempo hasta seleccionar/admitir la ventana, y el ACK es una señal del cliente, no una medición física del altavoz. La captura visual se muestrea cada segundo y envía al haber cambio con un intervalo mínimo de cuatro segundos; una escena sin cambio usa un intervalo de hasta doce segundos. Esa política puede perder oportunidades aunque la inferencia sea rápida. No se atribuye la diferencia entre pruebas al arreglo del temporizador: las llamadas y preguntas no son idénticas.

La sesión continua `dd697a9f-4b18-4214-a9df-7ad11742e708` deja un recurso HTML/JSON con el original, las preguntas con intervalo y la negativa escrita; cero fichas confirmadas. Las explicaciones reales posteriores pueden producir fichas pendientes que una persona revisa. El HTML enlaza imágenes/audio/vídeo en el servidor local; no empaqueta esos medios para uso sin servidor. Los diagnósticos completos, configuración y resultados están en [la carpeta de ensayo](experiments/2026-11-01-live-trial/trial-summary.json).

El JSON guarda eventos completos, observaciones, preguntas, motivos declarados, transcripciones, IDs/tiempos de evidencia, estados de herramientas, errores, modelo, esfuerzo y hashes de prompts. No expone claves ni rutas privadas; tampoco contiene el razonamiento interno del modelo. Algunas fases todavía son `not_measured`: captura física → audio audible y fin de voz → transcripción final del WebSocket.

Se corrigieron el reintento del margen de silencio, la selección de otro vídeo, la visualización de respuestas escritas en el historial y la distinción entre replay continuo/pausa de ayuda. Pasaron 141 pruebas del backend y 22 de interfaz; tras añadir el registro de preferencias del replay, su suite de ocho pruebas de exportación volvió a pasar. La prueba de reloj falso comprueba que el replay continuo avanza al iniciar voz y que la pausa solo ocurre si se elige.

Quedan límites detectados, sin presentar como resueltos: una respuesta fuera de tema puede cerrar una pregunta aunque su ficha siga sin confirmar; falta comprobar suficiencia del criterio, los turnos que alcanzan el límite de audio, el desfase horario de Android y la caducidad semántica de SAM dentro de la misma escena. [Opus y Grok revisaron realmente](reviews/2026-11-01-real-trial/) estos puntos. Sus sugerencias de solapamiento de palabras no se adoptaron como garantía de comprensión. Sus recibos incluyen una corrección de etiquetas de latencia y de los resultados SAM más recientes.

La ruta principal es `http://127.0.0.1:8017/#teach` (`npm run demo`). No requiere preparar análisis ni pulsar «observar» después de grabar. El vídeo familiar original sirve como entrada temporal para probar el mismo coordinador; no se heredan sus análisis anteriores ni sus explicaciones futuras.

## Cinco partes conectadas

1. **Captura.** Cámara del teléfono, replay causal o preview real del SDK Android. Se conserva el orden de las imágenes y el instante de fuente. El navegador conserva el vídeo continuo hasta 140 MiB, permite descargarlo y lo guarda localmente en la sesión. Android conserva el archivo producido por el SDK. Las ventanas de movimiento del teléfono son vídeos independientes completos, no fragmentos arbitrarios de MediaRecorder.
2. **Visión y comprensión.** `muse-spark-1.3` recibe hasta dos JPEG ordenados, en línea, mediante una conexión HTTP reutilizada. Una observación en curso y una pendiente sustituible; no se acumulan todas las imágenes atrasadas. El razonamiento visual usa `minimal`; la extracción posterior de decisiones usa `low`.
3. **Escucha y voz.** PCM16 mono24k continuo → WebSocket local → Meta `muse-voice-transcribe-1.0` en ENDPOINTING. Las transcripciones parciales solo se muestran. El texto final y su audio real pueden crear aprendizaje. Si no se abre el canal, se usa WAV por intervención, sin reenviar como WAV una intervención parcialmente transmitida por WS. Piper local mantiene la voz española cargada; navegador/Android tienen voz alternativa.
4. **Decisión y segmentación.** Una duda relevante puede pedir explicar el criterio, mostrar mejor o repetir un gesto. Solo se pronuncia una pregunta cuando la persona y su ASR pendiente han terminado; el acuse de reproducción real convierte `pending` en `spoken`. SAM3.1 es una herramienta automática separada, con una cola GPU y pesos residentes. El clip real se limita a los últimos dos segundos (ocho frames) para contener memoria y latencia; una ventana más larga alcanzó el umbral de memoria compartida y fue detenida sin bloquear la conversación. Sus máscaras localizan píxeles y no establecen textura, cantidades ni punto de cocción. Puede devolver cero detecciones.
5. **Aprendizaje y revisión.** La explicación literal conserva hablante declarado, pregunta, audio, imágenes, intervalo y epoch. La ficha comienza sin confirmar; los campos derivados son tentativos y nunca reescriben la cita. Confirmar es una acción humana separada. El modo aprendiz del coordinador recibe únicamente las reglas confirmadas de la sesión docente.

## Probarlo

En `#teach`, elige el vídeo de papá, otro vídeo local (hasta 150 MiB) o la cámara. Declara quién cocina y quién responderá: David mirando un vídeo no se identifica automáticamente como el padre del vídeo. Activa el micrófono si vas a responder y acepta el análisis de imagen/voz de esa sesión. Compinche empieza a observar y a escuchar automáticamente. Puedes interrumpir la voz, decir «ahora no», posponer o responder por escrito. El replay continúa durante las preguntas por defecto. «Pausar el vídeo mientras respondo» permite una pausa de ayuda explícita. Abre el diagnóstico plegable y descarga el recurso o el JSON completo; «Abrir otra prueba» crea una pestaña nueva.

El audio original del replay se envía por un canal separado. «Escuchar el vídeo original» permite oírlo: mientras suena, el micrófono humano envía silencio; al preguntar se silencia el original y se abre el turno de respuesta después de la voz del agente. Esto separa turnos y no afirma cancelación acústica perfecta ni detección de auriculares. Su origen `recorded_source` nunca responde una pregunta en nombre de la persona. El original con sonido sigue disponible en su reproductor habitual.

Durante TTS y una cola de650ms se envía silencio por el canal del micrófono. No se afirma tener interrupción acústica automática: se usa el botón de interrupción. El inicio VAD, `utterance_id`, `qid`, hablante y epoch se congelan antes del envío; los marcadores `audioProcessedMs` de Meta son aproximados y no se presentan como timestamps exactos de palabras. Sin onset VAD válido, WS puede guardar contexto pero no atribuir una respuesta a una pregunta.

Pausar, retroceder o terminar invalida los resultados anteriores de visión, SAM y ASR. Las preguntas tardías llevan una referencia retrospectiva explícita también en voz, conservando el texto originalmente propuesto y los tiempos de evidencia. Un resultado en curso no se invalida simplemente porque llegue otro frame. Los errores de herramienta aparecen en el historial sin fabricar una explicación.

La vía Android está en [CameraAccess/UPSTREAM.md](../wearables/android/CameraAccess/UPSTREAM.md). El APK `wearables/android/artifacts/compinche-replay-debug.apk` usa el SDK y MockDeviceKit, permite probar con el HEVC preparado sin gafas físicas y requiere Android12+. Compila aplicación, APK de pruebas y Lint: cero errores, 24 avisos. Se detiene al salir de primer plano. Queda ejecutar las pruebas instrumentadas con un teléfono conectado; compilar no acredita permisos, audio ni captura en hardware.

La verificación automatizada actual pasa 134 pruebas de backend y 20 de interfaz; los dobles de proveedor se limitan a pruebas.

## Medidas reales de esta sesión

Son muestras de desarrollo, no percentiles ni una garantía de tiempo máximo. En el replay integrado posterior, la primera pregunta se preparó en4,951s con4s de desfase y acuse real de reproducción144ms después; otras observaciones completaron en1,3–3,6s. SAM también completó una invocación automática durante ese replay, sin intervención manual: 21 de23frames tuvieron detecciones candidatas. Esa prueba precede al límite final de dos segundos; se conserva su recibo sin presentarla como prueba del recorte posterior.

| Etapa | Resultado observado |
| --- | --- |
| Spark inline en replay real, esfuerzo low inicial | 3,3–9s por observación; una pregunta lista a10,8s desde admisión de su ventana, con9s de desfase de fuente. |
| Dos imágenes, mismo contexto aislado | low6,088s / minimal3,425s; no basta una muestra para atribuir toda la diferencia al parámetro. |
| Una imagen, contexto equivalente | low5,503s / minimal5,108s; se retienen dos imágenes porque una sola produjo una pregunta más genérica y pierde el gesto. |
| Voz Piper CPU caliente | 89,9–109ms de síntesis; carga inicial1,063s. Inicio de reproducción237ms después de pregunta lista en una prueba CUA. |
| ASR WS real | Frase de Piper transcrita correctamente; final recibido a5,014s desde inicio del ensayo, incluido audio/silencios. Es una prueba sintética, no una respuesta familiar. |
| SAM residente, ventana real2s/8frames | Última repetición: primer trabajo23,250s total, incluidos12,955s de carga de pesos; segundo4,345s, sin recarga. Ambos devolvieron cero detecciones en ese intervalo. |

La prueba CUA mostró y pronunció una pregunta real de Spark sobre el gesto: «¿A qué fuego hay que dejarlo para este paso?». No se ha inventado una respuesta del padre ni se ha confirmado ninguna ficha de esa prueba. Falta la prueba humana completa micrófono → explicación literal → ficha → confirmación → guía, y la ejecución física Android. Las pruebas automatizadas sí ejercitan esos estados con proveedores controlados.

Recibos reproducibles: [replay inicial](experiments/live-family-smoke.json), [replay integrado](experiments/live-family-final-smoke.json), [ASR sintético](experiments/live-asr-smoke.json), [visión low](experiments/live-vision-benchmark.json), [visión minimal](experiments/live-vision-minimal-benchmark.json), [SAM frío/caliente](experiments/live-sam-benchmark.json). Los originales privados, pesos y secretos siguen en almacenamiento ignorado.

## Revisión externa y cambios resultantes

Se consultaron realmente los CLI `claude-opus-5-5` y `grok-4.7`, con herramientas desactivadas. Dictámenes: [diseño Opus](reviews/2026-10-31-implementation/parte-live-design-opus.txt), [diseño Grok](reviews/2026-10-31-implementation/parte-live-design-grok.txt), [implementación Opus](reviews/2026-10-31-implementation/parte-live-final-opus.txt), [implementación Grok](reviews/2026-10-31-implementation/parte-live-final-grok.txt).

Se aplicaron la vinculación por onset/qid/epoch, cuarentena y control explícito de voz, bloqueo durante ASR pendiente, separación del audio grabado, SAM residente y vía visual acotada. Se midió `minimal` antes de activarlo. Se mantiene su objeción sobre la validación: observar/preguntar/hablar con vídeo real y transcribir audio sintético no demuestra todavía aprendizaje conversacional con una persona. No se adoptó un descarte rígido a4s que, con muestras de visión de5–9s, impediría preguntar; las preguntas conservan su evidencia y el desfase visible, con límites de edad y contexto.

## Instalación y API

El entorno principal añade Pillow y websockets. Para síntesis local opcional:

```sh
backend/.venv/bin/python -m pip install -r backend/requirements-voice.txt
```

Configura `COMPINCHE_PIPER_MODEL` con un ONNX de Piper y su `.json` contiguo, o usa la voz del dispositivo. Este ordenador tiene el modelo español local preparado; no se incluye el archivo de pesos en el repositorio. La selección `minimal` sigue la [documentación oficial de razonamiento de Meta](https://dev.meta.ai/docs/reasoning); el transporte de [imágenes](https://dev.meta.ai/docs/image-understanding) y [ASR continuo](https://dev.meta.ai/docs/speech-to-text) también se contrastó con documentación oficial.

- `POST /api/live`: crea una sesión con consentimiento y procedencia declarada.
- `POST /api/live/{id}/observe`: admite una ventana JPEG acotada y devuelve202 sin esperar a Spark.
- `GET /api/live/{id}` y `/events`: instantánea y SSE con fallback de consulta.
- `GET /api/live/{id}/diagnostics?download=true`: registro completo, configuración y métricas con sus definiciones.
- `GET /api/live/{id}/learning-resource?format=html|json&download=true`: original, fuentes, dudas y fichas separadas por revisión.
- `WS /api/live/{id}/audio-stream`: PCM24k y metadatos de reloj/VAD; la clave de Meta permanece en el servidor.
- `POST /api/live/{id}/audio`: fallback WAV mono de hasta35s y2MB.
- `POST /api/live/{id}/answer`: respuesta escrita explícita.
- `POST /api/live/{id}/control`: pausa, fin, reproducción real, interrupción, posposición y confirmación.
- `GET /api/live/{id}/questions/{qid}/voice`: WAV de una pregunta vigente; obtenerlo no equivale a haberlo pronunciado.

El servidor es local. Para probar desde el teléfono por USB se mantiene el reverse de ADB descrito en el instalador, sin publicar el backend en Internet. Las rutas manuales de conversación rechazan modificaciones concurrentes de una sesión continua activa.
