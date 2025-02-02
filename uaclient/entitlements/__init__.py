import enum
from typing import Dict, List, Type  # noqa: F401

from uaclient.config import UAConfig
from uaclient.entitlements import fips
from uaclient.entitlements.base import UAEntitlement  # noqa: F401
from uaclient.entitlements.cc import CommonCriteriaEntitlement
from uaclient.entitlements.cis import CISEntitlement
from uaclient.entitlements.esm import ESMAppsEntitlement, ESMInfraEntitlement
from uaclient.entitlements.livepatch import LivepatchEntitlement
from uaclient.entitlements.realtime import RealtimeKernelEntitlement
from uaclient.entitlements.ros import ROSEntitlement, ROSUpdatesEntitlement
from uaclient.exceptions import EntitlementNotFoundError
from uaclient.util import is_config_value_true

ENTITLEMENT_CLASSES = [
    CommonCriteriaEntitlement,
    CISEntitlement,
    ESMAppsEntitlement,
    ESMInfraEntitlement,
    fips.FIPSEntitlement,
    fips.FIPSUpdatesEntitlement,
    LivepatchEntitlement,
    RealtimeKernelEntitlement,
    ROSEntitlement,
    ROSUpdatesEntitlement,
]  # type: List[Type[UAEntitlement]]


def entitlement_factory(cfg: UAConfig, name: str):
    """Returns a UAEntitlement class based on the provided name.

    The return type is Optional[Type[UAEntitlement]].
    It cannot be explicit because of the Python version on Xenial (3.5.2).
    :param cfg: UAConfig instance
    :param name: The name of the entitlement to return
    :param not_found_okay: If True and no entitlement with the given name is
        found, then returns None.
    :raise EntitlementNotFoundError: If not_found_okay is False and no
        entitlement with the given name is found, then raises this error.
    """
    for entitlement in ENTITLEMENT_CLASSES:
        if name in entitlement(cfg=cfg).valid_names:
            return entitlement
    raise EntitlementNotFoundError()


def valid_services(
    cfg: UAConfig, allow_beta: bool = False, all_names: bool = False
) -> List[str]:
    """Return a list of valid (non-beta) services.

    :param cfg: UAConfig instance
    :param allow_beta: if we should allow beta services to be marked as valid
    :param all_names: if we should return all the names for a service instead
        of just the presentation_name
    """
    allow_beta_cfg = is_config_value_true(cfg.cfg, "features.allow_beta")
    allow_beta |= allow_beta_cfg

    entitlements = ENTITLEMENT_CLASSES
    if not allow_beta:
        entitlements = [
            entitlement
            for entitlement in entitlements
            if not entitlement.is_beta
        ]

    if all_names:
        names = []
        for entitlement in entitlements:
            names.extend(entitlement(cfg=cfg).valid_names)

        return sorted(names)

    return sorted(
        [
            entitlement(cfg=cfg).presentation_name
            for entitlement in entitlements
        ]
    )


@enum.unique
class SortOrder(enum.Enum):
    REQUIRED_SERVICES = object()
    DEPENDENT_SERVICES = object()


def entitlements_disable_order(cfg: UAConfig) -> List[str]:
    """
    Return the entitlements disable order based on dependent services logic.
    """
    return _sort_entitlements(cfg=cfg, sort_order=SortOrder.DEPENDENT_SERVICES)


def entitlements_enable_order(cfg: UAConfig) -> List[str]:
    """
    Return the entitlements enable order based on required services logic.
    """
    return _sort_entitlements(cfg=cfg, sort_order=SortOrder.REQUIRED_SERVICES)


def _sort_entitlements_visit(
    cfg: UAConfig,
    ent_cls: Type[UAEntitlement],
    sort_order: SortOrder,
    visited: Dict[str, bool],
    order: List[str],
):
    if ent_cls.name in visited:
        return

    if sort_order == SortOrder.REQUIRED_SERVICES:
        cls_list = ent_cls(cfg).required_services
    else:
        cls_list = ent_cls(cfg).dependent_services

    for cls_dependency in cls_list:
        if ent_cls.name not in visited:
            _sort_entitlements_visit(
                cfg=cfg,
                ent_cls=cls_dependency,
                sort_order=sort_order,
                visited=visited,
                order=order,
            )

    order.append(str(ent_cls.name))
    visited[str(ent_cls.name)] = True


def _sort_entitlements(cfg: UAConfig, sort_order: SortOrder) -> List[str]:
    order = []  # type: List[str]
    visited = {}  # type: Dict[str, bool]

    for ent_cls in ENTITLEMENT_CLASSES:
        _sort_entitlements_visit(
            cfg=cfg,
            ent_cls=ent_cls,
            sort_order=sort_order,
            visited=visited,
            order=order,
        )

    return order
