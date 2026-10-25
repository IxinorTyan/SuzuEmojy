import errno
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import requests

from services.update_errors import (
    REASONS, UpdateError, UpdateNetworkError, format_update_error, request_error,
)
from services.update_service import (
    API_MIRRORS, API_URL, DOWNLOAD_MIRRORS, RELEASES_URL, Cancelled, UpdateService,
)
from services.update_protocol import MANIFEST, atomic_json, read_manifest
from test_updates import make_release


GET = 'services.update_service.requests.get'
DOWNLOAD_URL = RELEASES_URL + '/download/v1.14.0/SuzuEmojy_Release.zip'


def response(content, url=API_URL, status=200, content_type='application/json'):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result.headers['Content-Type'] = content_type
    result._content = content if isinstance(content, bytes) else json.dumps(content).encode()
    result._content_consumed = True
    return result


class UpdateNetworkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / '软件 中文 with spaces'
        make_release(self.root)
        self.service = UpdateService(self.root)
        stage = self.base / 'release'
        make_release(stage, '1.14.0')
        archive = self.base / 'release.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            for file in stage.rglob('*'):
                if file.is_file():
                    output.write(file, file.relative_to(stage).as_posix())
        self.content = archive.read_bytes()
        self.release = dict(url=DOWNLOAD_URL, version='1.14.0', size=len(self.content),
                            sha256=hashlib.sha256(self.content).hexdigest())
        self.metadata = dict(tag_name='v1.14.0', assets=[dict(
            name='SuzuEmojy_Release.zip', state='uploaded', size=len(self.content),
            digest='sha256:' + self.release['sha256'], browser_download_url=DOWNLOAD_URL,
        )])

    def zip_response(self, content=None):
        return response(self.content if content is None else content, DOWNLOAD_URL,
                        content_type='application/octet-stream')

    def assert_unchanged(self):
        self.assertEqual(read_manifest(self.root, True)['version'], '1.13.0')
        self.assertFalse((self.service.workspace / 'ready.json').exists())
        self.assertFalse(list(self.service.workspace.glob('*.part')))
        self.assertFalse(list(self.service.workspace.glob('stage-*')))

    def test_check_timeout_falls_back_to_api_capable_mirror(self):
        sources = []
        with patch(GET, side_effect=[requests.ConnectTimeout('direct timeout'), response(self.metadata)]) as get:
            release = self.service.check(report=sources.append)
        self.assertEqual(release['version'], '1.14.0')
        self.assertEqual(release['source'], 'gh-proxy.com')
        self.assertEqual(sources, ['GitHub', 'gh-proxy.com'])
        self.assertEqual([call.args[0] for call in get.call_args_list], [API_URL, API_MIRRORS[0] + API_URL])

    def test_github_api_rate_limit_uses_mirror(self):
        limited = response({}, status=403)
        limited.headers['X-RateLimit-Remaining'] = '0'
        with patch(GET, side_effect=[limited, response(self.metadata)]):
            self.assertEqual(self.service.check()['source'], 'gh-proxy.com')

    def test_html_instead_of_api_json_uses_mirror(self):
        with patch(GET, side_effect=[response(b'<html>blocked</html>'), response(self.metadata)]):
            self.assertEqual(self.service.check()['source'], 'gh-proxy.com')

    def test_all_check_failures_retain_each_source_and_cause(self):
        errors = [requests.ConnectTimeout('timeout'), requests.ConnectionError('getaddrinfo failed')]
        with patch(GET, side_effect=errors), self.assertRaises(UpdateNetworkError) as caught:
            self.service.check()
        error = caught.exception
        self.assertEqual([item[2].code for item in error.attempts], ['timeout', 'dns'])
        text = format_update_error(error)
        self.assertIn(API_URL, text)
        self.assertIn('gh-proxy.com', text)
        self.assertIn('getaddrinfo failed', text)

    def test_cancel_after_failure_does_not_try_next_source(self):
        def fail(*args, **kwargs):
            self.service.cancel.set()
            raise requests.ConnectionError('offline')
        with patch(GET, side_effect=fail) as get, self.assertRaises(Cancelled):
            self.service.check()
        self.assertEqual(get.call_count, 1)

    def test_invalid_release_package_is_reported_without_pointless_network_retry(self):
        self.metadata['assets'] = []
        with patch(GET, return_value=response(self.metadata)) as get, self.assertRaisesRegex(ValueError, 'SuzuEmojy_Release.zip'):
            self.service.check()
        self.assertEqual(get.call_count, 1)

    def test_invalid_release_field_types_have_actionable_errors(self):
        for field, value in (('assets', None), ('body', ['invalid']), ('published_at', 5)):
            metadata = dict(self.metadata, **{field: value})
            with self.subTest(field=field), patch(GET, return_value=response(metadata)) as get:
                with self.assertRaises(UpdateError) as caught:
                    self.service.check()
                self.assertEqual(caught.exception.code, 'release')
                self.assertEqual(get.call_count, 1)

    def test_unknown_current_version_reports_local_version_file(self):
        (self.root / 'version.json').write_text('{"version": null}', encoding='utf-8')
        with patch(GET, return_value=response(self.metadata)), self.assertRaises(UpdateError) as caught:
            self.service.check()
        self.assertEqual(caught.exception.code, 'version')
        self.assertIn('version.json', format_update_error(caught.exception))

    def test_unsafe_redirect_is_rejected_and_uses_next_source(self):
        with patch(GET, side_effect=[response(self.metadata, url='http://insecure.example'), response(self.metadata)]):
            self.assertEqual(self.service.check()['source'], 'gh-proxy.com')

    def test_http_errors_and_wrapped_read_timeout_have_specific_reasons(self):
        for status, code in ((404, 'not_found'), (401, 'forbidden'), (403, 'forbidden'),
                             (429, 'rate_limit'), (502, 'server'), (418, 'http')):
            with self.subTest(status=status):
                failed = response({}, status=status)
                problem = request_error(requests.HTTPError(response=failed))
                self.assertEqual(problem.code, code)
                self.assertIn(str(status), format_update_error(problem))
        problem = request_error(requests.ConnectionError('HTTPSConnectionPool: Read timed out.'))
        self.assertEqual(problem.code, 'timeout')

    def test_non_project_url_is_never_sent_to_mirrors(self):
        release = dict(self.release, url='https://example.com/private.zip')
        with patch(GET, side_effect=requests.ConnectTimeout('timeout')) as get, self.assertRaises(UpdateNetworkError):
            self.service.download(release, lambda value: None)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], release['url'])
        self.assert_unchanged()

    def test_download_http_failure_uses_mirror_and_preserves_original_install(self):
        sources, progress = [], []
        with patch(GET, side_effect=[response({}, DOWNLOAD_URL, status=404), self.zip_response()]) as get:
            item = self.service.download(self.release, progress.append, report=sources.append)
        self.assertEqual(sources, ['GitHub', 'gh-proxy.com'])
        self.assertEqual(get.call_args_list[1].args[0], DOWNLOAD_MIRRORS[0] + DOWNLOAD_URL)
        self.assertEqual(progress[-1], 100)
        self.assertEqual(read_manifest(self.root, True)['version'], '1.13.0')
        self.assertEqual(read_manifest(self.service.workspace / item['directory'], True)['version'], '1.14.0')

    def test_partial_download_is_replaced_instead_of_appended(self):
        def interrupted(chunk_size):
            yield self.content[:31]
            raise requests.ConnectionError('connection reset mid-download')
        broken = self.zip_response()
        broken.iter_content = interrupted
        with patch(GET, side_effect=[broken, self.zip_response()]):
            item = self.service.download(self.release, lambda value: None)
        self.assertEqual(self.service.ready(), item)

    def test_hash_mismatch_retries_another_source(self):
        corrupt = b'x' * len(self.content)
        with patch(GET, side_effect=[self.zip_response(corrupt), self.zip_response()]) as get:
            self.service.download(self.release, lambda value: None)
        self.assertEqual(get.call_count, 2)

    def test_http_200_web_page_is_not_treated_as_update_package(self):
        page = response(b'<html>blocked</html>', DOWNLOAD_URL, content_type='text/html')
        with patch(GET, side_effect=[page, self.zip_response()]) as get:
            self.service.download(self.release, lambda value: None)
        self.assertEqual(get.call_count, 2)

    def test_all_download_sources_fail_with_complete_diagnostics_and_cleanup(self):
        failures = [response({}, DOWNLOAD_URL, status=403), requests.ReadTimeout('read timed out'),
                    requests.exceptions.SSLError('certificate verify failed')]
        with patch(GET, side_effect=failures) as get, self.assertRaises(UpdateNetworkError) as caught:
            self.service.download(self.release, lambda value: None)
        self.assertEqual(get.call_count, 3)
        self.assertEqual([a[2].code for a in caught.exception.attempts], ['forbidden', 'timeout', 'tls'])
        text = format_update_error(caught.exception)
        for token in ('GitHub', 'gh-proxy.com', 'ghfast.top', '403', 'read timed out', 'certificate verify failed'):
            self.assertIn(token, text)
        self.assert_unchanged()

    def test_local_write_error_does_not_waste_time_switching_network_sources(self):
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.name.endswith('.zip.part'):
                raise PermissionError(errno.EACCES, 'file is locked', str(path))
            return original(path, *args, **kwargs)
        with patch(GET, return_value=self.zip_response()) as get, patch.object(Path, 'open', open_file):
            with self.assertRaises(PermissionError):
                self.service.download(self.release, lambda value: None)
        self.assertEqual(get.call_count, 1)
        self.assert_unchanged()

    def test_disk_full_does_not_start_network_download(self):
        with patch(GET) as get, patch('services.update_service.shutil.disk_usage', return_value=Mock(free=0)):
            with self.assertRaisesRegex(OSError, 'Insufficient disk space'):
                self.service.download(self.release, lambda value: None)
        get.assert_not_called()
        self.assert_unchanged()

    def test_missing_install_manifest_has_specific_actionable_error(self):
        (self.root / MANIFEST).unlink()
        with patch(GET) as get, self.assertRaises(UpdateError) as caught:
            self.service.download(self.release, lambda value: None)
        get.assert_not_called()
        self.assertEqual(caught.exception.code, 'installation')
        self.assertIn(MANIFEST, format_update_error(caught.exception))

    def test_cancel_mid_download_never_falls_back_and_leaves_install_intact(self):
        def cancel(value):
            if value:
                self.service.cancel.set()
        with patch(GET, return_value=self.zip_response()) as get, self.assertRaises(Cancelled):
            self.service.download(self.release, cancel)
        self.assertEqual(get.call_count, 1)
        self.assert_unchanged()

    def test_failed_new_download_preserves_previously_ready_package(self):
        previous = self.service.workspace / 'stage-previous'
        make_release(previous, '1.13.1')
        item = dict(version='1.13.1', directory=previous.name)
        atomic_json(self.service.workspace / 'ready.json', item)
        with patch(GET, side_effect=requests.ConnectionError('offline')), self.assertRaises(UpdateNetworkError):
            self.service.download(self.release, lambda value: None)
        self.assertEqual(self.service.ready(), item)
        self.assertEqual(read_manifest(previous, True)['version'], '1.13.1')

    def prepare_for_install(self):
        with patch(GET, return_value=self.zip_response()):
            self.service.download(self.release, lambda value: None)

    def test_installer_failure_reports_current_exit_code_and_log(self):
        self.prepare_for_install()
        error_log = self.service.workspace / 'last-error.txt'
        error_log.write_text('old unrelated error', encoding='utf-8')
        def launch(*args, **kwargs):
            self.assertFalse(error_log.exists())
            error_log.write_text('current helper failure', encoding='utf-8')
            return Mock(poll=Mock(return_value=7))
        with patch('services.update_service.subprocess.Popen', side_effect=launch), self.assertRaises(UpdateError) as caught:
            self.service.launch_installer()
        self.assertEqual(caught.exception.code, 'installer')
        self.assertIn('exit code: 7', caught.exception.detail)
        self.assertIn('current helper failure', caught.exception.detail)
        self.assertNotIn('old unrelated error', caught.exception.detail)
        self.assertEqual(read_manifest(self.root, True)['version'], '1.13.0')

    def test_unready_installer_is_stopped_so_retry_can_replace_it(self):
        self.prepare_for_install()
        process = Mock()
        process.poll.return_value = None
        def terminate():
            process.poll.return_value = 1
        process.terminate.side_effect = terminate
        with patch('services.update_service.subprocess.Popen', return_value=process), patch('time.sleep'), self.assertRaises(UpdateError) as caught:
            self.service.launch_installer()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)
        self.assertIn('20 seconds', caught.exception.detail)
        self.assertEqual(caught.exception.code, 'installer')
        self.assertEqual(read_manifest(self.root, True)['version'], '1.13.0')

    def test_diagnostics_do_not_expose_proxy_credentials_or_signed_redirect_urls(self):
        problem = request_error(requests.exceptions.ProxyError(
            'Cannot connect to https://username:password@proxy.example:1234/path?token=secret'
        ))
        text = format_update_error(problem)
        for secret in ('username', 'password', 'token=secret'):
            self.assertNotIn(secret, text)

    def test_all_error_reasons_are_translated_in_every_language(self):
        from services.i18n import i18n_engine
        previous = i18n_engine.current_lang
        self.addCleanup(i18n_engine.load_language, previous)
        for language in ('zh', 'zh_TW', 'en', 'ja'):
            i18n_engine.load_language(language)
            for code in REASONS:
                with self.subTest(language=language, code=code):
                    problem = UpdateError(code, 'diagnostic', status=404, expected=100, actual=5)
                    text = format_update_error(UpdateNetworkError('download', [('GitHub', DOWNLOAD_URL, problem)]))
                    self.assertNotIn('MISSING', text)


if __name__ == '__main__':
    unittest.main()
