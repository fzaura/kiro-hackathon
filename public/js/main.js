import { Character } from './avatar.js';
import { Speaker } from './speaker.js';
import { Mic, MIC_AVAILABLE } from './mic.js';

/* ------------------------------------------------------------------ the DOM */

const el = {
  canvas: document.getElementById('scene'),
  name: document.getElementById('name'),
  title: document.getElementById('title'),
  tagline: document.getElementById('tagline'),
  bubble: document.getElementById('bubble'),
  state: document.getElementById('state'),
  stateText: document.getElementById('state-text'),
  transcript: document.getElementById('transcript'),
  composer: document.getElementById('composer'),
  input: document.getElementById('input'),
  send: document.getElementById('send'),
  dance: document.getElementById('dance'),
  mic: document.getElementById('mic'),
  toast: document.getElementById('toast'),
  gate: document.getElementById('gate'),
  gateName: document.getElementById('gate-name'),
  gateSub: document.getElementById('gate-sub'),
  gateBtn: document.getElementById('gate-btn'),
  barFill: document.getElementById('bar-fill'),
};

/* ------------------------------------------------------------------- assets */

const CLIPS = {
  idle: '/animations/idle.fbx',
  thinking: '/animations/thinking.fbx',
  talking: '/animations/talking.fbx',
  dance: '/animations/dance.fbx',
  'fall-flat': '/animations/fall-flat.fbx',
  'stand-up': '/animations/stand-up.fbx',
};

const STAND_UP = { startAt: 2.5, timeScale: 1.6 }; // skip the 3 s lying still

/* -------------------------------------------------------------------- state */

const character = new Character(el.canvas);
const speaker = new Speaker();
const mic = new Mic(
  () => speaker.unlock(),
  (chunk) => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(chunk);
  },
);

const moves = new Map();
let profile = null;
let socket = null;
let reconnectIn = 500;
let started = false;
let wantsGreeting = false;
let busy = true;
let dancing = false;
let listening = false;
let micTimer = null;
let liveLine = null;

/* ------------------------------------------------------------------ helpers */

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const readingTime = (text) => Math.min(9000, 1400 + text.length * 55);

function setPhase(phase) {
  const labels = {
    thinking: 'thinking',
    speaking: 'speaking',
    listening: 'listening',
    dancing: 'dancing',
  };
  if (labels[phase]) {
    el.stateText.textContent = labels[phase];
    el.state.hidden = false;
  } else {
    el.state.hidden = true;
  }

  if (phase === 'thinking' || phase === 'listening') character.play('thinking');
  else if (phase === 'speaking') character.play('talking');
  else if (phase === 'dancing') character.play('dance');
  else if (phase === 'idle') character.play('idle');
}

function setBusy(value) {
  busy = value;
  el.send.disabled = value;
  el.input.disabled = value;
  el.mic.disabled = value && !listening;
  if (!value) el.input.focus({ preventScroll: true });
}

function addLine(who, text) {
  const node = document.createElement('p');
  node.className = `line ${who}`;
  node.textContent = text;
  el.transcript.append(node);
  el.transcript.scrollTop = el.transcript.scrollHeight;
  return node;
}

let toastTimer = null;
function toast(message) {
  el.toast.textContent = message;
  el.toast.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('on'), 3600);
}

function showBubble(text) {
  el.bubble.textContent = text;
  el.bubble.classList.add('on');
}

function hideBubble() {
  el.bubble.classList.remove('on');
}

/* ---------------------------------------------------------------- the stage */

function applyProfile(data) {
  profile = data;
  document.title = `${data.name} · ${data.title}`;
  el.name.textContent = data.name;
  el.title.textContent = data.title;
  el.tagline.textContent = data.tagline;
  el.gateName.textContent = data.name;
  el.gateSub.textContent = data.title;
  el.input.placeholder = `Ask ${data.name.split(' ')[0]} anything…`;

  const [top, bottom] = data.background || [];
  if (top) document.documentElement.style.setProperty('--bg-top', top);
  if (bottom) document.documentElement.style.setProperty('--bg-bottom', bottom);

  for (const move of data.moves || []) {
    moves.set(move.name, move);
    if (move.file && !CLIPS[move.name]) CLIPS[move.name] = move.file;
    if (move.recover && !CLIPS[move.recover]) {
      CLIPS[move.recover] = `/animations/${move.recover}.fbx`;
    }
  }

  const micPossible = MIC_AVAILABLE && data.mic !== false;
  el.mic.hidden = !micPossible;
  if (!micPossible && MIC_AVAILABLE === false) {
    console.info('Microphone needs localhost or https. Typing works everywhere.');
  }
}

