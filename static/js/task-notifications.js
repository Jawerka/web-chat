/**
 * Звук и системные уведомления о завершении генерации (Web Audio + Notification API).
 */
(function () {
  'use strict';

  const LS_SOUND = 'webchat_task_sound';
  const LS_NOTIFY = 'webchat_task_notify';

  let audioCtx = null;
  let unlockBound = false;

  function soundEnabled() {
    const v = localStorage.getItem(LS_SOUND);
    return v === null || v === '1';
  }

  function notifyEnabled() {
    const v = localStorage.getItem(LS_NOTIFY);
    return v === null || v === '1';
  }

  function setSoundEnabled(on) {
    localStorage.setItem(LS_SOUND, on ? '1' : '0');
  }

  function setNotifyEnabled(on) {
    localStorage.setItem(LS_NOTIFY, on ? '1' : '0');
  }

  function notificationsSupported() {
    return typeof window !== 'undefined' && 'Notification' in window;
  }

  function permissionState() {
    if (!notificationsSupported()) return 'unsupported';
    return Notification.permission;
  }

  function getAudioContext() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx || audioCtx.state === 'closed') {
      audioCtx = new Ctx();
    }
    return audioCtx;
  }

  async function unlockAudio() {
    const ctx = getAudioContext();
    if (!ctx) return false;
    if (ctx.state === 'suspended') {
      try {
        await ctx.resume();
      } catch {
        return false;
      }
    }
    return ctx.state === 'running';
  }

  function bindUnlockOnGesture() {
    if (unlockBound) return;
    unlockBound = true;
    const unlock = () => {
      void unlockAudio();
    };
    document.addEventListener('pointerdown', unlock, { passive: true, capture: true });
    document.addEventListener('keydown', unlock, { passive: true, capture: true });
    document.addEventListener('touchstart', unlock, { passive: true, capture: true });
  }

  /**
   * Двухтоновый сигнал (стабильнее одного короткого beep).
   */
  async function playDoneSound() {
    if (!soundEnabled()) return;
    const ctx = getAudioContext();
    if (!ctx) return;
    try {
      if (ctx.state !== 'running') {
        await ctx.resume();
      }
      if (ctx.state !== 'running') return;

      const t0 = ctx.currentTime;
      const notes = [
        { freq: 523.25, start: 0, dur: 0.14 },
        { freq: 659.25, start: 0.16, dur: 0.2 },
      ];

      for (const note of notes) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = note.freq;
        gain.gain.setValueAtTime(0, t0 + note.start);
        gain.gain.linearRampToValueAtTime(0.12, t0 + note.start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, t0 + note.start + note.dur);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(t0 + note.start);
        osc.stop(t0 + note.start + note.dur + 0.05);
      }
    } catch {
      /* autoplay policy или недоступный Web Audio */
    }
  }

  async function requestNotificationPermission() {
    if (!notificationsSupported()) {
      return 'unsupported';
    }
    if (Notification.permission === 'granted') {
      return 'granted';
    }
    if (Notification.permission === 'denied') {
      return 'denied';
    }
    try {
      return await Notification.requestPermission();
    } catch {
      return Notification.permission;
    }
  }

  function showNotification({ title, body, tag }) {
    if (!notificationsSupported()) return;
    if (!notifyEnabled()) return;
    if (Notification.permission !== 'granted') return;

    const opts = {
      body: body || 'Генерация завершена',
      tag: tag || 'webchat-task-done',
      renotify: true,
      silent: true,
    };
    const icon = document.querySelector('link[rel="icon"]')?.href;
    if (icon) opts.icon = icon;

    try {
      const n = new Notification(title || 'web-chat', opts);
      n.onclick = () => {
        window.focus();
        n.close();
      };
      setTimeout(() => n.close(), 8000);
    } catch {
      /* Safari / ограничения платформы */
    }
  }

  /**
   * @param {{ title?: string, body?: string, tag?: string, playSound?: boolean, showNotification?: boolean }} opts
   */
  async function notifyTaskDone(opts = {}) {
    const {
      title = 'web-chat',
      body = 'Генерация завершена',
      tag,
      playSound = true,
      showNotification: showNotify = true,
    } = opts;

    if (playSound) {
      await playDoneSound();
    }
    if (showNotify) {
      showNotification({ title, body, tag });
    }
  }

  function permissionLabel() {
    const p = permissionState();
    if (p === 'unsupported') return 'Браузер не поддерживает уведомления';
    if (p === 'granted') return 'Разрешено';
    if (p === 'denied') return 'Запрещено в настройках браузера';
    return 'Не запрошено';
  }

  function updateSettingsUi(root) {
    if (!root) return;
    const soundToggle = root.querySelector('#settings-task-sound');
    const notifyToggle = root.querySelector('#settings-task-notify');
    const permBtn = root.querySelector('#settings-notify-permission-btn');
    const permHint = root.querySelector('#settings-notify-permission-hint');

    if (soundToggle) soundToggle.checked = soundEnabled();
    if (notifyToggle) {
      notifyToggle.checked = notifyEnabled();
      notifyToggle.disabled = !notificationsSupported();
    }
    if (permHint) permHint.textContent = permissionLabel();
    if (permBtn) {
      const p = permissionState();
      permBtn.disabled = !notificationsSupported() || p === 'denied';
      permBtn.textContent = p === 'granted'
        ? 'Уведомления разрешены'
        : 'Разрешить уведомления в браузере';
    }
  }

  function bindSettings(root) {
    if (!root) return;
    updateSettingsUi(root);

    root.querySelector('#settings-task-sound')?.addEventListener('change', (e) => {
      setSoundEnabled(e.target.checked);
      if (e.target.checked) void unlockAudio();
    });

    root.querySelector('#settings-task-notify')?.addEventListener('change', async (e) => {
      const on = e.target.checked;
      setNotifyEnabled(on);
      if (on) {
        await requestNotificationPermission();
        updateSettingsUi(root);
      }
    });

    root.querySelector('#settings-notify-permission-btn')?.addEventListener('click', async () => {
      const result = await requestNotificationPermission();
      updateSettingsUi(root);
      if (result === 'granted') setNotifyEnabled(true);
    });
  }

  function init() {
    bindUnlockOnGesture();
    const panel = document.getElementById('settings-panel');
    if (panel) bindSettings(panel);
  }

  window.TaskNotifications = {
    init,
    bindSettings,
    updateSettingsUi,
    notifyTaskDone,
    playDoneSound,
    requestNotificationPermission,
    permissionState,
    permissionLabel,
    soundEnabled,
    notifyEnabled,
    setSoundEnabled,
    setNotifyEnabled,
    notificationsSupported,
    unlockAudio,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
