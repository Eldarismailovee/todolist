import axios from 'axios';
import { z } from 'zod';

import type { CurrentUser, OtpChallenge } from '../api/types';

/**
 * Клиент API. Запросы авторизует сессионная cookie, которую браузер отправляет
 * сам: токенов в JavaScript нет, поэтому XSS нечего прочитать и незачем
 * обменивать refresh перед каждым запросом. Прежняя схема брала общий Web Lock
 * и делала отдельный обмен на каждый вызов — параллельные запросы выстраивались
 * в очередь сетевых обменов и транзакций БД.
 */
export const api = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  timeout: 15_000,
  // Заголовок не отправить в simple-запросе с чужого origin без preflight,
  // а preflight не пройдёт CORS: вместе с SameSite=Strict это защита от CSRF.
  headers: { 'X-CSRF-Guard': '1' },
  /**
   * FastAPI объявляет списочные параметры как повторяющиеся: tag_id=1&tag_id=2.
   * По умолчанию axios сериализует массив как tag_id[]=1&tag_id[]=2, а такого
   * параметра в схеме нет — сервер молча игнорирует его, и фильтр не работает.
   */
  paramsSerializer: { indexes: null },
});

/** Сессии нет: сервер ответил 401. Единственный повод считать вход потерянным. */
export class AuthenticationRequired extends Error {
  constructor(message = 'Нужно снова войти в аккаунт') {
    super(message);
    this.name = 'AuthenticationRequired';
  }
}

/**
 * Сервер ответил, но отказал временно: 429 или 5xx. Сессия при этом цела —
 * приравнивать такой отказ к «разлогинили» нельзя.
 */
export class ServiceUnavailable extends Error {
  constructor(
    message = 'Сервис временно недоступен, попробуйте позже',
    readonly status?: number,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = 'ServiceUnavailable';
  }
}

/**
 * Ответа не было вовсе: сеть, таймаут или прерванный запрос. Результат
 * операции неизвестен — она могла и выполниться, поэтому повторять её
 * автоматически нельзя.
 */
export class NetworkUnavailable extends Error {
  constructor(message = 'Нет связи с сервером: результат операции неизвестен') {
    super(message);
    this.name = 'NetworkUnavailable';
  }
}

/**
 * Маршруты, где 401 означает «предъявленные данные не подошли», а не
 * «сессия закончилась»: их вызывают до входа. Превращать такой отказ в
 * AuthenticationRequired нельзя — вместо «неверный код» пользователь видел бы
 * предложение войти заново на экране входа.
 */
const CREDENTIAL_ENDPOINTS = ['/auth/login', '/auth/register', '/auth/otp/verify'];

function isCredentialCheck(url: string | undefined): boolean {
  return CREDENTIAL_ENDPOINTS.some((endpoint) => (url ?? '').endsWith(endpoint));
}

function retryAfter(headers: unknown): number | undefined {
  const value = (headers as Record<string, unknown> | undefined)?.['retry-after'];
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : undefined;
}

/**
 * Разбор отказа по видам. Раньше любая ошибка обмена превращалась в
 * AuthenticationRequired, и перегрузка сервера или пропавший Wi-Fi выглядели
 * как завершённая сессия: интерфейс чистил состояние и требовал вход заново.
 */
export function classifyError(error: unknown): unknown {
  if (!axios.isAxiosError(error)) return error;

  const status = error.response?.status;
  if (status === undefined) {
    // Запрос отменён самим приложением — это не сбой связи.
    if (axios.isCancel(error)) return error;
    return new NetworkUnavailable();
  }
  if (status === 401) {
    return isCredentialCheck(error.config?.url) ? error : new AuthenticationRequired();
  }
  if (status === 429) {
    return new ServiceUnavailable(
      'Слишком много запросов, попробуйте позже',
      status,
      retryAfter(error.response?.headers),
    );
  }
  if (status >= 500) {
    return new ServiceUnavailable(describeError(error), status);
  }
  return error;
}

api.interceptors.response.use(
  (response) => response,
  (error: unknown) => Promise.reject(classifyError(error)),
);

/**
 * Instance для SSE. Свой таймаут отключён: поток живёт минутами. Cookie
 * отправляется так же, как в обычных запросах.
 */
export const streamHttp = axios.create({
  baseURL: '/api/v1',
  adapter: 'fetch',
  withCredentials: true,
  timeout: 0,
});

streamHttp.interceptors.response.use(
  (response) => response,
  (error: unknown) => Promise.reject(classifyError(error)),
);

/**
 * Пароль сам по себе сессию не создаёт: сервер отвечает 202 и присылает код
 * на почту. Сессия появляется только после `verifyOtp`.
 */
export async function login(email: string, password: string): Promise<OtpChallenge> {
  return (await api.post<OtpChallenge>('/auth/login', { email, password })).data;
}

export async function register(email: string, password: string): Promise<OtpChallenge> {
  return (await api.post<OtpChallenge>('/auth/register', { email, password })).data;
}

/** Подтверждение кода завершает вход: ответ — сам пользователь, сессия — в cookie. */
export async function verifyOtp(
  email: string,
  code: string,
  purpose: 'login' | 'register',
): Promise<CurrentUser> {
  return (await api.post<CurrentUser>('/auth/otp/verify', { email, code, purpose })).data;
}

/** Какие кнопки внешнего входа показывать: список задаёт сервер. */
export async function oauthProviders(): Promise<string[]> {
  const { data } = await api.get<{ providers: string[] }>('/auth/oauth/providers');
  return data.providers;
}

/**
 * Переход к провайдеру — обычная навигация: XHR не может пройти
 * межсайтовый редирект и сохранить cookie.
 */
export function startOAuth(provider: string): void {
  window.location.assign(`/api/v1/auth/oauth/${provider}/start`);
}

export async function logout(): Promise<void> {
  await api.post('/auth/logout');
}

/**
 * AxiosError несёт config вместе с заголовками — целиком такие объекты в логи
 * и телеметрию отправлять нельзя.
 */
export function describeError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
    if (error.response?.status) return `Ошибка ${error.response.status}`;
    return 'Сеть недоступна';
  }
  if (error instanceof Error) return error.message;
  return 'Неизвестная ошибка';
}

/**
 * Тело ошибки валидации FastAPI. Форма проверяется, а не предполагается:
 * массив `detail` встречается и в других ответах, а его элементы приходят из
 * сети и могут не иметь ни `loc`, ни `msg`.
 */
const ValidationBody = z.object({
  detail: z.array(
    z.object({
      loc: z.array(z.union([z.string(), z.number().int()])),
      msg: z.string(),
    }),
  ),
});

/**
 * Полевые ошибки 422: путь поля → сообщение.
 *
 * Ключ — полный путь внутри тела с точками и индексами: `attributes.deadline`,
 * `tag_ids.0`. Последний элемент `loc` для этого не годится: `attributes`
 * и `tag_ids` схлопывались бы в имя вложенного ключа, а разные поля с
 * одинаковым последним сегментом затирали бы друг друга.
 */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!axios.isAxiosError<unknown>(error) || error.response?.status !== 422) return {};
  const parsed = ValidationBody.safeParse(error.response.data);
  if (!parsed.success) return {};

  const result: Record<string, string> = {};
  for (const issue of parsed.data.detail) {
    // Ошибки query, path и заголовков к полям формы не относятся.
    if (issue.loc[0] !== 'body') continue;
    const path = issue.loc.slice(1).map(String).join('.');
    // Первое сообщение по полю: остальные обычно уточняют то же самое.
    if (path) result[path] ??= issue.msg;
  }
  return result;
}
