class LessonSocket {
  constructor() {
    this.ws = null;
    this.handlers = new Map();
    this.pendingJoin = null;
    this.joinTimer = null;
    this.pingTimer = null;
    this.shouldReconnect = true;
    this.reconnectAttempt = 0;
    this.lastJoin = null;
    this.authToken = null;
  }

  on(type, handler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type).add(handler);
  }

  emitLocal(type, payload) {
    const set = this.handlers.get(type);
    if (!set) return;
    for (const handler of set) handler(payload);
  }

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${window.location.host}/ws`);

    this.ws.addEventListener('open', () => {
      this.reconnectAttempt = 0;
      this._startPing();
      if (this.authToken) {
        this.send('auth', { token: this.authToken });
      }
      this.emitLocal('connected', {});
      if (this.lastJoin && !this.pendingJoin) {
        this.joinRoom(this.lastJoin, (result) => {
          this.emitLocal('rejoined', result);
        });
      }
    });

    this.ws.addEventListener('message', (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      if (data.type === 'pong') return;

      if (data.type === 'join-result' && this.pendingJoin) {
        const cb = this.pendingJoin;
        this.pendingJoin = null;
        clearTimeout(this.joinTimer);
        cb(data);
        return;
      }

      if (data.type === 'whiteboard-stroke') {
        this.emitLocal('whiteboard-stroke', data.stroke);
        return;
      }

      this.emitLocal(data.type, data);
    });

    this.ws.addEventListener('error', () => {
      this.emitLocal('error', { message: 'Ошибка WebSocket' });
    });

    this.ws.addEventListener('close', () => {
      this._stopPing();
      if (this.pendingJoin) {
        const cb = this.pendingJoin;
        this.pendingJoin = null;
        clearTimeout(this.joinTimer);
        cb({ ok: false, error: 'Соединение с сервером закрыто. Пробуем снова…' });
      }
      this.emitLocal('disconnected', {});
      this._scheduleReconnect();
    });
  }

  _startPing() {
    this._stopPing();
    this.pingTimer = setInterval(() => {
      this.send('ping', { t: Date.now() });
    }, 20000);
  }

  _stopPing() {
    clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  _scheduleReconnect() {
    if (!this.shouldReconnect) return;
    this.reconnectAttempt += 1;
    const delay = Math.min(1000 * 2 ** Math.min(this.reconnectAttempt, 4), 12000);
    setTimeout(() => this.connect(), delay);
  }

  send(type, payload = {}) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ type, ...payload }));
    return true;
  }

  whenOpen(callback, onFail) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      callback();
      return;
    }

    let settled = false;
    const finishOk = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const set = this.handlers.get('connected');
      set?.delete(finishOk);
      callback();
    };

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      const set = this.handlers.get('connected');
      set?.delete(finishOk);
      onFail?.('Не удалось подключиться к серверу (WebSocket).');
    }, 8000);

    this.on('connected', finishOk);
    this.connect();
  }

  joinRoom({ code, name, role }, callback) {
    this.lastJoin = { code, name, role };
    this.pendingJoin = callback;
    clearTimeout(this.joinTimer);
    this.joinTimer = setTimeout(() => {
      if (!this.pendingJoin) return;
      const cb = this.pendingJoin;
      this.pendingJoin = null;
      cb({ ok: false, error: 'Сервер не ответил на вход в комнату.' });
    }, 10000);

    this.whenOpen(
      () => {
        const sent = this.send('join-room', { code, name, role });
        if (!sent && this.pendingJoin) {
          const cb = this.pendingJoin;
          this.pendingJoin = null;
          clearTimeout(this.joinTimer);
          cb({ ok: false, error: 'Нет соединения с сервером.' });
        }
      },
      (message) => {
        if (!this.pendingJoin) return;
        const cb = this.pendingJoin;
        this.pendingJoin = null;
        clearTimeout(this.joinTimer);
        cb({ ok: false, error: message });
      }
    );
  }
}

window.LessonSocket = LessonSocket;
