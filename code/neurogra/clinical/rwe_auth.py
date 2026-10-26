"""Shared RWE authentication. Errors deliberately omit remote bodies and secrets."""


class AuthenticationFailure(ValueError):
    pass


def obtain_token(http, base_url, account, password, *, timeout=30):
    if not isinstance(account, str) or not account.strip() or not password:
        raise AuthenticationFailure('RWE 登录凭据缺失：配置 RWE_LOGIN_ACCOUNT 和 RWE_LOGIN_PASSWORD。')
    try:
        response = http.post(base_url.rstrip('/') + '/account/login',
            json={'phone': account, 'password': password}, timeout=timeout, allow_redirects=False)
        if response.status_code != 200:
            raise AuthenticationFailure('RWE 登录失败：HTTP ' + str(response.status_code))
        payload = response.json()
    except AuthenticationFailure:
        raise
    except http.RequestException:
        raise AuthenticationFailure('RWE 登录服务不可用，请检查服务和地址。') from None
    except ValueError:
        raise AuthenticationFailure('RWE 登录返回无效数据。') from None
    data = payload.get('data') if isinstance(payload, dict) else None
    token = data.get('token') if isinstance(data, dict) else None
    if not isinstance(payload, dict) or payload.get('code') != 0 or not isinstance(token, str) or not token.strip() or '\n' in token or '\r' in token:
        raise AuthenticationFailure('RWE 登录失败：凭据无效或响应缺少有效 Token。')
    return token.strip()
