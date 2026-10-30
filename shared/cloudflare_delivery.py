"""Add an inbound-only delivery domain without changing the main domain's MX routing."""

from shared.errors import ServiceError
from shared.installation import site


def delivery_configuration(configuration: str) -> str:
    declaration = "$(local_domains) = $(primary_domain)"
    chain = "table.chain local_rewrites {"
    rewrite = '    optional_step regexp "^(.+)@{delivery}$" "$1@{domain}"'.format(
        delivery=site().delivery_domain.replace(".", "[.]"), domain=site().domain
    )
    if rewrite in configuration:
        return configuration
    if configuration.count(declaration) != 1 or configuration.count(chain) != 1:
        raise ServiceError("unrecognized_maddy_delivery_configuration", 503)
    return configuration.replace(
        declaration, declaration + " " + site().delivery_domain, 1
    ).replace(chain, chain + "\n" + rewrite, 1)


def __getattr__(name: str) -> str:
    fields = {"DELIVERY_DOMAIN": "delivery_domain"}
    if name in fields:
        return str(getattr(site(), fields[name]))
    raise AttributeError(name)
