from types import SimpleNamespace

import pytest

from crawler import score_contents


def _content(text: str):
    return SimpleNamespace(text=text)


def test_score_contents_matches_any_keyword_in_title():
    contents = [_content("财政,补贴")]  # Chinese comma should split into keywords
    scores = score_contents("财政部发布新政策", "与补贴相关的通知", contents)
    assert len(scores) == 1
    assert scores[0][0] is contents[0]
    assert scores[0][1] == 1.0


def test_score_contents_matches_keyword_in_summary():
    contents = [_content("医疗;教育")]
    scores = score_contents("不相关标题", "此次改革重点加强医疗体系建设", contents)
    assert scores[0][1] == 1.0


def test_score_contents_falls_back_to_similarity_when_no_keyword_match(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("crawler.similarity", lambda text, candidates: [0.5 for _ in candidates])
    contents = [_content("创新发展")]  # 与文本无直接关键字匹配
    scores = score_contents("不相关标题", "完全不同的描述", contents)
    assert len(scores) == 1
    assert scores[0][0] is contents[0]
    assert scores[0][1] < 1.0
