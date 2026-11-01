/* Only emits mono PCM. Capture remains local until the session controller sends it. */
class CompinchePCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = Math.round(sampleRate / 10);
    this.buffer = new Float32Array(this.size);
    this.offset = 0;
  }
  process(inputs, outputs) {
    const channels = inputs[0];
    {
      const count = channels?.[0]?.length || outputs?.[0]?.[0]?.length || 128;
      for (let i = 0; i < count; i++) {
        let sample = 0;
        for (const channel of channels || []) sample += channel[i] || 0;
        this.buffer[this.offset++] = channels?.length ? sample / channels.length : 0;
        if (this.offset === this.size) {
          const copy = this.buffer;
          this.port.postMessage({ samples: copy, sampleRate }, [copy.buffer]);
          this.buffer = new Float32Array(this.size);
          this.offset = 0;
        }
      }
    }
    // Keep the graph running without playing the microphone back into the room.
    for (const channel of outputs[0] || []) channel.fill(0);
    return true;
  }
}
registerProcessor("compinche-pcm", CompinchePCMProcessor);
