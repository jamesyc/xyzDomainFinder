"""Explainable, versioned integer scores for numeric .xyz labels."""

from collections import Counter
from datetime import date
from itertools import groupby

VERSION = 'numeric-interest-v1'
CONSTANTS = {'pi': '314159265', 'e': '271828182', 'golden ratio': '161803398'}
RULES = {
    'uniform': ('structure', 60, 'Uniform digits'),
    'repeat': ('structure', 45, 'Repeated block'),
    'palindrome': ('structure', 35, 'Palindrome'),
    'pair': ('structure', 30, 'Paired digits'),
    'chunks': ('structure', 20, 'Repeated-digit chunks'),
    'near_repeat': ('structure', 20, 'Near repetition'),
    'sequence': ('progression', 45, 'Whole sequence'),
    'counting_blocks': ('progression', 35, 'Counting blocks'),
    'stepping_pairs': ('progression', 30, 'Stepping pairs'),
    'consecutive_run': ('progression', 0, 'Consecutive run'),
    'digit_diversity': ('simplicity', 0, 'Few distinct digits'),
    'round': ('roundness', 25, 'Long zero ending'),
    'date': ('meaning', 12, 'Calendar date'),
    'constant': ('meaning', 55, 'Mathematical constant'),
}
PROFILE = {'version': VERSION, 'rules': RULES, 'diversity_points': {1: 20, 2: 14, 3: 6},
           'run_points': 'floor(30 * run_length / length), coverage >= ceil(2*length/3)',
           'constants': CONSTANTS, 'dates': {'YYMMDD': '2000–2099', 'YYYYMMDD': '1900–2099'}}


def is_sequence(values):
    return len(values) >= 3 and (all(b-a == 1 for a,b in zip(values, values[1:]))
                                 or all(b-a == -1 for a,b in zip(values, values[1:])))


def score(label, explain=False):
    """Sum the strongest rule per family; optionally include every matched rule."""
    n, digits = len(label), set(label)
    awards, matches = {}, []

    def add(rule, evidence, points=None):
        family, base, title = RULES[rule]
        points = base if points is None else points
        awards[family] = max(awards.get(family, 0), points)
        if explain:
            matches.append({'id': rule, 'family': family, 'title': title, 'points': points,
                            'awarded': 0, 'evidence': evidence})

    distinct = len(digits)
    if distinct <= 3:
        add('digit_diversity', ', '.join(sorted(digits)), {1:20, 2:14, 3:6}[distinct])
    if distinct == 1:
        add('uniform', f'{label[0]} repeated {n} times')
    else:
        for width in range(2, n//2 + 1):
            if n % width == 0 and label[:width] * (n//width) == label:
                add('repeat', f'{label[:width]} × {n//width}')
                break
    if label == label[::-1]:
        add('palindrome', f'{label} reads the same in reverse')
    paired = n % 2 == 0 and all(label[i] == label[i+1] for i in range(0,n,2))
    if paired:
        add('pair', ' / '.join(label[i:i+2] for i in range(0,n,2)))
        if is_sequence([int(c) for c in label[::2]]):
            add('stepping_pairs', ' → '.join(label[::2]))
    if distinct <= 3 and (explain or awards.get('structure', 0) < 20):
        runs = [c * len(list(g)) for c,g in groupby(label)]
        if 2 <= len(runs) <= 3 and all(len(run) >= 2 for run in runs):
            add('chunks', ' / '.join(runs))
    if explain or awards.get('structure', 0) < 20:
        for width in range(1,4):
            if n % width:
                continue
            block = ''.join(min(Counter(label[i::width]), key=lambda c: (-label[i::width].count(c),c)) for i in range(width))
            target = block * (n//width)
            if sum(a != b for a,b in zip(label,target)) == 1:
                add('near_repeat', f'One digit away from {target}')
                break
    if distinct >= (2*n+2)//3:
        best, start = 1, 0
        for direction in (1,-1):
            run, run_start = 1, 0
            for i in range(1,n):
                if ord(label[i])-ord(label[i-1]) == direction:
                    run += 1
                else:
                    run, run_start = 1, i
                if run > best:
                    best, start = run, run_start
        if best == n:
            add('sequence', ' → '.join(label))
        if best >= (2*n+2)//3:
            add('consecutive_run', label[start:start+best], 30*best//n)
    for width in (2,3):
        if n % width == 0 and n//width >= 3:
            blocks = [label[i:i+width] for i in range(0,n,width)]
            if is_sequence([int(block) for block in blocks]):
                add('counting_blocks', ' → '.join(blocks))
                break
    zeroes = n-len(label.rstrip('0'))
    if (2*n+2)//3 <= zeroes < n:
        add('round', f'{zeroes} trailing zeros')
    if n in (6,8):
        year = 2000 + int(label[:2]) if n == 6 else int(label[:4])
        month, day = int(label[-4:-2]), int(label[-2:])
        if 1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31:
            try:
                stamp = date(year,month,day)
                add('date', f'{stamp.isoformat()} ({"YYMMDD" if n == 6 else "YYYYMMDD"})')
            except ValueError:
                pass
    for name, value in CONSTANTS.items():
        if label == value[:n]:
            add('constant', f'{name}: first {n} digits, decimal point removed')
    if not explain:
        return sum(awards.values())
    for family, amount in awards.items():
        winner = min((m for m in matches if m['family'] == family and m['points'] == amount), key=lambda m:m['id'])
        winner['awarded'] = amount
    matches.sort(key=lambda match: (-match['awarded'], match['family'], match['id']))
    return {'domain': f'{label}.xyz', 'length': n, 'score': sum(awards.values()),
            'reasons': ';'.join(m['id'] for m in matches), 'properties': matches}
