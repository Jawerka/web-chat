/**
 * Страница входа: сессия HttpOnly cookie + тема (webchat_theme).
 */
(function () {
  const form = document.getElementById('login-form');
  const errEl = document.getElementById('login-error');
  const submitBtn = document.getElementById('login-submit');
  const themeBtn = document.getElementById('login-theme-toggle');
  const themeLabel = document.getElementById('login-theme-toggle-label');
  const metaTheme = document.getElementById('meta-theme-color');

  const params = new URLSearchParams(window.location.search);
  const nextPath = params.get('next') || '/';

  const THEME_COLORS = { light: '#f0f2f5', dark: '#0f1419' };

  function isDarkTheme() {
    return document.body.classList.contains('dark-theme');
  }

  function applyTheme(dark) {
    if (window.WebChatThemeBoot) {
      window.WebChatThemeBoot.applyTheme(dark);
    } else {
      document.body.classList.toggle('dark-theme', dark);
      document.documentElement.classList.toggle('login-dark', dark);
    }
    if (metaTheme) {
      metaTheme.setAttribute('content', dark ? THEME_COLORS.dark : THEME_COLORS.light);
    }
    if (themeLabel) {
      themeLabel.textContent = dark ? 'Тёмная' : 'Светлая';
    }
    if (themeBtn) {
      themeBtn.title = dark ? 'Включить светлую тему' : 'Включить тёмную тему';
    }
  }

  function loadTheme() {
    if (window.WebChatThemeBoot) {
      applyTheme(window.WebChatThemeBoot.resolveDark());
      return;
    }
    const stored = localStorage.getItem('webchat_theme');
    const dark = stored === 'dark'
      || (!stored && window.matchMedia('(prefers-color-scheme: dark)').matches);
    applyTheme(dark);
  }

  function toggleTheme() {
    const dark = !isDarkTheme();
    localStorage.setItem('webchat_theme', dark ? 'dark' : 'light');
    applyTheme(dark);
  }

  themeBtn?.addEventListener('click', toggleTheme);

  window.addEventListener('storage', (e) => {
    if (e.key !== 'webchat_theme') return;
    applyTheme(e.newValue === 'dark');
  });

  loadTheme();

  async function checkAlreadyLoggedIn() {
    try {
      const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
      if (res.ok) {
        window.location.replace(nextPath.startsWith('/') ? nextPath : '/');
      }
    } catch {
      /* ignore */
    }
  }

  function showError(msg) {
    if (!errEl) return;
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    errEl?.classList.add('hidden');
    const login = document.getElementById('login-input')?.value?.trim() || '';
    const password = document.getElementById('password-input')?.value || '';
    if (!login || !password) {
      showError('Введите логин и пароль');
      return;
    }
    submitBtn.disabled = true;
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ login, password }),
      });
      if (!res.ok) {
        let detail = 'Неверный логин или пароль';
        try {
          const body = await res.json();
          detail = typeof body.detail === 'string' ? body.detail : detail;
        } catch { /* ignore */ }
        showError(detail);
        return;
      }
      window.location.replace(nextPath.startsWith('/') ? nextPath : '/');
    } catch {
      showError('Ошибка сети');
    } finally {
      submitBtn.disabled = false;
    }
  });

  void checkAlreadyLoggedIn();
})();
