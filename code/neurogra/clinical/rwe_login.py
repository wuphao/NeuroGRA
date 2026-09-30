"""Refresh a local RWE token; the account password is never persisted."""
import argparse
import getpass
from pathlib import Path

from .rwe import local_settings


def main():
    import requests
    parser = argparse.ArgumentParser(description='Refresh local RWE API credentials')
    parser.add_argument('--account', required=True, help='RWE login phone/account')
    parser.add_argument('--settings', default='.env.rwe.local')
    args = parser.parse_args()
    path = Path(args.settings)
    settings = local_settings(path)
    try:
        response = requests.post(settings.get('RWE_API_BASE_URL', 'http://localhost:8081').rstrip('/') + '/account/login',
            json={'phone': args.account, 'password': getpass.getpass('RWE password: ')},
            timeout=20, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('HTTP ' + str(response.status_code))
        payload = response.json()
        token = (payload.get('data') or {}).get('token')
        if payload.get('code') != 0 or not isinstance(token, str) or not token or '\n' in token or '\r' in token:
            raise ValueError('invalid login response')
    except (requests.RequestException, ValueError, AttributeError):
        raise SystemExit('RWE login failed; check service and account credentials.') from None
    lines = path.read_text(encoding='utf-8-sig').splitlines() if path.exists() else []
    lines = [line for line in lines if line.split('=', 1)[0].strip() != 'RWE_API_TOKEN']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines + ['RWE_API_TOKEN=' + token]) + '\n', encoding='utf-8')
    print('RWE login successful; token saved locally. Password was not stored.')


if __name__ == '__main__':
    main()
