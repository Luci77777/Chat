/*
 * Group (mesh) voice/video calling for a single group chat room.
 *
 * Same non-trickle-ICE, HTTP-polling signaling philosophy as static/js/calls.js
 * (see the comment at the top of that file for why), just generalized from
 * one pairwise connection to a full mesh: every participant opens one
 * RTCPeerConnection directly to every other participant. Media never
 * touches our server — only the SDP offer/answer handshake does.
 *
 * Mesh connections grow as N*(N-1)/2, so this is capped server-side
 * (MAX_GROUP_CALL_PARTICIPANTS in calls/views.py) — fine for a small-group
 * call, not meant for large broadcast-style calls.
 *
 * To avoid two participants both trying to be the offerer for the same pair
 * (a "glare" race), we use one fixed rule everywhere: whichever of the two
 * has the lower user id creates the offer. Both the join endpoint and the
 * later participant-list poll apply this same rule, so it stays consistent
 * no matter who joins when.
 */
(function () {
  const root = document.getElementById('group-call-root');
  if (!root) return;

  const groupId = root.dataset.groupId;
  const iceUrl = root.dataset.iceUrl;
  const myUserId = parseInt(root.dataset.myUserId, 10);
  const myUsername = root.dataset.myUsername;
  const myAvatarColor = root.dataset.myAvatarColor;
  const myAvatarUrl = root.dataset.myAvatarUrl;
  const csrfToken = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';

  const barRoot = document.getElementById('group-call-bar');
  const overlayRoot = document.getElementById('group-call-overlay-root');

  const BAR_POLL_MS = 4000;
  const PARTICIPANT_POLL_MS = 3000;
  const SIGNAL_POLL_MS = 1200;
  const HEARTBEAT_MS = 10000;

  // ----- state -------------------------------------------------------------
  let callId = null;
  let kind = 'audio';
  let localStream = null;
  let inCall = false;
  const peers = new Map(); // username -> { pc, userId, avatarColor, avatarUrl }
  let micEnabled = true;
  let camEnabled = true;

  let barTimer = null;
  let participantTimer = null;
  let signalTimer = null;
  let heartbeatTimer = null;

  // ----- tiny fetch helpers (mirrors static/js/calls.js) -------------------
  function post(url, fields) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken },
      body: new URLSearchParams(fields).toString(),
    })
      .then((res) => res.json().then((data) => ({ ok: res.ok, status: res.status, data })))
      .catch((e) => {
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

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s || '';
    return div.innerHTML;
  }

  function initials(username) {
    return (username || '?').slice(0, 1).toUpperCase();
  }

  // ----- WebRTC helpers ------------------------------------------------
  const FALLBACK_ICE_SERVERS = [{ urls: 'stun:stun.l.google.com:19302' }];

  function sanitizeIceServers(list) {
    if (!Array.isArray(list)) return FALLBACK_ICE_SERVERS;
    const clean = list.filter((s) => s && (typeof s.urls === 'string' || Array.isArray(s.urls)));
    return clean.length ? clean : FALLBACK_ICE_SERVERS;
  }

  function waitForIceGatheringComplete(pc) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      const check = () => {
        if (pc.iceGatheringState === 'complete') {
          pc.removeEventListener('icegatheringstatechange', check);
          resolve();
        }
      };
      pc.addEventListener('icegatheringstatechange', check);
      setTimeout(resolve, 4000); // don't let a stuck TURN allocation hang the mesh forever
    });
  }

  function attachStream(mediaEl, stream) {
    if (mediaEl.srcObject !== stream) mediaEl.srcObject = stream;
    const p = mediaEl.play();
    if (p && typeof p.catch === 'function') {
      p.catch(() => {
        if (!mediaEl.muted) {
          mediaEl.muted = true;
          mediaEl.play().catch(() => {});
        }
      });
    }
  }

  async function newPeerConnection(username) {
    const res = await get(iceUrl);
    const iceServers = sanitizeIceServers(res.ok && res.data.ice_servers);
    let pc;
    try {
      pc = new RTCPeerConnection({ iceServers });
    } catch (e) {
      console.error('RTCPeerConnection construction failed, retrying STUN-only:', e);
      pc = new RTCPeerConnection({ iceServers: FALLBACK_ICE_SERVERS });
    }
    pc.addEventListener('track', (e) => {
      const mediaEl = document.getElementById(`gc-media-${cssId(username)}`);
      if (!mediaEl) return;
      const stream = e.streams[0] || new MediaStream([e.track]);
      attachStream(mediaEl, stream);
    });
    pc.addEventListener('connectionstatechange', () => {
      if (['failed', 'closed'].includes(pc.connectionState)) {
        removePeer(username);
      }
    });
    return pc;
  }

  function cssId(username) {
    return String(username).replace(/[^a-zA-Z0-9_-]/g, '_');
  }

  function removePeer(username) {
    const entry = peers.get(username);
    if (!entry) return;
    if (entry.pc) { try { entry.pc.close(); } catch (e) {} }
    peers.delete(username);
    renderOverlay();
  }

  // ----- offer / answer flow -------------------------------------------
  async function initiateOfferTo(peerInfo) {
    if (peers.has(peerInfo.username)) return; // already connecting/connected
    peers.set(peerInfo.username, {
      pc: null, userId: peerInfo.user_id,
      avatarColor: peerInfo.avatar_color, avatarUrl: peerInfo.avatar_url,
    });
    renderOverlay();

    try {
      const pc = await newPeerConnection(peerInfo.username);
      localStream.getTracks().forEach((t) => pc.addTrack(t, localStream));
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGatheringComplete(pc);

      const entry = peers.get(peerInfo.username);
      if (!entry) { pc.close(); return; } // they were removed mid-setup (left the call)
      entry.pc = pc;

      await post(`/calls/group/${callId}/signal/send/`, {
        to_username: peerInfo.username,
        kind: 'offer',
        sdp: pc.localDescription.sdp,
      });
    } catch (e) {
      console.error('initiateOfferTo failed:', e);
      removePeer(peerInfo.username);
    }
  }

  async function handleIncomingOffer(fromUsername, sdp, fromMeta) {
    try {
      let entry = peers.get(fromUsername);
      let pc = entry && entry.pc;
      const isNew = !pc;
      if (isNew) {
        pc = await newPeerConnection(fromUsername);
        localStream.getTracks().forEach((t) => pc.addTrack(t, localStream));
      }
      await pc.setRemoteDescription({ type: 'offer', sdp });
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await waitForIceGatheringComplete(pc);

      peers.set(fromUsername, {
        pc,
        userId: fromMeta ? fromMeta.userId : (entry && entry.userId),
        avatarColor: fromMeta ? fromMeta.avatarColor : (entry && entry.avatarColor),
        avatarUrl: fromMeta ? fromMeta.avatarUrl : (entry && entry.avatarUrl),
      });
      renderOverlay();

      await post(`/calls/group/${callId}/signal/send/`, {
        to_username: fromUsername,
        kind: 'answer',
        sdp: pc.localDescription.sdp,
      });
    } catch (e) {
      console.error('handleIncomingOffer failed:', e);
    }
  }

  async function handleIncomingAnswer(fromUsername, sdp) {
    const entry = peers.get(fromUsername);
    if (!entry || !entry.pc) return;
    try {
      await entry.pc.setRemoteDescription({ type: 'answer', sdp });
    } catch (e) {
      console.error('handleIncomingAnswer failed:', e);
    }
  }

  // ----- polling loops ---------------------------------------------------
  async function pollSignals() {
    if (!inCall) return;
    const { ok, data } = await get(`/calls/group/${callId}/signal/poll/`);
    if (ok && data && data.signals) {
      for (const sig of data.signals) {
        if (sig.kind === 'offer') await handleIncomingOffer(sig.from_username, sig.sdp);
        else if (sig.kind === 'answer') await handleIncomingAnswer(sig.from_username, sig.sdp);
      }
    }
    if (inCall) signalTimer = setTimeout(pollSignals, SIGNAL_POLL_MS);
  }

  async function pollParticipants() {
    if (!inCall) return;
    const { ok, data } = await get(`/calls/group/${groupId}/state/`);
    if (ok && data) {
      if (!data.call || data.call.call_id !== callId) {
        // Call ended server-side (everyone else left/timed out and so did our
        // last heartbeat window) — leave gracefully rather than hang forever.
        leaveCall();
        return;
      }
      const activeUsernames = new Set(data.call.participants.map((p) => p.username));
      activeUsernames.delete(myUsername);

      // New joiners we haven't connected to yet.
      data.call.participants.forEach((p) => {
        if (p.username === myUsername || peers.has(p.username)) return;
        const shouldOffer = myUserId < p.user_id;
        if (shouldOffer) initiateOfferTo(p);
        // else: wait for their offer to arrive via pollSignals()
      });

      // Participants who left.
      Array.from(peers.keys()).forEach((username) => {
        if (!activeUsernames.has(username)) removePeer(username);
      });
    }
    if (inCall) participantTimer = setTimeout(pollParticipants, PARTICIPANT_POLL_MS);
  }

  function heartbeat() {
    if (!inCall) return;
    post(`/calls/group/${callId}/heartbeat/`, {});
    heartbeatTimer = setTimeout(heartbeat, HEARTBEAT_MS);
  }

  async function pollBar() {
    if (inCall || document.hidden) {
      barTimer = setTimeout(pollBar, BAR_POLL_MS);
      return;
    }
    const { ok, data } = await get(`/calls/group/${groupId}/state/`);
    if (ok) renderBar(data.call);
    barTimer = setTimeout(pollBar, BAR_POLL_MS);
  }

  // ----- join / leave ------------------------------------------------------
  let joinInProgress = false;

  async function joinCall(startKind) {
    if (inCall || joinInProgress) return;
    joinInProgress = true;
    try {
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: startKind === 'video' });
      } catch (e) {
        alert("Couldn't access your microphone/camera. Check your browser's permission settings and try again.");
        return;
      }

      const res = await post(`/calls/group/${groupId}/join/`, { kind: startKind });
      if (!res.ok) {
        stream.getTracks().forEach((t) => t.stop());
        if (res.data && res.data.error === 'call_full') {
          alert(`This call is full (max ${res.data.max} people).`);
        } else {
          alert('Could not join the call. Please try again.');
        }
        return;
      }

      localStream = stream;
      callId = res.data.call_id;
      kind = res.data.kind;
      inCall = true;

      clearTimeout(barTimer);
      renderBar(null);
      renderOverlay();

      (res.data.others || []).forEach((other) => {
        if (other.should_offer) {
          initiateOfferTo(other);
        } else {
          peers.set(other.username, {
            pc: null, userId: other.user_id, avatarColor: other.avatar_color, avatarUrl: other.avatar_url,
          });
        }
      });
      renderOverlay();

      signalTimer = setTimeout(pollSignals, SIGNAL_POLL_MS);
      participantTimer = setTimeout(pollParticipants, PARTICIPANT_POLL_MS);
      heartbeatTimer = setTimeout(heartbeat, HEARTBEAT_MS);
    } finally {
      joinInProgress = false;
    }
  }

  function leaveCall() {
    if (!inCall) return;
    const endingCallId = callId;
    inCall = false;

    clearTimeout(signalTimer); clearTimeout(participantTimer); clearTimeout(heartbeatTimer);
    signalTimer = participantTimer = heartbeatTimer = null;

    if (localStream) { localStream.getTracks().forEach((t) => t.stop()); localStream = null; }
    peers.forEach((entry) => { if (entry.pc) { try { entry.pc.close(); } catch (e) {} } });
    peers.clear();
    micEnabled = camEnabled = true;
    callId = null;

    renderOverlay();
    barTimer = setTimeout(pollBar, 500);

    if (endingCallId) post(`/calls/group/${endingCallId}/leave/`, {});
  }

  // ----- controls ------------------------------------------------------
  function toggleMic() {
    if (!localStream) return;
    micEnabled = !micEnabled;
    localStream.getAudioTracks().forEach((t) => { t.enabled = micEnabled; });
    renderOverlay();
  }
  function toggleCam() {
    if (!localStream) return;
    camEnabled = !camEnabled;
    localStream.getVideoTracks().forEach((t) => { t.enabled = camEnabled; });
    renderOverlay();
  }

  // ----- rendering ------------------------------------------------------
  function tileHtml({ id, username, avatarColor, avatarUrl, isLocal, hasVideo }) {
    const label = isLocal ? 'You' : escapeHtml(username);
    const mediaTag = hasVideo
      ? `<video id="gc-media-${cssId(id)}" ${isLocal ? 'muted' : ''} autoplay playsinline class="${isLocal ? 'mirrored' : ''}"></video>`
      : `<audio id="gc-media-${cssId(id)}" ${isLocal ? 'muted' : ''} autoplay></audio>
         ${avatarUrl
            ? `<img class="tile-avatar" src="${avatarUrl}" alt="${label}">`
            : `<div class="tile-avatar" style="background:${avatarColor || '#6C63FF'};">${initials(username)}</div>`}`;
    return `<div class="group-call-tile">${mediaTag}<span class="tile-name">${label}</span></div>`;
  }

  function renderOverlay() {
    if (!overlayRoot) return;
    if (!inCall) { overlayRoot.innerHTML = ''; return; }

    const hasVideo = kind === 'video';
    const tiles = [tileHtml({ id: myUsername, username: myUsername, avatarColor: myAvatarColor, avatarUrl: myAvatarUrl, isLocal: true, hasVideo })];
    peers.forEach((entry, username) => {
      tiles.push(tileHtml({ id: username, username, avatarColor: entry.avatarColor, avatarUrl: entry.avatarUrl, isLocal: false, hasVideo }));
    });

    overlayRoot.innerHTML = `
      <div class="group-call-overlay">
        <div class="group-call-topbar">${peers.size + 1} in call</div>
        <div class="group-call-grid" data-count="${tiles.length}">${tiles.join('')}</div>
        <div class="call-controls">
          <button class="call-ctrl-btn ${micEnabled ? '' : 'off'}" id="gc-mic-btn" title="Mute">${micEnabled ? '🎙️' : '🔇'}</button>
          ${hasVideo ? `<button class="call-ctrl-btn ${camEnabled ? '' : 'off'}" id="gc-cam-btn" title="Camera">${camEnabled ? '📷' : '🚫'}</button>` : ''}
          <button class="call-ctrl-btn hangup" id="gc-leave-btn" title="Leave call">📞</button>
        </div>
      </div>`;

    // Re-attach local stream now that the tile exists in the DOM.
    const localMedia = document.getElementById(`gc-media-${cssId(myUsername)}`);
    if (localMedia && localStream) attachStream(localMedia, localStream);
    // Re-attach any already-connected remote streams (element was just recreated).
    peers.forEach((entry, username) => {
      if (!entry.pc) return;
      const mediaEl = document.getElementById(`gc-media-${cssId(username)}`);
      if (!mediaEl) return;
      const receivers = entry.pc.getReceivers().filter((r) => r.track);
      if (receivers.length) attachStream(mediaEl, new MediaStream(receivers.map((r) => r.track)));
    });

    document.getElementById('gc-mic-btn')?.addEventListener('click', toggleMic);
    document.getElementById('gc-cam-btn')?.addEventListener('click', toggleCam);
    document.getElementById('gc-leave-btn')?.addEventListener('click', leaveCall);
  }

  function renderBar(call) {
    if (!barRoot) return;
    if (!call) {
      barRoot.innerHTML = `
        <div class="group-call-bar">
          <button type="button" class="btn btn-ghost btn-sm" id="gc-start-audio">📞 Start audio call</button>
          <button type="button" class="btn btn-ghost btn-sm" id="gc-start-video">🎥 Start video call</button>
        </div>`;
      document.getElementById('gc-start-audio')?.addEventListener('click', () => joinCall('audio'));
      document.getElementById('gc-start-video')?.addEventListener('click', () => joinCall('video'));
    } else {
      const n = call.participants.length;
      barRoot.innerHTML = `
        <div class="group-call-bar active">
          <span class="pulse-dot"></span>
          <span>${n} ${n === 1 ? 'person' : 'people'} on a ${escapeHtml(call.kind)} call</span>
          <button type="button" class="btn btn-coral btn-sm" id="gc-join-btn">Join</button>
        </div>`;
      document.getElementById('gc-join-btn')?.addEventListener('click', () => joinCall(call.kind));
    }
  }

  // Leave cleanly if the person navigates away mid-call.
  window.addEventListener('beforeunload', () => {
    if (inCall && callId) {
      navigator.sendBeacon?.(`/calls/group/${callId}/leave/`, new URLSearchParams({ csrfmiddlewaretoken: csrfToken }));
    }
  });

  pollBar();
})();
