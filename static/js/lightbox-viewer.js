/**
 * Общая навигация lightbox: show/step/close, LightboxImage.load, touch (P5.9).
 * Подключается после lightbox-image.js.
 */
(function () {
  'use strict';

  function isOpen(ctx) {
    return Boolean(ctx.root && !ctx.root.classList.contains('hidden'));
  }

  function updateNav(ctx) {
    const n = ctx.getCount();
    const i = ctx.index;
    if (ctx.prev) ctx.prev.disabled = i <= 0;
    if (ctx.next) ctx.next.disabled = i >= n - 1;
    if (ctx.counter) {
      if (n > 1) {
        ctx.counter.textContent = `${i + 1} / ${n}`;
        ctx.counter.classList.remove('hidden');
      } else {
        ctx.counter.classList.add('hidden');
      }
    }
  }

  function showAt(ctx, index) {
    const n = ctx.getCount();
    if (n <= 0 || !ctx.root) return;
    const i = Math.max(0, Math.min(index, n - 1));
    ctx.index = i;
    const url = ctx.getUrl(i);
    if (!url) return;
    ctx.onBeforeShow?.(i);
    ctx.root.classList.remove('hidden');
    if (typeof LightboxImage !== 'undefined') {
      LightboxImage.load({
        lightbox: ctx.root,
        img: ctx.img,
        loader: ctx.loader,
        url,
      });
    } else if (ctx.img) {
      ctx.img.src = url;
    }
    updateNav(ctx);
    ctx.onShow?.(i);
  }

  function step(ctx, delta) {
    if (!isOpen(ctx)) return;
    const next = ctx.index + delta;
    if (next < 0 || next >= ctx.getCount()) return;
    showAt(ctx, next);
  }

  function close(ctx) {
    if (!ctx.root) return;
    ctx.root.classList.add('hidden');
    if (typeof LightboxImage !== 'undefined') {
      LightboxImage.reset(ctx.root, ctx.img, ctx.loader);
    } else if (ctx.img) {
      ctx.img.src = '';
    }
    ctx.onClose?.();
  }

  function bindTouch(ctx, el) {
    if (!el) return;
    let touchStart = null;
    el.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      touchStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    }, { passive: true });
    el.addEventListener('touchend', (e) => {
      if (!touchStart || e.changedTouches.length !== 1) return;
      const dx = e.changedTouches[0].clientX - touchStart.x;
      const dy = e.changedTouches[0].clientY - touchStart.y;
      touchStart = null;
      if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy)) return;
      step(ctx, dx < 0 ? 1 : -1);
    }, { passive: true });
  }

  /** @returns {boolean} handled */
  function onKeydown(ctx, e) {
    if (!isOpen(ctx)) return false;
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      step(ctx, -1);
      return true;
    }
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      step(ctx, 1);
      return true;
    }
    return false;
  }

  window.LightboxViewer = {
    isOpen,
    showAt,
    step,
    close,
    updateNav,
    bindTouch,
    onKeydown,
  };
})();
