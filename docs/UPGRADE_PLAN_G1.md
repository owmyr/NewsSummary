# Upgrade Plan: G1 News Source + Subscriber Routing

## Objective

Add G1 (g1.globo.com) as a second news source with Portuguese-language summaries, and implement per-subscriber source preferences so Brazilian subscribers receive G1 articles in PT-BR while international subscribers continue receiving BBC in English.

## Scrapability Confirmation

G1 is **fully scrapable with httpx + BeautifulSoup** (no browser needed):

- Homepage is server-side rendered (`IS_FULL_FEED_SSR = true`)
- Article links: `a.feed-post-link[href]` elements with `.ghtml` URLs
- Section labels: `span.feed-post-header-chapeu`
- Article pages: Full text in paragraphs, title in `<h1>`, image in `og:image`
- Example URL pattern: `https://g1.globo.com/economia/noticia/2026/06/11/...ghtml`

## Phase 1: G1 Source Implementation

### 1.1 Create `src/daily_bot/sources/g1.py`

```python
class G1Source(NewsSource):
    DEFAULT_HOMEPAGE = "https://g1.globo.com"
    URL_PATTERN = ".ghtml"

    @property
    def name(self) -> str:
        return "g1"

    @property
    def language(self) -> str:
        return "pt-BR"

    async def fetch_urls(self, client, limit):
        # GET homepage, parse a.feed-post-link[href]
        # Filter for .ghtml URLs, deduplicate, return up to limit

    async def scrape_article(self, client, url):
        # GET article page, parse:
        #   title: h1 or .content-head__title
        #   body: article paragraphs (fallback: main p)
        #   image: og:image meta tag
        #   section: from chapeu or editorial metadata
        # Set article.source = "g1"
```

### 1.2 Register G1

In `src/daily_bot/sources/__init__.py`:

```python
from .g1 import G1Source
default_registry.register("g1", G1Source)
```

### 1.3 Add `language` to NewsSource ABC

In `src/daily_bot/sources/base.py`:

```python
class NewsSource(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def language(self) -> str:  # Default, not abstract
        return "en"

    @abstractmethod
    async def fetch_urls(self, client, limit) -> list[str]: ...

    @abstractmethod
    async def scrape_article(self, client, url) -> ScrapedArticle | None: ...
```

### 1.4 Add G1 config

In `src/daily_bot/config.py`:

```python
g1_homepage_url: str = Field(
    default="https://g1.globo.com",
    description="G1 homepage URL",
)
```

### 1.5 Update `.env.example`

```
SOURCES=bbc,g1
# G1_HOMEPAGE_URL=https://g1.globo.com
```

### 1.6 G1 Category Mapping

Portuguese section labels to English categories:

```python
G1_CATEGORY_MAP = {
    "economia": "business",
    "politica": "politics",
    "mundo": "world",
    "saude": "health",
    "ciencia": "science",
    "tecnologia": "tech",
    "meio ambiente": "science",
    "educacao": "other",
    "carros": "other",
}
```

The `chapeu` section label is extracted during scraping and stored as metadata on the article. The summarizer can use this as a hint but still validates against `VALID_CATEGORIES`.

### 1.7 Tests for G1Source

- `tests/unit/test_sources.py`: Add `test_g1_source_name`, `test_g1_source_language`, `test_g1_registration`
- Create `tests/unit/test_g1_scraper.py` with mocked G1 HTML fixtures:
  - Homepage HTML with `feed-post-link` elements
  - Article HTML with Portuguese content
  - Test URL extraction, article scraping, section mapping
  - Test `language = "pt-BR"`

---

## Phase 2: Language-Aware Summarization

### 2.1 Add `language` param to `summarize_article()`

In `src/daily_bot/summarizer.py`:

```python
async def summarize_article(
    client: AsyncGeminiClient,
    article_text: str,
    title: str,
    settings: Settings,
    language: str = "en",
) -> Summary:
```

When `language == "pt-BR"`, prepend instruction to the Gemini prompt:

> "Resuma este artigo em portugues do Brasil. A saida deve estar em portugues."

Also include the category mapping hint when `language == "pt-BR"`.

### 2.2 Pass language through orchestrator

In `__main__.py`, `_process_one()`:

