(function () {
  const params = new URLSearchParams(window.location.search);
  const code = (params.get('code') || '').trim().toUpperCase();
  const name = (params.get('name') || sessionStorage.getItem('lessonName') || 'Гость').trim();
  const role = params.get('role') || sessionStorage.getItem('lessonRole') || 'student';
  const mode = params.get('mode') === 'audio' ? 'audio' : 'lesson';

  if (!code) {
    window.location.href = '/';
    return;
  }

  document.body.classList.toggle('mode-audio', mode === 'audio');
  if (mode === 'audio') {
    document.title = 'Аудиозвонок — УрокLive';
  }

  const gate = document.getElementById('permission-gate');
  const gateError = document.getElementById('gate-error');
  const allowBtn = document.getElementById('allow-media');
  const skipBtn = document.getElementById('skip-media');
  const lessonTitle = document.getElementById('lesson-title');
  const roomCodeEl = document.getElementById('room-code');
  const selfLabel = document.getElementById('self-label');
  const peopleEl = document.getElementById('people');
  const boardStatus = document.getElementById('board-status');
  const chatLog = document.getElementById('chat-log');
  const chatForm = document.getElementById('chat-form');
  const chatInput = document.getElementById('chat-input');
  const copyCodeBtn = document.getElementById('copy-code');
  const micBtn = document.getElementById('toggle-mic');
  const camBtn = document.getElementById('toggle-cam');

  roomCodeEl.textContent = code;

  let statusTimer = null;
  let entering = false;
  let joined = false;

  function setStatus(text) {
    boardStatus.textContent = text;
    boardStatus.classList.add('is-visible');
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      boardStatus.classList.remove('is-visible');
    }, 2200);
  }

  function roleLabel(value) {
    return value === 'tutor' ? 'репетитор' : 'ученик';
  }

  function renderPeople(participants) {
    peopleEl.innerHTML = '';
    for (const person of participants) {
      const li = document.createElement('li');
      li.textContent = `${person.name}\n(${roleLabel(person.role)})`;
      peopleEl.appendChild(li);
    }
  }

  function appendChat({ from, text, role: msgRole }) {
    const item = document.createElement('div');
    item.className = 'chat-item';
    item.innerHTML = `<strong>${escapeHtml(from)}</strong> <span>(${roleLabel(msgRole)})</span>: ${escapeHtml(text)}`;
    chatLog.appendChild(item);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function setGateBusy(busy, label) {
    allowBtn.disabled = busy;
    skipBtn.disabled = busy;
    allowBtn.classList.toggle('is-loading', busy);
    const text = allowBtn.querySelector('.btn-label');
    if (text) text.textContent = label || (busy ? 'Запрос доступа…' : 'Разрешить и войти');
  }

  function showGateError(message) {
    gate.hidden = false;
    gateError.hidden = !message;
    gateError.textContent = message || '';
  }

  const socket = new LessonSocket();
  socket.connect();

  const board = new Whiteboard(document.getElementById('board'), {
    onDraw: (draw) => socket.send('whiteboard-draw', draw),
    onStatus: setStatus,
  });

  const widthInput = document.getElementById('stroke-width');
  const widthValue = document.getElementById('width-value');
  const colorWrap = document.querySelector('.color-wrap');

  function syncWidthUi() {
    const eraser = board.tool === 'eraser';
    widthInput.max = eraser ? '48' : '24';
    widthInput.value = String(board.width);
    widthValue.textContent = String(board.width);
    document.body.classList.toggle('eraser-mode', eraser);
    if (colorWrap) colorWrap.style.opacity = eraser ? '0.35' : '1';
  }

  document.querySelectorAll('.tool[data-tool]').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tool[data-tool]').forEach((b) => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      board.setTool(btn.dataset.tool);
      syncWidthUi();
    });
  });

  document.getElementById('stroke-color').addEventListener('input', (event) => {
    board.setColor(event.target.value);
  });

  widthInput.addEventListener('input', (event) => {
    board.setWidth(event.target.value);
    widthValue.textContent = String(board.width);
  });

  syncWidthUi();

  document.getElementById('clear-board').addEventListener('click', () => {
    if (!confirm('Очистить доску для всех?')) return;
    board.clear();
    socket.send('whiteboard-clear');
  });

  copyCodeBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code);
      setStatus('Код скопирован');
    } catch {
      setStatus(`Код: ${code}`);
    }
  });

  const unmuteBtn = document.getElementById('unmute-remote');

  async function refreshUnmuteLabel() {
    const micState = await call.queryMicPermission();
    if (!call.hasLiveMic() && micState === 'denied') {
      unmuteBtn.textContent = 'Разрешить микрофон';
    } else if (!call.hasLiveMic()) {
      unmuteBtn.textContent = 'Включить микрофон и звук';
    } else {
      unmuteBtn.textContent = 'Включить звук';
    }
  }

  const call = new LessonCall({
    socket,
    localVideo: document.getElementById('local-video'),
    remoteVideo: document.getElementById('remote-video'),
    remoteAudio: document.getElementById('remote-audio'),
    remoteLabel: document.getElementById('remote-label'),
    onStatus: setStatus,
    onAudioBlocked: (blocked) => {
      unmuteBtn.hidden = !blocked;
      if (blocked) refreshUnmuteLabel();
    },
  });

  unmuteBtn.addEventListener('click', async () => {
    if (unmuteBtn.disabled) return;
    unmuteBtn.disabled = true;
    unmuteBtn.textContent = 'Запрос доступа…';
    try {
      const result = await call.enableSoundAndMic();
      if (result.ok) {
        unmuteBtn.hidden = true;
        setStatus('Микрофон и звук включены');
      } else {
        unmuteBtn.hidden = false;
        setStatus(result.error || 'Не удалось включить звук');
        if (result.needSettings) {
          showGateError(
            result.error ||
              'Браузер запретил микрофон. Откройте настройки сайта (замок у адресной строки) → разрешите микрофон и камеру → нажмите кнопку снова.'
          );
          gate.hidden = false;
        }
      }
    } catch (error) {
      console.error(error);
      unmuteBtn.hidden = false;
      setStatus(error.message || 'Ошибка при запросе микрофона');
    } finally {
      unmuteBtn.disabled = false;
      if (!unmuteBtn.hidden) {
        await refreshUnmuteLabel();
      }
    }
  });

  refreshUnmuteLabel();

  function joinRoom() {
    if (joined) {
      entering = false;
      setGateBusy(false);
      gate.hidden = true;
      return;
    }

    setStatus('Подключение к комнате…');
    socket.joinRoom({ code, name, role }, (response) => {
      if (!response?.ok) {
        showGateError(response?.error || 'Не удалось войти в комнату');
        setGateBusy(false);
        entering = false;
        return;
      }

      joined = true;
      gate.hidden = true;
      lessonTitle.textContent = response.title;
      selfLabel.textContent = `${response.self.name} · ${roleLabel(response.self.role)}`;
      renderPeople(response.participants);
      board.loadStrokes(response.strokes || []);
      call.setSelf(response.self.id, response.participants);
      setStatus(call.localStream ? 'Вы в уроке' : 'Вы в уроке без камеры');
      entering = false;
      setGateBusy(false);
    });
  }

  async function enterLesson({ withMedia }) {
    if (entering || joined) return;
    entering = true;
    showGateError('');
    setGateBusy(true, withMedia ? 'Запрос доступа…' : 'Вход…');

    try {
      void call.unlockAudio();
      if (withMedia) {
        const micState = await call.queryMicPermission();
        if (micState === 'denied') {
          throw new Error(
            'Микрофон запрещён для сайта. Нажмите на замок у адреса → разрешите микрофон и камеру, обновите страницу и войдите снова.'
          );
        }
        await call.startLocalMedia({ audioOnly: mode === 'audio' });
      }
      joinRoom();
      refreshUnmuteLabel();
    } catch (error) {
      console.error(error);
      entering = false;
      setGateBusy(false);
      showGateError(error.message || 'Не удалось получить доступ к камере/микрофону.');
      refreshUnmuteLabel();
    }
  }

  socket.on('participant-joined', ({ participant, participants }) => {
    renderPeople(participants);
    call.handleParticipantJoined(participant);
    setStatus(`${participant.name} подключился`);
  });

  socket.on('participant-left', ({ id, name: leftName, participants }) => {
    renderPeople(participants);
    call.handleParticipantLeft(id);
    if (leftName) setStatus(`${leftName}: связь прервалась`);
  });

  socket.on('rejoined', (response) => {
    if (!response?.ok) {
      setStatus(response?.error || 'Не удалось переподключиться');
      return;
    }
    joined = true;
    call.selfId = response.self.id;
    renderPeople(response.participants);
    board.loadStrokes(response.strokes || []);
    call.setSelf(response.self.id, response.participants);
    setStatus('Соединение восстановлено');
  });

  socket.on('disconnected', () => {
    setStatus('Связь с сервером потеряна, переподключение…');
  });

  socket.on('whiteboard-draw', (draw) => {
    board.applyRemoteDraw(draw);
  });

  socket.on('whiteboard-stroke', (stroke) => {
    board.addStroke(stroke);
  });

  socket.on('whiteboard-clear', () => {
    board.clear();
  });

  socket.on('chat-message', (message) => {
    appendChat(message);
  });

  chatForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    socket.send('chat-message', { text });
    chatInput.value = '';
  });

  let micOn = true;
  let camOn = true;

  micBtn.addEventListener('click', () => {
    micOn = !micOn;
    call.setMicEnabled(micOn);
    micBtn.setAttribute('aria-pressed', String(micOn));
    micBtn.textContent = micOn ? 'Микрофон вкл' : 'Микрофон выкл';
  });

  camBtn.addEventListener('click', () => {
    camOn = !camOn;
    call.setCamEnabled(camOn);
    camBtn.setAttribute('aria-pressed', String(camOn));
    camBtn.textContent = camOn ? 'Камера вкл' : 'Камера выкл';
  });

  window.addEventListener('beforeunload', () => {
    call.stop();
  });

  allowBtn.addEventListener('click', async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (joined) {
      setGateBusy(true, 'Запрос доступа…');
      showGateError('');
      try {
        const result = await call.enableSoundAndMic();
        if (result.ok) {
          gate.hidden = true;
          setStatus('Микрофон и звук включены');
        } else {
          showGateError(result.error || 'Не удалось получить доступ к микрофону');
        }
      } finally {
        setGateBusy(false);
        refreshUnmuteLabel();
      }
      return;
    }
    enterLesson({ withMedia: true });
  });

  skipBtn.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (joined) {
      gate.hidden = true;
      showGateError('');
      return;
    }
    enterLesson({ withMedia: false });
  });

  // Только показываем окно — запрос устройств строго по клику пользователя.
  gate.hidden = false;

  if (!window.isSecureContext && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
    showGateError(
      'Браузер может блокировать камеру на этом адресе. Для видео откройте http://127.0.0.1:3000 или настройте HTTPS на VPS. Можно войти без камеры.'
    );
  } else if (!navigator.mediaDevices?.getUserMedia) {
    showGateError('Браузер не даёт доступ к камере/микрофону. Можно войти без видео и пользоваться доской.');
  }
})();
