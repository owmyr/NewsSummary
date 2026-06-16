"""Unit tests for the G1 source (with mocked HTTP)."""

from __future__ import annotations

import httpx

from daily_bot.models import ScrapedArticle
from daily_bot.sources import G1Source, default_registry
from daily_bot.sources.base import NewsSourceProtocol

# ---------------- Fixtures ----------------


def g1_homepage_html() -> str:
    """A small G1 homepage with several ``a.feed-post-link`` entries."""
    return """<!DOCTYPE html>
<html>
<head><title>G1 - O portal de not\u00edcias da Globo</title></head>
<body>
  <header>
    <a href="/">Home</a>
  </header>
  <main>
    <a class="feed-post-link" href="https://g1.globo.com/politica/noticia/2026/01/01/politica-1.ghtml">
      Pol\u00edtica: not\u00edcia um
    </a>
    <a class="feed-post-link" href="/mundo/noticia/2026/01/01/mundo-1.ghtml">
      Mundo: not\u00edcia um
    </a>
    <a class="feed-post-link" href="/tecnologia/noticia/2026/01/01/tech-1.ghtml">
      Tecnologia: not\u00edcia um
    </a>
    <a class="feed-post-link" href="https://g1.globo.com/economia/noticia/2026/01/01/economia-1.ghtml">
      Economia: not\u00edcia um
    </a>
    <a class="feed-post-link" href="/policia/noticia/2026/01/01/policia-1.ghtml">
      Pol\u00edcia: not\u00edcia um
    </a>
    <a class="feed-post-link" href="/tecnologia/noticia/2026/01/01/tech-1.ghtml">
      Duplicate of tecnologia
    </a>
    <a class="feed-post-link" href="/ultimas-noticias.shtml">
      N\u00e3o \u00e9 artigo (no .ghtml)
    </a>
    <a href="/algum-link-sem-classe.ghtml">Sem classe feed-post-link</a>
  </main>
</body>
</html>"""


def g1_homepage_html_no_articles() -> str:
    """A G1 homepage with no ``feed-post-link`` elements (e.g. outage / error page)."""
    return """<!DOCTYPE html>
<html>
<body>
  <header><a href="/">Home</a></header>
  <main>
    <p>No articles right now.</p>
    <a class="feed-post-link" href="/noticias/ultimas.shtml">Ultimas</a>
  </main>
</body>
</html>"""


def g1_homepage_html_with_other_subdomains() -> str:
    """A G1 homepage that also links to other Globo sub-domains (ge.globo.com, etc.).

    These should be filtered out so we only return ``g1.globo.com`` URLs.
    """
    return """<!DOCTYPE html>
<html>
<body>
  <main>
    <a class="feed-post-link" href="https://g1.globo.com/politica/noticia/2026/01/01/p-1.ghtml">G1 real</a>
    <a class="feed-post-link" href="https://ge.globo.com/futebol/copa/jogo/11-06-2026/x.ghtml">Globoesporte</a>
    <a class="feed-post-link" href="https://gshow.globo.com/novelas/2026/x.ghtml">Gshow</a>
    <a class="feed-post-link" href="https://g1.globo.com/economia/noticia/2026/01/01/e-1.ghtml">G1 economy</a>
  </main>
</body>
</html>"""


def g1_article_html() -> str:
    """A small G1 article page with title, paragraphs and og:image."""
    return """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta property="og:image" content="https://s2.glbimg.com/abc123/hero.jpg">
  <meta property="og:title" content="T\u00edtulo do artigo de teste">
  <title>Teste - G1</title>
</head>
<body>
  <main>
    <article>
      <header class="content-head">
        <h1 class="content-head__title">T\u00edtulo do artigo de teste</h1>
        <time>01/01/2026 - 10h00</time>
      </header>
      <div class="mc-article-body">
        <p>Primeiro par\u00e1grafo do corpo do artigo.</p>
        <p>Segundo par\u00e1grafo com mais detalhes sobre o assunto.</p>
        <p>Terceiro par\u00e1grafo concluindo a mat\u00e9ria.</p>
      </div>
    </article>
  </main>
</body>
</html>"""


def g1_article_html_minimal() -> str:
    """An article page without og:image, to exercise the <img> fallback."""
    return """<!DOCTYPE html>
<html lang="pt-BR">
<body>
  <main>
    <article>
      <h1>Artigo sem og:image</h1>
      <p>Conte\u00fado do artigo m\u00ednimo.</p>
    </article>
    <img src="https://s2.glbimg.com/fallback/first.jpg" alt="alguma">
  </main>
</body>
</html>"""


def g1_article_html_title_class_only() -> str:
    """An article that has no <h1>, only the ``.content-head__title`` class."""
    return """<!DOCTYPE html>
<html lang="pt-BR">
<body>
  <main>
    <article>
      <header class="content-head">
        <span class="content-head__title">T\u00edtulo sem h1</span>
      </header>
      <p>Par\u00e1grafo \u00fanico do artigo.</p>
    </article>
  </main>
</body>
</html>"""


# ---------------- Source identity tests ----------------


def test_g1_source_name():
    assert G1Source().name == "g1"


def test_g1_source_language():
    assert G1Source().language == "pt-BR"


def test_g1_source_default_homepage():
    src = G1Source()
    assert src.homepage_url == "https://g1.globo.com"


