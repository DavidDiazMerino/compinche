# Panel de expertos para terminar y mejorar Compinche

31 de octubre de 2026

La siguiente entrega debería cerrar una lección real: una persona explica una decisión de cocina, revisa lo que Compinche conserva y otra persona utiliza esa explicación al cocinar. La infraestructura ya permite buena parte de ese recorrido. Las prioridades son hacer revisable la evidencia, recuperar el conocimiento auténtico y comprobar su utilidad con un aprendiz.

## Participantes y alcance

| Revisor | Consulta realizada |
| --- | --- |
| Grok 4.7 | Revisión del dossier completo y réplica a seis objeciones. El catálogo de la cuenta lo ofrece como modelo principal; el uso del CLI registra `grok-4.7-build`. |
| Claude Opus 5.5 | Opinión breve sobre el resumen verificado, extractos de código y la posición corregida de Grok. El uso registra `claude-opus-5-5`. |
| Codex | Lectura del código, comprobaciones locales, reproducción de hallazgos y síntesis del plan. |

Las dos consultas extensas a Opus agotaron sus límites de 15 y 10 minutos sin dictamen. La consulta breve completó en 32 segundos. Su opinión tiene un alcance menor que la revisión de Grok; los intentos fallidos no se presentan como opiniones. Las consultas se realizaron con los CLI autenticados en las cuentas del usuario.

Las revisiones cubren producto, experiencia móvil, aprendizaje, IA multimodal, fiabilidad y entrega. El dossier base contiene 18 archivos del estado de trabajo, incluidos cambios sin commit, y un resumen de los experimentos locales. Los revisores externos tuvieron las herramientas desactivadas y recibieron código y resultados, sin vídeos ni credenciales. La revisión de interfaz se basa en código y capturas existentes; hoy no se realizó una nueva prueba de interacción en navegador.

## Qué está demostrado

Pasan los 41 tests del backend y la comprobación de sintaxis de los cinco módulos JavaScript. El informe del 30 de octubre registra ocho respuestas completadas de Meta con `muse-spark-1.3`, aproximadamente entre 14 y 23 segundos incluyendo subida. Son seis condiciones iniciales y dos revisiones de formado, con una generación por condición. Las valoraciones de utilidad son editoriales; no prueban aprendizaje.

Hay inferencias SAM reales: en las ventanas registradas se detectaron manos en 8/8 fotogramas, cuchara y salsa en 4/8, y croquetas en 0/8 en algunas ventanas donde aparecen. Son conteos de detección, no medidas de exactitud ni de comprensión culinaria. El README principal todavía habla de 31 tests y de una integración Meta sin verificar; ha quedado por detrás de los resultados locales.

Se han reproducido tres límites con entradas construidas, sin llamar a Meta:

- `validate_output` acepta «Veo en el vídeo que la bechamel está lista» sin medio activo.
- Acepta una afirmación inferida «El aceite está a 180 ºC» cuando la nota citada no proporciona temperatura.
- Con 101 afirmaciones de la sesión original y una corrección reciente del aprendiz, el recorte de contexto deja fuera esa corrección y su referencia.

Los dos primeros son contraejemplos al validador, no respuestas observadas del proveedor. El tercero conserva el dato en SQLite, pero lo omite del contexto del modelo.

## Seis perspectivas

| Perspectiva | Mejora que aporta más valor ahora |
| --- | --- |
| Producto | Elegir una preparación y una decisión difícil de aprender con una receta escrita. Completar el recorrido entre quien enseña y quien aprende. |
| Cocina y móvil | Marcar momentos sin interrumpir la preparación, revisar después y probar el recorrido en un teléfono físico. La versión local actual requiere preparar un acceso seguro para esa prueba. |
| Aprendizaje | Conservar señal, acción y explicación de la persona. Pedir al aprendiz que reconozca la señal o muestre el gesto. |
| IA y evidencia | Abrir las fuentes desde cada afirmación, permitir corregir y evaluar preguntas útiles, redundantes o imposibles de contestar. |
| Fiabilidad | Preservar el contexto del aprendiz, conectar la idempotencia del cliente y disponer de una copia que pueda restaurarse. |
| Demo | Usar clips cortos preparados y distinguir ejemplo ilustrado, resultados registrados e inferencia en directo. |

El primer usuario a validar es una pareja de personas que ya quiere transmitir una preparación concreta. La necesidad y la disposición a pagar siguen siendo hipótesis.

## Acuerdos y decisiones discutidas

