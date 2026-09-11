import { expect, test, type Page } from '@playwright/test';

/**
 * Сквозной сценарий: регистрация с кодом из письма, канбан с перетаскиванием,
 * редактор, дедлайн, поиск и аналитика.
 *
 * Код подтверждения тесту неоткуда «подсмотреть» — он хранится хешем. Поэтому
 * бэкенд в тестовом режиме (`OTP_LOG_CODES=true`) пишет код в лог, а сюда его
 * подставляет отдельный эндпоинт e2e-помощника, включённый тем же флагом.
 */

const PASSWORD = 'very-long-password-1';

function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1000)}@example.com`;
}

async function readOtp(page: Page, email: string, purpose: 'login' | 'register'): Promise<string> {
  const response = await page.request.get(
    `/api/v1/testing/otp?email=${encodeURIComponent(email)}&purpose=${purpose}`,
  );
  expect(response.ok(), 'e2e-помощник должен быть включён (OTP_LOG_CODES=true)').toBeTruthy();
  return (await response.json()).code;
}

async function signUp(page: Page, email: string): Promise<void> {
  await page.goto('/login');
  await page.getByRole('button', { name: /Зарегистрироваться/ }).click();
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Пароль').fill(PASSWORD);
  await page.getByRole('button', { name: 'Создать аккаунт' }).click();

  await expect(page.getByRole('heading', { name: 'Код из письма' })).toBeVisible();
  await page.getByLabel('Код подтверждения').fill(await readOtp(page, email, 'register'));
  await page.getByRole('button', { name: 'Подтвердить' }).click();
  await page.waitForURL(/\/projects/);
}

async function createProject(page: Page, title: string): Promise<void> {
  await page.getByLabel('Новый проект').fill(title);
  await page.getByRole('button', { name: '+', exact: true }).click();
  await page.waitForURL(/\/projects\/\d+/);
}

async function addTask(page: Page, title: string): Promise<void> {
  await page.getByLabel('Новая задача').fill(title);
  await page.getByRole('button', { name: 'Добавить' }).click();
  await expect(page.getByRole('button', { name: title, exact: true })).toBeVisible();
}

test('регистрация подтверждается кодом, а пароль сам по себе не пускает', async ({ page }) => {
  const email = uniqueEmail('otp');
  await page.goto('/login');
  await page.getByRole('button', { name: /Зарегистрироваться/ }).click();
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Пароль').fill(PASSWORD);
  await page.getByRole('button', { name: 'Создать аккаунт' }).click();

  // Пароль принят, но приложение всё ещё на экране входа.
  await expect(page.getByRole('heading', { name: 'Код из письма' })).toBeVisible();
  expect(page.url()).toContain('/login');

  await page.getByLabel('Код подтверждения').fill('000000');
  await page.getByRole('button', { name: 'Подтвердить' }).click();
  await expect(page.getByRole('alert')).toContainText(/код/i);

  await page.getByLabel('Код подтверждения').fill(await readOtp(page, email, 'register'));
  await page.getByRole('button', { name: 'Подтвердить' }).click();
  await page.waitForURL(/\/projects/);
  await expect(page.getByText(email)).toBeVisible();
});

test('канбан: задача перетаскивается в «Готово» и отмечается выполненной', async ({ page }) => {
  await signUp(page, uniqueEmail('dnd'));
  await createProject(page, 'Ремонт');
  await addTask(page, 'Купить плитку');

  const card = page.getByRole('button', { name: 'Купить плитку', exact: true });
  const doneColumn = page.getByRole('region', { name: 'Готово' });

  await expect(page.getByRole('region', { name: 'К выполнению' })).toContainText('Купить плитку');

  // Перетаскивание мышью: dnd-kit слушает pointer-события, поэтому шаги
  // делаются вручную, а не одним dragTo.
  const source = await card.boundingBox();
  const target = await doneColumn.boundingBox();
  expect(source && target).toBeTruthy();
  await page.mouse.move(source!.x + source!.width / 2, source!.y + source!.height / 2);
  await page.mouse.down();
  await page.mouse.move(target!.x + target!.width / 2, target!.y + 60, { steps: 12 });
  await page.mouse.up();

  await expect(doneColumn).toContainText('Купить плитку');
  // Ищем внутри колонки: так проверка не зацепит копию из оверлея.
  await expect(doneColumn.getByRole('button', { name: 'Купить плитку', exact: true })).toHaveClass(
    /line-through/,
  );
});

test('редактор сохраняет форматированный текст, срок и тег', async ({ page }) => {
  await signUp(page, uniqueEmail('editor'));
  await createProject(page, 'Планы');
  await addTask(page, 'Написать отчёт');

  await page.getByRole('button', { name: 'Написать отчёт', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Краткое описание').fill('Свести цифры за квартал');
  await dialog.getByLabel('Срок выполнения').fill('2027-01-15T10:00');

  await dialog.getByPlaceholder('новый тег').fill('финансы');
  await dialog.getByPlaceholder('новый тег').press('Enter');

  const editor = dialog.locator('.ProseMirror');
  await editor.click();
  await page.keyboard.type('Проверить остатки по складу');
  await dialog.getByTitle('Маркированный список').click();
  await page.keyboard.type('Первый пункт');

  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await expect(dialog).toBeHidden();

  await expect(page.getByText('Свести цифры за квартал')).toBeVisible();
  await expect(page.getByText('#финансы').first()).toBeVisible();
  await expect(page.getByText('15.01')).toBeVisible();

  // Содержимое переживает перезагрузку: оно сохранено на сервере.
  await page.reload();
  await page.getByRole('button', { name: 'Написать отчёт', exact: true }).click();
  await expect(page.getByRole('dialog').locator('.ProseMirror')).toContainText(
    'Проверить остатки по складу',
  );
});

test('поиск находит задачу по содержимому, фильтры сужают доску', async ({ page }) => {
  await signUp(page, uniqueEmail('search'));
  await createProject(page, 'Работа');
  await addTask(page, 'Позвонить подрядчику');
  await addTask(page, 'Отпуск');

  await page.getByLabel('Поиск по задачам').fill('подрядчику');
  await expect(
    page.getByRole('button', { name: 'Позвонить подрядчику', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Отпуск', exact: true })).toBeHidden();

  await page.getByLabel('Поиск по задачам').fill('');
  await expect(page.getByRole('button', { name: 'Отпуск', exact: true })).toBeVisible();
});

test('AI-помощник превращает подсказку в задачу', async ({ page }) => {
  await signUp(page, uniqueEmail('ai'));
  await createProject(page, 'Идеи');

  await page.getByLabel('Новая задача').fill('Организовать переезд офиса');
  await page.getByRole('button', { name: 'Разбить на шаги' }).click();

  const suggestion = page.locator('li').first();
  await expect(suggestion).toBeVisible();
  const text = (await suggestion.innerText()).replace('Создать задачу', '').trim();

  await suggestion.getByRole('button', { name: 'Создать задачу' }).click();
  await expect(page.getByRole('button', { name: text, exact: true })).toBeVisible();
});

test('аналитика показывает графики и таблицу значений', async ({ page }) => {
  await signUp(page, uniqueEmail('stats'));
  await createProject(page, 'Метрики');
  await addTask(page, 'Первая задача');

  await page.getByRole('link', { name: 'Аналитика' }).click();
  await page.waitForURL(/\/projects\/analytics/);

  await expect(page.getByRole('heading', { name: 'Аналитика' })).toBeVisible();
  await expect(page.getByText('Всего задач')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Создано и выполнено по дням' })).toBeVisible();
  // Легенда обязательна для двух серий: цвет не единственный признак.
  await expect(page.getByText('Создано', { exact: true })).toBeVisible();
  await expect(page.getByText('Выполнено', { exact: true }).first()).toBeVisible();

  await page.getByRole('button', { name: '7 дней' }).click();
  await expect(page.getByRole('button', { name: '7 дней' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});

test('настройки уведомлений сохраняются', async ({ page }) => {
  await signUp(page, uniqueEmail('notify'));
  await page.getByRole('link', { name: 'Уведомления' }).click();
  await page.waitForURL(/\/projects\/settings/);

  await page.getByLabel('Присылать в Telegram').check();
  await page.getByLabel('Telegram chat ID').fill('123456789');
  await page.getByLabel('Предупреждать заранее, минут').fill('120');
  await page.getByRole('button', { name: 'Сохранить' }).click();

  await expect(page.getByText('Сохранено')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Telegram chat ID')).toHaveValue('123456789');
});

test('выход завершает сессию во всех вкладках', async ({ page, context }) => {
  const email = uniqueEmail('logout');
  await signUp(page, email);
  await createProject(page, 'Общий');

  const second = await context.newPage();
  await second.goto(page.url());
  await expect(second.getByText(email)).toBeVisible();

  await page.getByRole('button', { name: 'Выйти' }).click();
  await page.waitForURL(/\/login/);

  // Вторая вкладка узнаёт о выходе через BroadcastChannel.
  await expect(second.getByText('Проверяем сессию…').or(second.getByLabel('Email'))).toBeVisible();
  await second.close();
});
