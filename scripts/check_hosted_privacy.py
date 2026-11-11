"""Interactive hosted checks. Default: application GET requests only, plus Auth sign-in.
No credentials, IDs, response bodies or receipt bytes are saved in the report.
Optional settings PATCH probes require disposable test households and explicit opt-in.
Python standard library only. Run from the Bonwise repository root.
"""
import argparse
import base64
from datetime import datetime, timedelta
from getpass import getpass
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID
from zoneinfo import ZoneInfo

LIMIT = 2_000_000


class SetupError(ValueError):
    """Only static, credential-free setup messages may use this class."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def origin(value):
    p = urlparse(value.strip().rstrip('/'))
    if (p.scheme not in {'https', 'http'} or not p.hostname or p.username or p.password
            or p.query or p.fragment or p.path not in {'', '/'}
            or p.scheme == 'http' and p.hostname not in {'localhost', '127.0.0.1', '::1'}):
        raise SetupError('Use a trusted HTTPS origin, or localhost HTTP, without a path or credentials.')
    return f'{p.scheme}://{p.netloc}'


def public_config():
    """Read only the two named PUBLIC frontend values; no evaluation/interpolation."""
    values = {}
    p = Path('frontend/.env.local')
    if p.is_file():
        for line in p.read_text().splitlines():
            match = re.match(r'^\s*(VITE_SUPABASE_URL|VITE_SUPABASE_PUBLISHABLE_KEY|VITE_SUPABASE_ANON_KEY)\s*=\s*(.*?)\s*$', line)
            if match:
                values[match[1]] = match[2].strip().strip('\"\'')
    return values


def public_key(value):
    if value.startswith('sb_publishable_'):
        return value
    if value.startswith('sb_secret_'):
        raise SetupError('Never use a Supabase secret/service-role key here. Use the frontend publishable/anon key.')
    try:
        part = value.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        if claims.get('role') == 'anon':
            return value
    except (ValueError, IndexError, UnicodeDecodeError):
        pass
    raise SetupError('Expected the frontend Supabase publishable key or legacy anon JWT key.')


def uuid_text(value):
    return str(UUID(str(value)))


class Client:
    def __init__(self):
        self.opener = build_opener(NoRedirect())

    def request(self, url, token=None, method='GET', body=None, extra=None):
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        headers.update(extra or {})
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        try:
            response = self.opener.open(Request(url, headers=headers, method=method, data=data), timeout=20)
        except HTTPError as exc:
            response = exc
        except (URLError, TimeoutError, OSError):
            return None, {}, None
        with response:
            raw = response.read(LIMIT + 1)
            result = None
            if len(raw) <= LIMIT and 'json' in response.headers.get('Content-Type', ''):
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    pass
            # Binary receipt bytes are discarded. No response is printed or saved.
            return response.code, {k.lower(): v for k, v in response.headers.items()}, result


class Audit:
    def __init__(self, client, api):
        self.client, self.api, self.rows = client, api, []

    def record(self, name, state, status=None, reason=None):
        row = dict(check=name, result=state, http_status=status)
        if reason:
            row['reason'] = reason
        self.rows.append(row)
        print(f'[{state}] {name}' + (f' — HTTP {status}' if status is not None else ''))

    def get(self, name, path, token, allowed, check_cache=True, baseline=True):
        if not baseline:
            self.record(name, 'BLOCKED', reason='The corresponding owner baseline failed; denial is inconclusive.')
            return False, None
        status, headers, data = self.client.request(self.api + path, token)
        good = status in allowed
        if good and allowed == {200} and not path.endswith("/receipt") and not isinstance(data, (dict, list)):
            good = False
        self.record(name, 'PASS' if good else 'FAIL', status)
        if good and check_cache:
            no_store = 'no-store' in headers.get('cache-control', '').lower()
            self.record(name + ':cache_no_store', 'PASS' if no_store else 'FAIL', status)
        return good, data

    def resource(self, name, owner, other, suffix):
        home = '/households/' + owner['household']
        other_home = '/households/' + other['household']
        ok, data = self.get(name + ':owner', home + suffix, owner['token'], {200})
        self.get(name + ':other_account', home + suffix, other['token'], {403, 404}, baseline=ok)
        self.get(name + ':no_token', home + suffix, None, {401, 403}, baseline=ok)
        self.get(name + ':wrong_parent', other_home + suffix, other['token'], {403, 404}, baseline=ok)
        return data if ok else None


def login(client, api, supabase, key, label):
    print(f'\nAccount {label}: use a separate test account. Password is hidden.')
    email = input('Email: ').strip()
    password = getpass('Password: ')
    status, _, auth = client.request(supabase + '/auth/v1/token?grant_type=password', method='POST',
        body={'email': email, 'password': password}, extra={'apikey': key})
    del email, password
    if status != 200 or not isinstance(auth, dict) or not auth.get('access_token'):
        raise SetupError(f'Account {label} sign-in failed (HTTP {status}). Check account/project details; no response body shown.')
    token = auth['access_token']
    del auth
    status, _, me = client.request(api + '/me', token)
    if status != 200 or not isinstance(me, dict) or not me.get('households'):
        raise SetupError(f'Account {label} /me failed or has no household (HTTP {status}). Check the deployed API.')
    return {'token': token, 'user': uuid_text(me['user_id']), 'households': me['households']}


def choose_household(account, other, label):
    other_ids = {uuid_text(h['id']) for h in other['households']}
    choices = [h for h in account['households'] if uuid_text(h['id']) not in other_ids]
    if not choices:
        raise SetupError(f'Account {label} needs a household the other account does not belong to.')
    for i, h in enumerate(choices, 1):
        print(f'{i}. {h.get("name", "Household")}')
    index = 0 if len(choices) == 1 else int(input(f'Choose account {label} household number: ')) - 1
    if not 0 <= index < len(choices):
        raise SetupError('Invalid household selection.')
    account['household'] = uuid_text(choices[index]['id'])
    print(f'Account {label}: separate household selected. IDs will not be written to the report.')


def run_checks(audit, a, b):
    today = datetime.now(ZoneInfo('Europe/Berlin')).date()
    monday = today - timedelta(days=today.weekday())
    audit.get('authentication:missing_token', '/me', None, {401, 403}, check_cache=False)
    audit.get('authentication:invalid_token', '/me', 'invalid-test-token', {401, 403}, check_cache=False)
    paths = [
        ('household', ''), ('settings', '/settings'), ('saved_needs', '/upcoming-needs'),
        ('purchases', '/purchases?limit=5&offset=0'), ('budgets', '/budgets'),
        ('plans', f'/weekly-plans?week_start={monday}'),
        ('spending_summary', f'/spending-summary?start_date={today}&end_date={today}'),
        ('calendar', f'/activity-calendar?start_date={monday}&end_date={monday + timedelta(days=6)}'),
        ('home', '/shopping-overview'), ('offer_options', '/offer-options'),
        ('offer_recommendations', '/offers/recommendations')]
    for label, owner, other in [('A', a, b), ('B', b, a)]:
        for name, suffix in paths:
            stem = f'{name}:{label}'
            path = '/households/' + owner['household'] + suffix
            own, data = audit.get(stem + ':own', path, owner['token'], {200})
            audit.get(stem + ':cross', path, other['token'], {403, 404}, baseline=own)
            audit.get(stem + ':no_token', path, None, {401, 403}, baseline=own)
            if own and isinstance(data, dict):
                if name == 'purchases':
                    rows = data.get('purchases', [])
                    if not rows:
                        audit.record(f'purchase_details:{label}', 'SKIP', reason='No purchase available. Add a synthetic purchase then rerun.')
                    else:
                        ident = uuid_text(rows[0]['id'])
                        detail = audit.resource(f'purchase_details:{label}', owner, other, '/purchases/' + ident)
                        if isinstance(detail, dict) and detail.get('receipt_available'):
                            audit.resource(f'receipt:{label}', owner, other, '/purchases/' + ident + '/receipt')
                        else:
                            audit.record(f'receipt:{label}', 'SKIP', reason='The sampled purchase has no attached receipt. Check a known receipt manually or rerun with one among latest purchases.')
                if name in {'budgets', 'plans'}:
                    field = 'budgets' if name == 'budgets' else 'plans'
                    rows = data.get(field, [])
                    if rows:
                        ident = uuid_text(rows[0]['id'])
                        route = '/budgets/' if name == 'budgets' else '/weekly-plans/'
                        audit.resource(f'{name}_detail:{label}', owner, other, route + ident)
                    else:
                        audit.record(f'{name}_detail:{label}', 'SKIP', reason='No existing object in this sample; verify manually with a synthetic object.')


def probe_settings(audit, accounts):
    print('\nOPTIONAL WRITE PROBE: uses a valid PATCH body copied from owner settings, changing no field values.')
    print('If authorisation is broken, it CAN still increment a household version. Use disposable TEST households only.')
    if input('Type TEST HOUSEHOLDS to run, or press Enter to skip: ').strip() != 'TEST HOUSEHOLDS':
        audit.record('settings_write_probe', 'SKIP', reason='Not confirmed. Hosted write isolation remains unverified by this runner.')
        return
    a, b = accounts
    for label, owner, outsider in [('A', a, b), ('B', b, a)]:
        path = '/households/' + owner['household'] + '/settings'
        status, _, settings = audit.client.request(audit.api + path, owner['token'])
        if status != 200 or not isinstance(settings, dict) or not settings.get('can_edit'):
            audit.record(f'settings_write:{label}', 'BLOCKED', status, 'Owner baseline unavailable or selected account cannot edit.')
            continue
        body = dict(expected_version=settings['version'], name=settings['name'],
            shopping_day=settings.get('shopping_day'), setup_completed=settings.get('setup_completed', False))
        status, _, _ = audit.client.request(audit.api + path, outsider['token'], method='PATCH', body=body)
        if status not in {403, 404}:
            audit.record(f'settings_write:{label}', 'FAIL', status, 'Unexpected result. Stop and inspect test household; no automatic rollback attempted.')
            return
        status2, _, after = audit.client.request(audit.api + path, owner['token'])
        unchanged = status2 == 200 and after == settings
        audit.record(f'settings_write:{label}', 'PASS' if unchanged else 'FAIL', status,
            None if unchanged else 'Owner settings changed or could not be verified after denied request.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', default='https://bonwise-api.vercel.app')
    p.add_argument('--out', default='validation/hosted-privacy-results.json')
    p.add_argument('--probe-settings-write', action='store_true', help='Opt-in valid-body foreign settings PATCH; disposable test households only.')
    args = p.parse_args()
    audit = None
    try:
        api = origin(args.base_url)
        print('Backend target:', api)
        values = public_config()
        source = values.get('VITE_SUPABASE_URL') or input('Supabase project URL (same project as deployed backend): ').strip()
        supabase = origin(source)
        key = public_key(values.get('VITE_SUPABASE_PUBLISHABLE_KEY') or values.get('VITE_SUPABASE_ANON_KEY') or getpass('Frontend publishable/anon key (NOT service role): ').strip())
        print('Auth project:', supabase)
        print('Confirm these match the DEPLOYED application; local config may point to a different project.')
        if input('Continue? Type yes: ').strip().lower() != 'yes':
            return 1
        client = Client()
        a = login(client, api, supabase, key, 'A')
        b = login(client, api, supabase, key, 'B')
        if a['user'] == b['user']:
            raise SetupError('Use two different accounts, not two sessions of the same user.')
        choose_household(a, b, 'A')
        choose_household(b, a, 'B')
        audit = Audit(client, api)
        run_checks(audit, a, b)
        if args.probe_settings_write:
            probe_settings(audit, (a, b))
        else:
            audit.record('hosted_write_isolation', 'SKIP', reason='Default mode issues application GET requests only. Optional settings probe covers settings only.')
        for area in ['database_rls_and_grants', 'expired_valid_token', 'direct_storage_access_and_url_expiry',
                     'draft_object_access', 'private_profile_access', 'browser_cache_and_logout',
                     'ai_payload_logs_memory_and_injection', 'financial_writes_and_concurrency', 'rights_and_retention']:
            audit.record('manual:' + area, 'NOT_TESTED', reason='Follow the runbook; this runner does not verify this area.')
    except SetupError as exc:
        print("[STOP]", str(exc))
        return 1
    except (ValueError, KeyError, TypeError, EOFError):
        print('[STOP] Configuration, sign-in, selection or response format failed. Check the account/project and deployed routes. No raw exception or response is printed.')
        return 1
    except KeyboardInterrupt:
        print('\nStopped. No credentials or response bodies saved.')
        return 1
    counts = {s: sum(r['result'] == s for r in audit.rows) for s in ['PASS', 'FAIL', 'BLOCKED', 'SKIP', 'NOT_TESTED']}
    report = dict(scope='Hosted sampled GET access/cache checks; optional settings-only PATCH probe. Not a full security audit or GDPR assessment.',
        counts=counts, checks=audit.rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print('\nSummary:', json.dumps(counts), '\nReport:', str(out))
    print('SKIP / BLOCKED / NOT_TESTED are not passes. No purchase, receipt, budget or plan was created by this runner.')
    return 1 if counts['FAIL'] or counts['BLOCKED'] else 0


if __name__ == '__main__':
    sys.exit(main())
