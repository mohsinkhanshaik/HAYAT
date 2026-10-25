"""Local digital-product catalog, delivery bundles, and actual-sales ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from app.tools.registry import ToolRegistry



#: Two-decimal currencies, shared by the books, products and customer quotes. Rupees were missing until 2026-10-13:
#: an owner in India could not invoice in INR, and UPI pays in rupees only.
CURRENCIES = frozenset({"USD", "EUR", "GBP", "CAD", "AUD", "NZD", "INR", "AED", "SAR", "PKR", "BDT", "SGD", "MYR", "CHF", "ZAR",
                        "HKD", "SEK", "NOK", "DKK", "PLN", "BRL", "MXN", "QAR"})

def cents(value: str) -> int:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal('100000000'):
            raise ValueError('Amount must be finite and between 0 and 100000000.')
        return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError('Invalid monetary amount.') from exc


class ProductStore:
    ALLOWED_FILES = {'.md', '.txt', '.pdf', '.docx', '.xlsx', '.csv', '.pptx', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.mp4', '.mp3', '.wav', '.html', '.css', '.json'}

    @staticmethod
    def _digest_file(path, limit=252 * 1024 * 1024):
        digest, total = hashlib.sha256(), 0
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                total += len(block)
                if total > limit:
                    raise ValueError('File exceeds the verification size limit.')
                digest.update(block)
        return digest.hexdigest()
    def __init__(self, path: Path, output: Path, permissions):
        self.path, self.output, self.permissions = Path(path), Path(output), permissions

    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, audience TEXT NOT NULL,
                description TEXT NOT NULL, currency TEXT NOT NULL, price_cents INTEGER NOT NULL,
                license_text TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sales (
                reference TEXT PRIMARY KEY, product_id TEXT NOT NULL,
                gross_cents INTEGER NOT NULL, fee_cents INTEGER NOT NULL,
                refund_cents INTEGER NOT NULL, recorded TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS product_assets (
                product_id TEXT NOT NULL, position INTEGER NOT NULL, path TEXT NOT NULL,
                PRIMARY KEY (product_id, position));
            CREATE TABLE IF NOT EXISTS product_bundles (
                path TEXT PRIMARY KEY, product_id TEXT NOT NULL, sha256 TEXT NOT NULL,
                manifest TEXT NOT NULL, created TEXT NOT NULL);
        ''')
        return db

    def create(self, title: str, audience: str, description: str, price: str,
               license_text: str, currency: str = 'USD') -> dict:
        currency = currency.upper()
        # Fixed two-decimal accounting; do not silently misprice JPY/KWD/etc.
        if currency not in CURRENCIES:
            raise ValueError('Supported two-decimal currencies: ' + ', '.join(sorted(CURRENCIES)) + '.')
        if any(not value.strip() for value in (title, audience, description, license_text)):
            raise ValueError('Title, audience, description, and license terms are required.')
        product = dict(id=uuid4().hex, title=title, audience=audience,
                       description=description, currency=currency, price_cents=cents(price),
                       license_text=license_text, created=datetime.now(UTC).isoformat())
        db = self._db()
        try:
            with db:
                db.execute('INSERT INTO products VALUES (:id,:title,:audience,:description,:currency,:price_cents,:license_text,:created)', product)
        finally:
            db.close()
        return product

    def catalog(self) -> dict:
        db = self._db()
        try:
            return {'products': [dict(row) for row in db.execute('SELECT * FROM products ORDER BY created')],
                    'status': 'local drafts; publication and payments require a connected store'}
        finally:
            db.close()

    def _product(self, product_id: str) -> dict:
        db = self._db()
        try:
            row = db.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown product ID.')
            return dict(row)
        finally:
            db.close()

    def package(self, product_id: str, files: list[str]) -> dict:
        product = self._product(product_id)
        sources = self._sources(files)
        destination = self.permissions.validate_project_path(self.output / product_id / (uuid4().hex + '.zip'), write=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        manifest = {'product': product, 'files': [], 'published': False}
        try:
            with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sources:
                    remaining = 250 * 1024 * 1024 - sum(item['bytes'] for item in manifest['files'])
                    with path.open('rb') as handle:
                        data = handle.read(remaining + 1)
                    if len(data) > 250 * 1024 * 1024 or sum(item['bytes'] for item in manifest['files']) + len(data) > 250 * 1024 * 1024:
                        raise ValueError('Bundle grew beyond the size limit.')
                    archive.writestr('deliverables/' + path.name, data)
                    manifest['files'].append({'name': path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
                archive.writestr('LICENSE.txt', product['license_text'])
                archive.writestr('LISTING.md', f"# {product['title']}\n\n{product['description']}\n\nFor: {product['audience']}\n\nPrice: {Decimal(product['price_cents']) / 100:.2f} {product['currency']}\n")
                archive.writestr('manifest.json', json.dumps(manifest, indent=2))
            with zipfile.ZipFile(destination) as archive:
                if archive.testzip() is not None:
                    raise ValueError('Archive integrity verification failed.')
            digest = self._digest_file(destination)
            db = self._db()
            try:
                with db:
                    self._save_assets(db, product_id, sources)
                    db.execute('INSERT INTO product_bundles VALUES (?,?,?,?,?)',
                               (str(destination), product_id, digest, json.dumps(manifest), datetime.now(UTC).isoformat()))
            finally:
                db.close()
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return {'path': str(destination), 'files': manifest['files'], 'verified': True, 'published': False,
                'sha256': digest}

    def _sources(self, files):
        if not 1 <= len(files) <= 100:
            raise ValueError('Choose 1 to 100 deliverable files.')
        names, sources, total = set(), [], 0
        for name in files:
            path = self.permissions.validate_project_path(name)
            if path.suffix.lower() not in self.ALLOWED_FILES or not path.is_file():
                raise ValueError('Each deliverable must be an existing supported document or media file.')
            if path.name.casefold() in names:
                raise ValueError('Deliverables must have unique filenames.')
            names.add(path.name.casefold())
            total += path.stat().st_size
            if total > 250 * 1024 * 1024:
                raise ValueError('Bundle exceeds the 250 MiB limit.')
            sources.append(path)
        return sources

    @staticmethod
    def metadata_sha256(product):
        return hashlib.sha256(json.dumps(product, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _save_assets(db, product_id, paths):
        db.execute('DELETE FROM product_assets WHERE product_id=?', (product_id,))
        db.executemany('INSERT INTO product_assets VALUES (?,?,?)',
                       [(product_id, index, str(path)) for index, path in enumerate(paths)])

    def set_files(self, product_id: str, files: list[str]) -> dict:
        self._product(product_id)
        paths = self._sources(files) if files else []
        db = self._db()
        try:
            with db:
                self._save_assets(db, product_id, paths)
        finally:
            db.close()
        return self.details(product_id)

    def details(self, product_id: str) -> dict:
        product = self._product(product_id)
        db = self._db()
        try:
            paths = [row['path'] for row in db.execute('SELECT path FROM product_assets WHERE product_id=? ORDER BY position', (product_id,))]
            row = db.execute('SELECT path,sha256,created FROM product_bundles WHERE product_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (product_id,)).fetchone()
        finally:
            db.close()
        assets = []
        for name in paths:
            path = self.permissions.validate_project_path(name)
            exists = path.is_file()
            assets.append({'path': str(path), 'name': path.name, 'exists': exists,
                           'bytes': path.stat().st_size if exists else 0})
        return {'product': product, 'metadata_sha256': self.metadata_sha256(product),
                'assets': assets, 'bundle': dict(row) if row else None, 'published': False}

    def update(self, product_id: str, title: str, audience: str, description: str, price: str,
               license_text: str, expected_sha256: str, currency: str = 'USD') -> dict:
        currency = currency.upper()
        if currency not in CURRENCIES:
            raise ValueError('Unsupported currency.')
        if any(not value.strip() for value in (title, audience, description, license_text)):
            raise ValueError('Title, audience, description, and license terms are required.')
        amount = cents(price)
        db = self._db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
                if row is None:
                    raise ValueError('Unknown product ID.')
                product = dict(row)
                if self.metadata_sha256(product) != expected_sha256:
                    raise ValueError('Product changed since it was read. Refresh before saving.')
                if product['currency'] != currency and db.execute('SELECT 1 FROM sales WHERE product_id=? LIMIT 1', (product_id,)).fetchone():
                    raise ValueError('Currency cannot change after sales are recorded. Create a separate product.')
                db.execute('UPDATE products SET title=?,audience=?,description=?,price_cents=?,license_text=?,currency=? WHERE id=?',
                           (title, audience, description, amount, license_text, currency, product_id))
        finally:
            db.close()
        return self.details(product_id)

    def verify_bundle(self, product_id: str) -> dict:
        details = self.details(product_id)
        bundle = details['bundle']
        if not bundle:
            raise ValueError('Build a delivery ZIP first.')
        path = self.permissions.validate_project_path(bundle['path'])
        errors, changed = [], []
        listing_changed = False
        try:
            if path.stat().st_size > 252 * 1024 * 1024:
                raise ValueError('Archive exceeds the verification size limit.')
            if self._digest_file(path) != bundle['sha256']:
                errors.append('ZIP changed since it was created.')
            with zipfile.ZipFile(path) as archive:
                if sum(info.file_size for info in archive.infolist()) > 252 * 1024 * 1024:
                    raise ValueError('Unpacked archive exceeds the verification size limit.')
                manifest = json.loads(archive.read('manifest.json'))
                expected = {'manifest.json', 'LICENSE.txt', 'LISTING.md'}
                for item in manifest['files']:
                    name = 'deliverables/' + item['name']
                    expected.add(name)
                    data = archive.read(name)
                    if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                        errors.append('Deliverable checksum failed: ' + item['name'])
                if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
                    errors.append('Archive contains unexpected or duplicate files.')
                if archive.read('LICENSE.txt').decode() != manifest['product']['license_text']:
                    errors.append('License differs from the product snapshot.')
                listing_changed = manifest['product'] != details['product']
                snapshots = {item['name']: item['sha256'] for item in manifest['files']}
                for asset in details['assets']:
                    source = Path(asset['path'])
                    if (not asset['exists'] or asset['bytes'] > 250 * 1024 * 1024
                            or self._digest_file(source, 250 * 1024 * 1024) != snapshots.get(asset['name'])):
                        changed.append(asset['name'])
                if set(snapshots) != {asset['name'] for asset in details['assets']}:
                    changed.append('Selected deliverable list')
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
            errors.append(f'Verification failed: {type(exc).__name__}: {exc}')
        return {'path': str(path), 'verified': not errors, 'errors': errors,
                'sources_changed': changed, 'listing_changed': listing_changed,
                'needs_rebuild': bool(changed or listing_changed or errors), 'published': False,
                'basis': 'File integrity and snapshot checks only; review content and license before sale.'}

    def record_sale(self, product_id: str, reference: str, gross: str, fees: str = '0', refunds: str = '0') -> dict:
        self._product(product_id)
        if not reference.strip():
            raise ValueError('A unique store transaction reference is required.')
        values = (reference, product_id, cents(gross), cents(fees), cents(refunds))
        if values[4] > values[2]:
            raise ValueError('Refunds cannot exceed gross revenue.')
        db = self._db()
        try:
            with db:
                existing = db.execute('SELECT * FROM sales WHERE reference=?', (reference,)).fetchone()
                if existing:
                    if tuple(existing)[:5] != values:
                        raise ValueError('Transaction reference already exists with different amounts; no changes made.')
                    return {'recorded': False, 'duplicate': True, 'reference': reference}
                db.execute('INSERT INTO sales VALUES (?,?,?,?,?,?)', (*values, datetime.now(UTC).isoformat()))
        finally:
            db.close()
        return {'recorded': True, 'reference': reference, 'source': 'user-reported; payment not independently verified'}

    def report(self) -> dict:
        db = self._db()
        try:
            rows = db.execute('''SELECT p.currency, COUNT(*) AS transactions,
                SUM(s.gross_cents) AS gross_cents, SUM(s.fee_cents) AS fee_cents,
                SUM(s.refund_cents) AS refund_cents,
                SUM(s.gross_cents-s.fee_cents-s.refund_cents) AS net_receipts_cents
                FROM sales s JOIN products p ON p.id=s.product_id GROUP BY p.currency''')
            return {'by_currency': [dict(row) for row in rows],
                    'basis': 'User-reported transactions. Net receipts exclude production costs and taxes; not profit or forecasts.'}
        finally:
            db.close()

    def sales_list(self, limit: int = 200) -> list[dict]:
        """Recent sales with their net receipts, newest first, for the wallet's activity."""
        db = self._db()
        try:
            rows = db.execute('''SELECT s.recorded AS date, p.title, p.currency, s.reference,
                s.gross_cents-s.fee_cents-s.refund_cents AS net_cents
                FROM sales s JOIN products p ON p.id=s.product_id ORDER BY s.recorded DESC LIMIT ?''', (limit,))
            return [dict(row) for row in rows]
        finally:
            db.close()

    def net_by_product(self, start: str, end: str) -> list[dict]:
        """Net receipts and sale counts per product for sales recorded in [start, end)."""
        db = self._db()
        try:
            rows = db.execute('''SELECT p.title, p.currency, COUNT(*) AS entries,
                SUM(s.gross_cents-s.fee_cents-s.refund_cents) AS cents
                FROM sales s JOIN products p ON p.id=s.product_id
                WHERE s.recorded>=? AND s.recorded<? GROUP BY p.id''', (start, end))
            return [dict(row) for row in rows]
        finally:
            db.close()

    def net_by_currency(self, start: str, end: str) -> dict[str, int]:
        """Net receipts in cents per currency for sales recorded in [start, end)."""
        db = self._db()
        try:
            rows = db.execute('''SELECT p.currency, SUM(s.gross_cents-s.fee_cents-s.refund_cents) AS net
                FROM sales s JOIN products p ON p.id=s.product_id
                WHERE s.recorded>=? AND s.recorded<? GROUP BY p.currency''', (start, end))
            return {row['currency']: int(row['net'] or 0) for row in rows}
        finally:
            db.close()


