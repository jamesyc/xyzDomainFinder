"""Score, collect, and browse interesting numeric .xyz domains locally."""

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import candidates
import scoring


def positive_int(value):
    number=int(value)
    if number < 1: raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def nonnegative_int(value):
    number=int(value)
    if number < 0: raise argparse.ArgumentTypeError('must be nonnegative')
    return number


def digit_filter(value):
    if not value or any(c not in '0123456789' for c in value):
        raise argparse.ArgumentTypeError('must contain ASCII digits only')
    return value


def numeric_label(value):
    label=value.strip()
    if label.lower().endswith('.xyz'): label=label[:-4]
    if not 6 <= len(label) <= 9 or any(c not in '0123456789' for c in label):
        raise ValueError(f'Invalid name {value!r}: expected 6–9 ASCII digits')
    return label


def price_limit(value):
    try:
        amount = Decimal(value)
        if amount.is_finite() and amount >= 0:
            return amount
    except InvalidOperation:
        pass
    raise argparse.ArgumentTypeError('must be a finite nonnegative amount')


def check_inputs(args):
    names = [numeric_label(value) + '.xyz' for value in args.number]
    if args.input:
        with args.input.open(encoding='utf-8-sig', newline='') as source:
            first = source.readline()
            source.seek(0)
            if next(csv.reader([first]), [''])[0] == 'domain':
                reader = csv.DictReader(source)
                for item in reader:
                    try:
                        names.append(numeric_label(item.get('domain') or '') + '.xyz')
                    except ValueError as error:
                        raise ValueError(f'{args.input}:{reader.line_num}: {error}') from error
            else:
                for line_number, line in enumerate(source, 1):
                    if line.strip():
                        try:
                            names.append(numeric_label(line) + '.xyz')
                        except ValueError as error:
                            raise ValueError(f'{args.input}:{line_number}: {error}') from error
    return list(dict.fromkeys(names)) if args.number or args.input else None


def filter_options(command):
    command.add_argument('--length',type=int,choices=range(6,10),action='append')
    command.add_argument('--pattern',choices=scoring.RULES,action='append')
    for flag in ('prefix','suffix','contains'): command.add_argument('--'+flag,type=digit_filter)
    command.add_argument('--no-leading-zero',action='store_true')
    command.add_argument('--min-score',type=nonnegative_int,default=1)


