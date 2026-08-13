const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const scriptSource = fs.readFileSync(path.join(repoRoot, 'script.js'), 'utf8');

function extractFunctionSource(name) {
  const start = scriptSource.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} should exist`);

  const braceStart = scriptSource.indexOf('{', start);
  assert.notEqual(braceStart, -1, `${name} should have a body`);

  let depth = 0;
  for (let i = braceStart; i < scriptSource.length; i += 1) {
    const char = scriptSource[i];
    if (char === '{') depth += 1;
    if (char === '}') {
      depth -= 1;
      if (depth === 0) return scriptSource.slice(start, i + 1);
    }
  }

  throw new Error(`${name} body was not closed`);
}

function shouldReuseWithActiveAttachment(message) {
  const functionSource = extractFunctionSource('shouldReuseAttachmentContext');
  return Function(`
    var activeContextAttachments = [{ kind: 'image', name: 'screenshot.png' }];
    ${functionSource}
    return shouldReuseAttachmentContext(arguments[0]);
  `)(message);
}

test('plain short prompts do not inherit a previous attachment', () => {
  assert.equal(shouldReuseWithActiveAttachment('tell me about qa'), false);
  assert.equal(shouldReuseWithActiveAttachment('are u dump?'), false);
  assert.equal(shouldReuseWithActiveAttachment('hello'), false);
});

test('explicit attachment follow-up prompts can reuse the previous attachment', () => {
  assert.equal(shouldReuseWithActiveAttachment('what is in this image?'), true);
  assert.equal(shouldReuseWithActiveAttachment('summarize the attached file'), true);
});

test('failed attachment requests clear reusable attachment context', () => {
  assert.match(
    scriptSource,
    /if \(errReply\) \{\s*if \(attachments && attachments\.length\) activeContextAttachments = \[\];/s,
  );
  assert.match(
    scriptSource,
    /else if \(attachments && attachments\.length\) \{\s*activeContextAttachments = \[\];/s,
  );
});

test('attachment failure copy separates backend-down from free model failure', () => {
  assert.match(scriptSource, /The chat backend is unreachable/);
  assert.match(scriptSource, /free image-analysis model is temporarily unavailable/);
});
