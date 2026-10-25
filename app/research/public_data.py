"""Bounded public registry metadata and explicitly dated World Bank observations.

Discovery records are leads, not independently verified scientific findings.
Only registered public API endpoints are fetched; landing/download URLs remain data.
"""
from __future__ import annotations

import html
import ipaddress
import json
import math
import re
from datetime import UTC, datetime
from urllib.parse import quote, urlencode, urlsplit


SOURCES = (
    {'id': 'datacite', 'name': 'DataCite', 'scope': 'Dataset DOI metadata',
     'api': 'https://api.datacite.org/dois', 'documentation': 'https://support.datacite.org/docs/api-queries'},
    {'id': 'crossref', 'name': 'Crossref', 'scope': 'Publication DOI metadata',
     'api': 'https://api.crossref.org/works', 'documentation': 'https://www.crossref.org/documentation/retrieve-metadata/rest-api/'},
    {'id': 'worldbank', 'name': 'World Bank', 'scope': 'Annual indicators by country, series and year',
     'api': 'https://api.worldbank.org/v2',
     'documentation': 'https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures'},
    {'id': 'europe_pmc', 'name': 'Europe PMC', 'scope': 'Life-science publication metadata, including records without a DOI',
     'api': 'https://www.ebi.ac.uk/europepmc/webservices/rest/search',
     'documentation': 'https://europepmc.org/RestfulWebService'},
)
DOI = re.compile(r'10\.\d{4,9}/[^\s<>"\']+', re.I)


def text(value, limit=500):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ''
    return ' '.join(html.unescape(re.sub(r'<[^>]*>', ' ', str(value))).split())[:limit]


def public_link(value):
    if not isinstance(value, str) or len(value) > 2000:
        return ''
    try:
        parts = urlsplit(value.strip())
        parts.port
        host = (parts.hostname or '').casefold().rstrip('.')
        if parts.scheme not in ('https', 'http') or not host or parts.username or parts.password:
            return ''
        if host == 'localhost' or host.endswith(('.local', '.localhost')):
            return ''
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        return '' if address is not None and not address.is_global else value.strip()
    except ValueError:
        return ''


def doi_name(value):
    value = text(value, 500)
    value = re.sub(r'(?i)^https?://(?:dx\.)?doi\.org/', '', value)
    return value.casefold() if DOI.fullmatch(value) else ''


def catalog():
    return {'public_data': True, 'sources': [{**source, 'authentication': 'public read; no key required',
                                             'readiness': 'not checked by this catalog call'} for source in SOURCES],
            'basis': 'Registered API capabilities only. Availability is measured by requests, not inferred from registration.'}


