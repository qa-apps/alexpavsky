const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const { JSDOM } = require('jsdom');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const articles = [
  { source: 'Testomat Blog', title: 'Deterministic AI Decisions: Why Explorbot Added Jev', link: 'https://testomat.io/blog/deterministic-ai/', date: '2026-09-23T16:22:28Z', category: 'qa', sections: 8 },
  { source: 'Mabl Blog', title: 'Your ADLC Is Outrunning Your Quality Gates', link: 'https://www.mabl.com/blog/your-adlc-is-outrunning-your-quality-gates', date: '2026-09-23T20:07:33Z', category: 'qa', sections: 4 }
];
const scripts = ['assets/vendor/Readability.js', 'assets/vendor/purify.min.js', 'article-reader.js'];

async function renderer() {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://www.alexpavsky.com/', runScripts: 'outside-only' });
  for (const script of scripts) dom.window.eval(await fs.readFile(path.join(root, script), 'utf8'));
  return dom;
}

test('renderer strips executable markup, resolves links, and preserves semantic article content', async () => {
  const dom = await renderer();
  try {
    const text = 'Repeatable tests make browser automation easier to reason about. '.repeat(15);
    const html = `<h1>SEO title</h1><h1>Real title</h1><div class="content-col"><p>${text}<a href="javascript:alert(1)">unsafe</a><a href="/blog/">relative</a></p><h2>Details</h2><p>${text}</p><img src="https://testomat.io/example.png" onerror="alert(1)"><script>alert(1)</script><iframe src="https://example.com"></iframe><table><tr><th>Heading</th><td>Data</td></tr></table></div>`;
    const result = dom.window.ArticleReader.render(html, { url: articles[0].link, source: 'Testomat', title: 'Fallback title', image: 'javascript:alert(1)' });
    assert.doesNotMatch(result, /<script|<iframe|onerror|javascript:/i);
    const doc = new JSDOM(result).window.document;
    assert.equal(doc.querySelectorAll('h1').length, 1);
    assert.equal(doc.querySelector('h1').textContent, 'Real title');
    assert.equal(doc.querySelectorAll('.reader-prose h2').length, 1);
    assert.ok(doc.querySelector('.reader-table table'));
    assert.ok(doc.querySelector('a[href="https://testomat.io/blog/"]'));
    assert.ok(!doc.querySelector('.reader-cover'));
  } finally { dom.window.close(); }
});

test('generic publisher without source adapter or headings still renders', async () => {
  const dom = await renderer();
  try {
    const text = 'The article explains how to measure quality without losing the surrounding context. '.repeat(15);
    const result = dom.window.ArticleReader.render(`<article><h1>Another publisher</h1><p>${text}</p><p>${text}</p></article>`, { url: 'https://example.org/story', source: 'Example', title: 'Another publisher' });
    assert.match(result, /reader-no-contents/);
    assert.doesNotMatch(result, /Invalid Date/);
    assert.match(result, /Another publisher/);
  } finally { dom.window.close(); }
});

