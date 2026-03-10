from app.core.utils import split_articles, tokenize_query, best_sentence


def test_split_articles():
    text = "第一条 内容1\n第二条 内容2"
    items = split_articles(text)
    assert len(items) == 2
    assert items[0] == ("第一条", "内容1")
    assert items[1] == ("第二条", "内容2")


def test_tokenize_query():
    q = "法律法规 search"
    tokens = tokenize_query(q)
    assert "法律法规" in tokens
    assert "search" in tokens


def test_best_sentence():
    text = "句子一。句子二包含关键词。"
    tokens = ["关键词"]
    s, score = best_sentence(text, tokens)
    assert s == "句子二包含关键词"
    assert score == 1
