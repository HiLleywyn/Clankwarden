"""Clanksimus Prime entrypoint.

Boots from ``sojourns.json`` (the manifest's ``features`` is the cog list and
``bot.name`` is the app name) via the shared framework runtime. If the
manifest is missing or invalid the bot still starts from the fallback cog
list below, so a packaging slip can never make a working bot refuse to boot.
"""
from core.framework.run import run_manifest

# Mirrors sojourns.json -> features. Kept in sync as a safety net only; the
# manifest is the source of truth at runtime.
_FALLBACK_COGS = [
    "cogs.meta",
    "cogs.settings",
    "cogs.backups",
    "cogs.templates",
    "cogs.chatlog",
    "cogs.sync",
    "cogs.importexport",
    "cogs.clank",
]


if __name__ == "__main__":
    run_manifest(fallback_cogs=_FALLBACK_COGS, fallback_app_name="Clanksimus Prime")