/* ------------------------------------------------------------------ the AI  */

function connect() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${scheme}://${location.host}/ws`);
  socket.binaryType = 'arraybuffer';

  socket.onopen = () => {
    reconnectIn = 500;
    if (wantsGreeting) {
      wantsGreeting = false;
      socket.send(JSON.stringify({ type: 'start' }));
    }
  };

  socket.onmessage = (event) => {
    if (typeof event.data !== 'string') return;
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    handle(message);
  };

  socket.onclose = () => {
    if (!started) return;
    toast('Lost the connection. Reconnecting…');
    setTimeout(connect, reconnectIn);
    reconnectIn = Math.min(reconnectIn * 2, 8000);
  };

  socket.onerror = () => {};
}

function handle(message) {
  switch (message.type) {
    case 'hello':
      if (message.character) {
        el.mic.hidden = !(MIC_AVAILABLE && message.mic !== false);
      }
      break;

    case 'thinking':
      if (!listening) setPhase('thinking');
      break;

    case 'transcript':
      if (message.partial) {
        if (!liveLine) liveLine = addLine('live', '');
        liveLine.textContent = message.text || '…';
        el.transcript.scrollTop = el.transcript.scrollHeight;
      } else {
        if (liveLine) liveLine.remove();
        liveLine = null;
        addLine('me', message.text);
      }
      break;

    case 'reply':
      perform(message);
      break;

    case 'mic_limit':
      toast('Thirty seconds is my limit. Go on.');
      stopListening();
      break;

    case 'error':
      if (liveLine) {
        liveLine.remove();
        liveLine = null;
      }
      hideBubble();
      addLine('oops', message.message || 'Something broke.');
      toast(message.message || 'Something broke.');
      stopListening();
      character.setEmotion('sad', 0.4);
      setTimeout(() => character.setEmotion('neutral'), 1200);
      if (!dancing) setPhase('idle');
      setBusy(false);
      break;

    default:
      break;
  }
}

/** Say one answer out loud: face, bubble, lip sync, then the move. */
async function perform(message) {
  if (liveLine) {
    liveLine.remove();
    liveLine = null;
  }

  addLine('him', message.text);
  showBubble(message.text);
  character.setEmotion(message.emotion || 'neutral', 0.7);
  setPhase('speaking');

  try {
    if (message.audio) await speaker.speak(message.audio, message.visemes || []);
    else await sleep(readingTime(message.text));
  } catch (err) {
    console.error('voice playback failed', err);
    await sleep(readingTime(message.text));
  }

  hideBubble();
  character.setEmotion('neutral');
  character.setViseme(null);

  await doMove(message.move);
  setBusy(false);
}

/** Bonus B: the move the AI picked for this answer. */
async function doMove(name) {
  const move = name && name !== 'none' ? moves.get(name) : null;
  if (!move) {
    if (!dancing) setPhase('idle');
    return;
  }

  if (move.loop) {
    startDance(move); // looping moves start once the voice is done
    return;
  }

  el.state.hidden = true;
  await character.playOnce(move.name);
  if (move.recover) await character.playOnce(move.recover, STAND_UP);
  setPhase('idle');
}

/* ----------------------------------------------------------------- the moves */

function startDance(move = moves.get('dance') || { music: '/music/dance.mp3' }) {
  dancing = true;
  el.dance.classList.add('on');
  el.dance.textContent = 'Stop';
  setPhase('dancing');
  speaker.startMusic(move.music || '/music/dance.mp3');
}

function stopDance() {
  if (!dancing) return;
  dancing = false;
  el.dance.classList.remove('on');
  el.dance.textContent = 'Dance';
  speaker.stopMusic();
  setPhase('idle');
}

/* -------------------------------------------------------------- typing to him */

function say(text) {
  const clean = text.trim().slice(0, 500);
  if (!clean) return;
  if (busy) return;
  if (socket?.readyState !== WebSocket.OPEN) {
    toast('No line to the server yet. One second…');
    return;
  }
  stopDance();
  addLine('me', clean);
  el.input.value = '';
  setBusy(true);
  setPhase('thinking');
  socket.send(JSON.stringify({ type: 'chat', text: clean }));
}

