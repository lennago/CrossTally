"""The project's test run: Django's own, with a deviation.

In operation WhiteNoise serves the static files with a checksum in the name
('CompressedManifestStaticFilesStorage'). But this storage only knows a file
once 'collectstatic' has written it into the manifest. And until then every
page with '{% static %}' dies of a 'ValueError'.

In the CI 'collectstatic' never runs, locally only occasionally. The tests
thus hung on a build state that does not exist in the CI at all: there every
rendered page failed on the logo already, and locally every one as soon as a
new file was added. What should be checked are the pages, not the collecting
of files. The image checks that when building.

Therefore the ordinary storage applies in the tests, which builds addresses
without a manifest.

Second deviation: **no redirect to https**. Without 'DEBUG', 'settings.py'
switches it on, and the test client speaks http. Every page would come back
as a 301. The CI sets 'DEBUG=0' so the tests run against the settings of
operation. Without this exception it would never have turned green. That the
redirect is in place in operation is checked separately by 'test_settings.py'.

Third deviation: **a fast password hasher**. PBKDF2 is slow on purpose
(measured 133 ms per account), and almost every test class creates one. The
tests check *that* a password matches, not how expensive it is to guess:
measured, the suite runs in 12 instead of 76 seconds with it. In operation
Django's default remains. 'settings.py' therefore names no hasher at all.

Fourth deviation: **German and Berlin time**, whatever the '.env' says. A new
account takes the server's language, and the suite asserts German texts and
the comma. On a machine with 'LANGUAGE_CODE=en' in its '.env' everything
would turn red without anything being broken (noticed on 2026-10-09). Tests
that want English say so with 'override_settings'.

**In parallel** ('--parallel auto') the same applies in every worker. Under
Python 3.14 they start via 'forkserver' as fresh processes and inherit
nothing from 'setup_test_environment'. Without 'process_setup' they would
search the manifest again.
"""

from django.conf import settings
from django.test.runner import DiscoverRunner, ParallelTestSuite
from django.test.utils import override_settings


def abweichungen() -> override_settings:
    return override_settings(
        STORAGES=settings.STORAGES
        | {"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
        SECURE_SSL_REDIRECT=False,
        PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
        LANGUAGE_CODE="de",
        TIME_ZONE="Europe/Berlin",
    )


class ParallelerTestlauf(ParallelTestSuite):
    # A plain function, not a 'staticmethod': Django passes
    # 'process_setup.__func__' on to the worker. It runs there before
    # 'django.setup()', and stays switched on until the end of the worker.
    def process_setup(*args):
        abweichungen().enable()


class Testlauf(DiscoverRunner):
    parallel_test_suite = ParallelerTestlauf

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._ohne_manifest = abweichungen()
        self._ohne_manifest.enable()

    def teardown_test_environment(self, **kwargs):
        self._ohne_manifest.disable()
        super().teardown_test_environment(**kwargs)
