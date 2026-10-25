"""Conservative local checks for teaching questions whose numeric meaning is explicit."""
import re


def place_value_check(question):
    """Check swapping two unique digits; ambiguous/repeated positions stay unassessed."""
    match = re.search(r'(?i)\bswap\s+(?:the\s+)?([0-9])\s+and\s+(?:the\s+)?([0-9])\s+in\s+["\']?([0-9]{2,12})(?![0-9.])', question)
    if not match:
        return None
    first, second, number = match.groups()
    if first == second or number.count(first) != 1 or number.count(second) != 1:
        return None
    positions = {0: 'ones', 1: 'tens', 2: 'hundreds', 3: 'thousands', 4: 'ten-thousands', 5: 'hundred-thousands'}
    def place(digit):
        exponent = len(number) - 1 - number.index(digit)
        return positions.get(exponent, f'10^{exponent}')
    swapped = number.translate(str.maketrans({first: second, second: first}))
    return {'hint': f'The {first} is in the {place(first)} place and the {second} is in the {place(second)} place. '
                    'Swap those positions, keeping the other digits fixed, then compare the two numbers.',
            'evidence': f'Local place-value calculation: {number} becomes {swapped}; '
                        f'signed change = {int(swapped)-int(number)}. '
                        f'{first}: {place(first)} → {place(second)}; {second}: {place(second)} → {place(first)}.'}
