# Medir el recipiente

Compinche estima el diámetro de la olla o sartén durante la sesión en directo y, si la vista es muy inclinada, pide verla desde arriba.

| Pieza | Qué aporta | Dónde corre |
| --- | --- | --- |
| **SAM 3.1** (`sam-3.1`, Meta Model API) | Máscara del recipiente y de una referencia de tamaño conocido | API de Meta, ~1–2 s por concepto |
| **MapAnything** (`facebook/map-anything-apache`, Meta, Apache-2.0) | Intrínsecos de cámara y puntos 3D métricos por píxel | GPU local, contenedor sin red, ~0,3–1 s por imagen en caliente |
| Referencia conocida (brick de leche de 1 L, 16,5–19,5 cm de alto) | Contrasta y corrige la escala métrica de MapAnything | Geometría en `measure.py` |

1. El borde es la fila más ancha de la silueta tras un filtro de mediana vertical (las asas ocupan pocas filas y desaparecen).
2. Diámetro = ancho en píxeles × profundidad ÷ focal; la referencia corrige la escala.
3. Inclinación = arccos(eje menor ÷ eje mayor). Por encima de 60° Compinche pregunta «¿Me enseñas un momento la olla desde arriba?».
4. Varias vistas se agregan con la mediana; el rango mostrado incluye la dispersión real y la incertidumbre de la referencia.
5. En modo aprendiz, el recipiente nuevo se compara con el de la sesión original: proporción de superficie a la misma altura de masa (geometría, no una receta validada).

## Resultado real con el vídeo familiar

Olla de la grabación, vista lateral (~77° de inclinación), 8 fotogramas: medianas de 27–33 cm según el tramo; el ancho en píxeles es estable (124–125 px) y la dispersión procede de la escala (altura aparente del brick 21–28 cm sin corregir, a veces tapado por una jarra). Falta contrastarlo con la medida real de la olla.

## Ejecutar

```sh
docker build -t compinche-measure:local backend/measure_runtime          # sobre una imagen local con PyTorch CUDA
# Primera vez, con red: descarga pesos (HF) y el código de DINOv2 (torch hub) a cachés locales.
```

`COMPINCHE_MEASURE_IMAGE`, `COMPINCHE_SAM_CACHE` (caché de Hugging Face) y `COMPINCHE_TORCH_CACHE` activan la medición; sin ellos la sesión funciona sin medir. Los límites: no mide peso, volumen de ingredientes, temperatura ni punto; sin referencia visible el rango es amplio (±25 %).
