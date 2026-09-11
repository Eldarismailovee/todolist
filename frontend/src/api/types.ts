import type { components } from './schema';

/**
 * Типы приходят из OpenAPI бэкенда (`npm run gen:api`), а не пишутся руками:
 * расхождение контракта ломает сборку, а не рантайм пользователя.
 */
export type MetaField = components['schemas']['MetaFieldResponse'];
export type Task = components['schemas']['TaskResponse'];
export type Project = components['schemas']['ProjectResponse'];
export type CurrentUser = components['schemas']['CurrentUserResponse'];
export type BoardColumn = components['schemas']['BoardColumnResponse'];
export type Tag = components['schemas']['TagResponse'];
export type Category = components['schemas']['CategoryResponse'];
export type Attachment = components['schemas']['AttachmentResponse'];
export type NotificationPrefs = components['schemas']['NotificationPrefsSchema'];
export type Analytics = components['schemas']['AnalyticsResponse'];
export type AssistAction = components['schemas']['AssistRequest']['action'];
export type AssistResult = components['schemas']['AssistResponse'];
export type OtpPurpose = components['schemas']['OtpVerifyRequest']['purpose'];
export type OAuthProvider = components['schemas']['OAuthProvidersResponse']['providers'][number];

/** Документ Tiptap: структура ProseMirror, а не HTML. */
export type RichDocument = Record<string, unknown>;

/** Значение динамического атрибута: date приходит строкой YYYY-MM-DD. */
export type AttributeValue = string | boolean;
