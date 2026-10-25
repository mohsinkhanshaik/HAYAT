"""Explain object-detection scores without presenting them as identity matches."""
import math


def people_report(value):
    if not isinstance(value, dict):
        return 'No readable person-detector result was returned.'
    if 'people' not in value:
        return ('Local person detector\n'
                + ('Ready' if value.get('installed') and value.get('weights_verified') else 'Setup or verification required')
                + '\nModel: ' + str(value.get('model', 'Not reported'))
                + '\nDevice: ' + str(value.get('device', 'Not reported'))
                + '\n\nThis detector finds person-shaped regions. It does not search for names, identities or contacts.'
                + '\n\n' + str(value.get('limitations', '')))
    rows = value.get('people', [])
    lines = [f"{len(rows)} person region(s) returned in a {value.get('width', '?')} × {value.get('height', '?')} image.",
             'Detection confidence measures the person class, not who someone is.', '']
    for index, person in enumerate(rows):
        score = person.get('score')
        percentage = (f'{score * 100:.2f}%' if type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1
                      else 'Not available')
        box = person.get('box', [])
        lines += [f'Detection {index + 1}: {percentage} detector confidence',
                  'Box (left, top, right, bottom): ' + ', '.join(map(str, box)), '']
    if not rows:
        lines.append('No region passed this threshold. This does not establish that nobody is present.')
    lines += [f"Threshold: {value.get('threshold', '?')} · Device: {value.get('device', '?')} · Local inference",
              'Model: ' + str(value.get('model', 'Not reported')), '',
              'No identity matching or internet search was performed.',
              'Use a known name and organization for professional research, or Image source checks for exact-image provenance.',
              str(value.get('limitations', ''))]
    return '\n'.join(lines)
