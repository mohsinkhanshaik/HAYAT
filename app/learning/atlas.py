"""A broad, prerequisite-aware learning map. Coverage is a plan, never a claim of omniscience."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LearningDomain:
    title: str
    colour: str
    purpose: str
    topics: tuple[str, ...]


DOMAINS = (
    LearningDomain('Thinking & research', '#E4BE73', 'Ask better questions and check what the evidence supports.',
                   ('Critical thinking', 'Logic', 'Scientific method', 'Research literacy', 'Media literacy')),
    LearningDomain('Mathematics', '#8EB7F0', 'Understand quantity, change, uncertainty and patterns.',
                   ('Arithmetic', 'Algebra', 'Geometry', 'Probability', 'Statistics', 'Calculus')),
    LearningDomain('Physical science', '#78D3D5', 'Build models of matter, energy and the universe.',
                   ('Physics', 'Chemistry', 'Astronomy')),
    LearningDomain('Life & health', '#8ECD9A', 'Understand living systems and evaluate health information.',
                   ('Biology', 'Genetics', 'Human physiology', 'Health literacy')),
    LearningDomain('Earth & environment', '#9FCC81', 'Connect maps, weather, ecosystems and public observations.',
                   ('Geography', 'Geology', 'Meteorology', 'Climate science', 'Ecology', 'Public camera research')),
    LearningDomain('Computing & AI', '#A4ACF4', 'Understand programs, data, local models and their limitations.',
                   ('Python', 'Linux', 'Networking', 'Data structures', 'Databases', 'Machine learning', 'AI literacy')),
    LearningDomain('Engineering', '#E6A682', 'Reason about systems, design choices and failure modes.',
                   ('Engineering design', 'Electronics', 'Control systems', 'Energy systems')),
    LearningDomain('History & society', '#D5A0D9', 'Compare societies, institutions and historical explanations.',
                   ('World history', 'Civics', 'Sociology', 'Political geography')),
    LearningDomain('Economics & business', '#D9C785', 'Understand incentives, tradeoffs and business evidence.',
                   ('Economics', 'Accounting', 'Business strategy', 'Personal finance')),
    LearningDomain('Mind & philosophy', '#C3B0E7', 'Explore learning, arguments, values and human behaviour.',
                   ('Psychology', 'Philosophy', 'Ethics', 'Learning science')),
    LearningDomain('Language & arts', '#E9A7B3', 'Communicate clearly and understand creative expression.',
                   ('Writing', 'Arabic', 'Literature', 'Art history', 'Music theory')),
    LearningDomain('AI infrastructure', '#89BDD2', 'Move from foundations to running and diagnosing real systems.',
                   ('Docker', 'Kubernetes', 'Cloud', 'GPU architecture', 'CUDA', 'model serving', 'SRE')),
)

# Existing infrastructure definitions remain authoritative in LearningManager.
PREREQUISITES = {
    'Critical thinking': [], 'Logic': ['Critical thinking'], 'Scientific method': ['Critical thinking'],
    'Research literacy': ['Scientific method'], 'Media literacy': ['Research literacy'],
    'Arithmetic': [], 'Algebra': ['Arithmetic'], 'Geometry': ['Arithmetic'],
    'Probability': ['Arithmetic'], 'Statistics': ['Probability'], 'Calculus': ['Algebra', 'Geometry'],
    'Physics': ['Algebra', 'Scientific method'], 'Chemistry': ['Scientific method', 'Arithmetic'],
    'Astronomy': ['Physics'], 'Biology': ['Scientific method'], 'Genetics': ['Biology', 'Probability'],
    'Human physiology': ['Biology'], 'Health literacy': ['Research literacy', 'Human physiology'],
    'Geography': [], 'Geology': ['Geography', 'Scientific method'], 'Meteorology': ['Geography', 'Physics'],
    'Climate science': ['Meteorology', 'Statistics'], 'Ecology': ['Biology', 'Geography'],
    'Public camera research': ['Geography', 'Research literacy'],
    'Data structures': ['Python'], 'Databases': ['Python'], 'Machine learning': ['Python', 'Statistics', 'Algebra'],
    'AI literacy': ['Critical thinking'], 'Engineering design': ['Scientific method'],
    'Electronics': ['Physics'], 'Control systems': ['Calculus', 'Engineering design'], 'Energy systems': ['Physics'],
    'World history': [], 'Civics': ['Critical thinking'], 'Sociology': ['Statistics', 'World history'],
    'Political geography': ['Geography', 'Civics'], 'Economics': ['Arithmetic', 'Critical thinking'],
    'Accounting': ['Arithmetic'], 'Business strategy': ['Economics'], 'Personal finance': ['Arithmetic', 'Economics'],
    'Psychology': ['Scientific method', 'Statistics'], 'Philosophy': ['Logic'], 'Ethics': ['Critical thinking'],
    'Learning science': ['Psychology'], 'Writing': [], 'Arabic': [], 'Literature': ['Writing'],
    'Art history': ['World history'], 'Music theory': ['Arithmetic'],
}


def learning_atlas(learning):
    """Join the roadmap to saved assessment evidence without creating assessed topics."""
    states = {row['topic']: row for row in learning.topics()}
    domains = []
    for domain in DOMAINS:
        rows = []
        for topic in domain.topics:
            state = states.get(topic) or {}
            prerequisites = state.get('prerequisites', PREREQUISITES.get(topic, []))
            tested = bool(state.get('last_tested'))
            gaps = [name for name in prerequisites if not states.get(name, {}).get('last_tested')
                    or states[name].get('mastery', 0) < .55]
            rows.append({'topic': topic, 'level': state.get('level', 'UNKNOWN'), 'tested': tested,
                         'mastery': state.get('mastery', 0), 'confidence': state.get('confidence', 0),
                         'next_review': state.get('next_review'), 'prerequisites': prerequisites,
                         'prerequisite_gaps': gaps, 'ready': not gaps})
        assessed = [row for row in rows if row['tested']]
        domains.append({'title': domain.title, 'colour': domain.colour, 'purpose': domain.purpose, 'topics': rows,
                        'assessed': len(assessed), 'total': len(rows),
                        'mastery': sum(row['mastery'] for row in assessed) / len(assessed) if assessed else None})
    return {'domains': domains, 'assessed': sum(domain['assessed'] for domain in domains),
            'total': sum(domain['total'] for domain in domains),
            'basis': 'A broad starting roadmap, not all human knowledge. Assessment evidence is a learning estimate; '
                     'reading a lesson or skipping a question does not prove understanding.'}


def atlas_text(learning):
    atlas = learning_atlas(learning)
    lines = [f"Learning roadmap: {atlas['assessed']} of {atlas['total']} topics assessed.", atlas['basis']]
    for domain in atlas['domains']:
        lines.extend(['', domain['title'] + ': ' + ', '.join(row['topic'] for row in domain['topics'])])
    lines.extend(['', 'Begin with: teach me Critical thinking. Choose any topic above, then explain it back, '
                  'test a new example, and review it later. Use research <topic> for source-backed current information.'])
    return '\n'.join(lines)
