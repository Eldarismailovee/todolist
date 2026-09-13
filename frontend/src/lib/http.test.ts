import { AxiosError, AxiosHeaders } from 'axios';
import { describe, expect, it } from 'vitest';

import {
  api,
  AuthenticationRequired,
  classifyError,
  describeError,
  fieldErrors,
  NetworkUnavailable,
  ServiceUnavailable,
} from './http';

function axiosError(status: number, data: unknown): AxiosError {
  const error = new AxiosError('request failed');
  error.response = {
    status,
    statusText: '',
    data,
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return error;
}

describe('describeError', () => {
  it('показывает текст detail от сервера', () => {
    expect(describeError(axiosError(409, { detail: 'Такой тег уже есть' }))).toBe(
      'Такой тег уже есть',
    );
  });

  it('берёт первое сообщение из списка ошибок валидации', () => {
    const error = axiosError(422, {
      detail: [{ loc: ['body', 'title'], msg: 'Строка слишком длинная', type: 'value_error' }],
    });
    expect(describeError(error)).toBe('Строка слишком длинная');
  });

  it('не раскрывает объект запроса, если detail отсутствует', () => {
    const message = describeError(axiosError(500, { unexpected: true }));
    expect(message).toBe('Ошибка 500');
    // В сообщении не должно быть ничего из config: там живёт Authorization.
    expect(message).not.toContain('headers');
  });

  it('различает сетевой сбой и ответ сервера', () => {
    const offline = new AxiosError('Network Error');
    expect(describeError(offline)).toBe('Сеть недоступна');
  });

  it('передаёт сообщение обычной ошибки', () => {
    expect(describeError(new AuthenticationRequired('Сессия завершена'))).toBe('Сессия завершена');
  });
});

describe('сериализация параметров запроса', () => {
  it('повторяет параметр для каждого значения массива', () => {
    // Контракт FastAPI: tag_id=1&tag_id=2. Скобочная форма tag_id[] в схеме
    // не объявлена, сервер её игнорирует и фильтр по тегам не применяется.
    const uri = api.getUri({ url: '/tasks', params: { project_id: 7, tag_id: [1, 2] } });

    expect(uri).toBe('/api/v1/tasks?project_id=7&tag_id=1&tag_id=2');
    expect(uri).not.toContain('%5B%5D');
  });

  it('не добавляет пустые параметры', () => {
    expect(api.getUri({ url: '/tasks', params: { project_id: 7, q: undefined } })).toBe(
      '/api/v1/tasks?project_id=7',
    );
  });
});

describe('fieldErrors', () => {
  it('раскладывает ошибки 422 по полному пути поля', () => {
    const error = axiosError(422, {
      detail: [
        { loc: ['body', 'attributes', 'label'], msg: 'обязательное поле', type: 'missing' },
        { loc: ['body', 'title'], msg: 'слишком длинно', type: 'too_long' },
      ],
    });

    // Путь сохраняется целиком: по последнему элементу loc вложенный атрибут
    // нельзя отличить от одноимённого поля верхнего уровня.
    expect(fieldErrors(error)).toEqual({
      'attributes.label': 'обязательное поле',
      title: 'слишком длинно',
    });
  });

  it('сохраняет индекс элемента массива', () => {
    const error = axiosError(422, {
      detail: [{ loc: ['body', 'tag_ids', 1], msg: 'не число', type: 'int_parsing' }],
    });

    expect(fieldErrors(error)).toEqual({ 'tag_ids.1': 'не число' });
  });

  it('оставляет первое сообщение по полю', () => {
    const error = axiosError(422, {
      detail: [
        { loc: ['body', 'title'], msg: 'первое', type: 'value_error' },
        { loc: ['body', 'title'], msg: 'второе', type: 'value_error' },
      ],
    });

    expect(fieldErrors(error)).toEqual({ title: 'первое' });
  });

  it('пропускает ошибки вне тела запроса', () => {
    const error = axiosError(422, {
      detail: [{ loc: ['query', 'project_id'], msg: 'обязательный', type: 'missing' }],
    });

    expect(fieldErrors(error)).toEqual({});
  });

  it('принимает только ответ 422 подходящей формы', () => {
    // Массив detail сам по себе не означает ошибку валидации.
    expect(
      fieldErrors(axiosError(409, { detail: [{ loc: ['body', 'title'], msg: 'x' }] })),
    ).toEqual({});
    expect(fieldErrors(axiosError(422, { detail: [{ message: 'без loc' }] }))).toEqual({});
    expect(fieldErrors(axiosError(500, { detail: 'сломалось' }))).toEqual({});
    expect(fieldErrors(new Error('обычная ошибка'))).toEqual({});
  });
});

describe('classifyError', () => {
  it('401 — единственная причина считать сессию завершённой', () => {
    expect(classifyError(axiosError(401, { detail: 'нет сессии' }))).toBeInstanceOf(
      AuthenticationRequired,
    );
  });

  it('429 и 5xx не завершают сессию', () => {
    // Раньше любой отказ обмена превращался в «войдите снова»: перегрузка
    // сервера выглядела как потерянный вход.
    const throttled = classifyError(axiosError(429, { detail: 'слишком часто' }));
    const broken = classifyError(axiosError(503, { detail: 'хранилище недоступно' }));

    expect(throttled).toBeInstanceOf(ServiceUnavailable);
    expect(broken).toBeInstanceOf(ServiceUnavailable);
    expect(throttled).not.toBeInstanceOf(AuthenticationRequired);
    expect(broken).not.toBeInstanceOf(AuthenticationRequired);
  });

  it('передаёт Retry-After, когда сервер его прислал', () => {
    const error = axiosError(429, { detail: 'слишком часто' });
    error.response!.headers = new AxiosHeaders({ 'retry-after': '30' });

    const classified = classifyError(error);

    expect(classified).toBeInstanceOf(ServiceUnavailable);
    expect((classified as ServiceUnavailable).retryAfterSeconds).toBe(30);
  });

  it('отсутствие ответа — неопределённый результат, а не отказ авторизации', () => {
    const offline = new AxiosError('Network Error');

    const classified = classifyError(offline);

    expect(classified).toBeInstanceOf(NetworkUnavailable);
    expect(describeError(classified)).toContain('результат операции неизвестен');
  });

  it('4xx, кроме 401 и 429, остаётся обычной ошибкой запроса', () => {
    const conflict = axiosError(409, { detail: 'Такой тег уже есть' });

    expect(classifyError(conflict)).toBe(conflict);
  });
});

describe('401 на проверке учётных данных', () => {
  it('не превращается в «войдите снова»', () => {
    // На экране входа сессии ещё нет: 401 здесь означает неверный код или
    // пароль, и сообщение сервера должно дойти до пользователя.
    const error = axiosError(401, { detail: 'Неверный или истёкший код' });
    error.config = { url: '/auth/otp/verify', headers: new AxiosHeaders() };

    const classified = classifyError(error);

    expect(classified).not.toBeInstanceOf(AuthenticationRequired);
    expect(describeError(classified)).toBe('Неверный или истёкший код');
  });

  it('на обычном маршруте 401 по-прежнему завершает сессию', () => {
    const error = axiosError(401, { detail: 'Требуется вход' });
    error.config = { url: '/projects', headers: new AxiosHeaders() };

    expect(classifyError(error)).toBeInstanceOf(AuthenticationRequired);
  });
});