```python
summary = await summarize_article(
    gemini, article.content, article.title, settings,
    language=source.language,
)
```

### 2.3 Tests

- Update `test_summarizer.py` with parametrized language tests
- Add test that PT-BR prompt instruction is included
- Add test that English articles still get English summaries

---

## Phase 3: Subscriber Source Preferences

### 3.1 Update `Subscriber` model

In `src/daily_bot/models.py`:

```python
class Subscriber(BaseModel):
    email: str
    sources: list[str] = Field(default_factory=lambda: ["bbc"])
    subscribed_at: datetime | None = None
```

### 3.2 New `db.get_all_subscribers()` method

In `src/daily_bot/db.py`:

```python
def get_all_subscribers() -> list[Subscriber]:
    """Return all subscribers with their source preferences."""
    db = get_db()
    docs = db.collection("subscribers").stream()
    subscribers = []
    for doc in docs:
        data = doc.to_dict() or {}
        email = data.get("email")
        if not email:
            continue
        sources = data.get("sources", ["bbc"])
        subscribers.append(Subscriber(
            email=str(email),
            sources=sources,
            subscribed_at=data.get("subscribed_at"),
        ))
    return subscribers
```

Keep `get_all_subscriber_emails()` as backward-compatible (used in tests).

### 3.3 Routing logic in `__main__.py`

Replace current broadcast-all approach with preference-based routing:

```python
subscribers = db.get_all_subscribers()

# Group summaries by source
summaries_by_source: dict[str, list[Summary]] = defaultdict(list)
for s in summaries:
    summaries_by_source[s.source].append(s)

# Group subscribers by their source preferences
preference_groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
for sub in subscribers:
    key = tuple(sorted(sub.sources))
    preference_groups[key].append(sub.email)

sent, failed = 0, 0
for sources_key, emails in preference_groups.items():
    relevant = []
    for src in sources_key:
        relevant.extend(summaries_by_source.get(src, []))

    if not relevant:
        continue

    html = render_email_html(relevant)

    group_sent, group_failed = await send_daily_digest_async(
        settings, relevant, emails, ...
    )
    sent += group_sent
    failed += group_failed
```

### 3.4 Tests

- Unit test for `Subscriber` with new `sources` field
- Unit test for grouping logic
- Integration test: subscribers with `["bbc"]` only get BBC summaries, `["g1"]` only get G1, `["bbc", "g1"]` get both

---

## Phase 4: Email Template Updates

### 4.1 Source badge per article

In `src/daily_bot/templates/email.html.j2`, update article cards:

```html
{% if article.source == "bbc" %}
<span class="source-badge bbc">BBC News</span>
{% elif article.source == "g1" %}
<span class="source-badge g1">G1</span>
{% endif %}
```

With CSS:

```css
.source-badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 12px;
    text-transform: uppercase;
}
.source-badge.bbc { background: #bb1919; color: white; }
.source-badge.g1 { background: #c4170c; color: white; }
```

### 4.2 Section headers when multiple sources

Add source-grouped sections in the template (only show headers when >1 source is present):

```html
{% set sources_present = articles | map(attribute='source') | unique | list %}
{% if sources_present | length > 1 %}
  {% for source in sources_present %}
    <h2>{{ 'BBC News' if source == 'bbc' else 'G1' }}</h2>
    {% for article in articles if article.source == source %}
      ...card...
    {% endfor %}
  {% endfor %}
{% else %}
  ...single list of cards...
{% endif %}
```

### 4.3 Tests

- Visual test: render template with mixed BBC+G1 summaries
- Verify source badges appear correctly
- Verify section headers only appear when >1 source

---

## Phase 5: Frontend — Source Selection UI

### 5.1 Update `public/index.html` subscribe form

Add checkboxes below the email input:

```html
<div class="flex gap-4 mt-3 justify-center">
    <label class="flex items-center gap-2 text-sm">
        <input type="checkbox" name="sources" value="bbc" checked
               class="accent-brand-sage">
        <span>BBC News (English)</span>
    </label>
    <label class="flex items-center gap-2 text-sm">
        <input type="checkbox" name="sources" value="g1"
               class="accent-brand-sage">
        <span>G1 (Portugues)</span>
    </label>
</div>
```

