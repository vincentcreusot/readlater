-- All times are INTEGER Unix seconds (UTC).
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY,
  url TEXT,                          -- NULL for pasted text
  canonical_url TEXT UNIQUE,         -- NULL for pasted text
  content_hash TEXT,
  title TEXT, site TEXT, author TEXT, published_at TEXT,
  text TEXT, word_count INTEGER, lang TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS articles_hash ON articles(content_hash);

CREATE TABLE IF NOT EXISTS saves (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,              -- telegram | email | cli
  source_id TEXT NOT NULL,           -- '<chat>:<msg>:<hash16>' or '<Message-ID>:<hash16>'
  saved_at INTEGER NOT NULL,
  payload TEXT NOT NULL,             -- the URL or pasted text
  article_id INTEGER REFERENCES articles(id),
  reply_chat INTEGER, reply_to INTEGER, replied_at INTEGER,   -- Telegram reply bookkeeping
  UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS emails (
  message_id TEXT PRIMARY KEY,
  date INTEGER,
  from_addr TEXT
);

CREATE TABLE IF NOT EXISTS summaries (
  article_id INTEGER PRIMARY KEY REFERENCES articles(id),
  model TEXT NOT NULL,
  title TEXT, tldr TEXT,
  key_points TEXT, tags TEXT,        -- JSON arrays
  content_type TEXT, language TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  article_id INTEGER NOT NULL REFERENCES articles(id),
  stage TEXT NOT NULL,               -- extract | summarize
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | running | done | failed (retries used up) | rejected (retrying can't help)
  priority INTEGER NOT NULL,         -- lower runs first: telegram 0, email 10
  attempts INTEGER NOT NULL DEFAULT 0,
  next_try_at INTEGER NOT NULL,
  last_error TEXT,
  UNIQUE (article_id, stage)
);

-- earlier dedupe keys of an article (a short link before its redirect, a merged duplicate's URL)
CREATE TABLE IF NOT EXISTS url_aliases (
  canonical_url TEXT PRIMARY KEY,
  article_id INTEGER NOT NULL REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS sync_state (source TEXT PRIMARY KEY, cursor TEXT);
