/**
 * Общие утилиты галерей: прикрепление изображения в новый чат.
 */
(function () {
  'use strict';

  const PENDING_ATTACHMENTS_KEY = 'webchat_pending_attachments';
  const DEFAULT_CONV_TITLE = 'Новая беседа';
  const IMG2IMG_PRESET_SLUG = 'img2img';

  /**
   * @param {{ url: string, filename?: string }} item
   * @param {{ btn?: HTMLButtonElement, onStatus?: (text: string, isError?: boolean) => void }} [options]
   */
  async function attachImageToNewChat(item, options = {}) {
    const { btn, onStatus } = options;
    if (!item?.url) return;
    const prevDisabled = btn?.disabled;
    if (btn) btn.disabled = true;
    onStatus?.('Создаём чат…', false);
    try {
      const presetsRes = await fetch('/api/presets');
      if (!presetsRes.ok) throw new Error('Не удалось загрузить пресеты');
      const presets = await presetsRes.json();
      const img2imgPreset = presets.find((p) => p.slug === IMG2IMG_PRESET_SLUG);

      const convBody = { title: DEFAULT_CONV_TITLE };
      if (img2imgPreset?.id) convBody.preset_id = img2imgPreset.id;

      const convRes = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(convBody),
      });
      if (!convRes.ok) {
        const errBody = await convRes.json().catch(() => ({}));
        throw new Error(errBody.detail || convRes.statusText);
      }
      const conv = await convRes.json();

      const imgRes = await fetch(item.url);
      if (!imgRes.ok) throw new Error('Не удалось загрузить изображение');
      const blob = await imgRes.blob();
      const mime = blob.type && blob.type.startsWith('image/') ? blob.type : 'image/png';
      const file = new File([blob], item.filename || 'image.png', { type: mime });

      const fd = new FormData();
      fd.append('files', file);
      fd.append('conversation_id', conv.id);

      const upRes = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!upRes.ok) {
        const errBody = await upRes.json().catch(() => ({}));
        throw new Error(errBody.detail || 'Ошибка загрузки вложения');
      }
      const uploadData = await upRes.json();

      sessionStorage.setItem(
        PENDING_ATTACHMENTS_KEY,
        JSON.stringify({
          conversation_id: conv.id,
          attachments: uploadData.attachments || [],
        }),
      );
      localStorage.setItem('webchat_conv_id', conv.id);
      window.location.href = '/';
    } catch (err) {
      onStatus?.(err.message || 'Ошибка', true);
      if (btn) btn.disabled = prevDisabled ?? false;
    }
  }

  window.GalleryCommon = {
    PENDING_ATTACHMENTS_KEY,
    DEFAULT_CONV_TITLE,
    IMG2IMG_PRESET_SLUG,
    attachImageToNewChat,
  };
})();
