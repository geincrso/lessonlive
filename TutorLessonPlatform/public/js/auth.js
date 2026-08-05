const TOKEN_KEY = 'uroklive_token';
const USER_KEY = 'uroklive_user';

const Auth = {
  getToken() {
    return localStorage.getItem(TOKEN_KEY) || '';
  },

  getUser() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || 'null');
    } catch {
      return null;
    }
  },

  setSession({ token, user }) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },

  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },

  async api(path, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };
    const token = this.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    const response = await fetch(path, {
      ...options,
      headers,
      cache: 'no-store',
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(data.error || 'Ошибка запроса');
      err.status = response.status;
      err.data = data;
      throw err;
    }
    return data;
  },

  async requireUser() {
    const token = this.getToken();
    if (!token) return null;
    try {
      const data = await this.api('/api/me');
      this.setSession({ token, user: data.user });
      return data.user;
    } catch {
      this.clear();
      return null;
    }
  },
};

window.Auth = Auth;
