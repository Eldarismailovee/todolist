"""Проверка того, что загруженный файл действительно того типа, что заявлен.

`UploadFile.content_type` приходит из браузера и проверкой не является: клиент
называет тип сам. Байты сохраняются под расширением, выбранным по заявленному
типу, и отдаются с ним же в Content-Type, поэтому расхождение — это не
косметика, а выдача произвольного содержимого под видом картинки.

Проверяется сигнатура формата. Размеры изображения и стоимость декодирования
здесь не ограничиваются: для этого нужен декодер (Pillow), которого в
зависимостях нет, и это остаётся незакрытым риском.
"""

SVG_MARKER = b"<svg"
# Достаточно начала файла: у SVG перед корневым тегом бывают декларация XML,
# комментарии и DOCTYPE.
SVG_PROBE_BYTES = 4096


def _is_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _is_svg(data: bytes) -> bool:
    head = data[:SVG_PROBE_BYTES].lstrip()
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:].lstrip()
    if not head.startswith(b"<"):
        return False
    return SVG_MARKER in head.lower()


def matches_declared_type(content_type: str, data: bytes) -> bool:
    """Совпадает ли фактический формат с заявленным клиентом."""
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return _is_webp(data)
    if content_type == "image/svg+xml":
        return _is_svg(data)
    # Неизвестный тип сюда не доходит: список разрешённых проверяется раньше.
    return False
