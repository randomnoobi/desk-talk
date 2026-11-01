from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from desktalk_cloud.config import ENV_NAMES, ConfigError, load_config
from tests.fakes.cloud import CLOUD_FOLDER

SECRET = 'a-long-enough-secret-for-the-tests-0123456789'


def test_loads_the_real_settings():
    config = load_config(CLOUD_FOLDER, {'SESSION_SECRET': SECRET})
    assert config.secrets.session_secret.get_secret_value() == SECRET
    assert config.messages.bad_token
    assert isinstance(config.prompts, dict)


def test_spec_defaults():
    config = load_config(CLOUD_FOLDER, {'SESSION_SECRET': SECRET})
    assert config.pairing.code_valid_min == 10
    assert config.ingest.max_batch == 500
    assert config.retention.events_days == 365
    assert (config.reports.day, config.reports.weekday, config.reports.hour) == ('sunday', 6, 20)
    assert (config.reports.wait_for_pi_days, config.reports.max_tries) == (2, 3)
    assert (config.reports.provider, config.reports.model) == ('anthropic', 'claude-sonnet-5-5')
    assert config.server.https_only is False
    assert config.site.recent_hours == 24
    assert config.google.auth_url.startswith('https://accounts.google.com/')
    assert config.chat.url and config.chat.model and config.chat.limit_requests > 0
    assert config.chat.max_at_once > 0 and config.chat.deadline_sec >= config.chat.timeout_sec


def test_missing_session_secret_stops_and_names_it():
    with pytest.raises(ConfigError, match='SESSION_SECRET'):
        load_config(CLOUD_FOLDER, {})
    with pytest.raises(ConfigError, match='SESSION_SECRET'):
        load_config(CLOUD_FOLDER, {'SESSION_SECRET': ''})


def test_short_session_secret_stops_and_names_it():
    with pytest.raises(ConfigError, match='SESSION_SECRET.*too short'):
        load_config(CLOUD_FOLDER, {'SESSION_SECRET': 'short'})


def test_env_example_lists_every_secret_name():
    lines = (CLOUD_FOLDER / '.env.example').read_text().splitlines()
    names = {line.split('=')[0].strip() for line in lines if '=' in line and not line.lstrip().startswith('#')}
    assert set(ENV_NAMES) <= names
    assert 'SESSION_SECRET' in ENV_NAMES and 'DATABASE_URL' in ENV_NAMES


def test_other_secrets_are_optional_and_empty_means_unset():
    config = load_config(CLOUD_FOLDER, {'SESSION_SECRET': SECRET, 'GOOGLE_CLIENT_ID': '', 'CHAT_API_KEY': ''})
    s = config.secrets
    assert s.google_client_id is None and s.chat_api_key is None and s.database_url is None
    assert s.admin_username is None and s.admin_password is None and s.report_api_key is None


def test_secrets_read_from_the_environment_and_stay_out_of_text():
    env = {'SESSION_SECRET': SECRET, 'GOOGLE_CLIENT_ID': 'cid', 'GOOGLE_CLIENT_SECRET': 'google-secret-xyz',
           'ADMIN_USERNAME': 'boss', 'ADMIN_PASSWORD': 'admin-pass-xyz', 'REPORT_API_KEY': 'report-key-xyz',
           'CHAT_API_KEY': 'chat-key-xyz', 'DATABASE_URL': 'postgresql://u:db-pass-xyz@db/x'}
    config = load_config(CLOUD_FOLDER, env)
    assert config.secrets.google_client_secret
    assert config.secrets.google_client_secret.get_secret_value() == 'google-secret-xyz'
    assert config.secrets.admin_username == 'boss'
    assert config.secrets.database_url
    assert config.secrets.database_url.get_secret_value().endswith('@db/x')
    shown = repr(config) + str(config) + config.model_dump_json()
    for secret in (SECRET, 'google-secret-xyz', 'admin-pass-xyz', 'report-key-xyz', 'chat-key-xyz', 'db-pass-xyz'):
        assert secret not in shown


def test_public_and_login_address_can_come_from_the_environment():
    plain = load_config(CLOUD_FOLDER, {'SESSION_SECRET': SECRET})
    set_ = load_config(CLOUD_FOLDER, {'SESSION_SECRET': SECRET, 'PUBLIC_URL': 'http://192.168.1.5:8000',
                                      'LOGIN_URL': 'http://localhost:8000/login'})
    assert plain.server.public_url.startswith('http://localhost')
    assert set_.server.public_url == 'http://192.168.1.5:8000'
    assert set_.server.login_url == 'http://localhost:8000/login'


