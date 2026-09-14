import { useId, useRef, useState } from 'react';

import {
  useConfirmTelegram,
  useLinkTelegram,
  useNotificationPrefs,
  useSaveNotificationPrefs,
  useUnlinkTelegram,
} from '../api/queries';
import type { NotificationPrefs, NotificationPrefsUpdate } from '../api/types';
import { fieldRoot, invalidProps } from '../lib/fields';
import { api, describeError, fieldErrors } from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { FieldError } from './FieldError';

const field =
  'w-full rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800';

const DEFAULTS: NotificationPrefs = {
  email_enabled: true,
  telegram_enabled: false,
  telegram_chat_id: null,
  lead_time_minutes: 60,
};

export const SettingsPanel = () => {
  const user = useAuthStore((state) => state.user);
  const userId = user?.id ?? 0;
  const prefs = useNotificationPrefs(userId);
  const save = useSaveNotificationPrefs(userId);
  const linkTelegram = useLinkTelegram();
  const confirmTelegram = useConfirmTelegram(userId);
  const unlinkTelegram = useUnlinkTelegram(userId);

  const emailId = useId();
  const telegramId = useId();
  const chatId = useId();
  const codeId = useId();
  const leadId = useId();

  // Черновик появляется только после первой правки: до этого показываем то,
  // что пришло с сервера. Синхронизация через эффект вызывала бы каскадный
  // ререндер и затирала бы ввод при фоновом обновлении.
  const [draft, setDraft] = useState<NotificationPrefs | null>(null);
  const [testState, setTestState] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const inputs = useRef<Record<string, HTMLElement | null>>({});
  // Черновик подключения чата: номер вводится, потом в чат приходит код.
  const [chatDraft, setChatDraft] = useState('');
  const [code, setCode] = useState('');

  const form = draft ?? prefs.data ?? DEFAULTS;
  const setForm = setDraft;
  const linkedChatId = prefs.data?.telegram_chat_id ?? null;
  const codeSent = linkTelegram.isSuccess && !linkedChatId;
  const codeError = confirmTelegram.isError ? describeError(confirmTelegram.error) : undefined;

  const shownFields = ['lead_time_minutes'];
  const otherErrors = Object.entries(errors).filter(
    ([path]) => !shownFields.includes(fieldRoot(path)),
  );

  async function submit() {
    setErrors({});
    try {
      const body: NotificationPrefsUpdate = {
        email_enabled: form.email_enabled,
        telegram_enabled: form.telegram_enabled,
        lead_time_minutes: form.lead_time_minutes,
      };
      await save.mutateAsync(body);
    } catch (error) {
      const fields = fieldErrors(error);
      setErrors(fields);
      const first = shownFields.find((name) => fields[name]);
      if (first) inputs.current[first]?.focus();
    }
  }

  async function sendCode() {
    await linkTelegram.mutateAsync(chatDraft.trim());
  }

  async function confirmCode() {
    await confirmTelegram.mutateAsync(code.trim());
    setCode('');
    setChatDraft('');
    setDraft(null);
  }

  async function unlink() {
    await unlinkTelegram.mutateAsync();
    setDraft(null);
    linkTelegram.reset();
  }

  async function sendTest() {
    setTestState(null);
    try {
      await api.post('/notifications/test');
      setTestState('Отправили тестовое сообщение');
    } catch (error) {
      setTestState(describeError(error));
    }
  }

  return (
    <div className="max-w-xl space-y-6">
      <h2 className="text-lg font-bold tracking-tight">Уведомления о дедлайнах</h2>

      <section className="space-y-4 rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70">
        <div className="flex items-center justify-between gap-3">
          <label htmlFor={emailId} className="text-sm">
            Присылать на почту
          </label>
          <input
            id={emailId}
            type="checkbox"
            checked={form.email_enabled}
            onChange={(event) => setForm({ ...form, email_enabled: event.target.checked })}
            className="h-5 w-5 rounded accent-indigo-600"
          />
        </div>

        <div className="flex items-center justify-between gap-3">
          <label htmlFor={telegramId} className="text-sm">
            Присылать в Telegram
          </label>
          <input
            id={telegramId}
            type="checkbox"
            checked={form.telegram_enabled}
            disabled={linkedChatId === null}
            onChange={(event) => setForm({ ...form, telegram_enabled: event.target.checked })}
            className="h-5 w-5 rounded accent-indigo-600 disabled:opacity-50"
          />
        </div>

        {/*
          Подключение чата — не поле формы: номер, введённый в настройках, не
          доказывает, что чат принадлежит этому пользователю. Сервер шлёт код
          в указанный чат, и подключение засчитывается, только если код вернули.
        */}
        {linkedChatId !== null ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Чат подключён: <span className="font-medium">{linkedChatId}</span>
            </p>
            <button
              type="button"
              onClick={() => void unlink()}
              disabled={unlinkTelegram.isPending}
              className="rounded-xl border border-gray-200 px-3 py-1.5 text-xs transition-all hover:bg-gray-100 disabled:opacity-60 dark:border-gray-800 dark:hover:bg-gray-800"
            >
              Отключить чат
            </button>
          </div>
        ) : (
          <div className="space-y-3 rounded-xl border border-dashed border-gray-300 p-3 dark:border-gray-700">
            <div className="space-y-1.5">
              <label
                htmlFor={chatId}
                className="text-xs font-medium text-gray-500 dark:text-gray-400"
              >
                Telegram chat ID
              </label>
              <div className="flex flex-wrap gap-2">
                <input
                  id={chatId}
                  inputMode="numeric"
                  value={chatDraft}
                  placeholder="123456789"
                  onChange={(event) => setChatDraft(event.target.value)}
                  className={`${field} flex-1`}
                />
                <button
                  type="button"
                  onClick={() => void sendCode()}
                  disabled={linkTelegram.isPending || chatDraft.trim().length === 0}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm transition-all hover:bg-gray-100 disabled:opacity-60 dark:border-gray-800 dark:hover:bg-gray-800"
                >
                  Прислать код
                </button>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Напишите боту команду /start — он ответит вашим chat ID. Код придёт в этот чат.
              </p>
              {linkTelegram.isError && (
                <p role="alert" className="text-xs text-red-500">
                  {describeError(linkTelegram.error)}
                </p>
              )}
            </div>

            {codeSent && (
              <div className="space-y-1.5">
                <label
                  htmlFor={codeId}
                  className="text-xs font-medium text-gray-500 dark:text-gray-400"
                >
                  Код из чата
                </label>
                <div className="flex flex-wrap gap-2">
                  <input
                    id={codeId}
                    inputMode="numeric"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    className={`${field} flex-1`}
                    {...invalidProps(codeId, codeError)}
                  />
                  <button
                    type="button"
                    onClick={() => void confirmCode()}
                    disabled={confirmTelegram.isPending || code.trim().length === 0}
                    className="rounded-xl bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-all hover:bg-indigo-500 disabled:opacity-60"
                  >
                    Подтвердить
                  </button>
                </div>
                {codeError && (
                  <p id={`${codeId}-error`} role="alert" className="text-xs text-red-500">
                    {codeError}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        <div className="space-y-1.5">
          <label htmlFor={leadId} className="text-xs font-medium text-gray-500 dark:text-gray-400">
            Предупреждать заранее, минут
          </label>
          <input
            id={leadId}
            ref={(element) => {
              inputs.current.lead_time_minutes = element;
            }}
            type="number"
            min={5}
            max={10_080}
            value={form.lead_time_minutes}
            onChange={(event) =>
              setForm({ ...form, lead_time_minutes: Number(event.target.value) || 60 })
            }
            className={field}
            {...invalidProps(leadId, errors.lead_time_minutes)}
          />
          <FieldError id={`${leadId}-error`} message={errors.lead_time_minutes} />
        </div>

        {save.isError && (
          <div role="alert" className="space-y-1 text-xs text-red-500">
            {otherErrors.map(([path, message]) => (
              <p key={path}>{`${path}: ${message}`}</p>
            ))}
            {Object.keys(errors).length === 0 && <p>{describeError(save.error)}</p>}
          </div>
        )}
        {save.isSuccess && !save.isPending && (
          <p className="text-xs text-emerald-600 dark:text-emerald-400">Сохранено</p>
        )}
        {testState && <p className="text-xs text-gray-500 dark:text-gray-400">{testState}</p>}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void submit()}
            disabled={save.isPending}
            className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:bg-indigo-500 disabled:opacity-60"
          >
            {save.isPending ? 'Сохраняем…' : 'Сохранить'}
          </button>
          {form.telegram_enabled && (
            <button
              type="button"
              onClick={() => void sendTest()}
              className="rounded-xl border border-gray-200 px-4 py-2 text-sm transition-all hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800"
            >
              Проверить Telegram
            </button>
          )}
        </div>
      </section>
    </div>
  );
};
