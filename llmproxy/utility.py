import logging

from .validation import ProviderStatus

LOGGER = logging.getLogger("llmproxy")


def log_validation_results(results: dict[str, ProviderStatus]) -> None:
    valid = [n for n, s in results.items() if s.ok]
    invalid = [n for n, s in results.items() if not s.ok and s.detail != "no API key configured"]
    unconfigured = [n for n, s in results.items() if s.detail == "no API key configured"]

    if valid:
        LOGGER.info("Valid providers: %s", ", ".join(valid))
    if invalid:
        for name in invalid:
            LOGGER.warning("Invalid provider %s: %s", name, results[name].detail)
    if unconfigured:
        LOGGER.info("Unconfigured providers: %s", ", ".join(unconfigured))
    if not valid and not invalid:
        LOGGER.warning("No provider API keys configured.")
