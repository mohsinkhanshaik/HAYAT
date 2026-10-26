"""Bounded public professional-background research with source-by-source attribution.

A name match is a candidate, not an identity resolution. This workflow never
links faces, accounts or camera sightings, and never builds contact dossiers.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.research.query_planner import PlannedQuery

BROKERS = {'whitepages.com', 'spokeo.com', 'beenverified.com', 'truthfinder.com', 'intelius.com',
           'peoplefinders.com', 'fastpeoplesearch.com', 'truepeoplesearch.com', 'radaris.com',
           'clustrmaps.com', 'familytreenow.com', 'peekyou.com', 'mylife.com',
           # Contact-data brokers: they sell guessed or scraped work emails and phone numbers.
           'derrick-app.com', 'rocketreach.co', 'rocketreach.com', 'zoominfo.com', 'apollo.io', 'lusha.com',
           'signalhire.com', 'contactout.com', 'hunter.io', 'snov.io', 'leadiq.com', 'seamless.ai', 'adapt.io',
           'uplead.com', 'kaspr.io', 'getprospect.com', 'voilanorbert.com', 'anymailfinder.com', 'lead411.com',
           'aeroleads.com', 'skrapp.io', 'salesintel.io', 'swordfish.ai', 'datanyze.com', 'cognism.com'}
PRIVATE = re.compile(
    r'(?i)\b(?:home|residential|private|personal|current|street)\s+(?:address|phone|number|email|location)|'
    r'\b(?:phone|telephone|mobile|cellphone|e-?mail|date of birth|born on|birthday|social security|'
    r"passport|password|spouse|husband|wife|children(?!'s\s+(?:hospital|museum|theat|book|charity|fund))|relatives|"
    r'family members|net worth|'
    r'diagnosed|sexual orientation|religious beliefs|criminal record|voter registration)\b|'
    r'[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:\+?\d[\s().-]*){9,}'
)
#: Asking where someone is, what they do day to day, or about their household is not public work, however worded:
#: "where does she live", "daily routine", "kids", "license plate", a street address as context (found 2026-10-17).
#: The whole list applies to the name, which never holds such words; the context ("Partner at a law firm",
#: "Family Medicine Institute", "Daily Mail") is held only to the asks that cannot be about work (SURVEILLANCE_CONTEXT).
SURVEILLANCE_CONTEXT = re.compile(
    r'(?i)\b(?:where\s+(?:does|do|is|did)\b|lives\b|living\s+(?:at|in|near)|home\s+(?:town|street|location)|at\s+home|'
    r'whereabouts|current\s+location|'
    r'routine|commute|movements|tracking|track\s+(?:him|her|them)|follow\s+(?:him|her|them)|stalk\w*|spy\s+on|'
    r"(?:his|her|their|whose|\w+'s)\s+(?:kids?|children|sons?|daughters?|wife|husband)\b|"
    r'girlfriend|boyfriend|dating|license\s+plate|number\s+plate|'
    r'ip\s+address|photos?\s+of|pictures?\s+of|neighbou?rs?\b|'
    r'post\s*code|zip\s*code|maiden\s+name|medical|health\s+(?:condition|record|history)|pregnan\w*|religio\w*|'
    r'salary|income|social\s+media|instagram|facebook|tiktok|snapchat|onlyfans|'
    r'looks?\s+like|appearance|leaves?\s+(?:the\s+)?(?:office|work|home|house))|'
    r'\b\d{1,6}\s+[A-Za-z]+(?:\s+[A-Za-z]+)?\s+(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd|'
    r'court|ct|way|place|pl|terrace|close)\b'
)
SURVEILLANCE = re.compile(
    r'(?i)\b(?:where\s+(?:does|do|is|did)\b|lives?\b|living\b|address|whereabouts|location|located|locate|'
    r'routine|schedule|daily|commute|movements?|track(?:ing)?|follow(?:ing)?|stalk\w*|spy\w*|'
    r'kids?|child(?:ren)?|sons?|daughters?|family|parents?|girlfriend|boyfriend|partner|relationship|dating|'
    r'license\s+plate|number\s+plate|plates?|vehicle|car\b|ip\s+address|social\s+media\s+accounts?|'
    r'photos?\s+of|pictures?\s+of|face|appearance|hangouts?|neighbou?rs?\b|homes?\b|house\b|apartment)\b|'
    r"\w's\s+\w|"  # "Jane Smith's home": a possessive phrase is not a name
    r'\b\d{1,6}\s+[A-Za-z]+(?:\s+[A-Za-z]+)?\s+(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd|'
    r'court|ct|way|place|pl|terrace|close)\b'
)
#: A context is a public organization or role ("University of Toronto", "Partner at a law firm"). A question
#: ("where she works out", "is she pregnant") or an instruction ("find her") about the person is not one,
#: and "her …" / "his …" / "their …" is accepted only for their work ("her papers", "his company").
QUESTION_CONTEXT = re.compile(r'(?i)^\s*(?:where|when|what|which|who|whom|whose|how|is|are|does|do|did|was|were|has|have|'
                              r'can|could|will)\b|\?\s*$')
FIND_CONTEXT = re.compile(r'(?i)\b(?:find|locate|trace|track|follow|watch|monitor|spy|stalk|dox+|identify|unmask)\w*\s+'
                          r'(?:him|her|them|this\s+(?:person|guy|girl|man|woman))\b')
POSSESSIVE = re.compile(r"(?i)\b(?:his|her|hers|their|whose)\s+(?P<what>[a-z][a-z'-]*)")
WORK_WORDS = frozenset((
    'work', 'works', 'research', 'paper', 'papers', 'publication', 'publications', 'book', 'books', 'writing', 'writings',
    'novel', 'novels', 'memoir', 'thesis', 'dissertation', 'article', 'articles', 'company', 'companies', 'startup', 'firm',
    'lab', 'laboratory', 'group', 'team', 'role', 'roles', 'career', 'job', 'position', 'title', 'talk', 'talks', 'lecture',
    'lectures', 'keynote', 'art', 'artwork', 'music', 'album', 'albums', 'film', 'films', 'patent', 'patents', 'project',
    'projects', 'field', 'department', 'employer', 'organization', 'organisation', 'studio', 'band', 'campaign', 'office',
    'term', 'tenure', 'portfolio', 'blog', 'podcast', 'column', 'show', 'award', 'awards', 'prize', 'discovery', 'invention',
    'inventions', 'theory', 'contribution', 'contributions', 'court', 'ruling', 'rulings', 'policy', 'policies', 'party',
    'ministry', 'university', 'institute', 'school', 'faculty', 'course', 'courses', 'products', 'product', 'software', 'code'))


#: Real surnames that are also watched words: in a Title-case name they are the person's name ("Julia Child",
#: "Masayoshi Son", "Alec Douglas-Home"); lower-case ("jane smith home") they are an ask.
SURNAMES = frozenset({'home', 'daily', 'child', 'parent', 'parents', 'son', 'sons', 'lives', 'track', 'face', 'partner', 'house'})


def name_reason(name: str) -> str:
    """Why a supplied name is not just a name, or '' when it is one."""
    if PRIVATE.search(name):
        return 'private'
    words = re.findall(r"[^\W\d_][\w'-]*", name)
    titled = 1 < len(words) <= 5 and all(word[0].isupper() for word in words)
    for match in SURVEILLANCE.finditer(name):
        found = match.group(0)
        if titled and found[0].isupper() and found.casefold() in SURNAMES and match.start() > 0:
            continue
        return 'surveillance'
    return ''


def context_reason(context: str) -> str:
    """Why a supplied context is not a public-work context, or '' when it is one."""
    if not context:
        return ''
    if SURVEILLANCE_CONTEXT.search(context) or PRIVATE.search(context):
        return 'private'
    if QUESTION_CONTEXT.search(context):
        return 'question'
    if FIND_CONTEXT.search(context):
        return 'find'
    if any(match['what'].casefold() not in WORK_WORDS for match in POSSESSIVE.finditer(context)):
        return 'personal'
    return ''
PROFESSIONAL = re.compile(
    r'(?i)\b(?:professor|researcher|scientist|engineer|author|writer|artist|director|founder|'
    r'co-founder|chief|president|minister|mayor|governor|senator|representative|journalist|'
    r'lecturer|fellow|developer|designer|architect|musician|composer|actor|actress|athlete|'
    r'coach|lawyer|physician|economist|historian|mathematician|biologist|physicist|'
    r'works? at|worked at|serves? as|served as|joined|appointed|elected|graduated|degree|'
    r'university|institute|publication|published|authored|coauthored|co-authored|'
    r'award|awarded|prize|patent|interview|conference|keynote|book|paper|exhibition|github|repositories|repository)\b'
)


def blocked_source(url: str) -> bool:
    host = (urlsplit(url).hostname or '').lower().rstrip('.')
    return any(host == domain or host.endswith('.' + domain) for domain in BROKERS)


@dataclass(frozen=True)
class PersonRequest:
    name: str
    context: str = ''
    profile_url: str = ''

    def __post_init__(self):
        for key, limit in (('name', 120), ('context', 240), ('profile_url', 1000)):
            value = ' '.join(str(getattr(self, key) or '').split())
            if len(value) > limit:
                raise ValueError(f'{key.replace("_", " ").capitalize()} is too long (maximum {limit} characters).')
            object.__setattr__(self, key, value)
        if not self.name or len(self.name.split()) > 12 or not any(char.isalpha() for char in self.name):
            raise ValueError('Give a known name; a public organization or role helps distinguish namesakes.')
        if name_reason(self.name) or context_reason(self.context):
            raise ValueError('Use a name and public work context. Private contact, family, location and sensitive personal details are outside this search.')
        if self.profile_url:
            parts = urlsplit(self.profile_url)
            if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or blocked_source(self.profile_url):
                raise ValueError('Use a public professional profile URL without a login or people-broker listing.')
            host = parts.hostname.casefold().rstrip('.')
            if host == 'localhost' or host.endswith(('.local', '.localhost')):
                raise ValueError('A person profile must be a public internet URL.')
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                raise ValueError('A person profile must be a public internet URL.')

    @property
    def query(self):
        return f'Public professional background of {self.name}' + (f' — {self.context}' if self.context else '')

    def plan(self, budget):
        subject = f'"{self.name.replace(chr(34), "")}" {self.context}'.strip()
        return [PlannedQuery(f'{subject} {suffix}', intent, priority, rationale)
                for suffix, intent, priority, rationale in (
                    ('official biography professional profile', 'primary', 1.0, 'Public biography and work affiliation'),
                    ('publications work portfolio', 'primary', .96, 'Publicly attributed work'),
                    ('interview conference speaker', 'corroboration', .9, 'Independent public professional coverage'),
                    ('current role official announcement', 'freshness', .85, 'Check the date of role claims'),
                )][:budget.searches]

    def admits(self, document):
        if blocked_source(document.final_url) or PRIVATE.search(document.title):
            return False
        return self.mentions(document.title + ' ' + document.text)

    def mentions(self, text):
        normalized = lambda value: re.sub(r'[^\w]+', ' ', value.casefold()).strip()
        haystack = normalized(text)
        name = normalized(self.name)
        return bool(name and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', haystack))


def public_work_claim(text):
    return bool(PROFESSIONAL.search(text) and not PRIVATE.search(text))


def parse_person_request(text):
    """Explicit chat forms only; an unnamed face or vague 'find him' is not a name."""
    match = re.fullmatch(r'(?is)\s*(?:please\s+)?(?:deep(?:ly)?\s+)?(?:research|search)\s+'
                         r'(?:person(?:\s+named)?|public\s+(?:professional\s+)?(?:background|work)\s+of)\s+(.+?)\s*[.!?]*', text)
    if not match:
        return None
    value = re.sub(r'(?i)\s+(?:in depth|in deep|deeply|thoroughly)$', '', match[1]).strip()
    parts = re.split(r'\s+at\s+', value, maxsplit=1, flags=re.I)
    return PersonRequest(parts[0].strip('"\''), parts[1] if len(parts) > 1 else '')


#: Sources that usually describe a person's own work first: Wikipedia, universities, scholarly records.
_AUTHORITATIVE = re.compile(r'(?i)(?:^|\.)(?:wikipedia\.org|wikidata\.org|openalex\.org|orcid\.org|arxiv\.org|'
                            r'scholar\.google\.com|dblp\.org|acm\.org|ieee\.org)$|\.(?:edu|ac\.[a-z]{2}|gov)$')


def source_rank(request, source, has_facts):
    """Higher first. A fan-made 'Portfolio-Linus-Torvalds' repository ranked above Wikipedia-type sources before."""
    parts = urlsplit(source.final_url)
    host = (parts.hostname or '').lower()
    score = 2.0 if has_facts else 0.0
    if request.profile_url and source.final_url.rstrip('/').casefold() == request.profile_url.rstrip('/').casefold():
        score += 4
    if host == 'api.github.com' or _AUTHORITATIVE.search(host):
        score += 3
    if host == 'github.com':
        owner = parts.path.strip('/').split('/')[0].casefold()
        profile_owner = urlsplit(request.profile_url).path.strip('/').split('/')[0].casefold() if request.profile_url else ''
        if parts.path.strip('/').count('/') >= 1 and owner != profile_owner:
            score -= 3  # someone else's repository about the person
    return score + float(getattr(source, 'quality', 0) or 0)


def person_answer(request, documents, claims):
    """Keep same-name source candidates separate; no synthesized identity dossier."""
    from app.research.models import ClaimStatus
    lines = [f'Public professional sources for {request.name}',
             'Name matches are unverified candidates. Sources may describe different people; they are not merged into one identity.']
    if request.context:
        lines.append('Context supplied: ' + request.context)
    if not documents:
        lines.append('No retrievable public professional source passed the name and source checks. Add a public organization, role or profile link.')

    def tidy(text):
        # A page's own footnote marks ("since 1991 [1] .", "born 1986 [ 3 ]", "[citation needed]") read as HAYAT's
        # source numbers once quoted here.
        text = re.sub(r'\s*\[\s*(?:\d+|[a-z]|citation needed|note \d+)\s*\]', '', str(text), flags=re.I)
        return re.sub(r'\s+([.,;:)])', r'\1', re.sub(r'\(\s*in\s+[a-z]{2}\s*\)', '', text)).strip()

    def facts_for(source):
        found = [tidy(claim.text) for claim in claims if claim.status in (ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED)
                 and public_work_claim(claim.text) and request.mentions(claim.text)
                 and any(evidence.source_url in (source.url, source.final_url) for evidence in claim.evidence)]
        return list(dict.fromkeys(found))[:5]

    ranked = sorted(documents, key=lambda source: -source_rank(request, source, bool(facts_for(source))))
    by_source = [(index, source, facts_for(source)) for index, source in enumerate(ranked, 1)]
    # At a glance: facts the sources state, best supported first, each with the sources that state it. Still not a
    # merged identity: the line below says so, and every fact keeps its source numbers.
    cited: dict[str, list[int]] = {}
    for index, _source, facts in by_source:
        for fact in facts:
            cited.setdefault(fact, []).append(index)
    if cited:
        top = sorted(cited.items(), key=lambda item: (-len(item[1]), item[1][0]))[:5]
        lines += ['', 'At a glance (as the sources report it; check that they describe the person you mean):']
        lines += [f'• {fact} [{", ".join(map(str, where))}]' for fact, where in top]
    for index, source, facts in by_source:
        lines += ['', f'[{index}] {source.title}', source.final_url,
                  'Published: ' + (source.published_at or 'not reported') + ' · Retrieved: ' + source.retrieved_at]
        lines += ['• ' + text for text in facts] or ['No supported public-work claim was extracted from this source.']
    lines += ['', 'Not established: whether these sources describe the same person, whether older roles are current, or whether this is the person you mean.',
              'Scope: public work, professional background and published work. No face identification, private contact details, family dossiers or person tracking.']
    return '\n'.join(lines)
