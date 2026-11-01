package com.compinche.wearables

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.externalsampleapps.cameraaccess.camera.CapturePreview
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class CaptureUploadState(val preview: CapturePreview? = null, val sending: Boolean = false,
    val sentUrl: String? = null, val error: String? = null)

/** An in-flight send survives activity recreation; server receipts cover ambiguous timeouts. */
class CaptureUploadViewModel(application: Application) : AndroidViewModel(application) {
    private val mutableState = MutableStateFlow(CaptureUploadState())
    val state = mutableState.asStateFlow()

    fun send(preview: CapturePreview) {
        if (mutableState.value.sending ||
            (mutableState.value.preview == preview && mutableState.value.sentUrl != null)) return
        mutableState.value = CaptureUploadState(preview = preview, sending = true)
        viewModelScope.launch {
            try {
                val url = CompincheClient.upload(getApplication(), preview)
                mutableState.value = CaptureUploadState(preview = preview, sentUrl = url)
            } catch (error: Exception) {
                if (error is CancellationException) throw error
                mutableState.value = CaptureUploadState(preview = preview,
                    error = error.message ?: "No se pudo enviar a Compinche.")
            }
        }
    }
}
