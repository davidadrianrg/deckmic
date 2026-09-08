/* DeckMic — captura de micrófono con AudioWorklet.
   Downmix a mono + remuestreo lineal a 16 kHz + conversión a Int16. */
class DeckMicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;            // posición fraccional en el buffer de entrada
    this.buf = null;         // Float32Array del bloque actual (mono)
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch0 = input[0];
    const ch1 = input[1];
    // downmix a mono
    let mono;
    if (ch1) {
      mono = new Float32Array(ch0.length);
      for (let i = 0; i < ch0.length; i++) mono[i] = (ch0[i] + ch1[i]) * 0.5;
    } else {
      mono = ch0;
    }
    // remuestreo lineal a 16 kHz
    const outLen = Math.floor(mono.length / this.ratio);
    const out = new Float32Array(outLen);
    let j = 0;
    for (let n = 0; n < outLen; n++) {
      const p = n * this.ratio;
      const i0 = Math.floor(p);
      const frac = p - i0;
      const a = mono[i0] !== undefined ? mono[i0] : 0;
      const b = mono[i0 + 1] !== undefined ? mono[i0 + 1] : a;
      out[n] = a + (b - a) * frac;
    }
    // a Int16 PCM
    const pcm = new Int16Array(outLen);
    let peak = 0;
    for (let i = 0; i < outLen; i++) {
      const s = Math.max(-1, Math.min(1, out[i]));
      if (Math.abs(s) > peak) peak = Math.abs(s);
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage({ pcm: pcm.buffer, peak }, [pcm.buffer]);
    return true;
  }
}
registerProcessor("deckmic-processor", DeckMicProcessor);