class PublicData:
    def __init__(self, fetcher):
        self.fetcher = fetcher

    def _get(self, url):
        resource = self.fetcher.fetch(url, 2 * 1024 * 1024, {'application/json', 'application/*+json', 'text/json'})
        return json.loads(resource.content), {'api_url': resource.final_url, 'requested_url': url,
                                              'retrieved_at': resource.retrieved_at, 'sha256': resource.sha256,
                                              'bytes': len(resource.content)}

    def search(self, query, kind='all', limit=5, page=1, cursor='*'):
        if kind == 'literature':
            if type(page) is not int or page != 1:
                raise ValueError('Europe PMC uses next_cursor, not page numbers; pass its cursor with page=1.')
            return self.literature(query, limit, cursor)
        if cursor != '*':
            raise ValueError('Cursors are supported only for literature searches.')
        query = ' '.join(str(query or '').split())
        if not query or len(query) > 500 or kind not in ('all', 'datasets', 'publications'):
            raise ValueError('Give a query up to 500 characters and kind all, datasets or publications.')
        if type(limit) is not int or not 1 <= limit <= 10 or type(page) is not int or not 1 <= page <= 100:
            raise ValueError('Use 1–10 results per source and a page from 1 to 100.')
        reports, records, seen = [], [], {}
        for source in SOURCES[:2]:
            key = source['id']
            if kind == 'datasets' and key != 'datacite' or kind == 'publications' and key != 'crossref':
                continue
            if key == 'datacite':
                params = {'query': query, 'resource-type-id': 'dataset', 'page[size]': limit, 'page[number]': page,
                          'fields[dois]': 'doi,titles,publisher,publicationYear,types,url,rightsList,creators'}
            else:
                params = {'query.bibliographic': query, 'rows': limit, 'offset': (page - 1) * limit,
                          'select': 'DOI,title,type,publisher,published,license,URL,author'}
            url = source['api'] + '?' + urlencode(params)
            try:
                payload, provenance = self._get(url)
                if not isinstance(payload, dict):
                    raise ValueError('Registry returned a non-object response.')
                items = payload.get('data') if key == 'datacite' else (payload.get('message') or {}).get('items')
                if not isinstance(items, list):
                    raise ValueError('Registry did not return its documented records list.')
                total = (payload.get('meta') or {}).get('total') if key == 'datacite' else payload['message'].get('total-results')
                if type(total) is not int or total < 0:
                    total = None
                skipped = 0
                for item in items[:limit]:
                    try:
                        row = self._record(key, item)
                    except (ValueError, TypeError, AttributeError, IndexError):
                        row = None
                    if row is None:
                        skipped += 1
                        continue
                    row.update({'retrieved_at': provenance['retrieved_at'], 'registry_query_url': provenance['api_url']})
                    if row['doi'] in seen:
                        old = seen[row['doi']]
                        old['also_listed_by'].append(key)
                        old['metadata_conflicts'] += [field for field in ('title', 'year', 'publisher')
                                                      if row[field] and old[field] and row[field] != old[field]]
                    else:
                        seen[row['doi']] = row
                        records.append(row)
                reports.append({'provider': key, 'status': 'ok', 'returned': len(items[:limit]), 'skipped_records': skipped,
                                'reported_total': total, 'has_more': total is not None and page * limit < total, **provenance})
            except Exception as error:  # partial provider failure must leave the other registry usable
                reports.append({'provider': key, 'status': 'error', 'api_url': url,
                                'error': f'{type(error).__name__}: {str(error)[:300]}'})
        return {'public_data': True, 'metadata_only': True, 'query': query, 'kind': kind, 'page': page,
                'per_source_limit': limit, 'records': records, 'count': len(records), 'providers': reports,
                'next_page': page + 1 if page < 100 and any(row.get('has_more') for row in reports) else None,
                'complete_provider_response': all(row['status'] == 'ok' for row in reports),
                'basis': 'Publisher/repository metadata only; dataset contents and research conclusions were not retrieved or verified. '
                         'A DOI or a reported license does not establish data quality, current access or unrestricted reuse.'}

    def literature(self, query, limit=5, cursor='*'):
        """Bounded, credential-free metadata search; never follow a provider nextPageUrl."""
        if not isinstance(query, str) or not (query := ' '.join(query.split())) or len(query) > 500:
            raise ValueError('Give a literature query of 1–500 characters.')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('Use 1–10 literature records per request.')
        if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or any(ord(c) < 32 for c in cursor):
            raise ValueError('Use a bounded opaque Europe PMC cursor.')
        base = next(source['api'] for source in SOURCES if source['id'] == 'europe_pmc')
        url = base + '?' + urlencode({'query': query, 'format': 'json', 'pageSize': limit, 'cursorMark': cursor, 'resultType': 'lite'})
        records, seen, next_cursor = [], set(), None
        try:
            payload, provenance = self._get(url)
            items = payload.get('resultList', {}).get('result') if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ValueError('Europe PMC did not return its documented result list.')
            skipped = 0
            for item in items[:limit]:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                identifier, origin, title = text(item.get('id'), 80), text(item.get('source'), 20), text(item.get('title'))
                if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', identifier) or not re.fullmatch(r'[A-Z]{2,20}', origin) or not title:
                    skipped += 1
                    continue
                identity = origin + ':' + identifier
                if identity in seen:
                    skipped += 1
                    continue
                seen.add(identity)
                doi = doi_name(item.get('doi'))
                year = str(item.get('pubYear') or '')
                year = int(year) if re.fullmatch(r'\d{4}', year) and 1000 <= int(year) <= 2200 else None
                landing = 'https://europepmc.org/article/' + origin + '/' + quote(identifier, safe='')
                warnings = []
                if year and year > int(provenance['retrieved_at'][:4]):
                    warnings.append('Publisher-reported publication year is later than retrieval year; check forthcoming/indexing dates.')
                records.append({'record_id': identity, 'doi': doi, 'doi_url': 'https://doi.org/' + quote(doi, safe='/') if doi else '',
                                'title': title, 'publisher': text(item.get('journalTitle')), 'year': year,
                                'resource_type': text(item.get('pubType')), 'authors': [text(author, 150) for author in text(item.get('authorString'), 1500).split(',')[:10] if author.strip() and '@' not in author],
                                'reported_license_urls': [], 'landing_url': landing, 'metadata_url': provenance['api_url'],
                                'provider': 'europe_pmc', 'also_listed_by': [], 'metadata_conflicts': [], 'metadata_warnings': warnings,
                                'open_access_reported': item.get('isOpenAccess') == 'Y',
                                'retrieved_at': provenance['retrieved_at'], 'registry_query_url': provenance['api_url']})
            candidate = payload.get('nextCursorMark')
            if (items and isinstance(candidate, str) and candidate != cursor and 0 < len(candidate) <= 2048
                    and not any(ord(c) < 32 for c in candidate)):
                next_cursor = candidate
            total = payload.get('hitCount')
            report = {'provider': 'europe_pmc', 'status': 'ok', 'returned': len(items[:limit]), 'skipped_records': skipped,
                      'reported_total': total if type(total) is int and total >= 0 else None, 'has_more': next_cursor is not None, **provenance}
        except Exception as error:
            report = {'provider': 'europe_pmc', 'status': 'error', 'api_url': url, 'error': f'{type(error).__name__}: {str(error)[:300]}'}
        return {'public_data': True, 'metadata_only': True, 'query': query, 'kind': 'literature', 'page': 1,
                'per_source_limit': limit, 'records': records, 'count': len(records), 'providers': [report],
                'next_page': None, 'next_cursor': next_cursor, 'complete_provider_response': report['status'] == 'ok',
                'basis': 'Europe PMC bibliographic metadata only. Full text, scientific conclusions and reuse rights were not verified. '
                         'Open-access flags are publisher metadata, not a license grant; preprints and future dates need review.'}

    @staticmethod
    def _record(provider, item):
        if not isinstance(item, dict):
            return None
        data = item.get('attributes') if provider == 'datacite' else item
        if not isinstance(data, dict):
            return None
        doi = doi_name(data.get('doi') or data.get('DOI') or item.get('id'))
        titles = data.get('titles') if provider == 'datacite' else data.get('title')
        if not doi or not isinstance(titles, list) or not titles:
            return None
        title = text(titles[0].get('title') if isinstance(titles[0], dict) else titles[0])
        if not title:
            return None
        publisher = data.get('publisher')
        if isinstance(publisher, dict):
            publisher = publisher.get('name')
        if provider == 'datacite':
            year = data.get('publicationYear')
            raw_licenses = data.get('rightsList') or []
            authors = [text(row.get('name') or ' '.join(text(row.get(field)) for field in ('givenName', 'familyName')), 150)
                       for row in (data.get('creators') or [])[:10] if isinstance(row, dict)]
            resource_type = (data.get('types') or {}).get('resourceTypeGeneral', 'Dataset')
            if resource_type != 'Dataset':
                return None
        else:
            dates = (data.get('published') or {}).get('date-parts') or []
            year = dates[0][0] if dates and isinstance(dates[0], list) and dates[0] else None
            raw_licenses = data.get('license') or []
            authors = [' '.join(text(row.get(field), 100) for field in ('given', 'family')).strip()
                       for row in (data.get('author') or [])[:10] if isinstance(row, dict)]
            resource_type = data.get('type')
        licenses = [link for row in raw_licenses if isinstance(row, dict)
                    if (link := public_link(row.get('rightsUri') or row.get('URL')))]
        doi_url = 'https://doi.org/' + quote(doi, safe='/')
        return {'doi': doi, 'doi_url': doi_url, 'title': title, 'publisher': text(publisher),
                'year': year if type(year) is int and 1000 <= year <= 2200 else None,
                'resource_type': text(resource_type), 'authors': [author for author in authors if author and '@' not in author],
                'reported_license_urls': licenses[:5], 'landing_url': public_link(data.get('url') or data.get('URL')) or doi_url,
                'metadata_url': next(row['api'] for row in SOURCES if row['id'] == provider) + '/' + quote(doi, safe=''),
                'provider': provider, 'also_listed_by': [], 'metadata_conflicts': []}

    def indicator(self, country, indicator, start_year=2015, end_year=None, source=2):
        country, indicator = str(country).upper().strip(), str(indicator).upper().strip()
        end_year = datetime.now(UTC).year - 1 if end_year is None else end_year
        if not re.fullmatch(r'[A-Z]{2,3}', country) or country == 'ALL' or not re.fullmatch(r'[A-Z0-9_.]{3,80}', indicator):
            raise ValueError('Give one country/aggregate code (USA, IN, WLD) and one indicator code (SP.POP.TOTL).')
        if (type(start_year) is not int or type(end_year) is not int or not 1900 <= start_year <= end_year <= 2100
                or end_year - start_year >= 100 or type(source) is not int or not 1 <= source <= 999):
            raise ValueError('Use a year range of at most 100 annual observations (1900–2100) and a numeric source ID.')
        base = SOURCES[2]['api']
        params = urlencode({'format': 'json', 'date': f'{start_year}:{end_year}', 'source': source, 'per_page': 100, 'footnote': 'y'})
        url = f'{base}/country/{country}/indicator/{indicator}?{params}'
        payload, provenance = self._get(url)
        if not isinstance(payload, list) or len(payload) != 2 or not isinstance(payload[0], dict) or not isinstance(payload[1], list):
            raise ValueError('World Bank did not return the documented observation response; check country, series and source.')
        observations, invalid, duplicates = {}, 0, 0
        for row in payload[1]:
            if not isinstance(row, dict):
                invalid += 1
                continue
            series, place = row.get('indicator'), row.get('country')
            if (not isinstance(series, dict) or not isinstance(place, dict) or series.get('id') != indicator
                    or country not in (str(place.get('id') or '').upper(), str(row.get('countryiso3code') or '').upper())):
                invalid += 1
                continue
            date, value = str(row.get('date', '')), row.get('value')
            if not re.fullmatch(r'\d{4}', date) or not start_year <= int(date) <= end_year:
                invalid += 1
                continue
            valid = value is None or type(value) is int or type(value) is float and math.isfinite(value)
            record = {'year': int(date), 'value': value if valid else None,
                      'status': 'missing' if value is None else 'observed' if valid else 'invalid value',
                      'footnote': text(row.get('footnote'), 800)}
            invalid += not valid
            if int(date) in observations:
                duplicates += 1
                old = observations[int(date)]
                if old['value'] != record['value'] or old['status'] == 'conflicting values':
                    record['value'], record['status'] = None, 'conflicting values'
            observations[int(date)] = record
        metadata_url = f'{base}/indicator/{indicator}?' + urlencode({'format': 'json', 'source': source, 'per_page': 1})
        metadata, metadata_error = {}, ''
        try:
            detail, _meta_provenance = self._get(metadata_url)
            if not isinstance(detail, list) or len(detail) != 2 or not isinstance(detail[1], list):
                raise ValueError('Indicator metadata response is malformed.')
            metadata = next((row for row in detail[1] if isinstance(row, dict) and row.get('id') == indicator), {})
            if not metadata:
                raise ValueError('Requested indicator was absent from the metadata response.')
        except Exception as error:
            metadata_error = f'{type(error).__name__}: {str(error)[:240]}'
        values = [observations.get(year, {'year': year, 'value': None, 'status': 'not returned', 'footnote': ''})
                  for year in range(start_year, end_year + 1)]
        pages = payload[0].get('pages')
        return {'public_data': True, 'indicator_data': True, 'country': country, 'indicator': indicator,
                'indicator_name': text(metadata.get('name')), 'unit': text(metadata.get('unit')) or None,
                'source_note': text(metadata.get('sourceNote'), 1500), 'source_organization': text(metadata.get('sourceOrganization'), 800),
                'source_id': source, 'source_updated_at': payload[0].get('lastupdated'), 'metadata_url': metadata_url,
                'metadata_error': metadata_error, 'observations': values, 'invalid_rows': invalid, 'duplicate_years': duplicates,
                'response_truncated': type(pages) is int and pages > 1, **provenance,
                'basis': 'Annual source-reported observations, not a forecast. Missing/conflicting values remain empty; no gap filling. '
                         'Unit, methodology, revisions and reuse terms must be checked against the linked indicator/source.'}


