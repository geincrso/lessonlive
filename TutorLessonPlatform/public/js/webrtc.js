class LessonCall {
  constructor({
    socket,
    localVideo,
    remoteVideo,
    remoteAudio,
    remoteLabel,
    onStatus,
    onAudioBlocked,
  }) {
    this.socket = socket;
    this.localVideo = localVideo;
    this.remoteVideo = remoteVideo;
    this.remoteAudio = remoteAudio || null;
    this.remoteLabel = remoteLabel;
    this.onStatus = onStatus;
    this.onAudioBlocked = onAudioBlocked;
    this.localStream = null;
    this.remoteStream = null;
    this.remoteAudioStream = null;
    this.peer = null;
    this.remoteId = null;
    this.remoteName = null;
    this.selfId = null;
    this.makingOffer = false;
    this.ignoreOffer = false;
    this.isPolite = false;
    this.startingMedia = null;
    this.pendingIce = [];
    this.restartTimer = null;
    this.recoverTimer = null;
    this.audioCtx = null;
    this.audioSource = null;
    this.audioUnlocked = false;
    this.hasRemoteAudio = false;

    this.remoteVideo.muted = true;
    if (this.remoteAudio) {
      this.remoteAudio.autoplay = true;
      this.remoteAudio.muted = false;
      this.remoteAudio.volume = 1;
      this.remoteAudio.setAttribute('playsinline', '');
    }

    this._bindSocket();
  }

  async queryMicPermission() {
    try {
      if (!navigator.permissions?.query) return 'unknown';
      const status = await navigator.permissions.query({ name: 'microphone' });
      return status.state; // granted | denied | prompt
    } catch {
      return 'unknown';
    }
  }

  hasLiveMic() {
    return Boolean(
      this.localStream
        ?.getAudioTracks()
        .some((track) => track.readyState === 'live')
    );
  }

  _withTimeout(promise, ms, label = 'Операция') {
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        const err = new Error(`${label} заняла слишком много времени. Попробуйте ещё раз.`);
        err.name = 'TimeoutError';
        reject(err);
      }, ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
  }

  async unlockAudio() {
    this.audioUnlocked = true;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) {
        if (!this.audioCtx) this.audioCtx = new Ctx();
        if (this.audioCtx.state === 'suspended') {
          await this._withTimeout(this.audioCtx.resume(), 800, 'Разблокировка звука').catch(() => {});
        }
      }
    } catch (error) {
      console.warn('AudioContext unlock failed', error);
    }

    this._wireAudioGraph();
    const played = await this._playRemoteElements();
    if (played || (this.audioCtx && this.audioCtx.state === 'running' && this.audioSource)) {
      this.onAudioBlocked?.(false);
      return true;
    }
    if (this.hasRemoteAudio || !this.hasLiveMic()) this.onAudioBlocked?.(true);
    return false;
  }

  /**
   * Click handler: request mic FIRST (while user-gesture is fresh), then unlock playback.
   */
  async enableSoundAndMic() {
    // Kick AudioContext immediately, but never block the mic prompt on playback.
    void this.unlockAudio();

    const micState = await this.queryMicPermission().catch(() => 'unknown');
    if (!this.hasLiveMic()) {
      if (micState === 'denied') {
        const message =
          'Микрофон запрещён для сайта. Нажмите на замок/иконку слева от адреса → «Разрешить» микрофон и камеру, затем нажмите кнопку снова.';
        this.onStatus?.(message);
        this.onAudioBlocked?.(true);
        return { ok: false, error: message, needSettings: true };
      }

      try {
        this.onStatus?.('Разрешите микрофон в окне браузера…');
        // Do not await unlockAudio here — getUserMedia must stay in the click gesture.
        await this._withTimeout(
          this._requestMedia(),
          20000,
          'Запрос микрофона'
        );
        await this.publishLocalMedia();
        this.onStatus?.('Микрофон подключён');
      } catch (error) {
        const message =
          error?.name === 'TimeoutError'
            ? 'Браузер не ответил на запрос микрофона. Нажмите на замок у адреса и разрешите микрофон, затем попробуйте снова.'
            : error?.message || this._mediaErrorMessage(error);
        this.onStatus?.(message);
        this.onAudioBlocked?.(true);
        return {
          ok: false,
          error: message,
          needSettings:
            error?.name === 'NotAllowedError' ||
            error?.name === 'PermissionDeniedError' ||
            error?.name === 'TimeoutError' ||
            micState === 'denied',
        };
      }
    } else {
      await this.publishLocalMedia();
    }

    const played = await this.unlockAudio();
    const ok = played || this.hasLiveMic();
    this.onAudioBlocked?.(!played && this.hasRemoteAudio);
    return { ok, needSettings: false };
  }

  async publishLocalMedia() {
    if (!this.localStream) return;
    if (!this.peer || !this.remoteId) return;

    try {
      const transceivers = this.peer.getTransceivers();
      for (const track of this.localStream.getTracks()) {
        track.enabled = true;
        const sender = this.peer.getSenders().find((item) => item.track?.kind === track.kind);
        if (sender) {
          await sender.replaceTrack(track);
        } else {
          const transceiver = transceivers.find((item) => item.receiver?.track?.kind === track.kind);
          if (transceiver) {
            await transceiver.sender.replaceTrack(track);
            try {
              transceiver.direction = 'sendrecv';
            } catch {
              /* ignore */
            }
          } else {
            this.peer.addTrack(track, this.localStream);
          }
        }
      }

      for (const transceiver of this.peer.getTransceivers()) {
        if (transceiver.sender?.track) {
          try {
            transceiver.direction = 'sendrecv';
          } catch {
            /* ignore */
          }
        }
      }

      await this._withTimeout(this._makeOffer(false), 5000, 'Переподключение медиа').catch((error) => {
        console.warn(error);
      });
    } catch (error) {
      console.warn('publishLocalMedia failed', error);
    }
  }

  _wireAudioGraph() {
    if (!this.audioCtx || !this.remoteAudioStream) return;
    const tracks = this.remoteAudioStream.getAudioTracks().filter((t) => t.readyState === 'live');
    if (!tracks.length) return;

    try {
      if (this.audioSource) {
        this.audioSource.disconnect();
        this.audioSource = null;
      }
      const stream = new MediaStream(tracks);
      this.audioSource = this.audioCtx.createMediaStreamSource(stream);
      this.audioSource.connect(this.audioCtx.destination);
    } catch (error) {
      console.warn('Audio graph wire failed', error);
    }
  }

  async _playRemoteElements() {
    try {
      this.remoteVideo.muted = true;
      await this._withTimeout(this.remoteVideo.play().catch(() => {}), 500, 'video.play').catch(() => {});
      if (this.remoteAudio && this.remoteAudio.srcObject) {
        this.remoteAudio.muted = false;
        this.remoteAudio.volume = 1;
        await this._withTimeout(this.remoteAudio.play(), 800, 'audio.play');
        return !this.remoteAudio.paused;
      }
    } catch (error) {
      console.warn('HTMLMediaElement play failed', error);
    }
    return false;
  }

  _bindSocket() {
    this.socket.on('webrtc-offer', async ({ from, sdp }) => {
      try {
        await this._ensurePeer(from);
        const offerCollision = this.makingOffer || this.peer.signalingState !== 'stable';
        this.ignoreOffer = !this.isPolite && offerCollision;
        if (this.ignoreOffer) return;

        await this.peer.setRemoteDescription(sdp);
        await this._flushIce();
        const answer = await this.peer.createAnswer();
        await this.peer.setLocalDescription(answer);
        this.socket.send('webrtc-answer', {
          to: from,
          sdp: this.peer.localDescription,
        });
      } catch (error) {
        console.error(error);
        this.onStatus?.('Ошибка видеосвязи');
      }
    });

    this.socket.on('webrtc-answer', async ({ from, sdp }) => {
      try {
        if (!this.peer || this.remoteId !== from) return;
        if (this.peer.signalingState === 'stable') return;
        await this.peer.setRemoteDescription(sdp);
        await this._flushIce();
      } catch (error) {
        console.error(error);
      }
    });

    this.socket.on('webrtc-ice', async ({ from, candidate }) => {
      try {
        if (!candidate || this.remoteId !== from) return;
        if (!this.peer || !this.peer.remoteDescription) {
          this.pendingIce.push(candidate);
          return;
        }
        await this.peer.addIceCandidate(candidate);
      } catch (error) {
        if (!this.ignoreOffer) console.error(error);
      }
    });
  }

  async _flushIce() {
    if (!this.peer) return;
    const queued = this.pendingIce.splice(0);
    for (const candidate of queued) {
      try {
        await this.peer.addIceCandidate(candidate);
      } catch (error) {
        console.warn('ICE candidate skipped', error);
      }
    }
  }

  _mediaErrorMessage(error) {
    const name = error?.name || '';
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
      return 'Доступ запрещён. Нажмите на иконку замка в адресной строке и разрешите камеру и микрофон.';
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      return 'Камера или микрофон не найдены. Подключите устройство и попробуйте снова.';
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      return 'Устройство занято другим приложением. Закройте Zoom/Telegram/другие программы и повторите.';
    }
    if (name === 'SecurityError') {
      return 'Браузер блокирует камеру на этом адресе. Откройте сайт через http://127.0.0.1:3000 или по HTTPS.';
    }
    return error?.message || 'Не удалось получить доступ к камере/микрофону.';
  }

  async startLocalMedia({ audioOnly = false } = {}) {
    if (this.localStream) return this.localStream;
    if (this.startingMedia) return this.startingMedia;

    if (!navigator.mediaDevices?.getUserMedia) {
      const err = new Error(
        window.isSecureContext
          ? 'Браузер не поддерживает getUserMedia.'
          : 'Камера недоступна: откройте сайт по HTTPS (https://127.0.0.1:3443)'
      );
      err.name = 'SecurityError';
      throw err;
    }

    // Unlock playback without blocking getUserMedia on a hanging play().
    void this.unlockAudio();

    this.startingMedia = this._withTimeout(
      this._requestMedia({ audioOnly }),
      20000,
      'Запрос микрофона'
    );
    try {
      return await this.startingMedia;
    } finally {
      this.startingMedia = null;
    }
  }

  async _requestMedia({ audioOnly = false } = {}) {
    // echoCancellation:false — иначе при тесте в двух вкладках на одном ПК
    // браузер часто полностью глушит входящий голос как «эхо».
    const attempts = audioOnly
      ? [
          {
            audio: {
              echoCancellation: false,
              noiseSuppression: false,
              autoGainControl: true,
            },
            video: false,
          },
          { audio: true, video: false },
        ]
      : [
          {
            audio: {
              echoCancellation: false,
              noiseSuppression: false,
              autoGainControl: true,
            },
            video: true,
          },
          { audio: true, video: true },
          { audio: true, video: false },
        ];

    let lastError = null;
    for (const constraints of attempts) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        const audioTracks = stream.getAudioTracks();
        if (!audioTracks.length) {
          stream.getTracks().forEach((track) => track.stop());
          throw Object.assign(new Error('Микрофон не получен'), { name: 'NotFoundError' });
        }
        audioTracks.forEach((track) => {
          track.enabled = true;
        });

        this.localStream = stream;
        this.localVideo.srcObject = stream;
        this.localVideo.muted = true;
        await this.localVideo.play().catch(() => {});
        if (!stream.getVideoTracks().length) {
          this.onStatus?.('Видео недоступно — только звук');
        }
        return stream;
      } catch (error) {
        lastError = error;
        console.warn('getUserMedia attempt failed', constraints, error);
        if (error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError') {
          break;
        }
      }
    }

    const wrapped = new Error(this._mediaErrorMessage(lastError));
    wrapped.name = lastError?.name || 'MediaError';
    wrapped.cause = lastError;
    throw wrapped;
  }

  setSelf(selfId, participants) {
    this.selfId = selfId;
    const others = participants.filter((p) => p.id !== selfId);
    if (others.length) {
      const remote = others[0];
      this.isPolite = selfId > remote.id;
      this.connectTo(remote.id, remote.name);
    }
  }

  async connectTo(remoteId, remoteName) {
    if (remoteName) {
      this.remoteName = remoteName;
      this.remoteLabel.textContent = remoteName;
    }
    if (this.remoteId === remoteId && this.peer) {
      const state = this.peer.connectionState;
      if (state === 'connected' || state === 'connecting') return;
    }
    this.remoteId = remoteId;
    await this._ensurePeer(remoteId);
    if (!this.isPolite) {
      await this._makeOffer(false);
    }
  }

  handleParticipantJoined(participant) {
    if (!participant || participant.id === this.selfId) return;
    this.isPolite = this.selfId > participant.id;
    this.remoteName = participant.name;
    this.remoteLabel.textContent = participant.name;
    this.connectTo(participant.id, participant.name);
  }

  handleParticipantLeft(id) {
    if (this.remoteId !== id) return;
    this._scheduleSoftReset('Связь прервалась, ждём переподключения…');
  }

  _scheduleSoftReset(message) {
    clearTimeout(this.restartTimer);
    this.onStatus?.(message);
    this.remoteLabel.textContent = this.remoteName
      ? `Переподключение: ${this.remoteName}…`
      : 'Переподключение…';
    this.restartTimer = setTimeout(() => {
      if (this.peer && ['connected', 'connecting'].includes(this.peer.connectionState)) {
        this.remoteLabel.textContent = this.remoteName || 'Собеседник';
        return;
      }
      this._teardownPeer(false);
      this._clearRemoteMedia();
      this.remoteLabel.textContent = 'Ожидание собеседника…';
      this.onStatus?.('Собеседник пока не в сети');
    }, 5000);
  }

  _clearRemoteMedia() {
    this.remoteStream = null;
    this.remoteAudioStream = null;
    this.hasRemoteAudio = false;
    this.remoteVideo.srcObject = null;
    if (this.remoteAudio) this.remoteAudio.srcObject = null;
    if (this.audioSource) {
      try {
        this.audioSource.disconnect();
      } catch {
        /* ignore */
      }
      this.audioSource = null;
    }
  }

  async _ensurePeer(remoteId) {
    if (this.peer && this.remoteId === remoteId) return this.peer;
    this._teardownPeer(true);
    this.remoteId = remoteId;
    this.pendingIce = [];

    const peer = new RTCPeerConnection({
      iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun1.l.google.com:19302' },
      ],
    });
    this.peer = peer;

    if (this.localStream) {
      for (const track of this.localStream.getTracks()) {
        track.enabled = true;
        peer.addTrack(track, this.localStream);
      }
    } else {
      peer.addTransceiver('audio', { direction: 'recvonly' });
      peer.addTransceiver('video', { direction: 'recvonly' });
    }

    peer.onicecandidate = (event) => {
      if (!event.candidate || !this.remoteId) return;
      this.socket.send('webrtc-ice', {
        to: this.remoteId,
        candidate: event.candidate,
      });
    };

    peer.ontrack = (event) => {
      event.track.enabled = true;

      if (event.track.kind === 'audio') {
        this.hasRemoteAudio = true;
        this.remoteAudioStream = new MediaStream([event.track]);
        if (this.remoteAudio) {
          this.remoteAudio.srcObject = this.remoteAudioStream;
        }
        // Also keep audio in a combined stream for debugging/fallback.
        if (!this.remoteStream) this.remoteStream = new MediaStream();
        if (!this.remoteStream.getTracks().some((t) => t.id === event.track.id)) {
          this.remoteStream.addTrack(event.track);
        }
        this._wireAudioGraph();
        this.onStatus?.('Звук собеседника подключён');
        this._playRemote();
        return;
      }

      if (!this.remoteStream) this.remoteStream = new MediaStream();
      if (!this.remoteStream.getTracks().some((t) => t.id === event.track.id)) {
        this.remoteStream.addTrack(event.track);
      }
      this.remoteVideo.srcObject = new MediaStream(
        this.remoteStream.getVideoTracks()
      );
      clearTimeout(this.restartTimer);
      this.remoteLabel.textContent = this.remoteName || 'Собеседник';
      this.onStatus?.('Видеосвязь установлена');
      this._playRemote();
    };

    peer.onconnectionstatechange = () => {
      const state = peer.connectionState;
      if (state === 'connected') {
        clearTimeout(this.restartTimer);
        clearTimeout(this.recoverTimer);
        this.remoteLabel.textContent = this.remoteName || 'Собеседник';
        this._playRemote();
      } else if (state === 'failed') {
        this.onStatus?.('Соединение потеряно, переподключение…');
        this._recover(true);
      }
    };

    return peer;
  }

  async _playRemote() {
    if (this.audioUnlocked && this.audioCtx?.state === 'suspended') {
      await this.audioCtx.resume().catch(() => {});
    }
    this._wireAudioGraph();
    const played = await this._playRemoteElements();
    if (played || (this.hasRemoteAudio && this.audioCtx?.state === 'running' && this.audioSource)) {
      this.onAudioBlocked?.(false);
      return;
    }
    if (this.hasRemoteAudio) {
      this.onAudioBlocked?.(true);
      this.onStatus?.('Нажмите «Включить звук»');
    }
  }

  _recover(iceRestart) {
    clearTimeout(this.recoverTimer);
    this.recoverTimer = setTimeout(() => {
      if (!this.peer || !this.remoteId) return;
      if (this.peer.connectionState === 'connected') return;
      this._makeOffer(iceRestart).catch((error) => {
        console.warn('recover failed', error);
      });
    }, 1500);
  }

  async _makeOffer(iceRestart = false) {
    if (!this.peer || !this.remoteId) return;
    if (this.makingOffer) return;
    try {
      this.makingOffer = true;
      const offer = await this.peer.createOffer(iceRestart ? { iceRestart: true } : undefined);
      if (this.peer.signalingState !== 'stable' && this.peer.signalingState !== 'have-local-offer') {
        return;
      }
      await this.peer.setLocalDescription(offer);
      this.socket.send('webrtc-offer', {
        to: this.remoteId,
        sdp: this.peer.localDescription,
      });
    } finally {
      this.makingOffer = false;
    }
  }

  _teardownPeer(keepRemoteId) {
    clearTimeout(this.restartTimer);
    clearTimeout(this.recoverTimer);
    if (this.peer) {
      this.peer.onicecandidate = null;
      this.peer.ontrack = null;
      this.peer.onconnectionstatechange = null;
      try {
        this.peer.close();
      } catch {
        /* ignore */
      }
    }
    this.peer = null;
    this.pendingIce = [];
    this.makingOffer = false;
    this.ignoreOffer = false;
    if (!keepRemoteId) this.remoteId = null;
  }

  setMicEnabled(enabled) {
    this.localStream?.getAudioTracks().forEach((track) => {
      track.enabled = enabled;
    });
  }

  setCamEnabled(enabled) {
    this.localStream?.getVideoTracks().forEach((track) => {
      track.enabled = enabled;
    });
  }

  stop() {
    this._teardownPeer(false);
    if (this.localStream) {
      this.localStream.getTracks().forEach((track) => track.stop());
      this.localStream = null;
    }
    this.localVideo.srcObject = null;
    this._clearRemoteMedia();
    if (this.audioCtx) {
      this.audioCtx.close().catch(() => {});
      this.audioCtx = null;
    }
  }
}

window.LessonCall = LessonCall;
