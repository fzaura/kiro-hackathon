/**
 * Sound: the character's voice (mp3 from Polly) plus the dance music.
 *
 * One AudioContext for the whole page. It is created on the first click,
 * because browsers refuse to make noise before the user asks for it.
 */
export class Speaker {
  constructor() {
    this.ctx = null;
    this.source = null;
    this.visemes = [];
    this.startedAt = 0;
    this.index = 0;
    this.speaking = false;
    this.music = null;
  }

  /** Call this from inside a click handler. */
  async unlock() {
    if (!this.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new Ctx();
    }
    if (this.ctx.state === 'suspended') await this.ctx.resume();
    return this.ctx;
  }

  /**
   * Speak one line and resolve when the audio is done.
   * @param {string} base64Mp3
   * @param {{time:number,value:string}[]} visemes
   */
  async speak(base64Mp3, visemes = []) {
    await this.unlock();
    this.stop();

    const bytes = Uint8Array.from(atob(base64Mp3), (c) => c.charCodeAt(0));
    const buffer = await this.ctx.decodeAudioData(bytes.buffer);

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);

    this.source = source;
    this.visemes = visemes;
    this.index = 0;
    this.startedAt = this.ctx.currentTime;
    this.speaking = true;

    return new Promise((resolve) => {
      source.onended = () => {
        if (this.source === source) {
          this.speaking = false;
          this.source = null;
          this.visemes = [];
        }
        resolve();
      };
      source.start();
    });
  }

  /** The viseme that should be on the mouth right now, or null. */
  viseme() {
    if (!this.speaking || this.visemes.length === 0) return null;

    const latency = this.ctx.outputLatency || 0;
    const now = (this.ctx.currentTime - this.startedAt - latency) * 1000;
    if (now < 0) return null;

    // The timeline only moves forwards, so walk it instead of searching it.
    if (this.visemes[this.index] && this.visemes[this.index].time > now) {
      this.index = 0;
    }
    while (
      this.index + 1 < this.visemes.length &&
      this.visemes[this.index + 1].time <= now
    ) {
      this.index += 1;
    }
    const current = this.visemes[this.index];
    return current && current.time <= now ? current.value : null;
  }

  stop() {
    if (this.source) {
      this.source.onended = null;
      try {
        this.source.stop();
      } catch {
        /* already stopped */
      }
      this.source = null;
    }
    this.speaking = false;
    this.visemes = [];
  }

  startMusic(url, volume = 0.4) {
    if (!this.music) {
      this.music = new Audio();
      this.music.loop = true;
    }
    if (this.music.src !== new URL(url, location.href).href) {
      this.music.src = url;
    }
    this.music.volume = volume;
    this.music.currentTime = 0;
    this.music.play().catch(() => {});
  }

  stopMusic() {
    if (!this.music) return;
    this.music.pause();
    this.music.currentTime = 0;
  }
}
