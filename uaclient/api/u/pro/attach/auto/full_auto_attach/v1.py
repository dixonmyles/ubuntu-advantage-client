import time
from typing import List, Optional

from uaclient import actions, cli, contract, entitlements, exceptions, messages
from uaclient.api.api import APIEndpoint
from uaclient.api.data_types import AdditionalInfo
from uaclient.clouds import AutoAttachCloudInstance  # noqa: F401
from uaclient.config import UAConfig
from uaclient.data_types import (
    DataObject,
    Field,
    IntDataValue,
    StringDataValue,
    data_list,
)
from uaclient.entitlements.entitlement_status import CanEnableFailure


class FullAutoAttachOptions(DataObject):
    fields = [
        Field("enable", data_list(StringDataValue), False),
        Field("enable_beta", data_list(StringDataValue), False),
        Field("retries", IntDataValue, False),
    ]

    def __init__(
        self,
        enable: Optional[List[str]],
        enable_beta: Optional[List[str]],
        retries: Optional[int],
    ):
        self.enable = enable
        self.enable_beta = enable_beta
        self.retries = retries


class FullAutoAttachResult(DataObject, AdditionalInfo):
    pass


def get_auto_attach_token(cfg: UAConfig, cloud: AutoAttachCloudInstance):
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
    return token


def is_any_beta(cfg: UAConfig, ents: List[str]) -> bool:
    for name in ents:
        try:
            ent_cls = entitlements.entitlement_factory(cfg, name)
            if ent_cls.is_beta:
                return True
        except exceptions.EntitlementNotFoundError:
            continue
    return False


def full_auto_attach(options: FullAutoAttachOptions, cfg: UAConfig):
    i = 0
    limit = 3
    if options.retries:
        limit = options.retries
    already_attached = False  # avoid AlreadyAttachedOnProError after 1st time
    while i < limit:
        if not already_attached:
            instance = None  # type: Optional[AutoAttachCloudInstance]
            token = None  # Optional[str]
            instance = actions.get_cloud_instance(cfg)
            actions.detach_before_auto_attach(cfg)
            allow_enable = not any([options.enable, options.enable_beta])
            token = get_auto_attach_token(cfg, instance)  # type: ignore
            actions.attach_with_token(
                cfg, token=token, allow_enable=allow_enable
            )
            if allow_enable:
                return FullAutoAttachResult()
            already_attached = True

        services = list()
        if options.enable:
            if is_any_beta(cfg, options.enable):
                raise exceptions.BetaServiceError(
                    msg="beta service found in the enable list",
                    msg_code="beta-service-found",
                )
            services += options.enable
        if options.enable_beta:
            services += options.enable_beta

        services = list(set(services))
        found, not_found = cli.get_valid_entitlement_names(services, cfg)
        enabled_services = 0

        for name in found:
            ent_ret, reason = actions.enable_entitlement_by_name(
                cfg, name, assume_yes=True, allow_beta=True
            )
            if not ent_ret:
                if (
                    reason is not None
                    and isinstance(reason, CanEnableFailure)
                    and reason.message is not None
                ):
                    raise exceptions.EntitlementNotEnabledError(
                        msg=reason.message.msg,
                        msg_code=reason.message.name,
                        additional_info={"service": name},
                    )
            else:
                enabled_services += 1

        if not_found:
            msg = cli._create_enable_entitlements_not_found_message(
                not_found, cfg=cfg, allow_beta=True
            )
            raise exceptions.EntitlementNotFoundError(
                msg.msg
            )  # should we detach?
        if len(services) == enabled_services:
            return FullAutoAttachResult()
        i += 1
        time.sleep(2)

    raise exceptions.FullAutoAttachFailureError(
        msg="full_auto_attach was not successful",
        msg_code="full-auto-attach-error",
    )


endpoint = APIEndpoint(
    version="v1",
    name="AutoAttachWithShortRetry",
    fn=full_auto_attach,
    options_cls=FullAutoAttachOptions,
)
