/**
 * Microphone -> Amazon Transcribe.
 *
 * The browser hands us 44.1 or 48 kHz floats; Transcribe wants 16 kHz signed
 * 16-bit PCM. We average the samples that fall inside each output sample
 * (cheap, and it beats dropping samples) and post one 100 ms chunk at a time.
 */
const TARGET_RATE = 16000;
const CHUNK_SAMPLES = TARGET_RATE / 10; // 1600 samples = 100 ms

class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / TARGET_RATE; // e.g. 3 at 48 kHz, 2.756 at 44.1 kHz
    this.position = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(CHUNK_SAMPLES);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i += 1) {
      this.sum += channel[i];
      this.count += 1;
      this.position += 1;

      if (this.position >= this.step) {
        this.position -= this.step;
        let value = this.count > 0 ? this.sum / this.count : 0;
        this.sum = 0;
        this.count = 0;

        if (value > 1) value = 1;
        else if (value < -1) value = -1;

        this.out[this.filled] = value < 0 ? value * 0x8000 : value * 0x7fff;
        this.filled += 1;

        if (this.filled === CHUNK_SAMPLES) {
          const chunk = this.out.slice();
          this.port.postMessage(chunk.buffer, [chunk.buffer]);
          this.filled = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor('pcm-downsampler', PcmDownsampler);
