FROM ubuntu:26.04
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Штатные security updates; точный image фиксируется после native-приёмки.
# policy-rc.d запрещает запуск daemon во время apt install.
# hadolint ignore=DL3008
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod 755 /usr/sbin/policy-rc.d \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        squid-openssl ca-certificates openssl \
    && /usr/sbin/squid-openssl -v | grep -F -- '--with-openssl' \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' squid-openssl)" ge 7.2 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' squid-openssl)" lt 8 \
    && test -x /usr/lib/squid/security_file_certgen \
    && test "$(id -u proxy)" = 13 \
    && test "$(id -g proxy)" = 13 \
    && rm -rf /var/lib/apt/lists/*

USER 13:13
CMD ["/usr/sbin/squid-openssl", "-N", "-f", "/etc/squid/squid.conf"]
