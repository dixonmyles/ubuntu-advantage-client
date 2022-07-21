import logging
import os
import re
import uuid
from functools import lru_cache
from typing import Dict, NamedTuple, Optional, Set

from uaclient import exceptions, util

REBOOT_FILE_CHECK_PATH = "/var/run/reboot-required"
REBOOT_PKGS_FILE_PATH = "/var/run/reboot-required.pkgs"
ETC_MACHINE_ID = "/etc/machine-id"
DBUS_MACHINE_ID = "/var/lib/dbus/machine-id"
DROPPED_KEY = object()

# N.B. this relies on the version normalisation we perform in get_platform_info
REGEX_OS_RELEASE_VERSION = r"(?P<release>\d+\.\d+) (LTS )?\((?P<series>\w+).*"


RE_KERNEL_UNAME = (
    r"^"
    r"(?P<version>[\d]+[.-][\d]+[.-][\d]+)"
    r"-"
    r"(?P<abi>[\d]+)"
    r"-"
    r"(?P<flavor>[A-Za-z0-9_-]+)"
    r"$"
)
RE_KERNEL_PROC_VERSION_SIGNATURE = (
    r"^"
    r"(?P<version>[\d]+[.-][\d]+[.-][\d]+)"
    r"-"
    r"(?P<abi>[\d]+)"
    r"[.-]"
    r"(?P<subrev>[\d]+)"
    r"(~(?P<hwerev>[\d.]+))?"
    r"-"
    r"(?P<flavor>[A-Za-z0-9_-]+)"
    r"$"
)
RE_KERNEL_VERSION_SPLIT = (
    r"^"
    r"(?P<major>[\d]+)"
    r"[.-]"
    r"(?P<minor>[\d]+)"
    r"[.-]"
    r"(?P<patch>[\d]+)"
    r"$"
)

KernelInfo = NamedTuple(
    "KernelInfo",
    [
        ("uname_release", str),
        ("proc_version_signature_full", str),
        ("proc_version_signature_version", str),
        ("version", str),
        ("major", str),
        ("minor", str),
        ("patch", str),
        ("abi", str),
        ("subrev", str),
        ("hwerev", str),
        ("flavor", str),
    ],
)


@lru_cache(maxsize=None)
def get_kernel_info() -> KernelInfo:
    uname_release = os.uname().release

    proc_version_signature_full = ""
    proc_version_signature_version = ""
    try:
        proc_version_signature_full = util.load_file("/proc/version_signature")
        proc_version_signature_version = proc_version_signature_full.split(
            " "
        )[1]
    except:
        logging.warning(
            "failed to process /proc/version_signature. "
            "using uname for all kernel info"
        )

    if proc_version_signature_full != "":
        match = re.match(
            RE_KERNEL_PROC_VERSION_SIGNATURE, proc_version_signature_version
        )
        if match is None:
            raise exceptions.UserFacingError(
                "Failed to parse kernel: {}".format(
                    proc_version_signature_version
                )
            )
        version = match.group("version")
        abi = match.group("abi")
        subrev = match.group("subrev")
        hwerev = match.group("hwerev") or ""
        flavor = match.group("flavor")
    else:
        match = re.match(RE_KERNEL_UNAME, uname_release)
        if match is None:
            raise exceptions.UserFacingError(
                "Failed to parse kernel: {}".format(uname_release)
            )
        version = match.group("version")
        abi = match.group("abi")
        subrev = ""
        hwerev = ""
        flavor = match.group("flavor")

    version_split_match = re.match(RE_KERNEL_VERSION_SPLIT, version)
    if version_split_match is None:
        raise exceptions.UserFacingError(
            "Failed to split kernel version: {}".format(version)
        )

    major = version_split_match.group("major")
    minor = version_split_match.group("minor")
    patch = version_split_match.group("patch")

    return KernelInfo(
        uname_release=uname_release,
        proc_version_signature_full=proc_version_signature_full,
        proc_version_signature_version=proc_version_signature_version,
        version=version,
        major=major,
        minor=minor,
        patch=patch,
        abi=abi,
        subrev=subrev,
        hwerev=hwerev,
        flavor=flavor,
    )


@lru_cache(maxsize=None)
def get_lscpu_arch() -> str:
    """used for livepatch"""
    out, _err = util.subp(["lscpu"])
    for line in out.splitlines():
        if line.strip().startswith("Architecture"):
            return line.split(":")[1].strip()
    raise Exception()  # TODO


@lru_cache(maxsize=None)
def get_dpkg_arch() -> str:
    out, _err = util.subp(["dpkg", "--print-architecture"])
    return out.strip()


@lru_cache(maxsize=None)
def get_platform_info() -> Dict[str, str]:
    """
    Returns a dict of platform information.

    N.B. This dict is sent to the contract server, which requires the
    distribution, type and release keys.
    """
    os_release = parse_os_release()
    platform_info = {
        "distribution": os_release.get("NAME", "UNKNOWN"),
        "type": "Linux",
    }

    version = os_release["VERSION"]
    # Strip off an LTS point release (20.04.1 LTS -> 20.04 LTS)
    version = re.sub(r"\.\d LTS", " LTS", version)
    platform_info["version"] = version

    match = re.match(REGEX_OS_RELEASE_VERSION, version)
    if not match:
        raise RuntimeError(
            "Could not parse /etc/os-release VERSION: {} (modified to"
            " {})".format(os_release["VERSION"], version)
        )
    match_dict = match.groupdict()
    platform_info.update(
        {
            "release": match_dict["release"],
            "series": match_dict["series"].lower(),
        }
    )

    platform_info["kernel"] = get_kernel_info().uname_release
    platform_info["arch"] = get_dpkg_arch()

    return platform_info


