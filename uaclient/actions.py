import logging
from typing import Optional  # noqa: F401

from uaclient import (
    clouds,
    config,
    contract,
    entitlements,
    event_logger,
    exceptions,
    messages,
)
from uaclient import status as ua_status
from uaclient import util
from uaclient.clouds import AutoAttachCloudInstance  # noqa: F401
from uaclient.clouds import identity

LOG = logging.getLogger("ua.actions")
event = event_logger.get_event_logger()


def attach_with_token(
    cfg: config.UAConfig, token: str, allow_enable: bool
) -> None:
    """
    Common functionality to take a token and attach via contract backend
    :raise UrlError: On unexpected connectivity issues to contract
        server or inability to access identity doc from metadata service.
    :raise ContractAPIError: On unexpected errors when talking to the contract
        server.
    """
    from uaclient.jobs.update_messaging import update_apt_and_motd_messages

    try:
        contract.request_updated_contract(
            cfg, token, allow_enable=allow_enable
        )
    except exceptions.UrlError as exc:
        # Persist updated status in the event of partial attach
        ua_status.status(cfg=cfg)
        update_apt_and_motd_messages(cfg)
        raise exc
    except exceptions.UserFacingError as exc:
        # Persist updated status in the event of partial attach
        ua_status.status(cfg=cfg)
        update_apt_and_motd_messages(cfg)
        raise exc

    current_iid = identity.get_instance_id()
    if current_iid:
        cfg.write_cache("instance-id", current_iid)

    update_apt_and_motd_messages(cfg)


def auto_attach(
    cfg: config.UAConfig, cloud: clouds.AutoAttachCloudInstance
) -> None:
    """
    :raise UrlError: On unexpected connectivity issues to contract
        server or inability to access identity doc from metadata service.
    :raise ContractAPIError: On unexpected errors when talking to the contract
        server.
    :raise NonAutoAttachImageError: If this cloud type does not have
        auto-attach support.
    """
    contract_client = contract.UAContractClient(cfg)
    try:
        tokenResponse = contract_client.request_auto_attach_contract_token(
            instance=cloud
        )
    except exceptions.ContractAPIError as e:
        if e.code and 400 <= e.code < 500:
            raise exceptions.NonAutoAttachImageError(
                messages.UNSUPPORTED_AUTO_ATTACH
            )
        raise e

    token = tokenResponse["contractToken"]

    attach_with_token(cfg, token=token, allow_enable=True)


def enable_entitlement_by_name(
    cfg: config.UAConfig,
    name: str,
    *,
    assume_yes: bool = False,
    allow_beta: bool = False
):
    """
    Constructs an entitlement based on the name provided. Passes kwargs onto
    the entitlement constructor.
    :raise EntitlementNotFoundError: If no entitlement with the given name is
        found, then raises this error.
    """
    ent_cls = entitlements.entitlement_factory(cfg=cfg, name=name)
    entitlement = ent_cls(
        cfg, assume_yes=assume_yes, allow_beta=allow_beta, called_name=name
    )
    return entitlement.enable()


def status(
    cfg: config.UAConfig,
    *,
    simulate_with_token: Optional[str] = None,
    show_beta: bool = False
):
    """
    Construct the current Pro status dictionary.
    """
    if simulate_with_token:
        status, ret = ua_status.simulate_status(
            cfg=cfg, token=simulate_with_token, show_beta=show_beta
        )
    else:
        status = ua_status.status(cfg=cfg, show_beta=show_beta)
        ret = 0

    return status, ret


def get_cloud_instance(
    cfg: config.UAConfig,
) -> Optional[AutoAttachCloudInstance]:
    disable_auto_attach = util.is_config_value_true(
        config=cfg.cfg, path_to_value="features.disable_auto_attach"
    )
    if disable_auto_attach:
        msg = "Skipping auto-attach. Config disable_auto_attach is set."
        logging.debug(msg)
        print(msg)
        return None

    instance = None  # type: Optional[AutoAttachCloudInstance]
    try:
        instance = identity.cloud_instance_factory()
    except exceptions.CloudFactoryError as e:
        if cfg.is_attached:
            # We are attached on non-Pro Image, just report already attached
            raise exceptions.AlreadyAttachedError(cfg)
        if isinstance(e, exceptions.CloudFactoryNoCloudError):
            raise exceptions.UserFacingError(
                messages.UNABLE_TO_DETERMINE_CLOUD_TYPE
            )
        if isinstance(e, exceptions.CloudFactoryNonViableCloudError):
            raise exceptions.UserFacingError(messages.UNSUPPORTED_AUTO_ATTACH)
        if isinstance(e, exceptions.CloudFactoryUnsupportedCloudError):
            raise exceptions.NonAutoAttachImageError(
                messages.UNSUPPORTED_AUTO_ATTACH_CLOUD_TYPE.format(
                    cloud_type=e.cloud_type
                )
            )
        # we shouldn't get here, but this is a reasonable default just in case
        raise exceptions.UserFacingError(
            messages.UNABLE_TO_DETERMINE_CLOUD_TYPE
        )

    if not instance:
        # we shouldn't get here, but this is a reasonable default just in case
        raise exceptions.UserFacingError(
            messages.UNABLE_TO_DETERMINE_CLOUD_TYPE
        )
    return instance


def detach_before_auto_attach(cfg: config.UAConfig):
    from uaclient.cli import _detach

    current_iid = identity.get_instance_id()
    if cfg.is_attached:
        prev_iid = cfg.read_cache("instance-id")
        if str(current_iid) == str(prev_iid):
            raise exceptions.AlreadyAttachedOnPROError(str(current_iid))
        print("Re-attaching Ubuntu Pro subscription on new instance")
        if _detach(cfg, assume_yes=True) != 0:
            raise exceptions.UserFacingError(
                messages.DETACH_AUTOMATION_FAILURE
            )
