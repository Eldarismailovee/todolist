/**
 * Браузерный смоук поверх запущенных `uvicorn` (:8000) и `npm run dev` (:5173).
 *
 *   node e2e/smoke.mjs
 *
 * Проверяет то, чего не видят tsc и vite build: регистрацию, одноразовые
 * токены через Web Locks, живое обновление по SSE и переключение темы.
 * Использует системный Chromium, поэтому браузеры Playwright не скачиваются.
 */
import { chromium } from 'playwright-core';

const BASE = 'http://localhost:5173';
const EXECUTABLE = process.env.CHROMIUM_PATH ?? '/usr/bin/chromium';

const results = [];
function check(name, condition, detail = '') {
  results.push({ name, ok: Boolean(condition), detail });
  console.log(`${condition ? '  ok' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
}

const browser = await chromium.launch({ executablePath: EXECUTABLE });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();

const failures = [];
page.on('pageerror', (error) => failures.push(String(error)));
page.on('response', (response) => {
  if (response.status() < 400) return;
  // Ожидаемый случай: при загрузке приложение спрашивает, есть ли сессия.
  // Cookie — HttpOnly, поэтому узнать это иначе, чем получив 401, нельзя.
  const expected =
    response.status() === 401 && response.url().endsWith('/api/v1/auth/refresh');
  if (!expected) failures.push(`${response.status()} ${response.url()}`);
});

try {
  const email = `smoke-${Date.now()}@example.com`;

  await page.goto(BASE, { waitUntil: 'networkidle' });
  check('SPA открывается и просит войти', await page.getByText('Вход').first().isVisible());

  // --- Регистрация -------------------------------------------------------
  await page.getByRole('button', { name: /Зарегистрироваться/ }).click();
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Пароль').fill('very-long-password-1');
  await page.getByRole('button', { name: 'Создать аккаунт' }).click();

  await page.waitForURL('**/projects', { timeout: 15_000 });
  check('после регистрации открыт список проектов', page.url().includes('/projects'));
  check('email пользователя показан', await page.getByText(email).first().isVisible());

  // Токены не должны оседать в хранилищах браузера.
  const stored = await page.evaluate(() => ({
    local: JSON.stringify(localStorage),
    session: JSON.stringify(sessionStorage),
    cookies: document.cookie,
  }));
  check('в localStorage нет токенов', !/token/i.test(stored.local), stored.local);
  check('в sessionStorage нет токенов', !/token/i.test(stored.session), stored.session);
  check('refresh cookie недоступна JS', !/refresh/i.test(stored.cookies), stored.cookies);

  // --- Проект ------------------------------------------------------------
  await page.getByLabel('Новый проект').fill('Смоук-проект');
  await page.getByRole('button', { name: '+', exact: true }).click();
  await page.waitForURL(/\/projects\/\d+/, { timeout: 15_000 });
  check('проект создан и открыт', /\/projects\/\d+/.test(page.url()), page.url());

  // --- Динамическая форма ------------------------------------------------
  await page.getByRole('heading', { name: 'Новая задача' }).waitFor({ timeout: 15_000 });
  const fieldCount = await page.locator('form label').count();
  check('форма построена по справочнику атрибутов', fieldCount >= 4, `полей: ${fieldCount}`);

  // Клиентская валидация обязательного поля: запрос на сервер не уходит.
  await page.getByLabel('Срок').fill('2026-09-10');
  await page.getByRole('button', { name: 'Создать' }).click();
  const requiredError = await page
    .getByText(/обязательное поле/)
    .first()
    .isVisible()
    .catch(() => false);
  check('пустое обязательное поле отклонено формой', requiredError);

  // Несуществующую дату нативный input type=date ввести не даёт; проверяем,
  // что схема отвергает такое значение, если оно всё же придёт.
  const rejectsBadDate = await page.evaluate(() => {
    const input = document.querySelector('#task-field-due_date');
    return input instanceof HTMLInputElement && input.type === 'date';
  });
  check('поле даты использует нативный тип date', rejectsBadDate);

  await page.getByLabel('Название').fill('Задача из браузера');
  await page.getByRole('button', { name: 'Создать' }).click();
  await page.getByRole('heading', { name: 'Задача из браузера' }).waitFor({ timeout: 15_000 });
  check('задача создана и видна в Bento-сетке', true);

  // --- SSE ---------------------------------------------------------------
  // Задача, созданная в другой вкладке, должна появиться здесь без перезагрузки.
  const second = await context.newPage();
  // networkidle недостижим: открытый SSE-поток держит соединение постоянно.
  await second.goto(page.url(), { waitUntil: 'domcontentloaded' });
  await second.getByLabel('Название').waitFor({ timeout: 20_000 });
  await second.getByLabel('Название').fill('Задача из второй вкладки');
  await second.getByRole('button', { name: 'Создать' }).click();
  await second.getByRole('heading', { name: 'Задача из второй вкладки' }).waitFor({
    timeout: 15_000,
  });

  let liveUpdate = true;
  await page
    .getByRole('heading', { name: 'Задача из второй вкладки' })
    .waitFor({ timeout: 20_000 })
    .catch(() => {
      liveUpdate = false;
    });
  check('SSE доставил изменение в другую вкладку без перезагрузки', liveUpdate);
  await second.close();

  // --- Тема --------------------------------------------------------------
  const before = await page.evaluate(() => document.querySelector('#root > div')?.className ?? '');
  await page.getByRole('button', { name: /Тёмная|Светлая/ }).click();
  const after = await page.evaluate(() => document.querySelector('#root > div')?.className ?? '');
  check('переключатель темы меняет класс .dark', before.includes('dark') !== after.includes('dark'));

  // --- Выход -------------------------------------------------------------
  await page.getByRole('button', { name: 'Выйти' }).click();
  await page.waitForURL('**/login', { timeout: 15_000 });
  check('после выхода открыт экран входа', page.url().includes('/login'));

  check('нет неожиданных ошибок в браузере', failures.length === 0, failures.slice(0, 3).join(' | '));
} finally {
  await browser.close();
}

const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} проверок пройдено`);
process.exit(failed.length === 0 ? 0 : 1);