Update the form handler to send sources:

```javascript
const checkboxes = document.querySelectorAll('input[name="sources"]:checked');
const sources = Array.from(checkboxes).map(cb => cb.value);
// Add to fetch body: sources: sources
```

### 5.2 Update `functions/index.js` — `addSubscriber`

Accept `sources` array:

```javascript
const sources = data.sources || ['bbc'];
// Validate: must be non-empty array of known sources
if (!sources.every(s => ['bbc', 'g1'].includes(s))) {
    throw new Error('Invalid source');
}
// Save to Firestore subscriber doc: { email, sources, subscribed_at }
```

### 5.3 Update `functions/index.js` — `latestNews`

Include `source` field in API response:

```javascript
articles.push({
    title: escapeHtml(a.title),
    summary: escapeHtml(a.summary),
    category: a.category || 'other',
    url: a.url || '',
    image_url: a.image_url || '',
    source: a.source || 'bbc',
});
```

### 5.4 Update `public/index.html` — News preview

Show source badge in card:

```javascript
const sourceBadge = art.source === 'g1'
    ? '<span class="inline-block bg-red-600 text-white text-xs px-2 py-0.5 rounded-full font-bold">G1</span>'
    : '<span class="inline-block bg-red-700 text-white text-xs px-2 py-0.5 rounded-full font-bold">BBC</span>';
```

---

## Phase 6: Testing & QA

### 6.1 New unit tests

| File | Tests |
|---|---|
| `test_g1_scraper.py` | URL extraction from G1 homepage, article scraping, section mapping, language property, `.ghtml` filtering |
| `test_sources.py` | G1Source registration, SourceRegistry with G1, default_registry includes g1 |
| `test_subscriber_routing.py` | Subscriber model with sources, grouping logic, mixed source preferences |

### 6.2 Updated existing tests

- `test_summarizer.py`: Parametrize language (en/pt-BR), verify prompt differs
- `test_pipeline.py`: Verify `source.language` is passed to `summarize_article`
- `test_emailer.py`: Test template with source badges, multi-source rendering
- `test_multi_source.py`: Add test for G1+BBC routing to subscribers

### 6.3 Full test suite

```bash
pytest --cov=daily_bot --cov-report=term-missing  # Target: 80%+ coverage
ruff check src/daily_bot/ tests/                  # 0 issues
ruff format --check src/daily_bot/ tests/          # All formatted
```

---

## Phase 7: Deployment

### 7.1 Update README.md

- Add G1 to supported sources
- Document `SOURCES` env var
- Document `G1_HOMEPAGE_URL` config
- Add subscriber source preference screenshot

### 7.2 Update `.env.example`

Add all new env vars with comments.

### 7.3 Deploy steps

1. Deploy Cloud Functions: `firebase deploy --only functions`
2. Deploy Hosting: `firebase deploy --only hosting`
3. Update GitHub Actions secrets if needed
4. Test manually: `SOURCES=bbc,g1 python -m daily_bot`
5. Monitor health check and `email_log`

---

## Execution Order

| Step | Phase | Depends On | Effort |
|------|--------|-----------|--------|
| 1 | Phase 1.1-1.6 | None | 2-3h |
| 2 | Phase 1.7 | Step 1 | 1h |
| 3 | Phase 2.1-2.3 | Step 1 | 1h |
| 4 | Phase 3.1-3.4 | Step 3 | 1.5h |
| 5 | Phase 4.1-4.3 | Step 4 | 1h |
| 6 | Phase 5.1-5.4 | Step 4 | 1.5h |
| 7 | Phase 6 | Steps 1-6 | 1.5h |
| 8 | Phase 7 | Steps 1-7 | 30min |
| **Total** | | | **~10h** |

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| G1 HTML structure changes | CSS selectors are stable for a major news site; add defensive fallbacks |
| G1 rate limiting | httpx client with `follow_redirects=True`, `http_timeout_seconds=15`, circuit breaker handles failures |
| Gemini PT-BR quality | Test prompts with sample G1 articles; adjust prompts iteratively |
| Firestore migration — new `sources` field | Default `["bbc"]` means existing subscribers are unaffected |
| Email template regression | Visual test with single-source (BBC) still renders correctly |