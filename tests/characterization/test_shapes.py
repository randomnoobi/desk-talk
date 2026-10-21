"""The shape helper that every saved snapshot depends on."""

from __future__ import annotations

import pytest

from .shapes import differences, shape_of


@pytest.mark.parametrize('value, shape', [
    ({'ok': True, 'fps': 2.5, 'n': 3, 'tip': None, 'id': 'a1'},
     {'fps': 'number', 'id': 'str', 'n': 'number', 'ok': 'bool', 'tip': 'null'}),
    ([], []),
    ([1, 2.5, 3], ['number']),
    # items that differ only in null-vs-value fold into one item shape
    ([{'art': 'cup.svg'}, {'art': None}], [{'art': 'null|str'}]),
    # items with different keys stay separate
    ([{'type': 'roster', 'agent': {}}, {'type': 'group_message', 'text': 'hi'}],
     [{'agent': {}, 'type': 'str'}, {'text': 'str', 'type': 'str'}]),
    ([[0, 1], []], [['number']]),
])
def test_shape_of(value, shape):
    assert shape_of(value) == shape


def test_differences_name_the_place_that_changed():
    saved = {'messages': [{'id': 'str', 'text': 'str'}], 'fps': 'number'}
    now = {'messages': [{'id': 'number', 'text': 'str'}], 'detail': 'str'}
    assert differences(saved, now) == [
        '(the whole reply): missing key(s) fps',
        '(the whole reply): unexpected key(s) detail',
        'messages[].id: expected "str", got "number"',
    ]


def test_null_or_empty_list_only_allowed_where_the_case_says_so():
    saved = {'tip': 'str', 'items': ['str']}
    now = {'tip': 'null', 'items': []}
    assert len(differences(saved, now)) == 2
    assert differences(saved, now, vary=('tip', 'items')) == []
    assert differences({'tip': 'str'}, {'tip': 'number'}, vary=('tip',)) != []
