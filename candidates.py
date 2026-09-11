"""Construct promising labels and retain a bounded, scored selection."""

import heapq
from datetime import date, timedelta
from itertools import combinations, islice, product

import scoring


def labels(pattern, length, start=None, end=None):
    """Construct one family directly, preserving all leading zeros."""
    if pattern == "repeat":
        for width in range(1, length):
            if length % width == 0:
                for number in range(10**width):
                    yield f"{number:0{width}d}" * (length // width)
    elif pattern == "palindrome":
        width = (length + 1) // 2
        for number in range(10**width):
            half = f"{number:0{width}d}"
            yield half + (half[:-1] if length % 2 else half)[::-1]
    elif pattern == "sequence":
        for digits in ("0123456789", "9876543210"):
            for index in range(11 - length):
                yield digits[index:index + length]
    elif pattern == "pair":
        if length % 2 == 0:
            width = length // 2
            for number in range(10**width):
                yield "".join(digit * 2 for digit in f"{number:0{width}d}")
    elif pattern == "round":
        for digit in "123456789":
            yield digit + "0" * (length - 1)
    elif pattern == "chunks":
        for first in "0123456789":
            for second in "0123456789":
                if first != second:
                    for split in range(2, length - 1):
                        yield first * split + second * (length - split)
    elif pattern == "date":
        for offset in range((end - start).days + 1):
            current = start + timedelta(days=offset)
            label = f"{current.year:04d}{current.month:02d}{current.day:02d}"
            yield label if length == 8 else label[2:]


def near_repeats(length):
    for width in (1,2,3):
        if length % width:
            continue
        for value in range(10**width):
            seed = f'{value:0{width}d}' * (length//width)
            for position in range(length):
                for digit in '0123456789':
                    if digit != seed[position]:
                        yield seed[:position] + digit + seed[position+1:]


def counting_blocks(length):
    for width in (2,3):
        if length % width or length//width < 3:
            continue
        count = length//width
        for start in range(10**width):
            for step in (1,-1):
                if 0 <= start+step*(count-1) < 10**width:
                    yield ''.join(f'{start+step*i:0{width}d}' for i in range(count))


def consecutive_runs(length):
    for width in range((2*length+2)//3, length+1):
        for sequence in labels('sequence', width):
            for position in range(length-width+1):
                for value in range(10**(length-width)):
                    other = f'{value:0{length-width}d}' if width < length else ''
                    yield other[:position] + sequence + other[position:]


def zero_endings(length):
    for width in range(1, length-(2*length+2)//3+1):
        for value in range(1,10**width):
            prefix = f'{value:0{width}d}'
            if prefix[-1] != '0':
                yield prefix + '0'*(length-width)


def compact_digits(length, size):
    for alphabet in combinations('0123456789', size):
        for digits in product(alphabet, repeat=length):
            if len(set(digits)) == size:
                yield ''.join(digits)


def sources(length, patterns=None, start=None, end=None):
    for pattern in patterns or ('repeat','palindrome','pair','chunks','sequence','round',
                                'near_repeat','counting_blocks','consecutive_run','date','constant'):
        if pattern == 'date':
            if length not in (6,8):
                continue
            yield pattern, labels('date',length,start or date(2000 if length == 6 else 1900,1,1),end or date(2099,12,31))
        elif pattern == 'near_repeat':
            yield pattern, near_repeats(length)
        elif pattern == 'counting_blocks':
            yield pattern, counting_blocks(length)
        elif pattern == 'consecutive_run':
            yield pattern, consecutive_runs(length)
        elif pattern == 'constant':
            yield pattern, (value[:length] for value in scoring.CONSTANTS.values())
        elif pattern == 'round':
            yield pattern, zero_endings(length)
        elif pattern == 'uniform':
            yield pattern, (digit*length for digit in '0123456789')
        elif pattern == 'digit_diversity':
            for size in (1,2,3):
                yield pattern, compact_digits(length,size)
        elif pattern == 'stepping_pairs':
            if length % 2 == 0:
                yield pattern, (''.join(c*2 for c in label) for label in labels('sequence',length//2))
        else:
            yield pattern, labels(pattern,length)


def select(length, keep, *, patterns=None, explicit=(), prefix='', suffix='', contains='',
           no_leading_zero=False, max_generated=None, min_score=1, start=None, end=None):
    heap, retained = [], set()
    examined, capped = 0, False
    work = [('explicit', iter(explicit))]
    if patterns != []:
        work.extend(sources(length,patterns,start,end))
    # Low-alphabet fallbacks fill a sparse selection; don't enumerate millions of
    # plain low-score labels when the structured pool already meets the budget.
    if patterns is None:
        work.extend((f'compact_{size}', compact_digits(length,size)) for size in (1,2,3))
    for source, stream in work:
        if source.startswith('compact_') and len(heap) == keep and heap[0][0] > {1:20,2:14,3:6}[int(source[-1])]:
            continue
        if max_generated is not None:
            stream = islice(stream, max(0, max_generated-examined))
        for label in stream:
            examined += 1
            if (len(label) != length or label in retained or not label.startswith(prefix or '')
                    or not label.endswith(suffix or '') or (contains or '') not in label
                    or (no_leading_zero and label.startswith('0'))):
                continue
            points = scoring.score(label)
            if points < min_score:
                continue
            # Integer is only a tie-break key within one length, never an identity.
            item = (points, -int(label), label)
            if len(heap) < keep:
                heapq.heappush(heap,item)
                retained.add(label)
            elif item > heap[0]:
                old = heapq.heapreplace(heap,item)
                retained.remove(old[2])
                retained.add(label)
        if max_generated is not None and examined >= max_generated:
            capped = True
            break
    rows = []
    for rank, (_,_,label) in enumerate(sorted(heap,reverse=True),1):
        row = scoring.score(label,explain=True)
        row['rank'] = rank
        rows.append(row)
    return rows, {'examined':examined,'capped':capped,'retained':len(rows),
                  'cutoff': rows[-1]['score'] if rows else None}
