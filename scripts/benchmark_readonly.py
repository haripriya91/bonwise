"""Read-only HTTP timing probes; never records response bodies or access tokens.

Requires curl. These are client-observed request timings, not browser LCP or
verified platform cold starts. Authenticated mode reads existing records only.
"""
import argparse
import hashlib
import getpass
import sys
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from urllib.parse import urlencode, urljoin, urlsplit
from uuid import UUID


def origin(value):
    p = urlsplit(value)
    if p.username or p.password or p.query or p.fragment or p.path not in ('', '/'):
        raise argparse.ArgumentTypeError('Supply an origin without credentials, paths or query strings.')
    if p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in ('localhost', '127.0.0.1')):
        raise argparse.ArgumentTypeError('Use HTTPS, or HTTP localhost for local testing.')
    if not p.hostname:
        raise argparse.ArgumentTypeError('A hostname is required.')
    return value.rstrip('/')


def request(url, token=None):
    # The bearer token goes through stdin, never process arguments or output.
    if token and any(c in token for c in '\r\n'):
        raise ValueError('Invalid access token.')
    with tempfile.TemporaryDirectory() as folder:
        body = Path(folder) / 'body'
        headers = Path(folder) / 'headers'
        command = ['curl', '--silent', '--show-error', '--compressed', '--request', 'GET',
                   '--max-time', '30', '--connect-timeout', '10', '--output', str(body),
                   '--dump-header', str(headers), '--write-out', '%{json}', url]
        if token:
            command += ['--header', '@-']
        p = subprocess.run(command, input=f'Authorization: Bearer {token}\n' if token else '',
                           capture_output=True, text=True)
        if p.returncode:
            return {'success': False, 'curl_exit_code': p.returncode}, b''
        data = json.loads(p.stdout)
        raw = body.read_bytes()
        h = {}
        for line in headers.read_text(errors='replace').splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                if k.lower() in ('cache-control', 'age', 'x-vercel-cache', 'server-timing', 'content-encoding'):
                    h[k.lower()] = v.strip()
        result = {name: round(data[key] * 1000, 2) for name, key in (
            ('dns_ms', 'time_namelookup'), ('connect_ms', 'time_connect'),
            ('tls_ms', 'time_appconnect'), ('ttfb_ms', 'time_starttransfer'), ('total_ms', 'time_total'))}
        result.update(status=int(data['http_code']), success=200 <= int(data['http_code']) < 300,
                      transfer_bytes=int(data['size_download']), decoded_bytes=len(raw), headers=h)
        return result, raw


def summarize(samples):
    valid = [s['total_ms'] for s in samples if s.get('success')]
    return dict(samples=len(samples), successful=len(valid),
                median_total_ms=round(median(valid), 2) if valid else None,
                max_total_ms=max(valid) if valid else None,
                p95_total_ms=sorted(valid)[math.ceil(.95 * len(valid)) - 1] if len(valid) >= 20 else None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frontend', type=origin, default='https://bonwise.vercel.app')
    p.add_argument('--api', type=origin, default='https://bonwise-api.vercel.app')
    p.add_argument('--samples', type=int, default=5)
    p.add_argument('--authenticated', action='store_true')
    p.add_argument('--ledger-only', action='store_true', help='Measure the new Home budget path without offers.')
    p.add_argument('--household-id', type=UUID)
    p.add_argument('--start-date', type=date.fromisoformat)
    p.add_argument('--end-date', type=date.fromisoformat)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not 1 <= args.samples <= 30 or not shutil.which('curl'):
        p.error('Use 1–30 samples and install curl.')
    token = os.environ.get('BONWISE_ACCESS_TOKEN') if args.authenticated else None
    if args.authenticated and sys.stdin.isatty():
        try:
            token = token or getpass.getpass('Access token (hidden): ').strip()
            args.household_id = args.household_id or UUID(input('Household UUID: ').strip())
            args.start_date = args.start_date or date.fromisoformat(input('Start date YYYY-MM-DD: ').strip())
            args.end_date = args.end_date or date.fromisoformat(input('End date YYYY-MM-DD: ').strip())
        except (EOFError, ValueError):
            p.error('Run directly in a terminal and use valid UUID/ISO dates. Do not paste the token into chat.')
    if args.authenticated and (not token or not args.household_id or not args.start_date or not args.end_date):
        p.error('Authenticated mode needs BONWISE_ACCESS_TOKEN, household ID and dates.')
    if args.authenticated and not 0 <= (args.end_date - args.start_date).days <= 92:
        p.error('Use an ordered date range of at most 93 days.')
    scenarios = [('frontend_html', args.frontend + '/', None), ('api_health', args.api + '/health', None)]
    if args.authenticated:
        root = f'{args.api}/households/{args.household_id}'
        dates = urlencode({'start_date': args.start_date, 'end_date': args.end_date})
        scenarios += [('account', args.api + '/me', token),
                      ('home_overview', root + '/shopping-overview' + ('?include_offers=false' if args.ledger_only else ''), token),
                      ('purchases_first_page', root + '/purchases?limit=5&offset=0', token),
                      ('spending_summary', root + '/spending-summary?' + dates, token),
                      ('offer_options', root + '/offer-options', token),
                      ('home_offer_recommendations', root + '/offers/recommendations', token)]
    result = dict(schema_version=1, measured_at=datetime.now(timezone.utc).isoformat(),
                  frontend_origin=args.frontend, api_origin=args.api, authenticated=args.authenticated,
                  home_overview_includes_offers=not args.ledger_only,
                  notes=['Sequential requests; a new curl process/connection per sample; no redirects followed.',
                         'First observed request is not proof of a cold start. Server-Timing is captured when supplied by the deployed API.',
                         'No browser rendering/LCP measured. p95 omitted below 20 successful samples.',
                         'Authenticated response bodies and identifiers are not saved. No model calls or writes.'],
                  scenarios={})
    html = b''
    for label, url, bearer in scenarios:
        samples = []
        for index in range(args.samples):
            row, body = request(url, bearer)
            row['observation'] = index + 1
            samples.append(row)
            if label == 'frontend_html' and row.get('success'):
                html = body
        result['scenarios'][label] = dict(summary=summarize(samples), observations=samples)
        print(label, result['scenarios'][label]['summary'], flush=True)
    result['assets'] = []
    for asset in sorted(set(re.findall(r'(?:src|href)=["\'](/assets/[^"\']+\.(?:js|css))["\']', html.decode(errors='replace')))):
        row, body = request(urljoin(args.frontend + '/', asset))
        row.update(path=asset, sha256=hashlib.sha256(body).hexdigest() if row.get('success') else None)
        result['assets'].append(row)
    # This is an auth guard availability check, never a database latency metric.
    result['unauthenticated_me'], _ = request(args.api + '/me')
    result['unauthenticated_me']['expected_status'] = 401
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('Saved:', args.output)


if __name__ == '__main__':
    main()
