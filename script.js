(function () {
    'use strict';

    const RSS_SOURCES = [
        { name: 'The Gradient', url: 'https://thegradient.pub/rss/', category: 'ai' },
        { name: 'Hugging Face', url: 'https://huggingface.co/blog/feed.xml', category: 'ai' },
        { name: 'AI Weirdness', url: 'https://www.aiweirdness.com/rss/', category: 'ai' },
        { name: 'Martin Fowler', url: 'https://martinfowler.com/feed.atom', category: 'dev' },
        { name: 'Ministry of Testing', url: 'https://www.ministryoftesting.com/feeds/blogs', category: 'qa' },
        { name: 'Google AI Blog', url: 'https://blog.google/technology/ai/rss/', category: 'ai' },
        { name: 'OpenAI Blog', url: 'https://openai.com/blog/rss.xml', category: 'ai' },
    ];

    const CORS_PROXY = 'https://api.rss2json.com/v1/api.json?rss_url=';
    let allArticles = [];
    let displayedCount = 0;
    const ARTICLES_PER_PAGE = 9;
    let currentFilter = 'all';

    // ─── Nav ───
    const navMenuBtn = document.getElementById('nav-menu-btn');
    const mobileMenu = document.getElementById('mobile-menu');
    if (navMenuBtn && mobileMenu) {
        navMenuBtn.addEventListener('click', function () {
            mobileMenu.classList.toggle('active');
            const icon = navMenuBtn.querySelector('i');
            if (icon) icon.className = mobileMenu.classList.contains('active') ? 'fas fa-times' : 'fas fa-bars';
        });
        mobileMenu.querySelectorAll('.mobile-link').forEach(function (link) {
            link.addEventListener('click', function () {
                mobileMenu.classList.remove('active');
                var icon = navMenuBtn.querySelector('i');
                if (icon) icon.className = 'fas fa-bars';
            });
        });
    }

    // ─── Animated Terminal ───
    (function initTerminal() {
        var tb = document.getElementById('terminal-body');
        var tt = document.querySelector('.terminal-title');
        if (!tb) return;
        var scenes = [
            { title: 'ai-radar.sh', lines: [
                {p: true, html: '<span class="t-cmd">scanning</span> <span class="t-arg">--sources 7 --topics ai,qa,llm</span>'},
                {html: '<span class="t-success">✓</span> Connected to RSS feeds...'},
                {html: '<span class="t-info">↳</span> The Gradient — <span class="t-highlight">3 new</span>'},
                {html: '<span class="t-info">↳</span> Hugging Face — <span class="t-highlight">5 new</span>'},
                {html: '<span class="t-info">↳</span> AI Weirdness — <span class="t-highlight">1 new</span>'},
                {html: '<span class="t-info">↳</span> Martin Fowler — <span class="t-highlight">2 new</span>'},
                {html: '<span class="t-success">✓</span> Feed updated. <span class="t-dim">Next refresh in 30m</span>'},
            ]},
            { title: 'chat_server.py', lines: [
                {p: true, html: '<span class="t-cmd">python3</span> <span class="t-arg">chat_server.py</span>'},
                {html: '<span class="t-success">✓</span> Loaded 25 AI models'},
                {html: '<span class="t-info">↳</span> gemini: <span class="t-highlight">OK</span>'},
                {html: '<span class="t-info">↳</span> openrouter: <span class="t-highlight">OK</span>'},
                {html: '<span class="t-info">↳</span> groq: <span class="t-highlight">OK</span>'},
                {html: '<span class="t-success">✓</span> Smart router active on <span class="t-highlight">:8000</span>'},
                {html: '<span class="t-dim">Waiting for requests...</span>'},
            ]},
            { title: 'test_runner.py', lines: [
                {p: true, html: '<span class="t-cmd">pytest</span> <span class="t-arg">tests/ -v --parallel</span>'},
                {html: '<span class="t-success">PASS</span> test_login_flow <span class="t-dim">0.8s</span>'},
                {html: '<span class="t-success">PASS</span> test_api_response <span class="t-dim">0.3s</span>'},
                {html: '<span class="t-success">PASS</span> test_model_routing <span class="t-dim">1.2s</span>'},
                {html: '<span class="t-success">PASS</span> test_fallback_chain <span class="t-dim">0.9s</span>'},
                {html: '<span class="t-success">PASS</span> test_prompt_injection <span class="t-dim">0.5s</span>'},
                {html: '<span class="t-success">✓</span> <span class="t-highlight">5 passed</span> in <span class="t-dim">3.7s</span>'},
            ]},
            { title: 'deploy.sh', lines: [
                {p: true, html: '<span class="t-cmd">rsync</span> <span class="t-arg">--deploy alexpavsky.com</span>'},
                {html: '<span class="t-info">↳</span> Building assets...'},
                {html: '<span class="t-info">↳</span> Uploading <span class="t-highlight">4 files</span>'},
                {html: '<span class="t-info">↳</span> Restarting chat backend...'},
                {html: '<span class="t-success">✓</span> SSL certificate valid'},
                {html: '<span class="t-success">✓</span> nginx proxy active'},
                {html: '<span class="t-success">✓</span> Live at <span class="t-highlight">alexpavsky.com</span>'},
            ]},
            { title: 'ai_orchestrator.py', lines: [
                {p: true, html: '<span class="t-cmd">route</span> <span class="t-arg">--message "Explain prompt injection"</span>'},
                {html: '<span class="t-info">↳</span> Analyzing complexity... <span class="t-highlight">tier:M</span>'},
                {html: '<span class="t-info">↳</span> Selected: <span class="t-highlight">Gemini 2.5 Flash</span>'},
                {html: '<span class="t-info">↳</span> Tokens: <span class="t-dim">in:42 out:380</span>'},
                {html: '<span class="t-success">✓</span> Response in <span class="t-highlight">1.2s</span>'},
                {html: '<span class="t-dim">Fallback chain: OpenRouter → Groq</span>'},
                {html: '<span class="t-success">✓</span> <span class="t-highlight">25 models</span> ready'},
            ]},
            { title: 'playwright.config.ts', lines: [
                {p: true, html: '<span class="t-cmd">npx</span> <span class="t-arg">playwright test --headed</span>'},
                {html: '<span class="t-info">↳</span> Running 12 tests on 3 browsers...'},
                {html: '<span class="t-success">✓</span> chromium: <span class="t-highlight">12/12</span>'},
                {html: '<span class="t-success">✓</span> firefox: <span class="t-highlight">12/12</span>'},
                {html: '<span class="t-success">✓</span> webkit: <span class="t-highlight">12/12</span>'},
                {html: '<span class="t-success">✓</span> Screenshots captured'},
                {html: '<span class="t-success">✓</span> <span class="t-highlight">36 passed</span> <span class="t-dim">(18s)</span>'},
            ]},
        ];
        var idx = 0;
        function playScene() {
            var scene = scenes[idx];
            idx = (idx + 1) % scenes.length;
            tb.innerHTML = '';
            if (tt) tt.textContent = scene.title;
            var i = 0;
            function addLine() {
                if (i >= scene.lines.length) {
                    var cursor = document.createElement('div');
                    cursor.className = 'terminal-line';
                    cursor.innerHTML = '<span class="t-prompt">$</span> <span class="t-cursor">_</span>';
                    tb.appendChild(cursor);
                    setTimeout(playScene, 3500);
                    return;
                }
                var line = scene.lines[i];
                var el = document.createElement('div');
                el.className = 'terminal-line' + (line.p ? '' : ' t-output');
                el.style.opacity = '0'; el.style.transform = 'translateY(6px)';
                el.innerHTML = (line.p ? '<span class="t-prompt">$</span> ' : '') + line.html;
                tb.appendChild(el);
                requestAnimationFrame(function () {
                    el.style.transition = 'opacity 0.3s, transform 0.3s';
                    el.style.opacity = '1'; el.style.transform = 'translateY(0)';
                });
                i++;
                setTimeout(addLine, line.p ? 600 : 400);
            }
            addLine();
        }
        playScene();
    })();

    // ─── Scroll animations ───
    var animObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                animObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });

    document.querySelectorAll('[data-animate], .explore-card, .tool-card, .lab-card').forEach(function (el) {
        animObserver.observe(el);
    });

    // ─── RSS Feed ───
    async function fetchFeed(source) {
        try {
            var resp = await fetch(CORS_PROXY + encodeURIComponent(source.url));
            if (!resp.ok) return [];
            var data = await resp.json();
            if (data.status !== 'ok' || !data.items) return [];
            return data.items.slice(0, 8).map(function (item) {
                return {
                    title: item.title || 'Untitled',
                    link: item.link || '#',
                    description: stripHtml(item.description || '').slice(0, 200),
                    date: item.pubDate || '',
                    source: source.name,
                    category: source.category,
                };
            });
        } catch (e) {
            return [];
        }
    }

    function stripHtml(html) {
        var tmp = document.createElement('div');
        tmp.innerHTML = html;
        return tmp.textContent || tmp.innerText || '';
    }

    function timeAgo(dateStr) {
        if (!dateStr) return '';
        var date = new Date(dateStr);
        var now = new Date();
        var diff = Math.floor((now - date) / 1000);
        if (diff < 60) return 'just now';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }

    function getCategoryLabel(cat) {
        if (cat === 'ai') return 'AI & LLM';
        if (cat === 'qa') return 'QA & Testing';
        if (cat === 'dev') return 'Dev & Engineering';
        return cat;
    }

    function renderArticle(article) {
        var card = document.createElement('a');
        card.className = 'feed-card';
        card.href = article.link;
        card.target = '_blank';
        card.rel = 'noopener';
        card.setAttribute('data-category', article.category);
        card.innerHTML =
            '<div class="feed-card-source">' +
                '<span class="feed-source-name">' + escapeHtml(article.source) + '</span>' +
                '<span class="feed-card-date">' + timeAgo(article.date) + '</span>' +
            '</div>' +
            '<h3>' + escapeHtml(article.title) + '</h3>' +
            '<p>' + escapeHtml(article.description) + '</p>' +
            '<span class="feed-card-tag">' + getCategoryLabel(article.category) + '</span>';
        return card;
    }

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    function displayArticles() {
        var grid = document.getElementById('feed-grid');
        var loadMoreBtn = document.getElementById('load-more-btn');
        if (!grid) return;

        var filtered = currentFilter === 'all'
            ? allArticles
            : allArticles.filter(function (a) { return a.category === currentFilter; });

        var toShow = filtered.slice(0, displayedCount + ARTICLES_PER_PAGE);
        grid.innerHTML = '';

        if (toShow.length === 0) {
            grid.innerHTML = '<div class="feed-loading"><p>No articles found. Check back later.</p></div>';
            if (loadMoreBtn) loadMoreBtn.style.display = 'none';
            return;
        }

        toShow.forEach(function (article) {
            grid.appendChild(renderArticle(article));
        });

        displayedCount = toShow.length;
        if (loadMoreBtn) {
            loadMoreBtn.style.display = displayedCount < filtered.length ? 'inline-flex' : 'none';
        }
    }

    async function loadAllFeeds() {
        var promises = RSS_SOURCES.map(fetchFeed);
        var results = await Promise.allSettled(promises);
        var articles = [];
        results.forEach(function (r) {
            if (r.status === 'fulfilled') articles = articles.concat(r.value);
        });

        articles.sort(function (a, b) {
            return new Date(b.date) - new Date(a.date);
        });

        allArticles = articles;
        displayedCount = 0;
        displayArticles();
        updateTicker(articles);
        updateStats(articles);
    }

    (function initTickerArrows() {
        var content = document.getElementById('ticker-content');
        var leftBtn = document.getElementById('ticker-left');
        var rightBtn = document.getElementById('ticker-right');
        if (!content || !leftBtn || !rightBtn) return;
        function getCurrentX() {
            var style = getComputedStyle(content);
            var t = style.transform;
            if (!t || t === 'none') return 0;
            var mat = t.match(/matrix\((.+)\)/);
            if (mat) return parseFloat(mat[1].split(',')[4]) || 0;
            return 0;
        }
        function jump(dir) {
            var current = getCurrentX();
            content.style.animation = 'none';
            content.style.transform = 'translateX(' + current + 'px)';
            void content.offsetWidth;
            var next = current + dir * 400;
            var half = content.scrollWidth / 2;
            if (Math.abs(next) > half) next = 0;
            if (next > 0) next = -half + 100;
            content.style.transition = 'transform 0.4s ease';
            content.style.transform = 'translateX(' + next + 'px)';
            setTimeout(function () {
                content.style.transition = '';
                content.style.animation = '';
                content.style.transform = '';
            }, 500);
        }
        leftBtn.addEventListener('click', function () { jump(1); });
        rightBtn.addEventListener('click', function () { jump(-1); });
    })();

    function updateTicker(articles) {
        var ticker = document.getElementById('ticker-content');
        if (!ticker || articles.length === 0) return;
        var headlines = articles.slice(0, 20);
        var html = '';
        headlines.forEach(function (a) {
            html += '<a class="ticker-item" href="' + escapeHtml(a.link) + '" target="_blank" rel="noopener">' +
                    '<i class="fas fa-circle-dot"></i> ' +
                    '<strong>' + escapeHtml(a.source) + ':</strong> ' +
                    escapeHtml(a.title) + '</a>';
        });
        ticker.innerHTML = html + html;
    }

    function updateStats(articles) {
        var el = document.getElementById('stat-articles');
        if (el) {
            var today = new Date().toDateString();
            var todayCount = articles.filter(function (a) {
                return a.date && new Date(a.date).toDateString() === today;
            }).length;
            animateNumber(el, todayCount || articles.length);
        }
    }

    function animateNumber(el, target) {
        var current = 0;
        var duration = 1000;
        var step = Math.ceil(target / (duration / 30));
        var interval = setInterval(function () {
            current += step;
            if (current >= target) {
                current = target;
                clearInterval(interval);
            }
            el.textContent = current;
        }, 30);
    }

    // ─── Filter buttons ───
    document.querySelectorAll('.filter-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            document.querySelectorAll('.filter-btn').forEach(function (b) { b.classList.remove('active'); });
            btn.classList.add('active');
            currentFilter = btn.getAttribute('data-filter');
            displayedCount = 0;
            displayArticles();
        });
    });

    var loadMoreBtn = document.getElementById('load-more-btn');
    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', function () {
            displayArticles();
        });
    }

    // ─── JSON Formatter ───
    var jsonModal = document.getElementById('json-modal');
    var openJsonBtn = document.getElementById('open-json-btn');
    var jsonModalClose = document.getElementById('json-modal-close');
    var jsonFormatBtn = document.getElementById('json-format-btn');
    var jsonCopyBtn = document.getElementById('json-copy-btn');
    var jsonClearBtn = document.getElementById('json-clear-btn');
    var jsonInput = document.getElementById('json-input');
    var jsonOutput = document.getElementById('json-output');

    function openModal(modal) { if (modal) modal.classList.add('active'); }
    function closeModal(modal) { if (modal) modal.classList.remove('active'); }

    if (openJsonBtn) openJsonBtn.addEventListener('click', function () { openModal(jsonModal); });
    if (jsonModalClose) jsonModalClose.addEventListener('click', function () { closeModal(jsonModal); });
    if (jsonModal) {
        jsonModal.querySelector('.modal-overlay').addEventListener('click', function () { closeModal(jsonModal); });
    }

    if (jsonFormatBtn && jsonInput && jsonOutput) {
        jsonFormatBtn.addEventListener('click', function () {
            try {
                var parsed = JSON.parse(jsonInput.value);
                jsonOutput.textContent = JSON.stringify(parsed, null, 2);
                jsonOutput.style.color = 'var(--success)';
            } catch (e) {
                jsonOutput.textContent = 'Error: ' + e.message;
                jsonOutput.style.color = 'var(--danger)';
            }
        });
    }

    if (jsonCopyBtn && jsonOutput) {
        jsonCopyBtn.addEventListener('click', function () {
            navigator.clipboard.writeText(jsonOutput.textContent);
        });
    }

    if (jsonClearBtn && jsonInput && jsonOutput) {
        jsonClearBtn.addEventListener('click', function () {
            jsonInput.value = '';
            jsonOutput.textContent = '';
        });
    }

    // ─── Diff Checker ───
    var diffModal = document.getElementById('diff-modal');
    var openDiffBtn = document.getElementById('open-diff-btn');
    var diffModalClose = document.getElementById('diff-modal-close');
    var diffCompareBtn = document.getElementById('diff-compare-btn');
    var diffClearBtn = document.getElementById('diff-clear-btn');
    var diffLeft = document.getElementById('diff-left');
    var diffRight = document.getElementById('diff-right');
    var diffOutput = document.getElementById('diff-output');

    if (openDiffBtn) openDiffBtn.addEventListener('click', function () { openModal(diffModal); });
    if (diffModalClose) diffModalClose.addEventListener('click', function () { closeModal(diffModal); });
    if (diffModal) {
        diffModal.querySelector('.modal-overlay').addEventListener('click', function () { closeModal(diffModal); });
    }

    if (diffCompareBtn && diffLeft && diffRight && diffOutput) {
        diffCompareBtn.addEventListener('click', function () {
            var leftLines = diffLeft.value.split('\n');
            var rightLines = diffRight.value.split('\n');
            var maxLen = Math.max(leftLines.length, rightLines.length);
            var html = '';
            for (var i = 0; i < maxLen; i++) {
                var l = leftLines[i] || '';
                var r = rightLines[i] || '';
                if (l === r) {
                    html += '<div class="diff-line">&nbsp; ' + escapeHtml(l) + '</div>';
                } else {
                    if (l) html += '<div class="diff-line diff-remove">- ' + escapeHtml(l) + '</div>';
                    if (r) html += '<div class="diff-line diff-add">+ ' + escapeHtml(r) + '</div>';
                }
            }
            diffOutput.innerHTML = html || '<div class="diff-line">No differences found.</div>';
        });
    }

    if (diffClearBtn && diffLeft && diffRight && diffOutput) {
        diffClearBtn.addEventListener('click', function () {
            diffLeft.value = '';
            diffRight.value = '';
            diffOutput.innerHTML = '';
        });
    }

    // ─── Chat Widget ───
    (function initChatWidget() {
        var chatToggle = document.getElementById('chat-toggle');
        var chatWindow = document.getElementById('chat-window');
        var chatClose = document.getElementById('chat-close');
        var chatForm = document.getElementById('chat-form');
        var chatInput = document.getElementById('chat-input');
        var chatMessages = document.getElementById('chat-messages');
        var chatReset = document.getElementById('chat-reset');
        var chatMaximize = document.getElementById('chat-maximize');
        var chatStop = document.getElementById('chat-stop');
        var chatClearInput = document.getElementById('chat-clear-input');
        var attachBtn = document.getElementById('chat-attach-btn');
        var micBtn = document.getElementById('chat-mic-btn');
        var fileInput = document.getElementById('chat-file-input');
        var attachmentsContainer = document.getElementById('chat-attachments');
        var chatInputContainer = chatWindow ? chatWindow.querySelector('.chat-input-container') : null;
        var resizeTopHandle = document.getElementById('chat-resize-top');
        var resizeLeftHandle = document.getElementById('chat-resize-left');
        var openChatBtn = document.getElementById('open-chat-btn');

        if (!chatToggle || !chatWindow || !chatClose || !chatForm || !chatInput || !chatMessages) return;

        var isOpen = false;
        var isMaximized = false;
        var isSending = false;
        var currentAbort = null;
        var pendingFiles = [];
        var conversationHistory = [];
        var maxAttachments = 4;
        var maxFileBytes = 25 * 1024 * 1024;
        var maxTextChars = 12000;
        var minChatHeight = 420;
        var maxChatHeight = 860;
        var minChatWidth = 320;
        var maxChatWidth = 700;
        var resizeState = null;
        var WELCOME_MSG = 'Hey! I\'m an AI assistant. Ask me anything — QA, AI testing, coding, science, or just chat.';

        var CONSENT_KEY = 'alexpavsky_chat_consent';
        var chatConsent = document.getElementById('chat-consent');
        var chatConsentCb = document.getElementById('chat-consent-cb');
        var chatConsentBtn = document.getElementById('chat-consent-btn');
        var hasConsent = false;
        try { hasConsent = localStorage.getItem(CONSENT_KEY) === 'yes'; } catch (e) {}

        if (hasConsent && chatConsent) chatConsent.classList.add('hidden');

        var chatTermsLink = document.getElementById('chat-terms-link');
        var chatTermsPanel = document.getElementById('chat-terms-panel');
        var chatTermsBack = document.getElementById('chat-terms-back');
        var chatConsentMain = document.getElementById('chat-consent-main');

        if (chatTermsLink) {
            chatTermsLink.addEventListener('click', function (e) {
                e.preventDefault();
                if (chatConsentMain) chatConsentMain.classList.add('hidden');
                if (chatTermsPanel) chatTermsPanel.classList.remove('hidden');
            });
        }
        if (chatTermsBack) {
            chatTermsBack.addEventListener('click', function () {
                if (chatTermsPanel) chatTermsPanel.classList.add('hidden');
                if (chatConsentMain) chatConsentMain.classList.remove('hidden');
            });
        }

        if (chatConsentCb && chatConsentBtn) {
            chatConsentCb.addEventListener('change', function () {
                chatConsentBtn.disabled = !chatConsentCb.checked;
            });
            chatConsentBtn.addEventListener('click', function () {
                if (!chatConsentCb.checked) return;
                try { localStorage.setItem(CONSENT_KEY, 'yes'); } catch (e) {}
                hasConsent = true;
                if (chatConsent) chatConsent.classList.add('hidden');
                chatInput.focus();
            });
        }

        var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        var recognition = null;
        var isListening = false;
        var voiceLangs = [
            {code: '', label: 'Auto'}, {code: 'en-US', label: 'EN'}, {code: 'ru-RU', label: 'RU'},
            {code: 'es-ES', label: 'ES'}, {code: 'de-DE', label: 'DE'}, {code: 'fr-FR', label: 'FR'},
            {code: 'zh-CN', label: '中文'}, {code: 'pt-BR', label: 'PT'}, {code: 'ar-SA', label: 'AR'},
        ];
        var voiceLangIndex = 0;

        function openChat(focusInput) {
            if (isOpen) { if (focusInput) chatInput.focus(); return; }
            isOpen = true;
            chatWindow.classList.add('active');
            chatToggle.classList.add('active');
            if (!isMaximized) {
                isMaximized = true;
                chatWindow.classList.add('maximized');
                if (chatMaximize) {
                    var icon = chatMaximize.querySelector('i');
                    if (icon) icon.className = 'fas fa-compress-arrows-alt';
                }
            }
            if (focusInput) chatInput.focus();
        }

        function closeChat() {
            if (!isOpen) return;
            isOpen = false;
            chatWindow.classList.remove('active');
            chatToggle.classList.remove('active');
        }

        chatToggle.addEventListener('click', function () {
            if (isOpen) closeChat(); else openChat(true);
        });
        chatClose.addEventListener('click', closeChat);
        if (openChatBtn) openChatBtn.addEventListener('click', function () { openChat(true); });

        if (chatMaximize) {
            chatMaximize.addEventListener('click', function () {
                isMaximized = !isMaximized;
                chatWindow.classList.toggle('maximized', isMaximized);
                var icon = chatMaximize.querySelector('i');
                if (icon) icon.className = isMaximized ? 'fas fa-compress-arrows-alt' : 'fas fa-expand-arrows-alt';
            });
        }

        if (chatStop) {
            chatStop.addEventListener('click', function () {
                if (currentAbort) { currentAbort.abort(); currentAbort = null; }
                isSending = false;
                chatStop.classList.remove('visible');
                hideTypingIndicator();
            });
        }

        if (chatClearInput) {
            chatInput.addEventListener('input', function () {
                chatClearInput.classList.toggle('visible', chatInput.value.length > 0);
            });
            chatClearInput.addEventListener('click', function () {
                chatInput.value = '';
                chatClearInput.classList.remove('visible');
                chatInput.focus();
            });
        }

        if (chatReset) {
            chatReset.addEventListener('click', function () {
                if (currentAbort) { currentAbort.abort(); currentAbort = null; }
                isSending = false;
                if (chatStop) chatStop.classList.remove('visible');
                chatMessages.innerHTML = '<div class="chat-message bot-message"><div class="message-avatar"><i class="fas fa-robot"></i></div><div class="message-content"><p>' + WELCOME_MSG + '</p></div></div>';
                chatInput.value = '';
                pendingFiles = [];
                conversationHistory = [];
                renderPendingFiles();
                if (chatClearInput) chatClearInput.classList.remove('visible');
            });
        }

        // ── File upload ──
        function addFiles(files) {
            if (!files || !files.length) return;
            Array.from(files).forEach(function (file) {
                if (pendingFiles.length >= maxAttachments) return;
                if (file.size > maxFileBytes) return;
                var dup = pendingFiles.some(function (f) { return f.name === file.name && f.size === file.size; });
                if (!dup) pendingFiles.push(file);
            });
            renderPendingFiles();
        }

        function renderPendingFiles() {
            if (!attachmentsContainer) return;
            attachmentsContainer.innerHTML = '';
            pendingFiles.forEach(function (file, idx) {
                var chip = document.createElement('div');
                chip.className = 'chat-attachment-chip';
                var iconClass = file.type.startsWith('image/') ? 'fa-image' : 'fa-file';
                chip.innerHTML = '<i class="fas ' + iconClass + '"></i><span class="chip-name">' + escapeHtml(file.name) + '</span><button type="button" class="chat-attachment-remove" data-index="' + idx + '">&times;</button>';
                attachmentsContainer.appendChild(chip);
            });
            attachmentsContainer.querySelectorAll('.chat-attachment-remove').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    pendingFiles.splice(Number(this.dataset.index), 1);
                    renderPendingFiles();
                });
            });
        }

        if (attachBtn && fileInput) {
            attachBtn.addEventListener('click', function () { fileInput.click(); });
            fileInput.addEventListener('change', function () { addFiles(fileInput.files); fileInput.value = ''; });
        }

        var dropZone = chatWindow;
        if (dropZone) {
            dropZone.addEventListener('dragover', function (e) { e.preventDefault(); if (chatInputContainer) chatInputContainer.classList.add('drag-over'); });
            dropZone.addEventListener('dragleave', function (e) {
                if (!dropZone.contains(e.relatedTarget)) {
                    if (chatInputContainer) chatInputContainer.classList.remove('drag-over');
                }
            });
            dropZone.addEventListener('drop', function (e) {
                e.preventDefault();
                if (chatInputContainer) chatInputContainer.classList.remove('drag-over');
                if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
            });
        }

        // ── Voice input ──
        if (micBtn && SpeechRecognition) {
            recognition = new SpeechRecognition();
            recognition.lang = voiceLangs[0].code;
            recognition.interimResults = false;
            recognition.maxAlternatives = 1;
            recognition.continuous = false;

            var langBadge = document.createElement('span');
            langBadge.className = 'mic-lang-badge';
            langBadge.textContent = voiceLangs[0].label;
            micBtn.appendChild(langBadge);

            recognition.onstart = function () { isListening = true; micBtn.classList.add('active'); };
            recognition.onend = function () { isListening = false; micBtn.classList.remove('active'); };
            recognition.onerror = function () { isListening = false; micBtn.classList.remove('active'); };

            recognition.onresult = function (event) {
                var spoken = event && event.results && event.results[0] && event.results[0][0] ? event.results[0][0].transcript.trim() : '';
                if (!spoken) return;
                var cur = chatInput.value.trim();
                chatInput.value = cur ? cur + ' ' + spoken : spoken;
                if (chatClearInput) chatClearInput.classList.toggle('visible', chatInput.value.length > 0);
                chatInput.focus();
            };

            micBtn.addEventListener('click', function () {
                if (isListening) recognition.stop();
                else { recognition.lang = voiceLangs[voiceLangIndex].code; recognition.start(); }
            });

            micBtn.addEventListener('contextmenu', function (e) {
                e.preventDefault();
                if (isListening) recognition.stop();
                voiceLangIndex = (voiceLangIndex + 1) % voiceLangs.length;
                recognition.lang = voiceLangs[voiceLangIndex].code;
                langBadge.textContent = voiceLangs[voiceLangIndex].label;
            });
        } else if (micBtn) {
            micBtn.disabled = true;
            micBtn.title = 'Voice input not supported in this browser';
        }

        // ── File serialization ──
        function isTextFile(file) {
            if (file.type && file.type.startsWith('text/')) return true;
            var name = (file.name || '').toLowerCase();
            return ['.txt', '.md', '.csv', '.json', '.xml', '.yml', '.yaml'].some(function (ext) { return name.endsWith(ext); });
        }

        function readAsDataUrl(file) {
            return new Promise(function (resolve, reject) {
                var reader = new FileReader();
                reader.onload = function () { resolve(String(reader.result || '')); };
                reader.onerror = function () { reject(reader.error || new Error('read_error')); };
                reader.readAsDataURL(file);
            });
        }

        function serializeAttachments(files) {
            var promises = files.map(function (file) {
                var base = { name: file.name, type: file.type || 'application/octet-stream', size: file.size };
                if (file.type.startsWith('image/')) {
                    return readAsDataUrl(file).then(function (url) { return Object.assign(base, { kind: 'image', data_url: url }); });
                }
                if (isTextFile(file)) {
                    return file.text().then(function (text) {
                        var truncated = text.length > maxTextChars;
                        return Object.assign(base, { kind: 'text', text: truncated ? text.slice(0, maxTextChars) : text, truncated: truncated });
                    });
                }
                return Promise.resolve(Object.assign(base, { kind: 'file' }));
            });
            return Promise.all(promises);
        }

        // ── Messages ──
        function addUserMessage(text, attachments) {
            var safeText = text ? '<p>' + escapeHtml(text) + '</p>' : '';
            var names = (attachments || []).map(function (a) { return a && a.name; }).filter(Boolean);
            var filesLine = names.length ? '<p>📎 ' + escapeHtml(names.join(', ')) + '</p>' : '';
            var div = document.createElement('div');
            div.className = 'chat-message user-message';
            div.innerHTML = '<div class="message-avatar"><i class="fas fa-user"></i></div><div class="message-content">' + (safeText || '<p>📎 Sent attachment(s)</p>') + filesLine + '</div>';
            chatMessages.appendChild(div);
            scrollToBottom();
        }

        function addBotMessage(text) {
            var div = document.createElement('div');
            div.className = 'chat-message bot-message';
            var safe = escapeHtml(text).replace(/\n/g, '<br>');
            div.innerHTML = '<div class="message-avatar"><i class="fas fa-robot"></i></div><div class="message-content"><p>' + safe + '</p></div>';
            chatMessages.appendChild(div);
            scrollToBottom();
        }

        function showTypingIndicator() {
            var div = document.createElement('div');
            div.className = 'chat-message bot-message typing-indicator-msg';
            div.innerHTML = '<div class="message-avatar"><i class="fas fa-robot"></i></div><div class="message-content typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>';
            chatMessages.appendChild(div);
            scrollToBottom();
        }

        function hideTypingIndicator() {
            var el = chatMessages.querySelector('.typing-indicator-msg');
            if (el) el.remove();
        }

        function scrollToBottom() { chatMessages.scrollTop = chatMessages.scrollHeight; }

        // ── Submit ──
        chatForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            if (isSending) return;
            var message = chatInput.value.trim();
            if (!message && !pendingFiles.length) return;

            var queuedFiles = pendingFiles.slice();
            var attachmentSummary = queuedFiles.map(function (f) {
                return { name: f.name, type: f.type, size: f.size, kind: f.type.startsWith('image/') ? 'image' : 'file' };
            });

            isSending = true;
            if (recognition && isListening) recognition.stop();

            try {
                var attachments = await serializeAttachments(queuedFiles);
                addUserMessage(message, attachmentSummary);
                chatInput.value = '';
                if (chatClearInput) chatClearInput.classList.remove('visible');
                pendingFiles = [];
                renderPendingFiles();
                showTypingIndicator();
                await handleBotResponse(message, attachments);
            } catch (err) {
                hideTypingIndicator();
                addBotMessage('Could not process the request. Please try again.');
            } finally {
                isSending = false;
            }
        });

        // ── API call ──
        async function handleBotResponse(userMessage, attachments) {
            currentAbort = new AbortController();
            if (chatStop) chatStop.classList.add('visible');

            try {
                conversationHistory.push({ role: 'user', content: userMessage || '[attachment]' });
                var response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    signal: currentAbort.signal,
                    body: JSON.stringify({ message: userMessage, attachments: attachments || [], history: conversationHistory.slice(0, -1) })
                });

                if (!response.ok) {
                    var errText = '';
                    try { var errData = await response.json(); errText = errData.message || errData.error || ''; } catch (_) {}
                    throw new Error(errText || 'HTTP ' + response.status);
                }

                var data = await response.json();
                hideTypingIndicator();

                var reply = data && typeof data.reply === 'string' ? data.reply : '';
                var botMsg = reply || 'Sorry, I didn\'t get a response. Please try again.';
                conversationHistory.push({ role: 'assistant', content: botMsg });
                if (conversationHistory.length > 40) conversationHistory = conversationHistory.slice(-30);
                addBotMessage(botMsg);

            } catch (error) {
                hideTypingIndicator();
                if (error && error.name === 'AbortError') {
                    addBotMessage('Response stopped.');
                } else {
                    addBotMessage(getFallbackReply(userMessage));
                }
            } finally {
                currentAbort = null;
                isSending = false;
                if (chatStop) chatStop.classList.remove('visible');
            }
        }

        function getFallbackReply(msg) {
            var lower = (msg || '').toLowerCase();
            if (/^(hi|hello|hey|привет|здравствуй)/.test(lower))
                return 'Hey there! Ask me anything — AI testing, coding, science, history, or just chat.';
            if (/prompt.?inject|jailbreak|red.?team/i.test(lower))
                return 'Prompt injection is a top security risk in LLM apps. Common vectors: direct injection, indirect via retrieved context, and system prompt extraction. Tools like Promptfoo and Garak are great for automated red teaming.';
            if (/playwright|automat|e2e|selenium/i.test(lower))
                return 'Playwright is excellent for E2E testing — Chromium, Firefox, WebKit with one API. For AI apps, combine Playwright with custom assertions that check LLM output quality, not just UI state.';
            if (/eval|rag|agent|llm.?test|deepeval|promptfoo/i.test(lower))
                return 'LLM evaluation is evolving fast. Key frameworks: Promptfoo (open-source, CI-friendly), DeepEval (Python-native metrics), Phoenix (tracing + evals). For RAG: focus on context relevance, faithfulness, and answer completeness.';
            if (/python|javascript|typescript|code|function|bug|error/i.test(lower))
                return 'I can help with coding questions! For the full experience, the AI backend will use Groq, Gemini, and OpenRouter models. Right now I\'m running in demo mode with preset answers.';
            if (/weather|news|price|stock/i.test(lower))
                return 'I don\'t have real-time data access in demo mode. Once the full backend is connected, I\'ll use models with web search capabilities for current information.';
            return 'Great question! I\'m currently in demo mode — the full AI backend (Groq + Gemini + OpenRouter) is coming soon and will handle any topic. Try asking about prompt injection, Playwright, LLM evaluation, or coding!';
        }

        // ── Resize ──
        function getMaxChatHeight() { return Math.max(minChatHeight, Math.min(maxChatHeight, window.innerHeight - 110)); }
        function getMaxChatWidth() { return Math.max(minChatWidth, Math.min(maxChatWidth, window.innerWidth - 40)); }

        function applyChatHeight(h) {
            if (window.innerWidth <= 480) return;
            var clamped = Math.max(minChatHeight, Math.min(getMaxChatHeight(), h));
            chatWindow.style.height = Math.round(clamped) + 'px';
        }
        function applyChatWidth(w) {
            if (window.innerWidth <= 480) return;
            var clamped = Math.max(minChatWidth, Math.min(getMaxChatWidth(), w));
            chatWindow.style.width = Math.round(clamped) + 'px';
        }

        function startResize(edge, event) {
            if (window.innerWidth <= 480) return;
            event.preventDefault();
            var rect = chatWindow.getBoundingClientRect();
            resizeState = { edge: edge, startX: event.clientX, startY: event.clientY, startHeight: rect.height, startWidth: rect.width };
            chatWindow.classList.add('resizing');
            window.addEventListener('pointermove', onResizeMove);
            window.addEventListener('pointerup', stopResize);
        }

        function onResizeMove(event) {
            if (!resizeState) return;
            if (resizeState.edge === 'top') applyChatHeight(resizeState.startHeight - (event.clientY - resizeState.startY));
            else applyChatWidth(resizeState.startWidth - (event.clientX - resizeState.startX));
        }

        function stopResize() {
            if (!resizeState) return;
            resizeState = null;
            chatWindow.classList.remove('resizing');
            window.removeEventListener('pointermove', onResizeMove);
            window.removeEventListener('pointerup', stopResize);
            try {
                localStorage.setItem('chat_h', chatWindow.style.height);
                localStorage.setItem('chat_w', chatWindow.style.width);
            } catch (_) {}
        }

        if (resizeTopHandle) resizeTopHandle.addEventListener('pointerdown', function (e) { startResize('top', e); });
        if (resizeLeftHandle) resizeLeftHandle.addEventListener('pointerdown', function (e) { startResize('left', e); });

        try {
            var sh = parseInt(localStorage.getItem('chat_h'));
            var sw = parseInt(localStorage.getItem('chat_w'));
            if (sh) applyChatHeight(sh);
            if (sw) applyChatWidth(sw);
        } catch (_) {}

        renderPendingFiles();
    })();

    // ─── Terminal typing animation ───
    var terminalLines = document.querySelectorAll('#terminal-body .t-output');
    terminalLines.forEach(function (line, i) {
        line.style.opacity = '0';
        line.style.transform = 'translateX(-10px)';
        line.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
        setTimeout(function () {
            line.style.opacity = '1';
            line.style.transform = 'translateX(0)';
        }, 800 + i * 400);
    });

    // ─── Init ───
    loadAllFeeds();

    setInterval(loadAllFeeds, 30 * 60 * 1000);
})();