def build_parser():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    for name,description in (('build','Store a scored collection in SQLite (default: 6–9 digits)'),
                             ('generate','Print scored candidates without changing the database')):
        command=commands.add_parser(name,help=description,allow_abbrev=False)
        filter_options(command)
        command.add_argument('--number',action='append',default=[])
        command.add_argument('--input',type=Path,help='one numeric label or .xyz domain per line')
        command.add_argument('--max-generated',type=positive_int,help='optional candidate-work budget per length')
        command.add_argument('--date-start',type=date.fromisoformat)
        command.add_argument('--date-end',type=date.fromisoformat)
        if name == 'build':
            command.add_argument('--keep-per-length',type=positive_int,default=10000,help='retention budget per digit length (default: 10000)')
            command.add_argument('--database',type=Path,default=Path('domains.sqlite3'))
            command.add_argument('--replace',action='store_true',help='replace selection, preserving observations for retained names')
        else:
            command.add_argument('--limit',type=positive_int,default=50)
            command.add_argument('--format',choices=('text','csv','json'),default='text')
    score=commands.add_parser('score',allow_abbrev=False,help='Explain scores and property awards for specific names')
    score.add_argument('names',nargs='+')
    find=commands.add_parser('find',allow_abbrev=False,help='Filter the scored SQLite catalog')
    filter_options(find)
    find.add_argument('--database',type=Path,default=Path('domains.sqlite3'))
    find.add_argument('--state',choices=('unchecked','available','unavailable','unknown'))
    find.add_argument('--limit',type=positive_int,default=50)
    find.add_argument('--format',choices=('text','csv'),default='csv')
    check=commands.add_parser('check',allow_abbrev=False,help='Check a selected catalog shortlist through Namecheap')
    filter_options(check)
    check.add_argument('--database',type=Path,default=Path('domains.sqlite3'))
    check.add_argument('--number',action='append',default=[],help='explicit catalog name; may be repeated')
    check.add_argument('--input',type=Path,help='plaintext or CSV shortlist of names already in the catalog')
    check.add_argument('--state',choices=('unchecked','available','unavailable','unknown'))
    check.add_argument('--limit',type=positive_int,default=200,help='maximum candidates selected (default: 200)')
    check.add_argument('--max-checks',type=positive_int,default=50,help='maximum distinct live names (default: 50)')
    check.add_argument('--max-requests',type=positive_int,default=20,help='HTTP attempts including retries (default: 20)')
    check.add_argument('--timeout',type=positive_int,default=60,help='whole-run deadline in seconds')
    check.add_argument('--target',type=positive_int,default=10,help='stop after this many eligible available names')
    check.add_argument('--cache-minutes',type=nonnegative_int,default=15)
    check.add_argument('--refresh',action='store_true',help='force fresh observations instead of cache reuse')
    check.add_argument('--preview',action='store_true',help='show the selected names without writes, credentials, or requests')
    check.add_argument('--exclude-premium',action='store_true',help='exclude premium or unknown price classes from the available target')
    check.add_argument('--max-registration',type=price_limit,help='one-year base-price ceiling, excluding fees/taxes')
    check.add_argument('--max-renewal',type=price_limit,help='one-year base-price ceiling, excluding fees/taxes')
    check.add_argument('--currency',help='three-letter currency required when using price ceilings')
    scan=commands.add_parser('scan',allow_abbrev=False,help='Check only unchecked catalog rows, highest score first, until stopped')
    scan.add_argument('--database',type=Path,default=Path('domains.sqlite3'))
    scan.add_argument('--preview',action='store_true',help='show unchecked scope without writes or requests')
    scan.add_argument('--max-checks',type=positive_int,help='optional live-name budget for a limited run')
    scan.add_argument('--max-requests',type=positive_int,help='optional total request budget')
    scan.add_argument('--timeout',type=positive_int,help='optional whole-run time budget in seconds')
    serve=commands.add_parser('serve',allow_abbrev=False,help='Open the local catalog and selected-name checking workflow')
    serve.add_argument('--database',type=Path,default=Path('domains.sqlite3'))
    serve.add_argument('--port',type=positive_int,default=8765)
    return parser


def inputs(args):
    explicit=[numeric_label(value) for value in args.number]
    if args.input:
        with args.input.open(encoding='utf-8-sig') as source:
            for line_number,line in enumerate(source,1):
                if line.strip():
                    try: explicit.append(numeric_label(line))
                    except ValueError as error: raise ValueError(f'{args.input}:{line_number}: {error}') from error
    if args.length and any(len(label) not in args.length for label in explicit):
        raise ValueError('An explicit name conflicts with --length')
    lengths=list(dict.fromkeys(args.length or (sorted({len(v) for v in explicit}) if explicit and not args.pattern
                                              else [6,7,8,9] if args.command == 'build' else [6])))
    patterns=list(dict.fromkeys(args.pattern)) if args.pattern else ([] if args.number or args.input else None)
    if args.date_start or args.date_end:
        if not args.date_start or not args.date_end or args.date_start > args.date_end:
            raise ValueError('Provide an ordered --date-start and --date-end range')
        if not patterns or 'date' not in patterns or any(n not in (6,8) for n in lengths):
            raise ValueError('Date bounds require --pattern date and lengths 6 or 8')
    if patterns == ['pair'] and all(n%2 for n in lengths):
        raise ValueError('Paired digits require an even length')
    return explicit,lengths,patterns


