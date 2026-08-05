class Whiteboard {
  constructor(canvas, { onDraw, onStatus } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onDraw = onDraw;
    this.onStatus = onStatus;
    this.tool = 'pen';
    this.color = '#1f3d2b';
    this.penWidth = 4;
    this.eraserWidth = 28;
    this.drawing = false;
    this.currentPoints = [];
    this.strokeId = null;
    this.strokes = [];
    this.remoteLive = new Map();
    this.dpr = window.devicePixelRatio || 1;
    this.cursorPos = null;
    this.lastEmitAt = 0;

    this.wrap = canvas.parentElement;
    this.cursorEl = document.createElement('div');
    this.cursorEl.className = 'eraser-cursor';
    this.cursorEl.hidden = true;
    this.wrap.appendChild(this.cursorEl);

    this._onPointerDown = this._onPointerDown.bind(this);
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
    this._onPointerLeave = this._onPointerLeave.bind(this);
    this._onResize = this._onResize.bind(this);

    canvas.addEventListener('pointerdown', this._onPointerDown);
    canvas.addEventListener('pointermove', this._onPointerMove);
    canvas.addEventListener('pointerleave', this._onPointerLeave);
    window.addEventListener('pointerup', this._onPointerUp);
    window.addEventListener('pointercancel', this._onPointerUp);
    window.addEventListener('resize', this._onResize);

    this._syncCursorStyle();
    this._onResize();
  }

  get width() {
    return this.tool === 'eraser' ? this.eraserWidth : this.penWidth;
  }

  setTool(tool) {
    this.tool = tool === 'eraser' ? 'eraser' : 'pen';
    this._syncCursorStyle();
    this._renderCursor();
  }

  setColor(color) {
    this.color = color;
  }

  setWidth(width) {
    const value = Number(width) || 4;
    if (this.tool === 'eraser') {
      this.eraserWidth = Math.min(48, Math.max(2, value));
    } else {
      this.penWidth = Math.min(24, Math.max(2, value));
    }
    this._renderCursor();
  }

  loadStrokes(strokes) {
    this.strokes = Array.isArray(strokes) ? strokes.slice() : [];
    this.remoteLive.clear();
    this.redraw();
  }

  /** Apply live remote drawing updates. */
  applyRemoteDraw(draw) {
    if (!draw?.strokeId) return;
    const phase = draw.phase;
    const points = Array.isArray(draw.points) ? draw.points : [];

    if (phase === 'start') {
      this.remoteLive.set(draw.strokeId, {
        id: draw.strokeId,
        tool: draw.tool === 'eraser' ? 'eraser' : 'pen',
        color: draw.color || '#1a1a1a',
        width: draw.width || 4,
        points: points.slice(),
      });
      if (points.length >= 2) {
        this._drawStroke({
          tool: draw.tool,
          color: draw.color,
          width: draw.width,
          points,
        });
      }
      return;
    }

    if (phase === 'move') {
      let live = this.remoteLive.get(draw.strokeId);
      if (!live) {
        live = {
          id: draw.strokeId,
          tool: draw.tool === 'eraser' ? 'eraser' : 'pen',
          color: draw.color || '#1a1a1a',
          width: draw.width || 4,
          points: [],
        };
        this.remoteLive.set(draw.strokeId, live);
      }
      if (points.length) {
        const prev = live.points[live.points.length - 1];
        const segment = prev ? [prev, ...points] : points;
        if (segment.length >= 2) {
          this._drawStroke({
            tool: live.tool,
            color: live.color,
            width: live.width,
            points: segment.length > 2 ? segment.slice(-2) : segment,
          });
        }
        live.points.push(...points);
      }
      return;
    }

    if (phase === 'end') {
      const hadLive = this.remoteLive.has(draw.strokeId);
      this.remoteLive.delete(draw.strokeId);
      const stroke = {
        id: draw.strokeId,
        tool: draw.tool === 'eraser' ? 'eraser' : 'pen',
        color: draw.color || '#1a1a1a',
        width: draw.width || 4,
        points: points.length ? points : [],
      };
      if (stroke.points.length >= 2) {
        this.strokes.push(stroke);
        // Full stroke without prior live segments (legacy / missed moves).
        if (!hadLive) this._drawStroke(stroke);
      }
    }
  }

  addStroke(stroke) {
    if (!stroke?.points || stroke.points.length < 2) return;
    this.strokes.push(stroke);
    this._drawStroke(stroke);
  }

  clear() {
    this.strokes = [];
    this.remoteLive.clear();
    this.redraw();
    this.onStatus?.('Доска очищена');
  }

  redraw() {
    const { ctx, canvas } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    for (const stroke of this.strokes) {
      this._drawStroke(stroke);
    }
    for (const live of this.remoteLive.values()) {
      if (live.points.length >= 2) this._drawStroke(live);
    }
  }

  _emit(phase, points) {
    this.onDraw?.({
      phase,
      strokeId: this.strokeId,
      tool: this.tool,
      color: this.color,
      width: this.width,
      points,
    });
  }

  _syncCursorStyle() {
    this.canvas.classList.toggle('is-eraser', this.tool === 'eraser');
    this.cursorEl.hidden = this.tool !== 'eraser';
  }

  _renderCursor() {
    if (this.tool !== 'eraser' || !this.cursorPos) {
      this.cursorEl.hidden = true;
      return;
    }
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = rect.width / this.logicalWidth;
    const scaleY = rect.height / this.logicalHeight;
    const size = this.eraserWidth * ((scaleX + scaleY) / 2);
    this.cursorEl.hidden = false;
    this.cursorEl.style.width = `${size}px`;
    this.cursorEl.style.height = `${size}px`;
    this.cursorEl.style.left = `${this.cursorPos.clientX - rect.left}px`;
    this.cursorEl.style.top = `${this.cursorPos.clientY - rect.top}px`;
  }

  _drawStroke(stroke) {
    const points = stroke.points;
    if (!points || points.length < 2) return;

    const ctx = this.ctx;
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.lineWidth = stroke.width || 4;

    if (stroke.tool === 'eraser') {
      ctx.globalCompositeOperation = 'destination-out';
      ctx.strokeStyle = 'rgba(0,0,0,1)';
    } else {
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = stroke.color || '#1a1a1a';
    }

    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i += 1) {
      ctx.lineTo(points[i].x, points[i].y);
    }
    ctx.stroke();
    ctx.restore();
  }

  _pointFromEvent(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * this.logicalWidth;
    const y = ((event.clientY - rect.top) / rect.height) * this.logicalHeight;
    return {
      x: Math.round(x * 10) / 10,
      y: Math.round(y * 10) / 10,
    };
  }

  _onPointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;
    this.canvas.setPointerCapture?.(event.pointerId);
    this.drawing = true;
    this.strokeId = `${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}`;
    const point = this._pointFromEvent(event);
    this.currentPoints = [point];
    this.cursorPos = { clientX: event.clientX, clientY: event.clientY };
    this._renderCursor();
    this._emit('start', [point]);
  }

  _onPointerMove(event) {
    this.cursorPos = { clientX: event.clientX, clientY: event.clientY };
    this._renderCursor();

    if (!this.drawing) return;
    const point = this._pointFromEvent(event);
    const prev = this.currentPoints[this.currentPoints.length - 1];
    if (prev && Math.hypot(point.x - prev.x, point.y - prev.y) < 1.2) return;
    this.currentPoints.push(point);

    const segment = [prev, point];
    this._drawStroke({
      tool: this.tool,
      color: this.color,
      width: this.width,
      points: segment,
    });

    const now = performance.now();
    if (now - this.lastEmitAt >= 24) {
      this.lastEmitAt = now;
      this._emit('move', [point]);
    }
  }

  _onPointerLeave() {
    if (!this.drawing) {
      this.cursorPos = null;
      this.cursorEl.hidden = true;
    }
  }

  _onPointerUp() {
    if (!this.drawing) return;
    this.drawing = false;

    if (this.currentPoints.length < 2) {
      this._emit('end', this.currentPoints.slice());
      this.currentPoints = [];
      this.strokeId = null;
      return;
    }

    // Flush last point if throttling skipped it.
    const last = this.currentPoints[this.currentPoints.length - 1];
    this._emit('move', [last]);

    const stroke = {
      id: this.strokeId,
      tool: this.tool,
      color: this.color,
      width: this.width,
      points: this.currentPoints,
    };
    this.strokes.push(stroke);
    this._emit('end', this.currentPoints);
    this.currentPoints = [];
    this.strokeId = null;
  }

  _onResize() {
    const rect = this.canvas.getBoundingClientRect();
    this.logicalWidth = 1600;
    this.logicalHeight = 1000;
    this.dpr = window.devicePixelRatio || 1;

    const cssWidth = Math.max(rect.width, 1);
    const cssHeight = Math.max(rect.height, 1);
    this.canvas.style.width = `${cssWidth}px`;
    this.canvas.style.height = `${cssHeight}px`;
    this.canvas.width = Math.floor(this.logicalWidth * this.dpr);
    this.canvas.height = Math.floor(this.logicalHeight * this.dpr);
    this.redraw();
    this._renderCursor();
  }
}

window.Whiteboard = Whiteboard;
