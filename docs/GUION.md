# Del guion a la app

Cada escena del vídeo de 3 minutos frente a lo que Compinche hace de verdad, cómo provocarlo y qué falta grabar. Las escenas 01–04 y 12 (cierre) son de montaje.

**Regla del hackathon:** la demo solo puede mostrar lo construido durante el evento (26 oct – 4 nov) y cada `[resultado]` del guion tiene que salir de la app. Nada de esta tabla se presenta como hecho si dice «pendiente».

**Quién cocina:** la receta la hace Papá (el guion original decía «madre»). La grabación de referencia es `WhatsApp Video 2026-10-31 at 10.24.50.mp4`: 16 min, casi sin voz, cámara fija lateral.

| Escena | Qué hace la app | Cómo se provoca | Verificado | Falta |
|---|---|---|---|---|
| **04** Mirada de Compinche | `#teach`: cámara del móvil o replay causal; la interfaz observa y escucha | Iniciar sesión con consentimiento | Chrome + replay | Grabar con el móvil en posición de ojos |
| **05** Medir sartén/olla | SAM 3.1 (API de Meta) + MapAnything (local) + brick de leche de 1 L como escala; tarjeta con ≈ cm, rango y «Cómo se ha medido» | Automático al ver el recipiente | Olla de Papá: 27–33 cm según tramo, vista lateral 77° | Diámetro real de la olla para contrastar; brick visible y sin tapar |
| **05** «¿Me enseñas la sartén desde arriba?» | Si la inclinación supera 60°, lo pide y la medición se refina con la nueva vista | Automático | Pregunta real en el replay | Que Papá la enseñe desde arriba en la toma |
| **06** Decisión de fuego | Spark detecta encender/subir/bajar (heat_change) y un medidor de llama bajo el recipiente lo respalda; Spark pregunta «¿Qué te hizo decidir…?»; línea temporal con fases y fuego | Automático | «¿Qué te hizo decidir encender el fuego en ese momento?» / «¿A qué fuego lo dejas para empezar?» | Respuesta real de Papá |
| **07** «Enséñame qué significa así» | Pregunta por la señal o pide repetir el gesto (repeat); SAM destaca la herramienta; ficha con señal / sensación / decisión | Automático | En 11:44 (varillas en alto): «¿Qué te dice que ya está en su punto?» | Respuesta real y repetición del gesto |
| **08** Lo que hizo sin decirlo | Spark pregunta por gestos no explicados; la respuesta se comprueba (relevante / no) antes de crear la ficha; confirmar o **corregir** | Automático | En 16:05: «Has tapado el recipiente con film, ¿para qué lo haces?» | Respuesta real; confirmar/corregir con su nombre |
| **09** Fases y fichas | Fases en la línea temporal; fichas con categoría y título («Cuando cae como una cinta», «Cuándo bajar el fuego») | Automático | Spark real sobre respuestas de prueba | Fichas reales confirmadas |
| **10** Otra cocina, otra sartén | Modo aprendiz (`Practicar con lo aprendido`): mide el nuevo recipiente y lo compara con el de la lección | Automático en `#practice/<id>` | Mismo vídeo: «31 cm frente a 33, 90 % de su superficie, misma cantidad» | Tu sartén en tu cocina |
| **11** «¿La leche era fría?» | Respuesta solo con fichas confirmadas, citando a quien enseñó; si no se enseñó, lo dice; con Muse Glimmer local si está levantado | Preguntar en voz o por escrito | Spark: 1–2 s, cita correcta; «no lo explicó» cuando no está | Que Papá conteste esa pregunta en la sesión |
| **11** «¿Y esto ya está?» → comparar el gesto | Spark invita a repetir la comprobación; CoTracker3 + DTW alinean demostración e intento; pantalla partida y diferencia observada | Preguntar tras confirmarse la ficha del punto | Demostración 11:40 frente a 07:08: «Tú dejas las varillas dentro… él las levanta bien alto para ver cómo cae» | Tu intento real |
| **12** «Antes de tapar…» | Recordatorio proactivo de una ficha confirmada cuando la escena llega a su momento, una sola vez | Automático en modo aprendiz | En 910 s (bol sin tapar): «Papá decía que antes de tapar…» | Ficha real del gesto final |

## Modelos de Meta en juego

| Modelo | Uso | Dónde |
|---|---|---|
| Muse Spark 1.3 | Observar y preguntar, comprobar respuestas, derivar fichas, guiar, comparar gestos | Meta Model API |
| Muse Voice Transcribe 1.0 | Escucha continua en español | Meta Model API (WebSocket) |
| SAM 3.1 | Recipiente, referencia de escala, herramienta destacada | Meta Model API |
| Muse Glimmer 30B | Respuestas al aprendiz sobre conocimiento familiar confirmado | GPU local (vLLM), respaldo Spark |
| MapAnything (Apache-2.0) | Geometría métrica para medir | GPU local |
| CoTracker3 | Seguimiento de puntos para alinear gestos | GPU local |

## Reproducir

```sh
npm run demo                       # http://127.0.0.1:8017/#teach
backend/glimmer/serve.sh           # opcional: Muse Glimmer en :8100; luego COMPINCHE_GLIMMER_URL=http://127.0.0.1:8100/v1
```

Medición y gestos necesitan `COMPINCHE_MEASURE_IMAGE`, `COMPINCHE_SAM_CACHE` y `COMPINCHE_TORCH_CACHE` (ver [MEASURE.md](../backend/MEASURE.md)).

## Grabar con el móvil en otra casa

La cámara del navegador necesita HTTPS. Con Tailscale (ya instalado en la GB10 y en el móvil), sin abrir nada a internet:

```sh
COMPINCHE_ALLOWED_HOSTS=spark-17c7.tail6bdc3d.ts.net npm run demo   # servidor en :8017
tailscale serve --bg --https=443 http://127.0.0.1:8017               # solo visible dentro de tu tailnet
# en el móvil, con Tailscale activo: https://spark-17c7.tail6bdc3d.ts.net/#teach
tailscale serve --https=443 off                                      # al terminar
```

La app no tiene cuentas: cualquier equipo de la tailnet podría abrirla mientras `serve` esté activo.
