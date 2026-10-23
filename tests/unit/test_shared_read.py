"""desktalk_shared._read: reading the JSON the Pi, this computer and the detector send, with plain error messages."""

import pytest

from desktalk_shared._read import WireError, flag, mapping, number, text, whole


def test_a_message_must_be_a_json_object():
    with pytest.raises(WireError, match=r'^upload must be a JSON object \(got list\)$'):
        mapping([], 'upload')


@pytest.mark.parametrize('read, data, says', [
    (lambda d: text(d, 'model', 'info.', required=True), {}, 'info.model is missing'),
    (lambda d: text(d, 'model', 'info.'), {'model': 5}, 'info.model must be text (got 5)'),
    (lambda d: number(d, 'lux', 'info.', required=True), {'lux': None}, 'info.lux is missing'),
    (lambda d: number(d, 'lux', 'info.'), {'lux': float('nan')}, 'info.lux must be a number (got nan)'),
    (lambda d: number(d, 'lux', 'info.'), {'lux': True}, 'info.lux must be a number (got True)'),
    (lambda d: whole(d, 'photos', 'info.'), {'photos': 2.5}, 'info.photos must be a whole number (got 2.5)'),
    (lambda d: flag(d, 'half', 'model.'), {'half': 'yes'}, "model.half must be true or false (got 'yes')"),
])
def test_a_missing_or_wrong_value_says_which_one_and_why(read, data, says):
    with pytest.raises(WireError) as err:
        read(data)
    assert str(err.value) == says


def test_missing_values_that_are_not_required_get_their_default():
    assert text({}, 'model', 'info.', default='none') == 'none'
    assert number({}, 'lux', 'info.') is None
    assert whole({'photos': 3.0}, 'photos', 'info.') == 3
    assert flag({}, 'half', 'model.', default=True) is True