test('real production article payloads: desktop/mobile, reader controls, and fallback', { timeout: 180000 }, async () => {
  const payloads = new Map();
  const dom = await renderer();
  for (const item of articles) {
    const response = await fetch('https://www.alexpavsky.com/api/article-page?url=' + encodeURIComponent(item.link), { signal: AbortSignal.timeout(20000) });
    assert.equal(response.status, 200);
    const html = await response.text();
    const metadata = await (await fetch('https://www.alexpavsky.com/api/article-proxy?url=' + encodeURIComponent(item.link), { signal: AbortSignal.timeout(20000) })).json();
    payloads.set(item.link, { html, metadata });
    const result = dom.window.ArticleReader.render(html, { ...item, url: item.link, image: metadata.image });
    const parsed = new JSDOM(result);
    const doc = parsed.window.document;
    assert.equal(doc.querySelector('h1').textContent, item.title);
    assert.equal(doc.querySelectorAll('.reader-prose h2').length, item.sections);
    assert.ok(doc.querySelector('.reader-prose').textContent.length > 4000);
    assert.doesNotMatch(doc.querySelector('.reader-prose').textContent, /Latest articles|Read other posts|Quality Engineering Resources/);
    parsed.window.close();
  }
  dom.window.close();
  let failArticle = false;
  let delayArticle = false;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
    if (url.pathname === '/api/feed') return json({ articles });
    if (url.pathname === '/api/youtube') return json({ videos: [] });
    if (url.pathname === '/api/article-page' || url.pathname === '/api/article-proxy') {
      const content = payloads.get(url.searchParams.get('url'));
      if (!content || failArticle) { res.writeHead(502); return res.end('Unavailable'); }
      if (url.pathname.endsWith('proxy')) return json(content.metadata);
      res.setHeader('Content-Type', 'text/html');
      if (delayArticle) return setTimeout(() => res.end(content.html), 600);
      return res.end(content.html);
    }
    if (url.pathname.startsWith('/api/')) { res.writeHead(404); return json({ error: 'Not part of reader integration test' }); }
    const file = url.pathname === '/' ? 'index.html' : url.pathname === '/voice-api/widget.js' ? 'voice-agent/widget.js' : url.pathname.slice(1);
    if (file.includes('..')) { res.writeHead(400); return res.end(); }
    try {
      let data = await fs.readFile(path.join(root, file));
      if (file === 'script.js') data = Buffer.from(data.toString().replace("var base = isLocalPreview ? 'http://127.0.0.1:8000' : '';", "var base = '';"));
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream');
      res.end(data);
    } catch (_) { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch();
  await fs.mkdir(path.join(root, 'screenshots'), { recursive: true });
  try {
    for (const width of [1440, 1024, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: width > 600 ? 1000 : 844 }, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(base + '/#feed');
      for (const [i, item] of articles.entries()) {
        const card = page.locator('.feed-card').filter({ hasText: item.title }).first();
        await card.click();
        await page.locator('.reader-article h1').waitFor();
        assert.equal(await page.locator('.reader-article h1').textContent(), item.title);
        await page.waitForFunction(() => { const img = document.querySelector('.reader-cover img'); return img?.complete && img.naturalWidth > 0; });
        await page.screenshot({ path: path.join(root, `screenshots/reader-${i}-${width}.png`) });
        const layout = await page.locator('.article-modal-card').evaluate(el => {
          const r = el.getBoundingClientRect();
          const reader = el.querySelector('.article-modal-reader');
          return { left: r.left, right: r.right, overflow: reader.scrollWidth - reader.clientWidth, controlsFit: [...el.querySelectorAll('.article-modal-actions button, .article-modal-actions a')].every(e => { const c = e.getBoundingClientRect(); return c.left >= r.left && c.right <= r.right + 1; }) };
        });
        assert.ok(layout.left >= -1 && layout.right <= width + 1 && layout.overflow <= 1 && layout.controlsFit, JSON.stringify(layout));
        assert.equal(await page.locator('.chat-toggle').isVisible(), false);
        assert.equal(await page.locator('#va-voice-agent').isVisible(), false);
        assert.equal(await page.locator('#va-voice-agent .launcher').isVisible(), false);
        await page.locator('#reader-theme').click();
        await page.locator('#reader-larger').click();
        assert.equal(await page.locator('#article-modal-reader').evaluate(el => el.style.getPropertyValue('--reader-font-size')), '20px');
        await page.locator('#reader-smaller').click();
        if (width > 1000) {
          await page.locator('.reader-contents a').last().click();
          await page.waitForFunction(() => document.querySelector('#article-modal-reader').scrollTop > 500);
        }
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#article-modal-overlay').evaluate(el => el.classList.contains('active')), false);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    const page = await browser.newPage();
    await page.goto(base + '/#feed');
    failArticle = true;
    await page.locator('.feed-card').filter({ hasText: articles[0].title }).first().click();
    await page.locator('.reader-fallback').waitFor();
    assert.equal(await page.locator('#article-modal-link').getAttribute('href'), articles[0].link);
    await page.keyboard.press('Escape');
    failArticle = false;
    delayArticle = true;
    await page.locator('.feed-card').filter({ hasText: articles[0].title }).first().click();
    await page.keyboard.press('Escape');
    delayArticle = false;
    await page.locator('.feed-card').filter({ hasText: articles[1].title }).first().click();
    await page.locator('.reader-article').waitFor();
    await page.waitForTimeout(700);
    assert.equal(await page.locator('.reader-article h1').textContent(), articles[1].title);
    await page.close();
  } finally {
    await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
