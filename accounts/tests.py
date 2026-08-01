from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from accounts import spotify_client

User = get_user_model()


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class SpotifyAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client.login(username='testuser', password='password123')

    @override_settings(SPOTIFY_CLIENT_ID='test_client_id', SPOTIFY_CLIENT_SECRET='test_client_secret')
    def test_spotify_connect_redirects_to_spotify(self):
        response = self.client.get(reverse('accounts:spotify_connect'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('https://accounts.spotify.com/authorize', response.url)
        self.assertIn('client_id=test_client_id', response.url)

    @override_settings(SPOTIFY_CLIENT_ID='', SPOTIFY_CLIENT_SECRET='')
    def test_spotify_connect_unconfigured(self):
        response = self.client.get(reverse('accounts:spotify_connect'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('accounts:profile'))

    @override_settings(SPOTIFY_CLIENT_ID='test_client_id', SPOTIFY_CLIENT_SECRET='test_client_secret')
    def test_spotify_callback_routes_exist_without_404(self):
        urls = [
            '/accounts/spotify/callback/',
            '/accounts/spotify/callback',
            '/spotify/callback/',
            '/spotify/callback',
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertNotEqual(response.status_code, 404, f"URL {url} returned 404 Not Found")

    @override_settings(SPOTIFY_CLIENT_ID='test_client_id', SPOTIFY_CLIENT_SECRET='test_client_secret')
    def test_spotify_callback_error_cancelled(self):
        response = self.client.get(reverse('accounts:spotify_callback'), {'error': 'access_denied'})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('accounts:profile'))

    @override_settings(SPOTIFY_CLIENT_ID='test_client_id', SPOTIFY_CLIENT_SECRET='test_client_secret')
    def test_spotify_callback_mismatched_state(self):
        session = self.client.session
        session['spotify_oauth_state'] = 'state_a'
        session.save()

        response = self.client.get(
            reverse('accounts:spotify_callback'),
            {'code': 'fake_code', 'state': 'state_b'}
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('accounts:profile'))

    @override_settings(SPOTIFY_CLIENT_ID='test_client_id', SPOTIFY_CLIENT_SECRET='test_client_secret')
    @patch('accounts.spotify_client.requests.post')
    def test_spotify_callback_success(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            'access_token': 'fake_access_token',
            'refresh_token': 'fake_refresh_token',
            'expires_in': 3600,
        }

        session = self.client.session
        session['spotify_oauth_state'] = 'test_state'
        session.save()

        response = self.client.get(
            reverse('accounts:spotify_callback'),
            {'code': 'fake_code', 'state': 'test_state'}
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('accounts:profile'))

        self.user.refresh_from_db()
        self.assertEqual(self.user.spotify_access_token, 'fake_access_token')
        self.assertEqual(self.user.spotify_refresh_token, 'fake_refresh_token')

    @override_settings(SPOTIFY_CLIENT_ID='cid', SPOTIFY_CLIENT_SECRET='secret', SPOTIFY_REDIRECT_URI='http://custom/callback/')
    def test_get_redirect_uri_configured(self):
        self.assertEqual(spotify_client.get_redirect_uri(), 'http://custom/callback/')

    @override_settings(SPOTIFY_CLIENT_ID='cid', SPOTIFY_CLIENT_SECRET='secret', SPOTIFY_REDIRECT_URI='')
    def test_get_redirect_uri_dynamic_fallback(self):
        response = self.client.get(reverse('accounts:spotify_connect'))
        # request.build_absolute_uri will produce http://testserver/accounts/spotify/callback/
        self.assertIn('redirect_uri=http%3A%2F%2Ftestserver%2Faccounts%2Fspotify%2Fcallback%2F', response.url)
