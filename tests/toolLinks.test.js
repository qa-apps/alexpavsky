const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const indexHtml = fs.readFileSync(path.join(repoRoot, 'index.html'), 'utf8');

function hrefForToolCard(title) {
  const cards = indexHtml.match(/<a\s+[^>]*class="[^"]*tool-card[^"]*"[^>]*>[\s\S]*?<\/a>/gi) || [];
  const match = cards
    .find((card) => new RegExp(`<h4>${title}</h4>`, 'i').test(card))
    ?.match(/href="([^"]+)"/i);
  assert.ok(match, `${title} tool card should exist`);
  return match[1];
}

test('DeepEval tool card opens DeepEval, not Confident AI', () => {
  assert.equal(hrefForToolCard('DeepEval'), 'https://deepeval.com/');
});
