from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import AppConfig, OptimizationProfile, RigState


def build_xmrig_config(config: AppConfig) -> Dict[str, Any]:
    tuning = config.xmrig_tuning
    primary_pool = config.pools[0] if config.pools else None
    profile_defaults = _profile_defaults(tuning.profile)
    cpu_max_threads_hint = tuning.cpu_max_threads_hint or profile_defaults["cpu_max_threads_hint"]
    cpu_yield = tuning.cpu_yield
    if cpu_yield is None:
        cpu_yield = tuning.profile == OptimizationProfile.EFFICIENT

    xmrig_config: Dict[str, Any] = {
        "autosave": False,
        "donate-level": 1,
        "http": {
            "enabled": True,
            "host": _extract_http_host_from_command(config.miner.command) or "127.0.0.1",
            "port": _extract_http_port_from_command(config.miner.command) or 18080,
        },
        "randomx": {
            "mode": tuning.randomx_mode,
            "1gb-pages": tuning.randomx_1gb_pages,
            "rdmsr": tuning.randomx_wrmsr,
            "wrmsr": tuning.randomx_wrmsr,
            "cache_qos": tuning.randomx_cache_qos,
            "numa": not tuning.randomx_no_numa,
        },
        "cpu": {
            "enabled": True,
            "huge-pages": tuning.huge_pages,
            "max-threads-hint": cpu_max_threads_hint,
            "yield": cpu_yield,
        },
    }
    if tuning.cpu_priority is not None:
        xmrig_config["cpu"]["priority"] = tuning.cpu_priority
    if tuning.cpu_affinity:
        xmrig_config["cpu"]["hw-aes"] = True
        xmrig_config["cpu-affinity"] = tuning.cpu_affinity

    if primary_pool:
        xmrig_config["pools"] = [
            {
                "url": pool.url,
                "user": pool.user,
                "pass": pool.password,
                "keepalive": True,
                "tls": False,
            }
            for pool in sorted(config.pools, key=lambda pool: pool.priority)
        ]

    return xmrig_config


def optimization_status(config: AppConfig, state: RigState) -> Dict[str, Any]:
    host = detect_host_optimizations(config)
    recommendations = host["recommendations"][:]
    if state.last_hashrate > 0 and state.last_hashrate < config.thresholds.min_hashrate:
        recommendations.append(
            "Hashrate sous le seuil configure: verifier huge pages, max-threads-hint et affinite CPU."
        )
    if state.rejection_ratio > config.thresholds.max_rejection_ratio:
        recommendations.append("Le ratio de rejet est eleve: verifier le pool, la latence et les parametres CPU.")

    return {
        "profile": config.xmrig_tuning.profile.value,
        "generated_xmrig_config": build_xmrig_config(config),
        "host": host,
        "runtime": {
            "state": state.state.value,
            "health": state.health.value,
            "last_hashrate": state.last_hashrate,
            "active_pool": state.active_pool,
            "restart_attempts": state.restart_attempts,
        },
        "recommendations": recommendations,
    }


def detect_host_optimizations(config: AppConfig) -> Dict[str, Any]:
    cpu_count = os.cpu_count() or 1
    huge_pages_total = _read_meminfo_value("HugePages_Total")
    huge_page_size_kb = _read_meminfo_value("Hugepagesize")
    one_gb_pages = _read_int_file("/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages")
    thp_enabled = _read_sysfs_flag("/sys/kernel/mm/transparent_hugepage/enabled")
    numa_nodes = _count_numa_nodes("/sys/devices/system/node")
    recommended_threads = _recommended_threads(cpu_count, config.xmrig_tuning.profile)
    configured_threads = config.xmrig_tuning.cpu_max_threads_hint or _profile_defaults(config.xmrig_tuning.profile)["cpu_max_threads_hint"]

    recommendations: List[str] = []
    if huge_pages_total == 0:
        recommendations.append("Activer les huge pages Linux pour RandomX.")
    if config.xmrig_tuning.profile == OptimizationProfile.MAX_HASHRATE and one_gb_pages == 0:
        recommendations.append("Tester les 1GB huge pages sous Linux pour gagner encore un peu sur RandomX.")
    if numa_nodes > 1 and config.xmrig_tuning.randomx_no_numa:
        recommendations.append("Le host semble NUMA: eviter randomx_no_numa sauf cas de benchmark explicite.")
    if configured_threads < recommended_threads - 10:
        recommendations.append("Le max-threads-hint semble conservateur par rapport au profil choisi.")
    if thp_enabled is True:
        recommendations.append("Verifier l'impact de Transparent Huge Pages; Huge Pages dediees restent prioritaires.")

    return {
        "platform": os.uname().sysname if hasattr(os, "uname") else os.name,
        "cpu_count": cpu_count,
        "numa_nodes": numa_nodes,
        "huge_pages_total": huge_pages_total,
        "huge_page_size_kb": huge_page_size_kb,
        "randomx_1gb_pages_configured": config.xmrig_tuning.randomx_1gb_pages,
        "one_gb_pages_available": one_gb_pages > 0,
        "transparent_hugepages_enabled": thp_enabled,
        "configured_max_threads_hint": configured_threads,
        "recommended_max_threads_hint": recommended_threads,
        "recommendations": recommendations,
    }


def _profile_defaults(profile: OptimizationProfile) -> Dict[str, int]:
    if profile == OptimizationProfile.EFFICIENT:
        return {"cpu_max_threads_hint": 60}
    if profile == OptimizationProfile.MAX_HASHRATE:
        return {"cpu_max_threads_hint": 95}
    return {"cpu_max_threads_hint": 75}


def _recommended_threads(cpu_count: int, profile: OptimizationProfile) -> int:
    defaults = _profile_defaults(profile)
    return max(1, min(100, defaults["cpu_max_threads_hint"], int((cpu_count - 1) / max(cpu_count, 1) * 100) if cpu_count > 1 else 100))


def _read_meminfo_value(key: str) -> int:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 0
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return 0


def _read_int_file(path: str) -> int:
    file_path = Path(path)
    if not file_path.exists():
        return 0
    try:
        return int(file_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return 0


def _read_sysfs_flag(path: str) -> Optional[bool]:
    file_path = Path(path)
    if not file_path.exists():
        return None
    content = file_path.read_text(encoding="utf-8").strip()
    return "[" in content and "]" in content


def _count_numa_nodes(path: str) -> int:
    node_root = Path(path)
    if not node_root.exists():
        return 1
    nodes = [item for item in node_root.iterdir() if item.name.startswith("node")]
    return max(1, len(nodes))


def _extract_http_host_from_command(command: List[str]) -> Optional[str]:
    prefix = "--http-host="
    for item in command:
        if item.startswith(prefix):
            return item[len(prefix) :]
    return None


def _extract_http_port_from_command(command: List[str]) -> Optional[int]:
    prefix = "--http-port="
    for item in command:
        if item.startswith(prefix):
            value = item[len(prefix) :]
            if value.isdigit():
                return int(value)
    return None
