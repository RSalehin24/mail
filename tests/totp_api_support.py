"""Exercise an anonymous password/TOTP login through the actual HTTP boundary."""

from tests.auth_support import PASSWORD


class TotpLogin:
    def __init__(self, assertions, client):
        self.assertions, self.client = assertions, client

    async def verify(self, code):
        self.client.cookies.clear()
        challenge = await self.client.post('/api/login', json={
            'email': 'rsalehin24@gmail.com', 'password': PASSWORD,
        })
        self.assertions.assertEqual(challenge.status_code, 200)
        return await self.client.post('/api/2fa/verify', json={
            'challenge': challenge.json()['challenge'], 'factor': 'totp', 'code': code,
        })

    async def rejected(self, code):
        response = await self.verify(code)
        self.assertions.assertEqual(response.status_code, 401)
        self.assertions.assertNotIn('set-cookie', response.headers)
        self.assertions.assertEqual((await self.client.get('/api/accounts')).status_code, 401)
