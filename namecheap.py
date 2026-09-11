"""Bounded, read-only Namecheap availability requests."""

import ipaddress
import math
import os
import re
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

ENDPOINT = 'https://api.namecheap.com/xml.response'
TRANSIENT_API_CODES = {'3031510', '3011511'}


class APIError(Exception):
    def __init__(self, code, retryable=False, delay=None):
        super().__init__(code)
        self.retryable, self.delay = retryable, delay


class BudgetEnded(Exception):
    pass


def credentials():
    names = ('NAMECHEAP_USERNAME', 'NAMECHEAP_API_KEY', 'NAMECHEAP_CLIENT_IP')
    missing = [name for name in names if not os.getenv(name, '').strip()]
    if missing:
        raise APIError('Missing environment variables: ' + ', '.join(missing))
    username, key, address = (os.environ[name].strip() for name in names)
    try:
        ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        raise APIError('NAMECHEAP_CLIENT_IP must be the allowlisted public IPv4 address') from None
    return {'ApiUser': username, 'UserName': username, 'ApiKey': key, 'ClientIp': address}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fresh(row, minutes, now=None):
    if row['provider'] != 'namecheap' or row['availability'] not in ('available', 'unavailable'):
        return False
    try:
        stamp = datetime.fromisoformat(row['checked_at'])
        if stamp.tzinfo is None:
            return False
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        return 0 <= age < minutes * 60
    except (TypeError, ValueError):
        return False


def money(value):
    try:
        amount = Decimal(value)
        return str(amount) if amount.is_finite() and amount >= 0 else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def retry_after(value):
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    except (ValueError, TypeError):
        pass
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, (stamp - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def safe_code(value):
    return value if value and re.fullmatch(r'[0-9]{1,10}', value) else 'invalid_code'


def unknown(domain, error):
    return {'domain': domain, 'availability': 'unknown', 'checked_at': timestamp(),
            'provider': 'namecheap', 'registration_price': None, 'renewal_price': None,
            'currency': None, 'premium': None, 'term_years': None, 'icann_fee': None,
            'eap_fee': None, 'quote_note': None, 'check_error': error}


def parse_response(body, domains):
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise APIError('invalid_xml', True) from None
    if root.tag.split('}')[-1] != 'ApiResponse':
        raise APIError('invalid_response', True)
    errors = root.findall('.//{*}Error')
    if errors or root.get('Status') != 'OK':
        codes = [safe_code(node.get('Number')) for node in errors] or ['invalid_status']
        raise APIError('api_' + '_'.join(codes), all(code in TRANSIENT_API_CODES for code in codes))
    command = root.find('{*}RequestedCommand')
    if command is None or command.text != 'namecheap.domains.check':
        raise APIError('unexpected_command', True)
    result = {domain: unknown(domain, 'missing_result') for domain in domains}
    seen = set()
    for node in root.findall('.//{*}DomainCheckResult'):
        domain = node.get('Domain', '').lower()
        if domain not in result:
            raise APIError('unexpected_domain', True)
        if domain in seen:
            result[domain] = unknown(domain, 'duplicate_result')
            continue
        seen.add(domain)
        if node.get('ErrorNo') != '0':
            result[domain]['check_error'] = 'domain_' + safe_code(node.get('ErrorNo'))
            continue
        available = node.get('Available', '').lower()
        if available not in ('true', 'false'):
            result[domain]['check_error'] = 'invalid_availability'
            continue
        row = unknown(domain, None)
        row['availability'] = 'available' if available == 'true' else 'unavailable'
        row['premium'] = {'true': 1, 'false': 0}.get(node.get('IsPremiumName', '').lower())
        row['icann_fee'] = money(node.get('IcannFee'))
        row['eap_fee'] = money(node.get('EapFee'))
        row['quote_note'] = 'Standard per-domain price not supplied; verify at checkout.'
        if row['premium'] == 1:
            row['registration_price'] = money(node.get('PremiumRegistrationPrice'))
            row['renewal_price'] = money(node.get('PremiumRenewalPrice'))
            row['quote_note'] = 'Premium amounts reported; currency, term and complete fees are unconfirmed.'
        # The documented check response has no currency/term. Never infer them
        # from generic TLD prices or make an incomplete quote pass a price ceiling.
        result[domain] = row
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise APIError('redirect_refused')


@contextmanager
def network_deadline(seconds):
    def expired(*_):
        raise TimeoutError

    previous = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if old_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, *old_timer)


class Client:
    def __init__(self, max_requests, deadline):
        self.auth = credentials()
        if not hasattr(signal, 'setitimer'):
            raise APIError('Live checks currently require macOS or Linux for enforced deadlines')
        self.max_requests, self.deadline = max_requests, deadline
        self.requests = 0
        self.next_request = 0.0
        self.opener = urllib.request.build_opener(NoRedirect)

    def wait(self, delay):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or delay >= remaining:
            raise BudgetEnded('deadline')
        if delay > 0:
            time.sleep(delay)

    def check(self, domains):
        if not 1 <= len(domains) <= 50:
            raise ValueError('A Namecheap batch must contain 1–50 names')
        if self.requests >= self.max_requests:
            raise BudgetEnded('request_budget')
        self.wait(max(0, self.next_request - time.monotonic()))
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetEnded('deadline')
        self.requests += 1
        self.next_request = time.monotonic() + 3.1
        query = urllib.parse.urlencode({**self.auth, 'Command': 'namecheap.domains.check',
                                       'DomainList': ','.join(domains)})
        request = urllib.request.Request(ENDPOINT + '?' + query, headers={'User-Agent': 'xyzDomainFinder/0.1'})
        try:
            with network_deadline(min(10, remaining)):
                with self.opener.open(request, timeout=min(10, remaining)) as response:
                    body = response.read(1_000_001)
                    if len(body) > 1_000_000:
                        raise APIError('response_too_large', True)
            return parse_response(body, domains)
        except urllib.error.HTTPError as error:
            status = error.code
            delay = retry_after(error.headers.get('Retry-After'))
            error.close()
            raise APIError(f'http_{status}', status in (408, 429, 500, 502, 503, 504), delay) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError('network_error', True) from None