def _copy_folder(tmp_path: Path, change) -> Path:
    settings = yaml.safe_load((CLOUD_FOLDER / 'settings.yaml').read_text())
    change(settings)
    (tmp_path / 'settings.yaml').write_text(yaml.safe_dump(settings))
    (tmp_path / 'prompts.yaml').write_text((CLOUD_FOLDER / 'prompts.yaml').read_text())
    return tmp_path


def test_bad_value_gives_a_plain_message_with_its_name(tmp_path):
    folder = _copy_folder(tmp_path, lambda s: s['ingest'].update(max_batch=0))
    with pytest.raises(ConfigError, match='ingest.max_batch'):
        load_config(folder, {'SESSION_SECRET': SECRET})


def test_a_chat_deadline_shorter_than_one_wait_is_refused(tmp_path):
    folder = _copy_folder(tmp_path, lambda s: s['chat'].update(timeout_sec=30, deadline_sec=29))
    with pytest.raises(ConfigError, match='deadline_sec'):
        load_config(folder, {'SESSION_SECRET': SECRET})


@pytest.mark.parametrize('name', ['max_at_once', 'deadline_sec'])
def test_chat_limits_must_be_above_zero(tmp_path, name):
    folder = _copy_folder(tmp_path, lambda s: s['chat'].update({name: 0}))
    with pytest.raises(ConfigError, match=f'chat.{name}'):
        load_config(folder, {'SESSION_SECRET': SECRET})


def test_unknown_setting_is_an_error(tmp_path):
    folder = _copy_folder(tmp_path, lambda s: s['ingest'].update(max_batchh=5))
    with pytest.raises(ConfigError, match='max_batchh'):
        load_config(folder, {'SESSION_SECRET': SECRET})


def test_openai_compatible_reports_need_a_url(tmp_path):
    folder = _copy_folder(tmp_path, lambda s: s['reports'].update(provider='openai_compatible', url=''))
    with pytest.raises(ConfigError, match='reports.url'):
        load_config(folder, {'SESSION_SECRET': SECRET})


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match='settings.yaml'):
        load_config(tmp_path, {'SESSION_SECRET': SECRET})


def test_prompts_file_must_have_every_message(tmp_path):
    folder = _copy_folder(tmp_path, lambda s: None)
    (folder / 'prompts.yaml').write_text('messages:\n  bad_token: hi\n')
    with pytest.raises(ConfigError, match='messages'):
        load_config(folder, {'SESSION_SECRET': SECRET})


def test_settings_yaml_holds_no_secrets():
    text = (CLOUD_FOLDER / 'settings.yaml').read_text().lower()
    for word in ('api_key', 'client_secret', 'password', 'session_secret'):
        assert f'{word}:' not in text


def test_a_file_that_is_not_yaml_or_not_a_mapping_is_an_error(tmp_path):
    (tmp_path / 'settings.yaml').write_text('a: [unclosed')
    with pytest.raises(ConfigError, match='not valid YAML'):
        load_config(tmp_path, {'SESSION_SECRET': SECRET})
    (tmp_path / 'settings.yaml').write_text('- just\n- a list\n')
    with pytest.raises(ConfigError, match='must hold settings'):
        load_config(tmp_path, {'SESSION_SECRET': SECRET})


def test_unknown_section_is_an_error(tmp_path):
    folder = _copy_folder(tmp_path, lambda s: s.update(extras={'a': 1}))
    with pytest.raises(ConfigError, match="'extras' is not a section"):
        load_config(folder, {'SESSION_SECRET': SECRET})


def test_missing_setting_is_named(tmp_path):
    folder = _copy_folder(tmp_path, lambda s: s['pairing'].pop('code_valid_min'))
    with pytest.raises(ConfigError, match="pairing.code_valid_min' is missing"):
        load_config(folder, {'SESSION_SECRET': SECRET})


def test_default_read_size_may_not_pass_the_biggest(tmp_path):
    folder = _copy_folder(tmp_path, lambda s: s['ingest'].update(read_default_limit=5000))
    with pytest.raises(ConfigError, match='read_default_limit'):
        load_config(folder, {'SESSION_SECRET': SECRET})
