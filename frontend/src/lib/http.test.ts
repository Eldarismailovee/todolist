import { AxiosError, AxiosHeaders } from 'axios';
import { describe, expect, it } from 'vitest';

import { AuthenticationRequired, describeError, fieldErrors } from './http';

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

describe('fieldErrors', () => {
  it('раскладывает ошибки 422 по именам полей', () => {
    const error = axiosError(422, {
      detail: [
        { loc: ['body', 'attributes', 'label'], msg: 'обязательное поле', type: 'missing' },
        { loc: ['body', 'title'], msg: 'слишком длинно', type: 'too_long' },
      ],
    });

    expect(fieldErrors(error)).toEqual({
      label: 'обязательное поле',
      title: 'слишком длинно',
    });
  });

  it('возвращает пустой объект, если ошибок полей нет', () => {
    expect(fieldErrors(axiosError(500, { detail: 'сломалось' }))).toEqual({});
    expect(fieldErrors(new Error('обычная ошибка'))).toEqual({});
  });
});