@lru_cache(maxsize=None)
def is_lts(series: str) -> bool:
    out, _err = util.subp(["/usr/bin/ubuntu-distro-info", "--supported-esm"])
    return series in out


@lru_cache(maxsize=None)
def is_current_series_lts() -> bool:
    series = get_platform_info()["series"]
    return is_lts(series)


@lru_cache(maxsize=None)
def is_active_esm(series: str) -> bool:
    """Return True when Ubuntu series supports ESM and is actively in ESM."""
    if not is_lts(series):
        return False
    out, _err = util.subp(
        ["/usr/bin/ubuntu-distro-info", "--series", series, "-yeol"]
    )
    return int(out) <= 0


@lru_cache(maxsize=None)
def is_container(run_path: str = "/run") -> bool:
    """Checks to see if this code running in a container of some sort"""

    # We may mistake schroot environments for containers by just relying
    # in the other checks present in that function. To guarantee that
    # we do not identify a schroot as a container, we are explicitly
    # using the 'ischroot' command here.
    try:
        util.subp(["ischroot"])
        return False
    except exceptions.ProcessExecutionError:
        pass

    try:
        util.subp(["systemd-detect-virt", "--quiet", "--container"])
        return True
    except (IOError, OSError):
        pass

    for filename in ("container_type", "systemd/container"):
        path = os.path.join(run_path, filename)
        if os.path.exists(path):
            return True
    return False


@lru_cache(maxsize=None)
def get_machine_id(cfg) -> str:
    """Get system's unique machine-id or create our own in data_dir."""
    # Generate, cache our own uuid if not present in config or on the system

    if cfg.machine_token:
        cfg_machine_id = cfg.machine_token.get("machineTokenInfo", {}).get(
            "machineId"
        )
        if cfg_machine_id:
            return cfg_machine_id

    fallback_machine_id_file = cfg.data_path("machine-id")

    for path in [ETC_MACHINE_ID, DBUS_MACHINE_ID, fallback_machine_id_file]:
        if os.path.exists(path):
            content = util.load_file(path).rstrip("\n")
            if content:
                return content
    machine_id = str(uuid.uuid4())
    util.write_file(fallback_machine_id_file, machine_id)
    return machine_id


def should_reboot(
    installed_pkgs: Optional[Set[str]] = None,
    installed_pkgs_regex: Optional[Set[str]] = None,
) -> bool:
    """Check if the system needs to be rebooted.

    :param installed_pkgs: If provided, verify if the any packages in
        the list are present on /var/run/reboot-required.pkgs. If that
        param is provided, we will only return true if we have the
        reboot-required marker file and any package in reboot-required.pkgs
        file. When both installed_pkgs and installed_pkgs_regex are
        provided, they act as an OR, so only one of the two lists must have
        a match to return True.
    :param installed_pkgs_regex: If provided, verify if the any regex in
        the list matches any line in /var/run/reboot-required.pkgs. If that
        param is provided, we will only return true if we have the
        reboot-required marker file and any match in reboot-required.pkgs
        file. When both installed_pkgs and installed_pkgs_regex are
        provided, they act as an OR, so only one of the two lists must have
        a match to return True.
    """

    # If the reboot marker file doesn't exist, we don't even
    # need to look at the installed_pkgs param
    if not os.path.exists(REBOOT_FILE_CHECK_PATH):
        return False

    # If there is no installed_pkgs to check, we will rely only
    # on the existence of the reboot marker file
    if installed_pkgs is None and installed_pkgs_regex is None:
        return True

    try:
        reboot_required_pkgs = set(
            util.load_file(REBOOT_PKGS_FILE_PATH).split("\n")
        )
    except FileNotFoundError:
        # If the file doesn't exist, we will default to the
        # reboot  marker file
        return True

    if installed_pkgs is not None:
        if len(installed_pkgs.intersection(reboot_required_pkgs)) != 0:
            return True

    if installed_pkgs_regex is not None:
        for pkg_name in reboot_required_pkgs:
            for pkg_regex in installed_pkgs_regex:
                if re.search(pkg_regex, pkg_name):
                    return True

    return False


def which(program: str) -> Optional[str]:
    """Find whether the provided program is executable in our PATH"""
    if os.path.sep in program:
        # if program had a '/' in it, then do not search PATH
        if util.is_exe(program):
            return program
    paths = [
        p.strip('"') for p in os.environ.get("PATH", "").split(os.pathsep)
    ]
    normalized_paths = [os.path.abspath(p) for p in paths]
    for path in normalized_paths:
        program_path = os.path.join(path, program)
        if util.is_exe(program_path):
            return program_path
    return None


@lru_cache(maxsize=None)
def parse_os_release(release_file: Optional[str] = None) -> Dict[str, str]:
    if not release_file:
        release_file = "/etc/os-release"
    data = {}
    for line in util.load_file(release_file).splitlines():
        key, value = line.split("=", 1)
        if value:
            data[key] = value.strip().strip('"')
    return data
