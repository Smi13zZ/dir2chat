"""文件处理：类型判定、哈希、图片压缩、长文本切片。"""

from __future__ import annotations

import hashlib
import io
import os
from typing import List, Tuple

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MARKDOWN_EXT = {".md", ".markdown", ".txt"}

# 图片 / 文件在部分通道有硬上限，压缩时逐步下调质量与尺寸。
_MIN_SCALE = 0.4
_JPEG_QUALITY_STEPS = [85, 75, 60, 45, 35]


def file_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in MARKDOWN_EXT:
        return "markdown"
    return "file"


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def compress_image_to_limit(data: bytes, limit: int) -> Tuple[bytes, str]:
    """把图片压到 limit 字节以内，返回 (字节, 后缀)。

    策略：优先降 JPEG 质量，再逐步缩尺寸。已是 PNG 且带透明通道时
    保留 PNG 格式以避免黑底；否则统一转 JPEG（体积最小）。
    """
    from PIL import Image

    if limit <= 0 or len(data) <= limit:
        return data, _guess_suffix(data)

    img = Image.open(io.BytesIO(data))
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    target_fmt = "PNG" if has_alpha else "JPEG"

    image = img
    if target_fmt == "JPEG" and image.mode != "RGB":
        image = image.convert("RGB")

    scale = 1.0
    while scale >= _MIN_SCALE:
        w, h = image.size
        candidate = image if scale == 1.0 else image.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
        )
        if target_fmt == "JPEG":
            for quality in _JPEG_QUALITY_STEPS:
                buf = io.BytesIO()
                candidate.save(buf, format="JPEG", quality=quality, optimize=True)
                if buf.tell() <= limit:
                    return buf.getvalue(), ".jpg"
        else:
            buf = io.BytesIO()
            candidate.save(buf, format="PNG", optimize=True)
            if buf.tell() <= limit:
                return buf.getvalue(), ".png"
        scale -= 0.15

    # 兜底：最后一档质量强行产出，宁可超限也让对方看到图。
    buf = io.BytesIO()
    if target_fmt == "JPEG":
        image.save(buf, format="JPEG", quality=30, optimize=True)
        return buf.getvalue(), ".jpg"
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), ".png"


def _guess_suffix(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ".bin"


def split_markdown(text: str, max_bytes: int) -> List[str]:
    """按段落切片，保证每片 UTF-8 编码后不超过 max_bytes。

    优先在空行处断开，其次在行尾断开，最后才硬切，尽量不破坏 markdown 结构。
    """
    if byte_len(text) <= max_bytes:
        return [text]

    chunks: List[str] = []
    buf = ""
    paragraphs = text.split("\n\n")

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for para in paragraphs:
        para = para.rstrip() + "\n\n"
        if byte_len(buf + para) <= max_bytes:
            buf += para
            continue
        flush()
        if byte_len(para) <= max_bytes:
            buf = para
            continue
        # 单个段落就超长，按行切
        for line in para.splitlines(keepends=True):
            if byte_len(buf + line) <= max_bytes:
                buf += line
                continue
            flush()
            if byte_len(line) <= max_bytes:
                buf = line
                continue
            # 单行也超长，硬切
            acc = ""
            for ch in line:
                if byte_len(acc + ch) > max_bytes:
                    chunks.append(acc)
                    acc = ""
                acc += ch
            buf = acc
    flush()

    total = len(chunks)
    if total > 1:
        chunks = [f"**({i}/{total})**\n\n{c}" for i, c in enumerate(chunks, 1)]
    return chunks


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}GB"
