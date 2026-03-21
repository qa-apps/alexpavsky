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

    function updateTicker(articles) {
        var ticker = document.getElementById('ticker-content');
        if (!ticker || articles.length === 0) return;
        var headlines = articles.slice(0, 20);
        var html = '';
        headlines.forEach(function (a) {
            html += '<span class="ticker-item"><i class="fas fa-circle-dot"></i> ' +
                    '<strong>' + escapeHtml(a.source) + ':</strong> ' +
                    escapeHtml(a.title) + '</span>';
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
    var chatToggle = document.getElementById('chat-toggle');
    var chatWindow = document.getElementById('chat-window');
    var chatClose = document.getElementById('chat-close');
    var chatForm = document.getElementById('chat-form');
    var chatInput = document.getElementById('chat-input');
    var chatMessages = document.getElementById('chat-messages');
    var chatReset = document.getElementById('chat-reset');
    var openChatBtn = document.getElementById('open-chat-btn');
    var chatOpen = false;

    function toggleChat() {
        chatOpen = !chatOpen;
        chatWindow.classList.toggle('active', chatOpen);
        chatToggle.classList.toggle('active', chatOpen);
        if (chatOpen) chatInput.focus();
    }

    if (chatToggle) chatToggle.addEventListener('click', toggleChat);
    if (chatClose) chatClose.addEventListener('click', function () { if (chatOpen) toggleChat(); });
    if (openChatBtn) openChatBtn.addEventListener('click', function () { if (!chatOpen) toggleChat(); });

    if (chatReset && chatMessages) {
        chatReset.addEventListener('click', function () {
            chatMessages.innerHTML =
                '<div class="chat-message bot-message">' +
                '<div class="message-avatar"><i class="fas fa-robot"></i></div>' +
                '<div class="message-content"><p>Hey! I\'m an AI assistant focused on QA, testing, and AI safety topics. Ask me anything.</p></div>' +
                '</div>';
        });
    }

    if (chatForm && chatInput && chatMessages) {
        chatForm.addEventListener('submit', function (e) {
            e.preventDefault();
            var msg = chatInput.value.trim();
            if (!msg) return;

            var userDiv = document.createElement('div');
            userDiv.className = 'chat-message user-message';
            userDiv.innerHTML =
                '<div class="message-avatar"><i class="fas fa-user"></i></div>' +
                '<div class="message-content"><p>' + escapeHtml(msg) + '</p></div>';
            chatMessages.appendChild(userDiv);
            chatInput.value = '';
            chatMessages.scrollTop = chatMessages.scrollHeight;

            setTimeout(function () {
                var botDiv = document.createElement('div');
                botDiv.className = 'chat-message bot-message';
                botDiv.innerHTML =
                    '<div class="message-avatar"><i class="fas fa-robot"></i></div>' +
                    '<div class="message-content"><p>' + getBotReply(msg) + '</p></div>';
                chatMessages.appendChild(botDiv);
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }, 600);
        });
    }

    function getBotReply(msg) {
        var lower = msg.toLowerCase();
        if (/^(hi|hello|hey|привет)/.test(lower)) {
            return 'Hey there! Ask me anything about AI testing, QA automation, or red teaming.';
        }
        if (/prompt.?inject|jailbreak|red.?team/i.test(lower)) {
            return 'Prompt injection is one of the top security risks in LLM apps. Common vectors include direct injection, indirect injection via retrieved context, and system prompt extraction. Tools like Promptfoo and Garak are great for automated red teaming.';
        }
        if (/playwright|automat|e2e|test/i.test(lower)) {
            return 'Playwright is my go-to for E2E testing. It supports Chromium, Firefox, and WebKit with a single API. For AI apps, I combine Playwright with custom assertions that check LLM output quality, not just UI state.';
        }
        if (/eval|rag|agent|llm.?test/i.test(lower)) {
            return 'LLM evaluation is evolving fast. Key frameworks: Promptfoo (open-source, CI-friendly), DeepEval (Python-native metrics), and Phoenix (tracing + evals). For RAG, I focus on context relevance, faithfulness, and answer completeness.';
        }
        if (/tool|stack|what.*use/i.test(lower)) {
            return 'My current stack: Playwright for E2E, Promptfoo + DeepEval for LLM evals, Phoenix for observability, GitHub Actions for CI/CD, Docker for environments. For AI safety: Garak, custom red team scripts, and manual adversarial testing.';
        }
        return 'Interesting question! This is a demo chat — full AI backend coming soon. In the meantime, try asking about prompt injection, Playwright, LLM evaluation, or my tool stack.';
    }

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