def discovery_report(value):
    lines = [f'Public data discovery: {value["query"]}', value['basis'], '']
    for index, row in enumerate(value['records'], 1):
        lines += [f'{index}. {row["title"]}', row['doi_url'] or row['landing_url'],
                  f'{row["provider"]} · {row["publisher"] or "publisher not reported"} · {row["year"] or "year not reported"}',
                  'Metadata: ' + row['metadata_url']]
        if row['metadata_conflicts']:
            lines.append('Registries differ on: ' + ', '.join(row['metadata_conflicts']))
        lines.extend(row.get('metadata_warnings', []))
        lines.append('')
    if not value['records']:
        lines.append('No usable registry metadata was returned. Try a more specific topic or check the provider errors below.')
    for provider in value['providers']:
        lines.append(f'{provider["provider"]}: ' + (f'{provider["returned"]} records returned' if provider['status'] == 'ok' else provider['error']))
    if value['next_page']:
        lines.append(f'More registry records may be available on page {value["next_page"]}.')
    if value.get('next_cursor'):
        lines.append('More Europe PMC metadata is available with the returned next_cursor; nothing else was downloaded.')
    return '\n'.join(lines)


def parse_data_request(value):
    """Explicit catalog lookups; ordinary research-paper questions keep full research."""
    match = re.fullmatch(r'(?is)\s*(?:please\s+)?(?:find|search)(?:\s+for)?\s+'
                         r'(?P<kind>public\s+data\s+and\s+papers|(?:public\s+)?datasets?|publication\s+metadata)'
                         r'\s+(?:(?:about|on|for)\s+)?(?P<query>.+?)\s*[.!?]*', value)
    if not match:
        return None
    kind = 'all' if 'and' in match['kind'].lower() else 'publications' if 'publication' in match['kind'].lower() else 'datasets'
    return {'query': match['query'].strip(), 'kind': kind}