def eligible(row, args):
    if row['availability'] != 'available':
        return False
    if args.exclude_premium and row.get('premium') != 0:
        return False
    for column, ceiling in (('registration_price', args.max_registration), ('renewal_price', args.max_renewal)):
        if ceiling is not None:
            amount = money(row.get(column))
            if (amount is None or row.get('currency') != args.currency or row.get('term_years') != 1
                    or Decimal(amount) > ceiling):
                return False
    return True


def run(database, args, domains=None, client_factory=None):
    import csv
    import random
    import sys
    import catalog

    started = time.monotonic()
    deadline = started + args.timeout
    stats = {'selected': 0, 'cached': 0, 'live_names': 0, 'available': 0,
             'unavailable': 0, 'unknown': 0, 'eligible': 0}
    client, active, status, stop = None, {}, 0, 'input_exhausted'
    selected_by_domain = {}
    writer = csv.DictWriter(sys.stdout, fieldnames=(*catalog.COLUMNS, 'cached', 'eligible'), extrasaction='ignore')

    def emit(observation, cached=False):
        row = {**selected_by_domain[observation['domain']], **observation}
        accepted = eligible(row, args)
        writer.writerow({**row, 'cached': cached, 'eligible': accepted})
        sys.stdout.flush()
        stats[row['availability']] += 1
        stats['eligible'] += accepted
        stats['cached'] += cached

    with catalog.write_lock(database):
        selected = catalog.check_selection(database, vars(args), args.limit, domains)
        selected_by_domain = {row['domain']: row for row in selected}
        stats['selected'] = len(selected)
        if not selected:
            print('No catalog candidates match; no requests made.', file=sys.stderr)
            return 0
        catalog.ensure_observation_schema(database)
        writer.writeheader()
        pending = []
        try:
            # Reuse useful observations before spending requests on unknown names.
            for row in selected:
                if not args.refresh and fresh(row, args.cache_minutes):
                    emit(row, cached=True)
                    if stats['eligible'] >= args.target:
                        stop = 'target_reached_from_cache'
                        return 0
                else:
                    pending.append(row['domain'])
            index = 0
            while index < len(pending):
                if stats['eligible'] >= args.target:
                    stop = 'target_reached'
                    break
                if stats['live_names'] >= args.max_checks:
                    stop = 'name_budget'
                    break
                if time.monotonic() >= deadline:
                    raise BudgetEnded('deadline')
                if client is None:
                    client = (client_factory or Client)(args.max_requests, deadline)
                size = min(50, args.max_checks - stats['live_names'], args.target - stats['eligible'])
                batch = pending[index:index + size]
                index += len(batch)
                active = {domain: None for domain in batch}
                counted = False
                for attempt in range(3):
                    before = client.requests
                    try:
                        response = client.check(list(active))
                        active.update(response)
                        delay = 2**attempt + random.uniform(0, 0.25)
                    except APIError as error:
                        if client.requests > before:
                            active = {domain: unknown(domain, str(error)) for domain in active}
                        delay = error.delay if error.delay is not None else 2**attempt + random.uniform(0, 0.25)
                        if not error.retryable:
                            status, stop = 1, 'provider_error'
                            print(f'Namecheap stopped: {error}. Check account/API access and IP allowlisting.', file=sys.stderr)
                    except KeyboardInterrupt:
                        if client.requests > before:
                            active = {domain: unknown(domain, 'interrupted_request') for domain in active}
                        raise
                    finally:
                        if client.requests > before:
                            if not counted:
                                stats['live_names'] += len(batch)
                                counted = True
                            active = {domain: observation or unknown(domain, 'interrupted_request')
                                      for domain, observation in active.items()}
                    observations = [observation for observation in active.values() if observation is not None]
                    catalog.save_observations(database, observations)
                    for domain, observation in list(active.items()):
                        if observation is not None and observation['availability'] != 'unknown':
                            emit(observation)
                            del active[domain]
                    if status or not active or attempt == 2:
                        break
                    if client.requests >= args.max_requests:
                        raise BudgetEnded('request_budget')
                    client.wait(delay)
                for observation in active.values():
                    if observation is not None:
                        emit(observation)
                active = {}
                if status:
                    break
            if not status and stats['eligible'] >= args.target:
                stop = 'target_reached'
        except BudgetEnded as error:
            stop = str(error)
        except APIError as error:
            status, stop = 1, 'configuration_error'
            print(f'Namecheap: {error}', file=sys.stderr)
        except KeyboardInterrupt:
            status, stop = 130, 'interrupted'
        finally:
            remaining = [observation for observation in active.values() if observation is not None]
            if remaining:
                catalog.save_observations(database, remaining)
                for observation in remaining:
                    emit(observation)
            print(f'Stop: {stop}; ' + '; '.join(f'{key}={value}' for key, value in stats.items())
                  + f'; requests={client.requests if client else 0}; elapsed={time.monotonic()-started:.2f}s',
                  file=sys.stderr, flush=True)
    return status
