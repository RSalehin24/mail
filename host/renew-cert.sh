#!/bin/sh
set -eu
umask 077
cd /usr/local/lib/mail-admin
exec /usr/bin/python3 -m host.cli renew
