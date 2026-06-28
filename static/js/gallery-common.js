/**
 * Общие утилиты галерей: прикрепление изображения в новый чат.
 */
(function () {
  'use strict';

  const PENDING_ATTACHMENTS_KEY = 'webchat_pending_attachments';
  const DEFAULT_CONV_TITLE = 'Новая беседа';
  const IMG2IMG_PRESET_SLUG = 'img2img';

  /**
   * Текст для composer из SD-метаданных (как «Скопировать всё» в uploads-ref-lightbox).
   * @param {{ sd_prompt?: string, sd_negative?: string, sd_params?: string }} item
   * @returns {string}
   */
  function formatUploadMetadataForComposer(item) {
    if (!item) return '';
    const skip = (s) => !s || s === '—';
    const p = (item.sd_prompt || '').trim();
    const n = (item.sd_negative || '').trim();
    const par = (item.sd_params || '').trim();
    let text = '';
    if (!skip(p)) text = p;
    if (!skip(n)) text += `${text ? '\n' : ''}Negative prompt: ${n}`;
    if (!skip(par)) text += `${text ? '\n' : ''}${par}`;
    return text.trim();
  }

  /**
   * Собрать сопровождающий текст: комментарий пользователя + SD metadata.
   * @param {{ sd_prompt?: string, sd_negative?: string, sd_params?: string }} item
   * @param {string} [userComment]
   * @returns {string}
   */
  function buildComposerText(item, userComment = '') {
    const comment = (userComment || '').trim();
    const metadata = formatUploadMetadataForComposer(item);
    if (comment && metadata) return `${comment}\n${metadata}`;
    return comment || metadata;
  }

  /**
   * @param {{ source?: string, id?: string, filename?: string }} item
   * @returns {{ asset_id?: string, disk_filename?: string }}
   */
  function resolveImageSource(item) {
    if (!item) throw new Error('Нет изображения');
    if (item.source === 'disk' && item.filename) {
      return { disk_filename: item.filename };
    }
    if (item.id) {
      return { asset_id: item.id };
    }
    throw new Error('Неизвестный источник изображения');
  }

  /**
   * @param {{ source?: string, id?: string, filename?: string, url?: string, sd_prompt?: string, sd_negative?: string, sd_params?: string }} item
   * @param {{ btn?: HTMLButtonElement, onStatus?: (text: string, isError?: boolean) => void, userComment?: string }} [options]
   */
  async function attachImageToNewChat(item, options = {}) {
    const { btn, onStatus, userComment = '' } = options;
    if (!item?.id && !(item?.source === 'disk' && item?.filename)) return;
    const prevDisabled = btn?.disabled;
    if (btn) btn.disabled = true;
    onStatus?.('Создаём чат…', false);
    try {
      const composerText = buildComposerText(item, userComment);
      const body = {
        text: composerText,
        title: DEFAULT_CONV_TITLE,
        preset_slug: IMG2IMG_PRESET_SLUG,
        image: resolveImageSource(item),
      };

      const res = await fetch('/api/conversations/from-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        const detail = errBody.detail;
        throw new Error(
          typeof detail === 'string' ? detail : res.statusText || 'Ошибка создания чата',
        );
      }
      const data = await res.json();

      if (window.WebChatComposer?.primeComposerDraft) {
        window.WebChatComposer.primeComposerDraft(data.conversation_id, {
          text: data.composer_text ?? composerText,
          attachments: data.attachments || [],
        });
      } else {
        const pending = {
          conversation_id: data.conversation_id,
          attachments: data.attachments || [],
        };
        if (composerText) pending.composer_text = composerText;
        sessionStorage.setItem(PENDING_ATTACHMENTS_KEY, JSON.stringify(pending));
      }

      localStorage.setItem('webchat_conv_id', data.conversation_id);
      window.location.href = data.chat_url || `/?conv=${data.conversation_id}`;
    } catch (err) {
      onStatus?.(err.message || 'Ошибка', true);
      if (btn) btn.disabled = prevDisabled ?? false;
    }
  }

  window.GalleryCommon = {
    PENDING_ATTACHMENTS_KEY,
    DEFAULT_CONV_TITLE,
    IMG2IMG_PRESET_SLUG,
    formatUploadMetadataForComposer,
    buildComposerText,
    resolveImageSource,
    attachImageToNewChat,
  };

  const SCROLL_THRESHOLD_PX = 80;

  function initGalleryScrollNav() {
    const topBtn = document.getElementById('gallery-scroll-top');
    const bottomBtn = document.getElementById('gallery-scroll-bottom');
    if (!topBtn || !bottomBtn) return;

    const scrollRoot = () => document.scrollingElement || document.documentElement;

    function getScrollY() {
      return window.scrollY || scrollRoot().scrollTop || 0;
    }

    function getMaxScroll() {
      const el = scrollRoot();
      return Math.max(0, el.scrollHeight - window.innerHeight);
    }

    function isOverlayOpen() {
      return document.body.classList.contains('gallery-lightbox-open')
        || document.body.classList.contains('uploads-ref-lightbox-open');
    }

    function setBtnVisible(btn, visible) {
      btn.classList.toggle('is-visible', visible);
      btn.setAttribute('aria-hidden', visible ? 'false' : 'true');
      btn.tabIndex = visible ? 0 : -1;
    }

    function updateButtons() {
      if (isOverlayOpen()) {
        setBtnVisible(topBtn, false);
        setBtnVisible(bottomBtn, false);
        return;
      }
      const y = getScrollY();
      const maxY = getMaxScroll();
      setBtnVisible(topBtn, y > SCROLL_THRESHOLD_PX);
      setBtnVisible(bottomBtn, maxY > SCROLL_THRESHOLD_PX && maxY - y > SCROLL_THRESHOLD_PX);
    }

    topBtn.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    bottomBtn.addEventListener('click', () => {
      window.scrollTo({ top: getMaxScroll(), behavior: 'smooth' });
    });

    window.addEventListener('scroll', updateButtons, { passive: true });
    window.addEventListener('resize', updateButtons, { passive: true });

    const observer = new MutationObserver(updateButtons);
    observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });

    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(updateButtons);
      ro.observe(document.body);
      const page = document.querySelector('.gallery-page');
      if (page) ro.observe(page);
    }

    updateButtons();
  }

  document.addEventListener('DOMContentLoaded', initGalleryScrollNav);
})();
