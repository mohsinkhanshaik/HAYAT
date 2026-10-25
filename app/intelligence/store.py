"""Bounded durable snapshots, watch regions and deduplicated local alerts."""
import json
import sqlite3
import threading
from pathlib import Path


class IntelligenceStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        # Bumped on every write so displays can skip unchanged redraws.
        self.revision = 0
        self.run(lambda db: db.executescript('''
            CREATE TABLE IF NOT EXISTS feeds (key TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, feed TEXT NOT NULL, captured TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS watches (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, created TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feed_health (id INTEGER PRIMARY KEY, feed TEXT NOT NULL, at TEXT NOT NULL,
                ok INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS feed_health_feed ON feed_health(feed, id);
            CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, place TEXT NOT NULL, at TEXT NOT NULL,
                data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS observations_place ON observations(place, id);
            CREATE TABLE IF NOT EXISTS intel_objects (id TEXT PRIMARY KEY, first_reported TEXT, last_reported TEXT,
                category TEXT, latitude REAL, longitude REAL, status TEXT, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS intel_objects_time ON intel_objects(last_reported);
        '''))

    def run(self, operation):
        with self.lock, sqlite3.connect(self.path, timeout=15) as db:
            db.row_factory = sqlite3.Row
            return operation(db)

    def write(self, operation):
        with self.lock:
            value = self.run(operation)
            self.revision += 1
            return value

    def latest_ids(self):
        return self.run(lambda db: {row[0]: row[1] for row in db.execute('SELECT feed, MAX(id) FROM snapshots GROUP BY feed')})

    def states(self):
        return self.run(lambda db: {row['key']: json.loads(row['state']) for row in db.execute('SELECT * FROM feeds')})

    def preference(self, key, default=None):
        row = self.run(lambda db: db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone())
        return json.loads(row['value']) if row else default

    def save_preference(self, key, value):
        self.write(lambda db: db.execute('INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                      (key, json.dumps(value, allow_nan=False))))

    def trim_preferences(self, prefix, limit):
        self.run(lambda db: db.execute('DELETE FROM settings WHERE key LIKE ? AND key NOT IN (SELECT key FROM settings WHERE key LIKE ? ORDER BY rowid DESC LIMIT ?)',
                                      (prefix + '%', prefix + '%', limit)))

    def event_versions(self, feed, identifier):
        snapshots = self.run(lambda db: list(db.execute('SELECT captured,data FROM snapshots WHERE feed=? ORDER BY id DESC LIMIT 12', (feed,))))
        return [{'captured_at': row['captured'], 'event': event} for row in snapshots
                if (event := json.loads(row['data']).get(identifier)) is not None]

    def seed_watches(self, rules):
        def commit(db):
            if db.execute("SELECT 1 FROM settings WHERE key='watch_defaults_initialized'").fetchone():
                return
            for key, rule in rules.items():
                db.execute('INSERT OR IGNORE INTO watches VALUES (?, ?)', (key, json.dumps(rule, allow_nan=False)))
            db.execute("INSERT INTO settings VALUES ('watch_defaults_initialized', 'true')")
        self.run(commit)

    def state(self, key, value):
        self.write(lambda db: db.execute('INSERT INTO feeds VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET state=excluded.state',
                                      (key, json.dumps(value, allow_nan=False))))

    def latest(self, key):
        row = self.run(lambda db: db.execute('SELECT * FROM snapshots WHERE feed=? ORDER BY id DESC LIMIT 1', (key,)).fetchone())
        return {'id': row['id'], 'captured_at': row['captured'], 'events': json.loads(row['data'])} if row else None

    def save(self, key, captured, events):
        def commit(db):
            db.execute('INSERT INTO snapshots(feed,captured,data) VALUES (?, ?, ?)', (key, captured, json.dumps(events, allow_nan=False)))
            db.execute('DELETE FROM snapshots WHERE feed=? AND id NOT IN (SELECT id FROM snapshots WHERE feed=? ORDER BY id DESC LIMIT 12)', (key, key))
        self.write(commit)

    def history(self, key):
        return self.run(lambda db: [{'id': row['id'], 'captured_at': row['captured'], 'count': len(json.loads(row['data']))}
                                   for row in db.execute('SELECT * FROM snapshots WHERE feed=? ORDER BY id DESC LIMIT 12', (key,))])

    def record_health(self, feed, ok, count=0, error='', at=None):
        """One refresh outcome; the last 200 per feed give each source a measured reliability."""
        from datetime import UTC, datetime
        stamp = at or datetime.now(UTC).isoformat(timespec='seconds')

        def commit(db):
            db.execute('INSERT INTO feed_health (feed, at, ok, count, error) VALUES (?,?,?,?,?)',
                       (feed, stamp, int(bool(ok)), int(count), str(error or '')[:300]))
            db.execute('DELETE FROM feed_health WHERE feed=? AND id NOT IN (SELECT id FROM feed_health WHERE feed=? '
                       'ORDER BY id DESC LIMIT 200)', (feed, feed))
        self.run(commit)

    def health_summary(self, last=50):
        rows = self.run(lambda db: list(db.execute('SELECT feed, ok, count, at FROM feed_health ORDER BY id DESC')))
        summary = {}
        for row in rows:
            entry = summary.setdefault(row['feed'], {'attempts': 0, 'successes': 0, 'last_at': row['at'], 'counts': []})
            if entry['attempts'] >= last:
                continue
            entry['attempts'] += 1
            entry['successes'] += row['ok']
            if row['ok']:
                entry['counts'].append(row['count'])
        return summary

    def save_observation(self, place, at, data):
        def commit(db):
            db.execute('INSERT INTO observations (place, at, data) VALUES (?,?,?)', (place, at, json.dumps(data, allow_nan=False)))
            db.execute('DELETE FROM observations WHERE place=? AND id NOT IN (SELECT id FROM observations WHERE place=? '
                       'ORDER BY id DESC LIMIT 500)', (place, place))
        self.run(commit)

    def observations(self, place, limit=500):
        return self.run(lambda db: [{'at': row['at'], **json.loads(row['data'])} for row in db.execute(
            'SELECT at, data FROM observations WHERE place=? ORDER BY id DESC LIMIT ?', (place, limit))])

    def save_objects(self, objects):
        """Keep every merged event, so later questions can reach back past the feeds' own windows."""
        def commit(db):
            db.executemany('DELETE FROM intel_objects WHERE id=?',
                           [(key,) for item in objects for key in item.get('supersedes', [])])
            db.executemany('INSERT INTO intel_objects VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                           'last_reported=excluded.last_reported, category=excluded.category, latitude=excluded.latitude, '
                           'longitude=excluded.longitude, status=excluded.status, data=excluded.data',
                           [(item['id'], item.get('first_reported'), item.get('last_reported'), item['category'],
                             item['latitude'], item['longitude'], item['status'],
                             json.dumps({key: value for key, value in item.items() if key != 'sources'} |
                                        {'sources': item['sources'][:20]}, allow_nan=False)) for item in objects])
            db.execute('DELETE FROM intel_objects WHERE id NOT IN (SELECT id FROM intel_objects ORDER BY last_reported DESC LIMIT 20000)')
        self.run(commit)

    def objects_near(self, latitude, longitude, radius_km, since=None):
        import math
        degrees = radius_km / 111.0
        span = degrees / max(0.05, math.cos(math.radians(latitude)))
        rows = self.run(lambda db: list(db.execute(
            'SELECT data FROM intel_objects WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? AND (? IS NULL OR last_reported>=?) '
            'ORDER BY last_reported DESC LIMIT 500',
            (latitude - degrees, latitude + degrees, longitude - span, longitude + span, since, since))))
        return [json.loads(row['data']) for row in rows]

    def objects_since(self, since, limit=2000):
        return self.run(lambda db: [json.loads(row['data']) for row in db.execute(
            'SELECT data FROM intel_objects WHERE last_reported>=? ORDER BY last_reported DESC LIMIT ?', (since, limit))])

    def watches(self):
        return self.run(lambda db: [{'key': row['key'], **json.loads(row['data'])} for row in db.execute('SELECT * FROM watches ORDER BY key')])

    def save_watch(self, key, value):
        def commit(db):
            if db.execute('SELECT COUNT(*) FROM watches').fetchone()[0] >= 100 and not db.execute('SELECT 1 FROM watches WHERE key=?', (key,)).fetchone():
                raise ValueError('At most 100 watch rules can be stored.')
            db.execute('INSERT INTO watches VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET data=excluded.data', (key, json.dumps(value, allow_nan=False)))
        self.write(commit)

    def delete_watch(self, key):
        return self.write(lambda db: db.execute('DELETE FROM watches WHERE key=?', (key,)).rowcount)

    def add_alert(self, identifier, created, value):
        def commit(db):
            inserted = db.execute('INSERT OR IGNORE INTO alerts VALUES (?, ?, ?)', (identifier, created, json.dumps(value, allow_nan=False))).rowcount
            db.execute('DELETE FROM alerts WHERE id NOT IN (SELECT id FROM alerts ORDER BY created DESC, rowid DESC LIMIT 1000)')
            return bool(inserted)
        return self.write(commit)

    def alerts(self, limit=100):
        return self.run(lambda db: [{'id': row['id'], 'created_at': row['created'], **json.loads(row['data'])}
                                   for row in db.execute('SELECT * FROM alerts ORDER BY created DESC, rowid DESC LIMIT ?', (limit,))])
