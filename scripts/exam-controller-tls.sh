#!/usr/bin/env bash
# Изолированный TLS-вход контроллера на локальном стенде.
set -euo pipefail
umask 077

if (($# != 1)) || [[ "$1" != /* ]]; then
  echo 'Укажите абсолютный путь закрытого каталога контроллера.' >&2
  exit 64
fi
if [[ ! -d "$1" || ! -O "$1" || -L "$1" || $(stat -c %a -- "$1") != 700 ]]; then
  echo 'Каталог контроллера должен принадлежать оператору и иметь права 0700.' >&2
  exit 64
fi

state="$1"
repository=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec 9>"$state/tls.lock"
flock 9
if [[ ! -e "$state/tls" ]]; then
  temporary=$(mktemp -d -- "$state/.tls.XXXXXX")
  trap 'rm -rf -- "$temporary"' EXIT
  openssl req -x509 -newkey rsa:3072 -sha256 -noenc -days 365 -subj '/CN=Exam lab controller CA' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -keyout "$temporary/ca.key" -out "$temporary/ca.crt" 2>"$temporary/openssl.log"
  openssl req -new -newkey rsa:2048 -noenc -subj '/CN=Exam lab controller' \
    -keyout "$temporary/server.key" -out "$temporary/server.csr" 2>>"$temporary/openssl.log"
  cat >"$temporary/server.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:10.0.2.55
EOF
  openssl x509 -req -in "$temporary/server.csr" -CA "$temporary/ca.crt" -CAkey "$temporary/ca.key" \
    -days 180 -sha256 -extfile "$temporary/server.ext" -out "$temporary/server.crt" 2>>"$temporary/openssl.log"
  rm -- "$temporary/server.csr" "$temporary/server.ext" "$temporary/openssl.log"
  mv -T -- "$temporary" "$state/tls"
  trap - EXIT
fi

openssl verify -purpose sslserver -verify_ip 10.0.2.55 -CAfile "$state/tls/ca.crt" "$state/tls/server.crt" >/dev/null
for name in ca server; do
  certificate_key=$(openssl x509 -in "$state/tls/$name.crt" -noout -pubkey)
  private_key=$(openssl pkey -in "$state/tls/$name.key" -pubout)
  if [[ "$certificate_key" != "$private_key" ]]; then
    echo 'Ключ контроллера не соответствует сохранённому сертификату.' >&2
    exit 1
  fi
done

openssl x509 -in "$state/tls/ca.crt" -outform DER -out "$state/controller-ca.cer.tmp"
mv -T -- "$state/controller-ca.cer.tmp" "$state/controller-ca.cer"
install -m 600 -- "$repository/deploy/exam-controller.nginx.conf" "$state/nginx.conf.tmp"
mv -T -- "$state/nginx.conf.tmp" "$state/nginx.conf"
sha256sum -- "$state/controller-ca.cer"
