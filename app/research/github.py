from __future__ import annotations

import base64
import re
from urllib.parse import urlencode
from typing import Any

from app.research.fetcher import PublicFetcher


class GitHubResearch:
    def __init__(self, fetcher: PublicFetcher) -> None:
        self.fetcher = fetcher

    def search(self, query: str, limit: int = 5, language: str = '', license: str = '') -> dict[str, Any]:
        """Discover public candidates; a public repository is not itself a reuse license."""
        query = query.strip()
        if not query or len(query) > 250 or not 1 <= limit <= 20:
            raise ValueError('Supply a query up to 250 characters and a limit from 1 to 20.')
        for value in (language, license):
            if value and not re.fullmatch(r'[\w.+#-]{1,40}', value):
                raise ValueError('Invalid language or SPDX license filter.')
        filters = ' is:public archived:false fork:false'
        filters += f' language:{language}' if language else ''
        filters += f' license:{license}' if license else ''
        payload = self.fetcher.get_json('https://api.github.com/search/repositories?' + urlencode({
            'q': query + filters, 'per_page': limit}))
        rows = []
        for item in payload.get('items', [])[:limit]:
            if item.get('private') or item.get('archived') or item.get('disabled'):
                continue
            spdx = (item.get('license') or {}).get('spdx_id')
            rows.append({key: item.get(key) for key in (
                'full_name', 'html_url', 'description', 'language', 'pushed_at', 'stargazers_count',
                'open_issues_count', 'default_branch')})
            rows[-1].update(license=spdx, reuse_review_required=True,
                            next_tool='github_research', next_arguments={'repository': item['full_name']})
        return {'query': query, 'results': rows, 'count': len(rows),
                'incomplete': bool(payload.get('incomplete_results')), 'trust': 'untrusted_external_data',
                'next_steps': ['Inspect README, release and dependencies with github_research.',
                               'Check license obligations, hardware needs and compatibility with the actual task.',
                               'Prepare an isolated prototype and run its tests before integrating.'],
                'installed': False}

    def inspect(self, repository: str, include_issues: bool = True, include_pulls: bool = False, include_source: bool = True) -> dict[str, Any]:
        match = re.fullmatch(r"(?:https?://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", repository.strip())
        if not match:
            raise ValueError("Repository must be OWNER/REPO or a public GitHub repository URL.")
        owner, repo = match.groups()
        root = f"https://api.github.com/repos/{owner}/{repo}"
        metadata = self.fetcher.get_json(root)
        gaps = []

        def optional(path, default):
            try:
                return self.fetcher.get_json(root + path)
            except Exception as exc:
                gaps.append(f'{path}: {type(exc).__name__}: {exc}'[:240])
                return default

        readme = optional('/readme', {})
        releases = optional('/releases?per_page=10', [])
        issues = optional('/issues?state=all&per_page=20', []) if include_issues else []
        pulls = optional('/pulls?state=all&per_page=20', []) if include_pulls else []
        tags = optional('/tags?per_page=20', [])
        contents = optional('/contents', []) if include_source else []
        workflows = optional('/actions/workflows?per_page=20', {}) if include_source else {}
        readme_text = ""
        if isinstance(readme, dict) and readme.get("encoding") == "base64":
            readme_text = base64.b64decode(str(readme.get("content", ""))).decode("utf-8", errors="replace")[:100_000]
        files = [str(item.get("name")) for item in contents] if isinstance(contents, list) else []
        manifests = [name for name in files if name.lower() in {"pyproject.toml", "requirements.txt", "package.json", "cargo.toml", "go.mod", "pom.xml", "dockerfile", "compose.yaml", "docker-compose.yml"}]
        examples = [name for name in files if name.lower() in {"examples", "example", "samples", "docs", "doc"}]
        latest_release = releases[0] if isinstance(releases, list) and releases else None
        return {
            "repository": str(metadata.get("full_name", f"{owner}/{repo}")) if isinstance(metadata, dict) else f"{owner}/{repo}",
            "url": str(metadata.get("html_url", f"https://github.com/{owner}/{repo}")) if isinstance(metadata, dict) else f"https://github.com/{owner}/{repo}",
            "description": metadata.get("description") if isinstance(metadata, dict) else None,
            "default_branch": metadata.get("default_branch") if isinstance(metadata, dict) else None,
            "updated_at": metadata.get("updated_at") if isinstance(metadata, dict) else None,
            "license": (metadata.get("license") or {}).get("spdx_id") if isinstance(metadata, dict) else None,
            "readme": readme_text,
            "latest_release": {key: latest_release.get(key) for key in ("name", "tag_name", "published_at", "prerelease", "draft", "html_url")} if latest_release else None,
            "releases": [{key: item.get(key) for key in ("name", "tag_name", "published_at", "html_url", "body")} for item in releases[:10]] if isinstance(releases, list) else [],
            "tags": [{"name": item.get("name"), "commit": (item.get("commit") or {}).get("sha")} for item in tags[:20]] if isinstance(tags, list) else [],
            "issues": [{key: item.get(key) for key in ("number", "title", "state", "updated_at", "html_url")} for item in issues[:20] if "pull_request" not in item] if isinstance(issues, list) else [],
            "pulls": [{key: item.get(key) for key in ("number", "title", "state", "updated_at", "html_url")} for item in pulls[:20]] if isinstance(pulls, list) else [],
            "root_files": files,
            "manifests": manifests,
            "documentation_directories": examples,
            "workflows": [{key: item.get(key) for key in ("name", "path", "state", "html_url")} for item in workflows.get("workflows", [])[:20]] if isinstance(workflows, dict) else [],
            "trust": "untrusted_external_data",
            "gaps": gaps,
            "archived": metadata.get('archived', False),
            "reuse_review_required": True,
        }
