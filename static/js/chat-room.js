(function () {
  const root = document.querySelector('.chat-room');
  if (!root) return;

  const friendUsername = root.dataset.username;
  const pollUrl = root.dataset.pollUrl;
  const sendUrl = root.dataset.sendUrl;
  const typingUrl = root.dataset.typingUrl;
  const searchUrl = root.dataset.searchUrl;
  const muteUrl = root.dataset.muteUrl;
  const uploadVoiceUrl = root.dataset.uploadVoiceUrl;
  const uploadFileUrl = root.dataset.uploadFileUrl;
  const gifSearchUrl = root.dataset.gifSearchUrl;
  const gifEnabled = root.dataset.gifEnabled === '1';
  let isMuted = root.dataset.isMuted === '1';

  const messagesEl = document.getElementById('chat-messages');
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  const csrfToken = form.querySelector('[name=csrfmiddlewaretoken]').value;

  let lastId = 0;
  let replyToId = null;
  const knownMessages = {}; // id -> last-known message object, for quick DOM lookups when jumping from search

  // ---------------------------------------------------------------
  // helpers
  // ---------------------------------------------------------------
  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : s;
    return div.innerHTML;
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function isNearBottom() {
    return messagesEl.scrollTop + messagesEl.clientHeight >= messagesEl.scrollHeight - 60;
  }

  function formatBytes(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatDuration(seconds) {
    seconds = Math.max(0, Math.round(seconds || 0));
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  async function postForm(url, fields) {
    const params = new URLSearchParams(fields);
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken },
      body: params.toString(),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    return { ok: res.ok, data };
  }

  // ---------------------------------------------------------------
  // bubble rendering
  // ---------------------------------------------------------------
  function renderReplyQuote(m) {
    if (!m.reply_to) return '';
    const who = m.reply_to.sender_username || 'Someone';
    return `<div class="reply-quote" data-jump-to="${m.reply_to.id}">
      <span class="reply-sender">${escapeHtml(who)}</span>${escapeHtml(m.reply_to.preview)}
    </div>`;
  }

  function renderReactionRow(m) {
    if (!m.reactions || !m.reactions.length) return '<div class="reaction-row" data-reactions></div>';
    const chips = m.reactions.map((r) =>
      `<span class="reaction-chip ${r.mine ? 'mine' : ''}" data-emoji="${r.emoji}">${r.emoji} ${r.count}</span>`
    ).join('');
    return `<div class="reaction-row" data-reactions>${chips}</div>`;
  }

  function renderReceipt(m) {
    if (!m.mine || m.kind !== 'text') return '';
    return `<span class="receipt ${m.is_read ? 'read' : ''}">✓✓</span>`;
  }

  function renderActions(m) {
    const buttons = [`<button type="button" data-action="react" title="React">😊</button>`,
                      `<button type="button" data-action="reply" title="Reply">↩</button>`];
    if (m.mine && m.kind === 'text') buttons.push(`<button type="button" data-action="edit" title="Edit">✏️</button>`);
    if (m.mine) buttons.push(`<button type="button" data-action="delete" title="Delete">🗑️</button>`);
    return `<div class="msg-actions">${buttons.join('')}</div>`;
  }

  const REACTION_EMOJIS = ['❤️', '😂', '😮', '😢', '🙏', '👍'];
  function renderReactionPicker() {
    const buttons = REACTION_EMOJIS.map((e) => `<button type="button" data-pick-emoji="${e}">${e}</button>`).join('');
    return `<div class="reaction-picker hidden">${buttons}</div>`;
  }

  function bubbleInnerHtml(m) {
    if (m.deleted) {
      return `<div class="bubble deleted-bubble">Message deleted<span class="time"></span></div>`;
    }
    if (m.kind === 'voice') {
      return `<div class="bubble voice-bubble" data-audio-url="${escapeHtml(m.media_url)}">
        <button type="button" class="voice-play-btn" data-action="voice-play">▶</button>
        <div class="voice-track" data-action="voice-seek"><div class="voice-track-fill"></div></div>
        <span class="voice-duration">${formatDuration(m.duration_seconds)}</span>
      </div>`;
    }
    if (m.kind === 'file') {
      return `<a class="bubble file-bubble" href="${escapeHtml(m.media_url)}" target="_blank" rel="noopener">
        <span class="file-icon">📄</span>
        <div class="file-meta">
          <span class="file-name">${escapeHtml(m.file_name || 'File')}</span>
          <span class="file-size">${formatBytes(m.file_size)}</span>
        </div>
      </a>`;
    }
    if (m.kind === 'gif' || m.kind === 'sticker') {
      return `<div class="bubble media-bubble">
        <img src="${escapeHtml(m.media_url)}" alt="${m.kind}" loading="lazy">
        <span class="time">${m.created_at}</span>
      </div>`;
    }
    const editedTag = m.edited ? '<span class="edited-tag">(edited)</span>' : '';
    return `<div class="bubble" data-text-bubble>
      <span data-body>${escapeHtml(m.body)}</span>${editedTag}
      <span class="time">${m.created_at}${renderReceipt(m)}</span>
    </div>`;
  }

  function buildBubbleRow(m) {
    const row = document.createElement('div');
    row.className = 'bubble-row' + (m.mine ? ' mine' : '') + (m.deleted ? ' is-deleted' : '');
    row.dataset.id = m.id;
    row.dataset.kind = m.kind;
    row.innerHTML = `
      <div class="bubble-col">
        ${renderReplyQuote(m)}
        ${bubbleInnerHtml(m)}
        ${renderReactionRow(m)}
      </div>
      ${renderActions(m)}
      ${renderReactionPicker()}
    `;
    knownMessages[m.id] = m;
    return row;
  }

  function appendMessage(m) {
    if (document.querySelector(`.bubble-row[data-id="${m.id}"]`)) return; // already rendered
    const loading = document.getElementById('messages-loading');
    if (loading) loading.remove();
    const row = buildBubbleRow(m);
    messagesEl.appendChild(row);
    lastId = Math.max(lastId, m.id);
  }

  function updateReactionsInDom(id, reactions) {
    const row = messagesEl.querySelector(`.bubble-row[data-id="${id}"]`);
    if (!row) return;
    const container = row.querySelector('[data-reactions]');
    if (!container) return;
    if (!reactions.length) { container.innerHTML = ''; return; }
    container.innerHTML = reactions.map((r) =>
      `<span class="reaction-chip ${r.mine ? 'mine' : ''}" data-emoji="${r.emoji}">${r.emoji} ${r.count}</span>`
    ).join('');
  }

  function updateEditDeleteInDom(id, info) {
    const row = messagesEl.querySelector(`.bubble-row[data-id="${id}"]`);
    if (!row) return;
    if (info.deleted && !row.classList.contains('is-deleted')) {
      row.classList.add('is-deleted');
      const col = row.querySelector('.bubble-col');
      const bubble = col.querySelector(':scope > .bubble, :scope > a.bubble');
      if (bubble) bubble.outerHTML = `<div class="bubble deleted-bubble">Message deleted<span class="time"></span></div>`;
      const actions = row.querySelector('.msg-actions');
      if (actions) actions.remove();
    } else if (info.edited) {
      const bodyEl = row.querySelector('[data-body]');
      const tag = row.querySelector('.edited-tag');
      if (bodyEl && bodyEl.textContent !== info.body) bodyEl.textContent = info.body;
      if (!tag) {
        const bubble = row.querySelector('[data-text-bubble]');
        if (bubble) {
          const span = document.createElement('span');
          span.className = 'edited-tag';
          span.textContent = '(edited)';
          bodyEl.after(span);
        }
      }
    }
  }

  function updateReadReceipts(readStatus) {
    (readStatus || []).forEach(({ id, is_read }) => {
      if (!is_read) return;
      const row = messagesEl.querySelector(`.bubble-row.mine[data-id="${id}"]`);
      if (!row) return;
      const receipt = row.querySelector('.receipt');
      if (receipt) receipt.classList.add('read');
    });
  }

  // ---------------------------------------------------------------
  // initial load + polling
  // ---------------------------------------------------------------
  async function poll(isInitial) {
    try {
      const res = await fetch(pollUrl + '?after=' + lastId);
      if (!res.ok) return;
      const data = await res.json();

      if (isInitial) {
        const loading = document.getElementById('messages-loading');
        if (loading) loading.remove();
      }

      if (data.messages && data.messages.length) {
        const wasAtBottom = isInitial || isNearBottom();
        data.messages.forEach(appendMessage);
        if (wasAtBottom) scrollToBottom();
      }

      Object.entries(data.reaction_updates || {}).forEach(([id, reactions]) => updateReactionsInDom(id, reactions));
      Object.entries(data.edit_delete_updates || {}).forEach(([id, info]) => updateEditDeleteInDom(id, info));
      updateReadReceipts(data.read_status);

      const typingRow = document.getElementById('typing-row');
      typingRow.innerHTML = data.friend_typing
        ? `${escapeHtml(friendUsername)} is typing <span class="typing-dots"><span></span><span></span><span></span></span>`
        : '';

      const dot = document.getElementById('friend-presence-dot');
      const statusText = document.getElementById('friend-status-text');
      if (dot) dot.classList.toggle('online', !!data.friend_online);
      if (statusText) {
        statusText.classList.toggle('online-text', !!data.friend_online);
        if (data.friend_online) statusText.textContent = 'Online';
      }
    } catch (e) { /* silent — next tick retries */ }
  }

  poll(true);
  setInterval(() => poll(false), 3000);
  window.addEventListener('focus', () => poll(false));

  // ---------------------------------------------------------------
  // sending text
  // ---------------------------------------------------------------
  function clearReply() {
    replyToId = null;
    document.getElementById('reply-bar').classList.remove('active');
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    input.value = '';
    input.disabled = true;
    try {
      const fields = { body, kind: 'text' };
      if (replyToId) fields.reply_to = replyToId;
      const { ok, data } = await postForm(sendUrl, fields);
      if (ok && data) { appendMessage(data); scrollToBottom(); }
      clearReply();
    } finally {
      input.disabled = false;
      input.focus();
    }
  });

  // ---------------------------------------------------------------
  // typing indicator (throttled — at most once every 2.5s while typing)
  // ---------------------------------------------------------------
  let lastTypingPing = 0;
  input.addEventListener('input', () => {
    const now = Date.now();
    if (input.value.trim() && now - lastTypingPing > 2500) {
      lastTypingPing = now;
      postForm(typingUrl, {});
    }
  });

  // ---------------------------------------------------------------
  // message actions: reply / react / edit / delete (event delegation,
  // since bubbles are added/replaced dynamically)
  // ---------------------------------------------------------------
  messagesEl.addEventListener('click', async (e) => {
    const jumpTarget = e.target.closest('[data-jump-to]');
    if (jumpTarget) {
      const target = messagesEl.querySelector(`.bubble-row[data-id="${jumpTarget.dataset.jumpTo}"]`);
      if (target) { target.scrollIntoView({ block: 'center', behavior: 'smooth' }); target.classList.add('actions-open'); setTimeout(() => target.classList.remove('actions-open'), 1200); }
      return;
    }

    const voicePlay = e.target.closest('[data-action="voice-play"]');
    if (voicePlay) {
      toggleVoicePlayback(voicePlay);
      return;
    }
    const voiceSeek = e.target.closest('[data-action="voice-seek"]');
    if (voiceSeek) {
      seekVoice(voiceSeek, e);
      return;
    }

    const pickEmoji = e.target.closest('[data-pick-emoji]');
    if (pickEmoji) {
      const row = pickEmoji.closest('.bubble-row');
      const id = row.dataset.id;
      const { ok, data } = await postForm(`/chat/message/${id}/react/`, { emoji: pickEmoji.dataset.pickEmoji });
      if (ok) updateReactionsInDom(id, data.reactions);
      row.querySelector('.reaction-picker').classList.add('hidden');
      return;
    }

    const reactionChip = e.target.closest('.reaction-chip');
    if (reactionChip) {
      const row = reactionChip.closest('.bubble-row');
      const id = row.dataset.id;
      const { ok, data } = await postForm(`/chat/message/${id}/react/`, { emoji: reactionChip.dataset.emoji });
      if (ok) updateReactionsInDom(id, data.reactions);
      return;
    }

    const actionBtn = e.target.closest('[data-action]');
    if (!actionBtn) return;
    const row = actionBtn.closest('.bubble-row');
    const id = row.dataset.id;
    const action = actionBtn.dataset.action;

    if (action === 'react') {
      document.querySelectorAll('.reaction-picker').forEach((p) => { if (p !== row.querySelector('.reaction-picker')) p.classList.add('hidden'); });
      row.querySelector('.reaction-picker').classList.toggle('hidden');
    } else if (action === 'reply') {
      const m = knownMessages[id];
      if (!m) return;
      replyToId = id;
      document.getElementById('reply-bar-sender').textContent = m.mine ? 'You' : friendUsername;
      document.getElementById('reply-bar-preview').textContent = m.deleted ? 'Message deleted' : (m.body || `[${m.kind}]`);
      document.getElementById('reply-bar').classList.add('active');
      input.focus();
    } else if (action === 'edit') {
      startInlineEdit(row, id);
    } else if (action === 'delete') {
      if (!confirm('Delete this message?')) return;
      const { ok, data } = await postForm(`/chat/message/${id}/delete/`, {});
      if (ok) updateEditDeleteInDom(id, { deleted: true, edited: false, body: '' });
    }
  });

  function startInlineEdit(row, id) {
    const bubble = row.querySelector('[data-text-bubble]');
    if (!bubble) return;
    const currentText = row.querySelector('[data-body]').textContent;
    bubble.innerHTML = `
      <input type="text" class="inline-edit-input" value="${escapeHtml(currentText)}" maxlength="2000"
             style="width:100%; border:none; background:transparent; font:inherit; color:inherit; outline:none;">
      <div style="display:flex; gap:6px; margin-top:4px;">
        <button type="button" class="btn btn-sm btn-primary" data-save-edit>Save</button>
        <button type="button" class="btn btn-sm btn-ghost" data-cancel-edit>Cancel</button>
      </div>`;
    const editInput = bubble.querySelector('.inline-edit-input');
    editInput.focus();
    editInput.setSelectionRange(editInput.value.length, editInput.value.length);

    bubble.querySelector('[data-cancel-edit]').addEventListener('click', () => {
      const m = knownMessages[id];
      bubble.outerHTML = bubbleInnerHtml(m);
    });
    bubble.querySelector('[data-save-edit]').addEventListener('click', async () => {
      const newBody = editInput.value.trim();
      if (!newBody) return;
      const { ok, data } = await postForm(`/chat/message/${id}/edit/`, { body: newBody });
      if (ok) {
        knownMessages[id] = data;
        bubble.outerHTML = bubbleInnerHtml(data);
      }
    });
  }

  document.getElementById('reply-bar-close').addEventListener('click', clearReply);

  // ---------------------------------------------------------------
  // voice playback (simple play/pause + seek on the progress track)
  // ---------------------------------------------------------------
  let activeAudio = null;
  let activeAudioBtn = null;

  function toggleVoicePlayback(btn) {
    const bubble = btn.closest('.voice-bubble');
    const url = bubble.dataset.audioUrl;
    if (activeAudio && activeAudioBtn === btn) {
      if (activeAudio.paused) { activeAudio.play(); btn.textContent = '⏸'; }
      else { activeAudio.pause(); btn.textContent = '▶'; }
      return;
    }
    if (activeAudio) { activeAudio.pause(); if (activeAudioBtn) activeAudioBtn.textContent = '▶'; }

    const audio = new Audio(url);
    activeAudio = audio;
    activeAudioBtn = btn;
    btn.textContent = '⏸';
    const fill = bubble.querySelector('.voice-track-fill');
    audio.addEventListener('timeupdate', () => {
      if (audio.duration) fill.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
    });
    audio.addEventListener('ended', () => { btn.textContent = '▶'; fill.style.width = '0%'; });
    audio.play().catch(() => { btn.textContent = '▶'; });
  }

  function seekVoice(track, e) {
    if (!activeAudio) return;
    const bubble = track.closest('.voice-bubble');
    if (bubble.dataset.audioUrl !== activeAudio.src && !activeAudio.src.endsWith(bubble.dataset.audioUrl)) return;
    const rect = track.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    if (activeAudio.duration) activeAudio.currentTime = ratio * activeAudio.duration;
  }

  // ---------------------------------------------------------------
  // voice recording (MediaRecorder)
  // ---------------------------------------------------------------
  const micBtn = document.getElementById('mic-toggle');
  const recorderBar = document.getElementById('voice-recorder-bar');
  const recTimeEl = document.getElementById('rec-time');
  let mediaRecorder = null;
  let recordedChunks = [];
  let recordStart = 0;
  let recordTimer = null;
  let recordStream = null;

  micBtn.addEventListener('click', async () => {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      alert("Voice messages aren't supported in this browser.");
      return;
    }
    try {
      recordStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      alert("Couldn't access your microphone. Check your browser's permission settings.");
      return;
    }
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(recordStream);
    mediaRecorder.addEventListener('dataavailable', (e) => { if (e.data.size > 0) recordedChunks.push(e.data); });
    mediaRecorder.start();
    recordStart = Date.now();
    recorderBar.classList.add('active');
    recTimeEl.textContent = '0:00';
    recordTimer = setInterval(() => {
      recTimeEl.textContent = formatDuration((Date.now() - recordStart) / 1000);
    }, 500);
  });

  function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    if (recordStream) recordStream.getTracks().forEach((t) => t.stop());
    clearInterval(recordTimer);
    recorderBar.classList.remove('active');
  }

  document.getElementById('voice-cancel-btn').addEventListener('click', () => {
    recordedChunks = [];
    stopRecording();
  });

  document.getElementById('voice-send-btn').addEventListener('click', () => {
    if (!mediaRecorder) return;
    const durationSeconds = (Date.now() - recordStart) / 1000;
    mediaRecorder.addEventListener('stop', async () => {
      if (!recordedChunks.length) return;
      const blob = new Blob(recordedChunks, { type: 'audio/webm' });
      const fd = new FormData();
      fd.append('audio', blob, 'voice.webm');
      try {
        const res = await fetch(uploadVoiceUrl, { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, body: fd });
        const data = await res.json();
        if (!res.ok) { alert(data.error || 'Voice upload failed.'); return; }
        const { ok, data: msg } = await postForm(sendUrl, {
          kind: 'voice', media_url: data.media_url, duration_seconds: Math.round(durationSeconds),
        });
        if (ok) { appendMessage(msg); scrollToBottom(); }
      } catch (e) {
        alert('Voice upload failed.');
      }
    }, { once: true });
    stopRecording();
  });

  // ---------------------------------------------------------------
  // file attach
  // ---------------------------------------------------------------
  const attachToggle = document.getElementById('attach-toggle');
  const fileInput = document.getElementById('file-input');
  attachToggle.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    if (file.size > 25 * 1024 * 1024) { alert('That file is too large (25MB max).'); return; }
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetch(uploadFileUrl, { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, body: fd });
      const data = await res.json();
      if (!res.ok) { alert(data.error || 'Upload failed.'); return; }
      const { ok, data: msg } = await postForm(sendUrl, {
        kind: 'file', media_url: data.media_url, file_name: data.file_name, file_size: data.file_size,
      });
      if (ok) { appendMessage(msg); scrollToBottom(); }
    } catch (e) {
      alert('Upload failed.');
    }
  });

  // ---------------------------------------------------------------
  // search
  // ---------------------------------------------------------------
  const searchToggle = document.getElementById('search-toggle');
  const searchBar = document.getElementById('search-bar');
  const searchInput = document.getElementById('search-input');
  const searchResults = document.getElementById('search-results');
  let searchDebounce = null;

  searchToggle.addEventListener('click', () => {
    searchBar.classList.toggle('active');
    if (searchBar.classList.contains('active')) searchInput.focus();
    else { searchResults.classList.add('hidden'); searchResults.innerHTML = ''; searchInput.value = ''; }
  });

  searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(runSearch, 300);
  });

  async function runSearch() {
    const q = searchInput.value.trim();
    if (!q) { searchResults.classList.add('hidden'); searchResults.innerHTML = ''; return; }
    try {
      const res = await fetch(`${searchUrl}?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      const results = data.results || [];
      searchResults.classList.remove('hidden');
      if (!results.length) { searchResults.innerHTML = `<div class="search-result-row">No matches.</div>`; return; }
      searchResults.innerHTML = results.map((r) => `
        <div class="search-result-row" data-jump-to="${r.id}">
          <div class="sr-meta">${r.mine ? 'You' : escapeHtml(friendUsername)} · ${r.created_at}</div>
          ${escapeHtml(r.body)}
        </div>`).join('');
    } catch (e) { /* leave whatever was there */ }
  }

  searchResults.addEventListener('click', (e) => {
    const row = e.target.closest('[data-jump-to]');
    if (!row) return;
    const target = messagesEl.querySelector(`.bubble-row[data-id="${row.dataset.jumpTo}"]`);
    if (target) {
      searchBar.classList.remove('active');
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
      target.classList.add('actions-open');
      setTimeout(() => target.classList.remove('actions-open'), 1500);
    }
  });

  // ---------------------------------------------------------------
  // mute toggle
  // ---------------------------------------------------------------
  document.getElementById('mute-toggle').addEventListener('click', async () => {
    const { ok, data } = await postForm(muteUrl, {});
    if (ok) {
      isMuted = data.muted;
      document.getElementById('mute-toggle').textContent = isMuted ? '🔕' : '🔔';
    }
  });

  // ---------------------------------------------------------------
  // calls (delegates to window.PingbackCalls from static/js/calls.js)
  // ---------------------------------------------------------------
  document.getElementById('call-audio-btn').addEventListener('click', () => {
    if (window.PingbackCalls) window.PingbackCalls.startCall(friendUsername, 'audio');
  });
  document.getElementById('call-video-btn').addEventListener('click', () => {
    if (window.PingbackCalls) window.PingbackCalls.startCall(friendUsername, 'video');
  });

  // ---------------------------------------------------------------
  // emoji picker
  // ---------------------------------------------------------------
  const emojiToggle = document.getElementById('emoji-toggle');
  const emojiPanel = document.getElementById('emoji-panel');
  const emojiTabs = document.getElementById('emoji-tabs');
  const emojiGrid = document.getElementById('emoji-grid');
  const gifToggle = document.getElementById('gif-toggle');
  const gifPanel = document.getElementById('gif-panel');

  function closePanels(except) {
    if (except !== emojiPanel) { emojiPanel.classList.add('hidden'); emojiToggle.classList.remove('active'); }
    if (except !== gifPanel) { gifPanel.classList.add('hidden'); gifToggle.classList.remove('active'); }
  }

  function renderEmojiCategory(name) {
    emojiGrid.innerHTML = '';
    (window.PINGBACK_EMOJI[name] || []).forEach((emoji) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = emoji;
      btn.addEventListener('click', () => {
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? input.value.length;
        input.value = input.value.slice(0, start) + emoji + input.value.slice(end);
        const cursor = start + emoji.length;
        input.setSelectionRange(cursor, cursor);
        input.focus();
      });
      emojiGrid.appendChild(btn);
    });
  }

  if (window.PINGBACK_EMOJI) {
    Object.keys(window.PINGBACK_EMOJI).forEach((name, i) => {
      const tab = document.createElement('div');
      tab.className = 'picker-tab' + (i === 0 ? ' active' : '');
      tab.textContent = name;
      tab.addEventListener('click', () => {
        emojiTabs.querySelectorAll('.picker-tab').forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        renderEmojiCategory(name);
      });
      emojiTabs.appendChild(tab);
    });
    renderEmojiCategory(Object.keys(window.PINGBACK_EMOJI)[0]);
  }

  emojiToggle.addEventListener('click', () => {
    const willOpen = emojiPanel.classList.contains('hidden');
    closePanels(willOpen ? emojiPanel : null);
    emojiPanel.classList.toggle('hidden', !willOpen);
    emojiToggle.classList.toggle('active', willOpen);
  });

  // ---------------------------------------------------------------
  // GIF / sticker picker
  // ---------------------------------------------------------------
  if (gifEnabled) {
    const gifTabs = gifPanel.querySelectorAll('.picker-tab');
    const gifSearchInput = document.getElementById('gif-search-input');
    const gifGrid = document.getElementById('gif-grid');
    const gifEmpty = document.getElementById('gif-empty');
    let activeType = 'gif';
    let gifDebounce = null;

    async function runGifSearch() {
      const q = gifSearchInput.value.trim();
      gifGrid.innerHTML = '';
      gifEmpty.classList.add('hidden');
      try {
        const res = await fetch(`${gifSearchUrl}?type=${activeType}&q=${encodeURIComponent(q)}`);
        if (!res.ok) return;
        const data = await res.json();
        const results = data.results || [];
        if (!results.length) { gifEmpty.classList.remove('hidden'); return; }
        results.forEach((item) => {
          const img = document.createElement('img');
          img.src = item.preview_url;
          img.alt = item.title || activeType;
          img.loading = 'lazy';
          img.addEventListener('click', async () => {
            gifPanel.classList.add('hidden');
            gifToggle.classList.remove('active');
            const { ok, data: msg } = await postForm(sendUrl, { media_url: item.url, kind: activeType });
            if (ok) { appendMessage(msg); scrollToBottom(); }
          });
          gifGrid.appendChild(img);
        });
      } catch (e) { /* leave empty state as-is */ }
    }

    gifTabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        gifTabs.forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        activeType = tab.dataset.contentType;
        runGifSearch();
      });
    });

    gifSearchInput.addEventListener('input', () => {
      clearTimeout(gifDebounce);
      gifDebounce = setTimeout(runGifSearch, 350);
    });

    gifToggle.addEventListener('click', () => {
      const willOpen = gifPanel.classList.contains('hidden');
      closePanels(willOpen ? gifPanel : null);
      gifPanel.classList.toggle('hidden', !willOpen);
      gifToggle.classList.toggle('active', willOpen);
      if (willOpen && !gifGrid.children.length) runGifSearch();
    });
  }

  document.addEventListener('click', (e) => {
    if (!form.contains(e.target)) closePanels(null);
    if (!e.target.closest('.bubble-row')) {
      document.querySelectorAll('.reaction-picker').forEach((p) => p.classList.add('hidden'));
    }
  });
})();
