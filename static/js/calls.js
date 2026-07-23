/*
 * Voice/video calling over WebRTC.
 *
 * Media (audio/video) flows peer-to-peer directly between the two browsers
 * (or via a TURN relay as a fallback) — it never touches our server, so
 * call quality isn't affected by our hosting. Our server's only job is
 * "signaling": helping the two browsers exchange one SDP offer and one SDP
 * answer to set the call up.
 *
 * We do that signaling over plain HTTP polling rather than WebSockets, on
 * purpose — it keeps this deployable on literally any free host (Render,
 * PythonAnywhere, whatever), no ASGI server or Redis channel layer needed.
 * The trade-off is a little added latency (~1-1.5s) in the "ringing" and
 * "call was answered" moments, which is imperceptible for a phone-call-like
 * flow. Once the call is connected, everything after that is real-time P2P.
 *
 * We also use "non-trickle" ICE: instead of streaming ICE candidates one by
 * one over a live channel, each side waits for its browser to finish
 * gathering all candidates, then sends one complete SDP blob. Simpler, and
 * fine for a 1:1 calling feature like this one.
 */
(function () {
  const scriptTag = document.currentScript;
  if (!scriptTag) return;

  const iceUrl = scriptTag.dataset.iceUrl;
  const incomingUrl = scriptTag.dataset.incomingUrl;
  const csrfToken = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';

  const RING_TIMEOUT_MS = 30000;
  const RINGING_POLL_MS = 1500;
  const ACTIVE_POLL_MS = 4000;

  const bannerRoot = document.getElementById('call-banner-root');
  const overlayRoot = document.getElementById('call-overlay-root');

  // ----- state -----------------------------------------------------------
  let pc = null;
  let localStream = null;
  let currentCall = null; // { id, kind, isCaller, otherUsername, otherAvatarColor }
  let pollTimer = null;
  let ringTimeoutTimer = null;
  let callTimerInterval = null;
  let callStartedAt = null;
  let ringtoneCtx = null;
  let ringtoneInterval = null;
  let micEnabled = true;
  let camEnabled = true;

  function post(url, fields) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken },
      body: new URLSearchParams(fields).toString(),
    })
      .then((res) => res.json().then((data) => ({ ok: res.ok, status: res.status, data })))
      .catch((e) => {
        // Network failure, or a non-JSON response (e.g. a transient proxy/error page) —
        // never let this throw, or it silently kills whichever poll loop called it.
        console.error('post failed:', url, e);
        return { ok: false, status: 0, data: null };
      });
  }

  function get(url) {
    return fetch(url)
      .then((res) => res.json().then((data) => ({ ok: res.ok, status: res.status, data })))
      .catch((e) => {
        console.error('get failed:', url, e);
        return { ok: false, status: 0, data: null };
      });
  }

  // ----- ringtone (pure Web Audio, no files/API needed) -------------------
  function startRingtone() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      ringtoneCtx = new Ctx();
      const beep = () => {
        if (!ringtoneCtx) return;
        const osc = ringtoneCtx.createOscillator();
        const gain = ringtoneCtx.createGain();
        osc.frequency.value = 880;
        gain.gain.value = 0.16;
        osc.connect(gain).connect(ringtoneCtx.destination);
        osc.start();
        osc.stop(ringtoneCtx.currentTime + 0.22);
      };
      beep();
      ringtoneInterval = setInterval(beep, 1100);
    } catch (e) { /* autoplay restrictions etc — banner UI still works without sound */ }
  }
  function stopRingtone() {
    clearInterval(ringtoneInterval);
    ringtoneInterval = null;
    if (ringtoneCtx) { try { ringtoneCtx.close(); } catch (e) {} ringtoneCtx = null; }
  }

  // ----- WebRTC helpers -----------------------------------------------
  // Setting `srcObject` alone is not reliably enough to start playback —
  // browser autoplay policies (especially for *unmuted* video, which is
  // what carries the other person's audio here) frequently block it
  // silently, with no visible error, which is exactly why a call could
  // "connect" but show no video/play no sound. We call .play() explicitly
  // and, if the browser refuses because the element isn't muted, we mute
  // it and retry so the call still connects — audio can be restored via
  // the mic/cam controls' surrounding UI once the user has interacted.
  function attachRemoteStream(videoEl, stream) {
    if (videoEl.srcObject !== stream) videoEl.srcObject = stream;
    const playPromise = videoEl.play();
    if (playPromise && typeof playPromise.catch === 'function') {
      playPromise.catch(() => {
        if (!videoEl.muted) {
          videoEl.muted = true;
          videoEl.play().catch(() => {});
        }
      });
    }
  }

  function waitForIceGatheringComplete(peerConnection) {
    if (peerConnection.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      const check = () => {
        if (peerConnection.iceGatheringState === 'complete') {
          peerConnection.removeEventListener('icegatheringstatechange', check);
          resolve();
        }
      };
      peerConnection.addEventListener('icegatheringstatechange', check);
      setTimeout(resolve, 4000); // don't let a stuck TURN allocation hang the call forever
    });
  }

  const FALLBACK_ICE_SERVERS = [{ urls: 'stun:stun.l.google.com:19302' }];

  function sanitizeIceServers(list) {
    if (!Array.isArray(list)) return FALLBACK_ICE_SERVERS;
    const clean = list.filter((s) => s && (typeof s.urls === 'string' || Array.isArray(s.urls)));
    return clean.length ? clean : FALLBACK_ICE_SERVERS;
  }

  async function createPeerConnection() {
    const res = await get(iceUrl);
    const iceServers = sanitizeIceServers(res.ok && res.data.ice_servers);
    let conn;
    try {
      conn = new RTCPeerConnection({ iceServers });
    } catch (e) {
      // A malformed ice server entry throws synchronously here — never let
      // that take down the whole call silently. Retry STUN-only.
      console.error('RTCPeerConnection construction failed, retrying STUN-only:', e);
      conn = new RTCPeerConnection({ iceServers: FALLBACK_ICE_SERVERS });
    }
    conn.addEventListener('track', (e) => {
      const remoteVideo = document.getElementById('call-remote-video');
      if (!remoteVideo) return; // overlay isn't rendered yet — renderOverlay('active') re-attaches from getReceivers()
      const stream = e.streams[0] || new MediaStream([e.track]);
      attachRemoteStream(remoteVideo, stream);
    });
    conn.addEventListener('connectionstatechange', () => {
      if (['failed', 'closed'].includes(conn.connectionState) && currentCall) {
        hangUp();
      }
    });
    return conn;
  }

  function stopLocalMedia() {
    if (localStream) {
      localStream.getTracks().forEach((t) => t.stop());
      localStream = null;
    }
  }

  function teardown() {
    stopRingtone();
    clearTimeout(ringTimeoutTimer);
    clearTimeout(pollTimer);
    clearInterval(callTimerInterval);
    ringTimeoutTimer = pollTimer = callTimerInterval = null;
    stopLocalMedia();
    if (pc) { try { pc.close(); } catch (e) {} pc = null; }
    currentCall = null;
    micEnabled = camEnabled = true;
    renderBanner(null);
    renderOverlay(null);
  }

  // ----- outgoing call ---------------------------------------------------
  let callSetupInProgress = false; // true from the first click until currentCall is set (or setup fails)

  async function startCall(username, kind) {
    if (currentCall || callSetupInProgress) return; // already on/starting a call — "line busy" guard
    callSetupInProgress = true;

    try {
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: kind === 'video' });
      } catch (e) {
        alert("Couldn't access your microphone/camera. Check your browser's permission settings and try again.");
        return;
      }
      localStream = stream;

      let localPc;
      try {
        localPc = await createPeerConnection();
        stream.getTracks().forEach((t) => localPc.addTrack(t, stream));

        const offer = await localPc.createOffer();
        await localPc.setLocalDescription(offer);
        await waitForIceGatheringComplete(localPc);
      } catch (e) {
        console.error('startCall setup failed:', e);
        pc = localPc || null;
        teardown();
        alert('Could not start the call. Please try again.');
        return;
      }
      pc = localPc; // only now published — nothing else touches shared `pc` until this point

      const res = await post(`/calls/start/${encodeURIComponent(username)}/`, {
        kind,
        offer_sdp: pc.localDescription.sdp,
      });

      if (!res.ok) {
        teardown();
        if (res.status === 409) alert("You're already on a call.");
        else alert('Could not start the call. Please try again.');
        return;
      }

      currentCall = {
        id: res.data.id,
        kind: res.data.kind,
        isCaller: true,
        otherUsername: res.data.other_username,
        otherAvatarColor: res.data.other_avatar_color,
        otherAvatarUrl: res.data.other_avatar_url,
      };
      renderOverlay('outgoing-ringing');
      ringTimeoutTimer = setTimeout(() => {
        if (currentCall && currentCall.id === res.data.id) {
          post(`/calls/${res.data.id}/end/`, {});
          teardown();
        }
      }, RING_TIMEOUT_MS);
      pollOutgoingStatus();
    } finally {
      callSetupInProgress = false;
    }
  }

  async function pollOutgoingStatus() {
    if (!currentCall || !currentCall.isCaller) return;
    const { data, ok } = await get(`/calls/${currentCall.id}/status/`);
    if (!currentCall) return;
    if (!ok) {
      // Transient network/proxy hiccup — keep ringing and retry rather than dying silently.
      pollTimer = setTimeout(pollOutgoingStatus, RINGING_POLL_MS);
      return;
    }

    if (data.status === 'accepted' && pc && pc.signalingState !== 'stable') {
      clearTimeout(ringTimeoutTimer);
      try {
        await pc.setRemoteDescription({ type: 'answer', sdp: data.answer_sdp });
      } catch (e) {
        console.error('Failed to apply answer SDP:', e, '\nanswer_sdp was:', data.answer_sdp);
        teardown();
        post(`/calls/${currentCall.id}/end/`, {});
        alert('Could not connect the call. Please try again.');
        return;
      }
      enterActiveCall();
      return;
    }
    if (['declined', 'ended', 'missed'].includes(data.status)) {
      const wasDeclined = data.status === 'declined';
      teardown();
      if (wasDeclined) showToast(`${data.other_username} declined the call.`);
      return;
    }
    pollTimer = setTimeout(pollOutgoingStatus, RINGING_POLL_MS);
  }

  // ----- incoming call -----------------------------------------------
  async function checkForIncomingCall() {
    if (currentCall) return; // busy — the poll below will just retry later
    const { ok, data } = await get(incomingUrl);
    if (!ok || !data.call) return;

    const call = data.call;
    currentCall = {
      id: call.id,
      kind: call.kind,
      isCaller: false,
      otherUsername: call.other_username,
      otherAvatarColor: call.other_avatar_color,
      otherAvatarUrl: call.other_avatar_url,
      offerSdp: call.offer_sdp,
    };
    renderBanner(currentCall);
    startRingtone();
    watchIncomingCancelled();
  }

  async function watchIncomingCancelled() {
    if (!currentCall || currentCall.isCaller) return;
    const { ok, data } = await get(`/calls/${currentCall.id}/status/`);
    if (!currentCall) return;
    if (!ok) {
      pollTimer = setTimeout(watchIncomingCancelled, RINGING_POLL_MS);
      return;
    }
    if (data.status !== 'ringing') {
      // caller hung up / call timed out before we responded
      stopRingtone();
      renderBanner(null);
      currentCall = null;
      return;
    }
    pollTimer = setTimeout(watchIncomingCancelled, RINGING_POLL_MS);
  }

  async function acceptIncomingCall() {
    if (!currentCall || callSetupInProgress) return;
    callSetupInProgress = true;
    const call = currentCall;
    stopRingtone();
    clearTimeout(pollTimer);
    renderBanner(null);

    try {
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: call.kind === 'video' });
      } catch (e) {
        alert("Couldn't access your microphone/camera. Check your browser's permission settings and try again.");
        post(`/calls/${call.id}/decline/`, {});
        currentCall = null;
        return;
      }
      localStream = stream;

      let localPc;
      try {
        localPc = await createPeerConnection();
        stream.getTracks().forEach((t) => localPc.addTrack(t, stream));
        await localPc.setRemoteDescription({ type: 'offer', sdp: call.offerSdp });

        const answer = await localPc.createAnswer();
        await localPc.setLocalDescription(answer);
        await waitForIceGatheringComplete(localPc);
      } catch (e) {
        console.error('acceptIncomingCall setup failed:', e, '\noffer_sdp was:', call.offerSdp);
        pc = localPc || null;
        teardown();
        alert('Could not connect the call. Please try again.');
        post(`/calls/${call.id}/decline/`, {});
        return;
      }
      pc = localPc; // only now published — nothing else touches shared `pc` until this point

      const res = await post(`/calls/${call.id}/accept/`, { answer_sdp: pc.localDescription.sdp });
      if (!res.ok) {
        teardown();
        alert('Could not connect the call.');
        return;
      }
      enterActiveCall();
    } finally {
      callSetupInProgress = false;
    }
  }

  function declineIncomingCall() {
    if (!currentCall) return;
    stopRingtone();
    clearTimeout(pollTimer);
    post(`/calls/${currentCall.id}/decline/`, {});
    currentCall = null;
    renderBanner(null);
  }

  // ----- active call ----------------------------------------------------
  function enterActiveCall() {
    callStartedAt = Date.now();
    renderOverlay('active');
    watchActiveCall();
  }

  async function watchActiveCall() {
    if (!currentCall) return;
    const { ok, data } = await get(`/calls/${currentCall.id}/status/`);
    if (ok && data.status !== 'accepted') {
      teardown();
      return;
    }
    pollTimer = setTimeout(watchActiveCall, ACTIVE_POLL_MS);
  }

  function hangUp() {
    if (currentCall) post(`/calls/${currentCall.id}/end/`, {});
    teardown();
  }

  function toggleMic() {
    if (!localStream) return;
    micEnabled = !micEnabled;
    localStream.getAudioTracks().forEach((t) => (t.enabled = micEnabled));
    renderOverlay('active');
  }
  function toggleCam() {
    if (!localStream) return;
    camEnabled = !camEnabled;
    localStream.getVideoTracks().forEach((t) => (t.enabled = camEnabled));
    renderOverlay('active');
  }

  // ----- tiny toast for "call declined" etc ---------------------------
  function showToast(text) {
    const el = document.createElement('div');
    el.className = 'flash info';
    el.style.cssText = 'position:fixed; top:18px; left:50%; transform:translateX(-50%); z-index:1200;';
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3500);
  }

  // ----- rendering --------------------------------------------------------
  function initials(name) {
    return (name || '?').charAt(0).toUpperCase();
  }

  function avatarHtml(username, color, url, extraClass) {
    const cls = `avatar${extraClass ? ' ' + extraClass : ''}`;
    return url
      ? `<img class="${cls}" src="${url}" alt="${username}">`
      : `<div class="${cls}" style="background:${color};">${initials(username)}</div>`;
  }

  function renderBanner(call) {
    if (!call) { bannerRoot.innerHTML = ''; return; }
    bannerRoot.innerHTML = `
      <div class="call-banner">
        <div class="who">
          ${avatarHtml(call.otherUsername, call.otherAvatarColor, call.otherAvatarUrl, 'pulse-ring')}
          <div class="txt">
            <div class="name">${call.otherUsername}</div>
            <div class="sub">Incoming ${call.kind} call…</div>
          </div>
        </div>
        <div class="actions">
          <button class="btn btn-danger" id="call-decline-btn">Decline</button>
          <button class="btn btn-primary" id="call-accept-btn">Accept</button>
        </div>
      </div>`;
    document.getElementById('call-accept-btn').addEventListener('click', acceptIncomingCall);
    document.getElementById('call-decline-btn').addEventListener('click', declineIncomingCall);
  }

  function formatElapsed() {
    if (!callStartedAt) return '00:00';
    const secs = Math.floor((Date.now() - callStartedAt) / 1000);
    const m = String(Math.floor(secs / 60)).padStart(2, '0');
    const s = String(secs % 60).padStart(2, '0');
    return `${m}:${s}`;
  }

  function renderOverlay(mode) {
    clearInterval(callTimerInterval);
    if (!mode || !currentCall) { overlayRoot.innerHTML = ''; return; }

    const call = currentCall;
    const isVideo = call.kind === 'video';

    if (mode === 'outgoing-ringing') {
      if (isVideo && localStream) {
        overlayRoot.innerHTML = `
          <div class="call-overlay">
            <div class="call-video-stage">
              <video id="call-self-preview" autoplay playsinline muted></video>
              <div class="call-ringing-scrim">
                ${avatarHtml(call.otherUsername, call.otherAvatarColor, call.otherAvatarUrl, 'lg pulse-ring')}
                <div class="call-peer-name">${call.otherUsername}</div>
                <div class="call-status-text">Ringing…</div>
              </div>
            </div>
            <div class="call-controls">
              <button class="call-ctrl-btn hangup" id="call-hangup-btn" title="Cancel">✕</button>
            </div>
          </div>`;
        const selfPreview = document.getElementById('call-self-preview');
        if (selfPreview) {
          selfPreview.srcObject = localStream;
          selfPreview.play().catch(() => {}); // muted, so this should never be blocked
        }
      } else {
        overlayRoot.innerHTML = `
          <div class="call-overlay">
            ${avatarHtml(call.otherUsername, call.otherAvatarColor, call.otherAvatarUrl, 'lg pulse-ring')}
            <div class="call-peer-name">${call.otherUsername}</div>
            <div class="call-status-text">Ringing…</div>
            <div class="call-controls">
              <button class="call-ctrl-btn hangup" id="call-hangup-btn" title="Cancel">✕</button>
            </div>
          </div>`;
      }
      document.getElementById('call-hangup-btn').addEventListener('click', hangUp);
      return;
    }

    if (mode === 'active') {
      overlayRoot.innerHTML = `
        <div class="call-overlay">
          <div class="call-status-text" id="call-timer">${formatElapsed()}</div>
          <div class="call-video-stage ${isVideo ? '' : 'audio-only'}">
            ${isVideo
              ? '<video id="call-remote-video" autoplay playsinline></video><video id="call-local-video" autoplay playsinline muted></video>'
              : `${avatarHtml(call.otherUsername, call.otherAvatarColor, call.otherAvatarUrl, 'lg pulse-ring')}
                 <video id="call-remote-video" autoplay playsinline style="display:none;"></video>`}
          </div>
          <div class="call-peer-name">${call.otherUsername}</div>
          <div class="call-controls">
            <button class="call-ctrl-btn ${micEnabled ? '' : 'off'}" id="call-mic-btn" title="Mute">${micEnabled ? '🎙️' : '🔇'}</button>
            ${isVideo ? `<button class="call-ctrl-btn ${camEnabled ? '' : 'off'}" id="call-cam-btn" title="Camera">${camEnabled ? '📷' : '🚫'}</button>` : ''}
            <button class="call-ctrl-btn hangup" id="call-hangup-btn" title="Hang up">📞</button>
          </div>
        </div>`;

      if (isVideo && localStream) {
        const localVideo = document.getElementById('call-local-video');
        if (localVideo) {
          localVideo.srcObject = localStream;
          localVideo.play().catch(() => {}); // already muted, so this should never be blocked
        }
      }
      // Re-attach the remote stream if we already had one (e.g. re-render after toggling mic,
      // or the 'track' event fired before this overlay existed).
      const remoteVideo = document.getElementById('call-remote-video');
      if (remoteVideo && pc) {
        const receivers = pc.getReceivers().filter((r) => r.track);
        if (receivers.length) {
          const stream = new MediaStream(receivers.map((r) => r.track));
          attachRemoteStream(remoteVideo, stream);
          if (!isVideo) remoteVideo.style.display = '';
        }
      }

      document.getElementById('call-hangup-btn').addEventListener('click', hangUp);
      const micBtn = document.getElementById('call-mic-btn');
      if (micBtn) micBtn.addEventListener('click', toggleMic);
      const camBtn = document.getElementById('call-cam-btn');
      if (camBtn) camBtn.addEventListener('click', toggleCam);

      callTimerInterval = setInterval(() => {
        const timerEl = document.getElementById('call-timer');
        if (timerEl) timerEl.textContent = formatElapsed();
      }, 1000);
    }
  }

  // ----- hook into the existing lightweight sidebar poll ------------------
  window.addEventListener('pingback:summary', (e) => {
    if (e.detail.has_incoming_call) checkForIncomingCall();
  });

  // expose a small API for chat room buttons to call
  window.PingbackCalls = { startCall };
})();