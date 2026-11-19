#!/bin/sh
# Одноразовая проверка выделенных bind mounts, контейнер запускается без сети.
set -eu
umask 077
tls=/run/exam-tls
state=/var/lib/exam-squid
config=/etc/squid/squid.conf
issuer=$tls/issuer

if [ ! -e "$issuer" ]; then
  temporary=$(mktemp -d "$tls/.issuer.XXXXXX")
  trap 'rm -rf -- "$temporary"' EXIT
  if [ -e "$tls/issuer.crt" ] || [ -e "$tls/issuer.key" ]; then
    # Сохраняем уже выпущенную пару прежнего локального пилота.
    ln -- "$tls/issuer.crt" "$tls/issuer.key" "$temporary/"
  else
    openssl req -x509 -newkey rsa:3072 -sha256 -noenc -days 365 -subj '/CN=Exam fixture proxy CA' \
      -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
      -addext 'keyUsage=critical,keyCertSign,cRLSign' \
      -keyout "$temporary/issuer.key" -out "$temporary/issuer.crt" 2>"$temporary/openssl.log"
    rm -- "$temporary/openssl.log"
  fi
  mv -T -- "$temporary" "$issuer"
fi

openssl verify -CAfile "$issuer/issuer.crt" "$issuer/issuer.crt" >/dev/null
certificate_key=$(openssl x509 -in "$issuer/issuer.crt" -noout -pubkey)
private_key=$(openssl pkey -in "$issuer/issuer.key" -pubout)
test "$certificate_key" = "$private_key"

if [ -e "$tls/upstream-ca.pem" ]; then
  cmp "$tls/upstream-ca.pem" /run/nginx-ca.crt >/dev/null
else
  install -m 600 /run/nginx-ca.crt "$tls/upstream-ca.pem.tmp"
  mv -T -- "$tls/upstream-ca.pem.tmp" "$tls/upstream-ca.pem"
fi
chown 13:13 "$tls" "$issuer" "$issuer/issuer.crt" "$issuer/issuer.key" "$tls/upstream-ca.pem" "$state"
chmod 700 "$tls" "$issuer" "$state"
chmod 600 "$issuer/issuer.crt" "$issuer/issuer.key" "$tls/upstream-ca.pem"
install -d -o 13 -g 13 -m 700 "$state/spool" "$state/log" "$state/run"

if [ ! -d "$state/spool/ssl_db" ]; then
  setpriv --reuid=13 --regid=13 --clear-groups /usr/lib/squid/security_file_certgen \
    -c -s "$state/spool/ssl_db" -M 4MB >&2
fi
setpriv --reuid=13 --regid=13 --clear-groups /usr/sbin/squid-openssl -k parse -f "$config"
cat "$issuer/issuer.crt"
