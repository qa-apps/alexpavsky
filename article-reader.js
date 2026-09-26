(function () {
    'use strict';
    var escape = function (value) {
        return String(value || '').replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    };
    function safeURL(value, base) {
        if (!value) return '';
        try {
            var url = new URL(value, base);
            if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
            if (new URL(base).protocol === 'https:' && url.protocol === 'http:') url.protocol = 'https:';
            return url.href;
        } catch (_) { return ''; }
    }
    function render(html, info) {
        if (!window.Readability || !window.DOMPurify) throw new Error('Reader libraries unavailable');
        // The server response is sanitized again before extraction and before insertion.
        var clean = DOMPurify.sanitize(html, { FORBID_TAGS: ['style', 'form', 'iframe', 'noscript'], FORBID_ATTR: ['style'] });
        var doc = new DOMParser().parseFromString(clean, 'text/html');
        var base = doc.createElement('base');
        base.href = info.url;
        doc.head.appendChild(base);
        var host = new URL(info.url).hostname;
        var headings = doc.querySelectorAll('h1');
        var title = headings.length ? headings[headings.length - 1].textContent.trim() : info.title;
        var authorEl = doc.querySelector('.author-name, [rel="author"], .card-author-item');
        var author = authorEl ? authorEl.textContent.trim().split('\n')[0].trim() : '';
        var time = doc.querySelector('time[datetime]');
        var dateValue = info.date || (time ? time.getAttribute('datetime') : '');
        var date = dateValue ? new Date(dateValue) : null;
        var dateLabel = date && !isNaN(date.getTime()) ? date.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }) : '';
        var image = safeURL(info.image, info.url);
        var source = info.source || host;
        var selector = /(^|\.)testomat\.io$/.test(host) ? '.content-col' : /(^|\.)mabl\.com$/.test(host) ? '.long-form-content' : null;
        var contentRoot = selector && doc.querySelector(selector);
        if (contentRoot) doc.body.replaceChildren(contentRoot.cloneNode(true));
        // Readability promotes retained h1 elements to h2; remove the captured title first.
        doc.querySelectorAll('h1').forEach(function (el) { el.remove(); });
        doc.querySelectorAll('nav, aside, footer, .article-modal-source-banner, .author-section, .related-posts, .social-share, .table-of-contents').forEach(function (el) { el.remove(); });
        doc.querySelectorAll('img').forEach(function (img) {
            var src = safeURL(img.getAttribute('data-lazy-src') || img.getAttribute('data-src') || img.getAttribute('src'), info.url);
            if (!src || (Number(img.getAttribute('width')) === 1 && Number(img.getAttribute('height')) === 1)) { img.remove(); return; }
            img.setAttribute('src', src);
            img.removeAttribute('srcset');
            img.removeAttribute('sizes');
        });
        var parsed = new Readability(doc, { charThreshold: 200 }).parse();
        if (!parsed || parsed.textContent.trim().length < 200) throw new Error('Reader extraction unavailable');
        var safeContent = DOMPurify.sanitize(parsed.content, {
            ALLOWED_TAGS: ['div','section','article','p','h1','h2','h3','h4','h5','h6','a','img','figure','figcaption','ul','ol','li','blockquote','pre','code','strong','b','em','i','br','hr','table','thead','tbody','tfoot','tr','th','td','caption','sup','sub','del','span'],
            ALLOWED_ATTR: ['href','src','alt','title','colspan','rowspan','start'],
            ALLOW_DATA_ATTR: false
        });
        var body = new DOMParser().parseFromString(safeContent, 'text/html').body;
        body.querySelectorAll('h1').forEach(function (el) { el.remove(); });
        body.querySelectorAll('a').forEach(function (a) {
            a.setAttribute('href', safeURL(a.getAttribute('href'), info.url) || info.url);
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer');
        });
        body.querySelectorAll('img').forEach(function (img) {
            if (image && img.getAttribute('src') === image) { img.remove(); return; }
            img.setAttribute('loading', 'lazy');
            img.setAttribute('decoding', 'async');
            img.setAttribute('referrerpolicy', 'no-referrer');
        });
        body.querySelectorAll('h2,h3,h4,p').forEach(function (el) {
            if (!el.textContent.trim() && !el.querySelector('img')) el.remove();
        });
        var toc = Array.from(body.querySelectorAll('h2')).map(function (el, i) {
            el.id = 'reader-section-' + i;
            return '<a href="#' + el.id + '">' + escape(el.textContent.trim()) + '</a>';
        });
        body.querySelectorAll('table').forEach(function (table) {
            var wrapper = body.ownerDocument.createElement('div');
            wrapper.className = 'reader-table';
            wrapper.tabIndex = 0;
            wrapper.setAttribute('role', 'region');
            wrapper.setAttribute('aria-label', 'Article table');
            table.replaceWith(wrapper);
            wrapper.appendChild(table);
        });
        author = author || parsed.byline || '';
        var minutes = Math.max(1, Math.ceil(body.textContent.trim().split(/\s+/).length / 220));
        return '<div class="reader-layout' + (toc.length ? '' : ' reader-no-contents') + '">' +
            (toc.length ? '<aside class="reader-contents" aria-label="Contents"><p>In this article</p><nav>' + toc.join('') + '</nav></aside>' : '') +
            '<article class="reader-article"><header class="reader-heading">' +
            '<a class="reader-publication" href="' + escape(info.url) + '" target="_blank" rel="noopener noreferrer">' + escape(source) + ' <i class="fas fa-arrow-up-right-from-square" aria-hidden="true"></i></a>' +
            '<h1>' + escape(title || parsed.title || 'Article') + '</h1><div class="reader-byline">' +
            (author ? '<strong>' + escape(author) + '</strong>' : '') +
            (dateLabel ? '<span>' + escape(dateLabel) + '</span>' : '') + '<span>' + minutes + ' min read</span></div></header>' +
            (image ? '<figure class="reader-cover"><img src="' + escape(image) + '" alt="' + escape(title) + '" referrerpolicy="no-referrer"></figure>' : '') +
            '<div class="reader-prose">' + body.innerHTML + '</div><footer class="reader-end"><span>Published by ' + escape(source) + '</span>' +
            '<a href="' + escape(info.url) + '" target="_blank" rel="noopener noreferrer">View original article <i class="fas fa-arrow-up-right-from-square" aria-hidden="true"></i></a></footer></article></div>';
    }
    window.ArticleReader = { render: render };

    var overlay = document.getElementById('article-modal-overlay');
    if (!overlay) return;
    var dialog = overlay.querySelector('.article-modal-card');
    var reader = document.getElementById('article-modal-reader');
    var progress = overlay.querySelector('.reader-progress span');
    var theme = document.getElementById('reader-theme');
    var smaller = document.getElementById('reader-smaller');
    var larger = document.getElementById('reader-larger');
    var fontSize = 19;
    var returnFocus;
    var wasOpen = false;
    theme.onclick = function () {
        var dark = dialog.classList.toggle('reader-dark');
        theme.innerHTML = '<i class="fas fa-' + (dark ? 'sun' : 'moon') + '" aria-hidden="true"></i>';
        theme.setAttribute('aria-pressed', String(dark));
    };
    function updateProgress() {
        var total = reader.scrollHeight - reader.clientHeight;
        progress.style.width = (total > 0 ? Math.min(100, reader.scrollTop / total * 100) : 0) + '%';
    }
    function resize(delta) {
        fontSize = Math.min(23, Math.max(17, fontSize + delta));
        reader.style.setProperty('--reader-font-size', fontSize + 'px');
        smaller.disabled = fontSize === 17;
        larger.disabled = fontSize === 23;
        updateProgress();
    }
    smaller.onclick = function () { resize(-1); };
    larger.onclick = function () { resize(1); };
    reader.addEventListener('scroll', updateProgress, { passive: true });
    reader.addEventListener('load', updateProgress, true);
    window.addEventListener('resize', updateProgress);
    reader.addEventListener('click', function (event) {
        var anchor = event.target.closest('.reader-contents a');
        if (!anchor) return;
        event.preventDefault();
        var heading = reader.querySelector(anchor.getAttribute('href'));
        if (heading) heading.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    });
    new MutationObserver(function () { reader.scrollTop = 0; updateProgress(); }).observe(reader, { childList: true });
    new MutationObserver(function () {
        var open = overlay.classList.contains('active');
        if (open && !wasOpen) {
            returnFocus = document.activeElement;
            document.getElementById('article-modal-close').focus();
        } else if (!open && wasOpen && returnFocus && returnFocus.isConnected) returnFocus.focus();
        wasOpen = open;
    }).observe(overlay, { attributes: true, attributeFilter: ['class'] });
    dialog.addEventListener('keydown', function (event) {
        if (event.key !== 'Tab') return;
        var items = Array.from(dialog.querySelectorAll('a[href],button:not(:disabled),[tabindex="0"]')).filter(function (el) { return el.getClientRects().length; });
        var first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
        if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
    });
})();
