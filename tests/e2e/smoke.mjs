// Browser smoke test: sign in to a running Lily, visit the main pages, and fail on any
// non-2xx response, CSP violation or uncaught page error.
//
//   BASE_URL=http://localhost:8083 LILY_USER=harry LILY_PASSWORD=harry10 node tests/e2e/smoke.mjs
//
// A fresh instance forces a password change on first login; the script handles that and
// uses NEW_PASSWORD (default below) for the rest of the run. PLAYWRIGHT_CHROMIUM_PATH
// points at a browser binary when the default download is not available.
import { chromium } from 'playwright';

const base = process.env.BASE_URL || 'http://localhost:8083';
const user = process.env.LILY_USER || 'harry';
const password = process.env.LILY_PASSWORD || 'harry10';
const newPassword = process.env.NEW_PASSWORD || 'Sm0ke-test-pw-91!';

const PAGES = ['/', '/duplicates', '/me', '/cwa-settings', '/logs', '/admin/usertable', '/shelf/create',
  '/advsearch', '/author', '/series', '/category'];

const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined });
const page = await browser.newPage();
const problems = [];
let current = '(login)';
page.on('console', (m) => {
  if (/content security policy|refused to/i.test(m.text())) problems.push(`${current}: ${m.text().slice(0, 200)}`);
});
page.on('pageerror', (e) => problems.push(`${current}: uncaught ${e.message.slice(0, 200)}`));

async function submit() {
  await page.click('form.lily-login-form button[type=submit]');
  await page.waitForLoadState('networkidle');
}

await page.goto(`${base}/login`);
await page.fill('#username', user);
await page.fill('#password', password);
await submit();
if (page.url().includes('change-password')) {
  await page.fill('#current_password', password);
  await page.fill('#new_password', newPassword);
  await page.fill('#confirm_password', newPassword);
  await submit();
}
if (page.url().includes('/login')) problems.push('login failed');

for (const path of PAGES) {
  current = path;
  const resp = await page.goto(base + path);
  await page.waitForLoadState('networkidle');
  if (!resp || resp.status() >= 400) problems.push(`${path}: HTTP ${resp ? resp.status() : 'none'}`);
  console.log(resp ? resp.status() : '---', path);
}

await browser.close();
if (problems.length) {
  console.error('\nSMOKE FAILED:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nsmoke ok');
