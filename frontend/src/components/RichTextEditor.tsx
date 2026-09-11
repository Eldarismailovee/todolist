import Image from '@tiptap/extension-image';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import { EditorContent, useEditor, type Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { useCallback, useEffect, useRef, useState } from 'react';

import { uploadImage } from '../api/queries';
import type { RichDocument } from '../api/types';
import { describeError } from '../lib/http';

interface Props {
  value: RichDocument | null;
  onChange: (document: RichDocument | null) => void;
  placeholder?: string;
}

const BUTTON =
  'rounded-lg px-2 py-1 text-xs font-medium transition-all hover:bg-gray-200 dark:hover:bg-gray-700';
const ACTIVE = 'bg-indigo-600/15 text-indigo-700 dark:text-indigo-300';

function ToolbarButton({
  editor,
  label,
  title,
  isActive,
  onClick,
}: {
  editor: Editor;
  label: string;
  title: string;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={isActive}
      // onMouseDown + preventDefault: клик по кнопке не должен уводить фокус
      // из редактора, иначе выделение теряется и команда применяется впустую.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      disabled={!editor.isEditable}
      className={`${BUTTON} ${isActive ? ACTIVE : ''}`}
    >
      {label}
    </button>
  );
}

export const RichTextEditor = ({ value, onChange, placeholder }: Props) => {
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [2, 3] } }),
      Image.configure({ inline: false }),
      Link.configure({ openOnClick: false, autolink: true }),
      Placeholder.configure({ placeholder: placeholder ?? 'Опишите задачу…' }),
    ],
    content: value ?? undefined,
    onUpdate: ({ editor: instance }) => {
      // Пустой документ отправляем как null: пустой абзац — это не содержимое.
      onChange(instance.isEmpty ? null : (instance.getJSON() as RichDocument));
    },
    editorProps: {
      attributes: {
        class:
          'prose-sm min-h-[8rem] max-w-none px-3 py-2 outline-none [&_img]:max-w-full [&_img]:rounded-xl',
      },
    },
  });

  // Внешнее изменение (открыли другую задачу) должно заменить содержимое,
  // но не сбивать курсор при собственном вводе.
  useEffect(() => {
    if (!editor) return;
    const current = editor.isEmpty ? null : (editor.getJSON() as RichDocument);
    if (JSON.stringify(current) !== JSON.stringify(value)) {
      // Второй аргумент — emitUpdate: подстановка не должна вызывать onUpdate
      // и записывать те же данные обратно.
      editor.commands.setContent(value ?? '', false);
    }
  }, [value, editor]);

  const insertImage = useCallback(
    async (file: File) => {
      if (!editor) return;
      setUploadError(null);
      setUploading(true);
      try {
        const attachment = await uploadImage(file);
        // Сервер вернул подписанную ссылку: <img> не может отправить токен.
        editor.chain().focus().setImage({ src: attachment.url, alt: attachment.filename }).run();
      } catch (error) {
        setUploadError(describeError(error));
      } finally {
        setUploading(false);
      }
    },
    [editor],
  );

  if (!editor) return null;

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-900">
      <div className="flex flex-wrap items-center gap-1 border-b border-gray-200 px-2 py-1.5 dark:border-gray-800">
        <ToolbarButton
          editor={editor}
          label="Ж"
          title="Полужирный"
          isActive={editor.isActive('bold')}
          onClick={() => editor.chain().focus().toggleBold().run()}
        />
        <ToolbarButton
          editor={editor}
          label="К"
          title="Курсив"
          isActive={editor.isActive('italic')}
          onClick={() => editor.chain().focus().toggleItalic().run()}
        />
        <ToolbarButton
          editor={editor}
          label="H2"
          title="Заголовок"
          isActive={editor.isActive('heading', { level: 2 })}
          onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        />
        <ToolbarButton
          editor={editor}
          label="•"
          title="Маркированный список"
          isActive={editor.isActive('bulletList')}
          onClick={() => editor.chain().focus().toggleBulletList().run()}
        />
        <ToolbarButton
          editor={editor}
          label="1."
          title="Нумерованный список"
          isActive={editor.isActive('orderedList')}
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
        />
        <ToolbarButton
          editor={editor}
          label="</>"
          title="Код"
          isActive={editor.isActive('codeBlock')}
          onClick={() => editor.chain().focus().toggleCodeBlock().run()}
        />
        <ToolbarButton
          editor={editor}
          label="“”"
          title="Цитата"
          isActive={editor.isActive('blockquote')}
          onClick={() => editor.chain().focus().toggleBlockquote().run()}
        />

        <span className="mx-1 h-4 w-px bg-gray-200 dark:bg-gray-700" />

        <button
          type="button"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => fileInput.current?.click()}
          disabled={uploading}
          className={BUTTON}
        >
          {uploading ? 'Загрузка…' : '🖼 Картинка'}
        </button>
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            // Значение сбрасывается: иначе тот же файл второй раз не выберется.
            event.target.value = '';
            if (file) void insertImage(file);
          }}
        />
      </div>

      <EditorContent editor={editor} />

      {uploadError && (
        <p role="alert" className="px-3 pb-2 text-xs text-red-500">
          {uploadError}
        </p>
      )}
    </div>
  );
};
