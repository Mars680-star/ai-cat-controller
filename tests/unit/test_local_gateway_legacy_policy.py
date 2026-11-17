"""Keep legacy device domain code untouched when installing the sidecar."""
import pytest

from ai_cat_controller.domain import actions, intimacy
from ai_cat_controller.mini_gateway import product


def test_legacy_domain_supports_original_catalogue_without_importing_new_hardware_policy(monkeypatch):
    monkeypatch.delattr(actions,'action_requires_tail')
    monkeypatch.delattr(intimacy,'hardware_unlocks')
    original=('original existing unlock',)
    assert product.hardware_unlocks(original,has_tail=True)==original
    with pytest.raises(ValueError):product.hardware_unlocks(original,has_tail=False)
    with pytest.raises(ValueError):product.action_requires_tail(actions.ACTIONS[0])


def test_current_no_tail_policy_is_not_weakened():
    for action in actions.ACTIONS:
        assert product.action_requires_tail(action)==actions.action_requires_tail(action)
    values=('基础尾部动作','组合动作','庆祝动作','全部动作','voice')
    assert product.hardware_unlocks(values,has_tail=False)==intimacy.hardware_unlocks(values,has_tail=False)
