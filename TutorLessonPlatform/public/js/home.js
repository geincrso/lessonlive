const createForm = document.getElementById('create-form');
const joinForm = document.getElementById('join-form');
const createError = document.getElementById('create-error');
const joinError = document.getElementById('join-error');
const createSubmit = document.getElementById('create-submit');
const joinSubmit = document.getElementById('join-submit');
const tabs = document.querySelectorAll('.tab');

const FETCH_MS = 8000;

function showError(el, message) {
  el.hidden = !message;
  el.textContent = message || '';
}

function setLoading(button, loading, labelWhenDone) {
  button.disabled = loading;
  button.classList.toggle('is-loading', loading);
  const label = button.querySelector('.btn-label');
  if (label && labelWhenDone && !loading) {
    label.textContent = labelWhenDone;
  }
  if (label && loading) {
    label.dataset.prev = label.textContent;
    label.textContent = 'Подождите…';
  }
}

async function fetchJson(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_MS);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      cache: 'no-store',
    });
    const data = await response.json().catch(() => ({}));
    return { response, data };
  } finally {
    clearTimeout(timer);
  }
}

function goToLesson({ code, name, role }) {
  const params = new URLSearchParams({
    code: String(code).toUpperCase(),
    name,
    role,
  });
  window.location.href = `/lesson.html?${params.toString()}`;
}

tabs.forEach((tab) => {
  tab.addEventListener('click', () => {
    const target = tab.dataset.tab;
    tabs.forEach((t) => {
      const active = t === tab;
      t.classList.toggle('is-active', active);
      t.setAttribute('aria-selected', String(active));
    });
    createForm.hidden = target !== 'create';
    joinForm.hidden = target !== 'join';
    createForm.classList.toggle('is-active', target === 'create');
    joinForm.classList.toggle('is-active', target === 'join');
    showError(createError, '');
    showError(joinError, '');
  });
});

createForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(createError, '');

  const name = document.getElementById('create-name').value.trim();
  const title = document.getElementById('create-title').value.trim();
  if (!name || !title) {
    showError(createError, 'Заполните имя и название урока');
    return;
  }

  setLoading(createSubmit, true);
  try {
    const { response, data } = await fetchJson('/api/rooms', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, title }),
    });
    if (!response.ok) {
      throw new Error(data.error || 'Не удалось создать комнату');
    }
    sessionStorage.setItem('lessonName', name);
    sessionStorage.setItem('lessonRole', 'tutor');
    goToLesson({ code: data.code, name, role: 'tutor' });
  } catch (error) {
    const message =
      error.name === 'AbortError'
        ? 'Сервер не ответил вовремя. Проверьте, что он запущен.'
        : error.message || 'Ошибка сети';
    showError(createError, message);
    setLoading(createSubmit, false, 'Создать комнату');
  }
});

joinForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  showError(joinError, '');

  const name = document.getElementById('join-name').value.trim();
  const code = document.getElementById('join-code').value.trim().toUpperCase();
  const role = document.getElementById('join-role').value;

  if (!name || !code) {
    showError(joinError, 'Укажите имя и код комнаты');
    return;
  }

  setLoading(joinSubmit, true);
  try {
    const { response, data } = await fetchJson(`/api/rooms/${encodeURIComponent(code)}`);
    if (!response.ok) {
      throw new Error(data.error || 'Комната не найдена');
    }
    sessionStorage.setItem('lessonName', name);
    sessionStorage.setItem('lessonRole', role);
    goToLesson({ code: data.code, name, role });
  } catch (error) {
    const message =
      error.name === 'AbortError'
        ? 'Сервер не ответил вовремя. Проверьте, что он запущен.'
        : error.message || 'Ошибка сети';
    showError(joinError, message);
    setLoading(joinSubmit, false, 'Войти в урок');
  }
});
