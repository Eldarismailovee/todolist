import { EventSourceParserStream } from 'eventsource-parser/stream';

import { AuthenticationRequired, freshAccess, streamHttp } from './http';

type EventHandler = (event: string, data: string) => void;

/** Пауза без бизнес-событий: heartbeat приходит раз в 15 секунд. */
const WATCHDOG_MS = 45_000;

/**
 * Один SSE-сеанс. Нативный EventSource не используется: ему нельзя передать
 * заголовок Authorization, а токен в query string авторизацией не является.
 */
export async function openTaskStream(
  signal: AbortSignal,
  onEvent: EventHandler,
): Promise<void> {
  const token = await freshAccess('sse');
  if (signal.aborted) return;

  // Watchdog прекращает зависшее чтение даже при потере связи без EOF.
  const watchdog = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const resetWatchdog = () => {
    clearTimeout(timer);
    timer = setTimeout(() => watchdog.abort(), WATCHDOG_MS);
  };
  const abort = () => watchdog.abort();
  signal.addEventListener('abort', abort, { once: true });
  resetWatchdog();

  try {
    // BufferSource, а не Uint8Array: writable у TextDecoderStream объявлен именно так.
    const response = await streamHttp.get<ReadableStream<BufferSource>>('/tasks/stream', {
      responseType: 'stream',
      signal: watchdog.signal,
      headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
    });

    if (!String(response.headers['content-type']).includes('text/event-stream')) {
      await response.data.cancel();
      throw new Error('Сервер вернул неверный формат потока');
    }

    // Потоковый разбор: границы чанков, UTF-8 и многострочные data наивным
    // split('\n\n') корректно не разбираются.
    const reader = response.data
      .pipeThrough(new TextDecoderStream())
      .pipeThrough(
        new EventSourceParserStream({
          onComment: resetWatchdog,
          onError: 'terminate',
          maxBufferSize: 65_536,
        }),
      )
      .getReader();

    try {
      while (!signal.aborted) {
        const { value, done } = await reader.read();
        if (done) return;
        resetWatchdog();
        if (value.event === 'auth_revoked') {
          throw new AuthenticationRequired('Сессия завершена');
        }
        onEvent(value.event ?? 'message', value.data);
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', abort);
    watchdog.abort();
  }
}
