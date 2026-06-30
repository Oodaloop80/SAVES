import os
import time


def check_all_cookies(config: dict, cookies_dir: str) -> list[dict]:
    """
    Returns a list of warning dicts for cookies approaching expiry.
    Each dict: {platform, days_old, expiry_days, days_remaining, cookie_path}
    """
    platforms_cfg = config.get("platforms", {})
    warnings = []
    now = time.time()

    platform_cookie_files = {
        "instagram": "instagram.txt",
        "tiktok": "tiktok.txt",
        "facebook": "facebook.txt",
    }

    for platform, filename in platform_cookie_files.items():
        pcfg = platforms_cfg.get(platform, {})
        expiry_days = pcfg.get("cookie_expiry_days")
        warn_ahead = pcfg.get("cookie_warning_days_ahead")
        if not expiry_days or not warn_ahead:
            continue

        cookie_path = os.path.join(cookies_dir, filename)
        if not os.path.exists(cookie_path):
            warnings.append({
                "platform": platform,
                "missing": True,
                "cookie_path": cookie_path,
            })
            continue

        mtime = os.path.getmtime(cookie_path)
        days_old = (now - mtime) / 86400
        days_remaining = expiry_days - days_old

        if days_remaining <= warn_ahead:
            warnings.append({
                "platform": platform,
                "days_old": round(days_old),
                "expiry_days": expiry_days,
                "days_remaining": round(days_remaining),
                "cookie_path": cookie_path,
                "missing": False,
            })

    return warnings
