package com.compinche.wearables

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch

@Composable
fun LiveLearningPanel(
    bridge: LiveBridge,
    streaming: Boolean,
    archiveBusy: Boolean,
    recordingWithAudio: Boolean,
    requestMicrophone: suspend () -> Boolean,
    modifier: Modifier = Modifier,
) {
    val state by bridge.state.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    val keyboard = LocalSoftwareKeyboardController.current
    var expanded by rememberSaveable { mutableStateOf(false) }
    var speaker by rememberSaveable { mutableStateOf("") }
    var role by rememberSaveable { mutableStateOf("observer") }
    var teacher by rememberSaveable { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var metrics by remember { mutableStateOf(false) }
    val fieldColors = OutlinedTextFieldDefaults.colors(focusedTextColor = Color.White,
        unfocusedTextColor = Color.White, focusedLabelColor = Color.White,
        unfocusedLabelColor = Color.White, focusedBorderColor = Color.White,
        unfocusedBorderColor = Color.LightGray, cursorColor = Color.White)
    Column(modifier = modifier.fillMaxWidth().background(Color(0xED202A20), RoundedCornerShape(16.dp))
        .padding(horizontal = 12.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        TextButton(onClick = { expanded = !expanded }) {
            Text(if (state.active) "Compinche · ${if (state.agentSpeaking) "preguntando" else if (state.sourceSpeaking) "escuchando" else if (state.paused) "en pausa" else "observando"}"
                else "Compinche · aprender en directo", color = Color.White)
        }
        if (state.active) {
            state.question?.let { Text(it, style = MaterialTheme.typography.titleMedium, color = Color.White) }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                if (state.agentSpeaking) TextButton(onClick = bridge::interrupt) { Text("Interrumpir", color = Color.White) }
                if (state.questionId != null) TextButton(onClick = bridge::postpone) { Text("Ahora no", color = Color.White) }
                TextButton(onClick = bridge::pauseOrResume) { Text(if (state.paused) "Seguir" else "Pausar", color = Color.White) }
                TextButton(onClick = bridge::stop) { Text("Terminar", color = Color.White) }
            }
            if (expanded) {
                Text("Explicaciones guardadas: ${state.cards}. La voz se atribuye a $speaker según tu selección.", color = Color.White)
                Text(state.message, style = MaterialTheme.typography.bodySmall, color = Color.White)
                TextButton(onClick = { metrics = true }) { Text("Latencia registrada", color = Color.White) }
            }
        } else if (expanded) {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Al iniciar, Compinche analizará las imágenes del SDK y el micrófono del teléfono durante esta sesión. La grabación original se conserva por separado.", color = Color.White)
                OutlinedTextField(value = teacher, onValueChange = { teacher = it.take(100) },
                    label = { Text("Quién cocina") }, colors = fieldColors, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(value = speaker, onValueChange = { speaker = it.take(100) },
                    label = { Text("Quién habla por el micrófono") }, colors = fieldColors, singleLine = true, modifier = Modifier.fillMaxWidth())
                Row {
                    listOf("cook" to "Cocinero", "wearer" to "Portador", "observer" to "Observador").forEach { (id, label) ->
                        TextButton(onClick = { role = id }) { Text((if (role == id) "✓ " else "") + label, color = Color.White) }
                    }
                }
                Text("El micrófono no identifica automáticamente a la persona. Cambia estos datos si responde otra persona.", style = MaterialTheme.typography.bodySmall, color = Color.White)
                if (recordingWithAudio) Text("Termina la grabación con sonido del SDK antes de iniciar: el aprendizaje utiliza el micrófono del teléfono.", color = Color.White)
                Button(enabled = streaming && !state.starting && !archiveBusy && speaker.isNotBlank() && !recordingWithAudio,
                    onClick = {
                        scope.launch {
                            if (requestMicrophone()) {
                                error = null
                                keyboard?.hide()
                                bridge.start(speaker, role, teacher)
                                expanded = false
                            } else error = "Se necesita permiso de micrófono para escuchar las explicaciones."
                        }
                    }) { Text(if (state.starting) "Conectando…" else "Iniciar aprendizaje") }
                if (archiveBusy) Text("Conservando el original de la sesión. Espera antes de iniciar otra.", color = Color.White)
                if (!streaming) Text("Inicia primero Start Session → Preview para recibir imágenes reales del SDK.", color = Color.White)
                Text(state.message, style = MaterialTheme.typography.bodySmall, color = Color.White)
                error?.let { Text(it, color = Color.White) }
                TextButton(onClick = bridge::retryArchives) { Text("Reintentar copias locales", color = Color.White) }
            }
        }
    }
    if (metrics) AlertDialog(onDismissRequest = { metrics = false },
        title = { Text("Mediciones de esta sesión") },
        text = { Text("Frames enviados: ${state.framesSent}\nSDK PTS de preview aproximado: ${state.sdkPresentationTimeUs ?: "sin datos"}\n${state.metrics.ifBlank { "Sin mediciones del servidor todavía." }}") },
        confirmButton = { TextButton(onClick = { metrics = false }) { Text("Cerrar") } })
}
