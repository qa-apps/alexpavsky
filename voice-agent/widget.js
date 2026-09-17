/* Local Voice Agent — embeddable voice-only widget.
 * A pure voice experience (no chat): a floating, animated "Voice Agent" orb
 * that, when tapped, opens a full voice stage where you just talk to a living
 * circle — like ChatGPT voice mode. Rendered inside a Shadow DOM so the host
 * site's CSS cannot touch it and its CSS cannot leak out.
 *
 * Pipeline per turn: mic -> STT -> RAG brain -> Kokoro TTS -> audio.
 * Hands-free: it listens, auto-stops when you pause, answers, then listens
 * again — and you can cut in by voice while it's speaking (or tap the orb).
 * Tap End to close.
 *
 * Embed:  <script src="https://voice.example.com/widget.js" defer></script>
 * Config (optional, set before the script loads):
 *   window.VOICE_AGENT_API = "https://voice.example.com"   // backend origin
 *   window.VOICE_AGENT_POS = { right: 20, bottom: 148 }     // launcher offset (px)
 *   window.VOICE_AGENT_OPEN = true                          // start opened
 */
(function () {
  if (window.__voiceAgentLoaded) return;
  window.__voiceAgentLoaded = true;

  var SELF = (document.currentScript && document.currentScript.src) || '';
  var API = (window.VOICE_AGENT_API || '').replace(/\/$/, '');
  if (!API && SELF) { try { API = new URL(SELF).origin; } catch (e) { API = ''; } }
  var u = function (p) { return API ? API + p : p; };
  var POS = window.VOICE_AGENT_POS || { right: 20, bottom: 148 };

  var host = document.createElement('div');
  host.id = 'va-voice-agent';
  host.setAttribute('style', 'all: initial;');
  (document.body || document.documentElement).appendChild(host);
  var root = host.attachShadow({ mode: 'open' });

  root.innerHTML =
    '<style>' + CSS() + '</style>' +
    // --- collapsed launcher: animated orb + "Voice Agent" label ---
    '<button class="launcher" part="launcher" aria-label="Talk to the Voice Agent">' +
    '  <span class="l-halo"></span>' +
    '  <span class="l-wave"></span><span class="l-wave d2"></span><span class="l-wave d3"></span>' +
    '  <span class="l-orb"><span class="l-blob b1"></span><span class="l-blob b2"></span>' +
    '    <span class="bars"><i></i><i></i><i></i><i></i><i></i></span>' +
    '  </span>' +
    '  <span class="l-label">Voice Agent</span>' +
    '</button>' +
    // --- expanded voice stage: the living circle ---
    '<section class="stage" aria-hidden="true" aria-label="Voice Agent">' +
    '  <button class="end" aria-label="End voice session">' +
    '    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
    '  </button>' +
    '  <div class="brand-min">Voice Agent</div>' +
    '  <div class="orb-wrap">' +
    '    <span class="wave w1"></span><span class="wave w2"></span><span class="wave w3"></span>' +
    '    <button class="orb" aria-label="Tap to talk or interrupt">' +
    '      <span class="glow"></span>' +
    '      <span class="blob a"></span><span class="blob b"></span><span class="blob c"></span>' +
    '      <span class="sheen"></span>' +
    '    </button>' +
    '  </div>' +
    '  <div class="status">connecting…</div>' +
    '  <div class="hint">Tap once, then just talk — hands-free</div>' +
    '</section>';

  var $ = function (s) { return root.querySelector(s); };
  var launcher = $('.launcher'), stage = $('.stage'), orb = $('.orb');
  var statusEl = $('.status'), hintEl = $('.hint');

  var state = 'idle';            // idle | listening | thinking | speaking | offline
  var open = false;
  var positionFrame = 0;
  var mediaRec = null, chunks = [], stream = null;
  var player = new Audio();
  player.crossOrigin = 'anonymous';
  var abortCtl = null;
  var level = 0, levelRaf = 0;   // 0..1 audio level driving the orb
  var speakAnalyser = null, speakData = null, speakSrcNode = null, sharedCtx = null;
  var bargeStream = null, bargeCtx = null, bargeRaf = 0;  // voice barge-in mic

  player.addEventListener('ended', function () { if (state === 'speaking') { if (open) startRec(); else setState('idle'); } });
  player.addEventListener('pause', function () { /* level loop checks player.paused */ });

  var LABELS = {
    idle: 'Tap to start', listening: 'Listening…', thinking: 'Thinking…',
    speaking: 'Speaking…', offline: 'Offline', connecting: 'connecting…'
  };
  var HINTS = {
    idle: 'Tap once, then just talk — hands-free',
    listening: 'Just talk — I answer when you pause',
    thinking: 'One moment…', speaking: 'Speak or tap to interrupt',
    offline: 'Voice service is unavailable right now', connecting: ''
  };

  function positionLauncherWithFooter() {
    positionFrame = 0;
    if (open) {
      launcher.style.removeProperty('bottom');
      launcher.style.removeProperty('left');
      launcher.style.removeProperty('right');
      return;
    }

    var forum = document.querySelector('.footer-forum-wrap');
    if (!forum || window.innerWidth < 1100) {
      launcher.style.removeProperty('bottom');
      launcher.style.removeProperty('left');
      launcher.style.removeProperty('right');
      return;
    }

    var rect = forum.getBoundingClientRect();
    if (rect.bottom <= 0 || rect.top >= window.innerHeight) {
      launcher.style.removeProperty('bottom');
      launcher.style.removeProperty('left');
      launcher.style.removeProperty('right');
      return;
    }

    var launcherHeight = launcher.offsetHeight || 60;
    var launcherWidth = launcher.offsetWidth || 190;
    var targetTop = rect.bottom + 8;
    var targetLeft = rect.left + (rect.width - launcherWidth) / 2;
    var targetBottom = window.innerHeight - targetTop - launcherHeight;
    targetLeft = Math.max(12, Math.min(window.innerWidth - launcherWidth - 12, targetLeft));
    launcher.style.bottom = Math.max(12, targetBottom) + 'px';
    launcher.style.left = targetLeft + 'px';
    launcher.style.right = 'auto';
  }

  function scheduleLauncherPosition() {
    if (positionFrame) return;
    positionFrame = window.requestAnimationFrame(positionLauncherWithFooter);
  }

  window.addEventListener('scroll', scheduleLauncherPosition, { passive: true });
  window.addEventListener('resize', scheduleLauncherPosition);
  scheduleLauncherPosition();

  function setState(s) {
    state = s;
    stage.setAttribute('data-state', s);
    launcher.setAttribute('data-state', s);
    statusEl.textContent = LABELS[s] || '';
    hintEl.textContent = HINTS[s] || '';
    if (s !== 'listening' && s !== 'speaking') setLevel(0);
  }
  function setLevel(v) { level = v; var t = 1 + Math.min(0.5, v) * 0.9; stage.style.setProperty('--level', v.toFixed(3)); orb.style.transform = 'scale(' + (1 + Math.min(0.16, v * 0.28)).toFixed(3) + ')'; }

  /* ---- stage open/close ---- */
  function openStage() {
    open = true;
    scheduleLauncherPosition();
    stage.classList.add('on'); stage.setAttribute('aria-hidden', 'false');
    launcher.classList.add('hidden');
    if (state === 'offline') return;
    startRec();
  }
  function closeStage() {
    open = false;
    cancel(); stopAudio(); stopRec(true);
    stage.classList.remove('on'); stage.setAttribute('aria-hidden', 'true');
    launcher.classList.remove('hidden');
    scheduleLauncherPosition();
    if (state !== 'offline') setState('idle');
  }
  launcher.addEventListener('click', function () { open ? closeStage() : openStage(); });
  $('.end').addEventListener('click', closeStage);

  /* ---- primary action depends on state (barge-in) ---- */
  function onPrimary() {
    if (state === 'idle') startRec();
    else if (state === 'listening') stopRec(false);
    else if (state === 'thinking') { cancel(); startRec(); }
    else if (state === 'speaking') { stopAudio(); startRec(); }
  }
  orb.addEventListener('click', function (e) { e.stopPropagation(); onPrimary(); });

  /* ---- recording (auto-stops when you pause — no second tap needed) ---- */
  var vad = null, emptyStreak = 0, MAX_EMPTY_REARMS = 4;
  function nowMs() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
  function stopRec(discard) {
    if (vad) vad.discard = !!discard;
    if (mediaRec && state === 'listening') { try { mediaRec.stop(); } catch (e) {} }
  }
  // Silence timeout with the mic still open: re-arm a fresh listen (stay
  // hands-free) instead of ending the session, so a thinking pause never
  // "freezes" the agent into tap-to-talk.
  function rearmRec() {
    if (vad) { vad.discard = true; vad.rearm = true; }
    if (mediaRec && state === 'listening') { try { mediaRec.stop(); } catch (e) {} }
  }
  function startRec() {
    stopAudio();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { setState('idle'); return; }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      stream = s;
      var mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
        : (MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '');
      mediaRec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunks = [];
      mediaRec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      mediaRec.onstop = function () {
        var discarded = vad && vad.discard;
        var rearm = vad && vad.rearm;
        stopVad();
        if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
        if (discarded) {
          // Re-arm keeps the session hands-free after a silent stretch; a real
          // close/interrupt (rearm=false) drops to idle instead.
          if (rearm && open) startRec();
          else setState('idle');
          return;
        }
        var type = (mediaRec && mediaRec.mimeType) || 'audio/webm';
        var ext = type.indexOf('mp4') >= 0 ? '.mp4' : '.webm';
        sendTurn(new Blob(chunks, { type: type }), 'turn' + ext);
      };
      mediaRec.start();
      setState('listening');
      startVad(s);
    }).catch(function (err) { setState('idle'); hintEl.textContent = 'Microphone blocked — allow mic access to talk.'; });
  }

  /* Voice-activity detection: watch the mic level, drive the orb, and auto-stop
     ~1.2s after speech ends. Learns the room's noise floor for ~400ms. */
  function startVad(s) {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      if (ctx.state === 'suspended' && ctx.resume) ctx.resume();
      var analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(s).connect(analyser);
      var buf = new Uint8Array(analyser.fftSize);
      var raf = 0;
      vad = { discard: false, rearm: false, stop: function () { if (raf) cancelAnimationFrame(raf); try { ctx.close(); } catch (e) {} } };
      var t0 = nowMs(), lastVoice = t0, voiced = false, floor = 0.01, floorN = 0;
      var SILENCE_MS = 1200, MAX_MS = 15000, NO_SPEECH_MS = 7000;
      (function loop() {
        if (state !== 'listening' || !vad) return;
        analyser.getByteTimeDomainData(buf);
        var sum = 0;
        for (var i = 0; i < buf.length; i++) { var v = (buf[i] - 128) / 128; sum += v * v; }
        var rms = Math.sqrt(sum / buf.length);
        setLevel(Math.min(1, rms * 6));
        var t = nowMs(), dt = t - t0;
        if (dt < 400) { floor = (floor * floorN + rms) / (floorN + 1); floorN++; }
        var thresh = Math.min(0.09, Math.max(0.02, floor * 2.5));
        if (rms > thresh) { if (!voiced) emptyStreak = 0; voiced = true; lastVoice = t; }
        if (voiced && (t - lastVoice) > SILENCE_MS) { stopRec(false); return; }
        else if (!voiced && dt > NO_SPEECH_MS) {
          // No speech yet: stay hands-free by re-arming, up to a cap, then idle
          // (so we don't hold the mic open forever if the user walked away).
          if (emptyStreak < MAX_EMPTY_REARMS) { emptyStreak++; rearmRec(); }
          else { emptyStreak = 0; stopRec(true); }
          return;
        }
        else if (dt > MAX_MS) { stopRec(false); return; }
        raf = requestAnimationFrame(loop);
      })();
    } catch (e) { /* manual tap-to-stop remains the fallback */ }
  }
  function stopVad() { if (vad) { vad.stop(); vad = null; } }

  /* ---- audio playback (data: URI -> blob URL for strict CSPs) ---- */
  var lastAudio = null;
  function toBlobUrl(dataUri) {
    try {
      var comma = dataUri.indexOf(',');
      var head = dataUri.slice(5, comma);
      var mime = head.split(';')[0] || 'audio/wav';
      var bin = atob(dataUri.slice(comma + 1));
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return URL.createObjectURL(new Blob([bytes], { type: mime }));
    } catch (e) { return null; }
  }
  function setLastAudio(url) {
    if (lastAudio && lastAudio !== url) { try { URL.revokeObjectURL(lastAudio); } catch (e) {} }
    lastAudio = url;
  }
  function stopAudio() {
    stopBargeListen();
    try { player.pause(); } catch (e) {}
    try { player.currentTime = 0; } catch (e) {}
    if (levelRaf) { cancelAnimationFrame(levelRaf); levelRaf = 0; }
    if (state === 'speaking') setState('idle');
  }

  /* ---- voice barge-in: listen while speaking; if the user talks, cut in ----
     Opens the mic with the browser's echo canceller on, so the agent's own TTS
     (played through the speakers) is subtracted from the mic and can't
     self-interrupt. Requires a short sustained burst of real speech before
     barging, to shrug off any residual echo. Tap-to-interrupt still works too. */
  var BARGE_THRESH = 0.05, BARGE_SUSTAIN_MS = 340, BARGE_GRACE_MS = 600;
  function startBargeListen() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
    navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    }).then(function (s) {
      if (state !== 'speaking') { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      bargeStream = s;
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) { stopBargeListen(); return; }
      bargeCtx = new Ctx();
      if (bargeCtx.state === 'suspended' && bargeCtx.resume) bargeCtx.resume();
      var an = bargeCtx.createAnalyser();
      an.fftSize = 512;
      bargeCtx.createMediaStreamSource(s).connect(an);
      var buf = new Uint8Array(an.fftSize);
      var t0 = nowMs(), last = t0, voicedMs = 0;
      (function loop() {
        if (state !== 'speaking' || !bargeStream) return;
        an.getByteTimeDomainData(buf);
        var sum = 0;
        for (var i = 0; i < buf.length; i++) { var v = (buf[i] - 128) / 128; sum += v * v; }
        var rms = Math.sqrt(sum / buf.length);
        var t = nowMs(), dt = t - last; last = t;
        if (t - t0 < BARGE_GRACE_MS) { bargeRaf = requestAnimationFrame(loop); return; }
        if (rms > BARGE_THRESH) voicedMs += dt;
        else voicedMs = Math.max(0, voicedMs - dt * 0.6);
        if (voicedMs > BARGE_SUSTAIN_MS) { bargeIn(); return; }
        bargeRaf = requestAnimationFrame(loop);
      })();
    }).catch(function () { /* no barge mic — tap-to-interrupt still works */ });
  }
  function stopBargeListen() {
    if (bargeRaf) { cancelAnimationFrame(bargeRaf); bargeRaf = 0; }
    if (bargeStream) { bargeStream.getTracks().forEach(function (t) { t.stop(); }); bargeStream = null; }
    if (bargeCtx) { try { bargeCtx.close(); } catch (e) {} bargeCtx = null; }
  }
  function bargeIn() { stopBargeListen(); stopAudio(); startRec(); }
  function ensureSpeakAnalyser() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return false;
      if (!sharedCtx) sharedCtx = new Ctx();
      if (sharedCtx.state === 'suspended' && sharedCtx.resume) sharedCtx.resume();
      if (!speakSrcNode) {
        speakSrcNode = sharedCtx.createMediaElementSource(player);
        speakAnalyser = sharedCtx.createAnalyser();
        speakAnalyser.fftSize = 256;
        speakSrcNode.connect(speakAnalyser);
        speakAnalyser.connect(sharedCtx.destination);
        speakData = new Uint8Array(speakAnalyser.fftSize);
      }
      return true;
    } catch (e) { return false; }
  }
  function driveSpeakLevel() {
    if (state !== 'speaking') return;
    if (speakAnalyser && !player.paused) {
      speakAnalyser.getByteTimeDomainData(speakData);
      var sum = 0;
      for (var i = 0; i < speakData.length; i++) { var v = (speakData[i] - 128) / 128; sum += v * v; }
      setLevel(Math.min(1, Math.sqrt(sum / speakData.length) * 3.2));
    } else {
      // Fallback: gentle synthetic pulse if analyser is unavailable.
      setLevel(0.28 + 0.22 * Math.abs(Math.sin(nowMs() / 180)));
    }
    levelRaf = requestAnimationFrame(driveSpeakLevel);
  }
  function playAudio(src) {
    if (!src) { if (open) startRec(); else setState('idle'); return; }
    var hasAnalyser = ensureSpeakAnalyser();
    player.src = src;
    setState('speaking');
    player.play().then(function () {
      if (levelRaf) cancelAnimationFrame(levelRaf);
      driveSpeakLevel();
      startBargeListen();   // listen for the user cutting in by voice
    }).catch(function () { if (open) setState('idle'); });
  }

  /* ---- requests ---- */
  function cancel() { if (abortCtl) { try { abortCtl.abort(); } catch (e) {} abortCtl = null; } }
  function sendTurn(blob, filename) {
    setState('thinking');
    abortCtl = new AbortController();
    var fd = new FormData(); fd.append('file', blob, filename);
    fetch(u('/api/turn'), { method: 'POST', body: fd, signal: abortCtl.signal })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var audioUrl = d.audio_b64 ? toBlobUrl(d.audio_b64) : null;
        setLastAudio(audioUrl);
        if (audioUrl) playAudio(audioUrl);
        else if (open) startRec();
        else setState('idle');
      })
      .catch(function (e) {
        if (e.name !== 'AbortError') { hintEl.textContent = 'Something went wrong — tap the circle to retry.'; setState('idle'); }
      });
  }

  /* ---- health ---- */
  setState('connecting');
  fetch(u('/api/health')).then(function (r) { return r.json(); })
    .then(function () { if (state === 'connecting') setState('idle'); })
    .catch(function () { setState('offline'); });

  if (window.VOICE_AGENT_OPEN) setTimeout(openStage, 300);

  function CSS() {
    return [
      ':host{ all: initial; }',
      '*{ box-sizing:border-box; margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }',
      '[hidden]{ display:none !important; }',
      '.hidden{ opacity:0 !important; pointer-events:none !important; transform:translateY(10px) scale(.9) !important; }',

      /* ---------- launcher ---------- */
      '.launcher{ position:fixed; right:' + POS.right + 'px; bottom:' + POS.bottom + 'px; z-index:2147483000;',
      '  display:flex; align-items:center; gap:11px; padding:8px 16px 8px 8px; border:none; cursor:pointer;',
      '  border-radius:999px; color:#eaf0ff; background:rgba(16,20,38,.72);',
      '  -webkit-backdrop-filter:blur(14px) saturate(1.4); backdrop-filter:blur(14px) saturate(1.4);',
      '  box-shadow:0 14px 40px -12px rgba(90,70,255,.65), inset 0 0 0 1px rgba(255,255,255,.10);',
      '  transition:transform .25s cubic-bezier(.2,.8,.2,1), box-shadow .3s, opacity .3s;',
      '  animation:floaty 5.5s ease-in-out infinite; }',
      '.launcher:hover{ transform:translateY(-2px) scale(1.03); box-shadow:0 20px 52px -12px rgba(120,80,255,.85), inset 0 0 0 1px rgba(255,255,255,.16); }',
      '.launcher:focus-visible{ outline:2px solid #9d7bff; outline-offset:3px; }',

      '.l-orb{ position:relative; width:44px; height:44px; border-radius:50%; overflow:hidden; flex:0 0 auto; z-index:2;',
      '  background:radial-gradient(120% 120% at 30% 25%, #8ea2ff 0%, #7a5cff 40%, #4a2fb0 100%);',
      '  box-shadow:inset 0 1px 3px rgba(255,255,255,.5), inset 0 -6px 14px rgba(20,0,60,.55); }',
      '.l-blob{ position:absolute; border-radius:50%; filter:blur(6px); opacity:.9; mix-blend-mode:screen; }',
      '.l-blob.b1{ width:36px; height:36px; left:-6px; top:-4px; background:radial-gradient(circle,#39e6d0,transparent 65%); animation:spin1 6s linear infinite; }',
      '.l-blob.b2{ width:34px; height:34px; right:-8px; bottom:-6px; background:radial-gradient(circle,#c66bff,transparent 65%); animation:spin2 8s linear infinite; }',
      '.bars{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; gap:2.5px; z-index:2; }',
      '.bars i{ width:2.6px; border-radius:3px; background:rgba(255,255,255,.95); box-shadow:0 0 6px rgba(255,255,255,.6); animation:bar 1.1s ease-in-out infinite; }',
      '.bars i:nth-child(1){ height:8px; animation-delay:0s } .bars i:nth-child(2){ height:15px; animation-delay:.12s }',
      '.bars i:nth-child(3){ height:20px; animation-delay:.24s } .bars i:nth-child(4){ height:14px; animation-delay:.36s }',
      '.bars i:nth-child(5){ height:9px; animation-delay:.48s }',
      '.l-label{ font-size:14px; font-weight:650; letter-spacing:.2px; white-space:nowrap; z-index:2;',
      '  background:linear-gradient(90deg,#eaf0ff,#c8b9ff); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }',
      '.l-halo{ position:absolute; inset:-2px; border-radius:999px; z-index:0; pointer-events:none;',
      '  background:linear-gradient(120deg, rgba(122,92,255,.0), rgba(122,92,255,.35), rgba(57,230,208,.28), rgba(198,107,255,.0)); background-size:300% 100%;',
      '  filter:blur(6px); opacity:.75; animation:hue 6s linear infinite; }',
      '.l-wave{ position:absolute; left:8px; bottom:8px; width:44px; height:44px; border-radius:50%; z-index:0; pointer-events:none;',
      '  border:1.5px solid rgba(140,120,255,.55); animation:ripple 2.8s ease-out infinite; }',
      '.l-wave.d2{ animation-delay:.9s; border-color:rgba(57,230,208,.45); } .l-wave.d3{ animation-delay:1.8s; border-color:rgba(198,107,255,.4); }',

      /* ---------- voice stage ---------- */
      '.stage{ position:fixed; right:clamp(14px, ' + POS.right + 'px, calc(100vw - 354px)); bottom:' + POS.bottom + 'px; z-index:2147483000;',
      '  width:min(340px, calc(100vw - 28px)); padding:22px 22px 24px; --level:0;',
      '  display:flex; flex-direction:column; align-items:center; gap:4px;',
      '  border-radius:26px; color:#eaeefb; background:radial-gradient(130% 100% at 50% 0%, rgba(41,32,84,.92), rgba(12,15,30,.94) 70%);',
      '  -webkit-backdrop-filter:blur(22px) saturate(1.35); backdrop-filter:blur(22px) saturate(1.35);',
      '  border:1px solid rgba(255,255,255,.10); box-shadow:0 36px 90px -24px rgba(0,0,0,.78), inset 0 1px 0 rgba(255,255,255,.08);',
      '  opacity:0; transform:translateY(16px) scale(.96); transform-origin:bottom right; pointer-events:none;',
      '  transition:opacity .28s ease, transform .32s cubic-bezier(.2,.85,.25,1); }',
      '.stage.on{ opacity:1; transform:none; pointer-events:auto; }',
      '.end{ position:absolute; top:12px; right:12px; width:32px; height:32px; border-radius:10px; border:none; cursor:pointer;',
      '  display:flex; align-items:center; justify-content:center; color:#aeb6d6; background:rgba(255,255,255,.06); transition:background .15s,color .15s; }',
      '.end svg{ width:16px; height:16px; } .end:hover{ background:rgba(255,255,255,.14); color:#fff; }',
      '.end:focus-visible{ outline:2px solid #9d7bff; outline-offset:2px; }',
      '.brand-min{ font-size:12px; font-weight:600; letter-spacing:1.4px; text-transform:uppercase; color:#9aa3c9; margin-bottom:6px; align-self:center; }',

      '.orb-wrap{ position:relative; width:190px; height:190px; display:flex; align-items:center; justify-content:center; margin:6px 0 4px; }',
      '.wave{ position:absolute; width:120px; height:120px; border-radius:50%; border:1.5px solid rgba(130,110,255,.4); pointer-events:none;',
      '  opacity:0; animation:none; }',
      '.stage[data-state="listening"] .wave, .stage[data-state="speaking"] .wave{ animation:sonar 2.6s ease-out infinite; }',
      '.stage[data-state="listening"] .wave{ border-color:rgba(120,150,255,.5); }',
      '.stage[data-state="speaking"] .wave{ border-color:rgba(57,220,190,.5); }',
      '.wave.w2{ animation-delay:.85s !important; } .wave.w3{ animation-delay:1.7s !important; }',

      '.orb{ position:relative; width:132px; height:132px; border-radius:50%; border:none; cursor:pointer; padding:0; overflow:hidden;',
      '  background:#0a0d1c; box-shadow:inset 0 2px 6px rgba(255,255,255,.30), inset 0 -14px 30px rgba(10,0,40,.65), 0 18px 50px -12px rgba(90,60,255,.7);',
      '  transition:transform .12s ease-out; will-change:transform; }',
      '.orb:focus-visible{ outline:3px solid rgba(157,123,255,.8); outline-offset:4px; }',
      '.glow{ position:absolute; inset:-40%; border-radius:50%; z-index:0; pointer-events:none; filter:blur(22px); opacity:calc(.55 + var(--level)*.45);',
      '  background:radial-gradient(circle at 50% 50%, rgba(122,92,255,.55), rgba(57,220,190,.28) 45%, transparent 70%); }',
      '.blob{ position:absolute; border-radius:50%; filter:blur(14px); mix-blend-mode:screen; opacity:.92; }',
      '.blob.a{ width:120px; height:120px; left:-14px; top:-20px; background:radial-gradient(circle,#6b8bff,transparent 62%); animation:drift1 9s ease-in-out infinite; }',
      '.blob.b{ width:120px; height:120px; right:-20px; top:6px; background:radial-gradient(circle,#39e6d0,transparent 62%); animation:drift2 11s ease-in-out infinite; }',
      '.blob.c{ width:120px; height:120px; left:2px; bottom:-24px; background:radial-gradient(circle,#c66bff,transparent 62%); animation:drift3 13s ease-in-out infinite; }',
      '.sheen{ position:absolute; inset:0; border-radius:50%; z-index:2; pointer-events:none;',
      '  background:radial-gradient(120% 90% at 34% 22%, rgba(255,255,255,.55), rgba(255,255,255,.06) 34%, transparent 55%); }',
      '.stage[data-state="thinking"] .blob{ animation-duration:2.6s, 3s, 3.4s; }',
      '.stage[data-state="thinking"] .orb{ animation:thinkpulse 1.4s ease-in-out infinite; }',
      '.stage[data-state="idle"] .glow, .stage[data-state="connecting"] .glow, .stage[data-state="offline"] .glow{ opacity:.4; }',
      '.stage[data-state="offline"] .blob{ filter:blur(14px) grayscale(.8); opacity:.5; }',

      '.status{ font-size:19px; font-weight:600; letter-spacing:.2px; margin-top:8px; min-height:24px; text-align:center;',
      '  background:linear-gradient(90deg,#eef1ff,#c9bcff); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }',
      '.hint{ font-size:12.5px; color:#9098bb; text-align:center; min-height:17px; line-height:1.4; max-width:260px; }',

      /* ---------- keyframes ---------- */
      '@keyframes floaty{ 0%,100%{ transform:translateY(0) } 50%{ transform:translateY(-5px) } }',
      '@keyframes bar{ 0%,100%{ transform:scaleY(.45) } 50%{ transform:scaleY(1) } }',
      '@keyframes hue{ 0%{ background-position:0% 0 } 100%{ background-position:300% 0 } }',
      '@keyframes ripple{ 0%{ transform:scale(.7); opacity:.7 } 100%{ transform:scale(1.9); opacity:0 } }',
      '@keyframes spin1{ 0%{ transform:rotate(0) translate(3px,0) } 100%{ transform:rotate(360deg) translate(3px,0) } }',
      '@keyframes spin2{ 0%{ transform:rotate(0) translate(-3px,0) } 100%{ transform:rotate(-360deg) translate(-3px,0) } }',
      '@keyframes sonar{ 0%{ transform:scale(.75); opacity:.6 } 100%{ transform:scale(1.55); opacity:0 } }',
      '@keyframes drift1{ 0%,100%{ transform:translate(0,0) scale(1) } 33%{ transform:translate(18px,14px) scale(1.12) } 66%{ transform:translate(6px,-12px) scale(.95) } }',
      '@keyframes drift2{ 0%,100%{ transform:translate(0,0) scale(1) } 33%{ transform:translate(-16px,10px) scale(.94) } 66%{ transform:translate(-6px,-14px) scale(1.14) } }',
      '@keyframes drift3{ 0%,100%{ transform:translate(0,0) scale(1) } 33%{ transform:translate(10px,-16px) scale(1.1) } 66%{ transform:translate(-12px,8px) scale(.96) } }',
      '@keyframes thinkpulse{ 0%,100%{ box-shadow:inset 0 2px 6px rgba(255,255,255,.3), inset 0 -14px 30px rgba(10,0,40,.65), 0 18px 50px -12px rgba(90,60,255,.5) } 50%{ box-shadow:inset 0 2px 6px rgba(255,255,255,.3), inset 0 -14px 30px rgba(10,0,40,.65), 0 18px 58px -8px rgba(120,90,255,.95) } }',

      '@media (prefers-reduced-motion: reduce){',
      '  .launcher, .l-blob, .bars i, .l-halo, .l-wave, .wave, .blob, .orb{ animation:none !important; }',
      '  .stage, .launcher{ transition:opacity .2s ease; } }'
    ].join('\n');
  }
})();
