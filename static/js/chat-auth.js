/**
 * Аутентификация в настройках чата: аккаунт, смена пароля, admin users.
 * Подключается до chat.js; API: WebChatAuth.*
 */
(function () {
  'use strict';

  function initUi(app) {
    if (!app.config.auth_enabled || !app.currentUser) return;
    app.$.accountSection?.classList.remove('hidden');
    const u = app.currentUser;
    if (app.$.accountLogin) {
      app.$.accountLogin.textContent = u.display_name || u.login;
    }
    if (app.$.accountRole) {
      const isAdmin = u.role === 'admin';
      app.$.accountRole.textContent = isAdmin ? 'Администратор' : 'Пользователь';
      app.$.accountRole.classList.toggle('is-admin', isAdmin);
    }
    if (u.role === 'admin') {
      app.$.adminSection?.classList.remove('hidden');
      void refreshAdminUsersList(app);
    }
  }

  function bindEvents(app) {
    app.$.authLogoutBtn?.addEventListener('click', () => logout());
    app.$.createUserForm?.addEventListener('submit', (e) => {
      e.preventDefault();
      void createUserFromSettings(app);
    });
    app.$.changePasswordForm?.addEventListener('submit', (e) => {
      e.preventDefault();
      void changePasswordFromSettings(app);
    });
  }

  async function logout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
    } catch { /* ignore */ }
    window.location.replace('/login');
  }

  async function refreshAdminUsersList(app) {
    if (!app.config.auth_enabled || app.currentUser?.role !== 'admin' || !app.$.usersList) {
      return;
    }
    try {
      const users = await app.api('/api/users');
      app.$.usersList.replaceChildren();
      if (!users?.length) {
        const li = document.createElement('li');
        li.textContent = 'Нет пользователей';
        app.$.usersList.appendChild(li);
        return;
      }
      for (const user of users) {
        const li = document.createElement('li');
        const login = document.createElement('span');
        login.className = 'user-login';
        login.textContent = user.login;
        const role = document.createElement('span');
        role.className = 'user-role';
        role.textContent = user.role === 'admin' ? 'admin' : 'user';
        li.append(login, role);
        app.$.usersList.appendChild(li);
      }
    } catch (err) {
      app.log?.warn('auth', 'Не удалось загрузить список пользователей', err?.message);
    }
  }

  async function changePasswordFromSettings(app) {
    if (!app.$.changePasswordForm) return;
    app.$.changePasswordError?.classList.add('hidden');
    const current_password = document.getElementById('settings-change-current-password')?.value || '';
    const new_password = document.getElementById('settings-change-new-password')?.value || '';
    const btn = document.getElementById('settings-change-password-btn');
    if (btn) btn.disabled = true;
    try {
      await app.api('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password, new_password }),
      });
      app.$.changePasswordForm.reset();
    } catch (err) {
      if (app.$.changePasswordError) {
        app.$.changePasswordError.textContent = err?.message || 'Ошибка смены пароля';
        app.$.changePasswordError.classList.remove('hidden');
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function createUserFromSettings(app) {
    if (!app.$.createUserForm) return;
    app.$.createUserError?.classList.add('hidden');
    const login = document.getElementById('settings-new-login')?.value?.trim() || '';
    const password = document.getElementById('settings-new-password')?.value || '';
    const display_name = document.getElementById('settings-new-display')?.value?.trim() || undefined;
    const role = document.getElementById('settings-new-role')?.value || 'user';
    const btn = document.getElementById('settings-create-user-btn');
    if (btn) btn.disabled = true;
    try {
      await app.api('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login, password, display_name, role }),
      });
      app.$.createUserForm.reset();
      await refreshAdminUsersList(app);
    } catch (err) {
      if (app.$.createUserError) {
        app.$.createUserError.textContent = err?.message || 'Ошибка создания';
        app.$.createUserError.classList.remove('hidden');
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  window.WebChatAuth = {
    initUi,
    bindEvents,
    logout,
    refreshAdminUsersList,
    changePasswordFromSettings,
    createUserFromSettings,
  };
})();