def collect(args):
    explicit,lengths,patterns=inputs(args)
    rows,stats=[],{}
    keep=args.keep_per_length if args.command == 'build' else args.limit
    for length in lengths:
        start=time.perf_counter()
        selected,info=candidates.select(length,keep,patterns=patterns,explicit=explicit,
            prefix=args.prefix,suffix=args.suffix,contains=args.contains,no_leading_zero=args.no_leading_zero,
            max_generated=args.max_generated,min_score=args.min_score,start=args.date_start,end=args.date_end)
        info['elapsed_seconds']=round(time.perf_counter()-start,3)
        stats[str(length)]=info;rows.extend(selected)
        print(f'{length} digits: {info["examined"]:,} candidates considered; {len(selected):,} retained; '
              f'cutoff {info["cutoff"]}; {info["elapsed_seconds"]:.2f}s'+(' (candidate cap reached)' if info['capped'] else ''),file=sys.stderr,flush=True)
    rows.sort(key=lambda row:(-row['score'],row['length'],row['rank']))
    selection={'lengths':lengths,'patterns':patterns,'keep_per_length':keep,'min_score':args.min_score,
        'max_generated':args.max_generated,'prefix':args.prefix,'suffix':args.suffix,'contains':args.contains,
        'no_leading_zero':args.no_leading_zero,'date_start':str(args.date_start),'date_end':str(args.date_end),
        'explicit_digest':hashlib.sha256('\n'.join(explicit).encode()).hexdigest()}
    return rows,selection,stats


def main(argv=None):
    parser=build_parser();args=parser.parse_args(argv)
    try:
        if args.command == 'scan':
            import catalog
            import scan
            if args.preview:
                import rate_limit
                info = catalog.unchecked_info(args.database)
                info['estimated_seconds'] = rate_limit.estimate_seconds((info['total'] + 49) // 50)
                info['request_limits'] = dict(rate_limit.LIMITS)
                print(json.dumps(info, indent=2))
                return 0
            return scan.run(args.database, max_checks=args.max_checks, max_requests=args.max_requests, timeout=args.timeout)
        if args.command == 'check':
            import catalog
            import namecheap
            domains = check_inputs(args)
            if args.currency:
                args.currency = args.currency.upper()
                if len(args.currency) != 3 or not args.currency.isascii() or not args.currency.isalpha():
                    raise ValueError('--currency must be a three-letter code')
            if (args.max_registration is not None or args.max_renewal is not None) and not args.currency:
                raise ValueError('Price ceilings require --currency')
            if args.preview:
                selected = catalog.check_selection(args.database,vars(args),args.limit,domains)
                writer = csv.DictWriter(sys.stdout,fieldnames=(*catalog.COLUMNS,'fresh_cache'),extrasaction='ignore')
                writer.writeheader()
                writer.writerows({**row,'fresh_cache':not args.refresh and namecheap.fresh(row,args.cache_minutes)} for row in selected)
                print(f'Preview: {len(selected)} selected; live-name budget {args.max_checks}; no requests or writes.',file=sys.stderr)
                return 0
            return namecheap.run(args.database,args,domains)
        if args.command == 'score':
            labels=[numeric_label(value) for value in args.names]
            print(json.dumps([scoring.score(label,True) for label in labels],indent=2,ensure_ascii=False))
            return 0
        if args.command == 'serve':
            import viewer
            if args.port > 65535: raise ValueError('--port must be at most 65535')
            viewer.serve(args.database,args.port)
            return 0
        if args.command == 'build':
            import catalog
            rows,selection,stats=collect(args)
            created=catalog.build(args.database,rows,selection,stats,args.replace)
            print(f'{"Built" if created else "Reused"} {args.database.resolve()}: {len(rows):,} scored names. No registrar requests.',file=sys.stderr)
            return 0
        if args.command == 'find':
            import catalog
            rows=catalog.find(args.database,args)
            columns=catalog.COLUMNS
        else:
            rows,_,_=collect(args)
            rows=rows[:args.limit]
            columns=('domain','length','score','rank','reasons')
        if args.format == 'text':
            for row in rows: print(row['domain'])
        elif args.format == 'json':
            print(json.dumps(rows,indent=2,ensure_ascii=False))
        else:
            writer=csv.DictWriter(sys.stdout,fieldnames=columns,extrasaction='ignore',lineterminator='\n')
            writer.writeheader();writer.writerows(rows)
        if not rows: print('No candidates match these filters.',file=sys.stderr)
        sys.stdout.flush()
    except (ValueError,UnicodeError) as error:
        parser.error(str(error))
    except BrokenPipeError:
        sys.stdout=open(os.devnull,'w');return 0
    except (OSError,sqlite3.Error) as error:
        print(f'Error: {error}',file=sys.stderr);return 1
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
