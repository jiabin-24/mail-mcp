from __future__ import annotations

import re
from pypinyin import Style, lazy_pinyin

# 仅用于识别中文字符，决定是否执行拼音扩展。
_CHINESE_CHAR_RE = re.compile(r"[\u3400-\u9fff]")
# 限制扩展数量，避免生成过长查询表达式。
_MAX_EXPANDED_TOKENS = 8


def _has_chinese(text: str) -> bool:
    return bool(_CHINESE_CHAR_RE.search(text))


def _get_pinyin_variants(token: str) -> list[str]:
    """为中文 token 生成拼音全拼与首字母缩写，支持搜索条件扩展。"""
    if not _has_chinese(token):
        return []

    full_parts = lazy_pinyin(token)
    initials_parts = lazy_pinyin(token, style=Style.FIRST_LETTER)
    variants: list[str] = []

    full = "".join(part for part in full_parts if part)
    if full:
        variants.append(full)

    initials = "".join(part for part in initials_parts if part)
    if initials:
        variants.append(initials)

    return variants


def expand_search_tokens(tokens: list[str], max_tokens: int = _MAX_EXPANDED_TOKENS) -> list[str]:
    """将原始搜索词扩展为更适合过滤查询的候选 token。

    作用包括：
    - 保留原始词本身，确保关键词搜索不丢失；
    - 对中文词生成拼音全拼和首字母变体，扩大命中范围；
    - 统一小写并去重，避免查询膨胀和重复条件；
    - 最终限制返回数量，防止过长的过滤表达式影响 Graph 查询性能。
    """
    expanded: list[str] = []
    seen: set[str] = set()

    def push(token: str) -> bool:
        # 统一为小写并去重，避免重复条件导致查询膨胀。
        normalized = token.strip().lower()
        if not normalized or normalized in seen:
            return False
        seen.add(normalized)
        expanded.append(normalized)
        return True

    token_limit = max(1, max_tokens)
    for token in tokens:
        push(token)
        if len(expanded) >= token_limit:
            break
        for variant in _get_pinyin_variants(token):
            push(variant)
            if len(expanded) >= token_limit:
                break

        if len(expanded) >= token_limit:
            break

    return expanded[:token_limit]
