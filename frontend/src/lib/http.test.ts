import { AxiosError, AxiosHeaders } from 'axios';
import { describe, expect, it } from 'vitest';

import { api, AuthenticationRequired, describeError, fieldErrors } from './http';

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
