import axios from 'axios';
import { z } from 'zod';

/**
 * Обновление cookie идёт через отдельный instance без auth-interceptor:
 * иначе перехватчик вызвал бы сам себя.
 */
const authHttp = axios.create({
  baseURL: '/api/v1/auth',
  withCredentials: true,
  timeout: 15_000,
  headers: { 'X-CSRF-Guard': '1' },
});

export class AuthenticationRequired extends Error {
  constructor(message = 'Нужно снова войти в аккаунт') {
    super(message);
    this.name = 'AuthenticationRequired';
  }
}

/** Имя Web Lock: сериализует login/refresh/logout между всеми вкладками origin. */
const AUTH_LOCK = 'todo-auth-cookie';

/**
 * Каждый вызов делает СВОЙ обмен refresh: общий Promise, раздающий один access
 * нескольким запросам, нарушил бы одноразовость токена.
 *
 * Локального флага `isRefreshing` недостаточно — он не виден другим вкладкам,
 * а одновременный обмен одним значением cookie отзывает семейство сессии.
 */
export async function freshAccess(purpose: 'api' | 'sse'): Promise<string> {
  if (!navigator.locks) {
    // Без единого координатора параллельный refresh небезопасен: лучше
    // потребовать вход, чем молча гонять обмены наперегонки.
    throw new AuthenticationRequired('Браузеру нужен координатор сессии (Web Locks API)');
  }
  return navigator.locks.request(AUTH_LOCK, async () => {
    try {
      const { data } = await authHttp.post<{ access_token: string }>('/refresh', { purpose });
      return data.access_token;
    } catch {
      // Одноразовый refresh не повторяем автоматически: при неопределённом
      // результате повтор гарантированно отозвал бы семейство сессии.
      throw new AuthenticationRequired();
    }
  });
}

/** Операции, которым нужен тот же Web Lock, что и обмену refresh. */
export async function withAuthLock<T>(operation: () => Promise<T>): Promise<T> {
  if (!navigator.locks) return operation();
  return navigator.locks.request(AUTH_LOCK, operation);
}

export interface OtpChallenge {
  otp_required: true;
  purpose: 'login' | 'register';
  expires_in: number;
}

/**
 * Пароль сам по себе сессию не создаёт: сервер отвечает 202 и присылает код
 * на почту. Сессия появляется только после `verifyOtp`.
 */
export async function login(email: string, password: string): Promise<OtpChallenge> {
  return withAuthLock(async () => {
    const { data } = await authHttp.post<OtpChallenge>('/login', { email, password });
    return data;
  });
}

export async function register(email: string, password: string): Promise<OtpChallenge> {
  return withAuthLock(async () => {
    const { data } = await authHttp.post<OtpChallenge>('/register', { email, password });
    return data;
  });
}

export async function verifyOtp(
  email: string,
  code: string,
  purpose: 'login' | 'register',
): Promise<void> {
  await withAuthLock(async () => {
    await authHttp.post('/otp/verify', { email, code, purpose });
  });
}

/** Какие кнопки внешнего входа показывать: список задаёт сервер. */
export async function oauthProviders(): Promise<string[]> {
  const { data } = await authHttp.get<{ providers: string[] }>('/oauth/providers');
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
  await withAuthLock(async () => {
    await authHttp.post('/logout');
  });
}

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 15_000,
  /**
   * FastAPI объявляет списочные параметры как повторяющиеся: tag_id=1&tag_id=2.
   * По умолчанию axios сериализует массив как tag_id[]=1&tag_id[]=2, а такого
   * параметра в схеме нет — сервер молча игнорирует его, и фильтр не работает.
   */
  paramsSerializer: { indexes: null },
});

// Новый токен перед каждым защищённым запросом; в axios.defaults он не остаётся.
api.interceptors.request.use(async (config) => {
  const token = await freshAccess('api');
  config.headers.set('Authorization', `Bearer ${token}`);
  return config;
});

/**
 * Instance для SSE. Interceptor не используется: назначение токена другое,
 * а таймаут для длительного потока должен быть отключён.
 */
export const streamHttp = axios.create({
  baseURL: '/api/v1',
  adapter: 'fetch',
  timeout: 0,
});

/**
 * AxiosError несёт config вместе с заголовком Authorization — целиком такие
 * объекты в логи и телеметрию отправлять нельзя.
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