def test_g1_source_custom_homepage():
    src = G1Source(homepage_url="https://g1.globo.com/politica/")
    assert src.homepage_url == "https://g1.globo.com/politica/"


def test_g1_source_satisfies_protocol():
    assert isinstance(G1Source(), NewsSourceProtocol)


# ---------------- fetch_urls tests ----------------


async def test_g1_fetch_urls_extracts_ghtml_links():
    """Only ``a.feed-post-link`` elements with ``.ghtml`` URLs should be returned."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=g1_homepage_html().encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await G1Source().fetch_urls(client, limit=10)

    assert len(urls) == 5
    for url in urls:
        assert ".ghtml" in url
    assert all(u.startswith("https://g1.globo.com/") for u in urls)


async def test_g1_fetch_urls_deduplicates():
    """Duplicate URLs (same href) should appear only once."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=g1_homepage_html().encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await G1Source().fetch_urls(client, limit=10)

    assert len(urls) == len(set(urls)) == 5


async def test_g1_fetch_urls_respects_limit():
    """The returned list should contain at most ``limit`` entries."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=g1_homepage_html().encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await G1Source().fetch_urls(client, limit=2)

    assert len(urls) == 2


async def test_g1_fetch_urls_returns_absolute_urls_for_relative_hrefs():
    """Relative ``.ghtml`` hrefs should be resolved against the homepage."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=g1_homepage_html().encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await G1Source().fetch_urls(client, limit=10)

    assert "https://g1.globo.com/mundo/noticia/2026/01/01/mundo-1.ghtml" in urls
    assert "https://g1.globo.com/tecnologia/noticia/2026/01/01/tech-1.ghtml" in urls


async def test_g1_fetch_urls_handles_http_error():
    """A 5xx response should result in an empty list, not an exception."""
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await G1Source().fetch_urls(client, limit=5)
    assert urls == []


async def test_g1_fetch_urls_returns_empty_when_no_articles():
    """An empty G1 page should yield an empty URL list."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=g1_homepage_html_no_articles().encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await G1Source().fetch_urls(client, limit=5)
    assert urls == []


async def test_g1_fetch_urls_filters_out_other_subdomains():
    """Only ``g1.globo.com`` URLs should be returned; other Globo sub-domains are filtered."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=g1_homepage_html_with_other_subdomains().encode()
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await G1Source().fetch_urls(client, limit=10)

    assert len(urls) == 2
    assert all("g1.globo.com" in u for u in urls)
    assert "https://ge.globo.com/futebol/copa/jogo/11-06-2026/x.ghtml" not in urls
    assert "https://gshow.globo.com/novelas/2026/x.ghtml" not in urls


# ---------------- scrape_article tests ----------------


async def test_g1_scrape_article_extracts_title_and_content():
    """Title, body and image should all be extracted from a G1 article."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=g1_article_html().encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        article = await G1Source().scrape_article(
            client, "https://g1.globo.com/politica/noticia/2026/01/01/politica-1.ghtml"
        )

    assert article is not None
    assert isinstance(article, ScrapedArticle)
    assert article.source == "g1"
    assert article.url == "https://g1.globo.com/politica/noticia/2026/01/01/politica-1.ghtml"
    assert article.title == "T\u00edtulo do artigo de teste"
    assert "Primeiro par\u00e1grafo" in article.content
    assert "Segundo par\u00e1grafo" in article.content
    assert "Terceiro par\u00e1grafo" in article.content
    assert article.image_url == "https://s2.glbimg.com/abc123/hero.jpg"


async def test_g1_scrape_article_falls_back_to_class_title():
    """When there's no ``<h1>``, the ``.content-head__title`` class should be used."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=g1_article_html_title_class_only().encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        article = await G1Source().scrape_article(
            client, "https://g1.globo.com/economia/noticia/2026/01/01/economia-1.ghtml"
        )

    assert article is not None
    assert article.title == "T\u00edtulo sem h1"
    assert "Par\u00e1grafo \u00fanico" in article.content


async def test_g1_scrape_article_falls_back_to_article_img():
    """When og:image is missing, the first <img> in the article should be used."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=g1_article_html_minimal().encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        article = await G1Source().scrape_article(
            client, "https://g1.globo.com/tecnologia/noticia/2026/01/01/tech-1.ghtml"
        )

    assert article is not None
    assert article.title == "Artigo sem og:image"
    assert article.image_url == "https://s2.glbimg.com/fallback/first.jpg"


async def test_g1_scrape_article_handles_network_error():
    """A 5xx response should return None instead of raising."""
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        article = await G1Source().scrape_article(
            client, "https://g1.globo.com/politica/noticia/2026/01/01/politica-1.ghtml"
        )
    assert article is None


async def test_g1_scrape_article_returns_none_on_404():
    """A 404 response should be treated as a failed scrape."""
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        article = await G1Source().scrape_article(client, "https://g1.globo.com/nao-existe.ghtml")
    assert article is None


# ---------------- Default registry tests ----------------


def test_g1_registered_in_default_registry():
    """G1Source should be registered under the name 'g1' in the default registry."""
    assert "g1" in default_registry
    assert "g1" in default_registry.names()


def test_g1_registry_get_returns_g1source_instance():
    """``default_registry.get('g1')`` should return a G1Source instance."""
    src = default_registry.get("g1")
    assert isinstance(src, G1Source)
    assert src.name == "g1"
    assert src.language == "pt-BR"
