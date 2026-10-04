/* Скрипты сайта: навигация, каталог, карусель, слайдер и лайтбокс фото.
   Подключается с defer из base.html — браузер кэширует файл один раз. */
  // Нижняя навигация (мобилка): кнопка «Наверх».
  (function () {
    var btn = document.getElementById('scroll-top');
    if (!btn) return;
    btn.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  })();

  // Тень у прилипшего хедера, когда страница прокручена.
  (function () {
    var header = document.getElementById('site-header');
    if (!header) return;
    var sync = function () {
      header.classList.toggle('is-scrolled', window.scrollY > 8);
    };
    sync();
    window.addEventListener('scroll', sync, { passive: true });
  })();

  // Панель "Фильтры и сортировка" в каталоге — сворачивается на мобильных.
  (function () {
    var toggle = document.getElementById('filter-toggle');
    var panel = document.getElementById('catalog-filter-panel');
    if (!toggle || !panel) return;
    toggle.addEventListener('click', function () {
      var isOpen = panel.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });
  })();

  // Выпадающий список категорий в каталоге (замена ряду чипов на мобильных).
  (function () {
    var dropdown = document.getElementById('catalog-category-dropdown');
    var toggle = document.getElementById('catalog-category-toggle');
    if (!dropdown || !toggle) return;
    toggle.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = dropdown.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
      if (!dropdown.contains(e.target)) {
        dropdown.classList.remove('is-open');
        toggle.setAttribute('aria-expanded', 'false');
      }
    });
  })();

  // Карусель: горизонтальная прокрутка колесом/свайпом + стрелки.
  (function () {
    document.querySelectorAll('.carousel').forEach(function (carousel) {
      var track = carousel.querySelector('.carousel__track');
      var prev = carousel.querySelector('[data-carousel-prev]');
      var next = carousel.querySelector('[data-carousel-next]');
      if (!track) return;

      function step() {
        var card = track.firstElementChild;
        var cardW = card ? card.getBoundingClientRect().width + 28 : 288; // + gap
        var perView = Math.max(1, Math.floor(track.clientWidth / cardW));
        return cardW * perView;
      }
      function updateArrows() {
        var scrollable = track.scrollWidth - track.clientWidth > 4;
        if (prev) prev.hidden = !scrollable;
        if (next) next.hidden = !scrollable;
      }
      if (prev) prev.addEventListener('click', function () {
        track.scrollBy({ left: -step(), behavior: 'smooth' });
      });
      if (next) next.addEventListener('click', function () {
        track.scrollBy({ left: step(), behavior: 'smooth' });
      });
      updateArrows();
      window.addEventListener('resize', updateArrows);
    });
  })();

  // Слайдер фото на странице товара: миниатюры + стрелки + свайп.
  (function () {
    var slider = document.getElementById('product-slider');
    if (!slider) return;
    var main = document.getElementById('product-slider-main');
    var thumbs = [].slice.call(slider.querySelectorAll('.product-slider__thumb'));
    if (!main || thumbs.length < 2) return;
    var idx = 0;
    function show(n) {
      idx = (n + thumbs.length) % thumbs.length;
      main.src = thumbs[idx].dataset.sliderSrc;
      thumbs.forEach(function (t, k) { t.classList.toggle('is-active', k === idx); });
    }
    thumbs.forEach(function (t, k) { t.addEventListener('click', function () { show(k); }); });
    var prev = slider.querySelector('[data-slider-prev]');
    var next = slider.querySelector('[data-slider-next]');
    if (prev) prev.addEventListener('click', function () { show(idx - 1); });
    if (next) next.addEventListener('click', function () { show(idx + 1); });
    var x0 = null;
    main.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; }, { passive: true });
    main.addEventListener('touchend', function (e) {
      if (x0 === null) return;
      var dx = e.changedTouches[0].clientX - x0;
      if (Math.abs(dx) > 45) show(dx < 0 ? idx + 1 : idx - 1);
      x0 = null;
    });
  })();

  // Лайтбокс-галерея: открытие по клику на фото, свайп между фото,
  // приближение щипком (pinch) на телефоне и колёсиком/двойным кликом на десктопе —
  // похоже на системную галерею фото.
  (function () {
    var lightbox = document.getElementById('lightbox');
    var viewport = document.getElementById('lightbox-viewport');
    var imgEl = document.getElementById('lightbox-img');
    var closeBtn = document.getElementById('lightbox-close');
    var prevBtn = document.getElementById('lightbox-prev');
    var nextBtn = document.getElementById('lightbox-next');
    if (!lightbox || !imgEl) return;

    var groups = {};
    document.querySelectorAll('.js-lightbox').forEach(function (el) {
      var group = el.dataset.lightboxGroup || 'default';
      if (!groups[group]) groups[group] = [];
      groups[group].push({ src: el.dataset.lightboxSrc, alt: el.dataset.lightboxAlt || '' });
      el.addEventListener('click', function (e) {
        e.preventDefault();
        openLightbox(group, groups[group].length - 1);
      });
    });

    var currentGroup = null, currentIndex = 0;
    var scale = 1, originX = 0, originY = 0;
    var isPanning = false, panStartX = 0, panStartY = 0, panOriginX = 0, panOriginY = 0;
    var pinchStartDist = 0, pinchStartScale = 1;
    var swipeStartX = 0, swipeStartY = 0, swiping = false;
    var lastTapTime = 0;

    function openLightbox(group, index) {
      currentGroup = group;
      currentIndex = index;
      renderImage();
      lightbox.classList.add('is-open');
      lightbox.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
    }
    function closeLightbox() {
      lightbox.classList.remove('is-open');
      lightbox.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
      resetZoom();
    }
    function renderImage() {
      var item = groups[currentGroup][currentIndex];
      if (!item) return;
      imgEl.src = item.src;
      imgEl.alt = item.alt;
      resetZoom();
    }
    function showNext() {
      if (!currentGroup) return;
      currentIndex = (currentIndex + 1) % groups[currentGroup].length;
      renderImage();
    }
    function showPrev() {
      if (!currentGroup) return;
      currentIndex = (currentIndex - 1 + groups[currentGroup].length) % groups[currentGroup].length;
      renderImage();
    }
    function resetZoom() {
      scale = 1; originX = 0; originY = 0;
      applyTransform();
    }
    function applyTransform() {
      imgEl.style.transform = 'translate(' + originX + 'px, ' + originY + 'px) scale(' + scale + ')';
    }
    function clampScale(s) { return Math.min(Math.max(s, 1), 4); }

    closeBtn.addEventListener('click', closeLightbox);
    nextBtn.addEventListener('click', showNext);
    prevBtn.addEventListener('click', showPrev);
    lightbox.addEventListener('click', function (e) {
      if (e.target === lightbox) closeLightbox();
    });
    document.addEventListener('keydown', function (e) {
      if (!lightbox.classList.contains('is-open')) return;
      if (e.key === 'Escape') closeLightbox();
      if (e.key === 'ArrowRight') showNext();
      if (e.key === 'ArrowLeft') showPrev();
    });

    // Колёсико мыши — зум на десктопе.
    viewport.addEventListener('wheel', function (e) {
      e.preventDefault();
      scale = clampScale(scale - e.deltaY * 0.0015);
      if (scale === 1) { originX = 0; originY = 0; }
      applyTransform();
    }, { passive: false });

    // Двойной клик / двойной тап — переключение зума.
    function toggleZoomAt(clientX, clientY) {
      if (scale > 1) {
        resetZoom();
      } else {
        scale = 2.5;
        var rect = viewport.getBoundingClientRect();
        originX = (rect.width / 2 - (clientX - rect.left)) * (scale - 1) / scale;
        originY = (rect.height / 2 - (clientY - rect.top)) * (scale - 1) / scale;
        applyTransform();
      }
    }
    viewport.addEventListener('dblclick', function (e) { toggleZoomAt(e.clientX, e.clientY); });

    // Перетаскивание мышью, когда приближено.
    viewport.addEventListener('mousedown', function (e) {
      if (scale <= 1) return;
      isPanning = true;
      panStartX = e.clientX; panStartY = e.clientY;
      panOriginX = originX; panOriginY = originY;
    });
    window.addEventListener('mousemove', function (e) {
      if (!isPanning) return;
      originX = panOriginX + (e.clientX - panStartX);
      originY = panOriginY + (e.clientY - panStartY);
      applyTransform();
    });
    window.addEventListener('mouseup', function () { isPanning = false; });

    // Touch: pinch-zoom, перетаскивание приближенного фото, свайп между фото.
    function touchDist(touches) {
      var dx = touches[0].clientX - touches[1].clientX;
      var dy = touches[0].clientY - touches[1].clientY;
      return Math.sqrt(dx * dx + dy * dy);
    }
    viewport.addEventListener('touchstart', function (e) {
      if (e.touches.length === 2) {
        pinchStartDist = touchDist(e.touches);
        pinchStartScale = scale;
        swiping = false;
      } else if (e.touches.length === 1) {
        var now = Date.now();
        if (now - lastTapTime < 300) {
          toggleZoomAt(e.touches[0].clientX, e.touches[0].clientY);
          lastTapTime = 0;
          return;
        }
        lastTapTime = now;
        if (scale > 1) {
          isPanning = true;
          panStartX = e.touches[0].clientX; panStartY = e.touches[0].clientY;
          panOriginX = originX; panOriginY = originY;
        } else {
          swiping = true;
          swipeStartX = e.touches[0].clientX;
          swipeStartY = e.touches[0].clientY;
        }
      }
    }, { passive: true });
    viewport.addEventListener('touchmove', function (e) {
      if (e.touches.length === 2) {
        e.preventDefault();
        var dist = touchDist(e.touches);
        scale = clampScale(pinchStartScale * (dist / pinchStartDist));
        applyTransform();
      } else if (e.touches.length === 1 && isPanning) {
        e.preventDefault();
        originX = panOriginX + (e.touches[0].clientX - panStartX);
        originY = panOriginY + (e.touches[0].clientY - panStartY);
        applyTransform();
      }
    }, { passive: false });
    viewport.addEventListener('touchend', function (e) {
      isPanning = false;
      if (scale < 1.05) resetZoom();
      if (swiping && e.changedTouches.length === 1) {
        var dx = e.changedTouches[0].clientX - swipeStartX;
        var dy = e.changedTouches[0].clientY - swipeStartY;
        if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) {
          if (dx < 0) showNext(); else showPrev();
        }
      }
      swiping = false;
    });
  })();

// Цели аналитики: клики по WhatsApp / телефону / Instagram (если счётчики подключены).
(function () {
  var goals = [
    ['a[href^="https://wa.me/"]', 'click_whatsapp'],
    ['a[href^="tel:"]', 'click_phone'],
    ['a[href*="instagram.com"]', 'click_instagram']
  ];
  document.addEventListener('click', function (e) {
    if (!e.target.closest) return;
    goals.forEach(function (g) {
      if (!e.target.closest(g[0])) return;
      if (window.METRIKA_ID && typeof window.ym === 'function') window.ym(window.METRIKA_ID, 'reachGoal', g[1]);
      if (typeof window.gtag === 'function') window.gtag('event', g[1]);
      // Google Tag Manager: событие в dataLayer (триггер «Пользовательское событие» с этим именем).
      if (Array.isArray(window.dataLayer)) window.dataLayer.push({ event: g[1] });
    });
  });
})();
