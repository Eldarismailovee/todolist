interface Props {
  id: string;
  message?: string;
}

/**
 * Сообщение об ошибке поля. Идентификатор обязателен: поле ссылается на него
 * через aria-describedby (см. invalidProps), иначе программа чтения с экрана
 * объявит поле неверным, не сказав, что именно не так.
 */
export const FieldError = ({ id, message }: Props) =>
  message ? (
    <p id={id} className="text-xs text-red-500">
      {message}
    </p>
  ) : null;