| Decisión | Grok | Opus | Propuesta del moderador |
| --- | --- | --- | --- |
| Primera entrega | Guion honesto con clips preparados. | Reservar contexto para el aprendiz. | Corregir primero el fallo de contexto reproducible y preparar el guion en paralelo. |
| Riesgos de la demo | Regex y unidades antes del cupo de contexto. | Reintentos y cierre accidental de preguntas deben entrar en la demo. | Verificar ambos con casos acotados; no prometer garantía semántica. |
| Recortador, voz y backup | Pueden esperar para la demo. | Pueden esperar para la demo. | Usar clips preparados; priorizar las funciones de producto después del ciclo humano. |

Los tres análisis apuntan a validar el aprendizaje con personas. Codex propone que la siguiente iteración culmine en una lección completa, con fuentes revisables y un intento del aprendiz, en lugar de terminar tras corregir los fallos técnicos.

La réplica de Grok corrige cuatro puntos de su dictamen inicial. El segundo 320 del original corresponde al segundo 0 del clip que empieza allí; las anclas actuales identifican el comienzo del clip, no necesariamente el gesto. La ausencia de menciones a SAM no demuestra ausencia de influencia. Responder una pregunta no confirma una afirmación. Mostrar un UUID es un problema de presentación y no justifica rechazar una respuesta útil.

Estas correcciones están incorporadas en el plan siguiente. Los dictámenes originales se conservan como opiniones de los revisores, acompañados de la réplica y la verificación del moderador.

## Orden de trabajo propuesto

S, M y L expresan esfuerzo relativo, sin prometer días. P0 protege y cierra la demo; P1 prepara una versión útil; P2 depende de la evaluación. Se propone el orden de Opus para los defectos técnicos comprobados y una lección humana como resultado de la iteración.

1. **P0 · Conservar el contexto del aprendiz · S o M.** Sustituir el corte conjunto de conocimiento y notas por una selección que reserve espacio para ambas sesiones. Terminada cuando, con 101 afirmaciones del profesor y una corrección del aprendiz, la corrección aparece en `knowledge` y en las referencias. Incluirla no cambia su estado de confirmación.

2. **P0 · Cerrar los contraejemplos de validación · S o M.** Normalizar unidades y cubrir las observaciones en español sin medios, también en afirmaciones. Terminada cuando los dos casos reproducidos se rechazan y los controles sustentados siguen pasando. Registrar falsos rechazos y describir el control como un filtro heurístico. Una colección de patrones no garantiza veracidad.

3. **P0 · Turnos recuperables y acciones explícitas · M.** `sendTurn` no envía `idempotency_key`, aunque el servidor admite recibos para turnos aceptados. Conservar una clave por intento y reconciliar tras un timeout. Distinguir responder de añadir contexto: la casilla actual nace marcada. Terminada cuando un intento ya aceptado se recupera sin otro turno y añadir contexto mantiene la pregunta pendiente. Responder no confirma conocimiento. Esto no promete una sola llamada al proveedor ante cualquier caída anterior al guardado.

4. **P0 · Fuentes navegables y corrección · M.** `knowledgeCards` y `agentPanel` muestran contadores; la interfaz ofrece confirmar y la API ya permite corregir texto. Añadir acceso a vídeo, nota o cita y un control de corrección que conserve el original. Terminada cuando una corrección persiste y se recupera desde aprender. Abrir el clip correcto y saltar solo si existe un instante verificado; no confundir el origen del clip con el momento del gesto.

5. **P0 · Una lección completa y un guion coherente · M.** Capturar una decisión, obtener una respuesta real de quien cocina, revisarla y abrir una sesión de aprendizaje con un intento distinto. Actualizar README y guion con los resultados actuales. Terminada cuando un tercero recorre origen, explicación y aprendizaje, distingue autores y fuentes y sabe qué es ejemplo, resultado registrado o inferencia en directo. Para presentar, usar clips breves preparados y mostrar el estado real si falta un proveedor.

6. **P1 · Selección de intervalo · M.** `observe()` envía el archivo completo, aunque el botón diga «este momento»; el diálogo sí lo explica. Añadir inicio y fin, conservar el original y registrar los dos tiempos. Terminada cuando una grabación larga produce un clip bajo 40 MiB con relación correcta al original. Para la demo bastan clips preparados y un botón que describa lo que envía.

7. **P1 · Buscar el conocimiento propio · M.** `search()` y `memoryGrid()` se centran en ejemplos y títulos. Incluir notas y afirmaciones reales, con persona, receta y estado. Terminada cuando una explicación se encuentra por su contenido y abre su evidencia. Una búsqueda de texto basta para empezar; no requiere embeddings.

