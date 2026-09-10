import axios from 'axios';

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

export async function login(email: string, password: string): Promise<void> {
  await withAuthLock(async () => {
    await authHttp.post('/login', { email, password });
  });
}

export async function register(email: string, password: string): Promise<void> {
  await withAuthLock(async () => {
    await authHttp.post('/register', { email, password });
  });
}

export async function logout(): Promise<void> {
  await withAuthLock(async () => {
    await authHttp.post('/logout');
  });
}

export const api = axios.create({ baseURL: '/api/v1', timeout: 15_000 });

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

/** Полевые ошибки 422 для переноса в react-hook-form через setError. */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!axios.isAxiosError(error)) return {};
  const detail = error.response?.data?.detail;
  if (!Array.isArray(detail)) return {};
  const result: Record<string, string> = {};
  for (const item of detail as Array<{ loc?: unknown[]; msg?: string }>) {
    const location = item.loc ?? [];
    const field = location[location.length - 1];
    if (typeof field === 'string' && item.msg && field !== 'attributes') {
      result[field] = item.msg;
    }
  }
  return result;
}
