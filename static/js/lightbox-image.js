/**
 * Загрузка картинки в lightbox: спиннер при смене src (перелистывание, первое открытие).
 */
(function () {
  'use strict';

  function detach(img) {
    if (!img?._lbOnLoad) return;
    img.removeEventListener('load', img._lbOnLoad);
    img.removeEventListener('error', img._lbOnError);
    img._lbOnLoad = null;
    img._lbOnError = null;
  }

  function setLoading(lightbox, loader, loading) {
    if (lightbox) lightbox.classList.toggle('is-loading', Boolean(loading));
    if (loader) {
      loader.setAttribute('aria-hidden', loading ? 'false' : 'true');
    }
  }

  /**
   * @param {{ lightbox: HTMLElement|null, img: HTMLImageElement|null, loader?: HTMLElement|null, url: string }} opts
   */
  function load(opts) {
    const { lightbox, img, loader, url } = opts;
    if (!img || !url) return;

    detach(img);

    const done = () => {
      setLoading(lightbox, loader, false);
      img.classList.remove('is-faded');
    };

    if (img.src === url && img.complete && img.naturalWidth > 0) {
      done();
      return;
    }

    setLoading(lightbox, loader, true);
    img.classList.add('is-faded');

    const onLoad = () => {
      detach(img);
      done();
    };
    const onError = () => {
      detach(img);
      done();
    };

    img._lbOnLoad = onLoad;
    img._lbOnError = onError;
    img.addEventListener('load', onLoad);
    img.addEventListener('error', onError);
    img.src = url;
  }

  function reset(lightbox, img, loader) {
    detach(img);
    setLoading(lightbox, loader, false);
    if (img) {
      img.classList.remove('is-faded');
      img.src = '';
    }
  }

  window.LightboxImage = { load, reset, setLoading, detach };
})();
