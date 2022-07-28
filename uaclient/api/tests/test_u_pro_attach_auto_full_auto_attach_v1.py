import mock
import pytest

from uaclient import exceptions
from uaclient.api.u.pro.attach.auto.full_auto_attach.v1 import (
    FullAutoAttachOptions,
    full_auto_attach,
)

M_API = "uaclient.api.u.pro."


class TestFullAutoAttachV1:
    @mock.patch("uaclient.actions.get_cloud_instance")
    @mock.patch("uaclient.actions.detach_before_auto_attach")
    @mock.patch("uaclient.actions.attach_with_token")
    @mock.patch(
        M_API + "attach.auto.full_auto_attach.v1.get_auto_attach_token"
    )
    def test_error_when_beta_in_enable_list(
        self,
        _auto_attach_token,
        _attach_with_token,
        _before_auto_attach,
        _get_cloud_instance,
        FakeConfig,
    ):
        cfg = FakeConfig(root_mode=True)
        opts = {"enable": ["esm-infra", "realtime-kernel"]}
        options = FullAutoAttachOptions.from_dict(opts)
        with pytest.raises(exceptions.BetaServiceError):
            full_auto_attach(options, cfg)

    @mock.patch(
        "uaclient.actions.enable_entitlement_by_name",
        return_value=(True, None),
    )
    @mock.patch("uaclient.actions.get_cloud_instance")
    @mock.patch("uaclient.actions.detach_before_auto_attach")
    @mock.patch("uaclient.actions.attach_with_token")
    @mock.patch(
        M_API + "attach.auto.full_auto_attach.v1.get_auto_attach_token"
    )
    def test_error_invalid_ent_names(
        self,
        _auto_attach_token,
        _attach_with_token,
        _before_auto_attach,
        _get_cloud_instance,
        enable_ent_by_name,
        FakeConfig,
    ):
        cfg = FakeConfig(root_mode=True)
        opts = {
            "enable": ["esm-infra", "esm-apps", "cis"],
            "enable_beta": ["realtime-kernel", "test", "wrong"],
        }
        options = FullAutoAttachOptions.from_dict(opts)
        with pytest.raises(exceptions.EntitlementNotFoundError):
            full_auto_attach(options, cfg)

        assert 4 == enable_ent_by_name.call_count
