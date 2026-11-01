# Compinche como aprendiz durante la cocina

Compinche debe acompañar a una persona mientras cocina, observar su actividad, escuchar lo que explica, detectar decisiones que siguen sin explicación y preguntarle por ellas. El resultado es conocimiento que otra persona puede recuperar mientras intenta cocinarlo. La captura y la conversación suceden juntas; la biblioteca y el vídeo resumen son salidas de ese proceso.

El prototipo actual aporta piezas útiles, pero todavía no cierra ese ciclo. El vídeo de 1:49 muestra esas piezas y la revisión posterior de una grabación. No demuestra que el agente converse con quien está cocinando. El guion audiovisual aportado por David describe la experiencia objetivo; sus intervenciones propuestas no son respuestas realmente pronunciadas por su padre.

## Dónde se perdió la visión

El punto más explícito está en [Qué posponer del panel anterior](../2026-10-31/panel-expertos.md#qué-posponer): se pospuso el bucle de vídeo continuo y se dejó la voz para después. Codex adoptó esa priorización. Se trató como accesorio el mecanismo que hace que Compinche sea un aprendiz activo.

| Decisión tomada | Comportamiento actual | Consecuencia para la visión |
| --- | --- | --- |
| Capturar primero y analizar después | [capture.js](../../../dist/capture.js) recibe chunks cada segundo, los acumula y forma el vídeo al terminar. | El agente no participa durante la captura. No es correcto decir que MediaRecorder solo entrega al terminar: lo que falta es consumir lo que entrega mientras graba. |
| Observación por botón y archivo adjunto | [observe](../../../dist/app.js) pide preparar y enviar un intervalo; [provider.py](../../../backend/provider.py) procesa los medios adjuntados a ese turno. | La persona debe decidir cuándo mirar, qué mirar y cuándo preguntar. |
| SAM como trabajo separado | [sam_service.py](../../../backend/sam_service.py) arranca un contenedor por job y [sam_worker.py](../../../backend/sam_worker.py) carga el predictor. El agente recibe resúmenes de jobs terminados. | Hay integración de resultados con el contexto, pero falta la coordinación con la captura y las decisiones del diálogo. |
| Conversación principalmente escrita | [agent.py](../../../backend/agent.py) guarda preguntas y recibe respuestas con answer_to. | Faltan escucha continua, turnos de habla y preguntas audibles automáticas. Una pregunta pendiente puede impedir que surja otra aunque la cocina cambie de fase. |
| Replay y host Android como prueba de experiencia | La web reproduce un archivo. Android tiene preview, frames y grabador del SDK, pero no conecta esos frames con un coordinador de aprendizaje. | Reproducir la cámara y compilar un APK no demuestran percepción y conversación durante la cocción. |
| Pruebas centradas en módulos | Pasan 84 pruebas de backend y 11 de cliente. | Cubren piezas y regresiones; no prueban una pregunta útil, una respuesta real y su transferencia al aprendiz. |

El agente y su prompt conservan parte de la intención original: preguntar por decisiones, distinguir observación y explicación, y aprender de una fuente. Lo que falta es convertir esa intención en un comportamiento de la aplicación durante la captura.

## Lo que debe suceder en una sesión

1. **Observar y escuchar.** Gafas o teléfono alimentan un buffer de vídeo y audio con el mismo reloj. Quien lleva las gafas puede ser David grabando a su padre; portador, cocinero y voz del agente son roles distintos.
2. **Localizar y seguir.** La percepción sigue manos, utensilios y preparación, conserva los movimientos relevantes y combina imagen con la explicación que ya se ha oído. SAM debe aportar a ese mismo flujo, con sus tiempos y evidencias, sin detener toda la captura mientras trabaja.
3. **Detectar lo que falta aprender.** El agente distingue una acción visible de su criterio de decisión. Identificar una sartén no explica por qué se baja el fuego. Una frase como «hasta que esté así» puede requerir otra demostración o una explicación sensorial.
4. **Intervenir de forma útil.** Compinche puede preguntar por el motivo, pedir otra vista o pedir que se repita un movimiento. Escoge un momento apropiado, cede cuando habla la persona y permite «ahora no». Continúa observando aunque haya una cuestión aplazada.
5. **Conservar la explicación.** Vincula la respuesta y la demostración a la pregunta, al hablante correcto y al intervalo original. La ficha une acción, señal, sensación descrita y decisión. Los campos desconocidos siguen abiertos; la interpretación del agente no rellena una respuesta que nadie dio.
6. **Ayudar al aprendiz.** En otra sesión recupera esa ficha y el movimiento original cuando resulten pertinentes. La nueva cocina también se observa; la comparación debe señalar diferencias efectivamente observadas y separar las explicaciones de ambas personas.

Las limitaciones perceptivas son oportunidades para obtener información de la persona: pedir una vista cenital, una repetición más lenta o qué nota al remover. La reconstrucción 3D y las estimaciones siguen en la visión, con referencias de escala y validación de sus errores. Una máscara en píxeles no proporciona por sí sola centímetros, gramos, temperatura o sensaciones.

## Arquitectura que falta

Un coordinador de sesión debe conectar las piezas existentes:

```text
Entrada de cámara y audio con reloj común
                    ↓
Buffer temporal y archivo original
                    ↓
Observaciones visuales + palabras oídas + segmentación
                    ↓
Decisiones todavía sin explicación
                    ↓
Política de intervención y comprobación de contexto actual
                    ↓
Pregunta audible o petición de demostración
                    ↓
Respuesta hablada o mostrada por la persona
                    ↓
Ficha de decisión con fuentes → recuperación del aprendiz
```

La captura puede ser continua aunque el modelo analice selectivamente ventanas relevantes. No hace falta invocar al modelo por cada frame. Tampoco basta con sustituir el movimiento por fotografías aisladas: hacen falta secuencias ordenadas o clips para aprender un gesto, y conservar el vídeo original.

La vía rápida puede usar imágenes seleccionadas y audio separado. Para los gestos se mantienen ventanas temporales válidas; los chunks sucesivos de MediaRecorder no se deben tratar automáticamente como vídeos independientes decodificables. El mismo contrato de entrada debe servir para replay, cámara del teléfono y DAT, identificando claramente cuál se está utilizando.

La documentación de [vídeo de Meta](https://dev.meta.ai/docs/video-understanding) describe subir un archivo y referenciarlo en una petición de inferencia. El adaptador actual sigue ese recorrido. La captura de frames del [SDK de Meta](https://github.com/meta/meta-wearables-dat-android) es otra pieza; disponer de ambos componentes no constituye por sí mismo el coordinador anterior.

Las preguntas necesitan estados y acciones explícitas: candidata, pronunciada, esperando respuesta, respondida, aplazada, caducada o cancelada. Una intervención obsoleta se descarta o se reformula con su referencia temporal. El silencio, el paso del tiempo o «ahora no» no la convierten en respondida. Una contestación tampoco confirma automáticamente la interpretación que el agente extraiga de ella.

La escucha debe tratar la salida de voz de Compinche como una fuente conocida. VAD no identifica por sí solo al hablante. Hay que evitar que el eco del agente se use como respuesta y permitir que una persona lo interrumpa. La confirmación o corrección oral explícita forma parte del objetivo para manos ocupadas.

## Primera entrega que demostraría el núcleo

La primera prueba debe atravesar el ciclo completo, reutilizando LangGraph, almacenamiento, fuentes, intervalos e idempotencia. Son nuevos el coordinador, la entrada continua, la escucha y el habla, la política de intervención y la ficha estructurada de decisión. El replay es un banco de pruebas del mismo recorrido que utilizará la cámara, no un producto alternativo.

**Prueba con el vídeo del padre:** una sesión nueva recibe únicamente los frames y el audio que ya han transcurrido. Compinche detecta una decisión sin explicar, formula una pregunta audible sin que el operador pulse analizar y recoge una respuesta humana aportada por voz en ese momento. Guarda la explicación con su autor y evidencia, y una sesión de aprendiz consigue recuperarla.

Si quien contesta es David mirando el vídeo, la explicación se atribuye a David. Eso prueba una parte de la conversación, no demuestra que su padre haya respondido. La interacción con la persona que cocina debe comprobarse después con ella presente y una entrada de cámara, y la ruta de las gafas con el host Android ejecutado.

La prueba causal no puede reutilizar como contexto la sesión familiar ya analizada: sus nueve descripciones, notas y propuestas contienen información posterior al instante reproducido. El archivo original puede servir internamente para preparar ventanas, pero no se envía completo al modelo para que supuestamente ignore el futuro. Se comprueba el corte temporal antes de construir el contexto y los medios de cada inferencia.

## Qué debe comprobarse

- El sistema observa y pregunta durante la captura, sin selección manual de cada intervalo.
- La pregunta aporta una explicación que no estaba ya en las palabras o demostraciones recibidas.
- La respuesta humana y el movimiento se guardan con sus fuentes, tiempos y autor correcto.
- «Ahora no», ausencia de respuesta e interrupción de la voz tienen estados correctos y no bloquean indefinidamente el aprendizaje de fases posteriores.
- El audio del propio agente nunca se registra como explicación del cocinero.
- El contexto y el archivo enviados no contienen imágenes, resúmenes ni respuestas del futuro.
- El aprendiz recupera la explicación y el fragmento original en su propia sesión.
- Se registran tiempo del evento, recepción, inferencia, pregunta audible y respuesta, junto a descartes, repeticiones y valoración de utilidad de quien cocina.

Los turnos Meta registrados tardaron aproximadamente 14–23 segundos incluyendo subida; los jobs SAM documentados rondaron 23–24 segundos en esos casos, con carga de pesos. No se han medido todavía las latencias del ciclo propuesto. Las preguntas retrospectivas ancladas ayudan a tratar resultados tardíos, pero no convierten esa latencia en una conversación fluida. Hay que reducir y medir trabajo de subida, contexto y generación, estudiar un runtime SAM persistente durante la sesión y descartar resultados cuyo contexto haya cambiado. Pausar el replay antes de inferir no cuenta como reducir la latencia de pared.

## Debate con Opus y Grok

[Grok 4.7](dictamen-grok.txt) identifica la deriva hacia revisión posterior y propone recuperar el ciclo de percepción, intervención y aprendizaje. [Opus 5.5](dictamen-opus.txt) comparte el diagnóstico y subraya que el replay debe probar el mismo recorrido que la cámara; propone una entrada inicial de imágenes y audio separados y preguntas retrospectivas cuando la inferencia llegue tarde.

Codex conserva la propuesta de imágenes para la vía rápida, pero exige conservar secuencias o clips cuando el movimiento importa. También añade la separación de roles, el aislamiento del contexto futuro y la medición con reloj real. Corrige una inexactitud de Opus: MediaRecorder sí entrega chunks durante la grabación; el código actual se limita a acumularlos.

En su [réplica](replica-grok.txt), Grok acepta las correcciones y mantiene que los JPEG aislados no sustituyen el movimiento. Propone un solo reloj de captura y evidencias compartidas por SAM y el agente. La observación selectiva se integra en una captura continua; no vuelve a convertir el producto en revisión posterior.

Los dictámenes son revisiones de material suministrado, con herramientas desactivadas. La primera consulta larga de Opus agotó 300 segundos sin dictamen; se conserva como intento incompleto. Su consulta breve sí terminó. Los modelos y resultados están registrados en los JSON contiguos. Esta revisión fija el cambio de dirección y los criterios de entrega; no afirma que el ciclo en vivo esté implementado.