def register_product_tools(registry: ToolRegistry, store: ProductStore) -> None:
    def schema(properties, required=()):
        return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}

    text = {'type': 'string', 'minLength': 1, 'maxLength': 20000}
    registry.register('product_create', 'Create a local digital-product draft with audience, price, and explicit license terms. Does not publish.',
                      schema({key: text for key in ('title', 'audience', 'description', 'price', 'license_text', 'currency')},
                             ('title', 'audience', 'description', 'price', 'license_text')),
                      'low', 'filesystem_write_project', store.create)
    registry.register('product_catalog', 'List local digital products and their IDs, prices, and license terms.',
                      schema({}), 'low', 'filesystem_read', store.catalog)
    registry.register('product_package', 'Build and verify a ZIP delivery bundle of chosen files, with listing, license, manifest, and SHA256 hashes. Review contents before upload.',
                      schema({'product_id': text, 'files': {'type': 'array', 'items': text, 'minItems': 1, 'maxItems': 100}}, ('product_id', 'files')),
                      'low', 'filesystem_write_project', store.package)
    registry.register('product_record_sale', 'Record an actual user-reported sale using a unique transaction reference, gross, fees, and refunds. No payment is collected.',
                      schema({key: text for key in ('product_id', 'reference', 'gross', 'fees', 'refunds')}, ('product_id', 'reference', 'gross')),
                      'low', 'filesystem_write_project', store.record_sale)
    registry.register('product_sales_report', 'Report recorded gross, refunds, fees, and net receipts grouped by currency; excludes taxes and production costs.',
                      schema({}), 'low', 'filesystem_read', store.report)
