(async function () {
  const user = await Auth.requireUser();
  if (!user) {
    location.href = '/auth.html';
    return;
  }

  const meLabel = document.getElementById('me-label');
  const friendsList = document.getElementById('friends-list');
  const incomingList = document.getElementById('incoming-list');
  const friendsEmpty = document.getElementById('friends-empty');
  const incomingEmpty = document.getElementById('incoming-empty');
  const searchForm = document.getElementById('search-form');
  const searchInput = document.getElementById('search-input');
  const searchResults = document.getElementById('search-results');
  const chatEmpty = document.getElementById('chat-empty');
  const chatPanel = document.getElementById('chat-panel');
  const chatTitle = document.getElementById('chat-title');
  const chatSub = document.getElementById('chat-sub');
  const dmLog = document.getElementById('dm-log');
  const dmForm = document.getElementById('dm-form');
  const dmInput = document.getElementById('dm-input');
  const toast = document.getElementById('toast');
  const callModal = document.getElementById('call-modal');
  const callModalTitle = document.getElementById('call-modal-title');
  const callModalText = document.getElementById('call-modal-text');

  meLabel.textContent = `${user.name} (@${user.login})`;

  let friends = [];
  let incoming = [];
  let selectedFriend = null;
  let incomingCall = null;
  let toastTimer = null;

  function showToast(text) {
    toast.textContent = text;
    toast.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2800);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  const socket = new LessonSocket();
  socket.authToken = Auth.getToken();
  socket.connect();

  socket.on('auth-result', (data) => {
    if (!data.ok) showToast(data.error || 'Ошибка авторизации сокета');
  });

  socket.on('dm-message', ({ message }) => {
    if (!message) return;
    const otherId = message.fromId === user.id ? message.toId : message.fromId;
    if (selectedFriend && selectedFriend.id === otherId) {
      appendDm(message);
    } else if (message.fromId !== user.id) {
      const friend = friends.find((f) => f.id === message.fromId);
      showToast(`Сообщение от ${friend?.name || 'друга'}`);
    }
  });

  socket.on('friend-event', async () => {
    await loadFriends();
    showToast('Обновление списка друзей');
  });

  socket.on('call-invite', ({ call, from }) => {
    incomingCall = call;
    callModal.hidden = false;
    callModalTitle.textContent = call.kind === 'audio' ? 'Аудиозвонок' : 'Приглашение на урок';
    callModalText.textContent = `${from?.name || 'Друг'} приглашает: ${call.title}`;
  });

  socket.on('call-respond', ({ call, accept }) => {
    if (!call) return;
    if (accept) showToast('Друг принял звонок');
    else showToast('Звонок отклонён');
  });

  socket.on('call-created', ({ call }) => {
    showToast('Ожидаем ответа друга…');
  });

  async function loadFriends() {
    const data = await Auth.api('/api/friends');
    friends = data.friends || [];
    incoming = data.incoming || [];
    renderFriends();
    renderIncoming();
  }

  function renderFriends() {
    friendsList.innerHTML = '';
    friendsEmpty.hidden = friends.length > 0;
    for (const friend of friends) {
      const li = document.createElement('li');
      li.className = 'people-item' + (selectedFriend?.id === friend.id ? ' is-active' : '');
      li.innerHTML = `<strong>${escapeHtml(friend.name)}</strong><span>@${escapeHtml(friend.login)}</span>`;
      li.addEventListener('click', () => selectFriend(friend));
      friendsList.appendChild(li);
    }
  }

  function renderIncoming() {
    incomingList.innerHTML = '';
    incomingEmpty.hidden = incoming.length > 0;
    for (const person of incoming) {
      const li = document.createElement('li');
      li.className = 'people-item';
      li.innerHTML = `
        <div><strong>${escapeHtml(person.name)}</strong><span>@${escapeHtml(person.login)}</span></div>
        <div class="row-actions">
          <button type="button" class="btn btn-primary btn-sm" data-act="accept">Принять</button>
          <button type="button" class="btn btn-ghost btn-sm" data-act="decline">Нет</button>
        </div>`;
      li.querySelector('[data-act="accept"]').addEventListener('click', () => respondFriend(person.id, true));
      li.querySelector('[data-act="decline"]').addEventListener('click', () => respondFriend(person.id, false));
      incomingList.appendChild(li);
    }
  }

  async function respondFriend(userId, accept) {
    await Auth.api('/api/friends/respond', {
      method: 'POST',
      body: JSON.stringify({ userId, accept }),
    });
    await loadFriends();
  }

  async function selectFriend(friend) {
    selectedFriend = friend;
    renderFriends();
    chatEmpty.hidden = true;
    chatPanel.hidden = false;
    chatTitle.textContent = friend.name;
    chatSub.textContent = `@${friend.login}`;
    dmLog.innerHTML = '';
    const data = await Auth.api(`/api/messages/${friend.id}`);
    for (const message of data.messages || []) appendDm(message);
  }

  function appendDm(message) {
    const mine = message.fromId === user.id;
    const el = document.createElement('div');
    el.className = 'dm-item' + (mine ? ' is-mine' : '');
    el.innerHTML = `<span>${escapeHtml(message.text)}</span>`;
    dmLog.appendChild(el);
    dmLog.scrollTop = dmLog.scrollHeight;
  }

  searchForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const q = searchInput.value.trim();
    searchResults.innerHTML = '';
    if (!q) return;
    const data = await Auth.api(`/api/users/search?q=${encodeURIComponent(q)}`);
    for (const person of data.users || []) {
      const li = document.createElement('li');
      li.className = 'people-item';
      li.innerHTML = `
        <div><strong>${escapeHtml(person.name)}</strong><span>@${escapeHtml(person.login)}</span></div>
        <button type="button" class="btn btn-primary btn-sm">В друзья</button>`;
      li.querySelector('button').addEventListener('click', async () => {
        try {
          await Auth.api('/api/friends/request', {
            method: 'POST',
            body: JSON.stringify({ userId: person.id }),
          });
          showToast('Заявка отправлена');
          await loadFriends();
        } catch (error) {
          showToast(error.message);
        }
      });
      searchResults.appendChild(li);
    }
  });

  dmForm.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!selectedFriend) return;
    const text = dmInput.value.trim();
    if (!text) return;
    socket.send('dm-send', { toId: selectedFriend.id, text });
    dmInput.value = '';
  });

  async function invite(kind) {
    if (!selectedFriend) return;
    const title = kind === 'audio' ? `Аудио: ${user.name}` : `Урок: ${user.name}`;
    const data = await Auth.api('/api/calls/invite', {
      method: 'POST',
      body: JSON.stringify({ friendId: selectedFriend.id, kind, title }),
    });
    showToast('Приглашение отправлено');
    // Caller waits until friend accepts; also allow immediate join as tutor.
    openCall(data.call, 'tutor');
  }

  function openCall(call, role) {
    const mode = call.kind === 'audio' ? 'audio' : 'lesson';
    const params = new URLSearchParams({
      code: call.roomCode,
      name: user.name,
      role,
      mode,
    });
    location.href = `/lesson.html?${params.toString()}`;
  }

  document.getElementById('start-lesson').addEventListener('click', () => invite('lesson'));
  document.getElementById('start-audio').addEventListener('click', () => invite('audio'));

  document.getElementById('call-accept').addEventListener('click', () => {
    if (!incomingCall) return;
    socket.send('call-respond', { callId: incomingCall.id, accept: true });
    const call = incomingCall;
    incomingCall = null;
    callModal.hidden = true;
    openCall(call, 'student');
  });

  document.getElementById('call-decline').addEventListener('click', () => {
    if (!incomingCall) return;
    socket.send('call-respond', { callId: incomingCall.id, accept: false });
    incomingCall = null;
    callModal.hidden = true;
  });

  document.getElementById('logout-btn').addEventListener('click', async () => {
    try {
      await Auth.api('/api/auth/logout', { method: 'POST' });
    } catch {
      /* ignore */
    }
    Auth.clear();
    location.href = '/auth.html';
  });

  await loadFriends();
})();