8. **P1 · Copia restaurable · M o L.** El JSON actual exporta metadatos y pide descargar vídeos aparte; no hay restauración. Diseñar una copia con medios, relaciones y confirmaciones. Terminada cuando un perfil vacío recupera la lección, abre sus fuentes y mantiene el vínculo entre original y aprendiz.

9. **P1 · Probar el recorrido en cocina y teléfono físico · M o L.** Preparar acceso HTTPS autenticado y probar cámara, formato grabado, respuesta y consulta con manos ocupadas. La versión actual está restringida a localhost. Terminada cuando una persona realiza la tarea y recupera la explicación sin asistencia del desarrollador. Registrar interrupciones y ayuda requerida antes de decidir cuánto automatizar o añadir voz.

10. **P2 · SAM con una función de aprendizaje comprobada · M.** Mostrar «sin detecciones» cuando corresponda y probar si un resaltado ayuda a localizar un gesto. Un job completado puede tener cero detecciones. Terminada esta decisión cuando una comparación con el mismo material permita mantener, cambiar o retirar el resaltado del recorrido principal.

## Ideas para probar

**Ficha de decisión.** «Cuando ocurre esta señal, hago esta acción por esta razón», junto al clip, la voz o cita original y el estado de revisión. Las casillas vacías conservan las dudas; el modelo no las rellena por defecto.

**Responder mostrando.** Añadir un clip corto como respuesta a una pregunta pendiente. La API admite `evidence_ids` junto a `answer_to`; el compositor actual envía texto. Una demostración puede explicar un gesto mejor que una descripción larga.

**Ejemplo y contraejemplo aportados por quien enseña.** Guardar dos momentos que esa persona identifica como «todavía no» y «ahora sí». Probar si el aprendiz distingue la señal. Se conserva un criterio humano, sin convertirlo en una medición visual de temperatura o textura.

## Tres experimentos que deciden qué construir

**Utilidad de las preguntas.** Una persona que cocina clasifica preguntas sin ver las etiquetas editoriales: aporta una decisión, repite lo explicado o no se puede responder. Medir información nueva y correcciones necesarias. Si predominan repeticiones o preguntas imposibles, revisar selección de momentos y prompt antes de generar más. La mejora de `cues-v2` es una hipótesis para evaluar.

**Transferencia a otra persona.** Comparar vídeo con vídeo más explicación confirmada mediante tareas o preparaciones comparables, controlando el efecto de repetir la misma tarea. Registrar errores de decisión, ayuda solicitada y capacidad de explicar la señal. Un piloto pequeño guía el siguiente ensayo; no demuestra superioridad estadística. Incluir un teléfono físico antes de dar por validada la experiencia en cocina.

**Aporte de SAM.** Mantener clip, pregunta y contexto y comparar condiciones con y sin resultados SAM. Separar su efecto sobre la respuesta del modelo de la utilidad visual del overlay para el aprendiz. Medir localización del gesto, errores y ayuda requerida. Si no aporta a ninguna tarea, conservarlo como herramienta de investigación o función opcional.

## Qué posponer

El bucle de vídeo continuo, una red social de recetas, marketplace, reconstrucción 3D, cantidades o temperaturas inferidas de píxeles y adaptación física de cocción. Mantener la comparación de superficies como geometría ilustrativa, sin convertirla en recomendación de cocina. Evaluar voz a partir de la prueba con manos ocupadas. La arquitectura local actual permite resolver las primeras incertidumbres sin migrar de framework.

## Dictámenes y verificación

- [Dictamen de Grok](dictamen-grok.txt) y [réplica con correcciones](replica-grok.txt).
- [Dictamen breve de Opus 5.5](dictamen-opus.txt).
- [Revisión de Codex](dictamen-codex.txt).
- [Resultados de verificación](verificacion.json), [fuentes y hashes](fuentes.json) y [modelos y sesiones](sesiones.json).

Desde `application/`, los tres hallazgos técnicos se reproducen sin proveedores ni datos de la aplicación:

```sh
backend/.venv/bin/python docs/reviews/2026-10-31/verificar_hallazgos.py
```

En el repositorio, los cambios de esta tarea se limitan a los documentos y al script de verificación en este directorio. El CLI de Grok se actualizó de 1.0.5 a 1.0.70 mediante su actualizador oficial porque el servidor rechazaba la versión anterior.
