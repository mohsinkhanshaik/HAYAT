"""Generate a local example product to verify HAYAT's native output tools."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.tools.filesystem import FilesystemTools
    from app.tools.file_editing import FileEditing
    from app.tools.products import ProductStore
    from app.safety.permissions import PermissionManager
    from app.config import load_settings
    from app.tools.business import BusinessLedger

    permissions = PermissionManager()
    database = Path(load_settings()['memory_database'])
    if not database.is_absolute():
        database = ROOT / database
    profile = BusinessLedger(database.parent / 'business.db', ROOT / 'data/artifacts/business', permissions).profile()
    filesystem = FilesystemTools(permissions)
    editor = FileEditing(filesystem)
    folder = 'data/artifacts/freelancer-planning-kit'
    guide = '''# Freelancer weekly planning kit

## Set up your week

List this week's client deliverables in the workbook. Give each task an owner, due date, and estimated hours. Add your available working hours before accepting more work.

## Plan your content

Choose one useful problem your audience has. Write one draft that explains a concrete solution, then adapt it for the channels you already use. Keep customer information out of public examples.

## Review on Friday

Record what you delivered, what needs another step, and what took longer than expected. Update next week's estimates. Track actual sales and expenses separately from plans and goals.

## What is included

- An editable weekly planning workbook, with example rows to replace.
- This guide as editable Word and readable PDF files.
- A three-slide weekly review presentation to customize.

This is a sample planning product with a draft price. It does not establish market demand, promise earnings, or connect a storefront or payment account.
'''
    if profile['business_name']:
        guide = guide.replace('# Freelancer weekly planning kit', '# ' + profile['business_name'] + ' — Freelancer weekly planning kit', 1)
    if profile['owner_name']:
        heading, rest = guide.split('\n', 1)
        guide = heading + '\n\nPrepared by: ' + profile['owner_name'] + '\n' + rest
    files = [filesystem.write_file(folder + '/README.md', guide)['path'],
             filesystem.write_pdf(folder + '/guide.pdf', guide)['path'],
             filesystem.write_docx(folder + '/guide.docx', guide)['path'],
             editor.write_spreadsheet(folder + '/weekly-planner.xlsx', [
                 ['Task', 'Client / project', 'Due date', 'Estimated hours', 'Actual hours', 'Status'],
                 ['Replace this sample task', 'Your project', '', 2, 0, 'Planned'],
                 ['Write one useful content draft', 'Your audience', '', 1, 0, 'Planned'],
                 ['Total', '', '', '=SUM(D2:D3)', '=SUM(E2:E3)', ''],
             ], 'Weekly plan')['path'],
             editor.write_presentation(folder + '/weekly-review.pptx', [
                 {'title': 'Weekly review', 'body': 'Week of: [date]\nPrepared by: [name]'},
                 {'title': 'Delivered and learned', 'body': 'Completed deliverables:\nWhat worked:\nWhat needs improvement:'},
                 {'title': 'Next week', 'body': 'Top three priorities:\nAvailable hours:\nClient commitments:'},
             ])['path']]
    store = ProductStore(database.parent / 'products.db', ROOT / 'data/artifacts/products', permissions)
    title = 'Freelancer Weekly Planning Kit — draft'
    existing = next((p for p in store.catalog()['products'] if p['title'] == title), None)
    product = existing or store.create(title, 'Independent freelancers',
        'Editable weekly workbook, Word/PDF guide, and weekly review slides. Sample draft for review before sale.',
        '9.99', 'Sample terms for review: buyer may edit and use these templates for personal or internal business planning. Redistribution or resale of the purchased files is not included.')
    bundle = store.package(product['id'], files)
    report = {'product_id': product['id'], 'title': title, 'deliverables': files,
              'bundle': bundle, 'publication': 'local draft only', 'sales_recorded': False}
    target = ROOT / folder / 'verification.json'
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