/* ------------------------------------------------------- bonus A: listening */

async function startListening() {
  if (listening || busy || el.mic.hidden) return;
  if (socket?.readyState !== WebSocket.OPEN) {
    toast('No line to the server yet. One second…');
    return;
  }
  stopDance();
  listening = true;
  setBusy(true);
  el.mic.classList.add('on');
  el.mic.disabled = false;
  setPhase('listening');
  socket.send(JSON.stringify({ type: 'mic_start' }));

  try {
    await mic.start();
  } catch (err) {
    console.error('microphone refused', err);
    listening = false;
    el.mic.classList.remove('on');
    toast('I cannot reach your microphone. Type instead?');
    setPhase('idle');
    setBusy(false);
    return;
  }

  if (!liveLine) liveLine = addLine('live', 'Listening…');
  micTimer = setTimeout(stopListening, 30_000);
}

function stopListening() {
  clearTimeout(micTimer);
  micTimer = null;
  if (!listening) return;
  listening = false;
  el.mic.classList.remove('on');
  mic.stop();
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'mic_stop' }));
  }
  setPhase('thinking');
}

/* ---------------------------------------------------------------- the intro */

async function intro() {
  await character.playOnce('fall-flat', { fade: 0 });
  await character.playOnce('stand-up', { ...STAND_UP, fade: 0.15 });
  setPhase('idle');
}

/* ----------------------------------------------------------------- wiring up */

el.composer.addEventListener('submit', (event) => {
  event.preventDefault();
  say(el.input.value);
});

el.dance.addEventListener('click', () => {
  if (dancing) stopDance();
  else startDance();
});

el.mic.addEventListener('pointerdown', (event) => {
  event.preventDefault();
  startListening();
});
for (const name of ['pointerup', 'pointercancel', 'pointerleave']) {
  el.mic.addEventListener(name, () => stopListening());
}

const typing = () => {
  const node = document.activeElement;
  return (
    node &&
    (node.tagName === 'INPUT' ||
      node.tagName === 'TEXTAREA' ||
      node.isContentEditable)
  );
};

window.addEventListener('keydown', (event) => {
  if (event.code !== 'Space' || event.repeat || typing() || el.mic.hidden) return;
  event.preventDefault();
  startListening();
});

window.addEventListener('keyup', (event) => {
  if (event.code !== 'Space' || el.mic.hidden) return;
  stopListening();
});

window.addEventListener('blur', () => stopListening());

el.gateBtn.addEventListener('click', async () => {
  el.gateBtn.disabled = true;
  await speaker.unlock();
  el.gate.classList.add('off');
  started = true;

  await intro();

  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'start' }));
  } else {
    wantsGreeting = true;
  }
});

/* ------------------------------------------------------------------ lift-off */

/** Handy in the browser console (F12) when something looks wrong. */
window.monty = {
  character,
  speaker,
  mic,
  moves,
  snapshot: () => ({
    animation: character.currentName,
    busy,
    dancing,
    listening,
    chunks: mic.chunks,
    socket: socket?.readyState,
    speaking: speaker.speaking,
    mouth: { ...character.mouth },
    emotion: { ...character.emotion },
  }),
};

async function boot() {
  setBusy(true); // nothing to say until he is on his feet
  character.visemeSource = () => speaker.viseme();
  character.start();

  try {
    const response = await fetch('/api/character');
    applyProfile(await response.json());
  } catch (err) {
    console.error('could not read the character sheet', err);
    el.gateSub.textContent = 'The server is not answering. Is it running?';
    return;
  }

  try {
    await character.load(profile.avatar, CLIPS, (ratio, label) => {
      el.barFill.style.width = `${Math.round(ratio * 100)}%`;
      el.gateSub.textContent = label;
    });
  } catch (err) {
    console.error('the avatar did not load', err);
    el.gateSub.textContent = 'The avatar did not load. Check the console (F12).';
    return;
  }

  character.play('idle', { fade: 0 });
  connect();

  el.gateSub.textContent = profile.title;
  el.gateBtn.disabled = false;
  el.gateBtn.focus({ preventScroll: true });
}

boot();
