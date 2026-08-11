import importlib
import os
import unittest

import backend.test_api as test_api


class TestApiBaseUrl(unittest.TestCase):
    def _reload_with(self, value):
        previous = os.environ.get('TEST_API_BASE_URL')
        try:
            if value is None:
                os.environ.pop('TEST_API_BASE_URL', None)
            else:
                os.environ['TEST_API_BASE_URL'] = value
            return importlib.reload(test_api).BASE
        finally:
            if previous is None:
                os.environ.pop('TEST_API_BASE_URL', None)
            else:
                os.environ['TEST_API_BASE_URL'] = previous
            importlib.reload(test_api)

    def test_default_base_url_stays_on_local_backend_port(self):
        self.assertEqual(self._reload_with(None), 'http://127.0.0.1:5000')

    def test_local_override_is_available_for_unbound_port_checks(self):
        self.assertEqual(self._reload_with('http://127.0.0.1:5999/'),
                         'http://127.0.0.1:5999')


if __name__ == '__main__':
    unittest.main()
