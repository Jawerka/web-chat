/**
 * Лайтбокс галереи загрузок (layout meta-sd-to-markdown).
 */
/* global escapeHtml */

class UploadsRefLightbox {
  constructor() {
    this.items = [];
    this.index = -1;
    this.els = {
      root: document.getElementById('uploads-ref-lightbox'),
      close: document.getElementById('uploads-ref-close'),
      prev: document.getElementById('uploads-ref-prev'),
      next: document.getElementById('uploads-ref-next'),
      image: document.getElementById('uploads-ref-image'),
      stage: document.getElementById('uploads-ref-stage'),
      counter: document.getElementById('uploads-ref-counter'),
      prompt: document.getElementById('uploads-ref-prompt'),
      negative: document.getElementById('uploads-ref-negative'),
      params: document.getElementById('uploads-ref-params'),
      strip: document.getElementById('uploads-ref-strip'),
      copyAll: document.getElementById('uploads-ref-copy-all'),
      extract: document.getElementById('uploads-ref-extract'),
      attach: document.getElementById('uploads-ref-attach'),
      openSd: document.getElementById('uploads-ref-open-sd'),
      zoom: document.getElementById('uploads-ref-zoom'),
      zoomImg: document.getElementById('uploads-ref-zoom-img'),
    };
    this._onKey = this._onKey.bind(this);
    this._onWheel = this._onWheel.bind(this);
    this.bind();
  }

  bind() {
    this.els.close?.addEventListener('click', () => this.close());
    this.els.prev?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.step(-1);
    });
    this.els.next?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.step(1);
    });
    this.els.root?.addEventListener('click', (e) => {
      if (e.target === this.els.root) this.close();
    });
    this.els.stage?.addEventListener('click', (e) => {
      if (e.target === this.els.stage) this.close();
    });
    this.els.image?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.openZoom();
    });
    this.els.zoom?.addEventListener('click', () => this.closeZoom());
    this.els.copyAll?.addEventListener('click', () => this.copyAll());
    this.els.extract?.addEventListener('click', () => {
      const item = this.current();
      if (item && typeof this.onExtract === 'function') this.onExtract(item);
    });
    this.els.attach?.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = this.current();
      if (item && typeof this.onAttach === 'function') {
        this.onAttach(item, this.els.attach);
      }
    });
    this.els.openSd?.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = this.current();
      if (item && typeof this.onOpenSd === 'function') {
        this.onOpenSd(item, this.els.openSd);
      }
    });
    this.els.root?.querySelectorAll('.uploads-ref-copy').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const field = btn.getAttribute('data-copy');
        this.copyField(field);
      });
    });
    document.addEventListener('wheel', this._onWheel, { passive: false });
  }

  setItems(items) {
    this.items = items || [];
  }

  open(index) {
    if (!this.items.length || !this.els.root) return;
    this.index = Math.max(0, Math.min(index, this.items.length - 1));
    this.els.root.classList.remove('hidden');
    document.body.classList.add('uploads-ref-lightbox-open');
    document.addEventListener('keydown', this._onKey);
    this.render();
    this.updateHash();
  }

  close() {
    this.closeZoom();
    this.els.root?.classList.add('hidden');
    document.body.classList.remove('uploads-ref-lightbox-open');
    document.removeEventListener('keydown', this._onKey);
    if (history.replaceState) {
      history.replaceState(null, '', window.location.pathname);
    }
  }

  current() {
    return this.items[this.index] || null;
  }

  step(delta) {
    if (!this.items.length) return;
    this.index = (this.index + delta + this.items.length) % this.items.length;
    this.render();
    this.updateHash();
  }

  render() {
    const item = this.current();
    if (!item) return;
    const url = item.url || `/media/asset/${item.id}`;
    const thumb = item.thumb_url || url;
    if (this.els.image) {
      this.els.image.src = url;
      this.els.image.alt = item.filename || '';
    }
    if (this.els.counter) {
      const n = this.items.length;
      if (n > 1) {
        this.els.counter.textContent = `${this.index + 1} / ${n}`;
        this.els.counter.classList.remove('hidden');
      } else {
        this.els.counter.classList.add('hidden');
      }
    }
    if (this.els.prev) this.els.prev.disabled = this.index <= 0;
    if (this.els.next) this.els.next.disabled = this.index >= this.items.length - 1;
    const p = item.sd_prompt || '';
    const n = item.sd_negative || '';
    const par = item.sd_params || '';
    if (this.els.prompt) this.els.prompt.value = p || '—';
    if (this.els.negative) this.els.negative.value = n || '—';
    if (this.els.params) this.els.params.value = par || '—';
    const hasMeta = item.has_metadata || p || n || par;
    this.els.extract?.classList.toggle('hidden', !!hasMeta);
    if (typeof GallerySdBridge !== 'undefined') {
      GallerySdBridge.syncSdOpenButton(this.els.openSd, item);
    }

    if (this.els.strip) {
      this.els.strip.innerHTML = '';
      this.items.forEach((it, i) => {
        const img = document.createElement('img');
        img.src = it.thumb_url || it.url || '';
        img.alt = it.filename || '';
        if (i === this.index) img.classList.add('is-active');
        img.addEventListener('click', () => {
          this.index = i;
          this.render();
          this.updateHash();
        });
        this.els.strip.appendChild(img);
        if (i === this.index) {
          img.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
      });
    }
  }

  updateHash() {
    const item = this.current();
    if (!item?.id) return;
    const hash = `#upload-${item.id}`;
    if (window.location.hash !== hash) {
      history.replaceState(null, '', hash);
    }
  }

  openZoom() {
    const item = this.current();
    if (!item || !this.els.zoom || !this.els.zoomImg) return;
    this.els.zoomImg.src = item.url || '';
    this.els.zoom.classList.remove('hidden');
    this.els.zoom.setAttribute('aria-hidden', 'false');
  }

  closeZoom() {
    this.els.zoom?.classList.add('hidden');
    this.els.zoom?.setAttribute('aria-hidden', 'true');
  }

  copyField(field) {
    const map = {
      prompt: this.els.prompt,
      negative: this.els.negative,
      params: this.els.params,
    };
    const el = map[field];
    if (el?.value) navigator.clipboard.writeText(el.value);
  }

  copyAll() {
    const item = this.current();
    if (!item) return;
    const p = item.sd_prompt || '';
    const n = item.sd_negative || '';
    const par = item.sd_params || '';
    let text = p;
    if (n) text += `\nNegative prompt: ${n}`;
    if (par) text += `\n${par}`;
    navigator.clipboard.writeText(text.trim());
  }

  _onKey(e) {
    if (this.els.root?.classList.contains('hidden')) return;
    if (e.key === 'Escape') {
      if (!this.els.zoom?.classList.contains('hidden')) {
        this.closeZoom();
      } else {
        this.close();
      }
    } else if (e.key === 'ArrowLeft') {
      this.step(-1);
    } else if (e.key === 'ArrowRight') {
      this.step(+1);
    }
  }

  _onWheel(e) {
    if (this.els.root?.classList.contains('hidden')) return;
    const t = e.target;
    if (t instanceof HTMLTextAreaElement || t instanceof HTMLInputElement) {
      e.preventDefault();
      t.scrollTop += e.deltaY;
      return;
    }
    if (this.els.strip?.contains(t)) {
      e.preventDefault();
      this.els.strip.scrollLeft += e.deltaY;
      return;
    }
    if (this.els.root.contains(t)) {
      e.preventDefault();
      this.step(e.deltaY > 0 ? 1 : -1);
    }
  }
}

window.UploadsRefLightbox = UploadsRefLightbox;
