/**
 * Push to talk. Streams the microphone as 16 kHz PCM over the WebSocket.
 *
 * getUserMedia only exists on localhost or https, so the button that uses this
 * is hidden everywhere else.
 */
export const MIC_AVAILABLE =
  Boolean(navigator.mediaDevices?.getUserMedia) &&
  (window.isSecureContext ||
    location.hostname === 'localhost' ||
    location.hostname === '127.0.0.1');

export class Mic {
  /**
   * @param {() => Promise<AudioContext>} getContext
   * @param {(chunk: ArrayBuffer) => void} onChunk
   */
  constructor(getContext, onChunk) {
    this.getContext = getContext;
    this.onChunk = onChunk;
    this.stream = null;
    this.node = null;
    this.source = null;
    this.sink = null;
    this.moduleLoaded = false;
    this.recording = false;
  }

  async start() {
    if (this.recording) return;
    const ctx = await this.getContext();

    if (!this.moduleLoaded) {
      await ctx.audioWorklet.addModule('/js/pcm-worklet.js');
      this.moduleLoaded = true;
    }

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.source = ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(ctx, 'pcm-downsampler');
    this.node.port.onmessage = (event) => this.onChunk(event.data);

    // The worklet only runs while it is connected to the graph, so route it
    // into a muted gain node instead of the speakers (no echo that way).
    this.sink = ctx.createGain();
    this.sink.gain.value = 0;

    this.source.connect(this.node);
    this.node.connect(this.sink);
    this.sink.connect(ctx.destination);
    this.recording = true;
  }

  stop() {
    if (!this.recording) return;
    this.recording = false;

    if (this.node) {
      this.node.port.onmessage = null;
      this.node.disconnect();
      this.node = null;
    }
    if (this.source) {
      this.source.disconnect();
      this.source = null;
    }
    if (this.sink) {
      this.sink.disconnect();
      this.sink = null;
    }
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
  }
}
