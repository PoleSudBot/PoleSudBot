from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(slots=True)
class ImageInput:
    """用户输入图片在插件内部的统一表示。"""

    content: bytes
    url: str | None = None
    filename: str = "image.jpg"
    mimetype: str = "image/jpeg"


@dataclass(slots=True)
class SearchCandidate:
    """单个结果内部的候选项。"""

    title: str
    subtitle: str = ""


@dataclass(slots=True)
class SearchResultItem:
    """单条搜索结果的统一展示模型。"""

    title: str
    display_index: int | None = None
    url: str = ""
    links: list[str] = field(default_factory=list)
    source: str = ""
    author: str = ""
    similarity: float | None = None
    thumbnail_url: str = ""
    thumbnail_data_uri: str = ""
    thumbnail_visible: bool = True
    hidden: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    candidates: list[SearchCandidate] = field(default_factory=list)


@dataclass(slots=True)
class SearchSection:
    """一个搜索来源在结果页中的展示区块。"""

    title: str
    subtitle: str = ""
    items: list[SearchResultItem] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    link_lines: list[str] = field(default_factory=list)
    error: str = ""


@dataclass(slots=True)
class SearchPresentation:
    """命令处理层最终发送给用户的内容。"""

    title: str
    sections: list[SearchSection] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    link_lines: list[str] = field(default_factory=list)

    def build_link_text(self) -> str:
        """生成与汇总图编号对应的链接清单。"""

        lines = [line for line in self.link_lines if line.strip()]
        return "\n".join(lines) if lines else "本次结果没有可展示的来源链接。"


@dataclass(slots=True)
class SauceNAOQuota:
    """SauceNAO 返回的配额信息。"""

    short_remaining: int | None = None
    long_remaining: int | None = None


@dataclass(slots=True)
class SauceNAOResult:
    """SauceNAO 查询结果和配额状态。"""

    items: list[SearchResultItem] = field(default_factory=list)
    quota: SauceNAOQuota = field(default_factory=SauceNAOQuota)
    search_url: str = ""


@dataclass(slots=True)
class AniListInfo:
    """AniList 补全后的番剧信息。"""

    title: str = ""
    is_adult: bool = False
    cover_image: str = ""
    site_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImageTag:
    """图片 tag 和模型给出的置信度。"""

    name: str
    score: float

    @property
    def score_percent(self) -> float:
        """把 0-1 或 0-100 两类常见分数统一成百分比展示值。"""

        return self.score * 100 if self.score <= 1 else self.score

    @property
    def score_ratio(self) -> float:
        """把分数压到 0-1 区间，供结果图进度条使用。"""

        if self.score <= 1:
            return max(0, min(self.score, 1))
        return max(0, min(self.score / 100, 1))


@dataclass(frozen=True, slots=True)
class ImageTagResult:
    """一次图片 tag 识别的展示结果。"""

    tags: list[ImageTag]
    ratings: list[ImageTag]
    threshold: float
    model: str
    total_count: int

    def limited(self, limit: int) -> "ImageTagResult":
        """按配置限制展示 tag 数量，同时保留接口原始命中总数。"""

        return replace(self, tags=self.tags[: max(1, limit)])

    def build_copy_text(self) -> str:
        """生成方便用户复制到提示词或搜索框的 tag 文本。"""

        if not self.tags:
            return "tags: "
        return "tags: " + ",".join(tag.name for tag in self.tags)

    def build_score_text(self) -> str:
        """生成带置信度的文本兜底结果。"""

        if not self.tags:
            return "未识别到可用 tag。"
        lines = [
            f"{index}. {tag.name} {tag.score_percent:.1f}%"
            for index, tag in enumerate(self.tags, start=1)
        ]
        omitted = self.total_count - len(self.tags)
        if omitted > 0:
            lines.append(f"...另有 {omitted} 个 tag 已按配置省略")
        return "\n".join(lines)
