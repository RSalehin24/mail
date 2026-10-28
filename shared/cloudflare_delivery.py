"""Add an inbound-only delivery domain without changing the main domain's MX routing."""

from shared.errors import ServiceError

DELIVERY_DOMAIN = 'delivery.rsalehin24.me'


def delivery_configuration(configuration: str) -> str:
    declaration = '$(local_domains) = $(primary_domain)'
    chain = 'table.chain local_rewrites {'
    rewrite = '    optional_step regexp "^(.+)@delivery[.]rsalehin24[.]me$" "$1@rsalehin24.me"'
    if rewrite in configuration:
        return configuration
    if configuration.count(declaration) != 1 or configuration.count(chain) != 1:
        raise ServiceError('unrecognized_maddy_delivery_configuration', 503)
    return configuration.replace(declaration, declaration + ' ' + DELIVERY_DOMAIN, 1).replace(
        chain, chain + '\n' + rewrite, 1)
